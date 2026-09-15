"""OSC round-trip test: sends real UDP OSC packets into a running OSCApp
against a fake pyResi client, and checks the reply messages it sends back.
No network beyond localhost, no real Resi account."""

import threading
import time

from pythonosc.dispatcher import Dispatcher
from pythonosc.osc_server import BlockingOSCUDPServer
from pythonosc.udp_client import SimpleUDPClient

from resi_cuecontrol.osc_server import OSCApp

LISTEN_PORT = 9190
REPLY_PORT = 9191


class FakeCuesAPI:
    def list(self, event_profile_id, event_id):
        return [{"uuid": "c1", "position": "0:00:05.000", "name": "Start"}]

    def create(self, event_profile_id, event_id, position, name, **kw):
        return {"uuid": "newid", "position": position, "name": name}

    def update(self, *a, **kw):
        return True


class EventsListMixin:
    """Adds for_encoder/list support to FakeEventsAPI for the events tests."""

    def for_encoder(self, encoder_id):
        return [
            {"uuid": "evt1", "name": "Sunday 11am", "startTime": "2026-09-10T14:00:00Z"}
        ]

    def list(self):
        return [
            {
                "uuid": "evt1",
                "name": "Sunday 11am",
                "startTime": "2026-09-10T14:00:00Z",
                "encoderId": "enc1",
            }
        ]


class FakeEventsAPI(EventsListMixin):
    def current_for_encoder(self, encoder_id):
        return {
            "uuid": "evt1",
            "eventProfileId": "p1",
            "name": "Sunday 11am",
            "startTime": "2026-09-10T14:00:00Z",
        }

    def streaming_delay(self, event):
        return 0.0


class FakeEncodersAPI:
    def list(self):
        return [{"uuid": "enc1", "name": "Main Room"}]

    def status(self, encoder_id):
        return {"currentEventId": "evt1"}


class FakeClient:
    def __init__(self):
        self.events = FakeEventsAPI()
        self.cues = FakeCuesAPI()
        self.encoders = FakeEncodersAPI()


def _collect_replies(port, count, timeout=2.0):
    """Run a tiny OSC server that appends every message it receives to a
    list, for `timeout` seconds or until `count` messages arrive."""
    received = []
    dispatcher = Dispatcher()
    dispatcher.set_default_handler(lambda addr, *args: received.append((addr, args)))
    server = BlockingOSCUDPServer(("127.0.0.1", port), dispatcher)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def stop_when_done():
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline and len(received) < count:
            time.sleep(0.02)
        server.shutdown()

    stopper = threading.Thread(target=stop_when_done, daemon=True)
    stopper.start()
    return server, received, stopper


def test_osc_commands_round_trip():
    server, received, stopper = _collect_replies(REPLY_PORT, count=5)

    app = OSCApp(
        FakeClient(),
        listen_host="127.0.0.1",
        listen_port=LISTEN_PORT,
        reply_host="127.0.0.1",
        reply_port=REPLY_PORT,
    )
    app_thread = threading.Thread(target=app.serve_forever, daemon=True)
    app_thread.start()
    time.sleep(0.2)

    client = SimpleUDPClient("127.0.0.1", LISTEN_PORT)
    client.send_message("/resi/cue/read", ["enc1"])
    client.send_message("/resi/cue/create", ["enc1", "NewCue"])
    client.send_message("/resi/cue/create_at", ["enc1", 30.0, "AtCue"])
    client.send_message("/resi/cue/update", ["enc1", "c1", 42.0, "Renamed"])

    stopper.join(timeout=3.0)
    app.server.shutdown()

    addresses = [addr for addr, _ in received]
    assert "/resi/cue/entry" in addresses
    assert "/resi/cue/read/done" in addresses
    assert addresses.count("/resi/cue/created") == 2
    assert "/resi/cue/updated" in addresses

    entry = next(args for addr, args in received if addr == "/resi/cue/entry")
    assert entry == ("enc1", "c1", 5.0, "Start")

    created_names = {args[3] for addr, args in received if addr == "/resi/cue/created"}
    assert created_names == {"NewCue", "AtCue"}

    at_cue = next(
        args for addr, args in received
        if addr == "/resi/cue/created" and args[3] == "AtCue"
    )
    assert at_cue[2] == 30.0

    updated = next(args for addr, args in received if addr == "/resi/cue/updated")
    assert updated == ("enc1", "c1", "0:00:42.000", "Renamed")


