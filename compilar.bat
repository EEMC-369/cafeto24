@echo off
echo ===================================================
echo   Compilando Cafeto24 con Nuitka...
echo ===================================================

:: Ejecutar compilacion standalone
:: --standalone crea una carpeta con el exe y todas las dependencias DLLs
:: --windows-console-mode=disable oculta la fea consola de comando negra
:: --windows-icon-from-ico configura el icono del ejecutable
call py -3.14 -m nuitka --standalone --enable-plugins=tk-inter --windows-console-mode=disable --windows-icon-from-ico=static/icono.ico CajaCafeto24.py

if %ERRORLEVEL% neq 0 (
    echo [ERROR] La compilacion con Nuitka fallo.
    exit /b %ERRORLEVEL%
)

echo ===================================================
echo   Copiando recursos adicionales a CajaCafeto24.dist
echo ===================================================
:: Nuitka crea la carpeta CajaCafeto24.dist. Necesitamos copiar templates y static dentro.
xcopy /E /I /Y templates CajaCafeto24.dist\templates
xcopy /E /I /Y static CajaCafeto24.dist\static

echo ===================================================
echo   Compilacion finalizada exitosamente en CajaCafeto24.dist
echo ===================================================
pause
