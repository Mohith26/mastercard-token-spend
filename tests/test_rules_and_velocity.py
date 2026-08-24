import pytest

from tests.conftest import PAN, PAN2, auth
from tokengate.controls import SpendControls
from tokengate.engine import DAY_SECONDS, MONTH_SECONDS, AuthorizationEngine
from tokengate.tokens import TokenVault

T0 = 1_700_000_003  # deliberately not midnight-aligned: windows are pure epoch math


def test_mcc_blocked(world):
    vault, engine, _ = world
    tok = vault.provision(PAN, "dev_1")
    assert auth(vault, engine, tok, 1_00, T0, 1, mcc="7995") == (False, "MCC_BLOCKED")


def test_geo_blocked(world):
    vault, engine, _ = world
    tok = vault.provision(PAN, "dev_1")
    assert auth(vault, engine, tok, 1_00, T0, 1, country="RU") == (False, "GEO_BLOCKED")


def test_mcc_checked_before_geo(world):
    vault, engine, _ = world
    tok = vault.provision(PAN, "dev_1")
    got = auth(vault, engine, tok, 1_00, T0, 1, mcc="7995", country="RU")
    assert got == (False, "MCC_BLOCKED")


def test_txn_cap_checked_before_daily(world):
    vault, engine, _ = world
    tok = vault.provision(PAN, "dev_1")
    # 200_00 violates both the 100_00 per-txn cap and would violate daily later
    assert auth(vault, engine, tok, 200_00, T0, 1) == (False, "TXN_CAP_EXCEEDED")


def test_per_txn_cap_boundary(world):
    vault, engine, _ = world
    tok = vault.provision(PAN, "dev_1")
    assert auth(vault, engine, tok, 100_00, T0, 1) == (True, "APPROVED")
    assert auth(vault, engine, tok, 100_01, T0 + 1, 2) == (False, "TXN_CAP_EXCEEDED")


def test_daily_cap_at_cap_and_one_cent_over(world):
    vault, engine, _ = world
    tok = vault.provision(PAN, "dev_1")
    assert auth(vault, engine, tok, 100_00, T0, 1) == (True, "APPROVED")
    assert auth(vault, engine, tok, 100_00, T0 + 10, 2) == (True, "APPROVED")
    # exactly at the 250_00 daily cap: approve
    assert auth(vault, engine, tok, 50_00, T0 + 20, 3) == (True, "APPROVED")
    # one cent over: decline
    assert auth(vault, engine, tok, 1, T0 + 30, 4) == (False, "DAILY_CAP_EXCEEDED")


def test_daily_window_boundary_exact_second(world):
    vault, engine, _ = world
    tok = vault.provision(PAN, "dev_1")
    assert auth(vault, engine, tok, 100_00, T0, 1) == (True, "APPROVED")
    assert auth(vault, engine, tok, 100_00, T0 + 5, 2) == (True, "APPROVED")
    assert auth(vault, engine, tok, 100_00, T0 + 10, 3) == (False, "DAILY_CAP_EXCEEDED")
    # at T0 + DAY_SECONDS - 1 the first txn is still inside the window
    assert auth(vault, engine, tok, 100_00, T0 + DAY_SECONDS - 1, 4) == (False, "DAILY_CAP_EXCEEDED")
    # at exactly T0 + DAY_SECONDS the first txn ages out (age >= 86400)
    assert auth(vault, engine, tok, 100_00, T0 + DAY_SECONDS, 5) == (True, "APPROVED")


def test_monthly_cap_and_window_boundary():
    vault = TokenVault()
    controls = {PAN: SpendControls(100_00, 100_00, 150_00)}
    engine = AuthorizationEngine(vault, controls)
    tok = vault.provision(PAN, "dev_1")
    assert auth(vault, engine, tok, 100_00, T0, 1) == (True, "APPROVED")
    # next day: daily window clear, but monthly (150_00) only has 50_00 left
    t1 = T0 + DAY_SECONDS
    assert auth(vault, engine, tok, 50_01, t1, 2) == (False, "MONTHLY_CAP_EXCEEDED")
    assert auth(vault, engine, tok, 50_00, t1 + 1, 3) == (True, "APPROVED")
    # first txn ages out of the 30 day window at exactly T0 + MONTH_SECONDS
    assert auth(vault, engine, tok, 100_00, T0 + MONTH_SECONDS - 1, 4) == (False, "MONTHLY_CAP_EXCEEDED")
    assert auth(vault, engine, tok, 100_00, T0 + MONTH_SECONDS, 5) == (True, "APPROVED")