def test_unknown_encoder_replies_with_error_not_silence():
    server, received, stopper = _collect_replies(REPLY_PORT + 1, count=1)

    class OfflineEventsAPI:
        def current_for_encoder(self, encoder_id):
            return None

    class OfflineClient:
        def __init__(self):
            self.events = OfflineEventsAPI()
            self.cues = FakeCuesAPI()

    app = OSCApp(
        OfflineClient(),
        listen_host="127.0.0.1",
        listen_port=LISTEN_PORT + 1,
        reply_host="127.0.0.1",
        reply_port=REPLY_PORT + 1,
    )
    app_thread = threading.Thread(target=app.serve_forever, daemon=True)
    app_thread.start()
    time.sleep(0.2)

    client = SimpleUDPClient("127.0.0.1", LISTEN_PORT + 1)
    client.send_message("/resi/cue/read", ["enc-offline"])

    stopper.join(timeout=3.0)
    app.server.shutdown()

    assert received, "expected an /resi/cue/error reply, got nothing"
    addr, args = received[0]
    assert addr == "/resi/cue/error"
    assert args[0] == "enc-offline"


def test_encoders_list_round_trip():
    server, received, stopper = _collect_replies(REPLY_PORT + 2, count=2)

    app = OSCApp(
        FakeClient(),
        listen_host="127.0.0.1",
        listen_port=LISTEN_PORT + 2,
        reply_host="127.0.0.1",
        reply_port=REPLY_PORT + 2,
    )
    app_thread = threading.Thread(target=app.serve_forever, daemon=True)
    app_thread.start()
    time.sleep(0.2)

    client = SimpleUDPClient("127.0.0.1", LISTEN_PORT + 2)
    client.send_message("/resi/encoders/list", [])

    stopper.join(timeout=3.0)
    app.server.shutdown()

    addresses = [addr for addr, _ in received]
    assert "/resi/encoder/entry" in addresses
    assert "/resi/encoders/list/done" in addresses

    entry = next(args for addr, args in received if addr == "/resi/encoder/entry")
    assert entry == ("enc1", "Main Room", True)

    done = next(args for addr, args in received if addr == "/resi/encoders/list/done")
    assert done == (1,)


def test_events_list_and_current_round_trip():
    server, received, stopper = _collect_replies(REPLY_PORT + 3, count=3)

    app = OSCApp(
        FakeClient(),
        listen_host="127.0.0.1",
        listen_port=LISTEN_PORT + 3,
        reply_host="127.0.0.1",
        reply_port=REPLY_PORT + 3,
    )
    app_thread = threading.Thread(target=app.serve_forever, daemon=True)
    app_thread.start()
    time.sleep(0.2)

    client = SimpleUDPClient("127.0.0.1", LISTEN_PORT + 3)
    client.send_message("/resi/events/list", ["enc1"])
    client.send_message("/resi/events/current", ["enc1"])

    stopper.join(timeout=3.0)
    app.server.shutdown()

    addresses = [addr for addr, _ in received]
    assert "/resi/event/entry" in addresses
    assert "/resi/events/list/done" in addresses
    assert "/resi/event/current" in addresses

    entry = next(args for addr, args in received if addr == "/resi/event/entry")
    assert entry == ("enc1", "evt1", "Sunday 11am", "2026-09-10T14:00:00Z", True)

    current = next(args for addr, args in received if addr == "/resi/event/current")
    assert current == ("enc1", "evt1", "Sunday 11am", "2026-09-10T14:00:00Z")


def test_recent_events_round_trip():
    server, received, stopper = _collect_replies(REPLY_PORT + 4, count=2)

    app = OSCApp(
        FakeClient(),
        listen_host="127.0.0.1",
        listen_port=LISTEN_PORT + 4,
        reply_host="127.0.0.1",
        reply_port=REPLY_PORT + 4,
    )
    app_thread = threading.Thread(target=app.serve_forever, daemon=True)
    app_thread.start()
    time.sleep(0.2)

    client = SimpleUDPClient("127.0.0.1", LISTEN_PORT + 4)
    client.send_message("/resi/events/recent", [3650.0])

    stopper.join(timeout=3.0)
    app.server.shutdown()

    addresses = [addr for addr, _ in received]
    assert "/resi/recent_event/entry" in addresses
    assert "/resi/events/recent/done" in addresses

    entry = next(args for addr, args in received if addr == "/resi/recent_event/entry")
    assert entry == ("enc1", "evt1", "Sunday 11am", "2026-09-10T14:00:00Z")

    done = next(args for addr, args in received if addr == "/resi/events/recent/done")
    assert done == (3650.0, 1)
