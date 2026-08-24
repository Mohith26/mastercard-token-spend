# Results

Notes from the runs I actually executed. Machine: Apple silicon MacBook, macOS, Python 3.9.6,
single process, single thread. Everything below reproduces from a clean checkout with the exact
commands shown. The seeded stream is deterministic, so counts are stable run to run; wall clock
and latency numbers wobble a little with machine load.

## Oracle agreement (the headline check)

```
.venv/bin/python scripts/run_eval.py --events 1000000 --bench-events 250000 --out results/eval_1m.json
```

1,000,000 events with seed 42: 997,021 authorization requests plus 2,979 interleaved lifecycle
events. The engine (incremental deques, running sums) and the naive oracle (backward scan of raw
history per decision) produced identical (approved, reason) pairs on every request.

- Oracle mismatches: **0 out of 997,021**
- Engine plus oracle lockstep pass: 9.3 s wall

Reason code distribution over the run (all 11 codes exercised):

| reason | count |
|---|---|
| APPROVED | 485,041 |
| MONTHLY_CAP_EXCEEDED | 229,833 |
| TOKEN_NOT_ACTIVE | 152,803 |
| TXN_CAP_EXCEEDED | 60,856 |
| DAILY_CAP_EXCEEDED | 31,073 |
| GEO_BLOCKED | 13,768 |
| MCC_BLOCKED | 9,444 |
| BAD_CRYPTOGRAM | 5,861 |
| DEVICE_MISMATCH | 4,248 |
| MERCHANT_MISMATCH | 2,120 |
| TOKEN_UNKNOWN | 1,974 |

The decline mix is deliberately unrealistic: the generator injects noise to hit every path, tight
cards saturate their monthly caps over the ~115 simulated days, and suspended tokens keep getting
attempts until a later resume event. See the Limitations section of the README.

## Transition matrix

The same eval run sweeps all 9 state x action pairs on fresh vault tokens: 9/9 exercised, 4 legal,
5 illegal, and the observed outcomes match the declared matrix exactly
(`transition_matrix.matches_declared_matrix: true` in results/eval_1m.json).

## Latency and throughput

Latency: fresh world, first 250,000 events materialized, each `engine.decide()` call wrapped in
`perf_counter_ns`. 249,230 sampled authorizations:

- p50 1.334 us, p90 1.750 us, p99 2.333 us, mean 1.403 us, max 6.1 ms
- 712,606 decisions/sec computed from summed per-call latency (timer overhead is inside each sample,
  so the true function is slightly faster than this)

The max is a scheduling artifact, not the algorithm; the p99.9 region is still single-digit
microseconds. End-to-end throughput on the full 1M stream with no per-call timing, including stream
generation and sha256 cryptogram issuance in the driver loop: 272,073 events/sec (3.675 s wall).

## Tests and coverage

```
.venv/bin/pytest --cov=tokengate --color=no -rN
```

64 passed in 0.65 s. Coverage over `tokengate/`: **97%** (357 statements, 9 missed; the missed lines
are rare generator branches and one defensive oracle branch). Boundary behavior has dedicated tests:
at-cap approves, one cent over declines, a transaction ages out of the daily window at exactly 86,400
seconds and the monthly window at exactly 2,592,000 seconds, and the same offsets give the same
answers at unrelated absolute epochs.

## Repro from scratch

```
python3 -m venv .venv && .venv/bin/pip install -U pip pytest pytest-cov
.venv/bin/pytest --cov=tokengate --color=no -rN
.venv/bin/python scripts/run_eval.py --events 1000000 --bench-events 250000 --out results/eval_1m.json
```
