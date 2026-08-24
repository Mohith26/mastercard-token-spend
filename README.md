# TokenGate

I wanted to understand what actually happens between "tap phone" and "approved", so I built a small
payment-network-style engine from scratch: card numbers get replaced by device-bound tokens with a real
lifecycle, and every authorization runs through a fixed chain of spend controls with exact integer-cent
math. Then I built a second, deliberately dumb implementation of the whole decision path and made the
two argue over a million synthetic transactions until they agreed on every single one.

Everything here is invented. The cryptogram is a sha256 stub, the message shapes are my own, and I did
not consult any real network specification. The interesting parts are the state machine, the window
math, and the differential testing setup, not protocol fidelity.

## The two halves

**Token vault** (`tokengate/tokens.py`). Provisioning maps a PAN to a hashed token id that never embeds
card digits. Each token is bound to one device and optionally one merchant. The lifecycle is a strict
transition matrix over ACTIVE, SUSPENDED, DELETED with suspend, resume, delete actions: 4 legal pairs,
5 illegal pairs that raise, DELETED terminal. The PAN only comes back out through a separate
`detokenize` path, never through the public view. Each token also carries a toy cryptogram scheme: a
per-token derived key, a strictly increasing transaction counter, and a hash check, which is enough to
make replay detection testable.

**Authorization engine** (`tokengate/engine.py`). Ten rules in a fixed, documented order: token
existence, token state, device binding, merchant binding, cryptogram, MCC blocklist, geo blocklist,
per-transaction cap, rolling 24 hour cap, rolling 30 day cap. First failure wins and names a reason
code. Windows are pure epoch-second arithmetic (a prior approval at time u counts at time t iff
t - u < window), sums are exact integer cents, at-cap approves and one cent over declines, and only
approved transactions consume velocity. The engine keeps per-card deques with running sums so a
decision is amortized O(1).

## The oracle

`tokengate/oracle.py` is the referee: a from-scratch reimplementation that shares no runtime state or
bookkeeping code with the engine. It tracks lifecycle with hand-rolled if/else rules, recomputes every
cryptogram with its own sha256 calls, and answers every velocity question by scanning the raw per-card
history list backwards and summing inside the window, with no caching. The harness feeds both sides an
identical seeded stream of 1,000,000 events (auths plus interleaved lifecycle events, with injected
wrong devices, replayed counters, corrupted cryptograms, and unknown tokens) and compares every
(approved, reason) pair. Current result: 0 mismatches across 997,021 authorization requests, with all
11 reason codes exercised. Numbers and reproduction commands live in RESULTS.md.

## Running it

```
python3 -m venv .venv && .venv/bin/pip install -U pip pytest pytest-cov
.venv/bin/pytest                                  # 64 tests
.venv/bin/python scripts/run_eval.py              # full 1M eval, writes results/eval_1m.json
```

## Limitations

- The cryptogram is a hash stub with a counter. It demonstrates replay rejection and nothing else; it
  has no relationship to real payment cryptography.
- No real message formats. Requests are plain tuples, not ISO-style messages, and there is no network,
  serialization, or persistence layer; everything is in-process and in-memory.
- The rolling windows are fixed at 24 hours and 30 days of epoch seconds. Real issuers reason about
  calendar days, time zones, and statement cycles; I deliberately avoided all of that so the math
  stays exact and testable.
- The synthetic stream is tuned to exercise every decline path, not to look like real traffic. Monthly
  cap declines and suspended-token declines are far more common than any real portfolio would see,
  because suspensions accumulate and tight caps saturate over the simulated ~115 days.
- Latency numbers measure a Python function call on one core of my laptop. They are useful for
  comparing my own changes, not as a claim about production authorization systems.
- Single card scheme, single currency, no partial approvals, reversals, or refunds.
