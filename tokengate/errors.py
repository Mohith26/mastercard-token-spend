"""Exception types for the token vault."""


class TokenError(Exception):
    """Base class for vault errors."""


class UnknownToken(TokenError):
    """Raised when a token id is not present in the vault."""


class IllegalTransition(TokenError):
    """Raised when a lifecycle action is not legal from the current state."""


class ProvisionError(TokenError):
    """Raised when provisioning input is invalid."""
