from askari_vms.routing import Portal, may_open_admin, portal_for, workstation_lane
from askari_vms.users import UserRole


def test_the_workstation_decides_the_lane() -> None:
    assert workstation_lane("Admin + Entry") is Portal.ENTRY
    assert workstation_lane("Exit") is Portal.EXIT


def test_an_operator_lands_on_the_lane_for_their_pc() -> None:
    """An operator never chooses a portal: the machine they sign in on decides."""
    assert portal_for(UserRole.OPERATOR, "Admin + Entry") is Portal.ENTRY
    assert portal_for(UserRole.OPERATOR, "Exit") is Portal.EXIT


def test_admin_gets_the_admin_portal_on_either_pc() -> None:
    assert portal_for(UserRole.ADMIN, "Admin + Entry") is Portal.ADMIN
    assert portal_for(UserRole.ADMIN, "Exit") is Portal.ADMIN


def test_a_reporter_gets_the_admin_portal_not_a_gate() -> None:
    """Reporting is a desk job; a Reporter should not be handed a barrier."""
    assert portal_for(UserRole.REPORTER, "Admin + Entry") is Portal.ADMIN
    assert portal_for(UserRole.REPORTER, "Exit") is Portal.ADMIN


def test_only_admin_and_reporter_may_open_the_admin_portal() -> None:
    assert may_open_admin(UserRole.ADMIN)
    assert may_open_admin(UserRole.REPORTER)
    assert not may_open_admin(UserRole.OPERATOR)
