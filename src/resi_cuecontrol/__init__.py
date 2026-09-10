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
        reply_host=os.environ.get("OSC_REPLY_HOST", "127.0.0.1"),
        reply_port=int(os.environ.get("OSC_REPLY_PORT", "9001")),
    )
    app.serve_forever()


if __name__ == "__main__":
    main()
