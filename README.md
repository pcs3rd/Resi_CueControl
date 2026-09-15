# Resi_CueControl

OSC-driven cue control for Resi Central events (`studio.resi.io`), built on
[pyResi](https://github.com/pcs3rd/pyResi).

## What it does

Starts a UDP OSC server (`resi-cuecontrol`) that understands four commands.
The three cue commands are all scoped to whichever encoder's *currently
live* event is running; `encoders/list` is how you find real `encoder_id`
values to pass to them:

| Address | Args | Effect |
|---|---|---|
| `/resi/cue/read` | `encoder_id` | Lists the live event's cues |
| `/resi/cue/read_event` | `event_id` | Lists a specific event's cues directly, by its own id |
| `/resi/cue/create` | `encoder_id, name, visible, [position_seconds]` | Creates a cue — see below for the two modes |
| `/resi/cue/update` | `encoder_id, cue_id, position_seconds, name` | Moves/renames an existing cue |
| `/resi/encoders/list` | *(none)* | Lists every encoder on the account |
| `/resi/events/list` | `encoder_id` | Lists that encoder's events ("videos"), newest first |
| `/resi/events/current` | `encoder_id` | The encoder's current/active event, if it's live |
| `/resi/events/recent` | `days` | Every event across the account started in the last `days` days, grouped by encoder |

Every command replies to a fixed target (`OSC_REPLY_HOST`/`OSC_REPLY_PORT`)
rather than back to the sender — `/resi/cue/entry`, `/resi/cue/read/done`,
`/resi/cue/created`, `/resi/cue/updated`, `/resi/encoder/entry` +
`/resi/encoders/list/done`, `/resi/event/entry` + `/resi/events/list/done`,
`/resi/event/current`, or `/resi/cue/error` on failure.
`/resi/cue/read_event` is the one to reach for when you already have a
specific event id — e.g. from its Studio URL
(`studio.resi.io/media/encoder-videos/<event id>`) — since it looks that
event up directly rather than resolving whichever one is currently live
on an encoder; it replies on the same `/resi/cue/entry` /
`/resi/cue/read/done` addresses, with the event id in the encoder_id slot.

`/resi/cue/create`'s `visible` argument (bool) maps to Resi's own
`privateCue` field, inverted: `False` (matching Studio's own cue editor
default) hides the cue from other viewers of the event; `True` makes it
visible to them.

Its fourth argument, `position_seconds`, is optional and switches between
two different things, not just two ways of specifying the same one:

- **Omitted** — the cue is placed at *the moment the OSC message arrived*
  at `resi-cuecontrol`, delay-corrected (see below). This is the normal
  "mark this live moment" usage.
- **Given** (`position_seconds`) — the cue is placed at that exact
  timeline position, verbatim, with no delay correction and no
  dependence on request timing at all. Use this for testing cue creation
  itself, or for replaying a cue sheet where you already know the exact
  positions you want.

Both modes reply on the same `/resi/cue/created` address.

The address strings and argument order are placeholders: rename them in
`src/resi_cuecontrol/osc_server.py` to match whatever's actually sending the
OSC (Companion, a lighting console, etc.) — nothing else depends on the
exact spelling.

## Finding encoder IDs

Send `/resi/encoders/list` (no args) and you'll get back one
`/resi/encoder/entry <uuid> <name> <live>` per encoder on the account,
followed by `/resi/encoders/list/done <count>`. `live` is `True` when that
encoder currently has an event running — that's the `encoder_id` to use
with the cue commands above.


## Finding events ("videos")

Resi calls a recorded or in-progress broadcast an "event"; Studio's own UI
calls the same thing a "video" (as in "Encoder Videos") — this project
uses Resi's own field/endpoint name, "event", throughout.

`/resi/events/list <encoder_id>` replies with one
`/resi/event/entry <encoder_id> <uuid> <name> <start_time> <active>` per
event on that encoder (newest first), then
`/resi/events/list/done <encoder_id> <count>`. `active` is `True` for
whichever one is that encoder's current live event right now.

`/resi/events/current <encoder_id>` skips straight to that one: replies
with `/resi/event/current <encoder_id> <uuid> <name> <start_time>`, or
`/resi/cue/error` if the encoder isn't currently streaming.

`/resi/events/recent <days>` is account-wide rather than per-encoder — it
fetches every event (one request, no server-side date filter exists) and
keeps whichever ones started within the last `days` days, grouped by
encoder. Replies with one
`/resi/recent_event/entry <encoder_id> <uuid> <name> <start_time>` per
matching event, then `/resi/events/recent/done <days> <count>`.

## Why cue creation corrects for delay

Resi's live playback lags real time, in two separate stages:

1. **Encoder/CDN packaging lag** — encoder buffering, segmenting, CDN
   propagation. `event['hlsUrl']` itself is a master/variant playlist, not
   the manifest with actual segments in it — `pyResi.fetch_manifest()`
   follows the first variant it lists to get the real media playlist, then
   `events.streaming_delay()` compares the request time against the
   absolute time of the most recently encoded segment using its
   `EXT-X-PROGRAM-DATE-TIME` tags (confirmed present on real Resi
   manifests; falls back to `event.startTime` plus summed segment
   durations if they're ever absent).
2. **Decoder playback buffer** — separately, a downstream HLS decoder
   (hardware or software) doesn't render at the manifest's live edge; it
   holds back several segments first as a stall-avoidance buffer.
   `events.decoder_buffer_delay()` estimates this from the manifest's own
   `EXT-X-TARGETDURATION` (segments × target duration, 3 segments by
   default) rather than a hardcoded number of seconds, so it tracks
   whatever segment length the event is actually using. This is an
   estimate, not a measured constant — real decoders vary, and
   `buffer_segments` is a parameter on both `decoder_buffer_delay()` and
   `total_delay()` if it needs calibrating against a specific decoder.

Whatever triggers a delay-corrected `/resi/cue/create` (no explicit
`position_seconds`) is almost always reacting to something just watched on
a delayed player, so the real-world moment being marked actually happened
`streaming_delay + decoder_buffer_delay` seconds *before* the trigger
fired, not at the instant it fired.

`create_cue_now()` (`src/resi_cuecontrol/cues.py`) corrects for this. The
"now" it corrects from is the moment the OSC message arrived —
`osc_server.py` captures that timestamp as the very first thing `_on_create`
does, before making any Resi API calls, so the network round trip to
resolve the live event never gets baked into the cue position. It then
measures both delay components fresh on every cue creation (neither is a
fixed calibrated constant, since actual lag isn't guaranteed stable) and
subtracts their sum before turning the timestamp into a cue position.
`resi_cuecontrol.cues` logs the encode delay, decoder buffer delay, their
total, elapsed time, and resulting position at `INFO` on every such
create, so a mismatch can be traced to one component or the other in the
console.

## Setup

With direnv (recommended, since `.envrc` already says `use flake`):

```bash
direnv allow
```

Or manually: `nix develop`.

Either way, `uv sync` isn't something you run yourself here — the devShell's
virtualenv is built by Nix from `uv.lock`, already includes `pyresi` (pinned
to a commit on `pcs3rd/pyResi`), `requests`, and `python-osc`, and lands on
`PATH`.

## Running

```bash
RESI_USERNAME=you@yourchurch.org RESI_PASSWORD=... \
OSC_LISTEN_PORT=9000 OSC_REPLY_HOST=127.0.0.1 OSC_REPLY_PORT=9001 \
resi-cuecontrol
# or: RESI_TOKEN=... resi-cuecontrol
```

Environment variables, all optional except the Resi credentials:

- `RESI_TOKEN` or `RESI_USERNAME`/`RESI_PASSWORD` — authentication
- `OSC_LISTEN_HOST` (default `0.0.0.0`), `OSC_LISTEN_PORT` (default `9000`)
- `OSC_REPLY_HOST` (default `127.0.0.1`), `OSC_REPLY_PORT` (default `9001`)
- `LOG_LEVEL` (default `INFO`)

## Picking up pyResi changes

`pyresi` is a pinned git dependency, not editable — it won't pick up new
commits on its own. After pushing changes to pyResi:

```bash
uv lock --upgrade-package pyresi
```

then commit the updated `uv.lock`. Pushing pyResi itself needs your own
GitHub credentials (this tooling doesn't have push access to your
accounts) — push pyResi's `main` yourself first, then run the command
above here.

## Testing

Three layers, roughly in order of how much you can trust before going live:

1. **Unit tests — no network, no Resi account, run these constantly:**
   ```bash
   pytest
   ```
   `tests/test_cues.py` exercises the delay-correction math (including the
   clamp-to-zero edge case) against a fake pyResi client. `tests/test_osc_server.py`
   sends real UDP OSC packets into a running `OSCApp` and checks the reply
   messages — proves the dispatcher wiring and reply addresses are right
   without touching Resi at all.

2. **Manual OSC smoke test against the real server, still no live event
   needed for `read`/`update` against a *finished* recording:** start the
   server in one terminal:
   ```bash
   RESI_USERNAME=... RESI_PASSWORD=... resi-cuecontrol
   ```
   and in another, send it a command and watch for the reply. With
   `python-osc` installed (it's already in this project's venv):
   ```bash
   python3 -c "
   from pythonosc.udp_client import SimpleUDPClient
   SimpleUDPClient('127.0.0.1', 9000).send_message('/resi/cue/read', ['<encoder_id>'])
   "
   ```
   and a listener to see what comes back:
   ```bash
   python3 -c "
   from pythonosc.dispatcher import Dispatcher
   from pythonosc.osc_server import BlockingOSCUDPServer
   d = Dispatcher()
   d.set_default_handler(lambda addr, *args: print(addr, args))
   BlockingOSCUDPServer(('127.0.0.1', 9001), d).serve_forever()
   "
   ```
   (If you have `liblo`'s command-line tools, `oscsend localhost 9000 /resi/cue/read s <encoder_id>`
   and `oscdump 9001` do the same thing with less typing.)

3. **Live create/update against a real encoder — has real side effects.**
   Don't point `/resi/cue/create` or `/resi/cue/update` at a production
   Sunday event. Test against a low-stakes live event first (a test stream,
   an empty room) so a wrong delay calculation or a typo doesn't leave junk
   cues on something that matters. It's also the only way to calibrate
   `decoder_buffer_delay()`'s `buffer_segments` estimate against a
   specific decoder: fire a video into the encoder and an auto-time
   `/resi/cue/create` at the same instant, note how many seconds pass
   before that decoder actually shows anything, and compare that to the
   `INFO` log line's total delay — adjust `buffer_segments` if they don't
   match.

## Status / open questions

- Cue `position` values are relative to "the start of the video." Resi's
  HLS manifests do carry `EXT-X-PROGRAM-DATE-TIME` tags — confirmed against
  a real live event — but only on the actual media playlist a variant
  points to, not on `event['hlsUrl']` itself (that's a master/variant
  playlist with no segments of its own; `pyResi.fetch_manifest()` follows
  it to the real one).
- `decoder_buffer_delay()`'s 3-segment estimate is a documented guess, not
  a measured constant for any particular decoder hardware — see the
  calibration note in the testing section above if cues still land off
  from what a specific decoder shows.
- The OSC reply scheme (fixed host/port rather than reply-to-sender) assumes
  a fixed-IP setup on both ends, matching how the rest of the AV network is
  wired. If that's wrong, `osc_server.py` is a small file to change.
- Cue deletion isn't exposed here yet — `pyResi.cues.delete()` exists but is
  itself an unconfirmed guess at the underlying API (see pyResi's own docs).
