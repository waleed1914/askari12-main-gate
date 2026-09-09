# Askari 12 Main Gate — VMS / E-Tag / Gate Control

Spec of record for this project. Read this before changing code.

Local-first Windows desktop software for a single housing-society main gate: visitor
management (VMS), resident e-tags, ANPR/IP cameras, and two door controllers.
Runs entirely on the LAN with no internet dependency.

Requirements were captured in a 62-question interview. This file is the only durable
copy — see [Provenance](#provenance).

---

## 1. Status

**Milestone 1 (done):** shared desktop foundation + Admin portal shell. All hardware
sits behind adapters that are not yet written; every device call is simulated.

Implemented: Vehicle Categories, E-Tags, Users, Audit Logs, Door Controls, Settings,
E-Tag Logs, VMS Operations, Reports, and the **Entry portal**. The Dashboard is still
the original stub.

**Persistence:** `storage.py` — one SQLite file under the Settings data folder
(`askari_vms.sqlite3`, WAL). `Store` exposes a repository per record type; pages take a
repository and write through on every mutation. A fresh database seeds itself from
`demo_data.py`. E-tags carry a unique index on `vehicle_number`, and upserts use
`ON CONFLICT(<pk>) DO UPDATE` rather than `INSERT OR REPLACE`, which would silently
delete the conflicting row.

**Authentication:** `auth.py` + `ui/pages/login.py`. The app opens on a sign-in screen.
Startup provisions initial accounts when the users table is empty, before showing
login (`Store.seed_accounts_if_empty`). This also works if camera settings already
exist. Existing accounts are never reset, and this step creates no demo traffic.
Nothing else is reachable until an **active** account authenticates. `AuditLog.set_operator()`
then stamps every event with the real operator and workstation, and Login/Logout are
themselves audited with the session times. An unknown username and a wrong password give
the identical message, so the form cannot be used to enumerate accounts; the password is
verified *before* the status is checked for the same reason.

⚠ **Seeded accounts all share the password `change-me-123`.** That is fine for a demo
database and unacceptable on the gate PC. Before this goes near the society, either
change every password through Users or add a forced change-on-first-login.

**Reports:** `reports.py` + `ui/pages/reports.py`. Visits and e-tag readings merge into
one timeline (`build_rows`), filtered by search, inclusive date range, door and category,
with totals, a grouped breakdown and CSV export. Exports are audited — data leaving the
system is worth a record.

**Portal routing:** `routing.py`. The workstation decides the lane (Entry and Exit are
pinned to their PC, never chosen by the person signing in); the account role then decides
Admin portal vs the operator screen. So `operator01` on the Entry PC lands straight on
the Entry portal and never sees Settings, while Admin on the same PC gets the Admin
portal with the Entry portal one click away. Reporter is a desk role and gets Admin, not
a barrier.

**CNIC OCR:** `cnic_ocr.py` — fully offline (`rapidocr` + ONNX, OpenCV capture), returns
per-field confidence and matches CNIC date labels to values *geometrically*, because OCR
reading order on a card is not reliable. `CNICReadError` is documented as recoverable and
must never prevent manual entry.

**Entry portal:** `ui/pages/entry_portal.py`, opened from the Admin topbar and shown
only when the workstation role includes Entry.
The 2026-09-09 layout update uses a compact shortcut strip, larger form text and
inputs, and side-by-side ID/driver previews that scale without cropping. The inactive
ANPR panel stays compact. Form and camera cards scroll if the window is too small;
do not reintroduce fixed preview sizes or overlapping capture panels. The event table
shows two measured rows at once, with scrolling for the remaining readings.
Keyboard-first: F1–F12 pick the category
(read from the persisted `categories` table), Ctrl+D holds to dictate the destination,
Ctrl+Enter submits, Ctrl+N starts the next visitor. Submitting writes the visit, records
a `Visitor decision` event and a separate `Gate command` event — the gate opens on print,
so it is its own audited action.

Field order matches the operator's existing screen exactly: Vehicle Number, Destination,
Full Name, Father/Husband Name, CNIC, DoB, CNIC Issue Date, CNIC Expiry Date, Contact,
Vehicle Type. **Focus returns to Destination after every submit** — ANPR fills the plate,
so the destination is the operator's first real action.

Nothing blocks the operator. Blank fields submit and are listed in the audit details;
missed cameras submit and are listed too; both raise the event to WARNING. A plate with
an open visit warns and still allows. A returning vehicle or CNIC is *offered* for reuse
and never auto-filled — photographs are always taken fresh.

**Not built yet:** the Dashboard (still four zeroed cards), Exit portal, and every
hardware adapter.

Stack: Python 3.12 + PySide6 (Qt6), `pytest`. Entry point `python -m askari_vms`.

**Working order (agreed 2026-09-02):** finish UI and application logic first; test each
piece of hardware standalone next; integrate last. So keep every device call behind its
adapter and keep pages driveable from plain Python — `ETagLogsPage.record_reading()` is
the shape to follow: the page never talks to a controller, it is handed a reading.

---

## 2. Hardware

### Door controllers — 2 × two-door "Web Access Control System"

| Controller | Door 1 | Door 2 |
|---|---|---|
| **Entry** | Visitor Entry | E-tag Entry |
| **Exit** | Visitor Exit | E-tag Exit |

These four names are exact and confirmed. Known unit: `192.168.0.90` (the second is
not yet configured). Board also exposes fire alarm, Exit1/2 buttons, Sensor1/2,
tamper and reset inputs, an RS-485 long-range reader, and MQTT.

### Cameras — visitor lanes only

**Entry ANPR update, 2026-09-09:** the user assigned `192.168.1.13` to Visitor Entry.
Device identity ITC413-PW4D-Z3 and authenticated event subscription were verified on
the LAN. `anpr.py` subscribes to Dahua `snapManager.cgi` TrafficJunction events, parses
length-delimited multipart records, and delivers recognized plates to
`EntryPortalWindow.read_plate()`. New plates fill Vehicle Number without taking focus,
submitting a visit or operating a gate. Repeated reads do not undo manual corrections;
old queued detections are discarded on Next visitor. Network failures reconnect with
backoff and leave manual entry available. Credentials remain in Windows Credential
Manager, scoped by the configured camera key and address.

Camera keys are durable identifiers, not lane assignments: `.13` retains its legacy
`anpr_exit` key but is assigned to Entry. `.12` is now Unassigned until its physical
lane is confirmed; do not assume it is Exit. The factory selects by role and lane.
The ANPR panel currently reports detection status; plate detection does not claim to
capture an overview or cropped-plate image. Integration was checked against the
[Dahua real-time subscription specification](https://files.dahua.support/Solutions/Access%20Control%20Solution/Integration/DAHUA%20ACCESS%20CONTROL%20PRODUCTS%20INTEGRATION%20INSTRUCTION%20Ver1.0.pdf).

**Entry driver update, 2026-09-09:** the device at `192.168.1.16` was queried directly
and identifies as **Hikvision DS-2CD1653G0-IZS**, firmware **V5.7.20**. It replaces the
earlier Dahua Entry driver assignment below. `ip_camera.py` reads authenticated JPEGs
from `/ISAPI/Streaming/channels/101/picture` over HTTP port 80, approximately twice per
second. This is a refreshing snapshot preview, not full-motion RTSP video. Network
I/O runs outside Qt, retries automatically, and never blocks submission. Credentials
are in Windows Credential Manager, scoped by camera key and address; never add them
to settings, source, logs or this file. Credentials must be provisioned separately on
another Windows account or PC.

Both Entry launch paths now show this preview. Capture (or Submit if not already
captured) saves a fresh driver image under `data/images/driver_entry/YYYY-MM-DD`.
Schema version 4 adds `VisitRecord.driver_image`; old records remain valid with an
empty path. Exit and VMS detail show the saved entry driver photograph. Clearing the
form prevents the preceding visitor's cached frame from being reused. Missing or
stale frames and image write failures flag missing evidence and still allow submit.
Entry ANPR detection is also integrated as described above. Other IP camera feeds,
printing and gate commands remain simulated.

Recovered from the ConfigTool scan. E-tag lanes have **no cameras**; the controller
already knows who the holder is, so only event data is needed there.

| Role | Model | IP | Firmware |
|---|---|---|---|
| ANPR (ITC) | `ITC413-PW4D-Z3` | 192.168.1.12 | V5.004 |
| ANPR (ITC) | `ITC413-PW4D-Z3` | 192.168.1.13 | V5.004 |
| Driver (IPC) | `DH-IPC-HFW2441…` | 192.168.1.14 | V2.840 |
| Driver (IPC) | `DH-IPC-HFW2441…` | 192.168.1.15 | V2.840 |

All on Dahua SDK port `37777`. Assign each to Visitor Entry / Visitor Exit in Settings.

### Peripherals

- **ID card camera** — plain USB webcam in a fixed box at Entry, captures the full frame.
  The feed **reconnects on its own**: `camera_link.py` holds the policy (pure, no Qt) and
  the entry portal runs it on a 500 ms watchdog. Loss is detected by *silence* as well as
  by errors, because the common USB failure emits no error at all — the camera still
  reports itself active while frames stop. Three seconds of silence, or ten with no first
  frame, counts as lost; retries back off 1→2→4→8→10s and then continue at 10s forever,
  since nobody restarts the app overnight. `QMediaDevices.videoInputsChanged` pulls the
  next retry forward, so a replug reconnects at once. Each attempt builds a **new**
  `QCamera` from a fresh enumeration: a handle to an unplugged device is dead and holding
  it can stop Windows handing the device back. The camera is matched by **description,
  not index**, because the device list reorders across a replug and index 0 can become
  the laptop's built-in webcam. A deliberate stop (during OCR, or on close) is never
  mistaken for a fault. Measured on the bench: a silently destroyed camera was detected
  and back live in **4.7 s**.
- **Barcode scanner** — **Honeywell MK7120 Orbit** (`MK7120-31A38`), USB
  omnidirectional presentation scanner, Exit only. The `-31` suffix is USB
  keyboard-wedge: it types the decoded barcode into whatever field has focus, so no
  driver and no adapter are needed. CODE39 is enabled by default, which is the symbology
  our receipts print. The Exit search box just has to keep focus and treat Enter as
  submit.
- **Receipt printer** — **Black Copper BC-85AC**, 80mm thermal with auto-cutter, Entry
  only (Exit has no printer and needs none). Enumerates as
  `STMicroelectronics POS80 Printer USB`, `VID_0416/PID_5011`, USB printer class
  (`Class_07/SubClass_01/Prot_02`, service `usbprint`) on port `USB001`.
  Standard ESC/POS. Windows needs a print queue bound to that port — installed here as
  **`Askari Receipt Printer`** using the built-in *Generic / Text Only* driver, which
  passes RAW bytes through untouched. Use the same queue name on the client PC and no
  code changes are needed; the name is the `receipt_printer` value in Settings.

### Brand

The sidebar emblem is `src/askari_vms/ui/assets/logo.jpeg` (Housing Directorate /
Askari Greens Lhr); `ui/brand.py` accepts any of png/jpeg/jpg/webp/bmp. The supplied
JPEG has no transparency, a square background and a black band along the top, so
`circular_logo()` trims uniformly dark borders, centre-crops to a square and masks to a
circle at 2x. Missing file falls back to an "A12" text mark.

The same emblem appears in the Entry and Exit portal headers at 40px, on the login card,
and as the **window icon**: `brand.app_icon()` builds a multi-size `QIcon` (16-256) that
`main.py` sets on the QApplication, so the title bar, taskbar and Alt-Tab all inherit it.

**The sidebar must fit the window's 700px minimum height.** A tall centred brand block
pushed its minimum to 801px, and Qt then overlapped the emblem and the title rather than
scrolling. Keep the brand compact; `test_sidebar_fits_the_minimum_window_height` guards
it. Plain `QWidget` containers inside the sidebar need an explicit transparent
background or they pick up the global widget colour.

### Workstations

Admin + Entry share one PC; Exit is a second PC on the same LAN switch. The Entry PC
doubles as the server and holds all data. Entry and Exit portals are **restricted to
their assigned workstation**. Which portal an operator gets is decided by *which PC
they log into*, never by their account.

---

## 3. Controller HTTP protocol

Reverse-engineered from `192.168.0.90`. Plain HTTP + **HTTP Basic Auth** — credentials
are sent unencrypted, so keep controllers on an isolated LAN and store the password in
the OS credential store, never in this repo.

**Gate commands** — authenticated GET, `door=0` is Door 1, `door=1` is Door 2:

```
GET /cdor.cgi?open={cmd}&door={0|1}
  cmd 1 = Open    cmd 0 = Close
  cmd 6 = Lock    cmd 7 = Unlock
```

These four map exactly to `DoorCommand` in `src/askari_vms/controllers.py`.

**Live events** — poll at 500 ms, passing the last seen id:

```
GET /GEvent.xml?ID={last_event_id}
-> ID, Time, Card, Name, Door, Reader, Note, OutPut, InPut, Lock
```

Carries card/tag, reader direction, door, event text and controller status — this is
the long-range reader feed. **Still to verify on real hardware:** that rejected and
unknown tags also appear here (we need them for the critical "unknown tag" event).

**Card management** — one card per numbered slot:

```
GET  /EditCard.shtm?ID={zero_based_slot}
POST /EditCard.shtm
     Index  1-based slot        isEnb  1 = enabled
     Name   max 8 chars         Card   max 10 digits
     PIN    max 8 chars
     YearB MonthB DayB HourB MinuteB   expiry
     TZ1    Door 1 time zone    TZ17   Door 2 time zone
POST /SearchCard.shtm   Card={number}
GET  /ShowCards.shtm?ID={page}
```

Add card = write an unused slot. Renew = rewrite the same slot with a new expiry.

MQTT (`/Mqtt.shtm`) is available and is probably the better long-term event
transport, but HTTP polling is sufficient for the first integration.

---

## 4. Portals

1. **Admin** — configuration, controllers, users, e-tags, renewals, reports, logs,
   cameras, manual gate control.
2. **Entry** — live ANPR + cameras, live RFID reads, visitor registration, entry gate.
3. **Exit** — live ANPR + cameras, match against the entry record, checkout, exit gate.

---

## 5. Visitor entry flow

1. Visitor hands over their ID card; operator places it in the camera box.
2. Operator **holds** a shortcut key and speaks the destination — English dictation
   types it into the Destination field. Destination is free text.
3. Operator presses the **category shortcut** (F1–F12, defined by Admin, e.g. F1 Car).
4. System captures driver image, ANPR overview, cropped plate, and the ID card image.
5. OCR reads the front of a Pakistani CNIC **or** driving licence — same workflow for
   both. Front only; the operator never flips the card. Must be fast and reliable.
   Extracted fields are shown for correction, with low-confidence ones highlighted.
6. ANPR auto-fills the plate but the operator still confirms before submitting.
7. Submit → receipt prints → **Visitor Entry gate opens automatically on print.**

Receipt carries a human-readable visit number *and* a random barcode token (random
per visitor, so verification is stronger). It also shows visitor name, CNIC, vehicle
number, destination, entry time, category and operator name.

Returning vehicle/CNIC: offer prior details as a **corner notification** the operator
can click to reuse — never auto-filled. Fresh photos are always taken.

Duplicate active visit for one vehicle: **warn, but allow.**

Operator may submit at any time with fields empty. Such records are flagged and
findable under a **"missing data" filter**, and record who submitted them.

VMS Operations (`ui/pages/vms_operations.py`, domain in `visits.py`) is the Admin view of
this. A visit is incomplete when any of visitor name, CNIC, vehicle number, destination
or category is blank — `missing_fields()` names exactly which. Rows are coloured green
(inside), amber (missing data), red (mismatched driver), plain (exited), with **Missing
data only** and **Mismatched only** filters. Clicking a row opens the full record, its
captured-evidence placeholder, and previous visits by the same vehicle.

**Manual checkout** closes a visit from Admin without exit evidence, so it is audited at
WARNING severity — it bypasses the Exit portal and must stay visible as an exception.

---

## 6. Visitor exit flow

1. ANPR reads continuously. A hit **automatically opens the active visitor profile.**
2. Fallbacks in order: barcode scan → manual search (ANPR result, vehicle number, or
   CNIC — all three are searchable). If ANPR misreads, the operator corrects the plate
   and searches again.
3. Profile shows the entry photos and full entry record.
4. Operator selects **Matched / Mismatched** — this refers to the **driver's face**.
   Mandatory before submit. Never automatic; there is no face matching.
5. A mismatch **warns but does not block**. Matched/Mismatched is for the record only.
6. Submit captures fresh exit photos (driver, ANPR overview, plate crop) and opens the
   Visitor Exit gate.

Lost receipt: operator selects "receipt lost", finds the visit manually, and still
must mark Matched/Mismatched. No reprinting at Exit.

No active entry found: show "No active entry" and allow manual search / manual open.

The operator can open the gate at any time.

---

## 7. E-tags

The **controller** opens e-tag gates by itself — access rules are programmed into it
at registration. Our software never operates the e-tag doors; it only consumes events.

Event colours: **blue** at 10 days from expiry, **red** once expired, **critical** when
the tag is unknown to the controller. Implemented in `etag_events.py` (`classify` reuses
`expiry_state`, so the registry stays the single source of truth) and rendered by
`ui/pages/etag_logs.py`. An unknown tag also raises a CRITICAL audit event, because the
controller should never have opened for it.

Log columns are fixed to match the reference system: **Sr # · ETag ID · Full Name ·
Entry Time · Door · Status · Action**. Vehicle number, controller and direction are not
columns — they are one click away in the profile, and vehicle number is still searchable.

Clicking any log row — or its Action button — opens `ui/pages/resident_profile.py`: the resident's details, every
tag they hold with its expiry state and days remaining, and their complete reading
history merged across all of those tags. An unrecognised tag has no resident, so the
page reports the tag itself and every time a controller read it.

Admin add/renew/block/delete must push to every selected controller and verify it
succeeded. If a controller is offline, **queue the change and sync on reconnect**, and
show per-tag/per-controller status (Synced / Pending / Failed).

### Employee form

Matches the reference "New Employee" screen: left card holds Employee ID, Employee Name,
Status, Mobile Number, Address, Email, Team/Department, Role, Employee Type, Username,
Password, Confirm Password; the right card holds CNIC with front/back image pickers and
the Shortcut Keys panel.

**Role vs Employee Type are two different things.** `UserAccount.role` is the permission
level (Admin/Operator/Reporter) shown as **Employee Type**; `UserAccount.designation` is
the free-text job title shown as **Role**. Do not merge them.

Shortcuts, matching the reference: Ctrl+1 Employee ID, Ctrl+2 Name, Ctrl+3 Mobile,
Ctrl+4 Email, Ctrl+5 Department, Ctrl+6 Role, Ctrl+7 CNIC, Ctrl+8 Password, Ctrl+9
Confirm Password, Ctrl+Enter submit. CNIC images are stored as file paths for now; they
get copied into the data folder when persistence lands.

### E-Tag form

The Add/Edit E-Tag form carries exactly the reference system's 21 fields — verified
field-by-field, with a test in `tests/test_ui_regressions.py` pinning the set. Allowed
Controllers is built from the configured controllers via `settings.controller_choices()`,
so each row shows its address and e-tag door (`Entry Controller — 192.168.0.90 (E-tag
Entry)`) and follows whatever Settings holds. Saving Settings updates the picker.

One resident may hold **multiple e-tags** — personal details may repeat across them,
but **the vehicle must differ**.

---

## 8. Cross-cutting policy

- **Offline-first.** Everything runs on the LAN; no internet.
- **Never block the operator.** If ANPR, OCR, printer, camera or controller fails,
  warn and let them continue manually. Notify on breakage.
- **Audit everything.** Every entry, exit, e-tag event and manual gate command records
  operator, workstation, action, target and timestamp. Manual opening needs **no
  reason**, but is still audited.
- **Individual operator accounts.** Login and logout times recorded; operators may sign
  in and out freely. No cash, shift totals or attendance handling.
- **Retention.** All data lives on the Entry PC. At **80% disk**, delete oldest images
  first. Database records and audit logs are kept permanently.
- **Backup.** Admin backs up database + configuration to USB.
- **Startup.** All services and the app launch automatically with Windows and recover
  unfinished visits and controller polling after a restart.
- **No emergency "open all gates" control.** (Reversed from an earlier answer — the
  final decision is no.)

---

## 9. UI conventions

Explicitly requested — do not regress these:

- **Pages, not popups.** Navigate within a `QStackedWidget`; avoid modal dialogs.
- **Keep the green theme. Never use a black/dark theme.**
- Checkbox borders must be clearly visible; all borders prominent but not too dark.
- Bulk action buttons appear **only when something is selected**.
- Multi-select with bulk delete, status change, and renew-for-one-year.
- Status toggle flips Active↔Deactivated — not a one-way set to Active.
- Shortcut keys already used by another category are **removed** from the dropdown.
- Styled dropdown chevron used consistently everywhere.
- No servant/maid module — deliberately dropped from the reference system.
- **Never hardcode a table's height.** Qt's header is ~38px, not the 46px it looks like;
  guessing leaves a dead strip above the bottom border that reads as a doubled line.
  Size from `horizontalHeader().sizeHint().height()` plus real row heights — see
  `ResidentProfilePage._fit_to_rows`, pinned by `_assert_no_dead_strip` in the tests.
- A `QTableWidget` cannot clip its viewport to `border-radius`, so the first and last
  header sections carry matching corner radii in `theme.py`.

---

## 10. Defects

Audited 2026-09-02.

### Fixed

All seven high-severity findings, each covered by a test in
`tests/test_ui_regressions.py`:

1. Combo prompts no longer save as data — `SELECT_VEHICLE_TYPE` / `SELECT_GENDER` map
   back to `""` in `ETagFormPage.value()`, so `validate_etag` reports the missing field.
2. New accounts default to **Operator**, not Admin. Admin is now deliberate.
3. Passwords are stored as salted PBKDF2-SHA256 (`hash_password` / `verify_password`,
   240k iterations) in `UserAccount.password_hash`. Plain passwords are never retained.
4. A typed password is length-checked on edit too; blank still means "keep existing".
5. Each vehicle may hold only one e-tag (`ETagsPage._duplicate_error`). One resident
   may still hold several tags on different vehicles, per §7.
6. The last active Admin cannot be deleted, deactivated, or edited out of the role
   (`UsersPage._locks_out_admins`).
7. Gate commands are audited. `AuditLog` in `audit.py` is a shared sink created once in
   `AdminWindow` and passed to both Door Controls and Audit Logs; the audit page
   live-updates via listener. Clearing the on-screen history leaves audit records intact.

### Open

- `UserRole` lacks `SECURITY` (reference system has Admin/Operator/Reporter/Security).
- ~~Audit wording vs in-memory storage~~ — resolved: audit events are now written to
  the database and the table is insert-only, so "Permanent record" is true.
- No CNIC / mobile / email format validation despite the placeholder hints.
- With all 12 shortcuts assigned, the category form offers an empty dropdown and then
  rejects with "Choose a shortcut from F1 to F12" — it should say none are free.
- No issue/expiry date sanity bounds; an issue date decades in the future validates.
- `callable` used as a type annotation instead of `Callable` in `door_controls.py`
  and `audit_logs.py`; dead `members` / `member_logs` branches in `nav_icons.py`.

Audit events now carry the signed-in operator and workstation. The `system` /
`Admin / Entry PC` fallback in `audit.py` applies only before anyone has signed in
(and to `SYSTEM` events such as an unknown-tag alert).

---

## 11. Open items

- Printing is **not yet proven on paper**: the queue accepts jobs, the spooler clears
  them and the printer feeds and cuts on command, but every test slip came out blank —
  including solid `#` blocks at maximum heating. That points at the paper (loaded upside
  down, or not thermal stock) rather than the data path. Confirm with the BC-85AC
  power-on self-test: hold FEED while switching on.
- Confirm `/GEvent.xml` reports rejected and unknown tags, on real hardware.
- Second controller's IP is unassigned.
- Entry driver camera confirmed by the user on 2026-09-09: `http://192.168.1.16/`.
  This supersedes the earlier ConfigTool scan address `.14` for Visitor Entry.
- Camera lane assignment: ANPR .13 / driver .16 on Visitor Entry, driver .15 on
  Visitor Exit. ANPR .12 is Unassigned pending physical lane confirmation.
- Settings is UI + validation only: nothing is persisted and "Test connection" is
  simulated. Reachability testing is the controller adapter's first job.
- Controller passwords are held in memory for the session only. They belong in the
  Windows Credential Manager once persistence lands — `ControllerSettings` deliberately
  stores `has_password`, never the secret.

---

## 12. Parity backlog

Surveyed the live reference panel at `167.86.80.184:9000` on 2026-09-02 (read-only,
logged in as admin). Ordered by what matters for a working gate, not by page order.

### Blocking

1. ~~No pagination~~ — **done.** `ui/pagination.py` provides `Pager` (rows per page
   10/20/50/100, Prev/1/2/…/Next, "Showing X–Y of Z"), wired into VMS Operations,
   E-Tags, E-Tag Logs, Users and Audit Logs. A page never hides behind an ellipsis when
   only one is skipped, and the pager clamps itself when a filter shrinks the list.
   `demo_data.py` seeds 61 e-tags, 140 visits, 180 readings and 6 accounts so paging is
   exercised; it is deterministic and will seed the database too.
2. **Dashboard is a stub.** Theirs is the operational heart: Add Visitor / Add
   Commercial buttons, a Doors Controls shortcut, totals (Total Entries, Currently In
   Society, Total Visitors, Total E-Tags, Total Members), a **per-category count tile
   for every vehicle category**, and a live in-society table
   (`Sr # · Vehicle type · Vehicle Number · CNIC · Entry · Destination · Check Out ·
   Action`) with search, category filter and pagination. Ours shows four zeroed cards.
3. **Camera monitoring is missing entirely** — not even a nav item. Theirs (`/all_camera_feed/`)
   is a feed grid with Location/Status per camera, Refresh Feeds, Fullscreen, zoom,
   Take Snapshot, Refresh Feed. Settings already knows the four Dahua cameras.
4. ~~Reports placeholder~~ — **done**, with the same filter set. PDF and Excel export
   are not implemented: both need a new dependency (reportlab / openpyxl) and that is a
   decision to take deliberately. CSV is stdlib and covers the common case.

### Data model

5. ~~First-class Member record~~ — **settled 2026-09-02: a member and their e-tag are
   one record.** Registering an e-tag registers the member, so there is no Member table
   and no join. The reference's duplicated Member name / contact / CNIC fields are not
   copied; its Member-only fields (Member ID, Address, LESCO Ref No) now live on
   `ETagRecord`. "Members Logs" (`/member_entries/`) belongs to the dropped
   Servant/Maid module — do not build it.
6. ~~Model and vehicle image~~ — **done.** `ETagRecord` now carries `member_id`,
   `address`, `lesco_ref_no`, `model` and `vehicle_image`, with the form and the profile
   updated. Vehicle image stores a path for now, like the employee CNIC scans.
7. **E-Tag detail** still lacks the **Vehicle Image** display (Comments is done). Their
   Entry Logs table is simply `# · Date · Time · Gate`.

### Screens and columns

8. **Settings** is missing Society Name, Society Address, **gate management** (Add /
   Edit / Delete a gate with name + description), and barcode configuration (Commercials
   RFID Bar Code, Visitors RFID Bar Code). Ours covers controllers, cameras, storage,
   backup and workstation, which theirs does not — keep those.
9. **E-Tags list** is missing `Sr #` and `Address`, plus their Date Type / Month / Year
   filters. Ours adds Status, Controllers and Access, which are worth keeping.
10. **Users list** is missing `Sr #` and `CNIC`.
11. **Log filters**: their Members Logs and E-Tag Logs filter by Date, Status and Door.
    Ours filters by event type and direction but has no date filter.
12. **Visitor capture forms** — `/addvisitor` (name, cnic, RFID, cnic_front, cnic_back)
    and `/addcommercials` (adds contact, destination, vehicle_type, make, model,
    vehicle_number, car_image). Entry-portal scope, but Admin can reach them there.

### Their conventions worth knowing

- E-Tag Logs `Full Name` actually shows the **vehicle number** (`MNA-3996`), not a
  person — the e-tag's display identity is its plate. `Door` reads `Entry`, and `Status`
  reads `valid`.
- Our richer status vocabulary (Granted / Expiring soon / Expired / Blocked / Unknown
  tag) is a deliberate improvement required by the 10-day and unknown-tag rules; do not
  reduce it to `valid`.
- Servant / Maid (`/members`) and Members Logs (`/member_entries/`) exist there but were
  explicitly dropped from our build. Do not add them back.

---

## 13. Provenance

Requirements come from the Codex thread *"Plan gate controller software"*
(`~/.codex/sessions/2026/09/01/rollout-2026-09-01T21-10-52-01a05dbc-….jsonl`),
49 user messages across two question rounds, 2026-09-01/02. Camera models and the
controller UI were recovered from images embedded in that transcript. Codex was asked
for a handoff file and the session ended before it produced one — this file replaces it.

Controller and reference-portal passwords appear in that transcript. They are
deliberately **not** copied here; keep them out of the repo.


---

## 14. Traps already hit

Small, expensive things that cost real time here. Read before debugging a similar symptom.

- **`QDateEdit` clamps an invalid date to its minimum.** An "unset" End Date therefore
  read as *before 2000* and filtered every report row away. Use the minimum date as the
  sentinel with `setSpecialValueText`, and treat `date() == minimumDate()` as no bound.
- **`INSERT OR REPLACE` deletes the conflicting row** instead of raising on a unique
  index. Use `ON CONFLICT(<pk>) DO UPDATE`.
- **A `QVBoxLayout` overlaps its items when over-constrained** rather than scrolling —
  the symptom is widgets painted on top of each other, not a clipped window.
- **Never hardcode a table height**; Qt's header is ~38px, not the 46px it looks like.
- **A plain `QWidget` container inside the sidebar** inherits the global widget colour
  and shows as a pale box on the green; give it an explicit transparent background.
- **Wall-clock fixtures flip meaning around midnight.** Pin timestamps in tests.
- **Give the stretch to the widget that can use it.** The Entry portal's event table
  sizes itself to its rows, so stretching it only padded blank space while the form sat
  1px from clipping its last field.
- **Offscreen rendering draws every glyph as a fixed-width box**, so screenshots
  overflow horizontally in ways the real app does not. Do not fit layout to it.
