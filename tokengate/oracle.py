"""Reference oracle: independent naive recomputation of every decision.

This module deliberately shares no runtime state and no bookkeeping code
with the engine. It is handed the same static configuration the engine
gets (token directory, per-card controls, master secret) and then tracks
everything itself:

  * token lifecycle state, via its own hand-written if/else transition rules
  * ATC watermarks, via its own dict
  * cryptograms, recomputed from scratch with sha256 (same published stub
    formula, reimplemented here rather than imported)
  * velocity, by scanning the raw per-card approved history list backwards
    for every single decision, summing integer cents inside the window

No deques, no running sums, no eviction. If the engine's incremental
bookkeeping ever drifts from the spec, this disagrees and the harness
reports a mismatch.
"""

import hashlib

DAY = 86400
MONTH = 30 * 86400


class ReferenceOracle:
    def __init__(self, token_directory, controls_by_card, master_secret):
        # token -> (pan, device_id, merchant_id)
        self._dir = dict(token_directory)
        self._controls = controls_by_card
        self._secret = master_secret
        self._state = {t: "ACTIVE" for t in self._dir}
        self._last_atc = {t: 0 for t in self._dir}
        self._history = {}  # pan -> list of (ts, amount), append order = time order

    def _crypto(self, token, atc):
        key = hashlib.sha256(("key|" + self._secret + "|" + token).encode()).hexdigest()
        msg = ("crypt|" + key + "|" + token + "|" + str(atc)).encode()
        return hashlib.sha256(msg).hexdigest()[:16]

    def apply_lifecycle(self, token, action):
        """Track lifecycle with independent hand-rolled rules."""
        st = self._state[token]
        if st == "ACTIVE" and action == "suspend":
            self._state[token] = "SUSPENDED"
        elif st == "ACTIVE" and action == "delete":
            self._state[token] = "DELETED"
        elif st == "SUSPENDED" and action == "resume":
            self._state[token] = "ACTIVE"
        elif st == "SUSPENDED" and action == "delete":
            self._state[token] = "DELETED"
        else:
            raise ValueError("illegal transition %s from %s" % (action, st))

    def decide(self, token, amount_cents, mcc, country, device_id, merchant_id, ts, atc, cryptogram):
        info = self._dir.get(token)
        if info is None:
            return (False, "TOKEN_UNKNOWN")
        if self._state[token] != "ACTIVE":
            return (False, "TOKEN_NOT_ACTIVE")
        pan, bound_device, bound_merchant = info
        if bound_device != device_id:
            return (False, "DEVICE_MISMATCH")
        if bound_merchant is not None and bound_merchant != merchant_id:
            return (False, "MERCHANT_MISMATCH")
        if not isinstance(atc, int) or atc <= self._last_atc[token]:
            return (False, "BAD_CRYPTOGRAM")
        if self._crypto(token, atc) != cryptogram:
            return (False, "BAD_CRYPTOGRAM")
        self._last_atc[token] = atc

        ctl = self._controls[pan]
        if mcc in ctl.blocked_mccs:
            return (False, "MCC_BLOCKED")
        if country in ctl.blocked_countries:
            return (False, "GEO_BLOCKED")
        if amount_cents > ctl.per_txn_cap_cents:
            return (False, "TXN_CAP_EXCEEDED")

        hist = self._history.get(pan)
        day_sum = 0
        month_sum = 0
        if hist:
            # naive backward scan over raw history until we leave the 30d window
            for i in range(len(hist) - 1, -1, -1):
                u, amt = hist[i]
                age = ts - u
                if age >= MONTH:
                    break
                month_sum += amt
                if age < DAY:
                    day_sum += amt
        if day_sum + amount_cents > ctl.daily_cap_cents:
            return (False, "DAILY_CAP_EXCEEDED")
        if month_sum + amount_cents > ctl.monthly_cap_cents:
            return (False, "MONTHLY_CAP_EXCEEDED")

        if hist is None:
            hist = []
            self._history[pan] = hist
        hist.append((ts, amount_cents))
        return (True, "APPROVED")
