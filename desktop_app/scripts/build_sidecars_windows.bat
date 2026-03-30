@echo off
setlocal

set ROOT=%~dp0..\..
set APP_ROOT=%ROOT%\desktop_app
set BUILD_ROOT=%APP_ROOT%\build\windows-sidecars
set DIST_DIR=%APP_ROOT%\src-tauri\resources\sidecars\windows
set STAGE_DIR=%BUILD_ROOT%\dist

if exist "%BUILD_ROOT%" rmdir /s /q "%BUILD_ROOT%"
mkdir "%BUILD_ROOT%"
mkdir "%DIST_DIR%" 2>nul
mkdir "%STAGE_DIR%"

if "%TWOMAN_WINDOWS_PYTHON%"=="" (
  set TWOMAN_WINDOWS_PYTHON=py -3
)

%TWOMAN_WINDOWS_PYTHON% -m pip install --upgrade pip wheel >nul
%TWOMAN_WINDOWS_PYTHON% -m pip install -r "%ROOT%\requirements.txt" pyinstaller >nul

%TWOMAN_WINDOWS_PYTHON% -m PyInstaller ^
  --noconfirm ^
  --clean ^
  --onefile ^
  --noconsole ^
  --name twoman-helper ^
  --paths "%ROOT%" ^
  --hidden-import local_client.helper ^
  --hidden-import twoman_protocol ^
  --hidden-import twoman_transport ^
  --distpath "%STAGE_DIR%" ^
  --workpath "%BUILD_ROOT%\work-helper" ^
  --specpath "%BUILD_ROOT%\spec-helper" ^
  "%ROOT%\local_client\helper.py"

copy /Y "%STAGE_DIR%\twoman-helper.exe" "%DIST_DIR%\twoman-helper.exe" >nul

%TWOMAN_WINDOWS_PYTHON% -m PyInstaller ^
  --noconfirm ^
  --clean ^
  --onefile ^
  --noconsole ^
  --name twoman-gateway ^
  --paths "%ROOT%" ^
  --distpath "%STAGE_DIR%" ^
  --workpath "%BUILD_ROOT%\work-gateway" ^
  --specpath "%BUILD_ROOT%\spec-gateway" ^
  "%ROOT%\desktop_client\socks_gateway.py"

copy /Y "%STAGE_DIR%\twoman-gateway.exe" "%DIST_DIR%\twoman-gateway.exe" >nul

echo Built Windows sidecars in %DIST_DIR%
