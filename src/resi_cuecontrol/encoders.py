"""Encoder discovery — lists the account's encoders and whether each is
currently live, mainly so you have real encoder_id values to pass to the
cue commands instead of guessing."""


def list_encoders(client):
    """All encoders on the account, as (uuid, name, live) tuples.

    `live` costs one extra status request per encoder (there's no bulk
    "wide" field for it on the list endpoint), so this is meant for
    occasional discovery/debugging use, not a hot path.
    """
    result = []
    for enc in client.encoders.list():
        encoder_id = enc.get('uuid')
        name = enc.get('name')
        live = False
        if encoder_id:
            status = client.encoders.status(encoder_id)
            live = bool(status.get('currentEventId'))
        result.append((encoder_id, name, live))
    return result
