"""Per-card spend controls. All money values are integer cents."""


class SpendControls:
    """Spend controls attached to a funding card (PAN).

    per_txn_cap_cents: max amount for a single authorization.
    daily_cap_cents: max sum of approved amounts inside a rolling 24h window.
    monthly_cap_cents: max sum of approved amounts inside a rolling 30 day window.
    blocked_mccs: merchant category codes that always decline.
    blocked_countries: country codes that always decline.
    """

    __slots__ = (
        "per_txn_cap_cents",
        "daily_cap_cents",
        "monthly_cap_cents",
        "blocked_mccs",
        "blocked_countries",
    )

    def __init__(
        self,
        per_txn_cap_cents,
        daily_cap_cents,
        monthly_cap_cents,
        blocked_mccs=(),
        blocked_countries=(),
    ):
        for v in (per_txn_cap_cents, daily_cap_cents, monthly_cap_cents):
            if not isinstance(v, int) or v < 0:
                raise ValueError("caps must be non-negative integer cents")
        self.per_txn_cap_cents = per_txn_cap_cents
        self.daily_cap_cents = daily_cap_cents
        self.monthly_cap_cents = monthly_cap_cents
        self.blocked_mccs = frozenset(blocked_mccs)
        self.blocked_countries = frozenset(blocked_countries)
