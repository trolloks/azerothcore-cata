#!/usr/bin/env python3
"""All-race starter-content coverage for the clean world (#206).

Compares, per starter/follow-on zone, the TDB 434 quest candidates with the landed gold rows,
classifies the missing quests (skipped by the landing vs unimported), lists extras, diffs the key
quest fields, and reports the per-zone inventory denominators (TDB vs gold).

Read-only against the run-owned inventory container. Prints the markdown report and writes
`report.json` next to it.
"""

import argparse
import json
import re
import sys
from pathlib import Path

from starter_content_inventory import inventory, query, zone_quest_candidates
from land_zone import quest_sorts

REPO = Path(__file__).resolve().parents[2]
PENDING = REPO / "data/sql/updates/pending_db_world"
FIELDS = ("LogTitle", "QuestLevel", "MinLevel", "Flags", "RewardMoney")

# zone, map, label, race coverage
ZONES = (
    (85, 0, "Tirisfal Glades", "Undead"),
    (12, 0, "Elwynn Forest", "Human"),
    (1, 0, "Dun Morogh", "Dwarf/Gnome"),
    (141, 1, "Teldrassil", "Night Elf"),
    (3524, 530, "Azuremyst Isle", "Draenei"),
    (4714, 654, "Gilneas", "Worgen"),
    (14, 1, "Durotar", "Orc/Troll"),
    (215, 1, "Mulgore", "Tauren"),
    (3430, 530, "Eversong Woods", "Blood Elf"),
    (4737, 648, "Kezan", "Goblin"),
    (40, 0, "Westfall", "Human follow-on"),
    (44, 0, "Redridge Mountains", "Human follow-on"),
    (38, 0, "Loch Modan", "Dwarf/Gnome follow-on"),
    (148, 1, "Darkshore", "Night Elf follow-on"),
    (3525, 530, "Bloodmyst Isle", "Draenei follow-on"),
    (130, 0, "Silverpine Forest", "Undead follow-on"),
    (3433, 530, "Ghostlands", "Blood Elf follow-on"),
    (17, 1, "Northern Barrens", "Orc/Troll follow-on"),
    (16, 1, "Azshara", "Horde follow-on"),
    (331, 1, "Ashenvale", "Horde/Alliance follow-on"),
)


