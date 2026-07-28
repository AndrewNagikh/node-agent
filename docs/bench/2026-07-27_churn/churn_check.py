#!/usr/bin/env python3
"""Task 24 acceptance harness: prove a cluster survives node churn untouched.

The acceptance criteria in docs/TASK_24_CLUSTER_STABILITY.md are invariants,
not impressions -- "the layout did not change", "zero bytes were transferred",
"no blob was deleted". This records them and checks them, so a churn test is a
measurement rather than a look at the dashboard.

Usage:
    churn_check.py snapshot            # before touching anything
    churn_check.py verify              # after stopping/starting nodes
    churn_check.py verify --expect-offline node-b

Typical run:
    churn_check.py snapshot
    # stop node-b, wait, verify (it should hold: nothing moved)
    churn_check.py verify --expect-offline node-b
    # start node-b again, wait for it to register
    churn_check.py verify             # the one that matters: free return

Exit code 0 = all invariants held, 1 = something drifted.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

ORCH = os.environ.get("ORCHESTRATOR", "http://127.0.0.1:9000")
STATE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "snapshot.json")
TIMEOUT = 30


def get(url: str):
    with urllib.request.urlopen(url, timeout=TIMEOUT) as r:
        return json.loads(r.read())


def nodes() -> dict:
    """node_id -> {host, port, online}."""
    raw = get(f"{ORCH}/nodes")
    items = raw if isinstance(raw, list) else raw.get("nodes", [])
    return {
        n["node_id"]: {
            "host": n.get("host"),
            "port": n.get("http_port") or n.get("port"),
            "online": bool(n.get("online")),
        }
        for n in items
    }


def blob_inventory(node: dict, model_id: str) -> dict:
    """blob_id -> total bytes held by this node for this model.

    Byte totals are what make 'zero transferred' checkable: a layer that was
    deleted and re-downloaded looks identical by name but shows up here as a
    disappearance followed by a reappearance across two snapshots.
    """
    url = f"http://{node['host']}:{node['port']}/installed-layers?model={model_id}"
    try:
        layers = get(url).get("layers", [])
    except (urllib.error.URLError, OSError, TimeoutError):
        return {}
    out: dict[str, int] = {}
    for entry in layers:
        blob = entry.get("blob_id")
        if blob is None:
            continue
        out[blob] = out.get(blob, 0) + int(entry.get("size_bytes") or 0)
    return out


def collect() -> dict:
    models = get(f"{ORCH}/models")
    node_map = nodes()
    snap = {"orchestrator": ORCH, "nodes": node_map, "models": {}}

    for m in models:
        mid = m["model_id"]
        cov = m.get("coverage") or {}
        if not cov:
            continue  # never installed; nothing to protect

        try:
            layout = get(f"{ORCH}/models/{mid}/layout").get("placements", [])
        except (urllib.error.URLError, OSError):
            layout = []

        placement = {}
        for p in layout:
            placement.setdefault(p.get("node"), []).append(p.get("layer"))
        for node_id in placement:
            placement[node_id] = sorted(x for x in placement[node_id] if x is not None)

        snap["models"][mid] = {
            "coverage_state": cov.get("state"),
            "ready_layers": cov.get("ready_layers"),
            "total_layers": cov.get("total_layers"),
            "placement": placement,
            "blobs": {
                nid: blob_inventory(n, mid)
                for nid, n in node_map.items()
                if n["online"]
            },
        }
    return snap


def cmd_snapshot() -> int:
    snap = collect()
    with open(STATE, "w") as f:
        json.dump(snap, f, indent=1, sort_keys=True)
    online = [n for n, v in snap["nodes"].items() if v["online"]]
    total_blobs = sum(
        len(b) for m in snap["models"].values() for b in m["blobs"].values()
    )
    print(f"snapshot written: {STATE}")
    print(f"  nodes online : {', '.join(sorted(online)) or 'none'}")
    print(f"  models       : {len(snap['models'])}")
    print(f"  blobs tracked: {total_blobs}")
    return 0


def cmd_verify(expect_offline: list[str]) -> int:
    if not os.path.exists(STATE):
        print("no snapshot -- run `churn_check.py snapshot` first", file=sys.stderr)
        return 1
    with open(STATE) as f:
        before = json.load(f)
    after = collect()

    failures: list[str] = []
    notes: list[str] = []

    for node_id in expect_offline:
        if after["nodes"].get(node_id, {}).get("online"):
            notes.append(f"{node_id} was expected offline but reports online")

    for mid, was in before["models"].items():
        now = after["models"].get(mid)
        if now is None:
            failures.append(f"{mid}: disappeared from the registry")
            continue

        # 1. Layout must be identical. A changed placement is the thing that
        #    orphans blobs, so it is a failure even if coverage still says READY.
        if was["placement"] != now["placement"]:
            failures.append(
                f"{mid}: layout changed\n"
                f"      before {json.dumps(was['placement'], sort_keys=True)}\n"
                f"      after  {json.dumps(now['placement'], sort_keys=True)}"
            )

        # 2. No blob may vanish from a node that is online in both samples.
        #    An offline node is skipped -- absence is not loss (principle 2).
        for node_id, before_blobs in was["blobs"].items():
            node_now = after["nodes"].get(node_id, {})
            if not node_now.get("online"):
                if node_id not in expect_offline:
                    notes.append(f"{mid}: {node_id} offline, blob check skipped")
                continue
            after_blobs = now["blobs"].get(node_id, {})
            lost = sorted(set(before_blobs) - set(after_blobs))
            if lost:
                nbytes = sum(before_blobs[b] for b in lost)
                failures.append(
                    f"{mid}: {node_id} lost {len(lost)} blob(s), "
                    f"{nbytes / 2**20:.1f} MB -- {', '.join(lost[:6])}"
                    f"{' …' if len(lost) > 6 else ''}"
                )
            # Re-downloaded content is the same name at a different size, or a
            # blob that came back after having been gone -- both are movement.
            resized = [
                b for b in set(before_blobs) & set(after_blobs)
                if before_blobs[b] != after_blobs[b]
            ]
            if resized:
                failures.append(
                    f"{mid}: {node_id} has {len(resized)} blob(s) whose size "
                    f"changed -- data moved: {', '.join(sorted(resized)[:6])}"
                )

        # 3. Coverage must not regress while every node it needs is online.
        needed = set(was["placement"])
        all_present = all(after["nodes"].get(n, {}).get("online") for n in needed)
        if all_present and was["coverage_state"] == "READY" and now["coverage_state"] != "READY":
            failures.append(
                f"{mid}: was READY with all its nodes online, now "
                f"{now['coverage_state']} ({now['ready_layers']}/{now['total_layers']})"
            )

    for n in notes:
        print(f"note: {n}")
    if failures:
        print(f"\nFAIL — {len(failures)} invariant(s) broken:")
        for f_ in failures:
            print(f"  - {f_}")
        return 1
    print(f"\nPASS — layouts unchanged, no blob lost or moved, coverage held "
          f"({len(before['models'])} models checked)")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["snapshot", "verify"])
    ap.add_argument("--expect-offline", nargs="*", default=[],
                    help="node ids deliberately stopped; their blobs are not checked")
    args = ap.parse_args()
    if args.command == "snapshot":
        return cmd_snapshot()
    return cmd_verify(args.expect_offline)


if __name__ == "__main__":
    sys.exit(main())
