@echo off
setlocal enabledelayedexpansion
chcp 65001 >nul
title 微信机器人诊断工具（独立版本）

echo ========================================
echo    微信机器人一键诊断工具（独立版本）
echo ========================================
echo.

:: ---------------------------
:: 检查 Python 是否安装
:: ---------------------------
echo [信息] 正在检查Python环境...
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo ❌ Python 未安装，请先安装 Python 3.8 - 3.11 版本。
    echo [提示] 下载地址: https://www.python.org/downloads/
    pause
    exit /b 1
)

:: 获取 Python 版本并检查
for /f "tokens=2,*" %%i in ('python --version 2^>^&1') do set "pyversion=%%i"
for /f "tokens=1,2 delims=." %%a in ("%pyversion%") do (
    set major=%%a
    set minor=%%b
)

:: 检查Python版本范围（参考Run.bat的检查逻辑）
if %major% lss 3 (
    echo ❌ 当前 Python 版本 %pyversion%，请使用 Python 3.8+
    pause
    exit /b 1
)
if %major% gtr 3 (
    echo ❌ 当前 Python 版本 %pyversion%，请使用 Python 3.8-3.11 之间版本
    pause
    exit /b 1
)
if %minor% lss 8 (
    echo ❌ Python 版本太旧，最低要求为 Python 3.8
    pause
    exit /b 1
)
if %minor% geq 12 (
    echo ❌ 暂不支持 Python 3.12 及以上版本
    pause
    exit /b 1
)

echo ✅ Python 版本检查通过：%pyversion%

:: ---------------------------
:: 检查 pip 是否存在
:: ---------------------------
python -m pip --version >nul 2>&1
if %errorlevel% neq 0 (
    echo ❌ pip 未安装，请先安装 pip。
    pause
    exit /b 1
)

:: ---------------------------
:: 选择最快的 pip 源（参考Run.bat的国内加速逻辑）
:: ---------------------------
echo 🚀 正在检测可用镜像源...

:: 阿里源（首选）
python -m pip install --upgrade pip --index-url https://mirrors.aliyun.com/pypi/simple/ --trusted-host mirrors.aliyun.com >nul 2>&1
if !errorlevel! equ 0 (
    set "SOURCE_URL=https://mirrors.aliyun.com/pypi/simple/"
    set "TRUSTED_HOST=mirrors.aliyun.com"
    echo ✅ 使用阿里源
    goto :INSTALL
)

:: 清华源（备选）
python -m pip install --upgrade pip --index-url https://pypi.tuna.tsinghua.edu.cn/simple --trusted-host pypi.tuna.tsinghua.edu.cn >nul 2>&1
if !errorlevel! equ 0 (
    set "SOURCE_URL=https://pypi.tuna.tsinghua.edu.cn/simple"
    set "TRUSTED_HOST=pypi.tuna.tsinghua.edu.cn"
    echo ✅ 使用清华源
    goto :INSTALL
)

:: 豆瓣源（第三选择）
python -m pip install --upgrade pip --index-url https://pypi.douban.com/simple --trusted-host pypi.douban.com >nul 2>&1
if !errorlevel! equ 0 (
    set "SOURCE_URL=https://pypi.douban.com/simple"
    set "TRUSTED_HOST=pypi.douban.com"
    echo ✅ 使用豆瓣源
    goto :INSTALL
)

:: 官方源（最后备选）
python -m pip install --upgrade pip --index-url https://pypi.org/simple >nul 2>&1
if !errorlevel! equ 0 (
    set "SOURCE_URL=https://pypi.org/simple"
    set "TRUSTED_HOST="
    echo ✅ 使用官方源
    goto :INSTALL
)

echo ❌ 无可用镜像源，请检查网络
pause
exit /b 1

:INSTALL
echo 🔄 正在安装诊断工具依赖...

:: ---------------------------
:: 优先从本地安装所有依赖
:: ---------------------------
if exist "requirements_diagnostic.txt" (
    echo [本地] 尝试从本地libs安装依赖...
    python -m pip install --find-links ..\libs --prefer-binary --no-index --requirement requirements_diagnostic.txt 2>nul
    if !errorlevel! equ 0 (
        echo ✅ 本地依赖安装成功
        goto :WECHAT_MODULE
    )
    echo [网络] 本地安装失败，尝试从镜像源安装...
)

:: ---------------------------
:: 核心依赖逐个安装（确保稳定性）
:: ---------------------------

:: 核心Web框架
echo [检查] Flask Web框架...
python -c "import flask, flask_cors" >nul 2>&1
if !errorlevel! neq 0 (
    echo [安装] Flask及CORS支持...
    if "!TRUSTED_HOST!"=="" (
        python -m pip install flask flask-cors --index-url !SOURCE_URL!
    ) else (
        python -m pip install flask flask-cors --index-url !SOURCE_URL! --trusted-host !TRUSTED_HOST!
    )
    if !errorlevel! neq 0 (
        echo ❌ Flask安装失败
        pause
        exit /b 1
    )
)

