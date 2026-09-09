"""Operator sign-in.

Every audit event names an operator, so nothing is trustworthy until this exists.
Login and logout times are recorded, and operators may sign in and out freely.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from askari_vms.users import UserAccount, verify_password

# Deliberately identical for an unknown username and a wrong password, so the form
# cannot be used to discover which accounts exist.
BAD_CREDENTIALS = "Username or password is incorrect."
DEACTIVATED = "This account is deactivated. Ask an Admin to reactivate it."
MISSING_USERNAME = "Enter your username."
MISSING_PASSWORD = "Enter your password."


@dataclass(frozen=True, slots=True)
class Session:
    account: UserAccount
    workstation: str
    signed_in_at: datetime

    @property
    def operator(self) -> str:
        return self.account.username

    @property
    def display_name(self) -> str:
        return self.account.full_name or self.account.username

    @property
    def role(self) -> str:
        return self.account.role

    def describe(self) -> str:
        return f"{self.display_name} ({self.role}) on {self.workstation}"


def authenticate(
    accounts: Sequence[UserAccount], username: str, password: str
) -> tuple[UserAccount | None, str]:
    """Return the account, or None and a message explaining why not."""
    if not username.strip():
        return None, MISSING_USERNAME
    if not password:
        return None, MISSING_PASSWORD

    wanted = username.strip().casefold()
    account = next((a for a in accounts if a.username.casefold() == wanted), None)
    # Check the password before the status, so a wrong password never reveals that the
    # account exists at all.
    if account is None or not verify_password(password, account.password_hash):
        return None, BAD_CREDENTIALS
    if account.status != "Active":
        return None, DEACTIVATED
    return account, ""


def start_session(account: UserAccount, workstation: str, now: datetime | None = None) -> Session:
    return Session(account, workstation, now or datetime.now().replace(microsecond=0))
