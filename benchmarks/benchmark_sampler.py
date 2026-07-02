"""Background node/orchestrator sampling during benchmark runs."""

from __future__ import annotations

import threading
import time
from typing import Any, Callable


class MetricsSampler:
    """Poll cluster + node endpoints every interval_ms."""

    def __init__(
        self,
        interval_ms: int,
        snapshot_fn: Callable[[], dict[str, Any]],
        node_sample_fn: Callable[[dict[str, Any]], dict[str, Any]],
    ) -> None:
        self.interval_ms = interval_ms
        self.snapshot_fn = snapshot_fn
        self.node_sample_fn = node_sample_fn
        self.samples: list[dict[str, Any]] = []
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self) -> list[dict[str, Any]]:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)
        return self.samples

    def _loop(self) -> None:
        while not self._stop.is_set():
            t = time.time()
            try:
                cluster = self.snapshot_fn()
                node_detail = self.node_sample_fn(cluster)
                self.samples.append({
                    "t": t,
                    "cluster": cluster,
                    "nodes": node_detail,
                })
            except Exception as exc:  # noqa: BLE001
                self.samples.append({"t": t, "error": str(exc)})
            self._stop.wait(self.interval_ms / 1000.0)


def summarize_samples(samples: list[dict[str, Any]]) -> dict[str, Any]:
    if not samples:
        return {"sample_count": 0}
    ram_vals, vram_vals = [], []
    for s in samples:
        for n in s.get("cluster", {}).get("nodes", []):
            if n.get("free_ram_gb") is not None:
                ram_vals.append(float(n["free_ram_gb"]))
            if n.get("free_vram_gb") is not None:
                vram_vals.append(float(n["free_vram_gb"]))
    return {
        "sample_count": len(samples),
        "interval_ms_estimated": round(
            (samples[-1]["t"] - samples[0]["t"]) / max(len(samples) - 1, 1) * 1000, 1
        ) if len(samples) > 1 else None,
        "ram_free_gb": _min_max_avg(ram_vals),
        "vram_free_gb": _min_max_avg(vram_vals),
        "note": "CPU/GPU/disk/network require runtime exporters; RAM/VRAM from /nodes polling",
    }


def _min_max_avg(vals: list[float]) -> dict[str, float | None]:
    if not vals:
        return {"min": None, "max": None, "avg": None}
    return {
        "min": round(min(vals), 3),
        "max": round(max(vals), 3),
        "avg": round(sum(vals) / len(vals), 3),
    }


def estimate_network_delta(samples: list[dict[str, Any]]) -> dict[str, Any]:
    """Derived network proxy — unavailable without runtime counters."""
    return {
        "measurement_source": "unavailable",
        "hidden_packets": None,
        "total_bytes": None,
        "avg_latency_ms": None,
        "max_latency_ms": None,
        "retransmits": None,
        "reconnects": None,
        "note": "Passive benchmark cannot observe hidden tensors without runtime hooks",
    }
