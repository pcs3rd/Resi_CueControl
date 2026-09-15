"""Unit tests for encoder discovery — no network, no real Resi account."""

from resi_cuecontrol import encoders


class FakeEncodersAPI:
    def __init__(self, encoders_list, statuses):
        self._encoders = encoders_list
        self._statuses = statuses

    def list(self):
        return list(self._encoders)

    def status(self, encoder_id):
        return self._statuses.get(encoder_id, {})


class FakeClient:
    def __init__(self, encoders_list, statuses):
        self.encoders = FakeEncodersAPI(encoders_list, statuses)


def test_list_encoders_reports_live_state():
    client = FakeClient(
        encoders_list=[
            {"uuid": "enc1", "name": "Main Room"},
            {"uuid": "enc2", "name": "Overflow"},
        ],
        statuses={
            "enc1": {"currentEventId": "evt1"},
            "enc2": {"currentEventId": None},
        },
    )

    assert encoders.list_encoders(client) == [
        ("enc1", "Main Room", True),
        ("enc2", "Overflow", False),
    ]


def test_list_encoders_skips_status_lookup_when_uuid_missing():
    client = FakeClient(encoders_list=[{"name": "No UUID"}], statuses={})

    assert encoders.list_encoders(client) == [(None, "No UUID", False)]
