/* Pure drawing/color operations shared by the local canvas and regression checks. */
var QuestionDrawingTools = (() => {
  const shapeModes = new Set(['rectangle', 'square', 'circle', 'ellipse', 'triangle']);

  function normalizeHex(value) {
    const match = String(value).trim().match(/^#?([0-9a-f]{3}|[0-9a-f]{6})$/i);
    if (!match) return null;
    const hex = match[1].toLowerCase();
    return '#' + (hex.length === 3 ? [...hex].map(c => c + c).join('') : hex);
  }

  function rgbHex(channels) {
    return '#' + channels.slice(0, 3).map(value =>
      Math.max(0, Math.min(255, Math.round(Number(value) || 0))).toString(16).padStart(2, '0')
    ).join('');
  }

  function pixelHex(pixel) {
    const alpha = pixel[3] === undefined ? 1 : pixel[3] / 255;
    return rgbHex(Array.from(pixel).slice(0, 3).map(c => c * alpha + 255 * (1 - alpha)));
  }

  function imagePoint(clientX, clientY, bounds, width, height) {
    return [
      Math.max(0, Math.min(width - 1, (clientX - bounds.left) * width / bounds.width)),
      Math.max(0, Math.min(height - 1, (clientY - bounds.top) * height / bounds.height)),
    ];
  }

  function shapeGeometry(mode, from, to) {
    let dx = to[0] - from[0], dy = to[1] - from[1];
    if (mode === 'circle' || mode === 'square') {
      const side = Math.max(Math.abs(dx), Math.abs(dy));
      dx = (dx < 0 ? -1 : 1) * side;
      dy = (dy < 0 ? -1 : 1) * side;
    }
    const x = Math.min(from[0], from[0] + dx), y = Math.min(from[1], from[1] + dy);
    const width = Math.abs(dx), height = Math.abs(dy);
    if (mode === 'rectangle' || mode === 'square') return {kind: 'rect', x, y, width, height};
    if (mode === 'circle' || mode === 'ellipse') {
      return {kind: 'ellipse', cx: x + width / 2, cy: y + height / 2, rx: width / 2, ry: height / 2};
    }
    if (mode === 'triangle') return {kind: 'polygon', points: [[x + width / 2, y], [x + width, y + height], [x, y + height]]};
    return null;
  }

  function traceShape(context, geometry) {
    if (geometry.kind === 'rect') context.rect(geometry.x, geometry.y, geometry.width, geometry.height);
    else if (geometry.kind === 'ellipse') context.ellipse(geometry.cx, geometry.cy, geometry.rx, geometry.ry, 0, 0, Math.PI * 2);
    else {
      context.moveTo(...geometry.points[0]);
      geometry.points.slice(1).forEach(point => context.lineTo(...point));
      context.closePath();
    }
  }

  return {shapeModes, normalizeHex, rgbHex, pixelHex, imagePoint, shapeGeometry, traceShape};
})();
if (typeof module !== 'undefined') module.exports = QuestionDrawingTools;
