@echo off
chcp 65001 >nul
title Установщик ЭХО Мессенджер
color 0A

:: Запуск от админа
net session >nul 2>&1
if %errorlevel% neq 0 (
    echo Запуск от имени администратора...
    powershell start -verb runas '%0'
    exit
)

:: Переменные
set APP_NAME=EchoMessenger
set APP_DISPLAY=ЭХО Мессенджер
set APP_EXE=echo.bat
set INSTALL_DIR=%ProgramFiles%\%APP_NAME%
set DESKTOP_LNK=%USERPROFILE%\Desktop\%APP_NAME%.lnk
set STARTMENU_LNK=%APPDATA%\Microsoft\Windows\Start Menu\Programs\%APP_NAME%.lnk
set ICON_URL=set ICON_URL=https://raw.githubusercontent.com/aggcjz024689-cpu/echo-messenger/main/icon.ico
set ICON_FILE=%INSTALL_DIR%\icon.ico
set TEMP_LNK=%TEMP%\temp_shortcut.lnk

echo ===============================================
echo     Установка %APP_DISPLAY%
echo ===============================================
echo.

:: Создаём папку установки
if not exist "%INSTALL_DIR%" mkdir "%INSTALL_DIR%"

:: Создаём echo.bat
(
echo @echo off
echo start msedge --app=https://echo-messenger-wko2.onrender.com
) > "%INSTALL_DIR%\%APP_EXE%"
echo [OK] Файл %APP_EXE% создан

:: Скачиваем иконку
echo Скачивание иконки...
powershell -Command "Invoke-WebRequest -Uri '%ICON_URL%' -OutFile '%ICON_FILE%'" 2>nul
if exist "%ICON_FILE%" (
    echo [OK] Иконка загружена
) else (
    echo [WARN] Иконка не загружена, будет стандартная
)

:: Создаём временный ярлык через PowerShell
powershell -Command "$WshShell = New-Object -comObject WScript.Shell; $Shortcut = $WshShell.CreateShortcut('%TEMP_LNK%'); $Shortcut.TargetPath = '%INSTALL_DIR%\%APP_EXE%'; $Shortcut.WorkingDirectory = '%INSTALL_DIR%'; if (Test-Path '%ICON_FILE%') { $Shortcut.IconLocation = '%ICON_FILE%' }; $Shortcut.Save()" 2>nul

:: Копируем ярлык на рабочий стол и в меню Пуск
if exist "%TEMP_LNK%" (
    copy /Y "%TEMP_LNK%" "%DESKTOP_LNK%" >nul
    copy /Y "%TEMP_LNK%" "%STARTMENU_LNK%" >nul
    del "%TEMP_LNK%"
    echo [OK] Ярлыки созданы
) else (
    echo [WARN] Не удалось создать ярлыки
)

echo.
echo ===============================================
echo     УСТАНОВКА ЗАВЕРШЕНА
echo ===============================================
echo.
echo %APP_DISPLAY% установлен в: %INSTALL_DIR%
echo Ярлык на рабочем столе и в меню Пуск
echo.
pause