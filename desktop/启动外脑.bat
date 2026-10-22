@echo off
chcp 65001 >nul
rem 外脑 · Cordis Brain 桌面客户端启动器
cd /d "%~dp0dist\cordis-desktop"
if not exist cordis-desktop.exe (
    echo 未找到 cordis-desktop.exe，请先构建（见 使用说明.txt）。
    pause
    exit /b 1
)
start "" cordis-desktop.exe %*