def test_daily_checked_before_monthly():
    vault = TokenVault()
    controls = {PAN: SpendControls(100_00, 100_00, 100_00)}
    engine = AuthorizationEngine(vault, controls)
    tok = vault.provision(PAN, "dev_1")
    assert auth(vault, engine, tok, 100_00, T0, 1) == (True, "APPROVED")
    # violates both daily and monthly: daily's code must win
    assert auth(vault, engine, tok, 1_00, T0 + 1, 2) == (False, "DAILY_CAP_EXCEEDED")


def test_declined_txns_do_not_consume_velocity(world):
    vault, engine, _ = world
    tok = vault.provision(PAN, "dev_1")
    for i in range(5):
        assert auth(vault, engine, tok, 200_00, T0 + i, i + 1)[1] == "TXN_CAP_EXCEEDED"
    # nothing approved yet, so the full daily cap is available
    assert auth(vault, engine, tok, 100_00, T0 + 10, 6) == (True, "APPROVED")
    assert auth(vault, engine, tok, 100_00, T0 + 11, 7) == (True, "APPROVED")
    assert auth(vault, engine, tok, 50_00, T0 + 12, 8) == (True, "APPROVED")


def test_velocity_shared_across_tokens_of_same_card(world):
    vault, engine, _ = world
    t1 = vault.provision(PAN, "dev_1")
    t2 = vault.provision(PAN, "dev_2")
    assert auth(vault, engine, t1, 100_00, T0, 1) == (True, "APPROVED")
    assert auth(vault, engine, t2, 100_00, T0 + 1, 1) == (True, "APPROVED")
    assert auth(vault, engine, t2, 51_00, T0 + 2, 2) == (False, "DAILY_CAP_EXCEEDED")


def test_velocity_isolated_between_cards(world):
    vault, engine, _ = world
    t1 = vault.provision(PAN, "dev_1")
    t2 = vault.provision(PAN2, "dev_9")
    assert auth(vault, engine, t1, 100_00, T0, 1) == (True, "APPROVED")
    assert auth(vault, engine, t1, 100_00, T0 + 1, 2) == (True, "APPROVED")
    assert auth(vault, engine, t1, 51_00, T0 + 2, 3) == (False, "DAILY_CAP_EXCEEDED")
    # the other card is untouched
    assert auth(vault, engine, t2, 100_00, T0 + 3, 1) == (True, "APPROVED")


def test_window_math_is_timezone_free(world):
    """Same relative offsets give the same answers regardless of absolute epoch."""
    for base in (T0, T0 + 7 * 3600 + 123, 1_800_000_777):
        vault = TokenVault()
        controls = {PAN: SpendControls(100_00, 250_00, 1000_00)}
        engine = AuthorizationEngine(vault, controls)
        tok = vault.provision(PAN, "dev_1")
        decisions = [
            auth(vault, engine, tok, 100_00, base, 1),
            auth(vault, engine, tok, 100_00, base + 5, 2),
            auth(vault, engine, tok, 100_00, base + 6, 3),
            auth(vault, engine, tok, 100_00, base + DAY_SECONDS, 4),
        ]
        assert [d[1] for d in decisions] == [
            "APPROVED", "APPROVED", "DAILY_CAP_EXCEEDED", "APPROVED",
        ]


@pytest.mark.parametrize("bad", [10.5, "100", None, True])
def test_amount_must_be_int_cents(world, bad):
    vault, engine, _ = world
    tok = vault.provision(PAN, "dev_1")
    with pytest.raises(TypeError):
        engine.decide(tok, bad, "5411", "US", "dev_1", None, T0, 1, "0" * 16)


def test_amount_must_be_positive(world):
    vault, engine, _ = world
    tok = vault.provision(PAN, "dev_1")
    with pytest.raises(ValueError):
        engine.decide(tok, 0, "5411", "US", "dev_1", None, T0, 1, "0" * 16)
    with pytest.raises(ValueError):
        engine.decide(tok, -5, "5411", "US", "dev_1", None, T0, 1, "0" * 16)


def test_ts_must_be_int(world):
    vault, engine, _ = world
    tok = vault.provision(PAN, "dev_1")
    with pytest.raises(TypeError):
        engine.decide(tok, 1_00, "5411", "US", "dev_1", None, float(T0), 1, "0" * 16)


def test_controls_reject_bad_caps():
    with pytest.raises(ValueError):
        SpendControls(-1, 10, 10)
    with pytest.raises(ValueError):
        SpendControls(10, 10.5, 10)
