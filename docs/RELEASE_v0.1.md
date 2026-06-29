# v0.1 — Architecture-agnostic distributed runtime

Tag: `v0.1`  
Commit: `fa556b5`  
llama.cpp: `feature/distributed-runtime` @ `1a725b35a`

## Summary

First stable milestone of the distributed LLM node-agent platform with architecture-agnostic runtime (Task 9.9).

- **6 model families** verified: Llama, Qwen, Gemma, Phi, SmolLM, DeepSeek-Qwen
- **ArchitecturePlugin** registry + semantic runtime descriptor
- **Layer-first** install/materialization via semantic blobs
- **Docker 3-node E2E** suite for cluster validation
- **Partial forward** for distributed pipeline: Llama, Qwen, Gemma3

## Supported architectures

| Architecture | Статус | Partial Forward | Hidden Injection | Layer-first | Generate | Verification |
|--------------|--------|-----------------|------------------|-------------|----------|--------------|
| Llama | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| Qwen | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| Gemma | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| Phi | 🟡 | ✅ | ✅ | ✅ | Sync | ✅ |
| SmolLM | 🟡 | ✅ | ✅ | ✅ | Sync | ✅ |
| DeepSeek-Qwen | 🟡 | Через Qwen | Через Qwen | ✅ | Sync | ✅ |

## Quick start

```bash
git clone --recurse-submodules git@github.com:AndrewNagikh/node-agent.git
cd node-agent
git checkout v0.1
./build.sh all

./scripts/run_architecture_suite.sh

cd llama.cpp/tools/distributed/docker
docker compose up -d --build
ORCHESTRATOR=http://127.0.0.1:9000 python3 run_e2e_generate.py
```

## Publish GitHub Release (if not auto-created)

```bash
gh release create v0.1 --title "v0.1 — Architecture-agnostic distributed runtime" --notes-file docs/RELEASE_v0.1.md
```

Or: GitHub → Releases → Draft new release → choose tag `v0.1`.
