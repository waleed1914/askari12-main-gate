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
        match = _SLOT_RE.search(html)
        return ControllerCard(int(match.group(1)), rfid) if match else None

    def occupied_slots(self) -> set[int]:
        occupied: set[int] = set()
        for page in range(self.max_pages):
            html = self._request(f"/ShowCards.shtm?ID={page}")
            found = {int(value) for value in _SLOT_RE.findall(html)}
            occupied.update(found)
            if not found or "Next" not in html:
                break
        return occupied

    def save(self, record: ETagRecord) -> ControllerCard:
        current = self.find(record.rfid)
        occupied = set() if current else self.occupied_slots()
        slot = current.slot if current else next(
            number for number in range(self.max_pages * 30) if number not in occupied
        )
        issue = record.issue_date or date.today()
        expiry = record.expiry_date or date.today()
        enabled = record.status.casefold() == "active"
        self._request("/EditCard.shtm", {
            "Index": slot + 1, "isEnb": 1 if enabled else 0,
            "Name": record.vehicle_number.replace(" ", "")[:8], "Card": record.rfid[:10], "PIN": "",
            "YearB": issue.year, "MonthB": issue.month, "DayB": issue.day,
            "HourB": 0, "MinuteB": 0,
            "YearE": expiry.year, "MonthE": expiry.month, "DayE": expiry.day,
            "HourE": 23, "MinuteE": 59,
            "TZ1": 1 if enabled else 0, "TZ17": 0,
        })
        verified = self.find(record.rfid)
        if verified is None:
            raise GateControllerError("Controller did not verify the E-Tag after writing it.")
        return verified

    def revoke(self, rfid: str) -> None:
        current = self.find(rfid)
        if current is None:
            return
        self._request("/EditCard.shtm", {
            "Index": current.slot + 1, "isEnb": 0, "Name": "", "Card": rfid[:10],
            "PIN": "", "YearB": 2099, "MonthB": 12, "DayB": 31,
            "HourB": 23, "MinuteB": 59, "TZ1": 0, "TZ17": 0,
        })
