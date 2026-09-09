"""Keep a camera feed alive across unplugs, driver hiccups and silent stalls.

A webcam at a gate gets knocked, unplugged by a cleaner, or browns out on a long USB
run, and Qt does not reliably say so. The common failure is not an error signal at all:
the camera still reports itself active while frames quietly stop arriving. So loss is
detected by *silence* as well as by errors, and a feed that has gone quiet for a few
seconds is treated as lost even though nothing complained.

Recovery re-enumerates the device list and builds a new camera rather than restarting
the old one, because a handle to a device that has been unplugged is dead and cannot be
revived. Retries back off geometrically but never stop: nobody is going to restart the
application at three in the morning, so the feed must be able to come back on its own
after an outage of any length.

This module is pure policy and holds no Qt objects and no clock. The caller owns the
widgets and passes the time in, which is what makes the behaviour testable without a
camera attached.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class LinkState(StrEnum):
    STOPPED = "Stopped"    # deliberately off — during capture, or the window closed
    STARTING = "Starting"  # a camera was built and started; no picture yet
    LIVE = "Live"          # frames are arriving
    LOST = "Lost"          # wanted, but not delivering; a retry is scheduled


# Loss reasons, phrased for an operator rather than an engineer.
NO_DEVICE = "no camera found"
UNPLUGGED = "camera unplugged"
NO_FIRST_FRAME = "camera never sent a picture"
STALLED = "the picture stopped"


@dataclass(slots=True)
class CameraLink:
    """Decides when a feed counts as lost and when to try it again."""

    # A webcam delivers 15-30 frames a second, so three seconds of silence is a stall,
    # not a slow frame. Starting is slower — a cold camera can take several seconds to
    # expose and focus before the first frame lands.
    frame_timeout: float = 3.0
    start_timeout: float = 10.0
    first_retry: float = 1.0
    max_retry: float = 10.0

    state: LinkState = LinkState.STOPPED
    reason: str = ""
    attempts: int = 0
    _last_frame: float = 0.0
    _started: float = 0.0
    _retry_at: float = 0.0
    _delay: float = 0.0

    # ---------- events from the camera ----------

    def starting(self, now: float) -> None:
        """A fresh camera has been built and start() issued."""
        self.state = LinkState.STARTING
        self._started = now

    def frame(self, now: float) -> None:
        """A picture arrived, so the feed is healthy and the backoff is forgotten."""
        self.state = LinkState.LIVE
        self._last_frame = now
        self.attempts = 0
        self.reason = ""
        self._delay = 0.0

    def failed(self, now: float, reason: str) -> None:
        """The feed is not usable. Schedules a retry unless we stopped it ourselves."""
        if self.state is LinkState.STOPPED:
            # Stopping a camera can itself raise an error. That is not a loss.
            return
        self.state = LinkState.LOST
        self.reason = reason
        if not self._delay:
            self._delay = self.first_retry
        self._retry_at = now + self._delay

    def stopped(self) -> None:
        """Our own doing — capturing a card, or closing the window. Do not reconnect."""
        self.state = LinkState.STOPPED
        self.reason = ""

    def devices_changed(self, now: float) -> None:
        """Something was plugged in or removed, so retry now instead of waiting.

        The device list is not consulted here. Re-enumerating is the only honest test of
        whether our camera is back, and that is the caller's job on the next poll.
        """
        if self.state is LinkState.LOST:
            self._retry_at = now

    # ---------- the tick ----------

    def poll(self, now: float) -> bool:
        """Advance the clock. True means: build a new camera, then call starting().

        The caller must follow a True with either `starting()` or `failed()`; otherwise
        the next retry simply falls due at the backoff interval, which is safe.
        """
        if self.state is LinkState.STOPPED:
            return False
        if self.state is LinkState.STARTING and now - self._started >= self.start_timeout:
            self.failed(now, NO_FIRST_FRAME)
        elif self.state is LinkState.LIVE and now - self._last_frame >= self.frame_timeout:
            self.failed(now, STALLED)

        if self.state is LinkState.LOST and now >= self._retry_at:
            self.attempts += 1
            self._delay = min(self._delay * 2, self.max_retry) if self._delay else self.first_retry
            self._retry_at = now + self._delay
            return True
        return False

    # ---------- for the operator ----------

    def retry_in(self, now: float) -> float:
        return max(0.0, self._retry_at - now)

    def describe(self, now: float) -> str:
        """One line for the panel. Says what broke and that it is being dealt with."""
        if self.state is LinkState.LOST:
            seconds = int(self.retry_in(now) + 0.5)
            within = "now" if seconds <= 0 else f"in {seconds}s"
            attempt = f" (attempt {self.attempts + 1})" if self.attempts else ""
            return f"Camera lost — {self.reason}. Reconnecting {within}{attempt}"
        if self.state is LinkState.STARTING:
            return "Starting live camera…"
        if self.state is LinkState.LIVE:
            return "Live"
        return "Camera stopped"
