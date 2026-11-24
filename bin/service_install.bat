@echo off
chcp 65001 >nul
title ByeDPI - Install Service
pushd "%~dp0"

echo ============================================================
echo  ВНИМАНИЕ: этот скрипт установит ByeDPI как СЛУЖБУ Windows.
echo  Служба запускается автоматически вместе с системой, работает
echo  с правами администратора и слушает 127.0.0.1:1080 (порт, не
echo  доступный из сети). Программе TokSaver эта служба НЕ нужна:
echo  она сама запускает ByeDPI на время своей работы.
echo  Ставьте службу, только если понимаете, зачем она вам.
echo  Удалить её позже: service_delete.bat
echo ============================================================
echo.
set /p CONFIRM=Установить службу? Введите YES и нажмите Enter: 
if /i not "%CONFIRM%"=="YES" (
    echo Отменено. Ничего не установлено.
    popd
    pause
    exit /b 1
)

echo This script should be run with administrator privileges.
echo Right click - run as administrator.
echo Press any key if you're running it as administrator.
pause

set svc_name="ByeDPI"
set svc_desc="Local SOCKS proxy server to bypass DPI (Deep Packet Inspection)."

:: Set up launch args (bypass methods) here. Listens only on 127.0.0.1.
:: Change the port in -p if 1080 is already used on this machine.
set svc_bin="\"%cd%\ciadpi.exe\" -i 127.0.0.1 -p 1080 --split 1 --disorder 3+s --mod-http=h,d --auto=torst --tlsrec 1+s"

sc stop %svc_name%
sc delete %svc_name%
sc create %svc_name% binPath= %svc_bin% start= "auto"
sc description %svc_name% %svc_desc%
sc start %svc_name%

popd
pause
