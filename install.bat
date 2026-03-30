@echo off
:: Breadboard Builder — KiCad plugin installer for Windows
:: Double-click this file to run it.

setlocal enabledelayedexpansion

set PLUGIN_NAME=breadboard
set SOURCE=%~dp0plugins\%PLUGIN_NAME%

echo.
echo Breadboard Builder - installer
echo ==============================

:: Check KiCad 10 first, then 9
set TARGET=
if exist "%APPDATA%\kicad\10.0" set TARGET=%APPDATA%\kicad\10.0\scripting\plugins
if not defined TARGET (
    if exist "%APPDATA%\kicad\9.0" set TARGET=%APPDATA%\kicad\9.0\scripting\plugins
)

if not defined TARGET (
    echo.
    echo ERROR: Could not find a KiCad installation directory.
    echo Please install KiCad 9 or 10 first, then re-run this script.
    echo.
    echo If KiCad is installed but not found, copy the plugins\breadboard\
    echo folder manually into KiCad's scripting\plugins\ directory.
    echo ^(KiCad -^> Preferences -^> Configure Paths... shows the exact path.^)
    echo.
    pause
    exit /b 1
)

set DEST=%TARGET%\%PLUGIN_NAME%

echo Source : %SOURCE%
echo Target : %DEST%
echo.

:: Remove stale install
if exist "%DEST%" (
    echo Removing existing installation...
    rmdir /s /q "%DEST%"
)

:: Create target directory if needed
if not exist "%TARGET%" mkdir "%TARGET%"

:: Try a directory junction (no admin rights required)
mklink /j "%DEST%" "%SOURCE%" >nul 2>&1
if %errorlevel% == 0 (
    echo Directory junction created.
) else (
    echo Could not create junction - copying instead...
    xcopy /e /i /q "%SOURCE%" "%DEST%"
    echo Files copied.
)

echo.
echo Done! Next steps:
echo   1. Open KiCad and open your project in the PCB Editor.
echo   2. In the menu: Tools -^> External Plugins -^> Refresh Plugins.
echo   3. A breadboard icon will appear in the right-hand toolbar.
echo.
pause
