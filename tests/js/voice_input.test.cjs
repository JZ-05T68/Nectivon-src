// Run the shipped script, including lifecycle handlers, without real audio/network.
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const {test} = require('node:test');
const path = require('node:path');
const script = fs.readFileSync(path.join(__dirname, '../../src/voice_input.js'), 'utf8');

function environment(specs = [{}], supported = true) {
  class Element {
    constructor(tag) {
      this.tagName = tag.toUpperCase(); this.nodeType = 1; this.children = []; this.events = {};
      this.attributes = {}; this.style = {}; this.isConnected = true;
      const names = new Set();
      this.classList = {add: n => names.add(n), remove: n => names.delete(n),
        contains: n => names.has(n)};
    }
    appendChild(child) { child.parentElement = this; this.children.push(child); return child; }
    remove() { this.isConnected = false; }
    setAttribute(key, value) { this.attributes[key] = value; }
    getAttribute(key) { return this.attributes[key] ?? null; }
    addEventListener(name, callback) { this.events[name] = callback; }
    closest(selector) {
      return selector === '[inert]' ? (this.inert ? this.parentElement : null) : this.parentElement;
    }
    querySelector() { return this.children.find(c => ['INPUT','TEXTAREA','IFRAME'].includes(c.tagName)); }
  }
  class Field extends Element {
    constructor(spec = {}) {
      super(spec.tag || 'input'); this._value = spec.value || '';
      this.type = spec.type || 'text'; this.maxLength = spec.maxLength ?? -1;
      this.selectionStart = this.selectionEnd = this._value.length;
      this.disabled = false; this.readOnly = false; this.blurred = 0; this.dispatched = [];
      Object.assign(this, spec);
    }
    get value() { return this._value; }
    set value(value) { this._value = value; }
    dispatchEvent(event) { this.dispatched.push(event.type); }
    focus() { }
    blur() { this.blurred++; }
    setSelectionRange(start, end) { this.selectionStart = start; this.selectionEnd = end; }
  }
  const observers = [], recognitions = [], timers = new Map();
  let sequence = 0;
  const doc = {body: new Element('body'), head: new Element('head'), fields: [],
    events: {}, addEventListener(name, callback) {this.events[name] = callback;},
    createElement(tag) {const el = new Element(tag); el.ownerDocument = doc; return el;},
    getElementById(id) {return doc.body.children.find(e => e.id === id);},
    querySelectorAll(selector) {
      if (selector === 'input, textarea') return doc.fields.filter(f => f.isConnected);
      if (selector === 'style') return [{textContent: '.nectivon-voice-host {}'}];
      return [];
    }};
  function addField(spec) {
    const field = new Field(spec); field.ownerDocument = doc;
    const host = new Element('div'); doc.body.appendChild(host); host.appendChild(field);
    doc.fields.push(field); return field;
  }
  specs.forEach(addField);
  class Recognition {
    constructor() {recognitions.push(this);}
    start() {this.started = true; this.onstart?.();}
    stop() {this.stopped = true; this.onend?.();}
    abort() {this.aborted = true; this.onend?.();}
    result(text, final = true, index = 0) {
      const result = [{transcript: text}]; result.isFinal = final;
      const results = Array(index).fill(result); results.push(result);
      this.onresult({resultIndex: index, results});
    }
  }
  const win = {navigator: {language: 'zh-CN'}, HTMLInputElement: Field,
    HTMLTextAreaElement: Field, Event: class {constructor(type) {this.type = type;}},
    MutationObserver: class {constructor(callback) {this.callback = callback; observers.push(this);}
      observe() {}},
    setTimeout(callback, delay) {timers.set(++sequence, {callback, delay}); return sequence;},
    clearTimeout(id) {timers.delete(id);}, requestAnimationFrame(fn) {fn();},
    addEventListener() {},
  };
  if (supported) win.SpeechRecognition = Recognition;
  doc.defaultView = win;
  const context = vm.createContext({window: win, document: doc});
  const run = () => vm.runInContext(script, context);
  run();
  function button(field = doc.fields[0]) {
    return field.parentElement.children.find(e => e.tagName === 'BUTTON' && e.isConnected);
  }
  function click(field = doc.fields[0]) {
    button(field).events.click({preventDefault() {}, stopPropagation() {}});
    return recognitions.at(-1);
  }
  return {doc, win, button, click, run, recognitions, observers, timers, addField,
    notice: () => doc.getElementById('nectivon-voice-status')?.textContent};
}

