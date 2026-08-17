@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo [1/2] 正在打包，请稍候...
python -m PyInstaller --onefile --windowed --name "代写小助手" --clean main.py
echo [2/2] 打包完成！
echo.
echo 成品位置：%~dp0dist\代写小助手.exe
echo.
pause
