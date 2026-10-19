# Plan 33 follow-up: rejection cases for the native vendor path

Issue #157. Native rejection cases through the unchanged build-15595 client, same fixture and Godric
Rothgar scenario as `plan/evidence/plan33-vendor/`. Full logs, screenshots and captures stay in the run
directory (`/mnt/f79365ff-6a68-45da-925e-b9ddc6d5da6c/cata-runs/plan33-negative/`); this directory keeps
the sanitized verdicts (`no-money.json`, `full-bags.json`, `repeat-buy.json`).

## Pins

- Client build **15595**; reference TrinityCore `c699217775d90794158422387b07a917e161b582`.
- Branch `plan/33-followup-negative-vendor-cases`, harness at the commit carrying this file.
- Server binaries report the branch's C++ revision; the C++ source is unchanged since `2e62b063b` (the
  merged vendor conversion), so the accepted binaries run exactly the reviewed code.
- One generation per case (same manifest, `VENDOR_CASE` frozen at prepare), database cache reused.

## Commands

```bash
VENDOR_CASE=<case> bash /tmp/opencode/plan33f-acceptance.sh reset     # between cases
VENDOR_CASE=<case> ... prepare   # --mode vendor-negative, same roots as plan33-vendor
... run --auto-login --stability-seconds 8 --timeout 180
... verify --confirm-expected-screen
```

## Expected vs actual

| case | seed | expected | actual |
| --- | --- | --- | --- |
| `no-money` | money 0 | nothing granted or charged, refused for money | client sent `CMSG_BUY_ITEM` for 17184, server answered `SMSG_BUY_FAILED` with reason **2** (`BUY_ERR_NOT_ENOUGHT_MONEY`); saved inventory empty, money 0. Screenshot: "You don't have enough money." |
| `full-bags` | money 50000, all 16 backpack slots filled with bracers | nothing granted or charged, refused for space, seeded items preserved | client sent `CMSG_BUY_ITEM`, server answered `SMSG_INVENTORY_CHANGE_FAILURE` with reason **50** (`EQUIP_ERR_INVENTORY_FULL`); saved inventory still exactly the 16 seeded bracers in bag 0 slots 23-38, money 50000. Screenshot: "Inventory is full." |
| `repeat-buy` | money 50000 | exactly two grants and two charges | two `CMSG_BUY_ITEM` → two `SMSG_BUY_ITEM` + two `SMSG_ITEM_PUSH_RESULT`; saved inventory exactly two Small Shields in slots 23-24, money 49932 (2 × 34c). Screenshot: two "You receive item: [Small Shield]" lines, two shields in the bag, 4g 99s 32c. |

An important observation from the first case: this client does **not** pre-check affordability or bag
space before sending the purchase, so both rejections are exercised on the server side (the failure
packets and the unchanged row), not merely in the client UI. That is what makes these cases meaningful
for the AC handler. The verifier therefore requires the request, the **case-specific** result code
(`BUY_ERR_NOT_ENOUGHT_MONEY` / `EQUIP_ERR_INVENTORY_FULL`) and the complete saved inventory compared
against the seed — not just the absence of the purchased entry — so a missed click or an unrelated
refusal cannot pass.

## Still open on #157

- `out-of-stock` (needs a `maxcount`/`incrtime` row and a vendor-list payload check to show the row is
  hidden after the last unit).
- `currency-cost` (needs an `ItemExtendedCost.dbc` row with a currency requirement plus a temporary
  `npc_vendor` row; then the buy must be refused fail-closed with `EQUIP_ERR_VENDOR_MISSING_TURNINS`).
- Full relog (logout and re-enter, not only the saved-row read used here).
- Pending native attempts: a **dead or out-of-range vendor** and selling a **non-empty bag** are
  player-action/state scenarios, so they belong here rather than in the parse-level bucket; the refusing
  layer (client or server) is to be established, as it was for the buy cases.
- Parse/unit-level only, because the native client cannot encode them: invalid vendor/slot/type/count
  fields and a foreign-owned item. These are not faked by injecting packets.
- Guild-funded repair (documented AC-vs-pin deviation) and vendors other than Godric Rothgar.

## Ownership and cleanup

The harness owned the MySQL container/volume, client copy, wine prefix and servers; `run` stops the
client and servers in its `finally` block; all three generations are in state `accepted`. No credentials,
dumps or raw captures are committed; the database cache stays in the run directory.
