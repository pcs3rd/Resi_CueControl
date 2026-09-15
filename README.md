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
| `/resi/cue/create` | `encoder_id, name` | Creates a cue at "now", delay-corrected (see below) |
| `/resi/cue/create_at` | `encoder_id, position_seconds, name` | Creates a cue at an explicit timeline position — no delay correction |
| `/resi/cue/update` | `encoder_id, cue_id, position_seconds, name` | Moves/renames an existing cue |
| `/resi/encoders/list` | *(none)* | Lists every encoder on the account |

Every command replies to a fixed target (`OSC_REPLY_HOST`/`OSC_REPLY_PORT`)
rather than back to the sender — `/resi/cue/entry`, `/resi/cue/read/done`,
`/resi/cue/created`, `/resi/cue/updated`, `/resi/encoder/entry` +
`/resi/encoders/list/done`, or `/resi/cue/error` on failure.
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

`/resi/cue/create_at` is the one to reach for when testing cue creation
itself — a wrong or unvalidated `streaming_delay()` reading can't put the
cue somewhere unexpected, since there's no delay math involved at all;
you give it the exact timeline second you want. Both commands reply on
the same `/resi/cue/created` address.

## Why cue creation corrects for delay

Resi's live playback lags real time — encoder buffering, segmenting, CDN
propagation. Whatever triggers `/resi/cue/create` is almost always reacting
to something just watched on a delayed player, so the real-world moment
being marked actually happened a few seconds *before* the trigger fired, not
at the instant it fired.

`create_cue_now()` (`src/resi_cuecontrol/cues.py`) corrects for this: it
measures the current delay via `pyResi`'s `events.streaming_delay()` — which
reads the live event's HLS manifest and, when the manifest carries
`EXT-X-PROGRAM-DATE-TIME` tags, compares "now" against the absolute time of
the most recently encoded segment (falling back to `event.startTime` plus
summed segment durations if those tags aren't present) — and subtracts that
delay before turning the timestamp into a cue position. This is measured
fresh on every cue creation, not a fixed calibrated constant, since the
delay isn't guaranteed stable.

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

then commit the updated `uv.lock`. **This repo currently needs that step**:
the `streaming_delay`/`event_start_time`/`seconds_to_position` helpers this
project's cue logic depends on were added to pyResi locally but pushing
them requires your own GitHub credentials (this tooling doesn't have
push access to your accounts) — push pyResi's `main` yourself, then run the
command above here.

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
   cues on something that matters. This is also the only way to actually
   confirm the `EXT-X-PROGRAM-DATE-TIME` question in `streaming_delay()` —
   watch `LOG_LEVEL=DEBUG` output (add a log line in `pyResi.events.live_edge_time`
   if you want to see which path it took) the first time this runs against
   a real stream.

## Status / open questions

- Cue `position` values are relative to "the start of the video," and
  whether Resi's HLS manifests actually carry `EXT-X-PROGRAM-DATE-TIME` tags
  is unconfirmed — `streaming_delay()` falls back to `startTime` + summed
  segment durations if they're absent, but hasn't been validated against a
  real live event either way. Worth checking the first time this runs
  against a real stream.
- The OSC reply scheme (fixed host/port rather than reply-to-sender) assumes
  a fixed-IP setup on both ends, matching how the rest of the AV network is
  wired. If that's wrong, `osc_server.py` is a small file to change.
- Cue deletion isn't exposed here yet — `pyResi.cues.delete()` exists but is
  itself an unconfirmed guess at the underlying API (see pyResi's own docs).
