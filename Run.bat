@echo off
setlocal enabledelayedexpansion
chcp 65001 >nul

:: ---------------------------
:: 检查微信版本
:: ---------------------------
:: 依次检测 Weixin 和 WeChat 注册表路径,优先 Weixin
:: ---------------------------
set "wxversion="
rem 优先依次检测 Weixin 和 WeChat 的 DisplayVersion
for %%K in (
    "HKLM\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\Weixin"
    "HKLM\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\WeChat"
) do (
    for /f "tokens=2,*" %%i in ('reg query %%K /v DisplayVersion 2^>nul ^| find "DisplayVersion"') do (
        set "wxversion=%%j"
        set "RegPath=%%K"
        goto :found_wxversion
    )
)
if not defined wxversion (
    echo "⚠️ 警告：未检测到微信安装或无法读取注册表！"
    echo "⚠️这可能是由于以下原因："
    echo "⚠️1. 微信未正确安装"
    echo "⚠️2. 注册表访问权限不足"
    echo "⚠️3. 微信版本过老或过新"
    echo "⚠️4. 您使用的是便携版微信"
    echo.
    echo "⚠️程序将跳过微信版本检查并继续运行。"
    echo "⚠️如果程序启动后无法控制微信,请下载微信3.9版本：https://dldir1v6.qq.com/weixin/Windows/WeChatSetup.exe"
    echo.
    echo "🔄3秒后自动继续..."
    timeout /t 3 /nobreak >nul
    goto :check_python
)
:found_wxversion

if not defined wxversion (
    echo "⚠️警告：无法获取微信版本号！"
    echo "⚠️程序将跳过微信版本检查并继续运行,但建议检查微信安装状态。"
    echo "⚠️如果程序启动后无法控制微信,请下载微信3.9版本：https://dldir1v6.qq.com/weixin/Windows/WeChatSetup.exe"
    echo.
    echo "🔄3秒后自动继续..."
    timeout /t 3 /nobreak >nul
    goto :check_python
)

:: 解析主版本号
for /f "tokens=1 delims=." %%a in ("!wxversion!") do (
    set "major=%%a"
)

:: 只判断主版本
if !major! lss 3 (
    echo "❌当前微信版本 !wxversion!,版本过低！"
    echo "⚠️请下载微信3.9版本"
    echo "⚠️下载地址：https://dldir1v6.qq.com/weixin/Windows/WeChatSetup.exe"
    echo.
    echo "🔄如果您确信已经安装了正确版本的微信,请按下键盘任意键继续运行程序,否则关闭窗口退出。"
    pause
    goto :check_python
)
if !major! geq 4 (
    echo "❌当前微信版本 !wxversion!,版本过高！"
    echo "⚠️软件暂不支持微信4.x及以上版本,可能导致兼容性问题"
    echo "⚠️请下载微信3.9版本"
    echo "⚠️下载地址：https://dldir1v6.qq.com/weixin/Windows/WeChatSetup.exe"
    echo.
    echo "🔄 如果您确信已经安装了正确版本的微信,请按下键盘任意键继续运行程序,否则关闭窗口退出。"
    pause
    goto :check_python
)

echo "✅ 微信版本检查通过：!wxversion!"

:check_python

:: ---------------------------
:: 检查 Python 是否安装
:: ---------------------------
echo 🔍 检查Python环境...
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo "❌ Python 未安装或未添加到系统PATH！"
    echo "请前往官网下载并安装 Python 3.9-3.12 版本"
    echo "下载地址：https://www.python.org/downloads/"
    echo "⚠️ 安装时请勾选"Add Python to PATH"选项"
    pause
    exit /b 1
)

:: 获取 Python 版本
for /f "tokens=2,*" %%i in ('python --version 2^>^&1') do set "pyversion=%%i"
echo "检测到Python版本：%pyversion%"

:: 解析版本号
for /f "tokens=1,2,3 delims=." %%a in ("%pyversion%") do (
    set "py_major=%%a"
    set "py_minor=%%b"
    set "py_patch=%%c"
)

