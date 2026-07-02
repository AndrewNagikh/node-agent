@echo off
call "C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\VC\Auxiliary\Build\vcvars64.bat" >nul 2>&1
cd /d c:\node-agent\llama.cpp\build\bin
set "URL=https://huggingface.co/TheBloke/TinyLlama-1.1B-Chat-v1.0-GGUF/resolve/main/tinyllama-1.1b-chat-v1.0.Q4_K_M.gguf"
set "OUT=%TEMP%\curl_test.bin"
del "%OUT%" 2>nul
echo Testing curl capture to file...
curl.exe -sfL --max-time 120 --range 55469440-55478655 -o "%OUT%" "%URL%"
echo curl exit=%ERRORLEVEL%
for %%A in ("%OUT%") do echo file size=%%~zA bytes
exit /b 0
