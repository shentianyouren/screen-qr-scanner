@echo off
setlocal
cd /d "%~dp0\.."

echo [1/3] Installing dependencies...
python -m pip install -r requirements.txt pyinstaller || goto :error

echo [2/3] Generating icon...
python scripts\make_icon.py || goto :error

echo [3/3] Building exe...
python -m PyInstaller --noconfirm --windowed --name ScreenQRScanner ^
  --icon "%CD%\assets\icon.ico" ^
  --add-data "%CD%\assets\icon.ico;assets" ^
  --distpath dist --workpath build --specpath build qr_scanner.py || goto :error

echo.
echo Done!  Output: dist\ScreenQRScanner\ScreenQRScanner.exe
goto :eof

:error
echo.
echo Build failed.
exit /b 1
