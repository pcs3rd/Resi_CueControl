"""Cue business logic: turns OSC-triggered read/create/update requests into
pyResi calls, with real-time correction for Resi's live streaming delay."""

import logging
from datetime import datetime, timezone

from pyResi import event_start_time, position_to_seconds, seconds_to_position

log = logging.getLogger('resi_cuecontrol.cues')


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


def create_cue_now(
    client, encoder_id, name, *, now=None, private_cue=True,
    correct_for_delay=False, buffer_segments=3,
):
    """Create a cue at the real-world moment this was called (pass `now` to
    override, e.g. in tests).

    Defaults to placing the cue at elapsed time since the event started,
    with NO delay correction — correct whenever whatever triggers this
    fires at the same real-world instant as the thing being marked (this
    project's actual trigger: ProPresenter firing this over MIDI the
    instant it starts playing a video). Live calibration against that
    exact trigger showed any delay subtraction at all — encode lag,
    decoder buffer, or both — made the cue land early by very close to
    whatever was subtracted; the streaming/decoder delay this project was
    originally built to correct for just isn't part of that path.

    Pass `correct_for_delay=True` for the OTHER scenario this project can
    still handle: an operator reacting to something they just watched on
    a delayed decoder, where the real-world moment being marked genuinely
    did happen some number of seconds before the trigger fired. In that
    mode the lag has two components, logged separately so a mismatch can
    be traced to one or the other: `streaming_delay` (encoder/CDN
    packaging lag, read off the manifest) and `decoder_buffer_delay` (the
    downstream decoder's own playback buffer, estimated as
    `buffer_segments` times the manifest's segment duration — see
    pyResi's docstrings for both). Neither is a measured constant, and
    hasn't been calibrated against a real reacting-to-a-delayed-decoder
    workflow — only against the simultaneous-trigger one, where the
    answer turned out to be "don't correct at all".

    `private_cue` matches Resi's own field: True (the default, same as
    Studio's own cue editor) hides it from other viewers of the event;
    False makes it visible to them.
    """
    event = _live_event(client, encoder_id)
    now = now or datetime.now(timezone.utc)
    start = event_start_time(event)
    elapsed_seconds = (now - start).total_seconds()

    if correct_for_delay:
        encode_delay = client.events.streaming_delay(event)
        buffer_delay = client.events.decoder_buffer_delay(event, buffer_segments)
        delay = encode_delay + buffer_delay
        target_seconds = max(0.0, elapsed_seconds - delay)
        position = seconds_to_position(target_seconds)
        log.info(
            "auto time-adjusted cue %r on encoder %s: encode delay %.3fs + "
            "decoder buffer %.3fs (%s segments) = total delay %.3fs "
            "(elapsed %.3fs -> position %s)",
            name, encoder_id, encode_delay, buffer_delay, buffer_segments, delay,
            elapsed_seconds, position,
        )
    else:
        target_seconds = max(0.0, elapsed_seconds)
        position = seconds_to_position(target_seconds)
        log.info(
            "auto time cue %r on encoder %s: elapsed %.3fs -> position %s "
            "(no delay correction)",
            name, encoder_id, elapsed_seconds, position,
        )

    return client.cues.create(
        event['eventProfileId'], event['uuid'], position, name, private_cue=private_cue
    )


def create_cue_at(client, encoder_id, position_seconds, name, *, private_cue=True):
    """Create a cue at an explicit timeline position (seconds from event
    start), bypassing create_cue_now's real-time delay correction entirely.

    For testing the create path independent of the delay measurement, or
    for a caller that already knows the exact timeline position it wants
    (e.g. replaying a cue sheet) rather than reacting to something
    happening right now. `private_cue` has the same meaning as on
    create_cue_now.
    """
    event = _live_event(client, encoder_id)
    position = seconds_to_position(position_seconds)
    return client.cues.create(
        event['eventProfileId'], event['uuid'], position, name, private_cue=private_cue
    )


def update_cue(client, encoder_id, cue_id, position_seconds, name):
    """Move an existing cue to an absolute position (seconds) and/or rename
    it. Resi's PATCH replaces the whole cue, so both are always sent."""
    event = _live_event(client, encoder_id)
    position = seconds_to_position(position_seconds)
    client.cues.update(event['eventProfileId'], event['uuid'], cue_id, position, name)
    return position
