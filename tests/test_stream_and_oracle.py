from tokengate.oracle import ReferenceOracle
from tokengate.stream import build_world, event_stream, run_events

SEED = 42
SMALL = 30_000


def test_build_world_is_deterministic():
    w1 = build_world(SEED, n_cards=50)
    w2 = build_world(SEED, n_cards=50)
    assert w1.tokens == w2.tokens
    assert set(w1.token_dir) == set(w2.token_dir)
    for tok in w1.tokens:
        assert w1.token_dir[tok] == w2.token_dir[tok]


def test_different_seeds_differ():
    w1 = build_world(1, n_cards=20)
    w2 = build_world(2, n_cards=20)
    assert w1.tokens != w2.tokens


def test_event_stream_is_deterministic():
    w1 = build_world(SEED, n_cards=50)
    e1 = list(event_stream(w1, SEED, 2000))
    w2 = build_world(SEED, n_cards=50)
    e2 = list(event_stream(w2, SEED, 2000))
    assert e1 == e2


def test_events_are_well_formed():
    w = build_world(SEED, n_cards=50)
    saw_auth = saw_life = False
    last_ts = 0
    for ev in event_stream(w, SEED, 5000):
        if ev[0] == "L":
            saw_life = True
            assert ev[2] in ("suspend", "resume", "delete")
        else:
            saw_auth = True
            _, tok, amount, mcc, country, device, merchant, ts, atc, cg = ev
            assert isinstance(amount, int) and amount > 0
            assert isinstance(ts, int) and ts >= last_ts
            last_ts = ts
            assert isinstance(atc, int) and atc >= 1
            assert isinstance(cg, str) and len(cg) == 16
    assert saw_auth and saw_life


def test_oracle_agreement_small_stream():
    """30k events through engine and oracle in lockstep: zero mismatches."""
    world = build_world(SEED)
    oracle = ReferenceOracle(world.token_dir, world.controls, world.secret)
    n_auths, mismatches, reasons = run_events(
        world, event_stream(world, SEED, SMALL), oracle
    )
    assert mismatches == 0
    assert n_auths > SMALL * 0.99
    assert reasons.get("APPROVED", 0) > 0
    # the noise injection should surface a healthy variety of decline paths
    assert len(reasons) >= 8


def test_engine_decisions_deterministic_across_runs():
    runs = []
    for _ in range(2):
        world = build_world(SEED, n_cards=100)
        _, _, reasons = run_events(world, event_stream(world, SEED, 5000), None)
        runs.append(reasons)
    assert runs[0] == runs[1]


def test_oracle_rejects_illegal_lifecycle():
    world = build_world(SEED, n_cards=5)
    oracle = ReferenceOracle(world.token_dir, world.controls, world.secret)
    tok = world.tokens[0]
    try:
        oracle.apply_lifecycle(tok, "resume")  # ACTIVE cannot resume
        assert False, "expected ValueError"
    except ValueError:
        pass
