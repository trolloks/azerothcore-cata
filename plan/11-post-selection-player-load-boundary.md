# Plan 11: build 15595 initial post-load packet contract

Canonical issue: [#25](https://github.com/trolloks/azerothcore-cata/issues/25)

Status: open. Plan 10 proved `Player::LoadFromDB` returned true. This plan starts at
`SMSG_LOGIN_VERIFY_WORLD` and ends before map insertion.
