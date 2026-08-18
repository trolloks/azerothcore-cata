# Plan 16: build 15595 movement acknowledgement and speed contract

Canonical issue: [#34](https://github.com/trolloks/azerothcore-cata/issues/34)

Status: blocked by #33 (Plan 15). Continues the "Movement" plan family: proves a server-initiated
speed change (or teleport ack, if Plan 15's evidence points that way instead) is correctly
acknowledged by the client and accepted by the server. Ack counters, opcode numbers, and payload
shape must be derived from the pinned Cataclysm reference before implementation.
