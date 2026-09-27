@echo off
chcp 65001 > nul
set EXE_NAME=data_analyzer.exe
set SCRIPT_NAME=src\data_analyzer.py

echo [INFO] 正在检测并终止可能运行中的旧版 %EXE_NAME% 进程...
taskkill /f /im %EXE_NAME% >nul 2>&1

echo [INFO] 正在调用 PyInstaller 编译打包单文件上位机 (不显示控制台窗口)...
pyinstaller --clean --noconsole --onefile "%SCRIPT_NAME%" --name "data_analyzer"

if %ERRORLEVEL% equ 0 (
    echo [SUCCESS] 上位机编译打包完成！
    echo [SUCCESS] 输出文件位于: dist\%EXE_NAME%
) else (
    echo [ERROR] PyInstaller 编译打包失败，请确保本地 Python 环境已安装 pyinstaller。
    echo [ERROR] 可以使用命令: pip install pyinstaller pyserial 安装必要依赖。
    exit /b 1
)
pause
