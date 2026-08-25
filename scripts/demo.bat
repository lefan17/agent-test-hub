@echo off
chcp 65001 >nul
cd /d "%~dp0.."

where py >nul 2>nul
if %errorlevel%==0 (
  set "PYLAUNCHER=py -3"
) else (
  set "PYLAUNCHER=python"
)

if not exist ".venv\Scripts\python.exe" (
  echo [setup] 创建虚拟环境并安装依赖（首次约 1-2 分钟）...
  %PYLAUNCHER% -m venv .venv
  if errorlevel 1 goto :err
  .venv\Scripts\python.exe -m pip install -r requirements.txt
  if errorlevel 1 goto :err
)

echo [run] Agent 自动测试演示开始...
.venv\Scripts\python.exe -m agenttest run
echo.
echo 报告已生成：reports\report.html（可直接用浏览器打开）
pause
exit /b 0

:err
echo 安装失败，请确认 Python 3.9+ 已安装并加入 PATH
pause
exit /b 1
