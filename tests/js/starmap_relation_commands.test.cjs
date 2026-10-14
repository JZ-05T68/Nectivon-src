// Exercise the Nectivon relation-action layer injected into the shipped starmap.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {test} = require('node:test');

const html = fs.readFileSync(path.join(__dirname,
  '../../src/components/knowledge_starmap/index.html'), 'utf8');
const start = html.indexOf('/* Nectivon 星图关系动作');
const end = html.indexOf('/* ============ 意图解析 ============ */');
assert(start >= 0 && end > start, 'relation layer anchors must exist in the built view');
const layer = html.slice(start, end);
const okStart = html.indexOf('function okGesture(lms)');
const hookStart = html.indexOf('function hookLandmarks()', okStart);
const frameEnd = html.indexOf('/* ===== 装配', hookStart);
assert(okStart >= 0 && hookStart > okStart && frameEnd > hookStart);
const okDetector = html.slice(okStart, hookStart);
const frameHook = html.slice(hookStart, frameEnd);

const PUNC = /[\s　，。、；：？！“”‘’（）《》〈〉【】\[\]()<>《》·—…\-_/\\|,.!?:;"'~`+*=^&#$%@]/g;
const NODES = {
  'knowledge:1': {id: 'knowledge:1', title: '微积分基础'},
  'knowledge:2': {id: 'knowledge:2', title: '卡尔曼滤波'},
  'knowledge:3': {id: 'knowledge:3', title: '反馈控制'},
  'knowledge:4': {id: 'knowledge:4', title: '线性和模型'},
  'knowledge:5': {id: 'knowledge:5', title: '向量机'},
};
function lm(x, y) { return {x, y}; }
function baseHand() {
  // 21 个手部关键点：四指蜷曲贴近掌心，掌距 d(0,9)=0.15
  const h = new Array(21).fill(null).map(() => lm(0.52, 0.72));
  h[0] = lm(0.5, 0.8); h[2] = lm(0.46, 0.66); h[9] = lm(0.5, 0.65);
  h[6] = lm(0.54, 0.7); h[8] = lm(0.55, 0.74);
  h[10] = lm(0.5, 0.7); h[12] = lm(0.5, 0.75);
  h[14] = lm(0.47, 0.7); h[16] = lm(0.46, 0.74);
  h[18] = lm(0.44, 0.7); h[20] = lm(0.44, 0.74);
  return h;
}
function thumbsUp() {
  const h = baseHand();
  h[4] = lm(0.5, 0.45);
  return h;
}
function peace() {
  const h = baseHand();
  h[8] = lm(0.5, 0.35); h[6] = lm(0.5, 0.55);
  h[12] = lm(0.62, 0.35); h[10] = lm(0.56, 0.55);
  return h;
}
function openHand() {
  const h = baseHand();
  h[4] = lm(0.42, 0.5);
  h[8] = lm(0.5, 0.35); h[6] = lm(0.5, 0.55);
  h[12] = lm(0.56, 0.35); h[10] = lm(0.54, 0.55);
  h[16] = lm(0.61, 0.4); h[14] = lm(0.57, 0.55);
  h[20] = lm(0.65, 0.47); h[18] = lm(0.6, 0.57);
  return h;
}
function makeEnvironment({selected = null, hovered = null} = {}) {
  const events = [], toasts = [];
  const context = vm.createContext({
    PUNC,
    Commands: {
      find: name => {
        let best = null, bs = 0;
        for (const node of Object.values(NODES)) {
          const q = String(name).replace(PUNC, '');
          if (!q) continue;
          const score = node.title === q ? 1 : (node.title.includes(q) ? 0.9 : 0);
          if (score > bs) { bs = score; best = node; }
        }
        return {node: best, score: bs};
      },
      speak: text => events.push({speak: text}),
    },
    S: {state: {selected, hovered}},
    toast: text => toasts.push(text),
    readOn: false,
    okOn: true, okFrames: 0, okActive: false, listening: false,
    startListen: () => events.push({voice: 'start'}),
    stopListen: () => events.push({voice: 'stop'}),
    window: {
      nectivonRelationChange: request => events.push({request}),
    },
  });
  vm.runInContext(okDetector + layer + frameHook, context);
  return {context, events, toasts};
}

test('link command resolves both titles and defaults to relates_to', () => {
  const e = makeEnvironment();
  const cmd = e.context.parseRelate('连接 微积分基础 和 卡尔曼滤波');
  assert.equal(cmd.act, 'link');
  assert.equal(cmd.a.id, 'knowledge:1');
  assert.equal(cmd.b.id, 'knowledge:2');
  assert.equal(cmd.relation_type, 'relates_to');
});

test('typed middle verbs keep direction and relation type', () => {
  const e = makeEnvironment();
  const cmd = e.context.parseRelate('卡尔曼滤波 派生自 反馈控制');
  assert.equal(cmd.act, 'link');
  assert.equal(cmd.a.id, 'knowledge:2', 'named source stays the source');
  assert.equal(cmd.b.id, 'knowledge:3');
  assert.equal(cmd.relation_type, 'derived_from');
  assert.equal(e.context.parseRelate('反馈控制 支持 卡尔曼滤波').relation_type, 'supports');
  assert.equal(e.context.parseRelate('反馈控制 矛盾 卡尔曼滤波').relation_type, 'contradicts');
  assert.equal(e.context.parseRelate('微积分基础 依赖 卡尔曼滤波').relation_type, 'requires');
});

test('unlink command pairs both names without needing a type', () => {
  const e = makeEnvironment();
  const cmd = e.context.parseRelate('断开 卡尔曼滤波 和 反馈控制');
  assert.equal(cmd.act, 'unlink');
  assert.deepEqual([cmd.a.id, cmd.b.id].sort(), ['knowledge:2', 'knowledge:3']);
  assert.equal(cmd.relation_type, 'relates_to');
});

test('single name pairs with the currently selected star', () => {
  const e = makeEnvironment({selected: NODES['knowledge:1']});
  const cmd = e.context.parseRelate('连接 卡尔曼滤波');
  assert.equal(cmd.act, 'link');
  assert.equal(cmd.a.id, 'knowledge:1', 'selected star becomes the source');
  assert.equal(cmd.b.id, 'knowledge:2');
});

test('title containing the join word still splits at the best boundary', () => {
  const e = makeEnvironment();
  const cmd = e.context.parseRelate('连接 线性和模型 和 卡尔曼滤波');
  assert.equal(cmd.a.id, 'knowledge:4');
  assert.equal(cmd.b.id, 'knowledge:2');
});

test('open/search phrasing and unknown titles never create relation commands', () => {
  const e = makeEnvironment();
  assert.equal(e.context.parseRelate('打开 卡尔曼滤波'), null);
  assert.equal(e.context.parseRelate('搜索 支持向量机'), null);
  assert.equal(e.context.parseRelate('连接 不存在的节点 和 卡尔曼滤波'), null);
  assert.equal(e.context.parseRelate('连接 微积分基础 和 微积分基础'), null,
    'both sides resolving to one star must be rejected');
  assert.equal(e.context.parseRelate('下一个星座'), null);
});

test('doRelate sends the action to the page and announces pending removal', () => {
  const e = makeEnvironment();
  e.context.doRelate({act: 'link', a: NODES['knowledge:1'], b: NODES['knowledge:2'],
    relation_type: 'derived_from'});
  assert.equal(e.events[0].request.action, 'link');
  assert.equal(e.events[0].request.source, 'knowledge:1');
  assert.equal(e.events[0].request.target, 'knowledge:2');
  assert.match(e.toasts[0], /已连接/);
  e.context.doRelate({act: 'unlink', a: NODES['knowledge:1'], b: NODES['knowledge:2'],
    relation_type: 'relates_to'});
  assert.equal(e.events[1].request.action, 'unlink');
  assert.match(e.toasts[1], /待确认/);
});

test('unchanged relation gestures stay separate from navigation and voice gestures', () => {
  assert.equal(makeEnvironment().context.relateGesture([thumbsUp()]), 'link');
  assert.equal(makeEnvironment().context.relateGesture([peace()]), 'unlink');
  assert.equal(makeEnvironment().context.relateGesture([openHand()]), '');
  const pinch = openHand();
  pinch[4] = {...pinch[8]};
  assert.equal(makeEnvironment().context.relateGesture([pinch]), '',
    'pinch stays reserved for the voice trigger');
  assert.equal(makeEnvironment().context.relateGesture([baseHand()]), '');
});

function hold(e, hand, frames = 4) {
  for (let i = 0; i < frames; i++) e.context.relateFrame([hand]);
}
function release(e) { hold(e, baseHand(), 45); }

test('first pose locks only one star; second selection and pose request confirmation', () => {
  const e = makeEnvironment({selected: NODES['knowledge:1']});
  for (let i = 0; i < 3; i++) e.context.relateFrame([thumbsUp()]);
  assert.equal(e.events.length, 0, 'needs a stable pose, not a flicker');
  e.context.relateFrame([thumbsUp()]);
  assert.equal(e.events.length, 0, 'first gesture never writes a relation');
  assert.equal(e.context.relateSource.id, 'knowledge:1');
  assert.match(e.toasts[0], /已锁定第一颗星/);
  e.context.S.state.selected = null;
  release(e);
  assert.equal(e.context.relateSource.id, 'knowledge:1', 'source survives selection clearing');
  assert.equal(e.context.S.state.relationSelecting, true);
  e.context.S.state.selected = NODES['knowledge:2'];
  hold(e, thumbsUp());
  assert.equal(e.events.length, 1);
  assert.equal(e.events[0].request.action, 'link');
  assert.equal(e.events[0].request.source, 'knowledge:1', 'locked source stays the source');
  assert.equal(e.events[0].request.target, 'knowledge:2');
  assert.equal(e.events[0].request.require_confirmation, true);
  assert.match(e.toasts.at(-1), /确认连接/);
  assert.equal(e.context.relateSource, null);
  for (let i = 0; i < 6; i++) e.context.relateFrame([thumbsUp()]);
  assert.equal(e.events.length, 1, 'cooldown suppresses immediate retrigger');
});

test('real landmarker hook sends both relation gestures through the Streamlit bridge', () => {
  const messages = [];
  const listeners = {};
  const e = makeEnvironment();
  e.context.window.parent = {postMessage: message => messages.push(message)};
  e.context.window.addEventListener = (name, callback) => {listeners[name] = callback};
  e.context.document = {};
  vm.runInContext(fs.readFileSync(path.join(__dirname,
    '../../src/components/knowledge_starmap/bridge.js'), 'utf8'), e.context);
  const a = NODES['knowledge:1'], b = NODES['knowledge:2'];
  e.context.window.nectivonSelected(a);
  e.context.S.state.selected = a;
  let hand = thumbsUp();
  const landmarker = {detectForVideo: () => ({landmarks: [hand]})};
  e.context.S.gesture = {landmarker};
  e.context.hookLandmarks();
  e.context.hookLandmarks(); // Repeated startup must not wrap the detector twice.
  for (let i = 0; i < 4; i++) landmarker.detectForVideo({}, i);
  let requests = messages.filter(message => message.value?.relation);
  assert.equal(requests.length, 0, 'first detected pose only locks the source');
  hand = baseHand();
  for (let i = 0; i < 45; i++) landmarker.detectForVideo({}, i);
  e.context.window.nectivonSelected(b);
  e.context.S.state.selected = b;
  hand = thumbsUp();
  for (let i = 0; i < 4; i++) landmarker.detectForVideo({}, i);
  requests = messages.filter(message => message.value?.relation);
  assert.equal(requests.length, 1, 'real OK detector must not throw inside the hook');
  assert.equal(requests[0].value.relation.action, 'link');
  assert.equal(requests[0].value.relation.source, a.id);
  assert.equal(requests[0].value.relation.target, b.id);
  hand = baseHand();
  for (let i = 0; i < 45; i++) landmarker.detectForVideo({}, i);
  hand = peace();
  for (let i = 0; i < 4; i++) landmarker.detectForVideo({}, i);
  assert.equal(messages.filter(message => message.value?.relation).length, 1);
  hand = baseHand();
  for (let i = 0; i < 45; i++) landmarker.detectForVideo({}, i);
  e.context.S.state.selected = a;
  hand = peace();
  for (let i = 0; i < 4; i++) landmarker.detectForVideo({}, i);
  requests = messages.filter(message => message.value?.relation);
  assert.equal(requests.length, 2);
  assert.equal(requests[1].value.relation.action, 'unlink');
  assert.equal(requests[1].value.relation.source, b.id);
  assert.equal(requests[1].value.relation.target, a.id);
  assert.equal(requests[1].value.relation.require_confirmation, true);
  assert.match(e.toasts.at(-1), /确认断开/);
  assert.equal(e.events.filter(event => event.voice).length, 0);
});

test('two hands and malformed frames never trigger relation actions', () => {
  const e = makeEnvironment({selected: NODES['knowledge:2']});
  for (const frame of [null, [], [[]], [thumbsUp(), openHand()]]) {
    for (let i = 0; i < 5; i++) e.context.onFrame(frame);
  }
  assert.equal(e.events.length, 0, 'two-hand zoom stays separate from relation actions');
});

test('renderer consumes relation poses before its fist/detail handler', () => {
  const start = html.indexOf('tick(){let{video:e}=this.o;');
  const end = html.indexOf('}draw(e){', start);
  assert(start >= 0 && end > start);
  const e = makeEnvironment();
  e.context.performance = {now: () => 1000};
  vm.runInContext('function tick(){' + html.slice(start + 'tick(){'.length, end) + '}',
    e.context);
  for (const hand of [thumbsUp(), peace()]) {
    let drawn = 0, pose;
    const controller = {
      o: {video: {readyState: 2, currentTime: 1}, onStatus: () => {}},
      landmarker: {detectForVideo: () => ({landmarks: [hand]})},
      draw: () => {drawn++},
      setPose: value => {pose = value},
      stablePose: () => {throw new Error('relation pose must not reach the fist classifier')},
    };
    e.context.tick.call(controller);
    assert.equal(drawn, 1);
    assert.equal(pose, hand[4].y < hand[2].y ? 'relate' : 'unrelate');
    assert.equal(controller.prevPalm, null);
    assert.equal(controller.history.length, 0);
  }
});

test('peace gesture locks the first star and requests unlink after the second', () => {
  const e = makeEnvironment({selected: NODES['knowledge:1']});
  hold(e, peace());
  assert.equal(e.events.length, 0);
  release(e);
  e.context.S.state.selected = NODES['knowledge:2'];
  hold(e, peace());
  assert.equal(e.events[0].request.action, 'unlink');
  assert.deepEqual([e.events[0].request.source, e.events[0].request.target].sort(),
    ['knowledge:1', 'knowledge:2']);
  assert.equal(e.events[0].request.require_confirmation, true);
  assert.match(e.toasts.at(-1), /确认断开/);
});

test('pose switch resets stability; missing selection never locks a source', () => {
  const e = makeEnvironment({selected: NODES['knowledge:2']});
  e.context.relateFrame([thumbsUp()]);
  e.context.relateFrame([thumbsUp()]);
  e.context.relateFrame([peace()]);
  e.context.relateFrame([peace()]);
  e.context.relateFrame([peace()]);
  assert.equal(e.events.length, 0, 'switching pose restarts the count');
  e.context.relateFrame([peace()]);
  assert.equal(e.events.length, 0);
  assert.equal(e.context.relateAction, 'unlink');
  const none = makeEnvironment();
  for (let i = 0; i < 4; i++) none.context.relateFrame([thumbsUp()]);
  assert.equal(none.events.length, 0);
  assert.match(none.toasts[0], /停留选中一颗知识点/);
});

test('same star and changing action preserve the locked source without sending', () => {
  const same = makeEnvironment({selected: NODES['knowledge:1']});
  hold(same, peace()); release(same); hold(same, peace());
  assert.equal(same.events.length, 0);
  assert.match(same.toasts.at(-1), /不同/);
  release(same);
  same.context.S.state.selected = NODES['knowledge:2'];
  hold(same, thumbsUp());
  assert.equal(same.events.length, 0);
  assert.match(same.toasts.at(-1), /相同手势/);
  assert.equal(same.context.relateSource.id, 'knowledge:1');
  release(same); hold(same, peace());
  assert.equal(same.events.length, 1, 'original action can still complete');
});

test('holding a gesture beyond cooldown requires release before accepting another star', () => {
  const e = makeEnvironment({selected: NODES['knowledge:1']});
  hold(e, thumbsUp());
  e.context.S.state.selected = NODES['knowledge:2'];
  hold(e, thumbsUp(), 120);
  assert.equal(e.events.length, 0);
  release(e); hold(e, thumbsUp());
  hold(e, thumbsUp(), 120);
  assert.equal(e.events.length, 1, 'completed gesture does not start another action');
});

test('selection changes during stability count restart the four-frame requirement', () => {
  const e = makeEnvironment({selected: NODES['knowledge:1']});
  hold(e, thumbsUp(), 3);
  e.context.S.state.selected = NODES['knowledge:2'];
  hold(e, thumbsUp(), 3);
  assert.equal(e.context.relateSource, null);
  hold(e, thumbsUp(), 1);
  assert.equal(e.context.relateSource.id, 'knowledge:2');
});

test('non-knowledge stars cannot start a relation workflow', () => {
  for (const node of [{id: 'hub:math', hub: true}, {id: 'question:1'}, {id: 'page:1'}]) {
    const e = makeEnvironment({selected: node});
    hold(e, thumbsUp());
    assert.equal(e.context.relateSource, null);
    assert.equal(e.events.length, 0);
  }
});

test('progress stays visible, cancel and graph updates clear the locked source', () => {
  const e = makeEnvironment({selected: NODES['knowledge:1']});
  const listeners = {};
  const element = () => ({children: [], appendChild(child) {this.children.push(child)},
    setAttribute() {}, addEventListener(name, fn) {this[name] = fn}});
  e.context.document = {createElement: element};
  e.context.window.addEventListener = (name, fn) => {listeners[name] = fn};
  const footer = element();
  e.context.relateBuildUI(footer);
  hold(e, thumbsUp());
  const panel = footer.children[0];
  assert.equal(panel.hidden, false);
  assert.match(panel.children[0].textContent, /微积分基础.*第二颗/);
  panel.children[1].click();
  assert.equal(panel.hidden, true);
  assert.equal(e.context.relateSource, null);
  assert.equal(e.context.S.state.relationSelecting, false);
  release(e); hold(e, peace());
  listeners['nectivon:payload-changed']();
  assert.equal(e.context.relateSource, null);
  assert.equal(e.context.S.state.relationSelecting, false);
  assert.equal(panel.hidden, true);
  assert.equal(e.events.length, 0);
});
