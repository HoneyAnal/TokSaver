@echo off
title ByeDPI
rem Ручной запуск ByeDPI (только 127.0.0.1). Порт можно поменять в -p.
ciadpi.exe -i 127.0.0.1 -p 1080 --split 1 --disorder 3+s --mod-http=h,d --auto=torst --tlsrec 1+s
