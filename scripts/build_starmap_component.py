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
# 识别与指令解析沿用上游实现，控件布局由 Nectivon 管理。
_NECTIVON_BUILDUI = """    /* ===== 装配（Nectivon 自带控制条，不依赖 AR 栏） ===== */
    function buildUI() {
      var footer = document.createElement('div');
      footer.id = 'nectivonFooter';
      footer.className = 'nv-footer';
      var legend = document.getElementById('gestures');
      if (legend) footer.appendChild(legend);
      document.body.appendChild(footer);
      relateBuildUI(footer);

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

      var options = document.createElement('div');
      options.id = 'nectivonVoiceOptions';
      options.className = 'nv-options';
      options.setAttribute('role', 'group');
      options.setAttribute('aria-label', '语音选项');

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
      options.appendChild(lab);

      var lab2 = document.createElement('label');
      lab2.className = 'nv-opt';
      elOk = document.createElement('input');
      elOk.type = 'checkbox';
      elOk.id = 'vOk';
      lab2.appendChild(elOk);
      elOk.checked = true;
      lab2.appendChild(document.createTextNode('👌手势'));
      options.appendChild(lab2);

      elInput = document.createElement('input');
      elInput.className = 'v-input';
      elInput.id = 'vInput';
      elInput.type = 'text';
      elInput.placeholder = '听不清？直接打字：节点标题或内容关键词';
      elInput.hidden = !!SR;
      bar.appendChild(elInput);

      footer.appendChild(bar);
      document.body.appendChild(options);
      // 两排控件在同一容器中排版，实际高度同时用于星图与摄像头的避让。
      new ResizeObserver(function () {
        document.documentElement.style.setProperty('--nv-footer-height',
          Math.ceil(footer.getBoundingClientRect().height) + 'px');
        window.dispatchEvent(new Event('resize'));
      }).observe(footer);

      // 手势开关代理到星图自带的开启/关闭摄像头按钮，状态跟随 body.gesture-on。
      function syncGestureBtn() {
        var on = document.body.classList.contains('gesture-on');
        if (!on) relateReset();
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

# Nectivon 星图关系动作：语音/打字「连接/断开」两个知识点星子之间的关联。
# 指令解析在浏览器本地完成；动作只经 bridge 发回父页面持久化，组件不直接写库。
_RELATE_LAYER = """    /* Nectivon 星图关系动作：改变星子之间的关联（经父页面确认后持久化） */
    var RELATE_TYPES = [
      ['派生自', 'derived_from'], ['来源于', 'derived_from'], ['源于', 'derived_from'],
      ['源自', 'derived_from'],
      ['依赖于', 'requires'], ['依赖', 'requires'], ['取决于', 'requires'], ['前置', 'requires'],
      ['支持', 'supports'], ['印证', 'supports'], ['佐证', 'supports'],
      ['相矛盾', 'contradicts'], ['矛盾', 'contradicts'],
      ['冲突', 'contradicts'], ['相悖', 'contradicts'],
      ['的实例', 'example_of'], ['的特例', 'example_of'], ['的例子', 'example_of']
    ];
    var RELATE_LABELS = { derived_from: '派生自', requires: '依赖', supports: '支持',
      contradicts: '相矛盾', example_of: '是…的实例', relates_to: '相关' };
    var RELATE_LINK_HEAD = /^(连接|连上|关联|连起来|联系起来|接起来|建立关系|加上关系|连线|链接)/;
    var RELATE_UNLINK_HEAD = /^(断开|断掉|解除|取消关系|删除关系|拆掉关系|拆掉)/;
    var RELATE_TAIL = /(连接起来|连起来|联系起来|建立关系|连接|关联|断开|解除)$/;
    var RELATE_SKIP = [
      /^(搜索|搜一下|搜搜|查找|查一下|找一下|找找|哪里有|有没有)/,
      /^(打开|展开|点开|看一下|看一看|看一眼|讲讲|讲一下|说说)/,
      /^(跳到|进入|切换到|定位到|读一下|念一下|朗读|读出来)/,
      /^(收起|回到|下一个|上一个|放大|缩小)/
    ];
    var RELATE_JOIN = /[和与跟同及]/;

    function relateSkipped(text) {
      for (var i = 0; i < RELATE_SKIP.length; i++) {
        if (RELATE_SKIP[i].test(text)) return true;
      }
      return false;
    }

    function relateTrimSide(text) {
      return String(text || '').replace(
        /^(的|了|是|把|将|与|和|跟|同|及)+|(的|了|是|了|与|和|跟|同|及)+$/g, '');
    }
    function relateHit(name) {
      var text = relateTrimSide(name);
      if (!text) return { node: null, score: 0 };
      var hit = Commands.find(text);
      return hit && hit.node && hit.score >= 0.62 ? hit : { node: null, score: 0 };
    }
    function relateSides(text) {
      // 在每个连接词处尝试切分，取两侧都能命中且最低分最高的切法，避免标题含「和」时切错
      var best = null;
      for (var i = 0; i < text.length; i++) {
        if (!RELATE_JOIN.test(text.charAt(i))) continue;
        var left = relateHit(text.slice(0, i)), right = relateHit(text.slice(i + 1));
        if (!left.node || !right.node) continue;
        var low = Math.min(left.score, right.score);
        if (!best || low > best.low) best = { a: left.node, b: right.node, low: low };
      }
      return best;
    }
    function relateTypedWord(text) {
      for (var i = 0; i < RELATE_TYPES.length; i++) {
        var at = text.indexOf(RELATE_TYPES[i][0]);
        if (at > 0 && at + RELATE_TYPES[i][0].length < text.length) {
          return { at: at, word: RELATE_TYPES[i][0], type: RELATE_TYPES[i][1] };
        }
      }
      return null;
    }
    function parseRelate(text) {
      var t = String(text || '').replace(PUNC, '').trim();
      if (!t || relateSkipped(t)) return null;
      t = t.replace(/^(请|帮我|我想|我要|麻烦)/, '').replace(/(一下|那个|这个)$/, '').trim();
      var unlink = false, verbless = null, typed = null;
      if (RELATE_UNLINK_HEAD.test(t)) {
        unlink = true;
        verbless = t.replace(RELATE_UNLINK_HEAD, '');
      }
      else if (RELATE_LINK_HEAD.test(t)) verbless = t.replace(RELATE_LINK_HEAD, '');
      else if (RELATE_TAIL.test(t) && RELATE_TAIL.exec(t)[0].length < t.length) {
        var word = RELATE_TAIL.exec(t)[0];
        unlink = /^(断开|解除)$/.test(word);
        verbless = t.slice(0, t.length - word.length);
      } else typed = relateTypedWord(t);
      if (unlink) typed = null;
      var pair = null;
      if (typed) {
        var left = relateHit(t.slice(0, typed.at));
        var right = relateHit(t.slice(typed.at + typed.word.length));
        if (left.node && right.node) pair = { a: left.node, b: right.node };
      } else if (verbless !== null) {
        pair = relateSides(verbless.trim());
        if (!pair) {
          var other = relateHit(verbless.trim());
          var anchor = S.state.selected || S.state.hovered;
          if (other.node && anchor && !anchor.hub) pair = { a: anchor, b: other.node };
        }
      }
      if (!pair || pair.a.id === pair.b.id) return null;
      return { act: unlink ? 'unlink' : 'link', a: pair.a, b: pair.b,
        relation_type: unlink || !typed ? 'relates_to' : typed.type };
    }
    function doRelate(cmd) {
      if (!window.nectivonRelationChange) { toast('这个版本不支持星图关系动作'); return; }
      relateReset();
      window.nectivonRelationChange({
        action: cmd.act, source: cmd.a.id, target: cmd.b.id,
        relation_type: cmd.relation_type, source_title: cmd.a.title, target_title: cmd.b.title
      });
      var label = RELATE_LABELS[cmd.relation_type] || '相关';
      if (cmd.act === 'unlink') {
        toast('断开请求待确认：《' + cmd.a.title + '》 ↔ 《' + cmd.b.title + '》');
        if (readOn) Commands.speak('断开请求已发出，请在页面上确认');
      } else {
        toast('已连接：《' + cmd.a.title + '》 ' + label + ' 《' + cmd.b.title + '》');
        if (readOn) Commands.speak('已连接：' + cmd.a.title + label + cmd.b.title);
      }
    }

    /* —— 手势连接/断开：选星 → 手势锁定起点 → 再选星和同一手势 → 页面确认 —— */
    var relateFrames = 0, relateKind = '', relateCooldown = 0;
    var relateSource = null, relateAction = '', relateReleased = true, relateFrameNode = '';
    var relatePanel = null, relateText = null;
    function relateReset() {
      relateSource = null; relateAction = '';
      S.state.relationSelecting = false;
      relateFrames = 0; relateKind = ''; relateFrameNode = '';
      if (relatePanel) relatePanel.hidden = true;
    }
    function relateBuildUI(footer) {
      relatePanel = document.createElement('div');
      relatePanel.id = 'nectivonRelationProgress';
      relatePanel.className = 'nv-relation-progress';
      relatePanel.hidden = true;
      relateText = document.createElement('span');
      relateText.setAttribute('role', 'status');
      relateText.setAttribute('aria-live', 'polite');
      var cancel = document.createElement('button');
      cancel.type = 'button'; cancel.className = 'nv-btn';
      cancel.textContent = '取消这次操作';
      cancel.addEventListener('click', function () {
        relateReset(); toast('已取消，请重新选择第一颗知识点星');
      });
      relatePanel.appendChild(relateText); relatePanel.appendChild(cancel);
      footer.appendChild(relatePanel);
      window.addEventListener('nectivon:payload-changed', relateReset);
    }
    function relateDist(h, a, b) {
      var dx = h[a].x - h[b].x, dy = h[a].y - h[b].y;
      return Math.sqrt(dx * dx + dy * dy);
    }
    function relatePalmSize(h) { return Math.max(relateDist(h, 0, 9), 0.04); }
    function relateExtended(h, tip, pip, ratio) {
      return relateDist(h, 0, tip) > relatePalmSize(h) * ratio
        && relateDist(h, 0, tip) > relateDist(h, 0, pip);
    }
    function relateFolded(h, tip, pip) {
      return !relateExtended(h, tip, pip, 1.15);
    }
    function relateThumbsUp(h) {
      var size = relatePalmSize(h);
      if (relateDist(h, 0, 4) < size * 1.4) return false;
      if (h[4].y > h[2].y) return false;
      return relateFolded(h, 8, 6) && relateFolded(h, 12, 10)
        && relateFolded(h, 16, 14) && relateFolded(h, 20, 18);
    }
    function relatePeace(h) {
      if (!relateExtended(h, 8, 6, 1.3)) return false;
      if (!relateExtended(h, 12, 10, 1.3)) return false;
      if (relateDist(h, 8, 12) < relatePalmSize(h) * 0.4) return false;
      return relateFolded(h, 16, 14) && relateFolded(h, 20, 18);
    }
    function relateGesture(lms) {
      if (!lms || lms.length !== 1 || !lms[0] || lms[0].length < 21) return '';
      var h = lms[0];
      if (okGesture(lms)) return '';
      if (relateThumbsUp(h)) return 'link';
      if (relatePeace(h)) return 'unlink';
      return '';
    }
    // 关系手势不再被上游误当成握拳，双手缩放仍由上游处理。
    window.nectivonRelationPose = relateGesture;
    function relateSend(kind, a, b) {
      window.nectivonRelationChange({
        action: kind, source: a.id, target: b.id, relation_type: 'relates_to',
        source_title: a.title, target_title: b.title, require_confirmation: true
      });
      var label = kind === 'unlink' ? '断开' : '连接';
      toast('两星已选好：《' + a.title + '》 ↔ 《' + b.title
        + '》。请在星图下方点击「确认' + label + '」');
      if (readOn) Commands.speak('两星已选好，请在星图下方确认' + label);
    }
    function relateApply(kind) {
      if (!window.nectivonRelationChange) { toast('这个版本不支持星图关系动作'); return; }
      var current = S.state.selected;
      if (!current || current.hub || !String(current.id).startsWith('knowledge:')) {
        toast('请先用食指停留选中一颗知识点星，再比对应手势'); return;
      }
      if (!relateSource) {
        relateSource = { id: current.id, title: current.title }; relateAction = kind;
        S.state.relationSelecting = true;
        var pose = kind === 'link' ? '👍 拇指朝上' : '✌️ 二指张开';
        var message = '已锁定第一颗星《' + current.title
          + '》，请松开手势，再用食指选中第二颗知识点星，并再次比 ' + pose;
        if (relatePanel) { relateText.textContent = message; relatePanel.hidden = false; }
        toast(message);
        if (readOn) Commands.speak('已锁定第一颗星，请选择第二颗星并重复相同手势');
        return;
      }
      if (kind !== relateAction) { toast('请重复第一步的相同手势，或取消后重新开始'); return; }
      if (current.id === relateSource.id) { toast('两颗星需要不同，请选中第二颗知识点星'); return; }
      relateSend(kind, relateSource, current);
      relateReset();
    }
    function relateFrame(lms) {
      var kind = relateGesture(lms);
      if (relateCooldown > 0) relateCooldown--;
      if (!kind) {
        relateReleased = true;
        relateFrames = 0; relateKind = ''; relateFrameNode = ''; return;
      }
      if (!relateReleased || relateCooldown > 0) return;
      var node = S.state.selected, id = node ? node.id : '';
      if (kind !== relateKind || id !== relateFrameNode) {
        relateKind = kind; relateFrameNode = id; relateFrames = 1; return;
      }
      relateFrames++;
      if (relateFrames < 4) return;
      relateFrames = 0; relateKind = '';
      relateCooldown = 45;
      relateReleased = false;
      relateApply(kind);
    }

