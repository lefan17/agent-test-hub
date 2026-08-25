#!/usr/bin/env bash
# AgentTest Hub 一键演示（Git Bash / WSL / macOS / Linux）
set -e
cd "$(dirname "$0")/.."

if [ ! -d .venv ]; then
  echo "[setup] 创建虚拟环境并安装依赖..."
  python3 -m venv .venv
  if [ -f .venv/Scripts/python.exe ]; then
    PY=.venv/Scripts/python.exe
  else
    PY=.venv/bin/python
  fi
  "$PY" -m pip install -r requirements.txt
else
  if [ -f .venv/Scripts/python.exe ]; then
    PY=.venv/Scripts/python.exe
  else
    PY=.venv/bin/python
  fi
fi

echo "[run] Agent 自动测试演示开始..."
"$PY" -m agenttest run
echo "报告已生成：reports/report.html"
