# Plan 12: build 15595 map insertion and object bootstrap

Canonical issue: [#26](https://github.com/trolloks/azerothcore-cata/issues/26)

Status: complete. Covers `AddPlayerToMap`, the typed `SMSG_LOGIN_VERIFY_WORLD` send (moved
here from the pre-map path per the pinned Cataclysm reference, which sends it only after map
insertion), and the first object/update bootstrap (`SendInitialPacketsAfterAddToMap`, including
the player's own `SMSG_UPDATE_OBJECT`). Movement and player control remain out of scope for
Plan 13.

Two fresh build-15595 generations (6, 7) reached `map_insertion_object_bootstrap_pass`, each
with `SMSG_LOGIN_VERIFY_WORLD` and `SMSG_UPDATE_OBJECT` present in the captured prefix, a single
marker held stable for 10s across two snapshots, and a visually confirmed world-loading screen.
`compare-last-two` found the two runs byte-identical.
