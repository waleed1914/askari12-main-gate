"""Local USB-camera capture and OCR for Pakistani identity cards."""

from __future__ import annotations

import re
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Protocol

DATE_RE = re.compile(r"\b(?:0?[1-9]|[12]\d|3[01])[-./](?:0?[1-9]|1[0-2])[-./](?:19|20)\d{2}\b")
CNIC_RE = re.compile(r"\b(\d{5})[- ]?(\d{7})[- ]?(\d)\b")
# The card prints Identity Number and Date of Birth side by side, and the detector
# returns them as a single run with no separator: '31101-6646517-711.11.2000'. The
# trailing \b above then fails. With explicit dashes the 5-7-1 shape is unambiguous,
# so this variant needs no boundary after the check digit.
CNIC_JOINED_RE = re.compile(r"\b(\d{5})[- ](\d{7})[- ](\d)")


@dataclass(frozen=True, slots=True)
class OCRField:
    value: str
    confidence: float


@dataclass(frozen=True, slots=True)
class CNICCapture:
    image_path: str
    fields: dict[str, OCRField] = field(default_factory=dict)
    text_lines: tuple[str, ...] = ()


class CNICReader(Protocol):
    def capture_and_read(self) -> CNICCapture: ...


class CNICReadError(RuntimeError):
    """A recoverable failure that must never prevent manual entry."""


def _clean(text: str) -> str:
    return " ".join(re.sub(r"^[\s:.-]+", "", text).split())


def _squash(text: str) -> str:
    """Lowercase, with every space and punctuation mark removed.

    The detector frequently runs a caption's words together — 'FatherName',
    'DateofBirth' — so a caption can only be recognised with the spacing ignored.
    """
    return re.sub(r"[^a-z0-9]", "", text.lower())


def _label_end(line: str, label: str) -> int | None:
    """Index in `line` just past `label`, ignoring the spacing the detector dropped.

    Matching happens on the squashed text, but the value has to be cut out of the
    original line, so the position of every squashed character is kept to map back.
    """
    target = _squash(label)
    if not target:
        return None
    positions = [index for index, character in enumerate(line) if character.isalnum()]
    squashed = "".join(line[index].lower() for index in positions)
    at = squashed.find(target)
    if at < 0:
        return None
    return positions[at + len(target) - 1] + 1


def _has_label(text: str, alternatives: tuple[str, ...]) -> bool:
    return any(_label_end(text, label) is not None for label in alternatives)


def _is_label(text: str, labels: dict[str, tuple[str, ...]]) -> bool:
    """True when a line is one of the field captions rather than a value."""
    return any(_has_label(text, alternatives) for alternatives in labels.values())


# 'Waleed Bin Nasir' comes back as 'WaleedBinNasir' often enough to be worth undoing.
# Only a single unbroken token is split, and only where a lowercase letter is followed
# by a capital, so an all-capitals name is left exactly as printed.
_RUN_ON_NAME_RE = re.compile(r"(?<=[a-z])(?=[A-Z])")


def _split_run_on_name(value: str) -> str:
    # Per word, because only part of a name may be run together: the detector returned
    # 'ShakeelAnwar Tabassum' for a three-word name.
    return " ".join(
        _RUN_ON_NAME_RE.sub(" ", word) if word.isalpha() else word
        for word in value.split()
    )


