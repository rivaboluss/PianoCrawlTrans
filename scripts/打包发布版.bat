@echo off
chcp 65001 >nul
cd /d "%~dp0.."
title 打包发布版
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0make_release.ps1"
pause
