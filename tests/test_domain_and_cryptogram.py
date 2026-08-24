from tests.conftest import PAN, auth
from tokengate.oracle import ReferenceOracle
from tokengate.tokens import compute_cryptogram, derive_key

T0 = 1_700_000_000


def test_bound_device_enforced(world):
    vault, engine, _ = world
    tok = vault.provision(PAN, "dev_1")
    assert auth(vault, engine, tok, 1_00, T0, 1, device="dev_other") == (False, "DEVICE_MISMATCH")
    assert auth(vault, engine, tok, 1_00, T0, 2) == (True, "APPROVED")


def test_bound_merchant_enforced(world):
    vault, engine, _ = world
    tok = vault.provision(PAN, "dev_1", "m_001")
    assert auth(vault, engine, tok, 1_00, T0, 1, merchant="m_002") == (False, "MERCHANT_MISMATCH")
    assert auth(vault, engine, tok, 1_00, T0, 2, merchant="m_001") == (True, "APPROVED")


def test_unbound_merchant_accepts_any(world):
    vault, engine, _ = world
    tok = vault.provision(PAN, "dev_1", None)
    assert auth(vault, engine, tok, 1_00, T0, 1, merchant="m_007") == (True, "APPROVED")
    assert auth(vault, engine, tok, 1_00, T0 + 1, 2, merchant="m_042") == (True, "APPROVED")


def test_device_checked_before_merchant(world):
    vault, engine, _ = world
    tok = vault.provision(PAN, "dev_1", "m_001")
    got = auth(vault, engine, tok, 1_00, T0, 1, device="dev_bad", merchant="m_bad")
    assert got == (False, "DEVICE_MISMATCH")


def test_suspended_token_declines_and_resumes(world):
    vault, engine, _ = world
    tok = vault.provision(PAN, "dev_1")
    vault.transition(tok, "suspend")
    assert auth(vault, engine, tok, 1_00, T0, 1) == (False, "TOKEN_NOT_ACTIVE")
    vault.transition(tok, "resume")
    assert auth(vault, engine, tok, 1_00, T0, 1) == (True, "APPROVED")


def test_deleted_token_declines(world):
    vault, engine, _ = world
    tok = vault.provision(PAN, "dev_1")
    vault.transition(tok, "delete")
    assert auth(vault, engine, tok, 1_00, T0, 1) == (False, "TOKEN_NOT_ACTIVE")


def test_unknown_token_declines(world):
    vault, engine, _ = world
    assert auth(vault, engine, "tok_ghost", 1_00, T0, 1) == (False, "TOKEN_UNKNOWN")


def test_valid_cryptogram_approves(world):
    vault, engine, _ = world
    tok = vault.provision(PAN, "dev_1")
    c = vault.issue_cryptogram(tok, 1)
    assert auth(vault, engine, tok, 1_00, T0, 1, cryptogram=c) == (True, "APPROVED")


def test_wrong_cryptogram_declines(world):
    vault, engine, _ = world
    tok = vault.provision(PAN, "dev_1")
    got = auth(vault, engine, tok, 1_00, T0, 1, cryptogram="f" * 16)
    assert got == (False, "BAD_CRYPTOGRAM")


def test_replayed_atc_declines(world):
    vault, engine, _ = world
    tok = vault.provision(PAN, "dev_1")
    assert auth(vault, engine, tok, 1_00, T0, 5) == (True, "APPROVED")
    got = auth(vault, engine, tok, 1_00, T0 + 10, 5)
    assert got == (False, "BAD_CRYPTOGRAM")
    assert auth(vault, engine, tok, 1_00, T0 + 20, 6) == (True, "APPROVED")


def test_atc_must_strictly_increase(world):
    vault, engine, _ = world
    tok = vault.provision(PAN, "dev_1")
    assert auth(vault, engine, tok, 1_00, T0, 10) == (True, "APPROVED")
    assert auth(vault, engine, tok, 1_00, T0 + 1, 3) == (False, "BAD_CRYPTOGRAM")
    assert auth(vault, engine, tok, 1_00, T0 + 2, 11) == (True, "APPROVED")


def test_watermark_untouched_by_pre_cryptogram_decline(world):
    """A decline at the token-state step must not burn the ATC."""
    vault, engine, _ = world
    tok = vault.provision(PAN, "dev_1")
    vault.transition(tok, "suspend")
    assert auth(vault, engine, tok, 1_00, T0, 1) == (False, "TOKEN_NOT_ACTIVE")
    vault.transition(tok, "resume")
    assert auth(vault, engine, tok, 1_00, T0 + 1, 1) == (True, "APPROVED")


def test_watermark_advances_even_on_spend_decline(world):
    """A cryptogram that validates burns its ATC even if a cap declines later."""
    vault, engine, _ = world
    tok = vault.provision(PAN, "dev_1")
    assert auth(vault, engine, tok, 200_00, T0, 1) == (False, "TXN_CAP_EXCEEDED")
    assert auth(vault, engine, tok, 1_00, T0 + 1, 1) == (False, "BAD_CRYPTOGRAM")


def test_oracle_cryptogram_derivation_matches_vault(world):
    vault, _, controls = world
    tok = vault.provision(PAN, "dev_1")
    oracle = ReferenceOracle({tok: (PAN, "dev_1", None)}, controls, vault._secret)
    assert oracle._crypto(tok, 7) == vault.issue_cryptogram(tok, 7)
    key = derive_key(vault._secret, tok)
    assert compute_cryptogram(key, tok, 7) == vault.issue_cryptogram(tok, 7)
