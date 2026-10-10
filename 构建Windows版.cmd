@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
py -3.12 -m venv .venv-windows
if errorlevel 1 goto fail
call .venv-windows\Scripts\activate.bat
python -m pip install -r requirements.txt pytest pyinstaller
if errorlevel 1 goto fail
python -m pytest -q --junitxml=windows-test-results.xml
if errorlevel 1 goto fail
python scripts\build.py
if errorlevel 1 goto fail
python scripts\smoke_package.py
if errorlevel 1 goto fail
python scripts\package_windows.py
if errorlevel 1 goto fail
echo 构建和离线测试完成，请查看 dist 文件夹。
pause
exit /b 0
:fail
echo 构建或测试失败，未生成已验证的发布包。请保存窗口中的错误信息。
pause
exit /b 1
