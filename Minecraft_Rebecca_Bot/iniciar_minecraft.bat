@echo off
cd /d "%~dp0"
if not exist node_modules (
  echo Primero ejecuta: npm install
  pause
  exit /b 1
)
npm start
pause
