from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class NavigationItem:
    key: str
    label: str
    glyph: str


ADMIN_NAVIGATION: tuple[NavigationItem, ...] = (
    NavigationItem("dashboard", "Dashboard", "DB"),
    NavigationItem("vms", "VMS Operations", "VM"),
    NavigationItem("categories", "Vehicle Categories", "VC"),
    NavigationItem("etags", "E-Tags", "ET"),
    NavigationItem("users", "Users", "US"),
    NavigationItem("reports", "Reports", "RP"),
    NavigationItem("audit_logs", "Audit Logs", "AL"),
    NavigationItem("settings", "Settings", "ST"),
    NavigationItem("etag_logs", "E-Tag Logs", "EL"),
    NavigationItem("doors", "Door Controls", "DC"),
)


def navigation_keys() -> tuple[str, ...]:
    return tuple(item.key for item in ADMIN_NAVIGATION)
