from __future__ import annotations

from dataclasses import dataclass, replace


@dataclass(frozen=True, slots=True)
class VehicleCategory:
    name: str
    shortcut: str
    description: str = ""
    price: float = 0.0
    lost_receipt_price: float = 0.0
    active: bool = True

    def normalized(self) -> "VehicleCategory":
        return replace(
            self,
            name=" ".join(self.name.split()),
            shortcut=self.shortcut.strip().upper(),
            description=self.description.strip(),
        )


ALLOWED_SHORTCUTS = tuple(f"F{number}" for number in range(1, 13))


def validate_category(category: VehicleCategory) -> dict[str, str]:
    errors: dict[str, str] = {}
    if not category.name.strip():
        errors["name"] = "Category name is required."
    if category.shortcut.strip().upper() not in ALLOWED_SHORTCUTS:
        errors["shortcut"] = "Choose a shortcut from F1 to F12."
    if category.price < 0 or category.lost_receipt_price < 0:
        errors["price"] = "Amounts cannot be negative."
    return errors



def default_categories() -> list[VehicleCategory]:
    """Seeded so the Entry portal has shortcut keys on a fresh install."""
    return [
        VehicleCategory("Car", "F1", "Private car or SUV"),
        VehicleCategory("Truck", "F2", "Commercial or delivery truck"),
        VehicleCategory("Motorcycle", "F3", "Two-wheel vehicle"),
        VehicleCategory("Delivery Van", "F4", "Courier or delivery van"),
        VehicleCategory("Water Tanker", "F5", "Water supply tanker"),
        VehicleCategory("Rickshaw", "F6", "Auto rickshaw"),
    ]


def by_shortcut(categories, shortcut: str) -> VehicleCategory | None:
    wanted = shortcut.strip().upper()
    return next((c for c in categories if c.active and c.shortcut == wanted), None)
