"""Resi_CueControl — OSC-driven cue control for Resi Central events, built
on pyResi.

`main()` starts an OSC server (see osc_server.py for the three commands it
understands: read / create / update) against one Resi account,
authenticated via RESI_TOKEN or RESI_USERNAME/RESI_PASSWORD.
"""

import logging
import os
import sys

from pyResi import pyResi

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
        correct_for_delay=os.environ.get("CORRECT_FOR_DELAY", "false").lower()
        in ("1", "true", "yes"),
        # How many manifest segments' worth of decoder-side playback
        # buffering to correct auto-time cues for, on top of the measured
        # encoder/CDN lag, when CORRECT_FOR_DELAY is on. A rough estimate
        # (3 segments), not a measured constant for any specific decoder —
        # tune this against how far off a real cue lands from what your
        # decoder actually shows (see README's testing/calibration
        # section) rather than guessing.
        buffer_segments=float(os.environ.get("DECODER_BUFFER_SEGMENTS", "3")),
        # Flat manual nudge on every auto-time cue, applied regardless of
        # CORRECT_FOR_DELAY — positive moves it earlier, negative later.
        # For small real-world corrections once everything else is
        # already calibrated (see cues.create_cue_now()'s docstring).
        offset_seconds=float(os.environ.get("CUE_OFFSET_SECONDS", "0")),
    )
    app.serve_forever()


if __name__ == "__main__":
    main()
