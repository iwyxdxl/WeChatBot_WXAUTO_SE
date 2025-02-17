@echo off
cd /d "%~dp0"
start python config_editor.py
start "" "http://localhost:5000"
:prompt
set /p answer=是否完成配置（已完成请输入Y）: 
if /i "%answer%"=="Y" (
    python bot.py
) else (
    echo 请完成配置后输入Y。
    goto prompt
)
