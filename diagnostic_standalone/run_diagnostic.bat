@echo off
chcp 65001 >nul
title 微信机器人诊断工具

echo.
echo ========================================
echo    微信机器人一键诊断工具
echo ========================================
echo.
echo 正在启动诊断工具...
echo.

REM 检查Python是否安装
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [错误] 未检测到Python环境
    echo 请先安装Python 3.7或更高版本
    pause
    exit /b 1
)

REM 检查必要文件是否存在
if not exist "diagnostic_tool.py" (
    echo [错误] 诊断工具文件不存在
    echo 请确保diagnostic_tool.py文件在当前目录
    pause
    exit /b 1
)

if not exist "templates\diagnostic.html" (
    echo [错误] 前端界面文件不存在
    echo 请确保templates目录及diagnostic.html文件存在
    pause
    exit /b 1
)

REM 安装依赖（如果需要）
echo 检查依赖包...
if exist "requirements_diagnostic.txt" (
    echo 正在安装诊断工具依赖...
    pip install -r requirements_diagnostic.txt --find-links ./libs --prefer-binary
    if %errorlevel% neq 0 (
        echo 尝试备用安装方式...
        pip install flask flask-cors psutil requests openai
    )
) else (
    echo 安装基础依赖包...
    pip install flask flask-cors psutil requests openai
)

REM 启动诊断工具
echo.
echo 启动诊断工具中...
echo 请在浏览器中访问: http://localhost:5001
echo.
echo 按 Ctrl+C 可以停止诊断工具
echo.

python diagnostic_tool.py

pause