:: 检查主版本号
if "%py_major%" neq "3" (
    echo "❌ 不支持的Python主版本：%pyversion%"
    echo "支持版本：Python 3.9-3.12"
    echo "当前版本：Python %pyversion%"
    pause
    exit /b 1
)

:: 检查次版本号范围 (3.9-3.12)
if %py_minor% lss 9 (
    echo "❌ Python版本过低：%pyversion%"
    echo "最低要求：Python 3.9"
    echo "当前版本：Python %pyversion%"
    echo "请升级Python版本"
    pause
    exit /b 1
)
if %py_minor% gtr 12 (
    echo "❌ Python版本过高：%pyversion%"       
    echo "支持版本：Python 3.9-3.12"
    echo "当前版本：Python %pyversion%"
    echo "可能存在兼容性问题,建议降级"
    pause
    exit /b 1
)

echo "✅ Python版本检查通过：%pyversion% (满足3.9-3.12要求)"

:: ---------------------------
:: 检查 pip 是否存在
:: ---------------------------
python -m pip --version >nul 2>&1
if %errorlevel% neq 0 (
    echo "❌ pip 未安装,请先安装 pip。"
    pause
    exit /b 1
)

:: ---------------------------
:: 选择最快的 pip 源
:: ---------------------------
echo "🚀 正在检测可用镜像源..."

:: 阿里源
python -m pip install --upgrade pip --index-url https://mirrors.aliyun.com/pypi/simple/ --trusted-host mirrors.aliyun.com
if !errorlevel! equ 0 (
    set "SOURCE_URL=https://mirrors.aliyun.com/pypi/simple/"
    set "TRUSTED_HOST=mirrors.aliyun.com"
    echo "✅ 使用阿里源"
    goto :INSTALL
)

:: 清华源
python -m pip install --upgrade pip --index-url https://pypi.tuna.tsinghua.edu.cn/simple --trusted-host pypi.tuna.tsinghua.edu.cn
if !errorlevel! equ 0 (
    set "SOURCE_URL=https://pypi.tuna.tsinghua.edu.cn/simple"
    set "TRUSTED_HOST=pypi.tuna.tsinghua.edu.cn"
    echo "✅ 使用清华源"
    goto :INSTALL
)

:: 官方源
python -m pip install --upgrade pip --index-url https://pypi.org/simple
if !errorlevel! equ 0 (
    set "SOURCE_URL=https://pypi.org/simple"
    set "TRUSTED_HOST="
    echo "✅ 使用官方源"
    goto :INSTALL
)

echo "❌ 无可用镜像源,请检查网络"
pause
exit /b 1

:INSTALL
echo "🔄 正在安装依赖..."

if "!TRUSTED_HOST!"=="" (
    python -m pip install -r requirements.txt -f ./libs --index-url !SOURCE_URL!
) else (
    python -m pip install -r requirements.txt -f ./libs --index-url !SOURCE_URL! --trusted-host !TRUSTED_HOST!
)

if !errorlevel! neq 0 (
    echo "❌ 安装依赖失败,请检查网络或 requirements.txt 是否存在"
    pause
    exit /b 1
)

:: 安装wxautox-wechatbot
python -m pip install -U -i https://mirrors.tuna.tsinghua.edu.cn/pypi/web/simple wxautox-wechatbot
if !errorlevel! neq 0 (
    echo "⚠️ wxautox-wechatbot 安装失败,尝试其他镜像源..."
    python -m pip install -U wxautox-wechatbot
)

:: 安装wxauto
python -m pip install -U -i https://mirrors.tuna.tsinghua.edu.cn/pypi/web/simple wxauto
if !errorlevel! neq 0 (
    echo "⚠️ wxauto 安装失败,尝试其他镜像源..."
    python -m pip install -U wxauto
)

echo "✅ 所有依赖安装成功！"

:: 清屏
cls

:: ---------------------------
:: 检查程序更新
:: ---------------------------

echo "🟢 检查程序更新..."

python updater.py

echo "✅ 程序更新完成！"

:: 清屏
cls

:: ---------------------------
:: 启动程序
:: ---------------------------
echo "🟢 启动主程序..."
python config_editor.py
