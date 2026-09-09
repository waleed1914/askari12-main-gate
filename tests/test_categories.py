from askari_vms.categories import ALLOWED_SHORTCUTS, VehicleCategory, validate_category


def test_category_normalization() -> None:
    category = VehicleCategory("  Heavy   Truck ", "f4", " note ").normalized()
    assert category.name == "Heavy Truck"
    assert category.shortcut == "F4"
    assert category.description == "note"


def test_category_validation() -> None:
    assert ALLOWED_SHORTCUTS == tuple(f"F{number}" for number in range(1, 13))
    assert not validate_category(VehicleCategory("Car", "F1"))
    errors = validate_category(VehicleCategory("", "X", price=-1))
    assert {"name", "shortcut", "price"}.issubset(errors)
