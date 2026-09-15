"""Cue business logic: turns OSC-triggered read/create/update requests into
pyResi calls, with real-time correction for Resi's live streaming delay."""

from datetime import datetime, timezone

from pyResi import event_start_time, position_to_seconds, seconds_to_position


class EncoderNotLive(Exception):
    """Raised when a command needs a currently-live event on an encoder that
    isn't currently streaming."""


def _live_event(client, encoder_id):
    event = client.events.current_for_encoder(encoder_id)
    if event is None:
        raise EncoderNotLive(f"encoder {encoder_id!r} isn't currently streaming")
    return event


def read_cues(client, encoder_id):
    """The current live event's cues, as (uuid, position_seconds, name)
    tuples in timeline order."""
    event = _live_event(client, encoder_id)
    cues = client.cues.list(event['eventProfileId'], event['uuid'])
    return [
        (cue.get('uuid'), position_to_seconds(cue['position']), cue.get('name'))
        for cue in cues
    ]


def read_cues_for_event(client, event_id):
    """The given event's cues, as (uuid, position_seconds, name) tuples in
    timeline order — reads a specific event/video directly by its own id,
    bypassing which event is currently live on any encoder. Useful for
    checking a video you already have the id for (e.g. from its Studio
    URL), finished or not.
    """
    event = client.events.get(event_id)
    cues = client.cues.list(event['eventProfileId'], event['uuid'])
    return [
        (cue.get('uuid'), position_to_seconds(cue['position']), cue.get('name'))
        for cue in cues
    ]


def create_cue_now(client, encoder_id, name, *, now=None):
    """Create a cue at the real-world moment this was called (pass `now` to
    override, e.g. in tests), corrected for how far Resi's live playback is
    currently lagging behind real time.

    The correction matters because whatever triggers this is almost always
    reacting to something just watched on a delayed player: the real-world
    moment being marked happened `streaming_delay` seconds before the
    trigger fired, not at the instant it fired.
    """
    event = _live_event(client, encoder_id)
    now = now or datetime.now(timezone.utc)
    delay = client.events.streaming_delay(event)
    start = event_start_time(event)
    target_seconds = (now - start).total_seconds() - delay
    target_seconds = max(0.0, target_seconds)
    position = seconds_to_position(target_seconds)
    return client.cues.create(event['eventProfileId'], event['uuid'], position, name)


def create_cue_at(client, encoder_id, position_seconds, name):
    """Create a cue at an explicit timeline position (seconds from event
    start), bypassing create_cue_now's real-time delay correction entirely.

    For testing the create path independent of the delay measurement, or
    for a caller that already knows the exact timeline position it wants
    (e.g. replaying a cue sheet) rather than reacting to something
    happening right now.
    """
    event = _live_event(client, encoder_id)
    position = seconds_to_position(position_seconds)
    return client.cues.create(event['eventProfileId'], event['uuid'], position, name)


def update_cue(client, encoder_id, cue_id, position_seconds, name):
    """Move an existing cue to an absolute position (seconds) and/or rename
    it. Resi's PATCH replaces the whole cue, so both are always sent."""
    event = _live_event(client, encoder_id)
    position = seconds_to_position(position_seconds)
    client.cues.update(event['eventProfileId'], event['uuid'], cue_id, position, name)
    return position
