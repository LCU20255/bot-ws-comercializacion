@echo off
set "PATH=%~dp0..\data\mingit\cmd;C:\Program Files\nodejs;%PATH%"
echo Verificando Git y Node...
git --version
node -v
echo Instalando Baileys y dependencias...
call "C:\Program Files\nodejs\npm.cmd" install
echo Instalacion completada!
