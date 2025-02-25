@echo off
setlocal enabledelayedexpansion

:: ---------------------------
:: 检查Python环境和版本
:: ---------------------------

:: 检查Python是否安装
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo Python未安装，请先安装Python 3.8或更高版本。
    pause
    exit /b 1
)

:: 获取Python版本
for /f "tokens=2" %%i in ('python --version 2^>^&1') do set "pyversion=%%i"

:: 解析Python版本
for /f "tokens=1,2 delims=." %%a in ("%pyversion%") do (
    set major=%%a
    set minor=%%b
)

:: 检查版本是否符合要求
if %major% lss 3 (
    echo 您的Python版本是%pyversion%，但需要至少Python 3.8。
    pause
    exit /b 1
)

if %major% equ 3 if %minor% lss 8 (
    echo 您的Python版本是%pyversion%，但需要至少Python 3.8。
    pause
    exit /b 1
)

:: 检查pip是否安装
python -m pip --version >nul 2>&1
if %errorlevel% neq 0 (
    echo pip未安装，请先安装pip。
    pause
    exit /b 1
)

:: ---------------------------
:: 检查程序更新
:: ---------------------------

echo 检查程序更新...

python updater.py

echo 程序更新完成！

:: ---------------------------
:: 安装依赖
:: ---------------------------

echo 安装依赖...

python -m pip install -r requirements.txt

if %errorlevel% neq 0 (
    echo 安装依赖失败，请检查网络或手动安装依赖。
    pause
    exit /b 1
)
echo 依赖安装完成！

:: 清屏
cls

:: ---------------------------
:: 检查端口占用并关闭
:: ---------------------------

echo 检查端口占用...
set "PORT=5000"  :: 设置要检查的端口号

:: 使用netstat检查端口占用
for /f "tokens=5" %%a in ('netstat -aon ^| findstr :%PORT%') do (
    set "PID=%%a"
    goto :found
)

echo 端口 %PORT% 未被占用，继续启动程序...
goto :start_program

:found
echo 端口 %PORT% 被 PID %PID% 的进程占用！
choice /c YN /m "是否要关闭占用端口的程序？"
if errorlevel 2 goto :start_program

:: 关闭占用端口的进程
echo 正在关闭 PID %PID% 的进程...
taskkill /F /PID %PID% >nul 2>&1
echo 进程已关闭！

:start_program
echo.

:: ---------------------------
:: 启动程序
:: ---------------------------

:: 启动配置编辑器
start python config_editor.py

:: 打开浏览器访问Flask服务器
start "" "http://localhost:5000"