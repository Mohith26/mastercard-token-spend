import pytest

from tokengate.controls import SpendControls
from tokengate.engine import AuthorizationEngine
from tokengate.tokens import TokenVault

PAN = "5100112233445566"
PAN2 = "5200998877665544"


@pytest.fixture
def world():
    """Small hand-built world: one vault, two cards, generous default controls."""
    vault = TokenVault()
    controls = {
        PAN: SpendControls(
            per_txn_cap_cents=100_00,
            daily_cap_cents=250_00,
            monthly_cap_cents=1000_00,
            blocked_mccs={"7995"},
            blocked_countries={"RU"},
        ),
        PAN2: SpendControls(
            per_txn_cap_cents=100_00,
            daily_cap_cents=250_00,
            monthly_cap_cents=1000_00,
        ),
    }
    engine = AuthorizationEngine(vault, controls)
    return vault, engine, controls


def auth(vault, engine, tok, amount, ts, atc, mcc="5411", country="US",
         device=None, merchant=None, cryptogram=None):
    """Helper that fills in the happy-path fields unless overridden."""
    rec = vault._records.get(tok)
    if device is None:
        device = rec["device_id"] if rec else "dev_1"
    if merchant is None and rec is not None:
        merchant = rec["merchant_id"]
    if cryptogram is None:
        cryptogram = vault.issue_cryptogram(tok, atc) if rec else "0" * 16
    return engine.decide(tok, amount, mcc, country, device, merchant, ts, atc, cryptogram)
