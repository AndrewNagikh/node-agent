# distributed-llm node-agent

Deploy package for running a **split inference node** on a remote machine.

## Quick start

```bash
git clone --recurse-submodules git@github.com:AndrewNagikh/node-agent.git
cd node-agent
git submodule update --init --recursive

./build.sh          # auto: Metal on Mac, CUDA if nvidia-smi, else CPU
./build.sh all      # + orchestrator
```

### Homelab (orchestrator + node-a)

```bash
./run-orchestrator.sh

# other terminal
ORCHESTRATOR=http://127.0.0.1:9000 NODE_ID=node-a ./run-agent.sh
```

### Mac (node-b)

```bash
ORCHESTRATOR=http://192.168.50.154:9000 NODE_ID=node-b ./run-agent.sh
```

### Windows WSL (node-c, NVIDIA)

```bash
ORCHESTRATOR=http://192.168.50.154:9000 NODE_ID=node-c ./run-agent.sh
```

`run-agent.sh` auto-detects: LAN IP, model path, GPU backend (via build), benchmark score.

Set `MODEL=/path/to/model.gguf` if auto-find fails.  
Set `ADVERTISE_HOST=...` if IP detection is wrong.  
`REBENCHMARK=1` to force re-benchmark.

WSL: script prints a Windows `portproxy` hint on startup.

## Manual build overrides

```bash
GGML_CUDA=OFF ./build.sh    # force CPU on Linux
GGML_CUDA=ON  ./build.sh    # force CUDA
```

## Generate

```bash
curl -s http://192.168.50.154:9000/session/create \
  -H 'Content-Type: application/json' \
  -d '{"model":"llama-3.2-1b"}' | jq .

curl -s http://192.168.50.154:9000/session/generate \
  -H 'Content-Type: application/json' \
  -d '{"session_id":"sess-...","prompt":"Tell me a joke","max_tokens":32}'
```

## Docs

[llama.cpp/docs/task5_orchestrator.md](llama.cpp/docs/task5_orchestrator.md)
