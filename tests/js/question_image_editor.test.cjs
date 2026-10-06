const assert = require('node:assert/strict');
const {test} = require('node:test');
const tools = require('../../src/components/question_image_editor/drawing_tools.js');

test('color codes preserve exact RGB values and reject incomplete or invalid input', () => {
  assert.equal(tools.normalizeHex(' #24AB6C '), '#24ab6c');
  assert.equal(tools.normalizeHex('fff'), '#ffffff');
  assert.equal(tools.normalizeHex('#12'), null);
  assert.equal(tools.normalizeHex('#badhex'), null);
  assert.equal(tools.rgbHex([36, 104, 172]), '#2468ac');
  assert.equal(tools.rgbHex([-5, 300, 127.5]), '#00ff80');
});

test('sampled pixel color matches the visible image, including transparency', () => {
  assert.equal(tools.pixelHex(new Uint8ClampedArray([36, 104, 172, 255])), '#2468ac');
  assert.equal(tools.pixelHex([0, 0, 0, 0]), '#ffffff');
  assert.equal(tools.pixelHex([0, 0, 0, 128]), '#7f7f7f');
});

test('sampling maps zoomed and scrolled images back to original pixels', () => {
  assert.deepEqual(tools.imagePoint(150, 75, {left: 50, top: -25, width: 200, height: 100}, 800, 400), [400, 399]);
  assert.deepEqual(tools.imagePoint(100, 25, {left: 50, top: -25, width: 200, height: 100}, 800, 400), [200, 200]);
  assert.deepEqual(tools.imagePoint(-50, -50, {left: 0, top: 0, width: 200, height: 100}, 800, 400), [0, 0]);
});

for (const end of [[70, 60], [10, 60], [70, 20], [10, 20]]) {
  test(`circle and square keep equal dimensions when dragging to ${end}`, () => {
    const circle = tools.shapeGeometry('circle', [40, 40], end);
    const square = tools.shapeGeometry('square', [40, 40], end);
    assert.equal(circle.rx, 15); assert.equal(circle.ry, 15);
    assert.equal(square.width, 30); assert.equal(square.height, 30);
    assert.equal(circle.cx, square.x + 15); assert.equal(circle.cy, square.y + 15);
  });
}

test('rectangles, ellipses, and triangles cover the requested bounds', () => {
  assert.deepEqual(tools.shapeGeometry('rectangle', [70, 60], [10, 20]),
    {kind: 'rect', x: 10, y: 20, width: 60, height: 40});
  assert.deepEqual(tools.shapeGeometry('ellipse', [10, 20], [70, 60]),
    {kind: 'ellipse', cx: 40, cy: 40, rx: 30, ry: 20});
  assert.deepEqual(tools.shapeGeometry('triangle', [70, 60], [10, 20]),
    {kind: 'polygon', points: [[40, 20], [70, 60], [10, 60]]});
  assert.equal(tools.shapeGeometry('line', [10, 20], [70, 60]), null);
});
