"""OSC front-end for Resi_CueControl.

Listens for three commands and translates them into pyResi calls:

    /resi/cue/read       <encoder_id>
    /resi/cue/read_event <event_id>
    /resi/cue/create    <encoder_id> <name> <visible> [position_seconds]
    /resi/cue/update    <encoder_id> <cue_id> <position_seconds> <name>
    /resi/encoders/list  (no args)
    /resi/events/list    <encoder_id>
    /resi/events/current <encoder_id>
    /resi/events/recent  <days>

The address strings and argument order here are placeholders — rename them
to match whatever's actually sending the OSC (Companion, a lighting
console, etc.); nothing else in this project depends on the exact
spelling, just the encoder_id/name/cue_id/position_seconds shapes.

`visible` (bool) maps to Resi's own `privateCue` field, inverted —
`False` (matching Studio's own cue editor default) hides the cue from
other viewers of the event; `True` makes it visible to them.

`/resi/cue/create`'s trailing `position_seconds` is optional: omit it and
the cue lands at the moment the OSC message arrived, delay-corrected;
include it and that exact timeline position is used verbatim instead,
with no delay correction and no dependence on request timing at all.

Delay correction for the auto-time path is OFF by default — this
project's actual trigger (ProPresenter over MIDI) fires at the same
instant as the thing being marked, and live calibration showed any delay
subtraction there just makes the cue land early. Set CORRECT_FOR_DELAY=true
(an environment variable, see __init__.py's main()) only for a trigger
that reacts to something seen on a delayed decoder. Its decoder-buffer
component is an estimate (`buffer_segments`, default 3 — see
cues.create_cue_now()) rather than a measured constant, tunable via
DECODER_BUFFER_SEGMENTS.

CUE_OFFSET_SECONDS (default 0) is a separate, flat manual nudge applied
regardless of CORRECT_FOR_DELAY — positive moves every auto-time cue
earlier, negative moves it later. Use this for small real-world
adjustments (e.g. "it's landing half a second late") rather than
recalibrating the delay-correction math itself.

Every command replies to the IP address the request came from, on a
fixed OSC_REPLY_PORT — the reply target is inferred from each incoming
packet's source address rather than configured as a separate fixed host,
since this deployment doesn't want to hardcode a reply host per instance.
This relies on the sender listening for replies on OSC_REPLY_PORT at its
own IP; if whatever sends these commands uses a different local port for
listening than the one it sends from, replies won't reach it — confirm
your OSC sender binds a single fixed port for both send and receive
before relying on this. On error, callers get
`/resi/cue/error <encoder_id> <message>` instead of silence.
"""

import logging
from datetime import datetime, timezone

from pythonosc.dispatcher import Dispatcher
from pythonosc.osc_server import BlockingOSCUDPServer
from pythonosc.udp_client import SimpleUDPClient

from pyResi import position_to_seconds

from . import cues, encoders, events

log = logging.getLogger('resi_cuecontrol.osc')


