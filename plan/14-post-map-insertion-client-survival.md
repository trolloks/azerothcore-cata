# Plan 14: build 15595 post-map-insertion client survival

Canonical issue: [#32](https://github.com/trolloks/azerothcore-cata/issues/32)

Status: blocking #27, root cause still open. The crash is now precisely characterized: a
deterministic client-side stack overflow, byte-identical address and size (`0x6ffffff53ea2`, 2176
bytes) across every run on a given Wine build. Confirmed independent of Wine major version (11.0 vs
10.0) and of DXVK shader-compiler thread contention -- both ruled out with direct evidence, not
assumption. `WINEDEBUG=+seh` is now enabled by default in the harness so this signal persists on
every future run. See the issue's investigation comments for the full evidence trail, what's ruled
out, and the recommended next steps (native backtrace with symbols, zone-specific bisection).
