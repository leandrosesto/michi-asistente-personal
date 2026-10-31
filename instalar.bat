@echo off
rem Instala Michi: dependencias de Python y la voz local argentina (Daniela, ~110 MB).
cd /d "%~dp0"

where uv >nul 2>nul
if errorlevel 1 (
    echo Instalando uv, el gestor de Python...
    powershell -NoProfile -ExecutionPolicy Bypass -Command "irm https://astral.sh/uv/install.ps1 | iex"
    set "PATH=%USERPROFILE%\.local\bin;%PATH%"
)

echo Instalando dependencias (puede tardar unos minutos la primera vez)...
uv sync || goto :error

echo Descargando la voz local argentina...
if not exist voces mkdir voces
.venv\Scripts\python.exe -m piper.download_voices --download-dir voces es_AR-daniela-high || goto :error

echo.
echo Listo. Abri Michi con iniciar.bat y carga tus claves gratis con clic derecho ^> Configurar.
pause
exit /b 0

:error
echo.
echo Algo fallo durante la instalacion. Revisa el mensaje de arriba.
pause
exit /b 1
