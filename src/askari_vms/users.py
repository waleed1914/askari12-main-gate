from __future__ import annotations

import hashlib
import hmac
import os
from dataclasses import dataclass, replace
from enum import StrEnum


class UserRole(StrEnum):
    ADMIN = "Admin"
    OPERATOR = "Operator"
    REPORTER = "Reporter"


MINIMUM_PASSWORD_LENGTH = 6

_ALGORITHM = "pbkdf2_sha256"
_ITERATIONS = 240_000


def hash_password(password: str, salt: bytes | None = None) -> str:
    """Derive a storable hash. The plain password is never kept or logged."""
    salt = salt if salt is not None else os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _ITERATIONS)
    return f"{_ALGORITHM}${_ITERATIONS}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    """Check a password against a stored hash, in constant time."""
    try:
        algorithm, iterations, salt, expected = stored.split("$")
        if algorithm != _ALGORITHM:
            return False
        digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), int(iterations))
    except ValueError:
        return False
    return hmac.compare_digest(digest.hex(), expected)


@dataclass(frozen=True, slots=True)
class UserAccount:
    employee_id: str
    full_name: str
    username: str
    role: str
    status: str = "Active"
    contact: str = ""
    cnic: str = ""
    email: str = ""
    department: str = "Security"
    # `role` is the permission level (the reference screen calls it Employee Type);
    # `designation` is the free-text job title it labels Role.
    designation: str = ""
    address: str = ""
    cnic_front: str = ""
    cnic_back: str = ""
    password_hash: str = ""

    def normalized(self) -> "UserAccount":
        return replace(
            self,
            employee_id=self.employee_id.strip().upper(),
            full_name=" ".join(self.full_name.split()),
            username=self.username.strip().lower(),
            cnic=self.cnic.strip(),
            email=self.email.strip().lower(),
            designation=" ".join(self.designation.split()),
            address=self.address.strip(),
        )

    def is_active_admin(self) -> bool:
        return self.role == UserRole.ADMIN and self.status == "Active"


def count_active_admins(accounts: list[UserAccount]) -> int:
    return sum(1 for account in accounts if account.is_active_admin())


def validate_user(account: UserAccount, password: str = "", confirmation: str = "", require_password: bool = True) -> dict[str, str]:
    errors: dict[str, str] = {}
    for key, label, value in (
        ("employee_id", "Employee ID", account.employee_id),
        ("full_name", "Full name", account.full_name),
        ("username", "Username", account.username),
    ):
        if not value.strip():
            errors[key] = f"{label} is required."
    if require_password and not password:
        errors["password"] = "Password is required."
    elif password and len(password) < MINIMUM_PASSWORD_LENGTH:
        # Applies when editing too: a short replacement password is still too short.
        errors["password"] = f"Password must contain at least {MINIMUM_PASSWORD_LENGTH} characters."
    if password and password != confirmation:
        errors["confirmation"] = "Password confirmation does not match."
    return errors
