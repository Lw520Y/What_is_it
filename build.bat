@echo off
chcp 65001 >nul
echo ============================================
echo  What's it? 磁盘目录侦探 - 打包脚本
echo ============================================

pip install pyinstaller >nul 2>&1

pyinstaller --noconfirm --clean --onefile --windowed ^
  --name "WhatIsIt" ^
  --icon NONE ^
  main.py

echo.
if exist "dist\WhatIsIt.exe" (
    echo ✅ 打包成功: dist\WhatIsIt.exe（单文件，无需 _internal）
    explorer /select,"dist\WhatIsIt.exe"
) else (
    echo ❌ 打包失败，请检查上方错误信息
)
pause
