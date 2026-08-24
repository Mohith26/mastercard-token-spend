"""Full evaluation harness.

Phase 1: transition matrix sweep (all 9 state x action pairs).
Phase 2: oracle agreement over the seeded stream (default 1,000,000 events).
Phase 3: latency bench over a fresh materialized prefix (per-call perf_counter_ns).
Phase 4: raw throughput over the full stream with no per-call timing.

Writes one JSON results file. Run from the repo root:

  .venv/bin/python scripts/run_eval.py --events 1000000 --out results/eval_1m.json
"""

import argparse
import json
import platform
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tokengate.errors import IllegalTransition
from tokengate.oracle import ReferenceOracle
from tokengate.stream import build_world, event_stream, run_events
from tokengate.tokens import ACTIONS, STATES, TRANSITIONS, TokenVault


def transition_matrix_sweep():
    """Exercise every (state, action) pair on real vault tokens."""
    results = {}
    for state in STATES:
        for action in ACTIONS:
            vault = TokenVault()
            tok = vault.provision("5100000000000000", "dev_0")
            if state == "SUSPENDED":
                vault.transition(tok, "suspend")
            elif state == "DELETED":
                vault.transition(tok, "delete")
            try:
                nxt = vault.transition(tok, action)
                results["%s:%s" % (state, action)] = nxt
            except IllegalTransition:
                results["%s:%s" % (state, action)] = "ILLEGAL"
    legal = sum(1 for v in results.values() if v != "ILLEGAL")
    expected = {("%s:%s" % (s, a)): TRANSITIONS.get((s, a), "ILLEGAL")
                for s in STATES for a in ACTIONS}
    return {
        "pairs_exercised": len(results),
        "pairs_total": len(STATES) * len(ACTIONS),
        "legal": legal,
        "illegal": len(results) - legal,
        "matches_declared_matrix": results == expected,
        "matrix": results,
    }


def oracle_run(seed, n_events):
    world = build_world(seed)
    oracle = ReferenceOracle(world.token_dir, world.controls, world.secret)
    t0 = time.perf_counter()
    n_auths, mismatches, reasons = run_events(world, event_stream(world, seed, n_events), oracle)
    dt = time.perf_counter() - t0
    return {
        "seed": seed,
        "events": n_events,
        "auth_requests": n_auths,
        "lifecycle_events": n_events - n_auths,
        "oracle_mismatches": mismatches,
        "reason_counts": dict(sorted(reasons.items())),
        "wall_seconds_engine_plus_oracle": round(dt, 3),
    }


def latency_bench(seed, n_events):
    world = build_world(seed)
    events = list(event_stream(world, seed, n_events))
    engine = world.engine
    vault = world.vault
    pcn = time.perf_counter_ns
    samples = []
    for ev in events:
        if ev[0] == "L":
            vault.transition(ev[1], ev[2])
            continue
        _, tok, amount, mcc, country, device, merchant, ts, atc, cryptogram = ev
        t0 = pcn()
        engine.decide(tok, amount, mcc, country, device, merchant, ts, atc, cryptogram)
        samples.append(pcn() - t0)
    samples.sort()
    n = len(samples)
    total_ns = sum(samples)
    return {
        "sampled_auths": n,
        "p50_us": round(samples[n // 2] / 1000, 3),
        "p90_us": round(samples[int(n * 0.90)] / 1000, 3),
        "p99_us": round(samples[int(n * 0.99)] / 1000, 3),
        "max_us": round(samples[-1] / 1000, 3),
        "mean_us": round(total_ns / n / 1000, 3),
        "auths_per_sec_from_summed_latency": round(n / (total_ns / 1e9)),
    }


def throughput_run(seed, n_events):
    world = build_world(seed)
    events = event_stream(world, seed, n_events)
    t0 = time.perf_counter()
    n_auths, _, _ = run_events(world, events, None)
    dt = time.perf_counter() - t0
    return {
        "events": n_events,
        "auth_requests": n_auths,
        "wall_seconds_including_stream_generation": round(dt, 3),
        "end_to_end_events_per_sec": round(n_events / dt),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--events", type=int, default=1_000_000)
    ap.add_argument("--bench-events", type=int, default=250_000)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--out", default="results/eval_1m.json")
    args = ap.parse_args()

    out = {
        "machine": {
            "platform": platform.platform(),
            "machine": platform.machine(),
            "python": platform.python_version(),
            "note": "single process, single thread",
        },
        "transition_matrix": transition_matrix_sweep(),
    }
    print("transition matrix: %(pairs_exercised)d/%(pairs_total)d pairs, "
          "%(legal)d legal, %(illegal)d illegal, matches declared: %(matches_declared_matrix)s"
          % out["transition_matrix"])

    print("oracle run: %d events ..." % args.events)
    out["oracle_agreement"] = oracle_run(args.seed, args.events)
    print("  auths=%(auth_requests)d mismatches=%(oracle_mismatches)d wall=%(wall_seconds_engine_plus_oracle)ss"
          % out["oracle_agreement"])

    print("latency bench: %d events ..." % args.bench_events)
    out["latency"] = latency_bench(args.seed, args.bench_events)
    print("  p50=%(p50_us)sus p99=%(p99_us)sus mean=%(mean_us)sus decide/sec=%(auths_per_sec_from_summed_latency)d"
          % out["latency"])

    print("throughput run: %d events ..." % args.events)
    out["throughput"] = throughput_run(args.seed, args.events)
    print("  end-to-end %(end_to_end_events_per_sec)d events/sec" % out["throughput"])

    path = Path(args.out)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, indent=2) + "\n")
    print("wrote %s" % path)


if __name__ == "__main__":
    main()
