"""Resi_CueControl — cue control tooling for Resi Central events, built on pyResi.

This is a starting point, not a finished tool: `main()` just authenticates
and lists the account's channels, as a smoke test that the pyResi dependency
is wired up correctly. Build the actual cue-placement logic from here — see
https://github.com/pcs3rd/pyResi for the client this depends on, and note
that frame-accurate cue placement needs a calibrated delta (measured per
encoder) before positions can be trusted to the frame; see the project
README for where that stands.
"""

import os
import sys

from pyResi import pyResi


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
    client = _client()
    channels = client.channels.list()
    if not channels:
        print("Connected — no channels found on this account.")
        return
    print(f"Connected. {len(channels)} channel(s):")
    for ch in channels:
        print(f"  - {ch.get('name')} ({ch.get('uuid')})")


if __name__ == "__main__":
    main()
