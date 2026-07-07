#!/usr/bin/env python3
import json
import unittest
from pathlib import Path

from perf_trace.token_timeline import reconstruct_token_timeline


REPO = Path(__file__).resolve().parents[2]
DOCKER_RAW = REPO / "logs/perf_trace/docker_verify_20260707_151625/raw"


class TokenTimelineTest(unittest.TestCase):
    @unittest.skipUnless(DOCKER_RAW.is_dir(), "docker verify trace not present")
    def test_token_17_docker_trace(self) -> None:
        tl = reconstruct_token_timeline(DOCKER_RAW, "trace-000004", 17)
        assert tl is not None
        self.assertEqual(tl.token_idx, 17)
        self.assertGreater(tl.period_ms, 50.0)
        ids = [s.step_id for s in tl.steps]
        for required in (
            "orchestrator_send",
            "entry_recv",
            "entry_compute",
            "entry_send",
            "middle_recv",
            "middle_compute",
            "final_recv",
            "final_compute",
            "orchestrator_recv",
            "orchestrator_send_next",
        ):
            self.assertIn(required, ids)
        orch_recv = next(s for s in tl.steps if s.step_id == "orchestrator_recv")
        self.assertGreater(orch_recv.dur_ms, 35.0)
        self.assertLess(orch_recv.dur_ms, 50.0)


if __name__ == "__main__":
    unittest.main()
