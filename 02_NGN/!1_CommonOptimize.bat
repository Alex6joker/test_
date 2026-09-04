@echo off
chcp 65001 > nul

:: Переходим в папку, где лежит сам .bat файл (гарантирует правильные пути)
cd /d "%~dp0"

:: Запуск скриптов в разных окнах с аргументами
start "Скрипт 1" /d ".." /wait python "main.py" --work-mode 2 --selected-forts 02_NGN
start "Скрипт 2" /d ".." /wait python "main.py" --work-mode 3 --selected-forts 02_NGN
start "Скрипт 3" /d ".." /wait python "main.py" --work-mode 4 --selected-forts 02_NGN

start "Скрипт 4" /wait python "matrix_filter.py"

:: Ожидание 15 секунд перед закрытием
timeout /t 15

exit