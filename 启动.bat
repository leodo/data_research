@echo off
chcp 65001 >nul
cd /d "%~dp0"
title 恐贪指数 · 本地站点

echo.
echo   ============================================
echo     恐贪指数 本地站点启动器
echo   ============================================
echo.

python --version >nul 2>&1
if errorlevel 1 (
    echo   [错误] 未找到 Python，请先安装 Python 并加入 PATH。
    echo.
    pause
    exit /b 1
)

python start_site.py %*

echo.
echo   服务已停止。
pause
