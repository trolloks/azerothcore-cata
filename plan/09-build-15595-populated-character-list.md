# Plan 9: one database-backed build 15595 character

Canonical issue: [#19](https://github.com/trolloks/azerothcore-cata/issues/19)

Status: complete. The issue is the implementation plan and progress record. This stub preserves the path
used by the conversion ledger.

Runtime findings:

- `CHAR_SEL_ENUM` now reads list position from `characters.order` instead of `extra_flags`.
- The fixed `Cataplan` row serializes to the exact 278-byte build 15595 response fixture.
- Fresh generations 6 and 7 both reached `LOGIN_OK`, world `AUTH_OK`, and completed character enumeration.
- Both runs showed one stable character without selection or mutation opcodes and matched semantically.
- Fresh Wine prefixes can render the cinematic without a visible `MovieProxy.exe`; login automation therefore
  dismisses the cinematic during the startup window and explicitly focuses WoW before each field click.