class OSCApp:
    def __init__(
        self,
        client,
        listen_host='0.0.0.0',
        listen_port=9000,
        reply_port=9001,
        correct_for_delay=False,
        buffer_segments=3,
        offset_seconds=0.0,
    ):
        self.client = client
        # Reply target is inferred per-request from the sender's IP (see
        # _reply_for/needs_reply_address below) rather than fixed at
        # startup — only the port is fixed.
        self.reply_port = reply_port
        # Whether /resi/cue/create's auto-time path subtracts streaming/
        # decoder delay at all — see cues.create_cue_now()'s docstring.
        # Defaults to False: this project's actual trigger (ProPresenter
        # over MIDI) fires at the same instant as the thing being marked,
        # and live calibration showed any delay subtraction there just
        # makes the cue land early. Set CORRECT_FOR_DELAY=true only for a
        # trigger that reacts to something seen on a delayed decoder.
        self.correct_for_delay = correct_for_delay
        # How many manifest segments' worth of playback buffering to assume
        # a downstream decoder holds before it renders anything, on top of
        # the encode/CDN lag pyResi reads off the manifest directly. Only
        # used when correct_for_delay is True. A rough, calibratable
        # estimate, not a measured constant; tune via the
        # DECODER_BUFFER_SEGMENTS environment variable rather than editing
        # this default.
        self.buffer_segments = buffer_segments
        # A flat manual nudge applied to every auto-time cue after
        # whatever else this computes — positive moves the cue earlier,
        # negative moves it later. For fine-tuning against real-world
        # observation once everything else is already calibrated; tune
        # via the CUE_OFFSET_SECONDS environment variable.
        self.offset_seconds = offset_seconds

        dispatcher = Dispatcher()
        dispatcher.map('/resi/cue/read', self._on_read, needs_reply_address=True)
        dispatcher.map('/resi/cue/read_event', self._on_read_event, needs_reply_address=True)
        dispatcher.map('/resi/cue/create', self._on_create, needs_reply_address=True)
        dispatcher.map('/resi/cue/update', self._on_update, needs_reply_address=True)
        dispatcher.map('/resi/encoders/list', self._on_list_encoders, needs_reply_address=True)
        dispatcher.map('/resi/events/list', self._on_list_events, needs_reply_address=True)
        dispatcher.map('/resi/events/current', self._on_current_event, needs_reply_address=True)
        dispatcher.map('/resi/events/recent', self._on_recent_events, needs_reply_address=True)
        dispatcher.set_default_handler(self._on_unmatched)

        self.server = BlockingOSCUDPServer((listen_host, listen_port), dispatcher)

    def serve_forever(self):
        log.info('listening on %s', self.server.server_address)
        self.server.serve_forever()

    def _reply_for(self, client_address):
        """A reply client aimed at whoever just sent us a request: their
        IP (from the incoming packet), our fixed reply port. Built fresh
        per-request rather than cached, since the sender's IP can differ
        from one message to the next."""
        return SimpleUDPClient(client_address[0], self.reply_port)

    # ---------- handlers ----------
    # python-osc calls each of these as handler(client_address, address,
    # *osc_args) since every mapping above sets needs_reply_address=True —
    # the dispatcher itself does no argument validation, so a malformed
    # message (wrong arg count/type) surfaces here as a TypeError, caught
    # below and reported the same way as any other failure.

    def _on_read(self, client_address, address, encoder_id):
        reply = self._reply_for(client_address)
        try:
            entries = cues.read_cues(self.client, encoder_id)
        except Exception as exc:
            self._error(reply, encoder_id, str(exc))
            return
        for cue_id, position_seconds, name in entries:
            reply.send_message(
                '/resi/cue/entry', [encoder_id, cue_id, position_seconds, name or '']
            )
        reply.send_message('/resi/cue/read/done', [encoder_id, len(entries)])

    def _on_read_event(self, client_address, address, event_id):
        reply = self._reply_for(client_address)
        try:
            entries = cues.read_cues_for_event(self.client, event_id)
        except Exception as exc:
            self._error(reply, event_id, str(exc))
            return
        for cue_id, position_seconds, name in entries:
            reply.send_message(
                '/resi/cue/entry', [event_id, cue_id, position_seconds, name or '']
            )
        reply.send_message('/resi/cue/read/done', [event_id, len(entries)])

    def _on_create(self, client_address, address, encoder_id, name, visible, *rest):
        # Capture the arrival time before doing anything else — resolving
        # the live event below is a network round trip, and using "now" at
        # that later point would bake its latency into the cue position.
        received_at = datetime.now(timezone.utc)
        reply = self._reply_for(client_address)
        position_seconds = rest[0] if rest else None
        private_cue = not visible
        try:
            if position_seconds is None:
                cue = cues.create_cue_now(
                    self.client,
                    encoder_id,
                    name,
                    now=received_at,
                    private_cue=private_cue,
                    correct_for_delay=self.correct_for_delay,
                    buffer_segments=self.buffer_segments,
                    offset_seconds=self.offset_seconds,
                )
            else:
                cue = cues.create_cue_at(
                    self.client, encoder_id, position_seconds, name, private_cue=private_cue
                )
        except Exception as exc:
            self._error(reply, encoder_id, str(exc))
            return
        if cue is None:
            self._error(reply, encoder_id, 'cue created but could not be read back to confirm')
            return
        reply.send_message(
            '/resi/cue/created',
            [encoder_id, cue.get('uuid') or '', position_to_seconds(cue['position']), name],
        )

    def _on_update(self, client_address, address, encoder_id, cue_id, position_seconds, name):
        reply = self._reply_for(client_address)
        try:
            position = cues.update_cue(self.client, encoder_id, cue_id, position_seconds, name)
        except Exception as exc:
            self._error(reply, encoder_id, str(exc))
            return
        reply.send_message('/resi/cue/updated', [encoder_id, cue_id, position, name])

    def _on_list_encoders(self, client_address, address):
        reply = self._reply_for(client_address)
        try:
            entries = encoders.list_encoders(self.client)
        except Exception as exc:
            self._error(reply, '', str(exc))
            return
        for encoder_id, name, live in entries:
            reply.send_message(
                '/resi/encoder/entry', [encoder_id or '', name or '', live]
            )
        reply.send_message('/resi/encoders/list/done', [len(entries)])

    def _on_list_events(self, client_address, address, encoder_id):
        reply = self._reply_for(client_address)
        try:
            entries = events.list_events(self.client, encoder_id)
        except Exception as exc:
            self._error(reply, encoder_id, str(exc))
            return
        for event_id, name, start_time, active in entries:
            reply.send_message(
                '/resi/event/entry',
                [encoder_id, event_id or '', name or '', start_time or '', active],
            )
        reply.send_message('/resi/events/list/done', [encoder_id, len(entries)])

    def _on_current_event(self, client_address, address, encoder_id):
        reply = self._reply_for(client_address)
        try:
            current = events.current_event(self.client, encoder_id)
        except Exception as exc:
            self._error(reply, encoder_id, str(exc))
            return
        if current is None:
            self._error(reply, encoder_id, f"encoder {encoder_id!r} isn't currently streaming")
            return
        event_id, name, start_time = current
        reply.send_message(
            '/resi/event/current', [encoder_id, event_id or '', name or '', start_time or '']
        )

    def _on_recent_events(self, client_address, address, days):
        reply = self._reply_for(client_address)
        try:
            grouped = events.recent_events(self.client, days)
        except Exception as exc:
            self._error(reply, '', str(exc))
            return
        total = 0
        for encoder_id, entries in grouped.items():
            for event_id, name, start_time in entries:
                reply.send_message(
                    '/resi/recent_event/entry',
                    [encoder_id or '', event_id or '', name or '', start_time or ''],
                )
                total += 1
        reply.send_message('/resi/events/recent/done', [days, total])

    def _on_unmatched(self, address, *args):
        log.warning('unhandled OSC address %s %r', address, args)

    def _error(self, reply, encoder_id, message):
        log.error('%s: %s', encoder_id, message)
        reply.send_message('/resi/cue/error', [encoder_id, message])
