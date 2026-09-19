"""E-Tag card management for the isolated two-door controllers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import re
import urllib.error
import urllib.parse
import urllib.request

from askari_vms.etags import ETagRecord
from askari_vms.gate_controller import GateControllerError, read_credentials


_SLOT_RE = re.compile(r"EditCard\.shtm\?ID=(\d+)", re.IGNORECASE)
_INDEX_VALUE_RE = re.compile(r'name="Index"[^>]*value="(\d+)"', re.IGNORECASE)
_CARD_VALUE_RE = re.compile(r'name="Card"[^>]*value="([^"]*)"', re.IGNORECASE)
_ROW_RE = re.compile(
    r"<tr><th>\d+</th><th>.*?</th><th>(.*?)</th>.*?EditCard\.shtm\?ID=(\d+)",
    re.IGNORECASE | re.DOTALL,
)


@dataclass(frozen=True, slots=True)
class ControllerCard:
    slot: int
    rfid: str


@dataclass(frozen=True, slots=True)
class HttpETagController:
    key: str
    address: str
    port: int = 80
    timeout: float = 3.0
    max_pages: int = 200

    def _request(self, path: str, data: dict[str, object] | None = None) -> str:
        try:
            username, password = read_credentials(self.key, self.address)
        except Exception:
            raise GateControllerError("Controller credentials unavailable in Windows Credential Manager.") from None
        body = urllib.parse.urlencode(data).encode("ascii") if data is not None else None
        request = urllib.request.Request(
            f"http://{self.address}:{self.port}{path}", data=body,
            headers={"Content-Type": "application/x-www-form-urlencoded",
                     "User-Agent": "Mozilla/5.0", "Connection": "close"},
        )
        manager = urllib.request.HTTPPasswordMgrWithDefaultRealm()
        manager.add_password(None, f"http://{self.address}:{self.port}/", username, password)
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}), urllib.request.HTTPBasicAuthHandler(manager)
        )
        try:
            with opener.open(request, timeout=self.timeout) as response:
                return response.read(512_000).decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            raise GateControllerError(f"Controller rejected the card update (HTTP {exc.code}).") from None
        except Exception:
            raise GateControllerError("Controller unavailable. E-Tag update remains pending.") from None

    def find(self, rfid: str) -> ControllerCard | None:
        html = self._request("/SearchCard.shtm", {"Card": rfid})
        index = _INDEX_VALUE_RE.search(html)
        card = _CARD_VALUE_RE.search(html)
        if index and card and card.group(1).strip() == rfid:
            return ControllerCard(int(index.group(1)) - 1, rfid)
        return None

    def occupied_slots(self) -> set[int]:
        occupied: set[int] = set()
        for page in range(self.max_pages):
            html = self._request(f"/ShowCards.shtm?ID={page}")
            rows = _ROW_RE.findall(html)
            occupied.update(
                int(slot) for card, slot in rows if re.sub(r"<[^>]+>", "", card).strip() not in ("", "0")
            )
            if not rows or "Next" not in html:
                break
        return occupied

    def save(self, record: ETagRecord) -> ControllerCard:
        current = self.find(record.rfid)
        occupied = set() if current else self.occupied_slots()
        slot = current.slot if current else next(
            number for number in range(self.max_pages * 30) if number not in occupied
        )
        expiry = record.expiry_date or date.today()
        enabled = record.status.casefold() == "active"
        fields = {
            "Index": slot + 1,
            "Name": record.vehicle_number.replace(" ", "")[:8], "Card": record.rfid[:10], "PIN": "",
            "Year": 2000, "Month": 0, "Day": 0, "Hour": 0, "Minute": 0,
            "YearB": expiry.year, "MonthB": expiry.month, "DayB": expiry.day,
            "HourB": 23, "MinuteB": 59,
        }
        if enabled:
            fields.update({"isEnb": 1, "TZ1": 1})
        self._request("/EditCard.shtm", fields)
        verified = self.find(record.rfid)
        if verified is None:
            raise GateControllerError("Controller did not verify the E-Tag after writing it.")
        return verified

    def revoke(self, rfid: str) -> None:
        current = self.find(rfid)
        if current is None:
            return
        self._request("/EditCard.shtm", {
            "Index": current.slot + 1, "Name": "", "Card": rfid[:10], "PIN": "",
            "Year": 2000, "Month": 0, "Day": 0, "Hour": 0, "Minute": 0,
            "YearB": 2099, "MonthB": 12, "DayB": 31, "HourB": 23, "MinuteB": 59,
        })
