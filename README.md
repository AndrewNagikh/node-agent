# distributed-llm node-agent

Deploy package for running a **split inference node** on a remote machine.

The agent registers with an orchestrator and runs the appropriate `split_gen3_*` worker when configured.

## Clone

```bash
git clone --recurse-submodules git@github.com:AndrewNagikh/node-agent.git
cd node-agent
```

If you already cloned without submodules:

```bash
git submodule update --init --recursive
```

This pulls the patched [llama.cpp](https://github.com/AndrewNagikh/llama.cpp) fork (`feature/distributed-runtime` branch).

## Build

Requires: `cmake`, `g++`/`clang`, `git`, OpenSSL dev headers.

On macOS:

```bash
xcode-select --install
brew install cmake
```

```bash
./build.sh
```

Binaries:

```
llama.cpp/build/bin/node_agent
llama.cpp/build/bin/split_gen3_{a,b,c}
```

## Run on a worker machine

```bash
MODEL=/path/to/llama-3.2-1b-instruct-q4_k_m.gguf
ORCH=http://ORCHESTRATOR_IP:9000

./llama.cpp/build/bin/node_agent \
  --model "$MODEL" \
  --listen 0.0.0.0:9001 \
  --orchestrator "$ORCH" \
  --node-id node-b
```

Use a unique `--node-id` and `--listen` port on each machine (`node-a`, `node-b`, `node-c`).

## Orchestrator (coordinator machine)

Built from the same llama.cpp fork:

```bash
cmake --build llama.cpp/build --target orchestrator -j$(nproc)

./llama.cpp/build/bin/orchestrator \
  --model "$MODEL" \
  --listen 0.0.0.0:9000
```

Generate:

```bash
SID=$(curl -s http://ORCHESTRATOR_IP:9000/session/create \
  -H 'Content-Type: application/json' \
  -d '{"model":"llama-3.2-1b"}' | jq -r .session_id)

curl -s http://ORCHESTRATOR_IP:9000/session/generate \
  -H 'Content-Type: application/json' \
  -d "{\"session_id\":\"$SID\",\"prompt\":\"Tell me a joke\",\"max_tokens\":32}"
```

## Static pipeline (layout A)

| Node | Role  | Layers    |
|------|-------|-----------|
| A    | entry | [0, 5)    |
| B    | middle| [5, 10)   |
| C    | final | [10, 16)  |

Orchestrator assigns nodes sorted by `node_id`.

## Docs

Full Task 5 details: [llama.cpp/docs/task5_orchestrator.md](llama.cpp/docs/task5_orchestrator.md)
