@echo off
chcp 65001 >nul
title 微信机器人一键诊断

echo ========================================
echo 微信机器人一键诊断工具
echo ========================================
echo.
echo 提示：检测工具将在3分钟后自动关闭
echo 如果检测报错，请点击主页面上的"启动"按钮重试
echo.

:: 切换到诊断工具目录并启动
cd diagnostic_standalone
call run_diagnostic_standalone.bat

:: 返回原目录
cd ..
