import pytest

from tokengate.errors import IllegalTransition, ProvisionError, UnknownToken
from tokengate.tokens import ACTIONS, ACTIVE, DELETED, STATES, SUSPENDED, TRANSITIONS, TokenVault

PAN = "5100112233445566"


def make_token_in(vault, state):
    tok = vault.provision(PAN, "dev_1")
    if state == SUSPENDED:
        vault.transition(tok, "suspend")
    elif state == DELETED:
        vault.transition(tok, "delete")
    return tok


def test_provision_starts_active():
    v = TokenVault()
    tok = v.provision(PAN, "dev_1")
    assert v.state(tok) == ACTIVE


@pytest.mark.parametrize("state", STATES)
@pytest.mark.parametrize("action", ACTIONS)
def test_transition_matrix_exhaustive(state, action):
    """All 9 state x action pairs: legal ones land on the declared next state,
    illegal ones raise and leave the state untouched."""
    v = TokenVault()
    tok = make_token_in(v, state)
    expected = TRANSITIONS.get((state, action))
    if expected is None:
        with pytest.raises(IllegalTransition):
            v.transition(tok, action)
        assert v.state(tok) == state
    else:
        assert v.transition(tok, action) == expected
        assert v.state(tok) == expected


def test_suspend_resume_round_trip():
    v = TokenVault()
    tok = v.provision(PAN, "dev_1")
    v.transition(tok, "suspend")
    v.transition(tok, "resume")
    assert v.state(tok) == ACTIVE


def test_deleted_is_terminal_from_suspended():
    v = TokenVault()
    tok = make_token_in(v, SUSPENDED)
    v.transition(tok, "delete")
    for action in ACTIONS:
        with pytest.raises(IllegalTransition):
            v.transition(tok, action)


def test_unknown_action_is_illegal():
    v = TokenVault()
    tok = v.provision(PAN, "dev_1")
    with pytest.raises(IllegalTransition):
        v.transition(tok, "reactivate")


def test_unknown_token_raises():
    v = TokenVault()
    with pytest.raises(UnknownToken):
        v.transition("tok_nope", "suspend")
    with pytest.raises(UnknownToken):
        v.state("tok_nope")


@pytest.mark.parametrize("bad_pan", ["12345", "5" * 20, "51001122abc45566", ""])
def test_provision_rejects_bad_pan(bad_pan):
    v = TokenVault()
    with pytest.raises(ProvisionError):
        v.provision(bad_pan, "dev_1")


def test_provision_requires_device():
    v = TokenVault()
    with pytest.raises(ProvisionError):
        v.provision(PAN, "")


def test_token_id_does_not_leak_pan():
    v = TokenVault()
    tok = v.provision(PAN, "dev_1")
    assert PAN not in tok
    assert PAN[-8:] not in tok


def test_same_pan_gets_distinct_tokens():
    v = TokenVault()
    t1 = v.provision(PAN, "dev_1")
    t2 = v.provision(PAN, "dev_2")
    assert t1 != t2
    assert v.detokenize(t1) == v.detokenize(t2) == PAN


def test_view_has_no_pan_field():
    v = TokenVault()
    tok = v.provision(PAN, "dev_1", "m_001")
    view = v.view(tok)
    assert view.token == tok
    assert view.device_id == "dev_1"
    assert view.merchant_id == "m_001"
    assert view.state == ACTIVE
    assert not hasattr(view, "pan")
    with pytest.raises(AttributeError):
        view.pan = PAN  # slots: cannot even attach one


def test_detokenize_is_the_only_pan_path():
    v = TokenVault()
    tok = v.provision(PAN, "dev_1")
    assert v.detokenize(tok) == PAN