"""

_ASSET_FILES = ("hand-model.js", "hand-wasm.js", "ok.js")

# Mouse clicks use the same screen-space picker as gestures, even if a hub covers a star.
# ForceGraph suppresses these callbacks after dragging, so rotation remains unchanged.
_STAR_SELECTION_HELPERS = """function nectivonSelectStar(node) {
  i9();
  if (!node) { d9(); c9(null); return; }
  if (node.hub) { n9(k7.indexOf(node.cat)); return; }
  $.selected === node ? u9(node) : c9(node);
}
function nectivonMousePick(event, fallback = null) {
  const rect = g7('sky').getBoundingClientRect();
  const node = h9(event.clientX - rect.left, event.clientY - rect.top, 18) || fallback;
  nectivonSelectStar(node);
}
"""

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
    # 星图关系动作：注入 连接/断开 指令的解析与执行，并挂入统一 parse/run。
    (
        "    /* ============ 意图解析 ============ */",
        _RELATE_LAYER + "    /* ============ 意图解析 ============ */",
    ),
    (
        "      var isSearch = /(搜索|搜一下|搜搜|查找|查一下|找一下|找找|哪里有"
        "|有没有)/.test(low);",
        "      var relate = parseRelate(t);\n"
        "      if (relate) return relate;\n"
        "      var isSearch = /(搜索|搜一下|搜搜|查找|查一下|找一下|找找|哪里有"
        "|有没有)/.test(low);",
    ),
    (
        "        case 'search': doSearch(cmd); break;",
        "        case 'search': doSearch(cmd); break;\n"
        "        case 'link': case 'unlink': doRelate(cmd); break;",
    ),
    # 手势连接/断开：复用语音层既有的 detectForVideo 钩子，在每帧检测 👍/✌️。
    (
        "    function onFrame(lms) {\n"
        "      if (!okOn) return;",
        "    function onFrame(lms) {\n"
        "      relateFrame(lms);\n"
        "      if (!okOn) return;",
    ),
    (
        "          try { onFrame(r && r.landmarks); } catch (e) {}",
        "          try { onFrame(r && r.landmarks); } catch (e) {\n"
        "            console.error('Nectivon 手势帧处理失败', e);\n"
        "            if (!lm.__nvFrameError) {\n"
        "              lm.__nvFrameError = true;\n"
        "              toast('手势处理遇到问题，请关闭手势后重新开启');\n"
        "            }\n"
        "          }",
    ),
    (
        "        lm.__vHook = true;\n      } catch (e) {}",
        "        lm.__vHook = true;\n      } catch (e) {\n"
        "        console.error('Nectivon 手势检测回调安装失败', e);\n"
        "        toast('手势检测未就绪，请关闭手势后重新开启');\n"
        "      }",
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
        # Dimming unrelated nodes is presentation, never an eligibility rule for selection.
        "if(n.alpha<.5||(m9.set(n.x,n.y,n.z).project(B7),m9.z>1))continue;":
            "if((m9.set(n.x,n.y,n.z).project(B7),m9.z>1))continue;",
        "function h9(": _STAR_SELECTION_HELPERS + "function h9(",
        # A nebula's larger sprite must not outweigh a nearer knowledge star.
        "s=Math.hypot(a-e,o-t)-(n.hub?12:0);": "s=Math.hypot(a-e,o-t);",
        ".onNodeClick(e=>{if(i9(),e.hub){n9(k7.indexOf(e.cat));return}"
        "$.selected===e?u9(e):c9(e)}).onBackgroundClick(()=>{i9(),d9(),c9(null)})":
            ".onNodeClick((node,event)=>nectivonMousePick(event,node))"
            ".onBackgroundClick(nectivonMousePick)"
            ".onLinkClick((link,event)=>nectivonMousePick(event))",
        # While choosing a second endpoint, knowledge stars in all subjects stay readable.
        "s&&(i=n===s||c?.has(n.id)?1:Math.min(i,.22)),n.alpha+=":
            "s&&(i=n===s||c?.has(n.id)?1:Math.min(i,.22));"
            "let relationCandidate=$.relationSelecting&&String(n.id).startsWith(`knowledge:`);"
            "relationCandidate&&(i=1);n.alpha+=",
        "let m=n.hub||o===n.cat&&n.alpha>.5||n===s||n===l||!!(s&&c?.has(n.id))||d<1;":
            "let m=relationCandidate||n.hub||o===n.cat&&n.alpha>.5||n===s||n===l||"
            "!!(s&&c?.has(n.id))||d<1;",
        # 为底部说明与控制条留出真实空间，渲染、选星和摄像头光标使用同一高度。
        "i*window.innerHeight,t);this.o.onPoint(a,o)":
            "i*window.nectivonMapHeight(),t);this.o.onPoint(a,o)",
        "1.6/(innerWidth/innerHeight)": "1.6/(innerWidth/window.nectivonMapHeight())",
        "Math.min(innerHeight-r-16,a)": "Math.min(window.nectivonMapHeight()-r-16,a)",
        "o=(1-m9.y)/2*innerHeight": "o=(1-m9.y)/2*window.nectivonMapHeight()",
        "I7.width(innerWidth).height(innerHeight),W7.setSize(innerWidth*H7,innerHeight*H7)":
            "I7.width(innerWidth).height(window.nectivonMapHeight()),"
            "W7.setSize(innerWidth*H7,window.nectivonMapHeight()*H7)",
        # 拇指朝上会被上游识别为握拳；关系手势先消费，避免展开详情干扰动作。
        "let r=n[0],i=this.stablePose(u7(r)),a=d7(r);":
            "let relationPose=window.nectivonRelationPose?.(n);"
            "if(relationPose){this.history=[],this.prevPalm=null,"
            "this.setPose(relationPose===`link`?`relate`:`unrelate`),"
            "this.o.onStatus(relationPose===`link`?`拇指朝上：连接两星`:"
            "`二指张开：断开待确认`,relationPose);return}"
            "let r=n[0],i=this.stablePose(u7(r)),a=d7(r);",
        "function l9(e){": "function l9(e){return '';",
        "window.__starmap={graph:I7,": "window.__starmap={setPayload:D9,graph:I7,",
        # Retain saved relation metadata and recognise shared-subject endpoints.
        "p.push({source:t.source,target:t.target,"
        "kind:u.get(t.source)===u.get(t.target)?`note`:`bridge`})":
            "p.push({...t,kind:t.cross_subject||"
            "u.get(t.source)!==u.get(t.target)?`bridge`:`note`})",
        # Existing knowledge relations across subjects need a visible direct line.
        "e.baseAlpha={tag:.1,note:.34,bridge:.16,xtag:.05}[e.kind]":
            "e.baseAlpha=e.cross_subject?0.48:{tag:.1,note:.34,bridge:.16,xtag:.05}[e.kind]",
        # Count shared nodes in every connected subject, including secondary hubs.
        "s.forEach(e=>d.set(e.cat,(d.get(e.cat)||0)+1));":
            "s.forEach(e=>d.set(e.cat,(d.get(e.cat)||0)+1));"
            "for(let e of l){let t=e.target.slice(4);d.set(t,(d.get(t)||0)+1)}",
        # Hide empty groups, including groups edited in the renderer's settings.
        # A shared node's secondary group remains visible through its cross-tag edge.
        "let f=[...n.filter(e=>!e.hidden),a,o].filter(e=>!e.special||d.get(e.id));":
            "let occupiedTags=new Set(l.map(e=>e.target)),"
            "f=[...n.filter(e=>!e.hidden),a,o].filter("
            "e=>d.get(e.id)||occupiedTags.has(`hub:${e.id}`));",
        # Detach removed sprites before the CSS label renderer can reattach old labels.
        "for(let[e,t]of n)E7.has(e)||t.labelEl?.remove();":
            "for(let[e,t]of n)if(!E7.has(e)){"
            "t.sprite?.removeFromParent();t.labelEl?.remove();}",
        # 手势入口保持上游原样：用户已明确要求在知识串联中启用手势与语音控制。
        "C7.mode===`live`?null:EG(w9())": "null",
        "local:{canWatch:rX,": "local:{canWatch:false,",
        "async pick(){let e=await T9.pick();": "async pick(){return null;let e=await T9.pick();",
        "async fromFiles(e){let t=await T9.fromFileList(e);":
            "async fromFiles(e){return null;let t=await T9.fromFileList(e);",
        # 图例补充 Nectivon 关系手势（👍 连接 / ✌️ 断开需确认），沿用上游条目样式。
        "    <li data-g=\"zoom\"><span class=\"g-icon\" aria-hidden=\"true\">🙌</span>"
        "<span>双手张开合拢<br /><em>缩放</em></span></li>\n  </ol>":
            "    <li data-g=\"zoom\"><span class=\"g-icon\" aria-hidden=\"true\">🙌</span>"
            "<span>双手张开合拢<br /><em>缩放</em></span></li>\n"
            "    <li data-g=\"relate\"><span class=\"g-icon\" aria-hidden=\"true\">👍</span>"
            "<span>选一星比 👍，再选一星比 👍<br /><em>最后确认连接</em></span></li>\n"
            "    <li data-g=\"unrelate\"><span class=\"g-icon\" aria-hidden=\"true\">✌️</span>"
            "<span>选一星比 ✌️，再选一星比 ✌️<br /><em>最后确认断开</em></span></li>\n  </ol>",
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
