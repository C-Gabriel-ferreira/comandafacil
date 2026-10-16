@echo off
setlocal
cd /d "%~dp0"
chcp 65001 >nul
echo ============================================
echo   COMANDAFACIL (versao web)
echo ============================================
set "PY=python"
where python >nul 2>&1
if errorlevel 1 (
  set "PY=py"
  where py >nul 2>&1
  if errorlevel 1 (
    echo Python nao encontrado. Instale em https://www.python.org/downloads/
    echo e marque "Add python.exe to PATH" na instalacao.
    start "" "https://www.python.org/downloads/"
    pause
    exit /b 1
  )
)
%PY% -c "import flask, waitress" >nul 2>&1
if errorlevel 1 (
  echo Instalando dependencias...
  %PY% -m pip install -r requirements.txt
  if errorlevel 1 (
    echo Falha ao instalar. Verifique a internet.
    pause
    exit /b 1
  )
)
REM 0.0.0.0 libera o acesso por celulares/tablets do mesmo Wi-Fi. Para usar só neste PC, troque por 127.0.0.1
set COMANDAFACIL_HOST=0.0.0.0
set COMANDAFACIL_PORT=5000
echo.
echo Neste computador, abra:  http://127.0.0.1:5000
echo Nos celulares (mesmo Wi-Fi), abra:  http://IP-DESTE-PC:5000   (veja o IP com o comando ipconfig)
echo Feche esta janela para encerrar o sistema.
echo.
start "" "http://127.0.0.1:5000"
%PY% app.py
pause
