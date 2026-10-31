@echo off
setlocal
cd /d "%~dp0"
where py >nul 2>nul
if errorlevel 1 (
  echo Install Python 3.12 x64 from python.org, including the Python launcher.
  exit /b 1
)
py -3.12 -c "import sys; assert sys.platform == 'win32' and sys.maxsize > 2**32"
if errorlevel 1 exit /b 1
if not exist .venv\Scripts\python.exe (
  py -3.12 -m venv .venv
  if errorlevel 1 exit /b 1
)
.venv\Scripts\python.exe -c "import sys; assert sys.version_info[:2] == (3, 12), 'Recreate .venv with Python 3.12'"
if errorlevel 1 exit /b 1
.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
if errorlevel 1 exit /b 1
.venv\Scripts\python.exe packaging\check_ocr.py
if errorlevel 1 exit /b 1
.venv\Scripts\python.exe -m pytest -q
if errorlevel 1 exit /b 1
.venv\Scripts\python.exe -m PyInstaller --noconfirm --clean --windowed --onedir --name AI-Screen-Assistant --collect-all winrt --collect-all keyring --hidden-import keyring.backends.Windows --collect-data matplotlib --exclude-module PySide6.QtWebEngineCore --exclude-module PySide6.QtWebEngineWidgets --exclude-module tkinter main.py
if errorlevel 1 exit /b 1
copy /y README.md dist\AI-Screen-Assistant\README.md >nul
if errorlevel 1 exit /b 1
dist\AI-Screen-Assistant\AI-Screen-Assistant.exe --smoke-test
if errorlevel 1 exit /b 1
echo Build complete: dist\AI-Screen-Assistant\AI-Screen-Assistant.exe
echo Optional installer: ISCC.exe packaging\installer.iss
endlocal
