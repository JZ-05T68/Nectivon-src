/* Streamlit protocol bridge. Knowledge data is supplied by the local SQLite service. */
(() => {
  "use strict";
  const send = (type, extra = {}) => window.parent.postMessage(
    {isStreamlitMessage: true, type, ...extra}, "*"
  );
  let resolveInitial;
  let initialized = false;
  let revision = "";
  let sequence = 0;
  window.nectivonInitialPayload = new Promise(resolve => { resolveInitial = resolve; });
  window.nectivonSelected = node => {
    const id = node && !node.hub ? node.id : null;
    if (!id) return;
    send("streamlit:setComponentValue", {
      value: {node_id: id, event_id: `${Date.now()}:${++sequence}`}, dataType: "json"
    });
  };
  window.addEventListener("message", event => {
    if (event.source !== window.parent || event.data?.type !== "streamlit:render") return;
    const args = event.data.args;
    if (!args?.payload?.vault || args.revision === revision) return;
    revision = args.revision;
    if (!initialized) {
      initialized = true;
      resolveInitial(args.payload);
    } else if (window.__starmap) {
      window.__starmap.setPayload(args.payload);
    }
  });
  window.addEventListener("error", () => {
    let panel = document.getElementById("nectivon-error");
    if (!panel) {
      panel = document.createElement("p");
      panel.id = "nectivon-error";
      panel.textContent = "星图渲染遇到问题，请使用页面下方的节点列表查看关联与来源。";
      document.body.append(panel);
    }
  });
  send("streamlit:componentReady", {apiVersion: 1});
  send("streamlit:setFrameHeight", {height: 620});
})();
