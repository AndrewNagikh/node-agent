#!/usr/bin/env python3
"""Self-test for churn_check.py against a mock cluster.

A regression detector that is itself unverified is worthless -- these cases
check it actually fails on the drifts it exists to catch, and stays quiet on
the ones that are legitimate (an offline node is not data loss).
"""
import json, os, subprocess, sys, threading, tempfile, shutil
from http.server import BaseHTTPRequestHandler, HTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
STATE = {}   # mutated per scenario

def make_handler(kind):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a): pass
        def do_GET(self):
            path = self.path
            if kind == "orch":
                if path == "/nodes":
                    body = [{"node_id": n, "host": "127.0.0.1",
                             "http_port": STATE["ports"][n], "online": v}
                            for n, v in STATE["online"].items()]
                elif path == "/models":
                    body = [{"model_id": m, "coverage": c["coverage"]}
                            for m, c in STATE["models"].items()]
                elif "/layout" in path:
                    mid = path.split("/models/")[1].split("/layout")[0]
                    body = {"placements": STATE["models"][mid]["placements"]}
                else:
                    self.send_error(404); return
            else:  # node
                node = kind
                mid = path.split("model=")[1]
                body = {"layers": STATE["blobs"].get(node, {}).get(mid, [])}
            data = json.dumps(body).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
    return H

servers = []
def serve(kind, port):
    s = HTTPServer(("127.0.0.1", port), make_handler(kind))
    threading.Thread(target=s.serve_forever, daemon=True).start()
    servers.append(s)

def blobs(node, mid, spec):
    return [{"blob_id": b, "size_bytes": sz} for b, sz in spec]

def base_state():
    return {
        "ports": {"node-a": 18801, "node-b": 18802},
        "online": {"node-a": True, "node-b": True},
        "models": {"m1": {
            "coverage": {"state": "READY", "ready_layers": 4, "total_layers": 4},
            "placements": [{"layer": 0, "node": "node-a"}, {"layer": 1, "node": "node-a"},
                           {"layer": 2, "node": "node-b"}, {"layer": 3, "node": "node-b"}],
        }},
        "blobs": {
            "node-a": {"m1": blobs("node-a", "m1", [("layer:0", 100), ("layer:1", 100)])},
            "node-b": {"m1": blobs("node-b", "m1", [("layer:2", 100), ("layer:3", 100)])},
        },
    }

def run(cmd):
    env = dict(os.environ, ORCHESTRATOR="http://127.0.0.1:18800")
    r = subprocess.run([sys.executable, os.path.join(HERE, "churn_check.py")] + cmd,
                       capture_output=True, text=True, env=env)
    return r.returncode, r.stdout + r.stderr

def main():
    serve("orch", 18800); serve("node-a", 18801); serve("node-b", 18802)
    STATE.update(base_state())
    fails = 0
    def check(name, want_rc, rc, out, want_text=None):
        nonlocal fails
        ok = rc == want_rc and (want_text is None or want_text in out)
        print(("  ok   " if ok else "  FAIL ") + name)
        if not ok:
            print(f"        rc={rc} want={want_rc}\n{out.strip()[:400]}")
            fails += 1

    rc, out = run(["snapshot"]); check("snapshot succeeds", 0, rc, out)

    rc, out = run(["verify"]); check("no change -> PASS", 0, rc, out, "PASS")

    # layout drift
    STATE["models"]["m1"]["placements"][2]["node"] = "node-a"
    rc, out = run(["verify"]); check("layout changed -> FAIL", 1, rc, out, "layout changed")
    STATE.update(base_state())

    # blob deleted on an online node
    STATE["blobs"]["node-b"]["m1"] = blobs("node-b", "m1", [("layer:2", 100)])
    rc, out = run(["verify"]); check("blob lost -> FAIL", 1, rc, out, "lost 1 blob")
    STATE.update(base_state())

    # blob re-downloaded (same name, different size) = data moved
    STATE["blobs"]["node-a"]["m1"] = blobs("node-a", "m1", [("layer:0", 100), ("layer:1", 999)])
    rc, out = run(["verify"]); check("blob resized -> FAIL", 1, rc, out, "size\nchanged" if False else "data moved")
    STATE.update(base_state())

    # node offline: absence is NOT loss (principle 2)
    STATE["online"]["node-b"] = False
    STATE["blobs"]["node-b"] = {}
    rc, out = run(["verify", "--expect-offline", "node-b"])
    check("offline node -> PASS (absence != loss)", 0, rc, out, "PASS")

    # coverage regression while all nodes online
    STATE.update(base_state())
    STATE["models"]["m1"]["coverage"] = {"state": "DEGRADED", "ready_layers": 2, "total_layers": 4}
    rc, out = run(["verify"]); check("coverage regressed -> FAIL", 1, rc, out, "now DEGRADED")

    for s in servers: s.shutdown()
    print(f"\n{'FAILED' if fails else 'ALL PASS'} ({fails} failure(s))")
    return 1 if fails else 0

if __name__ == "__main__":
    sys.exit(main())
