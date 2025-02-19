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
:: 安装依赖
:: ---------------------------

echo 安装依赖...
:: 手动录入依赖项
echo wechaty==0.8.19 > temp_requirements.txt
echo wechaty-puppet-service==0.8.4 >> temp_requirements.txt
echo pyee==8.2.2 >> temp_requirements.txt
echo flask >> temp_requirements.txt
echo flask-cors >> temp_requirements.txt
echo sqlalchemy~=2.0.37 >> temp_requirements.txt
echo requests >> temp_requirements.txt
echo wxauto~=3.9.11.17.5 >> temp_requirements.txt
echo openai~=1.61.0 >> temp_requirements.txt
echo pyautogui >> temp_requirements.txt
echo werkzeug >> temp_requirements.txt
echo psutil>> temp_requirements.txt

python -m pip install -r temp_requirements.txt
if %errorlevel% neq 0 (
    echo 安装依赖失败，请检查网络或手动安装依赖。
    pause
    exit /b 1
)
echo 依赖安装完成！

:: 删除临时文件
del temp_requirements.txt

:: 清屏
cls

:: ---------------------------
:: 启动程序
:: ---------------------------

:: 启动配置编辑器
start python config_editor.py

:: 打开浏览器访问Flask服务器
start "" "http://localhost:5000"
