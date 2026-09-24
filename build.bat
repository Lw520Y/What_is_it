@echo off
rem 固定工作目录为脚本所在目录（以管理员身份运行时默认在 System32）
cd /d "%~dp0"

echo ============================================
echo  What's it? 磁盘目录侦探 - 打包脚本
echo ============================================

rem 正在运行的 WhatIsIt 会锁住 dist 里的 exe，先结束它再打包
taskkill /f /im WhatIsIt.exe >nul 2>&1

pip install pyinstaller >nul 2>&1
where pyinstaller >nul 2>&1
if errorlevel 1 (
    echo × 未找到 pyinstaller：请确认已安装 Python 并执行 pip install pyinstaller
    pause
    exit /b 1
)

pyinstaller --noconfirm --clean --onefile --windowed ^
  --name "WhatIsIt" ^
  --icon NONE ^
  main.py

echo.
if errorlevel 1 (
    echo × 打包失败：请查看上方错误信息。常见原因：杀毒软件拦截、Python 环境异常
    pause
    exit /b 1
)
if exist "dist\WhatIsIt.exe" (
    echo √ 打包成功: dist\WhatIsIt.exe（单文件，无需 _internal）
    explorer /select,"dist\WhatIsIt.exe"
) else (
    echo × 打包失败：dist 里没有生成 WhatIsIt.exe，请查看上方错误信息
)
pause
