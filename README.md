# distributed-llm node-agent

Deploy package for running a **split inference node** on a remote machine.

**Release v0.1** — architecture-agnostic distributed runtime, 6 model families, Docker 3-node E2E.

## Supported architectures

| Architecture | Статус | Partial Forward | Hidden Injection | Layer-first | Generate | Verification |
|--------------|--------|-----------------|------------------|-------------|----------|--------------|
| Llama | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| Qwen | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| Gemma | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| Phi | 🟡 | ✅ | ✅ | ✅ | Sync | ✅ |
| SmolLM | 🟡 | ✅ | ✅ | ✅ | Sync | ✅ |
| DeepSeek-Qwen | 🟡 | Через Qwen | Через Qwen | ✅ | Sync | ✅ |

Подробный реестр, модели E2E и инструкции: [docs/supported_architectures.md](docs/supported_architectures.md).

```bash
# Локальная verification suite (plugins + architecture report)
./scripts/run_architecture_suite.sh

# Docker E2E (3-node cluster)
cd llama.cpp/tools/distributed/docker && docker compose up -d --build
ORCHESTRATOR=http://127.0.0.1:9000 python3 run_e2e_generate.py
```

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

- [Supported architectures](docs/supported_architectures.md)
- [Task 9.9 runtime stabilization](llama.cpp/docs/task9_9_runtime_stabilization.md)
- [Orchestrator](llama.cpp/docs/task5_orchestrator.md)
