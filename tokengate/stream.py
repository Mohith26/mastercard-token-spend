"""Seeded synthetic world and event stream.

build_world(seed) provisions cards, controls, and tokens deterministically.
event_stream(world, seed, n) yields n events, each either

  ("L", token, action)                                lifecycle event, always legal
  ("A", token, amount, mcc, country, device, merchant, ts, atc, cryptogram)

The generator injects targeted noise so every decline path shows up in a
long run: wrong devices, wrong merchants, replayed ATCs, corrupted
cryptograms, unknown tokens, blocked MCCs, blocked countries, and amounts
above the per transaction cap. Timestamps are non-decreasing epoch seconds.
Same seeds give byte-identical streams.
"""

import random

from .controls import SpendControls
from .engine import AuthorizationEngine
from .tokens import TokenVault

START_TS = 1_700_000_000

BENIGN_MCCS = ["5411", "5812", "4111", "6011", "5541", "5732", "4899", "5912", "5999", "7011", "4511", "5813"]
RISKY_MCCS = ["7995", "5967", "7801", "6051"]
COMMON_COUNTRIES = ["US", "CA", "GB", "MX", "FR", "DE", "JP"]
RISKY_COUNTRIES = ["RU", "NG", "BR"]
MERCHANTS = ["m_%03d" % i for i in range(60)]


class World:
    __slots__ = ("vault", "engine", "controls", "tokens", "token_dir", "secret")

    def __init__(self, vault, engine, controls, tokens, token_dir, secret):
        self.vault = vault
        self.engine = engine
        self.controls = controls
        self.tokens = tokens
        self.token_dir = token_dir
        self.secret = secret


def build_world(seed, n_cards=4000):
    rng = random.Random(seed)
    secret = "tokengate-stub-master-secret"
    vault = TokenVault(secret)
    controls = {}
    tokens = []
    token_dir = {}
    for i in range(n_cards):
        pan = "5" + "".join(str(rng.randrange(10)) for _ in range(15))
        per_txn = rng.randrange(50_00, 300_00)
        if rng.random() < 0.10:
            daily = int(per_txn * 1.5)  # deliberately tight cards
        else:
            daily = per_txn * rng.randrange(2, 5)
        monthly = daily * rng.randrange(4, 10)
        controls[pan] = SpendControls(
            per_txn_cap_cents=per_txn,
            daily_cap_cents=daily,
            monthly_cap_cents=monthly,
            blocked_mccs=rng.sample(RISKY_MCCS, rng.randrange(0, 4)),
            blocked_countries=rng.sample(RISKY_COUNTRIES, rng.randrange(0, 3)),
        )
        for j in range(rng.randrange(1, 4)):
            device = "dev_%d_%d" % (i, j)
            merchant = rng.choice(MERCHANTS) if rng.random() < 0.5 else None
            tok = vault.provision(pan, device, merchant)
            tokens.append(tok)
            token_dir[tok] = (pan, device, merchant)
    engine = AuthorizationEngine(vault, controls)
    return World(vault, engine, controls, tokens, token_dir, secret)


def event_stream(world, seed, n_events):
    rng = random.Random(seed * 1_000_003 + 7)
    ts = START_TS
    issued = {}
    shadow = {t: "ACTIVE" for t in world.tokens}
    deleted = 0
    max_deleted = max(1, len(world.tokens) // 100)
    tokens = world.tokens
    token_dir = world.token_dir
    controls = world.controls
    vault = world.vault

    for _ in range(n_events):
        ts += rng.randrange(0, 21)
        r = rng.random()

        if r < 0.003:
            # lifecycle event, always legal for the token's current state
            tok = rng.choice(tokens)
            st = shadow[tok]
            if st == "ACTIVE":
                if deleted < max_deleted and rng.random() < 0.05:
                    action, nxt = "delete", "DELETED"
                    deleted += 1
                else:
                    action, nxt = "suspend", "SUSPENDED"
            elif st == "SUSPENDED":
                if deleted < max_deleted and rng.random() < 0.05:
                    action, nxt = "delete", "DELETED"
                    deleted += 1
                else:
                    action, nxt = "resume", "ACTIVE"
            else:
                # DELETED is terminal: emit an auth attempt against it instead
                yield _auth(rng, tok, token_dir, controls, vault, issued, ts)
                continue
            shadow[tok] = nxt
            yield ("L", tok, action)
            continue

        if r < 0.005:
            # unknown token probe
            yield ("A", "tok_unknown_%d" % rng.randrange(1000), 5_00, "5411", "US",
                   "dev_x", "m_000", ts, 1, "0" * 16)
            continue

        tok = rng.choice(tokens)
        yield _auth(rng, tok, token_dir, controls, vault, issued, ts)


def _auth(rng, tok, token_dir, controls, vault, issued, ts):
    pan, device, merchant = token_dir[tok]
    ctl = controls[pan]

    r = rng.random()
    if r < 0.005:
        device = "dev_evil"
    if 0.005 <= r < 0.010:
        merchant = "m_evil" if merchant is not None else rng.choice(MERCHANTS)
    elif merchant is None:
        merchant = rng.choice(MERCHANTS)

    if rng.random() < 0.03:
        mcc = rng.choice(RISKY_MCCS)
    else:
        mcc = rng.choice(BENIGN_MCCS)

    if rng.random() < 0.05:
        country = rng.choice(RISKY_COUNTRIES)
    else:
        country = rng.choice(COMMON_COUNTRIES) if rng.random() < 0.2 else "US"

    if rng.random() < 0.05:
        amount = rng.randrange(int(ctl.per_txn_cap_cents * 0.8), int(ctl.per_txn_cap_cents * 1.3))
    else:
        amount = rng.randrange(1_00, int(ctl.per_txn_cap_cents * 1.05))

    cur = issued.get(tok, 0)
    r2 = rng.random()
    if r2 < 0.004 and cur > 0:
        atc = rng.randrange(1, cur + 1)  # replay an already issued counter
        cryptogram = vault.issue_cryptogram(tok, atc)
    elif r2 < 0.007:
        atc = cur + 1
        issued[tok] = atc
        cryptogram = "deadbeefdeadbeef"  # corrupted
    else:
        atc = cur + 1
        issued[tok] = atc
        cryptogram = vault.issue_cryptogram(tok, atc)

    return ("A", tok, amount, mcc, country, device, merchant, ts, atc, cryptogram)


def run_events(world, events, oracle=None):
    """Drive engine (and optionally oracle) over events.

    Returns (n_auths, mismatches, reason_counts, decisions_digest_input).
    """
    engine = world.engine
    vault = world.vault
    reason_counts = {}
    mismatches = 0
    n_auths = 0
    for ev in events:
        if ev[0] == "L":
            vault.transition(ev[1], ev[2])
            if oracle is not None:
                oracle.apply_lifecycle(ev[1], ev[2])
            continue
        _, tok, amount, mcc, country, device, merchant, ts, atc, cryptogram = ev
        d = engine.decide(tok, amount, mcc, country, device, merchant, ts, atc, cryptogram)
        n_auths += 1
        reason_counts[d[1]] = reason_counts.get(d[1], 0) + 1
        if oracle is not None:
            o = oracle.decide(tok, amount, mcc, country, device, merchant, ts, atc, cryptogram)
            if o != d:
                mismatches += 1
    return n_auths, mismatches, reason_counts
