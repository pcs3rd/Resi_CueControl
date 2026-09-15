"""Unit tests for cue business logic — no network, no real Resi account.
Exercises the delay-correction math against a fake pyResi client."""

from datetime import datetime, timezone

import pytest

from resi_cuecontrol import cues


class FakeCuesAPI:
    def __init__(self, initial=None):
        self.created = []
        self.created_kwargs = []
        self.updated = []
        self._cues = list(initial or [])

    def list(self, event_profile_id, event_id):
        return list(self._cues)

    def create(self, event_profile_id, event_id, position, name, **kw):
        cue = {"uuid": "new-uuid", "position": position, "name": name}
        self._cues.append(cue)
        self.created.append((event_profile_id, event_id, position, name))
        self.created_kwargs.append(kw)
        return cue

    def update(self, event_profile_id, event_id, cue_id, position, name, **kw):
        self.updated.append((event_profile_id, event_id, cue_id, position, name))
        return True


class FakeEventsAPI:
    def __init__(self, event, delay=0.0, buffer_delay=0.0):
        self._event = event
        self._delay = delay
        self._buffer_delay = buffer_delay
        self.buffer_segments_calls = []

    def current_for_encoder(self, encoder_id):
        return self._event

    def get(self, event_id):
        return self._event

    def streaming_delay(self, event):
        return self._delay

    def decoder_buffer_delay(self, event, buffer_segments=3):
        self.buffer_segments_calls.append(buffer_segments)
        return self._buffer_delay


class FakeClient:
    def __init__(self, event, delay=0.0, buffer_delay=0.0, initial_cues=None):
        self.events = FakeEventsAPI(event, delay, buffer_delay)
        self.cues = FakeCuesAPI(initial_cues)


EVENT = {
    "uuid": "evt1",
    "eventProfileId": "prof1",
    "startTime": "2026-09-10T14:00:00Z",
}


def test_read_cues_returns_position_in_seconds():
    client = FakeClient(
        EVENT,
        initial_cues=[
            {"uuid": "c1", "position": "0:00:05.000", "name": "Start"},
            {"uuid": "c2", "position": "0:00:10.000", "name": "Mid"},
        ],
    )
    assert cues.read_cues(client, "enc1") == [
        ("c1", 5.0, "Start"),
        ("c2", 10.0, "Mid"),
    ]


def test_read_cues_for_event_uses_events_get_directly():
    client = FakeClient(
        EVENT,
        initial_cues=[{"uuid": "c1", "position": "0:00:05.000", "name": "Start"}],
    )

    assert cues.read_cues_for_event(client, "evt1") == [("c1", 5.0, "Start")]


def test_create_cue_now_subtracts_streaming_delay():
    # 60s after start, 8s of measured streaming delay -> cue lands at 52s.
    now = datetime(2026, 9, 10, 14, 1, 0, tzinfo=timezone.utc)
    client = FakeClient(EVENT, delay=8.0)

    cues.create_cue_now(client, "enc1", "Test Cue", now=now)

    assert client.cues.created[-1] == ("prof1", "evt1", "00:00:52.000", "Test Cue")


def test_create_cue_now_clamps_to_zero_when_delay_exceeds_elapsed():
    # Only 2s into the event but a 10s measured delay -> never go negative.
    now = datetime(2026, 9, 10, 14, 0, 2, tzinfo=timezone.utc)
    client = FakeClient(EVENT, delay=10.0)

    cues.create_cue_now(client, "enc1", "Edge", now=now)

    assert client.cues.created[-1][2] == "00:00:00.000"


def test_create_cue_now_logs_the_delay_it_applied(caplog):
    now = datetime(2026, 9, 10, 14, 1, 0, tzinfo=timezone.utc)
    client = FakeClient(EVENT, delay=8.0)

    with caplog.at_level("INFO", logger="resi_cuecontrol.cues"):
        cues.create_cue_now(client, "enc1", "Test Cue", now=now)

    assert len(caplog.records) == 1
    message = caplog.records[0].getMessage()
    assert "8.000" in message
    assert "00:00:52.000" in message


