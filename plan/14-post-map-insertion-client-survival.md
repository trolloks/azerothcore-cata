# Plan 14: build 15595 post-map-insertion client survival

Canonical issue: [#32](https://github.com/trolloks/azerothcore-cata/issues/32)

Status: blocking #27. Discovered while working Plan 13: the real client reliably exits ~30s after
world entry, before Plan 13's time-sync round trip can complete. Not a time-sync opcode/byte-layout
bug (both opcodes and the handler are already correct); root cause is still open. See the
investigation comment on #27 and the full issue body for the evidence trail and working hypotheses.
