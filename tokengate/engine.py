"""Authorization decision engine.

Rule evaluation order is fixed and documented. The first failing rule wins
and its reason code is returned. Order:

  1. TOKEN_UNKNOWN        token id not in the vault
  2. TOKEN_NOT_ACTIVE     token exists but is SUSPENDED or DELETED
  3. DEVICE_MISMATCH      request device differs from the token's bound device
  4. MERCHANT_MISMATCH    token is merchant-bound and the merchant differs
  5. BAD_CRYPTOGRAM       cryptogram invalid or ATC replayed
  6. MCC_BLOCKED          merchant category is on the card's blocklist
  7. GEO_BLOCKED          country is on the card's blocklist
  8. TXN_CAP_EXCEEDED     amount > per transaction cap
  9. DAILY_CAP_EXCEEDED   rolling 24h approved sum + amount > daily cap
 10. MONTHLY_CAP_EXCEEDED rolling 30d approved sum + amount > monthly cap
 11. APPROVED

Window semantics (timezone-free, pure epoch seconds):
  a prior approved transaction at time u counts toward the window of a
  request at time t iff t - u < window_seconds. Sums are exact integer
  cents. "At cap" (sum + amount == cap) approves; one cent over declines.
  Only APPROVED transactions consume velocity. The ATC watermark advances
  whenever the cryptogram check at step 5 passes, even if a later spend
  rule declines the transaction.

The engine keeps per-card deques with running sums so each decision is
amortized O(1). The oracle in oracle.py recomputes the same answers from
raw history with none of this bookkeeping.
"""

from collections import deque

DAY_SECONDS = 86400
MONTH_SECONDS = 30 * DAY_SECONDS

APPROVED = "APPROVED"
TOKEN_UNKNOWN = "TOKEN_UNKNOWN"
TOKEN_NOT_ACTIVE = "TOKEN_NOT_ACTIVE"
DEVICE_MISMATCH = "DEVICE_MISMATCH"
MERCHANT_MISMATCH = "MERCHANT_MISMATCH"
BAD_CRYPTOGRAM = "BAD_CRYPTOGRAM"
MCC_BLOCKED = "MCC_BLOCKED"
GEO_BLOCKED = "GEO_BLOCKED"
TXN_CAP_EXCEEDED = "TXN_CAP_EXCEEDED"
DAILY_CAP_EXCEEDED = "DAILY_CAP_EXCEEDED"
MONTHLY_CAP_EXCEEDED = "MONTHLY_CAP_EXCEEDED"

REASON_CODES = (
    APPROVED,
    TOKEN_UNKNOWN,
    TOKEN_NOT_ACTIVE,
    DEVICE_MISMATCH,
    MERCHANT_MISMATCH,
    BAD_CRYPTOGRAM,
    MCC_BLOCKED,
    GEO_BLOCKED,
    TXN_CAP_EXCEEDED,
    DAILY_CAP_EXCEEDED,
    MONTHLY_CAP_EXCEEDED,
)


class AuthorizationEngine:
    """Evaluates auth requests against token state and per-card spend controls."""

    def __init__(self, vault, controls_by_card):
        self._vault = vault
        self._controls = controls_by_card
        # card -> (daily deque, monthly deque, [daily_sum, monthly_sum])
        self._ledgers = {}

    def _ledger(self, card):
        led = self._ledgers.get(card)
        if led is None:
            led = (deque(), deque(), [0, 0])
        self._ledgers[card] = led
        return led

    def decide(self, token, amount_cents, mcc, country, device_id, merchant_id, ts, atc, cryptogram):
        """Return (approved: bool, reason_code: str) for one request."""
        if not isinstance(amount_cents, int) or isinstance(amount_cents, bool):
            raise TypeError("amount_cents must be an int (integer cents)")
        if amount_cents <= 0:
            raise ValueError("amount_cents must be positive")
        if not isinstance(ts, int):
            raise TypeError("ts must be epoch seconds as int")

        vault = self._vault
        if not vault.has(token):
            return (False, TOKEN_UNKNOWN)
        rec = vault._records[token]
        if rec["state"] != "ACTIVE":
            return (False, TOKEN_NOT_ACTIVE)
        if rec["device_id"] != device_id:
            return (False, DEVICE_MISMATCH)
        bound_merchant = rec["merchant_id"]
        if bound_merchant is not None and bound_merchant != merchant_id:
            return (False, MERCHANT_MISMATCH)
        if not vault.validate_cryptogram(token, atc, cryptogram):
            return (False, BAD_CRYPTOGRAM)

        card = rec["pan"]
        ctl = self._controls[card]
        if mcc in ctl.blocked_mccs:
            return (False, MCC_BLOCKED)
        if country in ctl.blocked_countries:
            return (False, GEO_BLOCKED)
        if amount_cents > ctl.per_txn_cap_cents:
            return (False, TXN_CAP_EXCEEDED)

        daily, monthly, sums = self._ledger(card)
        while daily and ts - daily[0][0] >= DAY_SECONDS:
            sums[0] -= daily.popleft()[1]
        while monthly and ts - monthly[0][0] >= MONTH_SECONDS:
            sums[1] -= monthly.popleft()[1]

        if sums[0] + amount_cents > ctl.daily_cap_cents:
            return (False, DAILY_CAP_EXCEEDED)
        if sums[1] + amount_cents > ctl.monthly_cap_cents:
            return (False, MONTHLY_CAP_EXCEEDED)

        entry = (ts, amount_cents)
        daily.append(entry)
        monthly.append(entry)
        sums[0] += amount_cents
        sums[1] += amount_cents
        return (True, APPROVED)
