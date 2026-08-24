"""Token vault: provisioning, lifecycle state machine, cryptogram stub.

A token stands in for a card number (PAN) inside a specific usage domain:
it is bound to one device, and optionally to one merchant. The PAN itself
never leaves the vault through the public read path.

Lifecycle states: ACTIVE, SUSPENDED, DELETED.
Actions: suspend, resume, delete.

Transition matrix (9 state x action pairs, 4 legal, 5 illegal):

    state      suspend      resume       delete
    ACTIVE     SUSPENDED    illegal      DELETED
    SUSPENDED  illegal      ACTIVE       DELETED
    DELETED    illegal      illegal      illegal

DELETED is terminal. Any illegal pair raises IllegalTransition.

The "cryptogram" is a deliberate stub: sha256 over a per-token derived key,
the token id, and an application transaction counter (ATC). It gives me
something replay-checkable to validate without pretending to be real
payment cryptography.
"""

import hashlib

from .errors import IllegalTransition, ProvisionError, UnknownToken

ACTIVE = "ACTIVE"
SUSPENDED = "SUSPENDED"
DELETED = "DELETED"

STATES = (ACTIVE, SUSPENDED, DELETED)
ACTIONS = ("suspend", "resume", "delete")

# (current_state, action) -> next_state. Absent pairs are illegal.
TRANSITIONS = {
    (ACTIVE, "suspend"): SUSPENDED,
    (ACTIVE, "delete"): DELETED,
    (SUSPENDED, "resume"): ACTIVE,
    (SUSPENDED, "delete"): DELETED,
}

DEFAULT_SECRET = "tokengate-stub-master-secret"


def derive_key(master_secret, token):
    """Per-token key derivation for the cryptogram stub."""
    return hashlib.sha256(("key|" + master_secret + "|" + token).encode()).hexdigest()


def compute_cryptogram(key, token, atc):
    """Cryptogram stub: 16 hex chars over key, token id, and ATC."""
    msg = ("crypt|" + key + "|" + token + "|" + str(atc)).encode()
    return hashlib.sha256(msg).hexdigest()[:16]


class TokenView:
    """Public, PAN-free view of a token record."""

    __slots__ = ("token", "device_id", "merchant_id", "state")

    def __init__(self, token, device_id, merchant_id, state):
        self.token = token
        self.device_id = device_id
        self.merchant_id = merchant_id
        self.state = state


class TokenVault:
    """Holds token records and enforces the lifecycle transition matrix."""

    def __init__(self, master_secret=DEFAULT_SECRET):
        self._secret = master_secret
        self._records = {}
        self._count = 0

    def provision(self, pan, device_id, merchant_id=None):
        """Create a new ACTIVE token bound to a device (and optionally a merchant).

        Returns the token id. The token id is a hash and never embeds PAN digits.
        """
        if not isinstance(pan, str) or not pan.isdigit():
            raise ProvisionError("pan must be a digit string")
        if not (12 <= len(pan) <= 19):
            raise ProvisionError("pan length must be 12 to 19 digits")
        if not device_id:
            raise ProvisionError("device_id is required")
        self._count += 1
        raw = ("tok|" + self._secret + "|" + pan + "|" + str(self._count)).encode()
        token = "tok_" + hashlib.sha256(raw).hexdigest()[:16]
        self._records[token] = {
            "pan": pan,
            "device_id": device_id,
            "merchant_id": merchant_id,
            "state": ACTIVE,
            "key": derive_key(self._secret, token),
            "last_atc": 0,
        }
        return token

    def _rec(self, token):
        rec = self._records.get(token)
        if rec is None:
            raise UnknownToken(token)
        return rec

    def has(self, token):
        return token in self._records

    def state(self, token):
        return self._rec(token)["state"]

    def view(self, token):
        rec = self._rec(token)
        return TokenView(token, rec["device_id"], rec["merchant_id"], rec["state"])

    def detokenize(self, token):
        """Privileged path: map token back to PAN. Kept separate on purpose."""
        return self._rec(token)["pan"]

    def transition(self, token, action):
        """Apply a lifecycle action. Illegal pairs raise IllegalTransition."""
        rec = self._rec(token)
        nxt = TRANSITIONS.get((rec["state"], action))
        if nxt is None:
            raise IllegalTransition(
                "cannot %s a token in state %s" % (action, rec["state"])
            )
        rec["state"] = nxt
        return nxt

    def issue_cryptogram(self, token, atc):
        """Client-side helper: compute the cryptogram a device would present."""
        rec = self._rec(token)
        return compute_cryptogram(rec["key"], token, atc)

    def validate_cryptogram(self, token, atc, cryptogram):
        """Verify a presented cryptogram and enforce a strictly increasing ATC.

        On success the ATC watermark advances, so replaying the same ATC fails.
        On failure the watermark is untouched.
        """
        rec = self._rec(token)
        if not isinstance(atc, int) or atc <= rec["last_atc"]:
            return False
        if compute_cryptogram(rec["key"], token, atc) != cryptogram:
            return False
        rec["last_atc"] = atc
        return True
