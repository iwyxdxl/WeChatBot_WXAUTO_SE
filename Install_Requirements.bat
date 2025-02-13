@echo off
setlocal enabledelayedexpansion

:: 检测Python安装
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo 错误：未检测到Python。请安装Python 3.8或更高版本。
    pause
    exit /b 1
)

:: 获取Python版本
for /f "tokens=2" %%i in ('python --version 2^>^&1') do set "pyversion=%%i"

:: 解析版本号
for /f "tokens=1,2 delims=." %%a in ("%pyversion%") do (
    set major=%%a
    set minor=%%b
)

:: 验证版本要求
if %major% lss 3 (
    echo 错误：Python版本%pyversion%过低，需要3.8或更高。
    pause
    exit /b 1
)

if %major% equ 3 if %minor% lss 8 (
    echo 错误：Python版本%pyversion%过低，需要3.8或更高。
    pause
    exit /b 1
)

:: 检测pip安装
python -m pip --version >nul 2>&1
if %errorlevel% neq 0 (
    echo 错误：未检测到pip。请确保pip已安装。
    pause
    exit /b 1
)

:: 安装依赖项
echo 正在安装依赖项...
python -m pip install -r requirements.txt

if %errorlevel% neq 0 (
    echo 错误：依赖项安装失败。
    pause
    exit /b 1
)

echo 所有依赖项已成功安装！
pause