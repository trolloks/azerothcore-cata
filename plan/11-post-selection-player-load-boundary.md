# Plan 11: build 15595 pre-map initial-packet contract

Canonical issue: [#25](https://github.com/trolloks/azerothcore-cata/issues/25)

Status: complete. Plan 10 proved `Player::LoadFromDB` returned true. This plan covers the
session- and player-owned initial packets through the existing pre-map marker. Two fresh
build-15595 generations (4, 5) reached `initial_post_load_packets_pass`, each with a non-empty
pre-map packet prefix, a single marker held stable for 10s across two snapshots, and a visually
confirmed screen.

Pinned Cataclysm sends `SMSG_LOGIN_VERIFY_WORLD` only after map insertion. It is therefore a Plan 12
packet; Plan 11 neither retains nor certifies the current early send.

The real-client runner uses a run-owned symlink overlay: source client files remain read-only, while
the client writable directories and locale `realmlist.wtf` are local to the disposable generation.
