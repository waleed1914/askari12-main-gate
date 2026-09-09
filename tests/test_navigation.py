from askari_vms.navigation import ADMIN_NAVIGATION, navigation_keys


def test_navigation_keys_are_unique() -> None:
    keys = navigation_keys()
    assert len(keys) == len(set(keys))


def test_required_admin_modules_are_present() -> None:
    assert navigation_keys() == (
        "dashboard",
        "vms",
        "categories",
        "etags",
        "users",
        "reports",
        "audit_logs",
        "settings",
        "etag_logs",
        "doors",
    )
    assert all(item.label.strip() for item in ADMIN_NAVIGATION)