def parse_identity_text(
    lines: list[str], scores: list[float], boxes: list | None = None,
) -> dict[str, OCRField]:
    """Extract only confidently labelled fields; never invent missing identity data."""
    fields: dict[str, OCRField] = {}
    labels = {
        "visitor_name": ("name",),
        "father_name": ("father name", "father's name", "husband name", "s/o", "d/o", "w/o"),
        "date_of_birth": ("date of birth", "dob"),
        "cnic_issue_date": ("date of issue", "issue date", "issued"),
        "cnic_expiry_date": ("date of expiry", "expiry date", "valid till", "expires"),
    }
    normal = [_clean(line) for line in lines]

    # A CNIC glued to a date hides both: the date has no left word boundary and the
    # CNIC no right one. Pull the CNIC out first and scan what remains for dates.
    found_cnic: tuple[int, OCRField] | None = None
    date_text = list(normal)
    for index, line in enumerate(normal):
        match = CNIC_RE.search(line) or CNIC_JOINED_RE.search(line)
        if match is None:
            continue
        if found_cnic is None:
            found_cnic = (index, OCRField("-".join(match.groups()), scores[index]))
        date_text[index] = (line[: match.start()] + " " + line[match.end():]).strip()
    if found_cnic is not None:
        fields["cnic"] = found_cnic[1]

    # CNIC date labels share rows, so OCR reading order is not reliable. Match each
    # label to the closest date geometrically below it when detection boxes exist.
    if boxes is not None and len(boxes) == len(normal):
        date_labels = {
            "date_of_birth": ("date of birth", "dob"),
            "cnic_issue_date": ("date of issue", "issue date", "issued"),
            "cnic_expiry_date": ("date of expiry", "expiry date", "valid till", "expires"),
        }
        date_values = [(i, DATE_RE.search(text)) for i, text in enumerate(date_text)]
        date_values = [(i, match) for i, match in date_values if match is not None]
        for key, alternatives in date_labels.items():
            label_index = next((
                i for i, text in enumerate(normal) if _has_label(text, alternatives)
            ), None)
            if label_index is None:
                continue
            label_box = boxes[label_index]
            label_x = sum(float(point[0]) for point in label_box) / len(label_box)
            label_y = sum(float(point[1]) for point in label_box) / len(label_box)
            candidates = []
            for value_index, match in date_values:
                value_box = boxes[value_index]
                value_x = sum(float(point[0]) for point in value_box) / len(value_box)
                value_y = sum(float(point[1]) for point in value_box) / len(value_box)
                if value_y <= label_y or value_y - label_y > 180:
                    continue
                candidates.append((abs(value_x - label_x) + 2 * (value_y - label_y), value_index, match))
            if candidates:
                _, value_index, match = min(candidates, key=lambda item: item[0])
                fields[key] = OCRField(match.group(0), min(scores[label_index], scores[value_index]))

    # Without geometry, the card still detects as a run of date labels followed by a
    # run of their values ('Date of Issue', 'Date of Expiry', '24.01.2019',
    # '24.01.2029'). Pair them positionally; matching each label to the next line
    # would file the issue date under expiry.
    date_keys = {
        "date_of_birth": ("date of birth", "dob"),
        "cnic_issue_date": ("date of issue", "issue date", "issued"),
        "cnic_expiry_date": ("date of expiry", "expiry date", "valid till", "expires"),
    }

    def _date_key(text: str) -> str | None:
        return next((key for key, names in date_keys.items()
                     if _has_label(text, names)), None)

    index = 0
    while index < len(normal):
        run = []
        while index + len(run) < len(normal) and (key := _date_key(normal[index + len(run)])):
            run.append(key)
        if len(run) < 2:
            index += 1
            continue
        values = []
        cursor = index + len(run)
        while cursor < len(normal) and (found := DATE_RE.search(date_text[cursor])):
            values.append((found.group(0), scores[cursor]))
            cursor += 1
        for key, (value, score) in zip(run, values):
            fields.setdefault(key, OCRField(value, score))
        index = cursor if values else index + len(run)

    for index, line in enumerate(normal):
        squashed = _squash(line)
        for key, alternatives in labels.items():
            if key in fields:
                continue
            # 'Father Name' also contains 'Name'. The holder's own name is captioned
            # 'Name' alone, so a caption mentioning a relative is never theirs.
            if key == "visitor_name" and any(word in squashed for word in ("father", "husband")):
                continue
            end = next(
                (found for found in (_label_end(line, value) for value in alternatives)
                 if found is not None),
                None,
            )
            if end is None:
                continue
            candidate = _clean(line[end:])
            confidence = scores[index]
            if "date" in key and not DATE_RE.search(candidate):
                # The value may have been merged onto the next detected line.
                candidate = ""
            if not candidate and index + 1 < len(normal):
                following = normal[index + 1]
                # 'Date of Issue' / 'Date of Expiry' are detected as adjacent labels
                # before either value, so the next line is another label, not the
                # answer. Taking it would file the issue date under expiry. Leave the
                # field blank for the operator rather than guess wrong.
                if _is_label(following, labels):
                    continue
                source = date_text if "date" in key else normal
                candidate = source[index + 1]
                confidence = min(confidence, scores[index + 1])
            if "date" in key:
                date = DATE_RE.search(candidate)
                candidate = date.group(0) if date else ""
            elif "name" in key:
                candidate = _split_run_on_name(candidate)
            if candidate:
                fields[key] = OCRField(candidate, confidence)
    return fields


