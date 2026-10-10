# star-vault 本地源码副本

本目录由桌面 `star-vault` 的 12 个文件逐字节复制而来。`source_manifest.json`
记录完整 SHA-256 清单；原项目的 `verification.json` 保留其上游身份记录。
原始源码及启动脚本仅用于追溯和灾备，Nectivon 不运行这里的独立服务。

运行副本位于 `src/components/knowledge_starmap`，由
`python scripts/build_starmap_component.py` 从本目录重建。构建前会验证所有源文件，
不访问桌面目录或网络。修改后的运行代码不写回本目录中的原始源文件。

v0.8.7 接入范围：保留 Three.js 星空、鼠标旋转/缩放、星座切换和节点详情；
数据由 Streamlit 从当前 Nectivon SQLite 注入，节点选择返回同一个本地页面。
独立 Obsidian 导入/监听、浏览器 AI 密钥与付费调用、AR 仍不进入接入范围。
组件 CSP 只允许同源与 blob: 资源，不放开任何外部来源；浏览器仅持有临时展示数据，不建立另一份知识库。

2026-10-07 起按用户明确要求，星图内启用上游的手势控制与语音操控（范围调整记录见
`docs/V087_STARMAP_GESTURE_VOICE.md`）：手势模型（MediaPipe HandLandmarker 的
`assets/hand-model.js`、`assets/hand-wasm.js`）随组件本地打包、经校验复制；
语音指令解析与模糊匹配在浏览器本地完成，语音识别走浏览器自带 Web Speech 服务
（Chrome/Edge 需联网，不可用时回退打字输入）。摄像头画面仅在本机处理，不上传。

来源与授权说明：这是用户指定的代码集成。提供的源码快照未附顶层 LICENSE。
2026-10-10，用户明确确认有权将此源码、资源及改编后的星图公开上传到 Nectivon-src。
本记录保留该授权事实，不替代依赖中的原有许可证声明，也不额外授予未声明的开源许可。
