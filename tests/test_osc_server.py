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


class FakeEventsAPI:
    def current_for_encoder(self, encoder_id):
        return {"uuid": "evt1", "eventProfileId": "p1", "startTime": "2026-09-10T14:00:00Z"}

    def streaming_delay(self, event):
        return 0.0


class FakeClient:
    def __init__(self):
        self.events = FakeEventsAPI()
        self.cues = FakeCuesAPI()


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
    server, received, stopper = _collect_replies(REPLY_PORT, count=4)

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
    client.send_message("/resi/cue/update", ["enc1", "c1", 42.0, "Renamed"])

    stopper.join(timeout=3.0)
    app.server.shutdown()

    addresses = [addr for addr, _ in received]
    assert "/resi/cue/entry" in addresses
    assert "/resi/cue/read/done" in addresses
    assert "/resi/cue/created" in addresses
    assert "/resi/cue/updated" in addresses

    entry = next(args for addr, args in received if addr == "/resi/cue/entry")
    assert entry == ("enc1", "c1", 5.0, "Start")

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
