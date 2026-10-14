// Exercise the grouping function extracted from the shipped offline renderer.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {test} = require('node:test');

const html = fs.readFileSync(path.join(__dirname,
  '../../src/components/knowledge_starmap/index.html'), 'utf8');
const helperStart = html.indexOf('mG={');
const helpers = 'const ' + html.slice(helperStart, html.indexOf(';function vG(', helperStart)) + ';';
const rendererStart = html.indexOf('var SG=');
const renderer = html.slice(rendererStart, html.indexOf('async function wG()', rendererStart));
assert(helperStart >= 0 && rendererStart >= 0, 'Renderer anchors must exist');
const context = vm.createContext({});
vm.runInContext(helpers + renderer, context);

const config = {
  nested: 'top',
  groups: [
    {id: 'math', name: '数学', tags: ['数学']},
    {id: 'control', name: '控制科学与工程', tags: ['控制科学与工程']},
    {id: 'electrical', name: '电气工程', tags: ['电气工程']},
    {id: 'empty', name: '无内容学科', tags: ['无内容学科']},
  ],
};

test('one knowledge point links all occupied subject hubs and counts in each', () => {
  const note = {id: 'knowledge:1', title: '反馈控制', summary: '合成测试',
    tags: ['数学/微积分', '控制科学与工程/自动控制原理', '电气工程/电力系统']};
  const graph = context.CG({notes: [note], links: []}, config);
  assert.equal(graph.nodes.filter(node => !node.hub).length, 1);
  assert.deepEqual(Array.from(graph.groups, group => group.name),
    ['数学', '控制科学与工程', '电气工程']);
  const hubs = graph.nodes.filter(node => node.hub);
  assert.equal(hubs.length, 3);
  assert(hubs.every(hub => hub.degree === 1), 'Shared node contributes to all subject counts');
  const targets = graph.links.filter(link => link.source === note.id).map(link => link.target);
  assert.deepEqual(new Set(targets), new Set(['hub:math', 'hub:control', 'hub:electrical']));
  assert.equal(graph.links.filter(link => link.kind === 'xtag').length, 2);
});

test('multiple directions of one subject create one hub connection', () => {
  const graph = context.CG({notes: [{id: 'knowledge:1', title: '积分',
    tags: ['数学/微积分', '数学/线性代数', '数学']}], links: []}, config);
  assert.equal(graph.nodes.filter(node => node.hub).length, 1);
  assert.equal(graph.links.filter(link => link.source === 'knowledge:1').length, 1);
});

test('empty data shows no nebulae while an unclassified node stays visible', () => {
  assert.equal(context.CG({notes: [], links: []}, config).groups.length, 0);
  const graph = context.CG({notes: [{id: 'knowledge:1', title: '待归类', tags: []}],
    links: []}, config);
  assert.equal(graph.nodes.filter(node => !node.hub).length, 1);
  assert.deepEqual(Array.from(graph.groups, group => group.name), ['未归类']);
});

test('saved cross-subject relation connects the two knowledge points with metadata intact', () => {
  const link = {source: 'knowledge:2', target: 'knowledge:1', label: '依赖',
    note: '反馈分析需要微积分', cross_subject: true};
  const graph = context.CG({notes: [
    {id: 'knowledge:1', title: '微积分', tags: ['数学/微积分']},
    {id: 'knowledge:2', title: '反馈分析', tags: ['控制科学与工程/自动控制原理']},
  ], links: [link]}, config);
  const direct = graph.links.filter(edge => !edge.target.startsWith('hub:'));
  assert.equal(direct.length, 1);
  assert.equal(direct[0].source, link.source);
  assert.equal(direct[0].target, link.target);
  assert.equal(direct[0].kind, 'bridge');
  assert.equal(direct[0].label, link.label);
  assert.equal(direct[0].note, link.note);
});

test('multi-subject endpoints keep a bridge even when their primary hub is the same', () => {
  const graph = context.CG({notes: [
    {id: 'knowledge:1', title: '微积分', tags: ['数学/微积分']},
    {id: 'knowledge:2', title: '反馈分析', tags: ['数学/微积分', '控制科学与工程/自动控制原理']},
  ], links: [{source: 'knowledge:2', target: 'knowledge:1', cross_subject: true}]}, config);
  assert.equal(graph.nodes.find(node => node.id === 'knowledge:1').cat,
    graph.nodes.find(node => node.id === 'knowledge:2').cat);
  assert.equal(graph.links.find(edge => edge.target === 'knowledge:1').kind, 'bridge');
});

test('subject membership alone does not generate a knowledge-to-knowledge relation', () => {
  const graph = context.CG({notes: [
    {id: 'knowledge:1', title: '微积分', tags: ['数学']},
    {id: 'knowledge:2', title: '反馈分析', tags: ['控制科学与工程']},
  ], links: []}, config);
  assert.equal(graph.links.filter(edge => !edge.target.startsWith('hub:')).length, 0);
});

test('confirmed cross-subject lines are brighter while ordinary bridge opacity is preserved', () => {
  const styleContext = vm.createContext({});
  vm.runInContext(`
    const E7 = new Map(), D7 = new Map();
    const Y7 = value => typeof value === 'object' ? value.id : value;
    const L = class {clone() {return this} lerp() {return this}};
    const b7 = new L();
    const da = class {constructor(options) {Object.assign(this, options)}};
  `, styleContext);
  const start = html.indexOf('function X7(');
  assert(start >= 0);
  vm.runInContext(html.slice(start, html.indexOf('function Z7(', start)), styleContext);
  const ordinary = {source: 'knowledge:1', target: 'knowledge:2', kind: 'bridge'};
  const cross = {...ordinary, cross_subject: true};
  assert.equal(styleContext.X7(ordinary).opacity, 0.16);
  assert(styleContext.X7(cross).opacity > ordinary.baseAlpha);
});