test('idle installation never records; excludes nontext, disabled and secret fields', () => {
  const e = environment([{}, {type:'password'}, {type:'number'}, {disabled:true},
    {readOnly:true}, {tag:'textarea'}]);
  assert.equal(e.recognitions.length, 0);
  assert.equal(e.button().type, 'button');
  assert.equal(e.button(e.doc.fields[1]), undefined);
  assert.equal(e.button(e.doc.fields[2]), undefined);
  assert.equal(e.button(e.doc.fields[3]), undefined);
  assert.equal(e.button(e.doc.fields[4]), undefined);
  assert.ok(e.button(e.doc.fields[5]));
  e.doc.fields[1].type = 'text'; e.win.__nectivonVoiceInput.scan();
  assert.equal(e.button(e.doc.fields[1]), undefined, 'showing a secret must not enable dictation');
});

test('final text replaces only selection, preserves surrounding text and deduplicates', () => {
  const e = environment([{value:'前旧后', selectionStart:1, selectionEnd:2}]);
  const r = e.click(); const f = e.doc.fields[0];
  assert.equal(r.lang, 'zh-CN');
  r.result('中间结果', false); assert.equal(f.value, '前旧后');
  r.result('新'); r.result('新'); assert.equal(f.value, '前新后');
  assert.deepEqual(f.dispatched, ['input', 'change']);
  assert.equal(f.selectionStart, 2);
  r.onend(); assert.equal(f.blurred, 1);
  assert.match(e.notice(), /修改后再提交/);
});

test('native setter bypasses React value tracker and existing limits remain enforced', () => {
  const e = environment([{value:'1234', maxLength:6}]); const f = e.doc.fields[0];
  let tracked = 0;
  Object.defineProperty(f, 'value', {get() {return this._value;},
    set(value) {tracked++; this._value = value;}});
  const r = e.click(); r.result('ABCDE'); r.onend();
  assert.equal(f.value, '1234AB'); assert.equal(tracked, 0);
});

test('manual edits while listening are preserved and inserted text follows current caret', () => {
  const e = environment([{tag:'textarea', value:'原稿\n'}]); const r = e.click();
  const f = e.doc.fields[0]; f.value += '手工修改';
  f.selectionStart = f.selectionEnd = f.value.length;
  r.result('语音'); assert.equal(f.value, '原稿\n手工修改语音');
});

test('switching fields aborts old recognition and ignores its late results', () => {
  const e = environment([{value:'一'}, {value:'二'}]); const r1 = e.click();
  const r2 = e.click(e.doc.fields[1]); assert.ok(r1.aborted);
  r1.result('错误位置'); r2.result('正确位置');
  assert.equal(e.doc.fields[0].value, '一');
  assert.equal(e.doc.fields[1].value, '二正确位置');
});

test('dynamic reruns install once; removed or disabled inputs abort safely', () => {
  const e = environment(); e.run(); e.run();
  assert.equal(e.observers.length, 1);
  assert.equal(e.doc.fields[0].parentElement.children.length, 2);
  const added = e.addField({value:'动态'});
  e.observers[0].callback([{type:'childList', addedNodes:[added], removedNodes:[]}]);
  assert.ok(e.button(added)); const r = e.click(added);
  added.disabled = true; e.observers[0].callback([{type:'attributes'}]);
  assert.ok(r.aborted); assert.equal(e.button(added), undefined);
  r.result('不写入'); assert.equal(added.value, '动态');
});

test('denied permission or unavailable service leaves drafts intact and provides recovery', () => {
  const e = environment([{value:'原稿'}]); const r = e.click();
  r.onerror({error:'not-allowed'}); r.result('延迟错误结果');
  assert.equal(e.doc.fields[0].value, '原稿'); assert.match(e.notice(), /麦克风权限/);
  assert.equal(e.button().getAttribute('aria-pressed'), 'false');
  e.win.SpeechRecognition = undefined; e.click();
  assert.match(e.notice(), /不支持语音识别/); assert.equal(e.recognitions.length, 1);
  e.win.SpeechRecognition = class {start() {throw new Error('unavailable');}};
  e.click(); assert.match(e.notice(), /无法启动/);
});

test('opening a folded panel enables its text fields, closing it aborts dictation', () => {
  const e = environment([{tag:'textarea', inert:true}]); const f = e.doc.fields[0];
  assert.equal(e.button(), undefined);
  f.inert = false; e.observers[0].callback([{type:'attributes'}]);
  assert.ok(e.button()); const r = e.click();
  f.inert = true; e.observers[0].callback([{type:'attributes'}]);
  assert.ok(r.aborted); r.result('不覆盖隐藏内容'); assert.equal(f.value, '');
});

test('second click stops; escape, disconnection and session timeout release recognition', () => {
  let e = environment(); let r = e.click(); e.click(); assert.ok(r.stopped);
  e = environment(); r = e.click(); e.doc.events.keydown({key:'Escape'});
  assert.ok(r.aborted);
  e = environment(); r = e.click(); e.doc.fields[0].isConnected = false;
  e.win.__nectivonVoiceInput.scan(); assert.ok(r.aborted);
  e = environment(); r = e.click();
  [...e.timers.values()].find(t => t.delay === 60000).callback();
  assert.ok(r.stopped);
});
