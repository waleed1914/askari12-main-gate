from askari_vms.users import (
    UserAccount,
    UserRole,
    count_active_admins,
    hash_password,
    validate_user,
    verify_password,
)


def test_user_normalization() -> None:
    user = UserAccount(" emp-1 ", "  Test  User ", " TEST ", UserRole.OPERATOR).normalized()
    assert user.employee_id == "EMP-1"
    assert user.full_name == "Test User"
    assert user.username == "test"


def test_new_user_requires_matching_password() -> None:
    account = UserAccount("E1", "Operator", "operator", UserRole.OPERATOR)
    assert "password" in validate_user(account, "123", "123")
    assert "confirmation" in validate_user(account, "123456", "different")
    assert not validate_user(account, "123456", "123456")


def test_missing_password_is_reported_for_new_accounts() -> None:
    account = UserAccount("E1", "Operator", "operator", UserRole.OPERATOR)
    assert "password" in validate_user(account, "", "")


def test_short_replacement_password_is_rejected_when_editing() -> None:
    account = UserAccount("E1", "Operator", "operator", UserRole.OPERATOR)
    # Blank means "keep the existing password" and stays valid.
    assert not validate_user(account, "", "", require_password=False)
    # Anything typed must still meet the length rule.
    assert "password" in validate_user(account, "123", "123", require_password=False)
    assert not validate_user(account, "123456", "123456", require_password=False)


def test_password_hashes_are_salted_and_verifiable() -> None:
    first = hash_password("secret123")
    second = hash_password("secret123")

    assert first != second, "each hash must use a fresh salt"
    assert "secret123" not in first
    assert first.startswith("pbkdf2_sha256$")
    assert verify_password("secret123", first)
    assert verify_password("secret123", second)
    assert not verify_password("secret124", first)


def test_verify_password_rejects_malformed_hashes() -> None:
    for stored in ("", "secret123", "pbkdf2_sha256$bad", "md5$1$00$00", "pbkdf2_sha256$1$zz$00"):
        assert not verify_password("secret123", stored)


def test_active_admins_are_counted_for_lockout_protection() -> None:
    accounts = [
        UserAccount("A1", "Admin One", "admin1", UserRole.ADMIN),
        UserAccount("A2", "Admin Two", "admin2", UserRole.ADMIN, status="Deactivated"),
        UserAccount("O1", "Operator", "operator", UserRole.OPERATOR),
    ]
    assert count_active_admins(accounts) == 1
    assert accounts[0].is_active_admin()
    assert not accounts[1].is_active_admin()
    assert not accounts[2].is_active_admin()
