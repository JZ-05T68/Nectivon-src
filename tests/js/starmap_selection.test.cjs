// Exercise the shipped picking code; preselected test nodes cannot catch this regression.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {test} = require('node:test');

const html = fs.readFileSync(path.join(__dirname,
  '../../src/components/knowledge_starmap/index.html'), 'utf8');
const pickStart = html.indexOf('function h9(');
const pickEnd = html.indexOf('var g9=new tc', pickStart);
assert(pickStart >= 0 && pickEnd > pickStart);
const helpersStart = html.indexOf('function nectivonSelectStar(');
const helpers = helpersStart >= 0 ? html.slice(helpersStart, pickStart) : '';
const mouseStart = html.lastIndexOf('.onNodeClick(');
const mouseEnd = html.indexOf(');I7.d3Force(', mouseStart);
const pointStart = html.indexOf('onPoint(e,t){');
const pointEnd = html.indexOf('onPointEnd()', pointStart);
assert(mouseStart >= 0 && mouseEnd > mouseStart && pointStart >= 0 && pointEnd > pointStart);

function environment() {
  const source = {id: 'knowledge:1', title: '第一颗星', cat: 'math',
    x: -0.6, y: 0, z: 0, alpha: 1};
  const target = {id: 'knowledge:2', title: '无关系的星', cat: 'control',
    x: 0.2, y: 0, z: 0, alpha: 0.2};
  for (const node of [source, target]) {
    Object.assign(node, {baseSize: 9, twinkle: 0, label: {visible: false}, labelEl: {style: {}},
      __threeObj: {material: {}, scale: {set() {}}}});
  }
  const events = [], callbacks = {};
  let now = 0;
  const context = vm.createContext({
    T7: {nodes: [source, target], links: []},
    $: {selected: source, hovered: null},
    m9: {set(x, y, z) {Object.assign(this, {x, y, z}); return this}, project() {return this}},
    B7: {}, innerWidth: 1000, window: {nectivonMapHeight: () => 500},
    g7: () => ({getBoundingClientRect: () => ({left: 100, top: 80})}),
    i9: () => {}, d9: () => events.push('close'),
    c9: node => {context.$.selected = node; events.push(node?.id ?? null)},
    u9: node => events.push(`detail:${node.id}`),
    n9: page => events.push(`page:${page}`),
    k7: [null, 'math', 'control'], D7: new Map([['math', {name: '数学'}]]),
    j9: () => {}, performance: {now: () => now},
    N9: {style: {}, classList: {toggle() {}}}, P9: {style: {}}, F9: 100,
    I9: {node: null, since: 0},
    N7: () => 'math', O7: new Map([[source.id, new Set()]]), _7: true, t: 1 / 60, e: 2000,
    I7: {
      onNodeClick(fn) {callbacks.node = fn; return this},
      onBackgroundClick(fn) {callbacks.background = fn; return this},
      onLinkClick(fn) {callbacks.link = fn; return this},
    },
  });
  vm.runInContext(helpers + html.slice(pickStart, pickEnd), context);
  vm.runInContext('I7' + html.slice(mouseStart, mouseEnd) + ');', context);
  vm.runInContext('var gesture={' + html.slice(pointStart, pointEnd) + '};', context);
  const renderStart = html.indexOf('let o=N7(),s=$.selected');
  const renderEnd = html.indexOf('for(let e of T7.links)', renderStart);
  assert(renderStart >= 0 && renderEnd > renderStart);
  vm.runInContext('function renderNodes(){' + html.slice(renderStart, renderEnd) + '}', context);
  return {context, source, target, events, callbacks, setTime: value => {now = value}};
}

test('dim unrelated stars remain pickable across subjects after selecting the source', () => {
  const e = environment();
  assert.equal(e.context.h9(600, 250), e.target);
});

test('mouse click near a dim star selects it instead of clearing selection', () => {
  const e = environment();
  e.callbacks.background({clientX: 708, clientY: 330});
  assert.equal(e.context.$.selected, e.target);
});

test('a line under the mouse does not prevent picking a nearby unrelated star', () => {
  const e = environment();
  assert.equal(typeof e.callbacks.link, 'function');
  e.callbacks.link({}, {clientX: 708, clientY: 330});
  assert.equal(e.context.$.selected, e.target);
});

