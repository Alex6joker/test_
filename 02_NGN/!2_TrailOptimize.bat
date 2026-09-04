@echo off
chcp 65001 > nul

:: Переходим в папку, где лежит сам .bat файл (гарантирует правильные пути)
cd /d "%~dp0"

:: Запуск скриптов в разных окнах с аргументами
start "Скрипт 1" /d ".." /wait python "main.py" --work-mode 7 --selected-forts 01_SILVER
start "Скрипт 2" /d ".." /wait python "main.py" --work-mode 8 --selected-forts 01_SILVER
start "Скрипт 3" /d ".." /wait python "main.py" --work-mode 9 --selected-forts 01_SILVER

start "Скрипт 4" /wait python "matrix_filter_trail.py"

:: Ожидание 15 секунд перед закрытием
timeout /t 15

exit