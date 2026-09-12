"""Offline push-to-talk destination dictation."""

from __future__ import annotations

import json
from pathlib import Path
import re
from threading import Lock


class SpeechError(RuntimeError):
    """A recoverable microphone/model failure; manual typing always remains available."""


_DIGITS = {
    "zero": 0, "oh": 0, "one": 1, "two": 2, "three": 3, "four": 4,
    "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
}
_SMALL_NUMBERS = {
    **_DIGITS,
    "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13,
    "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17,
    "eighteen": 18, "nineteen": 19,
}
_TENS = {
    "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50,
    "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90,
}
_NUMBER_WORDS = set(_SMALL_NUMBERS) | set(_TENS) | {"hundred", "thousand", "and"}


def _number_value(words: list[str]) -> int:
    total = current = 0
    for word in words:
        if word == "and":
            continue
        if word in _SMALL_NUMBERS:
            current += _SMALL_NUMBERS[word]
        elif word in _TENS:
            current += _TENS[word]
        elif word == "hundred":
            current = (current or 1) * 100
        elif word == "thousand":
            total += (current or 1) * 1000
            current = 0
    return total + current


def normalize_spoken_numbers(text: str) -> str:
    """Turn English number words in a dictated destination into digits.

    Consecutive single digits are treated as a number read digit-by-digit, so
    ``four zero seven`` becomes ``407``. Normal phrases such as ``twenty five``
    and ``one hundred and five`` are evaluated arithmetically.
    """
    tokens = text.split()
    output: list[str] = []
    index = 0
    while index < len(tokens):
        word = re.sub(r"[^a-z]", "", tokens[index].casefold())
        if word not in _NUMBER_WORDS or word == "and":
            output.append(tokens[index])
            index += 1
            continue

        end = index
        words: list[str] = []
        while end < len(tokens):
            candidate = re.sub(r"[^a-z]", "", tokens[end].casefold())
            if candidate not in _NUMBER_WORDS:
                break
            # "and" only belongs inside a number, never at either edge.
            if candidate == "and" and (not words or end + 1 == len(tokens)):
                break
            words.append(candidate)
            end += 1
        while words and words[-1] == "and":
            words.pop()
            end -= 1

        if len(words) > 1 and all(item in _DIGITS for item in words):
            replacement = "".join(str(_DIGITS[item]) for item in words)
        else:
            replacement = str(_number_value(words))
        output.append(replacement)
        index = end
    return " ".join(output)


def choose_input_device(devices, preferred: str = "USB Microphone") -> int | None:
    """Pick by stable display name rather than the index Windows may reorder."""
    inputs = [(index, item) for index, item in enumerate(devices) if item["max_input_channels"] > 0]
    wanted = preferred.casefold()
    exact = [index for index, item in inputs if item["name"].casefold() == f"microphone ({wanted})"]
    if exact:
        return exact[0]
    contains = [index for index, item in inputs if wanted in item["name"].casefold()]
    return contains[0] if contains else None


class OfflineDictation:
    """Capture 16 kHz mono PCM and transcribe it locally with Vosk."""

    sample_rate = 16_000
    max_seconds = 20

    def __init__(self, model_path: str | Path, microphone_name: str = "USB Microphone") -> None:
        self.model_path = Path(model_path)
        self.microphone_name = microphone_name
        self._stream = None
        self._chunks: list[bytes] = []
        self._bytes = 0
        self._lock = Lock()
        self._model = None

    @property
    def recording(self) -> bool:
        return self._stream is not None

    def start(self) -> None:
        if self.recording:
            return
        try:
            import sounddevice as sd
            device = choose_input_device(sd.query_devices(), self.microphone_name)
            if device is None:
                raise SpeechError(f"Microphone {self.microphone_name!r} was not found")
            sd.check_input_settings(device=device, channels=1, dtype="int16", samplerate=self.sample_rate)
            with self._lock:
                self._chunks = []
                self._bytes = 0

            def receive(indata, _frames, _time_info, status) -> None:
                if status:
                    return
                data = bytes(indata)
                with self._lock:
                    limit = self.sample_rate * 2 * self.max_seconds
                    if self._bytes < limit:
                        data = data[:limit - self._bytes]
                        self._chunks.append(data)
                        self._bytes += len(data)

            stream = sd.RawInputStream(
                samplerate=self.sample_rate, blocksize=4000, device=device,
                dtype="int16", channels=1, callback=receive,
            )
            stream.start()
            self._stream = stream
        except SpeechError:
            raise
        except Exception as exc:
            raise SpeechError(f"USB microphone could not start: {exc}") from exc

    def stop(self) -> bytes:
        stream, self._stream = self._stream, None
        if stream is not None:
            try:
                stream.stop()
                stream.close()
            except Exception:
                pass
        with self._lock:
            audio = b"".join(self._chunks)
            self._chunks = []
            self._bytes = 0
        return audio

    def transcribe(self, audio: bytes) -> str:
        if len(audio) < self.sample_rate // 2:
            raise SpeechError("No speech was captured; hold Ctrl+D while speaking")
        if not self.model_path.is_dir():
            raise SpeechError(f"Offline speech model is missing from {self.model_path}")
        try:
            from vosk import KaldiRecognizer, Model, SetLogLevel
            SetLogLevel(-1)
            if self._model is None:
                self._model = Model(str(self.model_path))
            recognizer = KaldiRecognizer(self._model, self.sample_rate)
            recognizer.AcceptWaveform(audio)
            text = " ".join(json.loads(recognizer.FinalResult()).get("text", "").split())
            return normalize_spoken_numbers(text)
        except SpeechError:
            raise
        except Exception as exc:
            raise SpeechError(f"Offline speech recognition failed: {exc}") from exc


def default_model_path(data_directory: str | Path) -> Path:
    return Path(data_directory).parent / "models" / "vosk-model-small-en-us-0.15"
