# Windows node-c — handoff для Cursor на PC

Документ для продолжения работы на **нативном Windows** (без WSL, без portproxy).  
Цель: собрать node-c с CUDA, открыть сеть, запустить агент в кластере и добиться **E2E generate**.

---

## Топология кластера

| Роль | Хост | IP | Порт HTTP |
|------|------|-----|-----------|
| Orchestrator | homelab | `192.168.50.154` | `9000` |
| node-a | Mac M3 Pro | `192.168.50.42` | `9001` |
| node-b | Mac M1 Pro | `192.168.50.254` | `9002` |
| **node-c** | **Windows PC (RTX 4070 Ti)** | **`192.168.50.51`** | **`9003`** |

Pipeline TCP между воркерами: динамические порты **`9100–9700`** (назначает orchestrator при configure).

---

## Что уже сделано (в репо)

Код запушен в `main` (node-agent) и `feature/distributed-runtime` (llama.cpp submodule).

| Коммит / область | Содержание |
|------------------|------------|
| `llama.cpp` `83cb60cb9` | Native Windows pipeline: `dist_process`, `CreateProcess`, Winsock init/close, убраны блокировки `/configure` и `/pipeline/generate` |
| `node-agent` `0027c14` | `build.ps1`, `run-agent.ps1`, `scripts/setup-windows.ps1` |

**Раньше на WSL** были проблемы: portproxy, timeout на `9003`, `prefill failed` / `recv mid resp failed`.  
**Решение:** нативный Windows — прямой LAN IP, firewall для `9003` + `9100–9700`.

**На Windows ещё не проверялось:** сборка CUDA, запуск агента, sync, generate.

---

## Что нужно сделать на Windows (чеклист)

### 1. Клонировать и обновить репо

```powershell
git clone --recurse-submodules https://github.com/AndrewNagikh/node-agent.git
cd node-agent
git pull
git submodule update --init --recursive
```

Убедиться, что submodule `llama.cpp` на ветке `feature/distributed-runtime` и содержит `tools/distributed/dist_process.cpp`.

### 2. Зависимости

- Git, CMake
- **Visual Studio 2022 Build Tools** с workload **Desktop development with C++**
- **CUDA Toolkit** (для RTX 4070 Ti), `nvcc` в PATH
- NVIDIA driver актуальный

Проверка:

```powershell
cmake --version
cl
nvcc --version
nvidia-smi
```

Сборку лучше запускать из **x64 Native Tools Command Prompt for VS 2022** или Developer PowerShell.

### 3. Сборка (один раз, от администратора — для firewall)

```powershell
powershell -ExecutionPolicy Bypass -File scripts\setup-windows.ps1 -Cuda -Firewall
```

Или вручную:

```powershell
.\build.ps1 -Cuda agents
.\run-agent.ps1 -NodeId node-c -ConfigureFirewallOnly
```

Ожидаемые бинарники в `llama.cpp\build\bin\Release\`:

- `node_agent.exe`
- `split_gen3_a.exe`
- `split_gen3_b.exe`
- `split_gen3_c.exe`

**Важно:** воркеры должны лежать **в той же папке**, что и `node_agent.exe` (spawn ищет их рядом).

### 4. Firewall

Inbound TCP:

- `9003` — HTTP API node-c
- `9100–9700` — pipeline между Mac ↔ Windows

Скрипт `run-agent.ps1 -ConfigureFirewallOnly` создаёт правило `DistributedLLM-node-c`.  
Без этого generate падает с `recv mid resp failed` / `prefill failed`.

### 5. Запуск node-c

С `nodes.conf` в корне репо (см. `nodes.conf.example`, топология уже совпадает с таблицей выше) достаточно:

```powershell
.\run-agent.ps1 NodeId=node-c
```

Без `nodes.conf` — вручную:

```powershell
$env:ORCHESTRATOR = "http://192.168.50.154:9000"
# при необходимости явно:
# $env:ADVERTISE_HOST = "192.168.50.51"

.\run-agent.ps1 -NodeId node-c
```

Проверка с другой машины в LAN:

```bash
curl http://192.168.50.51:9003/health
```

### 6. Sync + generate (с homelab или Mac)

После того как orchestrator видит все 3 ноды:

```bash
ORCHESTRATOR=http://192.168.50.154:9000 \
  python3 llama.cpp/tools/distributed/docker/run_e2e_generate.py --model tinyllama-1.1b
```

Для Qwen3-8B (когда TinyLlama проходит):

```bash
ORCHESTRATOR=http://192.168.50.154:9000 \
  python3 llama.cpp/tools/distributed/docker/run_e2e_generate.py --model qwen3-8b
