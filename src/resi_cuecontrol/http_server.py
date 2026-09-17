"""HTTP REST front-end for Resi_CueControl — the same operations as
osc_server.py's OSC interface, exposed as JSON over HTTP.

This exists because OSC's untyped, space-delimited "multiple arguments"
field (as implemented by common OSC control surfaces, e.g. Bitfocus
Companion's generic OSC module) has no way to protect a string containing
spaces, and silently mis-types a string that happens to start with digits
(like a UUID) as a truncated number. A JSON request body has neither
problem — every value keeps its real type and content, always.

Routes, all JSON in and out:

    GET   /encoders                     -> [{"encoder_id", "name", "live"}, ...]
    GET   /encoders/<encoder_id>/events  -> [{"event_id", "name", "start_time", "active"}, ...]
    GET   /encoders/<encoder_id>/current -> {"event_id", "name", "start_time"} (409 if not live)
    GET   /events/recent?days=<days>     -> {"<encoder_id>": [{"event_id", "name", "start_time"}, ...], ...}
    GET   /encoders/<encoder_id>/cues    -> [{"cue_id", "position_seconds", "name"}, ...]
    GET   /events/<event_id>/cues        -> same shape, by event id directly
    POST  /cues                          -> create a cue
    PATCH /cues/<cue_id>                 -> move/rename a cue

POST /cues body: {"encoder_id": "...", "name": "...", "visible": true,
"position_seconds": 12.3}. `position_seconds` is optional, with the same
auto-time-vs-explicit-position behavior as the OSC /resi/cue/create
command (see cues.create_cue_now()'s docstring): omit it to place the cue
at the moment this request arrived (delay-corrected per the usual rules),
include it for an exact timeline position instead. `visible` maps to
Resi's own `privateCue` field, inverted, same as the OSC side.

PATCH /cues/<cue_id> body: {"encoder_id": "...", "position_seconds": 12.3,
"name": "..."} — same shape as the OSC /resi/cue/update command.

Every error response is JSON: {"error": "<message>"}, with a 4xx/5xx
status rather than a 200 with an error field, so callers can branch on
status code without parsing the body. An encoder that exists but isn't
currently live comes back as 409 Conflict, not 404, since the encoder
itself was found — only the "no route matched" case and a genuinely
unknown resource use 404.

If HTTP_API_KEY is set (see __init__.main()), every request must include
a matching `X-API-Key` header, checked with a constant-time comparison;
a missing or wrong key gets 401 Unauthorized rather than reaching any
route. If HTTP_API_KEY is unset, the API takes no requests unauthenticated
at all — see make_server()'s api_key parameter.
"""

import hmac
import json
import logging
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from pyResi import position_to_seconds

from . import cues, encoders, events
from .cues import EncoderNotLive

log = logging.getLogger('resi_cuecontrol.http')


class _HTTPError(Exception):
    """Raised by a route to produce a specific HTTP status + JSON message,
    instead of the generic 500 an unexpected exception gets."""

    def __init__(self, status, message):
        super().__init__(message)
        self.status = status
        self.message = message


