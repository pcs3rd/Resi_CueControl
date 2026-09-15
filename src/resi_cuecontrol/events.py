"""Event ("video") discovery — listing an encoder's events and finding
whichever one is currently live, mirroring Studio's "Encoder Videos" list."""

from datetime import datetime, timedelta, timezone

from pyResi import event_start_time


def list_events(client, encoder_id):
    """All events for one encoder, newest first, as (uuid, name, start_time,
    active) tuples. `active` is True for whichever event is that encoder's
    current live one right now (at most one will be)."""
    status = client.encoders.status(encoder_id)
    current_event_id = status.get('currentEventId')
    events = client.events.for_encoder(encoder_id)
    events = sorted(events, key=lambda e: e.get('startTime') or '', reverse=True)
    return [
        (
            e.get('uuid'),
            e.get('name'),
            e.get('startTime'),
            e.get('uuid') == current_event_id,
        )
        for e in events
    ]


def current_event(client, encoder_id):
    """The encoder's current/active event as (uuid, name, start_time), or
    None if it isn't currently streaming."""
    event = client.events.current_for_encoder(encoder_id)
    if event is None:
        return None
    return (event.get('uuid'), event.get('name'), event.get('startTime'))


def recent_events(client, days):
    """Every event across the account that started within the last `days`
    days, as a dict of encoder_id -> [(uuid, name, start_time), ...],
    newest first within each encoder.

    One GET across the whole account (client.events.list()) filtered
    client-side on startTime — there's no server-side date filter on this
    endpoint, so this costs the same one request regardless of `days`.
    Events with no parseable startTime are skipped rather than guessed at.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    grouped = {}
    for event in client.events.list():
        start = event_start_time(event)
        if start is None or start < cutoff:
            continue
        grouped.setdefault(event.get('encoderId'), []).append(
            (event.get('uuid'), event.get('name'), event.get('startTime'))
        )
    for entries in grouped.values():
        entries.sort(key=lambda entry: entry[2] or '', reverse=True)
    return grouped
