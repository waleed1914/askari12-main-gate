from datetime import datetime

import pytest

from askari_vms.auth import (
    BAD_CREDENTIALS,
    DEACTIVATED,
    MISSING_PASSWORD,
    MISSING_USERNAME,
    authenticate,
    start_session,
)
from askari_vms.users import UserAccount, UserRole, hash_password


def account(username="operator01", password="secret123", status="Active", role=UserRole.OPERATOR) -> UserAccount:
    return UserAccount(
        "OP-001", "Operator One", username, role, status=status,
        password_hash=hash_password(password),
    )


ACCOUNTS = [account(), account("admin", "adminpass1", role=UserRole.ADMIN)]


def test_a_correct_password_signs_in() -> None:
    found, problem = authenticate(ACCOUNTS, "operator01", "secret123")
    assert problem == ""
    assert found is not None and found.username == "operator01"


def test_username_matching_ignores_case_and_padding() -> None:
    for typed in ("OPERATOR01", "  operator01  ", "Operator01"):
        found, problem = authenticate(ACCOUNTS, typed, "secret123")
        assert found is not None, typed
        assert problem == ""


def test_a_wrong_password_is_refused() -> None:
    found, problem = authenticate(ACCOUNTS, "operator01", "wrong")
    assert found is None
    assert problem == BAD_CREDENTIALS


def test_an_unknown_user_gives_the_same_message_as_a_wrong_password() -> None:
    """Otherwise the form becomes a way to discover which accounts exist."""
    _, unknown = authenticate(ACCOUNTS, "nobody", "secret123")
    _, wrong = authenticate(ACCOUNTS, "operator01", "wrong")
    assert unknown == wrong == BAD_CREDENTIALS


def test_a_deactivated_account_cannot_sign_in_even_with_the_right_password() -> None:
    accounts = [account(status="Deactivated")]
    found, problem = authenticate(accounts, "operator01", "secret123")
    assert found is None
    assert problem == DEACTIVATED

    # A wrong password on a deactivated account still says nothing about it.
    _, problem = authenticate(accounts, "operator01", "wrong")
    assert problem == BAD_CREDENTIALS


def test_blank_fields_are_reported_separately() -> None:
    assert authenticate(ACCOUNTS, "", "secret123")[1] == MISSING_USERNAME
    assert authenticate(ACCOUNTS, "   ", "secret123")[1] == MISSING_USERNAME
    assert authenticate(ACCOUNTS, "operator01", "")[1] == MISSING_PASSWORD


def test_an_account_without_a_hash_cannot_sign_in() -> None:
    blank = UserAccount("X-1", "No Password", "nopass", UserRole.OPERATOR)
    assert authenticate([blank], "nopass", "")[1] == MISSING_PASSWORD
    assert authenticate([blank], "nopass", "anything")[1] == BAD_CREDENTIALS


def test_a_session_describes_who_is_working_where() -> None:
    when = datetime(2026, 9, 3, 8, 30, 0)
    session = start_session(ACCOUNTS[1], "Admin + Entry", when)
    assert session.operator == "admin"
    assert session.display_name == "Operator One"
    assert session.role == UserRole.ADMIN
    assert session.workstation == "Admin + Entry"
    assert session.signed_in_at == when
    assert "Admin + Entry" in session.describe()


def test_initial_accounts_allow_login_without_creating_gate_traffic(qapp):
    from askari_vms.storage import Store
    from askari_vms.settings import default_settings
    from askari_vms.ui.pages.login import LoginWindow

    store = Store()
    # Hardware configuration can exist before the first operator signs in.
    store.settings.save(default_settings())
    try:
        assert store.seed_accounts_if_empty()
        login = LoginWindow(store, workstation="Admin + Entry")
        login.username.setText("operator01")
        login.password.setText("change-me-123")
        session = login.sign_in()
        assert session is not None and session.role == UserRole.OPERATOR
        assert authenticate(store.users.list(), "admin", "change-me-123")[0] is not None
        assert store.visits.count() == store.etags.count() == store.etag_events.count() == 0
        assert store.audit.list()[0].target == "Initial user accounts"
        login.close()
    finally:
        store.close()


def test_initial_account_setup_never_resets_existing_users():
    from askari_vms.storage import Store

    store = Store()
    existing = account(password="my-new-password", status="Deactivated")
    try:
        store.users.save(existing)
        assert not store.seed_accounts_if_empty()
        assert store.users.list() == [existing]
        assert store.audit.count() == 0
    finally:
        store.close()