def make_server(
    client,
    *,
    listen_host='0.0.0.0',
    listen_port=8080,
    correct_for_delay=False,
    buffer_segments=3,
    offset_seconds=0.0,
    api_key=None,
):
    """A ThreadingHTTPServer ready for serve_forever() — see __init__.main(),
    which runs this in a background thread alongside the OSC server.

    `api_key`, when set, requires every request to carry a matching
    `X-API-Key` header (constant-time compared) or get 401 Unauthorized
    before any route runs. When left as None, every request is accepted
    unauthenticated — fine for a trusted internal network, not otherwise."""

    class Handler(BaseHTTPRequestHandler):
        server_version = 'resi-cuecontrol-http/1.0'

        def log_message(self, fmt, *args):
            log.info('%s - %s', self.address_string(), fmt % args)

        # ---------- plumbing ----------

        def _send_json(self, status, payload):
            body = json.dumps(payload).encode('utf-8')
            self.send_response(status)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _read_json_body(self):
            length = int(self.headers.get('Content-Length') or 0)
            if length == 0:
                return {}
            raw = self.rfile.read(length)
            try:
                return json.loads(raw)
            except json.JSONDecodeError as exc:
                raise _HTTPError(HTTPStatus.BAD_REQUEST, f'invalid JSON body: {exc}')

        def _require(self, body, *fields):
            missing = [f for f in fields if f not in body]
            if missing:
                raise _HTTPError(
                    HTTPStatus.BAD_REQUEST, f"missing field(s): {', '.join(missing)}"
                )

        def _authorized(self):
            if api_key is None:
                return True
            provided = self.headers.get('X-API-Key')
            return provided is not None and hmac.compare_digest(provided, api_key)

        def _handle(self, method):
            if not self._authorized():
                self._send_json(
                    HTTPStatus.UNAUTHORIZED,
                    {'error': 'missing or invalid X-API-Key header'},
                )
                return
            parsed = urlparse(self.path)
            parts = [p for p in parsed.path.split('/') if p]
            query = parse_qs(parsed.query)
            try:
                status, payload = self._route(method, parts, query)
                self._send_json(status, payload)
            except _HTTPError as exc:
                self._send_json(exc.status, {'error': exc.message})
            except EncoderNotLive as exc:
                self._send_json(HTTPStatus.CONFLICT, {'error': str(exc)})
            except Exception as exc:
                log.error('unhandled error on %s %s: %s', method, self.path, exc)
                self._send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {'error': str(exc)})

        def do_GET(self):
            self._handle('GET')

        def do_POST(self):
            self._handle('POST')

        def do_PATCH(self):
            self._handle('PATCH')

        # ---------- routing ----------

        def _route(self, method, parts, query):
            if method == 'GET' and parts == ['encoders']:
                return HTTPStatus.OK, [
                    {'encoder_id': eid, 'name': name, 'live': live}
                    for eid, name, live in encoders.list_encoders(client)
                ]

            if (
                method == 'GET'
                and len(parts) == 3
                and parts[0] == 'encoders'
                and parts[2] == 'events'
            ):
                encoder_id = parts[1]
                return HTTPStatus.OK, [
                    {'event_id': eid, 'name': name, 'start_time': start, 'active': active}
                    for eid, name, start, active in events.list_events(client, encoder_id)
                ]

            if (
                method == 'GET'
                and len(parts) == 3
                and parts[0] == 'encoders'
                and parts[2] == 'current'
            ):
                encoder_id = parts[1]
                current = events.current_event(client, encoder_id)
                if current is None:
                    raise _HTTPError(
                        HTTPStatus.CONFLICT,
                        f"encoder {encoder_id!r} isn't currently streaming",
                    )
                eid, name, start = current
                return HTTPStatus.OK, {'event_id': eid, 'name': name, 'start_time': start}

            if method == 'GET' and parts == ['events', 'recent']:
                days_raw = (query.get('days') or ['1'])[0]
                try:
                    days = float(days_raw)
                except ValueError:
                    raise _HTTPError(
                        HTTPStatus.BAD_REQUEST, f'invalid days value: {days_raw!r}'
                    )
                grouped = events.recent_events(client, days)
                return HTTPStatus.OK, {
                    encoder_id: [
                        {'event_id': eid, 'name': name, 'start_time': start}
                        for eid, name, start in entries
                    ]
                    for encoder_id, entries in grouped.items()
                }

            if (
                method == 'GET'
                and len(parts) == 3
                and parts[0] == 'encoders'
                and parts[2] == 'cues'
            ):
                encoder_id = parts[1]
                entries = cues.read_cues(client, encoder_id)
                return HTTPStatus.OK, [
                    {'cue_id': cid, 'position_seconds': pos, 'name': name}
                    for cid, pos, name in entries
                ]

            if (
                method == 'GET'
                and len(parts) == 3
                and parts[0] == 'events'
                and parts[2] == 'cues'
            ):
                event_id = parts[1]
                entries = cues.read_cues_for_event(client, event_id)
                return HTTPStatus.OK, [
                    {'cue_id': cid, 'position_seconds': pos, 'name': name}
                    for cid, pos, name in entries
                ]

            if method == 'POST' and parts == ['cues']:
                # Capture arrival time before doing anything else (parsing
                # the body, resolving the live event) — same reasoning as
                # osc_server._on_create: using "now" any later would bake
                # that work's latency into the cue position.
                received_at = datetime.now(timezone.utc)
                body = self._read_json_body()
                self._require(body, 'encoder_id', 'name', 'visible')
                encoder_id = body['encoder_id']
                name = body['name']
                private_cue = not body['visible']
                position_seconds = body.get('position_seconds')
                if position_seconds is None:
                    cue = cues.create_cue_now(
                        client,
                        encoder_id,
                        name,
                        now=received_at,
                        private_cue=private_cue,
                        correct_for_delay=correct_for_delay,
                        buffer_segments=buffer_segments,
                        offset_seconds=offset_seconds,
                    )
                else:
                    cue = cues.create_cue_at(
                        client, encoder_id, position_seconds, name, private_cue=private_cue
                    )
                if cue is None:
                    raise _HTTPError(
                        HTTPStatus.INTERNAL_SERVER_ERROR,
                        'cue created but could not be read back to confirm',
                    )
                return HTTPStatus.CREATED, {
                    'cue_id': cue.get('uuid') or '',
                    'position_seconds': position_to_seconds(cue['position']),
                    'name': name,
                }

            if method == 'PATCH' and len(parts) == 2 and parts[0] == 'cues':
                cue_id = parts[1]
                body = self._read_json_body()
                self._require(body, 'encoder_id', 'position_seconds', 'name')
                position = cues.update_cue(
                    client, body['encoder_id'], cue_id, body['position_seconds'], body['name']
                )
                return HTTPStatus.OK, {
                    'cue_id': cue_id,
                    'position_seconds': position,
                    'name': body['name'],
                }

            raise _HTTPError(HTTPStatus.NOT_FOUND, f'no route for {method} {self.path}')

    return ThreadingHTTPServer((listen_host, listen_port), Handler)
