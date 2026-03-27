@echo off
setlocal

set ROOT=%~dp0..
set BUILD_ROOT=%ROOT%\desktop_client\build\windows
set DIST_DIR=%ROOT%\desktop_client\dist\windows

if exist "%BUILD_ROOT%" rmdir /s /q "%BUILD_ROOT%"
if exist "%DIST_DIR%" rmdir /s /q "%DIST_DIR%"
mkdir "%BUILD_ROOT%"
mkdir "%DIST_DIR%"

if "%TWOMAN_WINDOWS_PYTHON%"=="" (
  set TWOMAN_WINDOWS_PYTHON=python
)

%TWOMAN_WINDOWS_PYTHON% -m pip install --upgrade pip wheel >nul
%TWOMAN_WINDOWS_PYTHON% -m pip install -r "%ROOT%\requirements.txt" -r "%ROOT%\desktop_client\requirements.txt" pyinstaller >nul

%TWOMAN_WINDOWS_PYTHON% -m PyInstaller ^
  --noconfirm ^
  --clean ^
  --onefile ^
  --name twoman-desktop ^
  --paths "%ROOT%" ^
  --hidden-import local_client.helper ^
  --hidden-import twoman_protocol ^
  --hidden-import twoman_transport ^
  --distpath "%DIST_DIR%" ^
  --workpath "%BUILD_ROOT%\work" ^
  --specpath "%BUILD_ROOT%\spec" ^
  "%ROOT%\desktop_client\__main__.py"

echo Built %DIST_DIR%\twoman-desktop.exe

