# Plan 13: build 15595 in-world control bootstrap

Canonical issue: [#27](https://github.com/trolloks/azerothcore-cata/issues/27)

Status: blocked. The `SMSG_TIME_SYNC_REQ`/`CMSG_TIME_SYNC_RESP` implementation (opcodes, handler,
new runtime marker) is in place and opcode-audited against the pinned reference, but real-client
evidence shows the client process reliably exits ~30s after world entry -- before a time-sync round
trip can complete -- for reasons unrelated to the time-sync path itself. See [Plan 14](https://github.com/trolloks/azerothcore-cata/issues/32),
which owns diagnosing and resolving that exit. This plan resumes once Plan 14 proves the client
survives past the current post-map-insertion boundary.
