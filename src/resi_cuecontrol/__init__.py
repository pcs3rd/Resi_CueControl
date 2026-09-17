"""Resi_CueControl — OSC-driven cue control for Resi Central events, built
on pyResi.

`main()` starts an OSC server (see osc_server.py for the commands it
understands: read / create / update / list, etc.) and, unless disabled, an
HTTP REST server (see http_server.py) offering the same operations as
plain JSON — useful for callers like Bitfocus Companion's HTTP Request
action, where OSC's untyped, space-delimited argument fields are awkward
for values that contain spaces or start with digits (a cue name, an
encoder UUID). Both run against the same Resi account, authenticated via
RESI_TOKEN or RESI_USERNAME/RESI_PASSWORD.
"""

import logging
import os
import sys
import threading

from pyResi import pyResi

from .http_server import make_server as make_http_server
from .osc_server import OSCApp


def _client() -> pyResi:
    token = os.environ.get("RESI_TOKEN")
    if token:
        return pyResi(token=token)

    username = os.environ.get("RESI_USERNAME")
    password = os.environ.get("RESI_PASSWORD")
    if not (username and password):
        sys.exit(
            "Set RESI_TOKEN, or both RESI_USERNAME and RESI_PASSWORD, "
            "in the environment."
        )
    return pyResi(username=username, password=password)


def main() -> None:
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
    client = _client()

    # Shared between the OSC and HTTP interfaces, so a cue created through
    # either one behaves identically.
    correct_for_delay = os.environ.get("CORRECT_FOR_DELAY", "false").lower() in (
        "1",
        "true",
        "yes",
    )
    buffer_segments = float(os.environ.get("DECODER_BUFFER_SEGMENTS", "3"))
    offset_seconds = float(os.environ.get("CUE_OFFSET_SECONDS", "0"))

    if os.environ.get("HTTP_ENABLED", "true").lower() in ("1", "true", "yes"):
        api_key = os.environ.get("HTTP_API_KEY")
        http_server = make_http_server(
            client,
            listen_host=os.environ.get("HTTP_LISTEN_HOST", "0.0.0.0"),
            listen_port=int(os.environ.get("HTTP_LISTEN_PORT", "8080")),
            correct_for_delay=correct_for_delay,
            buffer_segments=buffer_segments,
            offset_seconds=offset_seconds,
            api_key=api_key,
        )
        http_log = logging.getLogger("resi_cuecontrol.http")
        http_log.info("listening on %s", http_server.server_address)
        if api_key:
            http_log.info("X-API-Key authentication is required")
        else:
            http_log.warning(
                "HTTP_API_KEY is not set — the HTTP API is accepting requests "
                "with no authentication at all"
            )
        # Daemon thread: it dies with the process rather than keeping it
        # alive on its own, so Ctrl-C / container stop still works normally.
        threading.Thread(target=http_server.serve_forever, daemon=True).start()

    app = OSCApp(
        client,
        listen_host=os.environ.get("OSC_LISTEN_HOST", "0.0.0.0"),
        listen_port=int(os.environ.get("OSC_LISTEN_PORT", "9000")),
        # Reply target is inferred per-request from the sender's IP (see
        # osc_server.OSCApp._reply_for) rather than a configured host —
        # only the port is fixed.
        reply_port=int(os.environ.get("OSC_REPLY_PORT", "9001")),
        # Off by default: this project's actual trigger (ProPresenter over
        # MIDI) fires at the same real-world instant as the thing being
        # marked, and live calibration showed delay correction just makes
        # the cue land early in that case. Only turn this on for a trigger
        # that reacts to something seen on a delayed decoder.
        correct_for_delay=correct_for_delay,
        buffer_segments=buffer_segments,
        offset_seconds=offset_seconds,
    )
    app.serve_forever()


if __name__ == "__main__":
    main()
