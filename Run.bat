@echo off
setlocal enabledelayedexpansion

:: ---------------------------
:: 检查Python环境和版本
:: ---------------------------

:: 检查Python是否安装
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo Python未安装，请先安装Python 3.8或更高的版本，但请安装3.12以下的版本。
    echo 若您已安装Python,请检查是否已经将Python添加到系统Path。
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
    echo 您的Python版本是%pyversion%，但需要至少Python 3.8，且低于Python 3.12。
    pause
    exit /b 1
)

if %major% equ 3 (
    if %minor% lss 8 (
        echo 您的Python版本是%pyversion%，但需要至少Python 3.8，且低于Python 3.12。
        pause
        exit /b 1
    )
    if %minor% gtr 11 (
        echo 您的Python版本是%pyversion%，但目前仅支持Python 3.12以下版本。
        pause
        exit /b 1
    )
)

:: 检查pip是否安装
python -m pip --version >nul 2>&1
if %errorlevel% neq 0 (
    echo pip未安装，请先安装pip。
    pause
    exit /b 1
)

echo Python版本检查通过。


:: ---------------------------
:: 安装依赖
:: ---------------------------

echo 正在检测可用镜像源...

:: 尝试阿里源
echo 正在尝试阿里源...
python -m pip install --upgrade pip --index-url https://mirrors.aliyun.com/pypi/simple/ --trusted-host mirrors.aliyun.com
if !errorlevel! equ 0 (
    set "SOURCE_URL=https://mirrors.aliyun.com/pypi/simple/"
    set "TRUSTED_HOST=mirrors.aliyun.com"
    echo 成功使用阿里源。
    goto :INSTALL
)

:: 尝试清华源
echo 正在尝试清华源...
python -m pip install --upgrade pip --index-url https://pypi.tuna.tsinghua.edu.cn/simple --trusted-host pypi.tuna.tsinghua.edu.cn
if !errorlevel! equ 0 (
    set "SOURCE_URL=https://pypi.tuna.tsinghua.edu.cn/simple"
    set "TRUSTED_HOST=pypi.tuna.tsinghua.edu.cn"
    echo 成功使用清华源。
    goto :INSTALL
)

:: 尝试官方源
echo 正在尝试官方源...
python -m pip install --upgrade pip --index-url https://pypi.org/simple
if !errorlevel! equ 0 (
    set "SOURCE_URL=https://pypi.org/simple"
    set "TRUSTED_HOST="
    echo 成功使用官方源。
    goto :INSTALL
)

:: 所有源均失败
echo 所有镜像源均不可用，请检查网络连接。
pause
exit /b 1

:INSTALL
echo 正在使用源：%SOURCE_URL%
echo 安装依赖...

if "!TRUSTED_HOST!"=="" (
    python -m pip install -r requirements.txt -f ./libs --index-url !SOURCE_URL!
) else (
    python -m pip install -r requirements.txt -f ./libs --index-url !SOURCE_URL! --trusted-host !TRUSTED_HOST!
)

if !errorlevel! neq 0 (
    echo 安装依赖失败，请检查网络或手动安装。
    pause
    exit /b 1
)

echo 依赖安装完成！
cls

:: ---------------------------
:: 检查程序更新
:: ---------------------------

echo 检查程序更新...

python updater.py

echo 程序更新完成！

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
echo 端口 %PORT% 被 PID %PID% 的进程占用！正在关闭进程...

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