class LocalCNICReader:
    """Capture via OpenCV and OCR through bundled ONNX models, fully offline."""

    def __init__(self, camera_index: int, data_directory: str | Path) -> None:
        self.camera_index = camera_index
        self.data_directory = Path(data_directory)
        self._engine = None

    def _ocr(self):
        if self._engine is None:
            from rapidocr import RapidOCR
            self._engine = RapidOCR()
        return self._engine

    def new_image_path(self) -> Path:
        folder = self.data_directory / "visitor-images" / datetime.now().strftime("%Y-%m-%d")
        folder.mkdir(parents=True, exist_ok=True)
        return folder / f"cnic-{uuid.uuid4().hex}.jpg"

    def read_image(self, image_path: str | Path) -> CNICCapture:
        """OCR a frame already captured by the live Qt camera pipeline."""
        path = Path(image_path)
        try:
            result = self._ocr()(path)
            lines = list(result.txts or ())
            scores = [float(value) for value in (result.scores or ())]
            boxes = list(result.boxes) if result.boxes is not None else None
        except Exception as exc:
            raise CNICReadError(f"OCR failed: {exc}") from exc
        return CNICCapture(str(path), parse_identity_text(lines, scores, boxes), tuple(lines))

    def capture_and_read(self) -> CNICCapture:
        import cv2

        camera = None
        # This fixed close-up card box needs DirectShow's manual-focus control. The
        # attached Logitech measured sharpest at 255; Media Foundation remains a
        # fallback for cameras that do not expose DirectShow.
        backends = [
            value for value in (
                getattr(cv2, "CAP_DSHOW", None),
                getattr(cv2, "CAP_MSMF", None),
                getattr(cv2, "CAP_ANY", 0),
            ) if value is not None
        ]
        for backend in dict.fromkeys(backends):
            candidate = cv2.VideoCapture(self.camera_index, backend)
            if candidate.isOpened():
                camera = candidate
                break
            candidate.release()
        if camera is None:
            raise CNICReadError(
                f"ID card camera {self.camera_index} could not be opened; "
                "close any browser or Camera app using it and try again"
            )
        camera.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
        camera.set(cv2.CAP_PROP_FRAME_WIDTH, 1920)
        camera.set(cv2.CAP_PROP_FRAME_HEIGHT, 1080)
        camera.set(cv2.CAP_PROP_AUTOFOCUS, 0)
        camera.set(cv2.CAP_PROP_FOCUS, 255)
        best_frame = None
        best_sharpness = -1.0
        deadline = time.monotonic() + 2.0
        try:
            # Let exposure and the manual lens position settle, then keep the sharpest
            # frame rather than whichever frame happened to arrive last.
            while time.monotonic() < deadline:
                ok, candidate = camera.read()
                if ok and candidate is not None:
                    gray = cv2.cvtColor(candidate, cv2.COLOR_BGR2GRAY)
                    height, width = gray.shape
                    centre = gray[height // 8: height * 7 // 8, width // 8: width * 7 // 8]
                    sharpness = float(cv2.Laplacian(centre, cv2.CV_64F).var())
                    if sharpness > best_sharpness:
                        best_frame, best_sharpness = candidate.copy(), sharpness
                time.sleep(0.08)
        finally:
            camera.release()
        if best_frame is None:
            raise CNICReadError("ID card camera opened but returned no image")
        frame = best_frame

        try:
            path = self.new_image_path()
            if not cv2.imwrite(str(path), frame):
                raise OSError("OpenCV could not encode the image")
        except OSError as exc:
            raise CNICReadError(f"ID card image could not be saved: {exc}") from exc

        return self.read_image(path)
