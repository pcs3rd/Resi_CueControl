"""Unit tests for event discovery — no network, no real Resi account."""

from datetime import datetime, timedelta, timezone

from resi_cuecontrol import events


class FakeEncodersAPI:
    def __init__(self, statuses):
        self._statuses = statuses

    def status(self, encoder_id):
        return self._statuses.get(encoder_id, {})


class FakeEventsAPI:
    def __init__(self, events_by_encoder, current_by_encoder, all_events=None):
        self._events_by_encoder = events_by_encoder
        self._current_by_encoder = current_by_encoder
        self._all_events = all_events or []

    def for_encoder(self, encoder_id):
        return list(self._events_by_encoder.get(encoder_id, []))

    def current_for_encoder(self, encoder_id):
        return self._current_by_encoder.get(encoder_id)

    def list(self):
        return list(self._all_events)


class FakeClient:
    def __init__(self, statuses, events_by_encoder, current_by_encoder, all_events=None):
        self.encoders = FakeEncodersAPI(statuses)
        self.events = FakeEventsAPI(events_by_encoder, current_by_encoder, all_events)


def test_list_events_marks_current_one_active_and_sorts_newest_first():
    client = FakeClient(
        statuses={"enc1": {"currentEventId": "evt2"}},
        events_by_encoder={
            "enc1": [
                {"uuid": "evt1", "name": "Early Morning Prayer 09/10", "startTime": "2026-09-10T11:00:00Z"},
                {"uuid": "evt2", "name": "Early Morning Prayer 09/15", "startTime": "2026-09-15T11:00:00Z"},
            ]
        },
        current_by_encoder={},
    )

    assert events.list_events(client, "enc1") == [
        ("evt2", "Early Morning Prayer 09/15", "2026-09-15T11:00:00Z", True),
        ("evt1", "Early Morning Prayer 09/10", "2026-09-10T11:00:00Z", False),
    ]


def test_list_events_none_active_when_encoder_not_live():
    client = FakeClient(
        statuses={"enc1": {"currentEventId": None}},
        events_by_encoder={
            "enc1": [{"uuid": "evt1", "name": "Old recording", "startTime": "2026-09-01T00:00:00Z"}]
        },
        current_by_encoder={},
    )

    assert events.list_events(client, "enc1") == [
        ("evt1", "Old recording", "2026-09-01T00:00:00Z", False)
    ]


def test_current_event_returns_none_when_not_streaming():
    client = FakeClient(statuses={}, events_by_encoder={}, current_by_encoder={"enc1": None})
    assert events.current_event(client, "enc1") is None


def test_current_event_returns_tuple_when_streaming():
    client = FakeClient(
        statuses={},
        events_by_encoder={},
        current_by_encoder={
            "enc1": {"uuid": "evt9", "name": "Sunday 11am", "startTime": "2026-09-15T14:00:00Z"}
        },
    )

    assert events.current_event(client, "enc1") == ("evt9", "Sunday 11am", "2026-09-15T14:00:00Z")


def _iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def test_recent_events_filters_by_start_time_and_groups_by_encoder():
    now = datetime.now(timezone.utc)
    recent = now - timedelta(days=1)
    old = now - timedelta(days=30)

    client = FakeClient(
        statuses={},
        events_by_encoder={},
        current_by_encoder={},
        all_events=[
            {"uuid": "evt1", "name": "Recent A", "startTime": _iso(recent), "encoderId": "enc1"},
            {"uuid": "evt2", "name": "Too Old", "startTime": _iso(old), "encoderId": "enc1"},
            {"uuid": "evt3", "name": "Recent B", "startTime": _iso(now), "encoderId": "enc2"},
        ],
    )

    result = events.recent_events(client, days=7)

    assert result == {
        "enc1": [("evt1", "Recent A", _iso(recent))],
        "enc2": [("evt3", "Recent B", _iso(now))],
    }


def test_recent_events_skips_events_with_no_start_time():
    client = FakeClient(
        statuses={},
        events_by_encoder={},
        current_by_encoder={},
        all_events=[{"uuid": "evt1", "name": "No start time", "encoderId": "enc1"}],
    )

    assert events.recent_events(client, days=7) == {}
