"""Which portal an account reaches after signing in.

Two rules, in order:
  * the workstation decides which lane this PC is — Entry and Exit are pinned to their
    assigned machine, never chosen by the person signing in;
  * the account role decides whether they get the Admin portal or go straight to the
    operator screen for that lane.

So an Operator on the Entry PC lands on the Entry portal and never sees Settings, while
Admin on the same PC gets the Admin portal with the Entry portal one click away.
"""

from __future__ import annotations

from enum import StrEnum

from askari_vms.users import UserRole


class Portal(StrEnum):
    ADMIN = "Admin"
    ENTRY = "Entry"
    EXIT = "Exit"


def workstation_lane(workstation_role: str) -> Portal:
    """The lane this PC serves. Admin shares the Entry PC."""
    return Portal.EXIT if "Exit" in workstation_role else Portal.ENTRY


def portal_for(role: str, workstation_role: str) -> Portal:
    """Where this account lands on this machine."""
    lane = workstation_lane(workstation_role)
    if role in (UserRole.ADMIN, UserRole.REPORTER):
        return Portal.ADMIN
    return lane


def may_open_admin(role: str) -> bool:
    return role in (UserRole.ADMIN, UserRole.REPORTER)
