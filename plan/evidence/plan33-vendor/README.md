# Plan 33: native vendor buy, sell, buyback and repair

Issue #135. Acceptance record for the vendor round trip through the unchanged build-15595 client.
The full logs, screenshots, packet capture, client copy and wine prefix stay in the run directory
(`/mnt/f79365ff-6a68-45da-925e-b9ddc6d5da6c/cata-runs/plan33-acceptance/`); this directory keeps the
sanitized verdict.

## Pins

- Client: build **15595** (unchanged Cata client, launched through GE-Proton wine).
- Reference: TrinityCore `c699217775d90794158422387b07a917e161b582`.
- Branch `plan/33-native-vendor-buy-sell-buyback-and-repair`, harness at `11d50c672`.
- Server binaries report `3ec5ea395851` (generation 2); the C++ source is unchanged since `2e62b063b` (every later
  commit on the branch is Python harness or Markdown), so the accepted binary contains exactly the
  reviewed C++.
- Data: `--data-root` = the pinned TrinityCore extraction root; `--server-dbc-root` = this
  checkout's flattened `var/extractors/dbc-out3/dbc`; world/characters/auth from the harness's
  database cache (world dump built from this branch's SQL, unchanged by the branch).

## Scenario (pinned rows)

Vendor: **Godric Rothgar**, creature entry **1213**, spawn guid **79952**, map 0, `(-8898.23,
-119.838, 82.016)`, `npcflag 4224 (0x1080, vendor|repair)`, faction 12, **`gossip_menu_id = 0`** — so
the client opens the inventory directly (`CMSG_LIST_INVENTORY`) instead of showing a gossip menu.

`npc_vendor` (all `ExtendedCost` 0, `maxcount` 0, `BuyCount` 1; displayed prices are the discounted
ones the run showed in `SMSG_VENDOR_INVENTORY`):

| MuID | item | name | list price | run price |
| --- | --- | --- | --- | --- |
| 1 | 2129 | Large Round Shield | 77 | 73 |
| 2 | 2379 | Tarnished Chain Vest | 76 | 72 |
| 3 | 2380 | Tarnished Chain Belt | 38 | 36 |
| 4 | 2381 | Tarnished Chain Leggings | 76 | 72 |
| 5 | 2383 | Tarnished Chain Boots | 58 | 55 |
| 6 | 2384 | Tarnished Chain Bracers | 38 | 36 |
| 7 | 2385 | Tarnished Chain Gloves | 38 | 36 |
| 8 | 17184 | Small Shield | 36 | **34** |

Fixture character (`--mode vendor-trade` seeds): 5 gold (50000c), a **bracer 2384** in bag 0 slot 23
(durability 18), and an equipped **vest 2379** at chest, damaged to **5/55**.

## Commands

```bash
cmake --build "$BUILD" --target revision.h worldserver authserver -j3      # once, from the branch
python3 apps/cata/run_real_client_authentication.py prepare --manifest "$M" \
  --authserver "$BUILD/src/server/apps/authserver" --worldserver "$BUILD/src/server/apps/worldserver" \
  --unit-tests var/build/src/test/unit_tests --client-root "$CLIENT" --data-root "$DATA" \
  --server-dbc-root "$DBC" --wine-runner "$RUNNER/files" --personal-bottle "$BOTTLE" \
  --display ":0" --xauthority "$XAUTHORITY" --mode vendor-trade
python3 apps/cata/run_real_client_authentication.py run --manifest "$M" \
  --auto-login --stability-seconds 8 --timeout 180
python3 apps/cata/run_real_client_authentication.py verify --manifest "$M" --confirm-expected-screen
```

Reproduced twice on the same prepared generation (generation 2). The recorded verdict is
`plan/evidence/plan33-vendor/acceptance.json`: `outcome: vendor_trade_pass`, generation state
`accepted`, `screen_confirmed: true`.

## Expected vs actual

| Check | Expected | Actual |
| --- | --- | --- |
| vendor opens | `CMSG_LIST_INVENTORY` → `SMSG_VENDOR_INVENTORY` | 1 / 1 |
| buy MuID 8 | `CMSG_BUY_ITEM` → `SMSG_BUY_ITEM` + `SMSG_ITEM_PUSH_RESULT`, no `SMSG_BUY_FAILED` | 1 → 1 + 1, 0 failures; chat "You receive item: [Small Shield]" |
| sell seeded bracer | `CMSG_SELL_ITEM` naming item counter 99000301 | 1, counter 99000301 |
| buyback | `CMSG_BUYBACK_ITEM` naming slot 74 (`BUYBACK_SLOT_START`) | 1, slot 74; buyback page empty afterwards |
| repair-all | `CMSG_REPAIR_ITEM` (item guid 0) | 1 |
| persistence | logout saves the row; shield owned, bracer back in a backpack slot, vest at 55/55 | `bought_item_count 1`, `buyback_item_count 1`, `repaired_durability 55` |
| money | 50000 − 34 + 7 − 7 − 38 = 49928 | 49928 |
| logout | `SMSG_LOGOUT_COMPLETE` (normal 20 s timer) | 1 |

The screenshots in the run directory show each step: the merchant window with the eight rows, the
purchase chat line, the 7c sale (4g 99s 66c → 73c), the buyback page holding the bracers, the bracer
back in slot 1 next to the shield, the repair-all charge (→ 4g 99s 28c), and the logout countdown.

## UI facts this pins

- The merchant grid is two columns of six filled left to right (MuID 8 = right column, row 4).
- The buyback page re-anchors the same twelve buttons into its own two-column grid (wide cells) and
  hides the single recent-sale control; the newest sale is its first cell.
- The repair-all anvil sits at the merchant frame's bottom-left; both repair buttons are hidden while
  the buyback page is selected, so the purchase tab is reselected before repairing.
- Returning to the character screen after `/logout` makes the client request its character list
  again, so one login produces two enumerations.

## Exclusions and not covered natively

- Negative native cases from the issue: insufficient money, full inventory, invalid vendor/slot/type/
  count, exhausted stock, out-of-range or dead interaction, and a foreign-owned item/bag. The code
  paths are reviewed and the request-level rejections are covered by unit tests, but the client-side
  negotiation for each is not exercised here.
- Repeated-purchase deduplication (none is implemented; the protocol allows repeats) and a full
  relog: the run logs out and the verification reads the stored character row rather than logging in
  again.
- Extended-cost vendor rows, currency purchases (`itemType 2` is refused and logged), `PlayerConditionID`
  rows, guild-funded repair, and any vendor other than Godric Rothgar.
- Not exercised: `CMSG_BUY_ITEM_IN_SLOT` (no 4.3.4 opcode) and the WotLK-only paths.

## Ownership and cleanup

The harness created and owned the MySQL container/volume, the generation client copy, the wine prefix
and the server processes; `run` stops the client and both servers in its `finally` block, and the
generation state is `accepted`. No credentials, dumps or raw captures are committed. The database
cache stays in the run directory for reuse.
