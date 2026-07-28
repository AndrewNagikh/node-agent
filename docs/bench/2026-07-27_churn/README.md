# Task 24 acceptance harness — node churn

Turns the acceptance criteria of
[TASK_24_CLUSTER_STABILITY.md](../../TASK_24_CLUSTER_STABILITY.md) into
something measurable. "The layout did not change" and "zero bytes were
transferred" are invariants; this records them before churn and checks them
after, instead of judging by eye from the dashboard.

## What it checks

| Invariant | Why it matters |
|---|---|
| Layout is identical | A changed placement is what orphans blobs — a failure even if coverage still reads READY |
| No blob disappeared from an **online** node | Deletion is the irreversible one |
| No blob changed size | Same name at a different size means it was re-fetched — data moved |
| Coverage did not regress while all of a model's nodes are online | Catches breakage that leaves the files in place |

An **offline** node is skipped deliberately: its layers are still on its disk,
and absence is not loss (Task 24, principle 2).

## Use

```bash
export ORCHESTRATOR=http://192.168.50.154:9000   # optional, this is the default

python3 churn_check.py snapshot                  # cluster healthy, before touching anything

# stop a node, wait for the orchestrator to notice
python3 churn_check.py verify --expect-offline node-b

# start it again, wait for it to register
python3 churn_check.py verify                    # the step that matters: free return
```

Exit code 0 = invariants held, 1 = something drifted. Repeat per node, for two
nodes at once, and with an orchestrator restart in the middle.

The third command is the one expected to fail today: a returning node triggers
re-planning that moves layers which never needed to move.

## Self-test

`churn_check.py` is a regression detector, so it is itself tested against a
mock cluster — a detector nobody verified is worth nothing:

```bash
python3 selftest.py
```

Covers: clean run passes; layout drift, blob loss, blob resize and coverage
regression each fail; an offline node passes.
