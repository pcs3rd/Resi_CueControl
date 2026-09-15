"""OSC front-end for Resi_CueControl.

Listens for three commands and translates them into pyResi calls:

    /resi/cue/read      <encoder_id>
    /resi/cue/create    <encoder_id> <name>
    /resi/cue/create_at <encoder_id> <position_seconds> <name>
    /resi/cue/update    <encoder_id> <cue_id> <position_seconds> <name>
    /resi/encoders/list  (no args)
    /resi/events/list    <encoder_id>
    /resi/events/current <encoder_id>

The address strings and argument order here are placeholders — rename them
to match whatever's actually sending the OSC (Companion, a lighting
console, etc.); nothing else in this project depends on the exact
spelling, just the encoder_id/name/cue_id/position_seconds shapes.

Every command sends a reply to a fixed target (OSC_REPLY_HOST /
OSC_REPLY_PORT) rather than back to the sender's address, since the usual
setup here is fixed IPs on both ends. On error, callers get
`/resi/cue/error <encoder_id> <message>` instead of silence.
"""

import logging

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
        reply_host='127.0.0.1',
        reply_port=9001,
    ):
        self.client = client
        self.reply = SimpleUDPClient(reply_host, reply_port)

        dispatcher = Dispatcher()
        dispatcher.map('/resi/cue/read', self._on_read)
        dispatcher.map('/resi/cue/create', self._on_create)
        dispatcher.map('/resi/cue/create_at', self._on_create_at)
        dispatcher.map('/resi/cue/update', self._on_update)
        dispatcher.map('/resi/encoders/list', self._on_list_encoders)
        dispatcher.map('/resi/events/list', self._on_list_events)
        dispatcher.map('/resi/events/current', self._on_current_event)
        dispatcher.set_default_handler(self._on_unmatched)

        self.server = BlockingOSCUDPServer((listen_host, listen_port), dispatcher)

    def serve_forever(self):
        log.info('listening on %s', self.server.server_address)
        self.server.serve_forever()

    # ---------- handlers ----------
    # python-osc calls each of these as handler(address, *osc_args) — the
    # dispatcher itself does no argument validation, so a malformed message
    # (wrong arg count/type) surfaces here as a TypeError, caught below and
    # reported the same way as any other failure.

    def _on_read(self, address, encoder_id):
        try:
            entries = cues.read_cues(self.client, encoder_id)
        except Exception as exc:
            self._error(encoder_id, str(exc))
            return
        for cue_id, position_seconds, name in entries:
            self.reply.send_message(
                '/resi/cue/entry', [encoder_id, cue_id, position_seconds, name or '']
            )
        self.reply.send_message('/resi/cue/read/done', [encoder_id, len(entries)])

    def _on_create(self, address, encoder_id, name):
        try:
            cue = cues.create_cue_now(self.client, encoder_id, name)
        except Exception as exc:
            self._error(encoder_id, str(exc))
            return
        if cue is None:
            self._error(encoder_id, 'cue created but could not be read back to confirm')
            return
        self.reply.send_message(
            '/resi/cue/created',
            [encoder_id, cue.get('uuid') or '', position_to_seconds(cue['position']), name],
        )

    def _on_create_at(self, address, encoder_id, position_seconds, name):
        try:
            cue = cues.create_cue_at(self.client, encoder_id, position_seconds, name)
        except Exception as exc:
            self._error(encoder_id, str(exc))
            return
        if cue is None:
            self._error(encoder_id, 'cue created but could not be read back to confirm')
            return
        self.reply.send_message(
            '/resi/cue/created',
            [encoder_id, cue.get('uuid') or '', position_to_seconds(cue['position']), name],
        )

    def _on_update(self, address, encoder_id, cue_id, position_seconds, name):
        try:
            position = cues.update_cue(self.client, encoder_id, cue_id, position_seconds, name)
        except Exception as exc:
            self._error(encoder_id, str(exc))
            return
        self.reply.send_message('/resi/cue/updated', [encoder_id, cue_id, position, name])

    def _on_list_encoders(self, address):
        try:
            entries = encoders.list_encoders(self.client)
        except Exception as exc:
            self._error('', str(exc))
            return
        for encoder_id, name, live in entries:
            self.reply.send_message(
                '/resi/encoder/entry', [encoder_id or '', name or '', live]
            )
        self.reply.send_message('/resi/encoders/list/done', [len(entries)])

    def _on_list_events(self, address, encoder_id):
        try:
            entries = events.list_events(self.client, encoder_id)
        except Exception as exc:
            self._error(encoder_id, str(exc))
            return
        for event_id, name, start_time, active in entries:
            self.reply.send_message(
                '/resi/event/entry',
                [encoder_id, event_id or '', name or '', start_time or '', active],
            )
        self.reply.send_message('/resi/events/list/done', [encoder_id, len(entries)])

    def _on_current_event(self, address, encoder_id):
        try:
            current = events.current_event(self.client, encoder_id)
        except Exception as exc:
            self._error(encoder_id, str(exc))
            return
        if current is None:
            self._error(encoder_id, f"encoder {encoder_id!r} isn't currently streaming")
            return
        event_id, name, start_time = current
        self.reply.send_message(
            '/resi/event/current', [encoder_id, event_id or '', name or '', start_time or '']
        )

    def _on_unmatched(self, address, *args):
        log.warning('unhandled OSC address %s %r', address, args)

    def _error(self, encoder_id, message):
        log.error('%s: %s', encoder_id, message)
        self.reply.send_message('/resi/cue/error', [encoder_id, message])
