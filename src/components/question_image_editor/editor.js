/* All editing stays in this local component until the user explicitly saves. */
(() => {
  const el = id => document.getElementById(id), tools = QuestionDrawingTools;
  const canvas = el('canvas'), ctx = canvas.getContext('2d');
  const source = new Image(), base = new Image();
  const sourceCanvas = document.createElement('canvas'), sourceCtx = sourceCanvas.getContext('2d');
  let args = {}, signature = '', scale = .5, history = [], future = [], stroke = null;
  let ready = false, comparing = false, sampling = false, color = '#000000';
  const post = value => parent.postMessage({isStreamlitMessage: true, ...value}, '*');
  const status = text => el('status').textContent = text;
  const load = (image, url) => new Promise((resolve, reject) => {
    image.onload = resolve; image.onerror = reject; image.src = url;
  });

  function size() {
    const width = canvas.width * scale, height = Math.min(460, Math.max(180, canvas.height * scale + 18));
    canvas.style.width = width + 'px'; canvas.style.height = canvas.height * scale + 'px';
    el('original').style.width = width + 'px';
    el('leftView').style.height = height + 'px'; el('rightView').style.height = height + 'px';
    el('zoom').textContent = Math.round(scale * 100) + '%';
    post({type: 'streamlit:setFrameHeight', height: document.body.scrollHeight + 8});
  }

  function colorPanel(open) {
    el('colors').hidden = !open;
    el('colorToggle').setAttribute('aria-expanded', String(open)); size();
  }

  function setColor(value, syncHex = true) {
    const normalized = tools.normalizeHex(value);
    if (!normalized) return false;
    color = normalized;
    if (syncHex) el('hex').value = color;
    el('hex').setAttribute('aria-invalid', 'false');
    el('swatch').style.backgroundColor = color; el('colorValue').textContent = color;
    ['red', 'green', 'blue'].forEach((name, index) => {
      const channel = parseInt(color.slice(1 + index * 2, 3 + index * 2), 16);
      el(name).value = channel; el(name + 'Value').textContent = channel;
    });
    return true;
  }

  const presets = [['黑色', '#000000'], ['白色', '#ffffff'], ['灰色', '#808080'],
    ['浅灰色', '#d9d9d9'], ['红色', '#e53935'], ['橙色', '#fb8c00'], ['黄色', '#fdd835'],
    ['绿色', '#43a047'], ['青色', '#00acc1'], ['蓝色', '#1e88e5'], ['紫色', '#8e24aa'], ['棕色', '#795548']];
  for (const [name, value] of presets) {
    const button = document.createElement('button');
    button.style.backgroundColor = value; button.setAttribute('aria-label', name); button.title = name;
    button.onclick = () => {setColor(value); status('已选择 ' + color + '，可继续绘图。');};
    el('presets').appendChild(button);
  }
  el('colorToggle').onclick = () => colorPanel(el('colors').hidden);
  el('colorDone').onclick = () => colorPanel(false);
  el('hex').oninput = () => {
    if (setColor(el('hex').value, false)) status('已选择 ' + color + '，可继续绘图。');
    else {el('hex').setAttribute('aria-invalid', 'true'); status('请输入有效颜色代码，例如 #ffffff；画笔保留当前颜色。');}
  };
  el('hex').onblur = () => setColor(el('hex').value);
  for (const name of ['red', 'green', 'blue']) el(name).oninput = () => {
    setColor(tools.rgbHex(['red', 'green', 'blue'].map(c => Number(el(c).value))));
    status('已选择 ' + color + '，可继续绘图。');
  };

  function setSampling(active) {
    sampling = active; el('sample').setAttribute('aria-pressed', String(active));
    el('sample').classList.toggle('active', active);
    el('sample').textContent = active ? '取消取色' : '吸管取色';
    el('original').style.cursor = active ? 'crosshair' : 'default';
  }
  el('sample').onclick = () => {
    setSampling(!sampling);
    status(sampling ? '点击左侧原图或右侧显示图提取颜色；取色不会修改图片。' : '已取消取色，可继续绘图。');
  };
  function sampleImage(event, target, context) {
    const point = tools.imagePoint(event.clientX, event.clientY, target.getBoundingClientRect(), canvas.width, canvas.height);
    const pixel = context.getImageData(Math.floor(point[0]), Math.floor(point[1]), 1, 1).data;
    setColor(tools.pixelHex(pixel)); setSampling(false); colorPanel(false);
    status('已提取颜色 ' + color + '，可继续绘图。');
  }
  el('original').addEventListener('pointerdown', event => {
    if (!ready || !sampling || event.button !== 0) return;
    event.preventDefault(); sampleImage(event, el('original'), sourceCtx);
  });

  function paint(op) {
    if (op.mode === 'text') {
      ctx.fillStyle = op.color; ctx.font = op.fontSize + 'px "Microsoft YaHei",sans-serif';
      ctx.fillText(op.text, ...op.points[0]); return;
    }
    const points = op.mode === 'line' ? [op.points[0], op.points[op.points.length - 1]] : op.points;
    const mask = document.createElement('canvas'); mask.width = canvas.width; mask.height = canvas.height;
    const m = mask.getContext('2d');
    m.lineWidth = op.width; m.strokeStyle = '#fff'; m.fillStyle = '#fff'; m.lineCap = 'round'; m.lineJoin = 'round';
    const first = points[0], last = points[points.length - 1];
    const geometry = tools.shapeGeometry(op.mode, first, last);
    m.beginPath();
    if (geometry) tools.traceShape(m, geometry);
    else {m.moveTo(...first); for (const point of points.slice(1)) m.lineTo(...point);}
    if (geometry && op.fill) m.fill();
    m.stroke();
    if (points.length === 1 && !geometry) {
      m.beginPath(); m.arc(...first, op.width / 2, 0, Math.PI * 2); m.fill();
    }
    m.globalCompositeOperation = 'source-in';
    if (op.mode === 'restore') m.drawImage(source, 0, 0);
    else {m.fillStyle = op.mode === 'erase' ? '#fff' : op.color; m.fillRect(0, 0, mask.width, mask.height);}
    ctx.drawImage(mask, 0, 0);
  }
  function redraw() {
    ctx.clearRect(0, 0, canvas.width, canvas.height); ctx.drawImage(comparing ? source : base, 0, 0);
    if (!comparing) {history.forEach(paint); if (stroke) paint(stroke);}
    el('undo').disabled = !history.length; el('redo').disabled = !future.length;
  }
  function pos(event) {
    return tools.imagePoint(event.clientX, event.clientY, canvas.getBoundingClientRect(), canvas.width, canvas.height);
  }
  el('mode').onchange = () => {
    setSampling(false); el('fill').disabled = !tools.shapeModes.has(el('mode').value);
  };
  canvas.addEventListener('pointerdown', event => {
    if (!ready || comparing || event.button !== 0) return;
    event.preventDefault();
    if (sampling) {sampleImage(event, canvas, ctx); return;}
    colorPanel(false); canvas.setPointerCapture(event.pointerId);
    stroke = {mode: el('mode').value, width: Number(el('width').value), color,
      text: el('text').value, fontSize: Number(el('fontSize').value), fill: el('fill').checked, points: [pos(event)]};
    redraw();
  });
  canvas.addEventListener('pointermove', event => {
    if (!stroke) return;
    if (stroke.points.length < 3000) stroke.points.push(pos(event)); redraw();
  });
  function finish() {
    if (!stroke) return;
    history.push(stroke); stroke = null; future = []; redraw();
    status('有未保存的绘图修订。点击「保存绘图修订」同步到题目。');
  }
  canvas.addEventListener('pointerup', event => {
    if (stroke) stroke.points.push(pos(event)); finish();
  });
  canvas.addEventListener('pointercancel', () => {stroke = null; redraw();});
  el('undo').onclick = () => {if (history.length) future.push(history.pop()); redraw(); status('已撤销。保存后生效。');};
  el('redo').onclick = () => {if (future.length) history.push(future.pop()); redraw(); status('已重做。保存后生效。');};
  el('plus').onclick = () => {scale = Math.min(4, scale * 1.3); size();};
  el('minus').onclick = () => {scale = Math.max(.1, scale / 1.3); size();};
  el('compare').onpointerdown = event => {event.preventDefault(); comparing = true; redraw();};
  window.addEventListener('pointerup', () => {if (comparing) {comparing = false; redraw();}});
  el('compare').onkeydown = event => {if (event.key === ' ' || event.key === 'Enter') {event.preventDefault(); comparing = true; redraw();}};
  el('compare').onkeyup = () => {comparing = false; redraw();};
  el('save').onclick = () => {
    if (!ready) return;
    finish(); comparing = false; redraw();
    post({type: 'streamlit:setComponentValue', dataType: 'json', value: {
      request_id: Date.now() + '-' + Math.random().toString(36).slice(2),
      source_hash: args.source_hash, scope: args.scope, region_id: args.region_id, revision: args.revision, data_url: canvas.toDataURL('image/png'),
    }}); status('正在保存…');
  };
  let syncing = false;
  for (const [from, to] of [[el('leftView'), el('rightView')], [el('rightView'), el('leftView')]]) from.onscroll = () => {
    if (syncing) return;
    syncing = true; to.scrollTop = from.scrollTop; to.scrollLeft = from.scrollLeft;
    requestAnimationFrame(() => syncing = false);
  };
  window.addEventListener('message', async event => {
    if (event.data?.type !== 'streamlit:render') return;
    args = event.data.args || {};
    const next = args.source_hash + ':' + args.scope + ':' + args.region_id + ':' + args.revision;
    if (next === signature) {size(); return;}
    ready = false; signature = next; history = []; future = []; stroke = null; comparing = false;
    setSampling(false); el('save').disabled = true; el('sample').disabled = true;
    try {
      await Promise.all([load(source, args.original), load(base, args.current)]);
      canvas.width = source.width; canvas.height = source.height; el('original').src = args.original;
      if (base.width !== canvas.width || base.height !== canvas.height) throw Error('size');
      sourceCanvas.width = canvas.width; sourceCanvas.height = canvas.height; sourceCtx.drawImage(source, 0, 0);
      scale = Math.min(1, Math.max(.1, el('rightView').clientWidth / canvas.width)); ready = true;
      el('save').disabled = false; el('sample').disabled = false; redraw(); size();
      status('对照原图人工绘制，点击「保存绘图修订」后同步到题目。');
    } catch {signature = ''; status('图片加载失败，请重新打开工具。');}
  });
  window.addEventListener('resize', size); post({type: 'streamlit:componentReady', apiVersion: 1});
})();
