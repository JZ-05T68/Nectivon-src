"""Rebuild the offline component from Nectivon's verified local star-vault copy.

Never reads or writes the Desktop disaster-recovery source. The bundled code
is version-pinned and each adapter edit fails loudly if its anchor changes.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src import __version__  # noqa: E402

# Nectivon 语音/手势控制条：替换 star-vault 语音层里挂在 AR 栏上的装配段。
# 生成的控制条不依赖 AR/LLM 面板，手势开关代理回星图自带的开/关摄像头按钮；
# 其余语音识别、👌手势触发与指令解析逐字保留上游实现。
_NECTIVON_BUILDUI = """    /* ===== 装配（Nectivon 自带控制条，不依赖 AR 栏） ===== */
    function buildUI() {
      var bar = document.createElement('div');
      bar.id = 'nectivonVoiceBar';
      bar.className = 'nv-bar';
      bar.setAttribute('role', 'group');
      bar.setAttribute('aria-label', '语音与手势控制');

      elGesture = document.createElement('button');
      elGesture.className = 'nv-btn';
      elGesture.id = 'nvGesture';
      elGesture.type = 'button';
      elGesture.textContent = '✋ 开启手势';
      bar.appendChild(elGesture);

      var gsep = document.createElement('span');
      gsep.className = 'nv-sep';
      gsep.setAttribute('aria-hidden', 'true');
      bar.appendChild(gsep);

      elMic = document.createElement('button');
      elMic.className = 'nv-btn';
      elMic.id = 'micBtn';
      elMic.type = 'button';
      elMic.textContent = '🎤 说话（按住空格）';
      bar.appendChild(elMic);

      elText = document.createElement('span');
      elText.className = 'v-text';
      elText.id = 'vText';
      elText.textContent = '按住空格说话';
      bar.appendChild(elText);

      var lab = document.createElement('label');
      lab.className = 'nv-opt';
      elRead = document.createElement('input');
      elRead.type = 'checkbox';
      elRead.id = 'vRead';
      elRead.checked = true;
      lab.appendChild(elRead);
      lab.appendChild(document.createTextNode('朗读'));
      bar.appendChild(lab);

      var lab2 = document.createElement('label');
      lab2.className = 'nv-opt';
      elOk = document.createElement('input');
      elOk.type = 'checkbox';
      elOk.id = 'vOk';
      lab2.appendChild(elOk);
      elOk.checked = true;
      lab2.appendChild(document.createTextNode('👌手势'));
      bar.appendChild(lab2);

      elInput = document.createElement('input');
      elInput.className = 'v-input';
      elInput.id = 'vInput';
      elInput.type = 'text';
      elInput.placeholder = '听不清？直接打字：节点标题或内容关键词';
      elInput.hidden = !!SR;
      bar.appendChild(elInput);

      document.body.appendChild(bar);

      // 手势开关代理到星图自带的开启/关闭摄像头按钮，状态跟随 body.gesture-on。
      function syncGestureBtn() {
        var on = document.body.classList.contains('gesture-on');
        elGesture.textContent = on ? '✋ 关闭手势' : '✋ 开启手势';
        elGesture.classList.toggle('on', on);
        var status = document.getElementById('lensStatus');
        if (status) elGesture.title = status.textContent || '';
      }
      elGesture.addEventListener('click', function () {
        var on = document.body.classList.contains('gesture-on');
        var target = document.getElementById(on ? 'camOff' : 'camBtn');
        if (target) target.click();
      });
      syncGestureBtn();
      new MutationObserver(syncGestureBtn).observe(document.body, {
        attributes: true, attributeFilter: ['class']
      });

      elMic.addEventListener('click', function () { listening ? stopListen() : startListen(); });
      elRead.addEventListener('change', function (e) { readOn = e.target.checked; });
      elOk.addEventListener('change', function (e) { okOn = e.target.checked; });
      elInput.addEventListener('keydown', function (e) {
        if (e.isComposing || e.keyCode === 229 || e.key !== 'Enter') return;
        var v = elInput.value.trim();
        if (!v) return;
        show('“' + v + '”', 'hit');
        dispatch(v, []);
        elInput.value = '';
      });

      var spaceDown = false;
      window.addEventListener('keydown', function (e) {
        if (e.code !== 'Space' && e.key !== ' ') return;
        var tag = (e.target && e.target.tagName) || '';
        if (tag === 'INPUT' || tag === 'TEXTAREA' || e.metaKey || e.ctrlKey || e.altKey) return;
        if (e.repeat || spaceDown) return;
        spaceDown = true;
        e.preventDefault();
        startListen();
      });
      window.addEventListener('keyup', function (e) {
        if (e.code !== 'Space' && e.key !== ' ') return;
        if (!spaceDown) return;
        spaceDown = false;
        stopListen();
      });
      window.addEventListener('blur', function () {
        if (!spaceDown) return;
        spaceDown = false;
        stopListen();
      });

      hookLandmarks();
      var g = S.gesture;
      if (g && !g.__vPatched) {
        g.__vPatched = true;
        var os = g.start.bind(g);
        g.start = function () { return os().then(function (r) { hookLandmarks(); return r; }); };
      }
    }

