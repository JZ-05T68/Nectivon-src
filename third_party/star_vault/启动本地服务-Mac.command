#!/bin/bash
# 知识星图 · 一键本地服务（可选）
# 作用：把页面挂在 http://localhost 上打开。
#       一般用不到——双击 index.html 已经能跑。只有在 Safari 里摄像头用不了、
#       或个别大模型服务商报跨域时，才需要这个方式。
# 用法：双击本文件；演示结束后回到这个窗口按 Ctrl+C。
cd "$(dirname "$0")" || exit 1

PY=""
for c in python3 python; do
  if command -v "$c" >/dev/null 2>&1; then PY="$c"; break; fi
done

if [ -z "$PY" ]; then
  echo "没找到 Python，已直接用默认浏览器打开 index.html。"
  echo "建议：右键 index.html → 打开方式 → Google Chrome 或 Edge"
  open "index.html"
  exit 0
fi

PORT=$("$PY" -c 'import socket;s=socket.socket();s.bind(("127.0.0.1",0));print(s.getsockname()[1]);s.close()')
echo "本地服务已启动： http://localhost:$PORT/index.html"
echo "浏览器稍后自动打开。演示结束后，回到这个窗口按 Ctrl+C 结束。"

"$PY" -m http.server "$PORT" --bind 127.0.0.1 >/dev/null 2>&1 &
PID=$!
sleep 1
open "http://localhost:$PORT/index.html"
trap 'kill $PID 2>/dev/null' EXIT
wait $PID