```

### 7. Если что-то ломается — типичные задачи для Cursor

- [ ] Исправить ошибки **cmake / compile** на MSVC (missing includes, linker `ws2_32`, CUDA arch)
- [ ] Починить **spawn воркеров** (`CreateProcess`, пути с `\`, `.exe` suffix)
- [ ] Проверить **ADVERTISE_HOST** — не должен быть WSL/vEthernet IP
- [ ] Убедиться что **firewall** открыт на Private network
- [ ] При `prefill failed` — смотреть логи node-a/node-b/node-c и порты pipeline
- [ ] При sync failed — `curl` health всех нод, HF_TOKEN в `.env` если нужна загрузка моделей

---

## Ключевые файлы проекта

### Скрипты Windows (начинать отсюда)

| Файл | Назначение |
|------|------------|
| `scripts/setup-windows.ps1` | One-shot: deps + build + firewall hint |
| `build.ps1` | CMake configure + build `node_agent` + workers |
| `run-agent.ps1` | Запуск агента, firewall, проверка воркеров |

### Ядро native Windows port (llama.cpp submodule)

| Файл | Назначение |
|------|------------|
| `llama.cpp/tools/distributed/dist_process.h` | API spawn/kill/env для Windows и Unix |
| `llama.cpp/tools/distributed/dist_process.cpp` | `CreateProcess`, `dist_home_dir`, `dist_exe_suffix` |
| `llama.cpp/tools/distributed/node_agent.cpp` | HTTP API, spawn воркеров, `/configure`, `/pipeline/generate` |
| `llama.cpp/tools/distributed/transport/split_tcp_wire.h` | `split_tcp_init()`, `split_tcp_close()` |
| `llama.cpp/tools/distributed/transport/split_tcp_wire.cpp` | Winsock / BSD sockets, connect retry |
| `llama.cpp/tools/distributed/workers/split_gen3_a.cpp` | Entry stage worker |
| `llama.cpp/tools/distributed/workers/split_gen3_b.cpp` | Middle stage worker |
| `llama.cpp/tools/distributed/workers/split_gen3_c.cpp` | Final stage + sampling |
| `llama.cpp/tools/distributed/CMakeLists.txt` | `dist_process.cpp`, link `ws2_32` on WIN32 |
| `llama.cpp/tools/distributed/dist_http_fetch.cpp` | HTTP range download (curl fallback на Windows) |
| `llama.cpp/tools/distributed/node_benchmark.cpp` | Benchmark при регистрации ноды |

### Кластер и E2E

| Файл | Назначение |
|------|------------|
| `llama.cpp/tools/distributed/orchestrator.cpp` | Orchestrator HTTP API, heartbeat, configure |
| `llama.cpp/tools/distributed/docker/run_e2e_generate.py` | E2E: install → sync → generate |
| `config/architecture_matrix.json` | Поддерживаемые архитектуры моделей |

### Устаревшее (не использовать для node-c)

| Файл | Примечание |
|------|------------|
| `scripts/setup-node-c-from-windows.ps1` | WSL path — **не нужен** для native Windows |

---

## Диагностика

### Health всех нод

```bash
curl http://192.168.50.154:9000/health
curl http://192.168.50.42:9001/health
curl http://192.168.50.254:9002/health
curl http://192.168.50.51:9003/health
```

### Во время активной сессии generate

Порты pipeline смотреть в логах orchestrator / node_agent.  
С Mac проверить доступность порта node-c:

```bash
nc -zv 192.168.50.51 <pipeline_port>
```

`connection refused` на final peer port может быть нормой, если соединение уже принято воркером.

### Типичные ошибки

| Симптом | Вероятная причина |
|---------|-------------------|
| `9003` timeout | Firewall, неверный IP, агент не запущен |
| `configure unsupported on Windows` | Старый бинарник — пересобрать после `git pull` |
| `missing worker split_gen3_*.exe` | Не собраны workers или не в `bin\Release` |
| `prefill failed` / `recv mid resp failed` | Firewall `9100–9700`, падение worker на GPU, Mac firewall |
| cmake: `cl` not found | Запуск не из VS Native Tools shell |
| CUDA errors | Неверная arch, нет `nvcc`, старый driver |

---

## Сообщение для нового чата в Cursor (скопировать)

```
Продолжаем Windows node-c для distributed LLM кластера.

Контекст: docs/WINDOWS_NODE_C_HANDOFF.md

Задача:
1. git pull + submodule update
2. Собрать с CUDA: scripts\setup-windows.ps1 -Cuda -Firewall (или build.ps1 -Cuda)
3. Запустить node-c: run-agent.ps1, ORCHESTRATOR=http://192.168.50.154:9000
4. Починить все ошибки сборки/запуска
5. Добиться curl http://192.168.50.51:9003/health и E2E generate (tinyllama-1.1b)

Кластер: orchestrator 192.168.50.154:9000, node-a .42:9001, node-b .254:9002, node-c .51:9003.
Native Windows (без WSL). Код Windows port уже в llama.cpp (dist_process, split_tcp_init).

Читай docs/WINDOWS_NODE_C_HANDOFF.md и ключевые файлы из таблицы там.
```

---

## Критерий готовности

1. `curl http://192.168.50.51:9003/health` → OK с любой ноды LAN  
2. Orchestrator: все 3 ноды registered, sync → READY  
3. `run_e2e_generate.py --model tinyllama-1.1b` → **PASS** (текст сгенерирован)  
4. Опционально: `qwen3-8b` generate без `prefill failed`
