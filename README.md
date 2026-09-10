# Resi_CueControl

Cue control tooling for Resi Central events (`studio.resi.io`), built on
[pyResi](https://github.com/pcs3rd/pyResi).

This is a scaffold, not a finished tool. `main()` currently just
authenticates and lists the account's channels as a smoke test that the
pyResi dependency is wired up. Build the real cue-placement logic from
there.

## Setup

With direnv (recommended, since `.envrc` already says `use flake`):

```bash
direnv allow
```

Or manually:

```bash
nix develop
```

Either way, `uv sync` isn't something you run yourself here — the devShell's
virtualenv is built by Nix from `uv.lock`, already includes `pyresi` (pinned
to a commit on `pcs3rd/pyResi`) and `requests`, and lands on `PATH`.

## Running

```bash
RESI_USERNAME=you@yourchurch.org RESI_PASSWORD=... resi-cuecontrol
# or: RESI_TOKEN=... resi-cuecontrol
```

## Picking up pyResi changes

`pyresi` is a pinned git dependency, not editable — it won't pick up new
commits on its own. After pushing changes to pyResi:

```bash
uv lock --upgrade-package pyresi
```

then commit the updated `uv.lock`.

## Frame-accurate cues — open question

Cue `position` values are relative to "the start of the video," but what
that's anchored to (event `startTime`, first manifest frame, or something
else) hasn't been confirmed against a real stream yet. Before this project
can place cues to the frame, that delta needs to be measured — either from
`EXT-X-PROGRAM-DATE-TIME` tags in the HLS manifest if present, or by
calibrating against a burned-in timecode reference pushed through a real
encoder. See pyResi's own docs for what's confirmed vs. inferred about the
API in the meantime.
