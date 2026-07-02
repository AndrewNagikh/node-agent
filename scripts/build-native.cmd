@echo off
call "C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\VC\Auxiliary\Build\vcvars64.bat"
set "PATH=C:\Program Files\CMake\bin;C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v13.3\bin;%LOCALAPPDATA%\Microsoft\WinGet\Links;%PATH%"
cd /d c:\node-agent\llama.cpp

if exist build rmdir /s /q build 2>nul

echo cmake configure (CUDA + Ninja)...
cmake -G Ninja -S . -B build ^
  -DCMAKE_BUILD_TYPE=Release ^
  -DCMAKE_CUDA_COMPILER="C:/Program Files/NVIDIA GPU Computing Toolkit/CUDA/v13.3/bin/nvcc.exe" ^
  -DCMAKE_CUDA_ARCHITECTURES=89 ^
  -DLLAMA_BUILD_TESTS=OFF ^
  -DLLAMA_DISTRIBUTED=ON ^
  -DGGML_CUDA=ON
if errorlevel 1 exit /b 1

echo building targets...
cmake --build build --target node_agent split_gen3_a split_gen3_b split_gen3_c -j
exit /b %ERRORLEVEL%
