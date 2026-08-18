# Plan 8 runtime findings

Canonical work item: [GitHub issue #18](https://github.com/trolloks/azerothcore-cata/issues/18)

## Why the earlier client launch was faster

The successful Plan 7 run reused an already-prepared generation, client baseline,
database state, and Wine prefix. It therefore did not perform the first-run setup
steps below. It also predates the typed character-enumeration commit, so it is not
Plan 8 completion evidence.

## Fresh Plan 8 setup observations

- Current-head binaries compile successfully and the four focused character packet
  tests pass.
- The first generation restores or builds the isolated MySQL state, copies the
  immutable client baseline once, and then converts the generation client path to
  symlinks. Later generations reuse both caches; they do not copy the client again.
- The database cache is now working: generation 4 restored the 293 MiB dump.
- The first MySQL readiness probe could observe MySQL's temporary initialization
  server immediately before it was replaced by the final server. The runner now
  requires one second of continuous successful probes.
- A linked run-owned `d3d9.dll` must be replaced by an ordinary file after the
  client baseline is linked. Unlinking the run-owned link is safe and does not
  mutate the source client; the runner now does this.
- The Soda runner's fresh `wineboot` timed out. The installed GE-Proton 11 runner
  contains the expected Wine and DXVK layout and is the supported retry path.
- A fresh `prepare` also re-hashes the supplied personal Bottle tree to protect
  the baseline. That tree is about 111 GiB, so this metadata pass can take several
  minutes even when the database cache is restored. This is setup overhead, not a
  character-protocol failure.
- The current-head retry reached worldserver, then stopped during startup because
  the cata-js DBC input is under `dbc/enUS/`, while AzerothCore loads required files
  directly from `DataDir/dbc/`. The earlier successful Plan 7 run used the existing
  flat `node-dbc-reader/data/dbc` extraction; this is an input-layout mismatch, not
  a client or typed character-enum failure.
- A failed DXVK setup once created the run-owned read-only `client-base` before its
  manifest record was persisted. The runner now saves ownership immediately after
  baseline creation and unlinks only the generation-local `d3d9.dll` symlink before
  installing DXVK.
- The first GE-Proton client launch reached the 4.3.4 intro cinematic and consumed
  the run timeout before credentials could be entered. The isolated config now sets
  both `movie` and `playIntroMovie` to `0`, so fresh prefixes should go directly to
  the login UI. The live input trial also showed that focus and field clearing must
  be one atomic operation; otherwise keystrokes can land in the controlling terminal
  or append stale text to the account field.
- The temporary UI helper also used stale shorthand credentials (`PLAN7` /
  `PLANPASS`) instead of the runner-owned account (`PLAN6USER` / `PLAN6PASS`). The
  runner now has an explicit `run --auto-login` path that focuses the owned window,
  tracks the owned `MovieProxy.exe` process while repeatedly sending Escape, then
  clears both fields, enters the canonical synthetic credentials, and advances with
  Tab/Return using positions derived from live window geometry. This avoids typing
  into the login UI while the cinematic is still active.

## Current gate

Two independent fresh generations (15 and 16) now pass the Plan 8 gate with the
current-head binaries: build 15595 authenticates, reaches world, completes
`CMSG_CHAR_ENUM`/`SMSG_CHAR_ENUM`, receives the typed empty response
`000001000000`, and holds the expected zero-character screen for 10 seconds.
`compare-last-two` reports matching evidence, both generations reset cleanly, and
the client/server/database inputs remain unchanged. The existing Plan 7
authentication generation also has a separate Plan 6 replay pass. The sanitized
Plan 8 fixture and ledger update are the final publication steps; non-empty
character data and world entry remain later plans.