test('a large foreground hub cannot intercept a click on an unrelated small star', () => {
  const e = environment();
  e.callbacks.node({id: 'hub:control', cat: 'control', hub: true},
    {clientX: 708, clientY: 330});
  assert.equal(e.context.$.selected, e.target);
  assert(!e.events.includes('page:2'), 'clicking the small star must not switch nebulae');
});

test('a hub near the pointer does not take priority over a closer knowledge star', () => {
  const e = environment();
  e.context.T7.nodes.unshift({id: 'hub:control', cat: 'control', hub: true,
    x: 0.22, y: 0, z: 0, alpha: 1});
  assert.equal(e.context.h9(600, 250), e.target);
  e.callbacks.background({clientX: 700, clientY: 330});
  assert.equal(e.context.$.selected, e.target);
});

test('gesture dwell selects an unrelated dim star through the real pointing callback', () => {
  const e = environment();
  e.context.gesture.onPoint(600, 250);
  assert.equal(e.context.$.selected, e.source, 'dwell still requires one second');
  e.setTime(1001);
  e.context.gesture.onPoint(600, 250);
  assert.equal(e.context.$.selected, e.target);
});

test('mouse tolerance is bounded and empty space still clears selection', () => {
  const e = environment();
  e.callbacks.background({clientX: 740, clientY: 330});
  assert.equal(e.context.$.selected, null);
});

test('off-camera stars are excluded even when nearer than a visible target', () => {
  const e = environment();
  e.context.T7.nodes.unshift({id: 'knowledge:3', x: 0.2, y: 0, z: 2, alpha: 1});
  assert.equal(e.context.h9(600, 250), e.target);
});

test('existing mouse selection, second-click details and hub navigation still work', () => {
  const e = environment();
  e.callbacks.node(e.target, {clientX: 700, clientY: 330});
  assert.equal(e.context.$.selected, e.target);
  e.callbacks.node(e.target, {clientX: 700, clientY: 330});
  assert(e.events.includes('detail:knowledge:2'));
  e.callbacks.node({id: 'hub:math', cat: 'math', hub: true}, {clientX: 900, clientY: 430});
  assert(e.events.includes('page:1'));
});

for (const input of ['mouse', 'gesture']) {
  test(`${input} can choose an unrelated second endpoint and request confirmed connection`, () => {
    const e = environment();
    const requests = [];
    Object.assign(e.context, {
      S: {state: e.context.$}, toast: () => {}, readOn: false,
    });
    e.context.window.nectivonRelationChange = request => requests.push(request);
    const okStart = html.indexOf('function okGesture(lms)');
    const okEnd = html.indexOf('function hookLandmarks()', okStart);
    const layerStart = html.indexOf('/* Nectivon 星图关系动作');
    const layerEnd = html.indexOf('/* ============ 意图解析 ============ */', layerStart);
    vm.runInContext(html.slice(okStart, okEnd) + html.slice(layerStart, layerEnd), e.context);
    e.context.relateApply('link');
    assert.equal(e.context.$.relationSelecting, true);
    assert.equal(requests.length, 0, 'locking a source never saves a relation');
    for (let i = 0; i < 60; i++) e.context.renderNodes();
    assert(e.target.alpha > 0.9, 'unrelated stars in other subjects become readable');
    assert.equal(e.target.label.visible, true);
    if (input === 'mouse') e.callbacks.background({clientX: 708, clientY: 330});
    else {
      e.context.gesture.onPoint(600, 250);
      e.setTime(1001);
      e.context.gesture.onPoint(600, 250);
    }
    e.context.relateApply('link');
    assert.equal(requests.length, 1);
    assert.equal(requests[0].source, e.source.id);
    assert.equal(requests[0].target, e.target.id);
    assert.equal(requests[0].require_confirmation, true);
    assert.equal(e.context.$.relationSelecting, false);
    e.context.$.selected = e.source;
    for (let i = 0; i < 60; i++) e.context.renderNodes();
    assert(e.target.alpha < 0.3, 'normal focus returns after ending the operation');
  });
}
