@echo off
setlocal enabledelayedexpansion

set process_name=bilibili_mgba_controller.py
echo 正在查找并关闭进程：%process_name%...

set found=0
:: 查找包含指定名称的Python进程
for /f "tokens=2 delims=," %%a in ('tasklist /fi "imagename eq python.exe" /fo csv /nh ^| findstr /i "%process_name%"') do (
    set pid=%%~a
    echo 找到进程ID：!pid!，正在终止...
    taskkill /f /pid !pid! >nul 2>&1
    if !errorlevel! equ 0 (
        echo 成功终止进程ID：!pid!
        set found=1
    ) else (
        echo 终止进程ID：!pid! 失败
    )
)

:: 检查是否有Pythonw进程（无控制台窗口的Python进程）
for /f "tokens=2 delims=," %%a in ('tasklist /fi "imagename eq pythonw.exe" /fo csv /nh ^| findstr /i "%process_name%"') do (
    set pid=%%~a
    echo 找到进程ID：!pid!，正在终止...
    taskkill /f /pid !pid! >nul 2>&1
    if !errorlevel! equ 0 (
        echo 成功终止进程ID：!pid!
        set found=1
    ) else (
        echo 终止进程ID：!pid! 失败
    )
)

if !found! equ 0 (
    echo 未找到名称包含 %process_name% 的进程
)

echo 操作完成
pause