def test_create_cue_now_subtracts_decoder_buffer_delay_too():
    # 60s in, 8s streaming delay + 5s decoder buffer delay -> lands at 47s.
    now = datetime(2026, 9, 10, 14, 1, 0, tzinfo=timezone.utc)
    client = FakeClient(EVENT, delay=8.0, buffer_delay=5.0)

    cues.create_cue_now(client, "enc1", "Test Cue", now=now)

    assert client.cues.created[-1] == ("prof1", "evt1", "00:00:47.000", "Test Cue")


def test_create_cue_now_logs_both_delay_components(caplog):
    now = datetime(2026, 9, 10, 14, 1, 0, tzinfo=timezone.utc)
    client = FakeClient(EVENT, delay=8.0, buffer_delay=5.0)

    with caplog.at_level("INFO", logger="resi_cuecontrol.cues"):
        cues.create_cue_now(client, "enc1", "Test Cue", now=now)

    message = caplog.records[-1].getMessage()
    assert "8.000" in message
    assert "5.000" in message
    assert "13.000" in message
    assert "00:00:47.000" in message


def test_create_cue_at_ignores_streaming_delay():
    # Explicit position, not "now minus delay" — an 8s delay set on the
    # fake client must have zero effect here.
    client = FakeClient(EVENT, delay=8.0)

    cues.create_cue_at(client, "enc1", 30.0, "At 30s")

    assert client.cues.created[-1] == ("prof1", "evt1", "00:00:30.000", "At 30s")


def test_create_cue_now_passes_buffer_segments_through():
    now = datetime(2026, 9, 10, 14, 1, 0, tzinfo=timezone.utc)
    client = FakeClient(EVENT, delay=0.0, buffer_delay=0.0)

    cues.create_cue_now(client, "enc1", "Test Cue", now=now, buffer_segments=5)

    assert client.events.buffer_segments_calls == [5]


def test_create_cue_now_defaults_buffer_segments_to_three():
    now = datetime(2026, 9, 10, 14, 1, 0, tzinfo=timezone.utc)
    client = FakeClient(EVENT, delay=0.0, buffer_delay=0.0)

    cues.create_cue_now(client, "enc1", "Test Cue", now=now)

    assert client.events.buffer_segments_calls == [3]


def test_create_cue_now_defaults_to_private():
    now = datetime(2026, 9, 10, 14, 1, 0, tzinfo=timezone.utc)
    client = FakeClient(EVENT, delay=0.0)

    cues.create_cue_now(client, "enc1", "Test Cue", now=now)

    assert client.cues.created_kwargs[-1]["private_cue"] is True


def test_create_cue_now_can_be_made_visible():
    now = datetime(2026, 9, 10, 14, 1, 0, tzinfo=timezone.utc)
    client = FakeClient(EVENT, delay=0.0)

    cues.create_cue_now(client, "enc1", "Test Cue", now=now, private_cue=False)

    assert client.cues.created_kwargs[-1]["private_cue"] is False


def test_create_cue_at_can_be_made_visible():
    client = FakeClient(EVENT)

    cues.create_cue_at(client, "enc1", 30.0, "At 30s", private_cue=False)

    assert client.cues.created_kwargs[-1]["private_cue"] is False


def test_update_cue_sends_seconds_as_position_string():
    client = FakeClient(EVENT)

    position = cues.update_cue(client, "enc1", "c1", 12.5, "Renamed")

    assert position == "00:00:12.500"
    assert client.cues.updated[-1] == ("prof1", "evt1", "c1", "00:00:12.500", "Renamed")


def test_encoder_not_live_raises_for_read_create_and_update():
    client = FakeClient(event=None)

    with pytest.raises(cues.EncoderNotLive):
        cues.read_cues(client, "enc-offline")

    with pytest.raises(cues.EncoderNotLive):
        cues.create_cue_now(client, "enc-offline", "x")

    with pytest.raises(cues.EncoderNotLive):
        cues.create_cue_at(client, "enc-offline", 0.0, "x")

    with pytest.raises(cues.EncoderNotLive):
        cues.update_cue(client, "enc-offline", "c1", 0.0, "x")