:: 系统监控
echo [检查] psutil系统监控...
python -c "import psutil" >nul 2>&1
if !errorlevel! neq 0 (
    echo [安装] psutil系统监控...
    if "!TRUSTED_HOST!"=="" (
        python -m pip install psutil --index-url !SOURCE_URL!
    ) else (
        python -m pip install psutil --index-url !SOURCE_URL! --trusted-host !TRUSTED_HOST!
    )
    if !errorlevel! neq 0 (
        echo ❌ psutil安装失败
        pause
        exit /b 1
    )
)

:: 网络请求
echo [检查] requests网络库...
python -c "import requests" >nul 2>&1
if !errorlevel! neq 0 (
    echo [安装] requests网络库...
    if "!TRUSTED_HOST!"=="" (
        python -m pip install requests --index-url !SOURCE_URL!
    ) else (
        python -m pip install requests --index-url !SOURCE_URL! --trusted-host !TRUSTED_HOST!
    )
    if !errorlevel! neq 0 (
        echo ❌ requests安装失败
        pause
        exit /b 1
    )
)

:: OpenAI API（可选）
echo [检查] OpenAI API（可选）...
python -c "import openai" >nul 2>&1
if !errorlevel! neq 0 (
    echo [安装] OpenAI API支持（用于对话测试）...
    if "!TRUSTED_HOST!"=="" (
        python -m pip install openai --index-url !SOURCE_URL! >nul 2>&1
    ) else (
        python -m pip install openai --index-url !SOURCE_URL! --trusted-host !TRUSTED_HOST! >nul 2>&1
    )
    if !errorlevel! neq 0 (
        echo [警告] OpenAI API安装失败，对话测试功能将不可用
    )
)

:WECHAT_MODULE
:: ---------------------------
:: 微信自动化模块（从本地libs安装）
:: ---------------------------
echo [检查] 微信自动化模块...
python -c "import wxautox_wechatbot" >nul 2>&1
if !errorlevel! neq 0 (
    echo [信息] 尝试安装wxautox_wechatbot...
    
    :: 优先从本地libs目录安装适配版本的wheel包
    set "wheel_found=false"
    if %minor% equ 9 (
        if exist "..\libs\wxautox_wechatbot-39.1.1-cp39-cp39-win_amd64.whl" (
            echo [安装] Python 3.9版本模块...
            python -m pip install "..\libs\wxautox_wechatbot-39.1.1-cp39-cp39-win_amd64.whl" --force-reinstall --no-deps >nul 2>&1
            if !errorlevel! equ 0 set "wheel_found=true"
        )
    )
    if %minor% equ 10 (
        if exist "..\libs\wxautox_wechatbot-39.1.1-cp310-cp310-win_amd64.whl" (
            echo [安装] Python 3.10版本模块...
            python -m pip install "..\libs\wxautox_wechatbot-39.1.1-cp310-cp310-win_amd64.whl" --force-reinstall --no-deps >nul 2>&1
            if !errorlevel! equ 0 set "wheel_found=true"
        )
    )
    if %minor% equ 11 (
        if exist "..\libs\wxautox_wechatbot-39.1.1-cp311-cp311-win_amd64.whl" (
            echo [安装] Python 3.11版本模块...
            python -m pip install "..\libs\wxautox_wechatbot-39.1.1-cp311-cp311-win_amd64.whl" --force-reinstall --no-deps >nul 2>&1
            if !errorlevel! equ 0 set "wheel_found=true"
        )
    )
    
    if "!wheel_found!"=="true" (
        echo ✅ 本地微信模块安装完成
    ) else (
        echo [警告] 未找到适配的微信模块，微信相关测试功能将不可用
        echo [提示] 仅影响微信测试，其他功能正常
    )
)

:: ---------------------------
:: 最终验证核心依赖
:: ---------------------------
echo [验证] 核心依赖完整性...
python -c "import flask, flask_cors, psutil, requests; print('✅ 核心依赖验证通过')" 2>nul
if !errorlevel! neq 0 (
    echo ❌ 核心依赖验证失败，请重新运行脚本
    pause
    exit /b 1
)

:: ---------------------------
:: 启动诊断工具
:: ---------------------------
echo.
echo ========================================
echo        启动诊断工具
echo ========================================
echo [信息] 正在启动诊断工具...
echo [提示] 请在浏览器中访问: http://localhost:5001
echo [提示] 按 Ctrl+C 可停止诊断工具
echo ========================================
echo.

:: 检查端口占用
netstat -an | find "5001" | find "LISTENING" >nul 2>&1
if !errorlevel! equ 0 (
    echo [警告] 端口5001已被占用，诊断工具可能无法启动
    echo [提示] 请关闭其他占用5001端口的程序后重试
    echo.
)

::启动诊断工具
:: 先启动一个新的进程来延迟2秒后打开浏览器
start /b cmd /c "timeout /t 2 /nobreak >nul && start http://localhost:5001"

::启动诊断工具并捕获错误
echo [提示] 诊断工具将在3分钟后自动关闭
python diagnostic_tool.py
set "exit_code=%errorlevel%"

echo.
echo ========================================
if %exit_code% equ 0 (
    echo ✅ 诊断工具正常退出
) else (
    echo ❌ 诊断工具已自动关闭: %exit_code%
    echo [提示] 如需再次检测，请重新运行脚本
)
echo ========================================

