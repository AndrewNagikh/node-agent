# Running a node or the orchestrator in Docker

One container is one machine's role. Same rule as the native launchers: the
only address you supply is where the orchestrator is — a machine detects its
own and advertises it when it registers.

```bash
cp docker/.env.example docker/.env
```

On the coordinating machine:

```bash
docker compose -f docker/docker-compose.yml --profile orchestrator up -d --build
```

On every other machine (set `ORCHESTRATOR` in `docker/.env` first):

```bash
docker compose -f docker/docker-compose.yml --profile node up -d --build
```

The node id defaults to the machine's hostname, so nothing else needs setting.
A cluster of any size joins this way — nothing anywhere is told how many nodes
to expect.

## Read this before using it on macOS or Windows

**A containerised node runs on CPU only.** No Metal, and no CUDA unless you
install the container toolkit and pass `--gpus`. On a Mac the native
`./run-agent.sh` will be several times faster, because it uses Metal and the
container cannot. Docker is the better choice on a headless Linux box, or when
you want the node isolated — not for speed.

**Host networking is Linux-only.** The compose file uses `network_mode: host`
deliberately: nodes advertise the address others reach them at, and the worker
transports pick a port at runtime derived from the orchestrator's pid (roughly
9110–9700), so there is no fixed set of ports to publish. On Docker Desktop
host networking does not do this, so a node started there would advertise a
bridge address no other machine can route to. The entrypoint detects that case
and refuses to start rather than joining a cluster that cannot answer it.

If you do want a container on Docker Desktop, drop `network_mode: host` and
publish the range explicitly:

```yaml
    ports:
      - "9001:9001"
      - "9100-9700:9100-9700"
    environment:
      ADVERTISE_HOST: "192.0.2.11"   # this machine's LAN IP, required here
```

That works, but publishing 600 ports to dodge a pid-derived port is a
workaround, and worth knowing you are making.

## Where the models live

`MODELS_DIR` in `.env` is a **host** path, bind-mounted into the container. It
holds the layer shards, and it is deliberately not a named volume: those get
discarded with `docker compose down -v`, and re-downloading a 70B model's
shards is the exact cost this project is built to avoid paying twice.

Put it somewhere with room and somewhere you will not casually delete.

## Checking it worked

```bash
curl -s http://<orchestrator-host>:9000/nodes
```

Each node should appear with `online: true` and its LAN address — not a
`172.x` bridge address. If you see one, the node registered somewhere
unreachable and the cluster will fail the moment it is asked to do work.

```bash
docker compose -f docker/docker-compose.yml --profile node logs -f
```

## Not the same as the test harness

`llama.cpp/tools/distributed/docker/` runs all three nodes side by side on one
host over a bridge network, with the verify tools baked in. That is for
testing the runtime. This directory is for putting a real node on a real
machine.
