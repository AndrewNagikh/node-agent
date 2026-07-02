@echo off
call "C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\VC\Auxiliary\Build\vcvars64.bat"
cd /d c:\node-agent\llama.cpp\build\bin
echo === node_agent.exe ===
dumpbin /dependents node_agent.exe
echo === ggml-cuda.dll ===
dumpbin /dependents ggml-cuda.dll
