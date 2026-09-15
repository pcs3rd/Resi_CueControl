"""Event ("video") discovery — listing an encoder's events and finding
whichever one is currently live, mirroring Studio's "Encoder Videos" list."""


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
