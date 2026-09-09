"""The camera watchdog. Time is passed in, so none of this needs a camera or a clock."""

from __future__ import annotations

from askari_vms.camera_link import (
    NO_DEVICE,
    NO_FIRST_FRAME,
    STALLED,
    UNPLUGGED,
    CameraLink,
    LinkState,
)


def live_link(at: float = 100.0) -> CameraLink:
    link = CameraLink()
    link.starting(at)
    link.frame(at)
    return link


def test_a_feed_delivering_frames_is_left_alone() -> None:
    link = live_link(100.0)
    for tick in range(1, 20):
        now = 100.0 + tick * 0.5
        link.frame(now)
        assert link.poll(now) is False
    assert link.state is LinkState.LIVE


def test_a_feed_that_goes_quiet_is_lost_even_though_nothing_errored() -> None:
    # The important failure: Qt reports no error, the camera claims to be running, and
    # the frames simply stop. Silence is the only evidence we get.
    link = live_link(100.0)
    assert link.poll(102.0) is False        # 2s of silence is not yet a stall
    assert link.state is LinkState.LIVE

    assert link.poll(103.0) is False        # 3s: declared lost, retry not yet due
    assert link.state is LinkState.LOST
    assert link.reason == STALLED

    assert link.poll(104.0) is True         # one second later the retry falls due


def test_a_camera_that_never_sends_a_first_picture_is_retried() -> None:
    link = CameraLink()
    link.starting(100.0)
    assert link.poll(105.0) is False        # still warming up
    assert link.poll(110.0) is False        # start_timeout reached: lost
    assert link.state is LinkState.LOST
    assert link.reason == NO_FIRST_FRAME
    assert link.poll(111.0) is True


def test_our_own_stop_is_not_a_loss() -> None:
    # The portal stops the camera on purpose while it OCRs the captured card. That must
    # not look like a fault, or every capture would trigger a reconnect.
    link = live_link(100.0)
    link.stopped()
    assert link.poll(200.0) is False
    assert link.state is LinkState.STOPPED

    # Stopping a camera can itself raise an error. Ignore it.
    link.failed(201.0, "device stopped unexpectedly")
    assert link.state is LinkState.STOPPED
    assert link.poll(300.0) is False


def test_retries_back_off_but_never_give_up() -> None:
    link = CameraLink()
    link.starting(0.0)
    link.failed(0.0, NO_DEVICE)

    # Step the clock exactly onto each due time, so what is measured is the interval the
    # link asked for rather than the granularity of the loop.
    now, waits = 0.0, []
    for _ in range(12):
        now += link.retry_in(now)
        assert link.poll(now) is True
        waits.append(link.retry_in(now))     # what it will wait before trying again
        link.failed(now, NO_DEVICE)          # the retry fails too

    # 1s already served, then 2, 4, 8 and held at the 10s ceiling — still going after a
    # dozen attempts, because nobody is awake to restart the application.
    assert waits[:4] == [2.0, 4.0, 8.0, 10.0]
    assert all(wait == 10.0 for wait in waits[4:])
    assert link.attempts == 12
    assert link.state is LinkState.LOST


def test_one_good_frame_forgets_the_backoff() -> None:
    # An outage that lasted an hour must not leave a ten-second lag on the next one.
    link = CameraLink()
    link.starting(0.0)
    link.failed(0.0, NO_DEVICE)
    now = 0.0
    for _ in range(6):
        now += link.retry_in(now)
        assert link.poll(now) is True
        link.failed(now, NO_DEVICE)
    assert link.attempts == 6

    link.frame(now)
    assert link.state is LinkState.LIVE
    assert link.attempts == 0

    link.failed(now, STALLED)
    assert link.poll(now + 1.0) is True     # back to the one-second first retry


def test_replugging_reconnects_at_once_instead_of_waiting_out_the_backoff() -> None:
    link = CameraLink()
    link.starting(0.0)
    link.failed(0.0, UNPLUGGED)
    now = 0.0
    for _ in range(5):                       # push the delay up to the ceiling
        now += link.retry_in(now)
        assert link.poll(now) is True
        link.failed(now, UNPLUGGED)
    assert link.retry_in(now) > 5.0

    link.devices_changed(now)                # the operator plugs it back in
    assert link.retry_in(now) == 0.0
    assert link.poll(now) is True


def test_a_plug_event_while_healthy_changes_nothing() -> None:
    # Someone attaching an unrelated webcam must not disturb a working feed.
    link = live_link(100.0)
    link.devices_changed(100.0)
    assert link.state is LinkState.LIVE
    assert link.poll(100.5) is False


def test_the_operator_is_told_what_broke_and_when_it_retries() -> None:
    link = CameraLink()
    link.starting(0.0)
    assert link.describe(0.0) == "Starting live camera…"

    link.frame(0.0)
    assert link.describe(0.0) == "Live"

    link.failed(0.0, UNPLUGGED)
    assert link.describe(0.0) == "Camera lost — camera unplugged. Reconnecting in 1s"

    link.poll(1.0)
    assert link.describe(1.0) == "Camera lost — camera unplugged. Reconnecting in 2s (attempt 2)"
