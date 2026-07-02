@echo off
call "C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\VC\Auxiliary\Build\vcvars64.bat"
set "PATH=C:\Program Files\CMake\bin;C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v13.3\bin\x64;%LOCALAPPDATA%\Microsoft\WinGet\Links;%PATH%"
cd /d c:\node-agent\llama.cpp
cmake --build build --target node_agent split_gen3_a split_gen3_b split_gen3_c -j
exit /b %ERRORLEVEL%