"""

_ASSET_FILES = ("hand-model.js", "hand-wasm.js", "ok.js")

# 语音层逐字锚定改造：每条锚点在上游语音 IIFE 中必须恰好出现一次，变化即报错。
# 1) 说话前显式申请麦克风权限（触发浏览器授权提示），被拒时给出指引并回退打字；
# 2) 匹配层从“仅标题模糊匹配”扩展为“标题+内容片段+识别候选词”，
#    说出节点标题或内容（题干/知识点/页面文本）的一部分即可命中任意层级节点。
_VOICE_LAYER_TWEAKS = (
    (
        "    function startListen() {\n"
        "      if (listening) return;\n"
        "      if (!SR) {\n"
        "        if (elInput) elInput.hidden = false;\n"
        "        show('这个浏览器不支持语音识别，直接打字也行', 'miss');\n"
        "        return;\n"
        "      }\n"
        "      try {\n"
        "        rec = new SR();",
        "    async function startListen() {\n"
        "      if (listening) return;\n"
        "      if (!SR) {\n"
        "        if (elInput) elInput.hidden = false;\n"
        "        show('这个浏览器不支持语音识别，直接打字也行', 'miss');\n"
        "        return;\n"
        "      }\n"
        "      // Nectivon：先显式申请麦克风权限，拿到授权立即释放，再交给语音识别。\n"
        "      if (navigator.mediaDevices && navigator.mediaDevices.getUserMedia) {\n"
        "        show('正在获取麦克风权限…', 'live');\n"
        "        try {\n"
        "          var micStream = await navigator.mediaDevices.getUserMedia({ audio: true });\n"
        "          micStream.getTracks().forEach(function (t) { t.stop(); });\n"
        "        } catch (permErr) {\n"
        "          listening = false;\n"
        "          if (elMic) elMic.classList.remove('on');\n"
        "          var denied = permErr && (permErr.name === 'NotAllowedError'\n"
        "            || permErr.name === 'SecurityError');\n"
        "          var why = permErr && permErr.name ? permErr.name : '未知';\n"
        "          show(denied\n"
        "            ? '麦克风权限被拒绝：点击地址栏左侧的锁形/麦克风图标，允许后重试'\n"
        "            : '无法访问麦克风（' + why + '）', 'miss');\n"
        "          if (elInput) elInput.hidden = false;\n"
        "          return;\n"
        "        }\n"
        "      }\n"
        "      try {\n"
        "        rec = new SR();",
    ),
    (
        "    /* ============ 统一指令层 ============ */",
        "    /* 内容片段匹配（Nectivon）：说出标题或内容的一部分即可命中节点 */\n"
        "    function cjkGrams(s) {\n"
        "      var out = [], ms = String(s || '').match(/[㐀-鿿]{2,}/g) || [];\n"
        "      for (var i = 0; i < ms.length; i++)\n"
        "        for (var j = 0; j + 2 <= ms[i].length; j++) out.push(ms[i].substr(j, 2));\n"
        "      return out;\n"
        "    }\n"
        "    function contentScore(query, text) {\n"
        "      var nq = norm(query), nt = norm(text || '');\n"
        "      if (!nq || !nt) return 0;\n"
        "      if (nt.indexOf(nq) >= 0) return 0.97;\n"
        "      // 中文滑窗兜底：所说词组与原文词序不必完全一致，按二字覆盖度计分\n"
        "      var grams = cjkGrams(query);\n"
        "      if (grams.length) {\n"
        "        var gh = 0;\n"
        "        for (var g = 0; g < grams.length; g++) {\n"
        "          if (nt.indexOf(grams[g]) >= 0) gh++;\n"
        "        }\n"
        "        var cover = gh / grams.length;\n"
        "        if (cover >= 0.49) return Math.min(0.95, 0.62 + cover * 0.35);\n"
        "      }\n"
        "      var ts = toks(query), hit = 0, total = 0;\n"
        "      for (var i = 0; i < ts.length; i++) {\n"
        "        var t = norm(ts[i]);\n"
        "        if (!t) continue;\n"
        "        total++;\n"
        "        if (nt.indexOf(t) >= 0) hit++;\n"
        "      }\n"
        "      return total ? 0.9 * hit / total : 0;\n"
        "    }\n"
        "    function nodeScore(query, node) {\n"
        "      var s = node.title ? matchScore(query, node.title) : 0;\n"
        "      if (node.summary) {\n"
        "        s = Math.max(s, contentScore(query, node.title || ''),\n"
        "          contentScore(query, node.summary));\n"
        "      }\n"
        "      return s;\n"
        "    }\n"
        "\n"
        "    /* ============ 统一指令层 ============ */",
    ),
    (
        "      find: function (target) {\n"
        "        var nodes = (S.graph.graphData() || {}).nodes || [];\n"
        "        var best = null, bs = 0;\n"
        "        for (var i = 0; i < nodes.length; i++) {\n"
        "          var n = nodes[i];\n"
        "          if (!n || n.hub || !n.title) continue;\n"
        "          var s = matchScore(target, n.title);\n"
        "          if (s > bs) { bs = s; best = n; }\n"
        "        }\n"
        "        return { node: best, score: bs };\n"
        "      }",
        "      find: function (target, alts) {\n"
        "        var nodes = (S.graph.graphData() || {}).nodes || [];\n"
        "        var queries = [target];\n"
        "        if (alts) for (var k = 0; k < alts.length; k++) {\n"
        "          var a = String(alts[k] || '').trim();\n"
        "          if (a && queries.indexOf(a) < 0) queries.push(a);\n"
        "        }\n"
        "        var best = null, bs = 0;\n"
        "        for (var i = 0; i < nodes.length; i++) {\n"
        "          var n = nodes[i];\n"
        "          if (!n || n.hub) continue;\n"
        "          for (var q = 0; q < queries.length; q++) {\n"
        "            var s = nodeScore(queries[q], n);\n"
        "            if (s > bs) { bs = s; best = n; }\n"
        "          }\n"
        "        }\n"
        "        return { node: best, score: bs };\n"
        "      }",
    ),
    (
        "      for (var i = 0; i < nodes.length; i++) {\n"
        "        var n = nodes[i];\n"
        "        if (!n || n.hub || !n.title) continue;\n"
        "        var s = matchScore(target, n.title);\n"
        "        if (s >= 0.62) hits.push({ node: n, score: s });\n"
        "      }",
        "      for (var i = 0; i < nodes.length; i++) {\n"
        "        var n = nodes[i];\n"
        "        if (!n || n.hub) continue;\n"
        "        var s = nodeScore(target, n);\n"
        "        if (s >= 0.62) hits.push({ node: n, score: s });\n"
        "      }",
    ),
    (
        "      var hit = target ? Commands.find(target) : { node: null, score: 0 };",
        "      var hit = target ? Commands.find(target, lastAlts) : { node: null, score: 0 };",
    ),
)


def _extract_voice_layer(source_html: str) -> str:
    """Return the upstream voice/👌-gesture IIFE verbatim from the verified snapshot."""

    scripts = re.findall(r"<script[^>]*>([\s\S]*?)</script>", source_html)
    matches = [s for s in scripts if "webkitSpeechRecognition" in s and "var Commands = {" in s]
    if len(matches) != 1:
        raise ValueError("star-vault 语音控制层定位失败：应恰好存在一段脚本")
    return "  <script>\n" + matches[0] + "  </script>"


def _adapt_voice_layer(script: str) -> str:
    """Re-host the voice UI on a Nectivon-owned bar; keep recognition logic upstream."""

    tweaks = {
        "var elText, elMic, elInput, elRead, elOk;":
            "var elText, elMic, elInput, elRead, elOk, elGesture;",
    }
    for before, after in tweaks.items():
        if script.count(before) != 1:
            raise ValueError(f"语音层适配锚点发生变化：{before}")
        script = script.replace(before, after)
    for before, after in _VOICE_LAYER_TWEAKS:
        if script.count(before) != 1:
            raise ValueError(f"语音层适配锚点发生变化：{before[:60]}…")
        script = script.replace(before, after)
    start_anchor = "    /* ============ 装配 ============ */"
    end_anchor = "    function whenReady(cb) {"
    if script.count(start_anchor) != 1 or script.count(end_anchor) != 1:
        raise ValueError("语音层装配段锚点发生变化，需同步更新 Nectivon 控制条")
    start = script.index(start_anchor)
    end = script.index(end_anchor)
    return script[:start] + _NECTIVON_BUILDUI + script[end:]


def build_component(destination: Path | None = None) -> Path:
    """Build the view from a verified snapshot, retaining all upstream source files."""

    source = ROOT / "third_party" / "star_vault"
    manifest = json.loads((source / "source_manifest.json").read_text(encoding="utf-8"))
    for item in manifest["files"]:
        content = (source / item["path"]).read_bytes()
        if hashlib.sha256(content).hexdigest() != item["sha256"]:
            raise ValueError(f"内部 star-vault 源码副本校验失败：{item['path']}")
    source_html = (source / "index.html").read_text(encoding="utf-8")
    voice_layer = _adapt_voice_layer(_extract_voice_layer(source_html))
    html = source_html
    scripts = list(re.finditer(r"<script[^>]*>([\s\S]*?)</script>", html))
    for match in reversed(scripts[1:]):
        html = html[:match.start()] + html[match.end():]
    replacements = {
        "async function wG(){": "async function wG(){return await window.nectivonInitialPayload;",
        "function c9(e){": "function c9(e){window.nectivonSelected(e);",
        "function l9(e){": "function l9(e){return '';",
        "window.__starmap={graph:I7,": "window.__starmap={setPayload:D9,graph:I7,",
        # 手势入口保持上游原样：用户已明确要求在知识串联中启用手势与语音控制。
        "C7.mode===`live`?null:EG(w9())": "null",
        "local:{canWatch:rX,": "local:{canWatch:false,",
        "async pick(){let e=await T9.pick();": "async pick(){return null;let e=await T9.pick();",
        "async fromFiles(e){let t=await T9.fromFileList(e);":
            "async fromFiles(e){return null;let t=await T9.fromFileList(e);",
    }
    for before, after in replacements.items():
        if html.count(before) != 1:
            raise ValueError(f"星图适配锚点发生变化：{before}")
        html = html.replace(before, after)
    html = html.replace(
        "<title>知识星图</title>", f"<title>知识星图 · Nectivon v{__version__}</title>"
    )
    html = html.replace("把 Obsidian 笔记变成可以用手势浏览的 3D 星空。",
                        "查看 Nectivon 本地知识、题目和来源的关联。")
    html = html.replace("篇笔记", "个节点").replace("条双链", "条关联").replace("双链", "关联")
    html = html.replace("</head>", '<link rel="stylesheet" href="nectivon.css"></head>')
    html = html.replace("  <script>", '  <script src="bridge.js"></script>\n  <script>', 1)
    # 手势模型经 assets/ 以脚本标签装入内存，再转 blob: URL 交给 MediaPipe，
    # 因此 script/connect 允许 blob:；仍不放开任何外部来源，保持离线闭环。
    policy = (
        "default-src 'self' data: blob:; connect-src 'self' blob:; media-src blob:; "
        "frame-src 'none'; object-src 'none'; "
        "script-src 'self' blob: 'unsafe-inline' 'unsafe-eval'; "
        "worker-src 'self' blob:; style-src 'self' 'unsafe-inline'; font-src data:"
    )
    html = html.replace('<meta charset="UTF-8" />', '<meta charset="UTF-8" />\n  '
                        f'<meta http-equiv="Content-Security-Policy" content="{policy}" />')
    voice_header = ("<!-- Nectivon: 语音/👌手势控制层，由构建脚本从已校验的 star-vault 副本移植；"
                    "指令解析在浏览器本地完成，语音识别走浏览器自带能力。 -->\n  ")
    html = html.replace("</body>", voice_header + voice_layer + "\n\n</body>")
    destination = destination or ROOT / "src" / "components" / "knowledge_starmap"
    destination.mkdir(parents=True, exist_ok=True)
    (destination / "assets").mkdir(exist_ok=True)
    for name in _ASSET_FILES:
        shutil.copyfile(source / "assets" / name, destination / "assets" / name)
    result = destination / "index.html"
    result.write_text(html, encoding="utf-8")
    return result


if __name__ == "__main__":
    print(build_component())
