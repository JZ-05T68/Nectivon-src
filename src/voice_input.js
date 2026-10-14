/* Optional, user-started dictation. No backend, credentials, or automatic submission. */
(() => {
  "use strict";
  const win = window;
  if (win.__nectivonVoiceInput) {
    win.__nectivonVoiceInput.scan();
    return;
  }
  const mic = '\x3csvg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" aria-hidden="true">\x3crect x="9" y="2" width="6" height="12" rx="3"/>\x3cpath d="M5 10v2a7 7 0 0 0 14 0v-2M12 19v3M8 22h8"/>\x3c/svg>';
  const stop = '\x3csvg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">\x3crect x="6" y="6" width="12" height="12" rx="2"/>\x3c/svg>';
  // SVG markup uses escaped opening brackets above so st.html's sanitizer retains
  // the trusted script. The stylesheet also travels to local custom components.
  const sheet = Array.from(document.querySelectorAll("style"))
    .find(item => item.textContent.includes(".nectivon-voice-host"))?.textContent || "";
  const documents = new WeakSet();
  const frames = new WeakSet();
  const controls = new WeakMap();
  const sensitiveFields = new WeakSet();
  let active = null;
  let pending = false;
  let noticeTimer;
  let notice;

  function message(doc, text, persistent = false) {
    let status = doc.getElementById("nectivon-voice-status");
    if (!status) {
      status = doc.createElement("div");
      status.id = "nectivon-voice-status";
      status.className = "nectivon-voice-status";
      status.setAttribute("role", "status");
      status.setAttribute("aria-live", "polite");
      doc.body.appendChild(status);
    }
    win.clearTimeout(noticeTimer);
    if (notice && notice !== status) notice.hidden = true;
    notice = status;
    status.textContent = text;
    status.hidden = false;
    if (!persistent) noticeTimer = win.setTimeout(() => { status.hidden = true; }, 7000);
  }

  function editable(field) {
    if (field.type === "password") sensitiveFields.add(field);
    return !sensitiveFields.has(field) && !field.disabled && !field.readOnly &&
      !field.closest('[inert]') &&
      (field.tagName === "TEXTAREA" || ["text", "search", "email", "url", "tel"]
        .includes(field.type));
  }

  function reset(session) {
    win.clearTimeout(session.timer);
    session.button.innerHTML = mic;
    session.button.setAttribute("aria-pressed", "false");
    session.button.title = "语音输入（由浏览器识别；可能需要联网）";
    if (active === session) active = null;
  }

  function cancel() {
    if (!active) return;
    const session = active;
    reset(session); // Invalidate before abort: late callbacks must never fill another field.
    session.recognition.abort();
    if (notice) notice.hidden = true;
  }

  function insert(field, text) {
    const view = field.ownerDocument.defaultView;
    const start = field.selectionStart ?? field.value.length;
    const end = field.selectionEnd ?? start;
    const capacity = field.maxLength < 0 ? text.length :
      Math.max(0, field.maxLength - field.value.length + end - start);
    const addition = text.slice(0, capacity);
    if (!addition) return false;
    const prototype = field.tagName === "TEXTAREA" ?
      view.HTMLTextAreaElement.prototype : view.HTMLInputElement.prototype;
    Object.getOwnPropertyDescriptor(prototype, "value").set.call(
      field, field.value.slice(0, start) + addition + field.value.slice(end));
    field.dispatchEvent(new view.Event("input", {bubbles: true}));
    field.dispatchEvent(new view.Event("change", {bubbles: true}));
    field.focus({preventScroll: true});
    if (field.setSelectionRange && field.selectionStart !== null) {
      field.setSelectionRange(start + addition.length, start + addition.length);
    }
    return true;
  }

  function start(field, button) {
    if (!editable(field) || !field.isConnected) return;
    if (active?.field === field) {
      active.recognition.stop();
      return;
    }
    cancel();
    const Recognition = win.SpeechRecognition || win.webkitSpeechRecognition;
    if (!Recognition) {
      message(field.ownerDocument, "当前浏览器不支持语音识别。可使用系统输入法听写，或在支持语音识别的浏览器中打开。" );
      return;
    }
    let session;
    try {
      const recognition = new Recognition();
      recognition.lang = field.lang || win.navigator.language || "zh-CN";
      recognition.continuous = false;
      recognition.interimResults = false;
      recognition.maxAlternatives = 1;
      session = {field, button, recognition, seen: new Set(), changed: false};
      active = session;
      button.innerHTML = stop;
      button.title = "正在语音输入，点击结束";
      button.setAttribute("aria-pressed", "true");
      recognition.onstart = () => {
        if (active === session) message(field.ownerDocument,
          "正在听，请说话；再次点击麦克风结束。文字不会自动提交。", true);
      };
      recognition.onresult = event => {
        if (active !== session || !field.isConnected || !editable(field)) return;
        for (let i = event.resultIndex; i < event.results.length; i++) {
          if (!event.results[i].isFinal || session.seen.has(i)) continue;
          session.seen.add(i);
          session.changed = insert(field, event.results[i][0].transcript) || session.changed;
        }
      };
      recognition.onerror = event => {
        if (active !== session) return;
        const errors = {
          "not-allowed": "未获得麦克风权限，请在浏览器地址栏允许麦克风后重试。",
          "service-not-allowed": "浏览器的语音识别服务不可用，可使用系统输入法听写。",
          "audio-capture": "没有可用的麦克风，请检查设备连接和系统麦克风权限。",
          "network": "语音识别服务连接失败，可检查网络或使用系统输入法听写。",
          "no-speech": "没有识别到语音，请点击麦克风重试。",
          "language-not-supported": "浏览器不支持当前语音语言，可使用系统输入法听写。",
          "aborted": "语音输入已结束。",
        };
        reset(session);
        message(field.ownerDocument, errors[event.error] || "语音识别失败，可重试或直接输入文字。");
      };
      recognition.onend = () => {
        if (active !== session) return;
        reset(session);
        // Streamlit commits text on blur; focus stays available for manual correction.
        if (session.changed && field.isConnected) field.blur();
        message(field.ownerDocument, session.changed ? "文字已填入，可修改后再提交或保存。" :
          "语音输入已结束，未识别到文字。可重试或直接输入。");
      };
      session.timer = win.setTimeout(() => {
        if (active === session) {
          recognition.stop();
          session.timer = win.setTimeout(() => {
            if (active === session) {
              cancel();
              message(field.ownerDocument, "语音输入已结束，可重新点击麦克风继续。");
            }
          }, 5000);
        }
      }, 60000);
      field.focus({preventScroll: true});
      recognition.start(); // The sole permission/recording entry point is a user click.
    } catch (error) {
      if (session) reset(session);
      message(field.ownerDocument, error.name === "NotAllowedError" ?
        "未获得麦克风权限，请在浏览器地址栏允许麦克风后重试。" :
        "无法启动浏览器语音识别，可重试或使用系统输入法听写。");
    }
  }

  function attach(field) {
    const previous = controls.get(field);
    if (!editable(field)) {
      if (previous) {
        previous.remove();
        field.classList.remove("nectivon-voice-field");
        controls.delete(field);
      }
      return;
    }
    if (previous?.isConnected) return;
    const doc = field.ownerDocument;
    let host = field.parentElement;
    // Streamlit's React-owned input wrappers remain intact. Plain custom-component
    // controls need their own wrapper so neighboring toolbar controls are not covered.
    if (!field.closest('[data-testid="stTextInput"], [data-testid="stTextArea"], [data-baseweb="select"], [data-testid="stChatInput"]') &&
        !host.classList.contains("nectivon-voice-host")) {
      const wrapper = doc.createElement("span");
      wrapper.className = "nectivon-voice-inline";
      wrapper.style.flex = doc.defaultView.getComputedStyle(field).flex;
      host.insertBefore(wrapper, field);
      wrapper.appendChild(field);
      host = wrapper;
    }
    host.classList.add("nectivon-voice-host");
    if (field.tagName === "TEXTAREA") host.classList.add("nectivon-voice-multiline");
    field.classList.add("nectivon-voice-field");
    const button = doc.createElement("button");
    button.type = "button";
    button.className = "nectivon-voice-button";
    button.innerHTML = mic;
    const label = field.getAttribute("aria-label") || field.labels?.[0]?.textContent ||
      field.placeholder || "文字框";
    button.setAttribute("aria-label", `语音输入：${label}`);
    button.setAttribute("aria-pressed", "false");
    button.title = "语音输入（由浏览器识别；可能需要联网）";
    button.addEventListener("mousedown", event => event.preventDefault());
    button.addEventListener("click", event => {
      event.preventDefault();
      event.stopPropagation();
      start(field, button);
    });
    host.appendChild(button);
    controls.set(field, button);
  }

  function scanDocument(doc) {
    if (!doc.body) return;
    if (!documents.has(doc)) {
      documents.add(doc);
      if (doc !== document && sheet) {
        const style = doc.createElement("style");
        style.textContent = sheet;
        doc.head.appendChild(style);
      }
      new win.MutationObserver(changes => {
        // Streaming answers and graph counters change text frequently. Rescan
        // only when controls or their enclosing elements change.
        if (changes.some(change => change.type === "attributes" ||
            [...change.addedNodes, ...change.removedNodes].some(node =>
              node.nodeType === 1 && (["INPUT", "TEXTAREA", "IFRAME"].includes(node.tagName) ||
                node.classList.contains("nectivon-voice-button") ||
                node.querySelector("input, textarea, iframe"))))) schedule();
      }).observe(doc.body, {
        childList: true, subtree: true, attributes: true,
        attributeFilter: ["disabled", "readonly", "type", "inert"],
      });
      doc.addEventListener("keydown", event => {
        if (event.key === "Escape" && active?.field.ownerDocument === doc) cancel();
      });
    }
    doc.querySelectorAll("input, textarea").forEach(attach);
    doc.querySelectorAll("iframe").forEach(frame => {
      if (!frames.has(frame)) {
        frames.add(frame);
        frame.addEventListener("load", schedule);
      }
      // Only components served by this local app are eligible, never external frames.
      try {
        // contentDocument is null for cross-origin pages. srcdoc inherits the local
        // origin even though location.origin reports "null", so it is included here.
        if (frame.contentDocument) scanDocument(frame.contentDocument);
      } catch { /* Cross-origin frames are deliberately outside the local UI. */ }
    });
  }

  function scan() {
    pending = false;
    if (active && (!active.field.isConnected || !editable(active.field))) cancel();
    scanDocument(document);
  }

  function schedule() {
    if (!pending) {
      pending = true;
      win.requestAnimationFrame(scan);
    }
  }

  win.__nectivonVoiceInput = {scan: schedule};
  win.addEventListener("pagehide", cancel);
  scan();
})();
