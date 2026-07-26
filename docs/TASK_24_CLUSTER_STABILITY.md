# Task 24 — Cluster stability under node churn

**Status:** specified, not started. Written 2026-07-27.
**Priority:** highest remaining work item. Everything else waits.

## The requirement, in one sentence

Nodes come and go at random — someone closes a laptop, a machine reboots,
Wi-Fi drops — and the cluster keeps working, returns to its usual layout once
everyone is back, and **never re-downloads a layer it already had**.

Concretely, the bar is: **several days of chaotic on/off operation with zero
manual intervention.**

## Why this is the top priority

The runtime already does the hard thing (a 70B model across three mismatched
consumer machines, 190/190 soak cycles). What it does not survive is ordinary
domestic reality. On 2026-07-24 alone, on a cluster that had been healthy:

- a layout collapsed onto a single node, producing a one-stage pipeline that
  **crashed the orchestrator process** (no final role is ever assigned when
  there is only one stage);
- `gemma-3-1b` lost its entire node-a share and needed a 424 MB re-download,
  because the optimizer "repaired" a model that was never broken;
- models needed manual repair three times over, and one of them
  (`qwen3-14b`) ended up with all 40 layers on a single node while the layout
  expected 7/7/26 — 3 GB to move back.

None of that was caused by a node actually failing. It was caused by the
orchestrator **acting on state it had not verified**.

## Root cause (established, not hypothesised)

Two facts combine badly.

1. **Layout is derived from live cluster state; installed blobs follow the
   layout.** So anything that changes the layout invalidates data on disk.
2. **Coverage is polled at moments when the answer is meaningless** — during
   startup while nodes are still registering, or while nodes are busy loading
   tensors — and the optimizer treats "I could not see it" as "it is gone".

The result is a feedback loop: incomplete view → relayout → orphaned blobs →
model is not READY → qualifies for another relayout on the next cluster change.
A model that fell out of READY could not recover on its own.

Partial mitigations already landed (2026-07-24, `74371b6` / `c7d2296`):
re-poll coverage before judging, skip models whose layout references an offline
node, cap futile relayout attempts, refuse one-stage pipelines, self-heal a
structurally broken layout on session create. Those stop the bleeding. They do
not deliver the requirement.

## The design change this needs

**Invert the authority.** Installed layers are the durable, expensive state;
the layout is a cheap derived artifact. Today it is backwards.

Working principles for the implementation:

1. **The layout is sticky.** Once a model is installed, its layout is the
   default and stays. A recomputation must justify itself against what is
   already on disk, not against an idealised placement.
2. **Offline is not lost.** A node that is unreachable still holds its layers.
   Its share is never reassigned, and its blobs are never deleted, on the basis
   of its absence.
3. **Deletion requires positive evidence.** A blob is removed only when a node
   that is demonstrably online and healthy reports it as unneeded. "The poll
   timed out" is not evidence.
4. **Degraded is a state, not a repair trigger.** With a node missing, a model
   that needs it is simply unavailable until it returns. It must not be
   rebuilt around the survivors.
5. **Return is free.** When the node comes back, the original layout resumes
   with no data movement. This is the acceptance test.

## Open design questions

- **Should a model run on a reduced node set at all?** Running 3-node models on
  2 nodes needs a second, temporary layout while preserving the original — more
  machinery, and it competes with principle 5. Probably not worth it until
  stability is proven; note the decision either way.
- **What legitimately justifies a relayout?** A node permanently leaving, a
  model being reinstalled, an explicit user request. Probably nothing else.
  Automatic optimisation for throughput has now been reverted twice (G4, Task
  21.4) — both times it cost more than it gained.
- **Where does the sticky layout live?** It is already persisted in the
  registry; the question is whether it needs its own durability guarantees
  separate from coverage, which is transient by nature.

## Acceptance test

Not a code review — a measurement, run before this is called done:

1. Cluster healthy, all models READY, note every layout.
2. Stop one node. Confirm: no relayout, no deletion, models that do not need
   that node still serve.
3. Restart it. Confirm: original layout resumes, **zero bytes transferred**,
   coverage returns to READY without manual action.
4. Repeat for each node, and for two nodes at once.
5. Restart the orchestrator mid-churn. Confirm the same.
6. Then leave the cluster running for several days with normal use and
   incidental machine restarts. Zero manual repairs is the pass condition.

Step 3 is the one that matters most: it is exactly what fails today.

## Related

- `docs/KNOWN_ISSUES.md` — the design reversals (G4, Task 21.4) and the
  incidents above
- ROADMAP Task 22.1/22.2 (fault tolerance, dynamic membership) — adjacent but
  larger; this task is the prerequisite that makes them meaningful
