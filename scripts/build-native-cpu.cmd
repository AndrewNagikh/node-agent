@echo off
call "C:\Program Files (x86)\Microsoft Visual Studio\2019\BuildTools\VC\Auxiliary\Build\vcvars64.bat"
set "PATH=C:\Program Files\CMake\bin;%PATH%"
cd /d c:\node-agent\llama.cpp

if exist build rmdir /s /q build

echo cmake configure (CPU)...
cmake -S . -B build -DCMAKE_BUILD_TYPE=Release -DLLAMA_BUILD_TESTS=OFF -DLLAMA_DISTRIBUTED=ON
if errorlevel 1 exit /b 1

echo building targets...
cmake --build build --config Release --target node_agent split_gen3_a split_gen3_b split_gen3_c -j
exit /b %ERRORLEVEL%