def parse_skips(zone: int, map_id: int) -> dict[int, str]:
    """Quest id -> landing reason, from the pending landing SQL headers.

    Two header formats exist: land_zone's `Skipped quests: 123 (reason); ...` and the earlier
    emit_pending_sql's `Skipped quests (reason): 123,456,...`.
    """
    skips = {}
    for path in sorted(PENDING.glob("*.sql")):
        if not path.stat().st_size:
            continue
        first = path.read_text(errors="replace").splitlines()[0]
        if f"(zone {zone}, map {map_id})" not in first:
            continue
        if "Skipped quests:" in first:
            for entry in first.split("Skipped quests:", 1)[1].split("; "):
                quest = entry.split(" (", 1)[0].strip()
                if quest.isdigit():
                    skips[int(quest)] = entry
        elif "Skipped quests (" in first and "):" in first:
            reason = first.split("Skipped quests (", 1)[1].split("):", 1)[0]
            for quest in first.split("):", 1)[1].split(","):
                quest = quest.strip().split(".")[0]
                if quest.isdigit():
                    skips[int(quest)] = reason
    return skips


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--container", default="plan175-inventory-mysql")
    parser.add_argument("--user", default="root")
    parser.add_argument("--password", default="plan175ro")
    parser.add_argument("--ac-schema", default="acore_world_gold")
    parser.add_argument("--tdb-schema", default="tdb_world")
    parser.add_argument(
        "--dbc-dir",
        default=("/mnt/e1384d9e-bede-40dd-8b1c-be0beb488490/Fun/azerothcore-cata/"
                 "var/extractors/dbc-out3/dbc"))
    parser.add_argument("--out", type=Path, default=REPO / "plan/evidence/coverage-2026-10-28")
    args = parser.parse_args()
    c, u, pw = args.container, args.user, args.password

    gold_ids = {int(r[0]) for r in query(c, u, pw, args.ac_schema,
                                          f"SELECT ID FROM {args.ac_schema}.quest_template")}
    report = []
    for zone, map_id, label, race in ZONES:
        sorts = quest_sorts(args.dbc_dir, zone)
        candidates = [int(q) for q in zone_quest_candidates(c, u, pw, args.tdb_schema, sorts, zone, map_id)]
        missing = [q for q in candidates if q not in gold_ids]
        skips = parse_skips(zone, map_id)
        unimported = [q for q in missing if q not in skips]
        gold_sorted = {int(r[0]) for r in query(
            c, u, pw, args.ac_schema,
            f"SELECT ID FROM {args.ac_schema}.quest_template "
            f"WHERE QuestSortID IN ({','.join(str(value) for value in sorts)})")}
        extra = sorted(gold_sorted - set(candidates))
        common = [q for q in candidates if q in gold_ids]
        changed = []
        if common:
            ids = ",".join(str(q) for q in common)
            columns = ",".join(FIELDS)
            tdb_rows = {int(r[0]): r[1:] for r in query(
                c, u, pw, args.tdb_schema,
                f"SELECT ID,{columns} FROM {args.tdb_schema}.quest_template WHERE ID IN ({ids})")}
            gold_rows = {int(r[0]): r[1:] for r in query(
                c, u, pw, args.ac_schema,
                f"SELECT ID,{columns} FROM {args.ac_schema}.quest_template WHERE ID IN ({ids})")}
            for quest in common:
                left, right = tdb_rows.get(quest), gold_rows.get(quest)
                if not left or not right or left == right:
                    continue
                fields = {FIELDS[i]: {"tdb": left[i], "gold": right[i]}
                          for i in range(len(FIELDS)) if left[i] != right[i]}
                if fields:
                    changed.append({"ID": quest, "fields": fields})
        tdb_inv = inventory(c, u, pw, args.tdb_schema, zone, map_id)
        gold_inv = inventory(c, u, pw, args.ac_schema, zone, map_id)
        inventory_diff = {key: {"tdb": tdb_inv.get(key), "gold": gold_inv.get(key)}
                          for key in sorted(set(tdb_inv) | set(gold_inv))}
        report.append({
            "zone": zone, "map": map_id, "label": label, "race": race, "sorts": sorts,
            "candidates": len(candidates), "landed": len(common), "missing": len(missing),
            "skipped": len([q for q in missing if q in skips]),
            "unimported": unimported,
            "extra": extra,
            "changed": changed,
            "inventory": inventory_diff,
        })

    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

    lines = [
        "# Starter-content coverage (2026-10-28, clean world)",
        "",
        "Per-zone TDB 434 quest candidates vs the landed gold rows. `skipped` are the missing quests "
        "with a documented landing reason; `unimported` are missing candidates without a documented "
        "reason (see Limitations).",
        "",
        "| Zone | Map | Race | Candidates | Landed | Missing | Skipped | Unimported | Extra | Changed |",
        "|---|---:|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in report:
        lines.append(f"| {row['label']} | {row['map']} | {row['race']} | {row['candidates']} | "
                     f"{row['landed']} | {row['missing']} | {row['skipped']} | {len(row['unimported'])} | "
                     f"{len(row['extra'])} | {len(row['changed'])} |")
    lines += ["", "## Unimported quests (no landing reason)", ""]
    any_unimported = False
    for row in report:
        if row["unimported"]:
            any_unimported = True
            lines.append(f"- {row['label']} ({row['zone']}): {', '.join(map(str, row['unimported']))}")
    if not any_unimported:
        lines.append("- none")
    lines += ["", "## Extras beyond the TDB candidates", ""]
    any_extra = False
    for row in report:
        if row["extra"]:
            any_extra = True
            lines.append(f"- {row['label']} ({row['zone']}): {', '.join(map(str, row['extra'][:40]))}"
                         + (" ..." if len(row["extra"]) > 40 else ""))
    if not any_extra:
        lines.append("- none")
    lines += ["", "## Changed quest fields", ""]
    any_changed = False
    for row in report:
        for item in row["changed"][:20]:
            any_changed = True
            fields = ", ".join(f"{name} {values['tdb']!r}->{values['gold']!r}"
                               for name, values in item["fields"].items())
            lines.append(f"- {row['label']} {item['ID']}: {fields}")
        if len(row["changed"]) > 20:
            lines.append(f"- {row['label']}: ... {len(row['changed']) - 20} more in report.json")
    if not any_changed:
        lines.append("- none")
    lines += [
        "",
        "## Inventory denominators (gold / TDB)",
        "",
        "Values are `gold / TDB`.",
        "",
        "| Zone | Quests | Creatures | Gameobjects | Creature loot | GO loot | SmartAI |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for row in report:
        inv = row["inventory"]

        def pair(key: str) -> str:
            item = inv.get(key, {})
            return f"{item.get('gold')}/{item.get('tdb')}"
        lines.append(f"| {row['label']} | {pair('quests')} | {pair('creatures')} | {pair('gameobjects')} | "
                     f"{pair('creature_loot')} | {pair('gameobject_loot')} | {pair('smart_scripts')} |")
    lines += [
        "",
        "## Limitations",
        "",
        "- `unimported` means no landing SQL documents a reason, not necessarily a bug. The earlier "
        "`emit_pending_sql` headers (most zones before Ashenvale) list only the items-absent/objective "
        "template skips; the importer's other skip reasons (no turn-in, no giver, no spawn) are not in "
        "the SQL and appear here. Only the newer `land_zone` zones document every skip.",
        "- Candidate sets use the client DBC subzones; the earlier landings imported the shell's explicit "
        "sort set, so some missing rows are import scope rather than content gaps.",
        "- The inventory counts are the #202 proxy denominators, not a chain-by-chain proof.",
        "- The upgrade-from-pinned-baseline convergence is not tested here: the project moved to the "
        "clean TDB-built world (#274). Only the fresh clean-world state is reported.",
        "- One landed quest per zone does not prove the quest mechanics, chains or the in-world flow; "
        "the per-zone relation checks remain the landing evidence. Geometry prerequisites are #176; "
        "phasing and terrain are #180.",
        "",
    ]
    markdown = "\n".join(lines)
    (args.out / "REPORT.md").write_text(markdown, encoding="utf-8")
    print(markdown)
    return 0


if __name__ == "__main__":
    sys.exit(main())
