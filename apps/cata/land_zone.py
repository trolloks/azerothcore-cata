#!/usr/bin/env python3
"""Run the per-zone starter-content landing pipeline end to end (#203).

For one zone: clone the golden schema (master) into a baseline and an import target, decide which TDB quests
cannot land (their items are absent from the client Item DB2, or an objective has no template anywhere), convert
the missing items into a pending item_template file, import, emit the pending landing SQL, lint it, replay it on a
fresh clone and run the relation checker. Writes two files into data/sql/updates/pending_db_world/ and prints a
JSON summary; it never touches a database other than the run-owned disposable schemas named below.

    land_zone.py --zone 215 --map 1 --label Mulgore --issue 231

Needs a golden schema (acore_world_gold) holding master's world database, built once with the SQL replay and
refreshed with clone_world_db.sh whenever master changes. The worldserver load diff and the Codex review are
separate steps.
"""

import argparse
import json
import re
import struct
import subprocess
import sys
from pathlib import Path

from import_starter_content import run, scalar

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
PENDING = REPO / "data/sql/updates/pending_db_world"
ITEM_COLUMNS = ([f"RequiredItemId{i}" for i in range(1, 7)] + [f"RewardItem{i}" for i in range(1, 5)]
                + [f"RewardChoiceItemID{i}" for i in range(1, 7)] + ["StartItem"]
                + [f"ItemDrop{i}" for i in range(1, 5)])
OBJECTIVES = [f"RequiredNpcOrGo{i}" for i in range(1, 5)]


def quest_sorts(dbc_dir, zone):
    """The zone id plus its subzone ids: TDB may sort a zone's quests under either."""
    data = (Path(dbc_dir) / "AreaTable.dbc").read_bytes()
    if data[:4] != b"WDBC":
        raise SystemExit(f"AreaTable.dbc in {dbc_dir} is not a WDBC file")
    records, _, record_size, _ = struct.unpack_from("<4I", data, 4)
    subzones = set()
    for i in range(records):
        area, _continent, parent = struct.unpack_from("<3I", data, 20 + i * record_size)
        if parent == zone:
            subzones.add(area)
    return sorted(subzones | {zone})



def sh(args, **kwargs):
    return subprocess.run(args, cwd=REPO, text=True, capture_output=True, **kwargs)


def mysql_file(container, password, schema, path):
    with open(path, encoding="utf-8") as handle:
        result = subprocess.run(["docker", "exec", "-i", container, "mysql", "-uroot", f"-p{password}",
                                 "--default-character-set=utf8mb4", schema], stdin=handle, text=True,
                                capture_output=True)
    errors = [line for line in result.stderr.splitlines() if "Using a password" not in line]
    if result.returncode or errors:
        raise SystemExit(f"applying {path} to {schema} failed: {errors[:3]}")


def clone(source, targets):
    procs = [subprocess.Popen(["bash", str(HERE / "clone_world_db.sh"), source, target]) for target in targets]
    if any(proc.wait() for proc in procs):
        raise SystemExit("schema clone failed")


def convert_items(ids, dbc_dir):
    """Return (sql, absent): SQL for the ids the client DB2 has, and the ids it lacks."""
    ids, absent = sorted(set(ids), key=int), []
    while ids:
        result = sh([sys.executable, str(HERE / "convert_items_from_db2.py"), "--dbc-dir", dbc_dir, "--insert-only",
                     f"{dbc_dir}/Item.db2", f"{dbc_dir}/Item-sparse.db2", *ids])
        if result.returncode == 0:
            return result.stdout, absent
        match = re.search(r"item (\d+) missing", result.stderr)
        if not match or match.group(1) not in ids:
            raise SystemExit(f"item conversion failed: {result.stderr[-300:]}")
        absent.append(match.group(1))
        ids.remove(match.group(1))
    return "", absent


def quests_using(c, u, p, source, quests, items):
    if not quests or not items:
        return set()
    wanted = ",".join(items)
    clause = " OR ".join(f"{column} IN ({wanted})" for column in ITEM_COLUMNS)
    return {r[0] for r in run(c, u, p, f"SELECT ID FROM {source}.quest_template WHERE ID IN ({','.join(quests)}) "
                                       f"AND ({clause})")}


def importer(args, target, baseline, skip, apply, sorts):
    command = [sys.executable, str(HERE / "import_starter_content.py"), "--target", target, "--baseline", baseline,
               "--zone", str(args.zone), "--map", str(args.map), "--skip-quests", ",".join(sorted(skip, key=int)),
               "--quest-sorts", ",".join(map(str, sorts)), "--dbc-dir", args.dbc_dir]
    result = sh(command + (["--apply"] if apply else []))
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        raise SystemExit(f"importer output unreadable: {result.stderr[-400:] or result.stdout[-400:]}")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--container", default="plan175-inventory-mysql")
    parser.add_argument("--password", default="plan175ro")
    parser.add_argument("--zone", type=int, required=True)
    parser.add_argument("--map", type=int, required=True)
    parser.add_argument("--label", required=True, help="zone name for the file headers")
    parser.add_argument("--issue", type=int, required=True, help="tracking issue number for the file headers")
    parser.add_argument("--golden", default="acore_world_gold")
    parser.add_argument("--base", default="acore_world_base")
    parser.add_argument("--clean", default="acore_world_clean")
    parser.add_argument("--replay", default="acore_world_land")
    parser.add_argument("--dbc-dir", default="/mnt/e1384d9e-bede-40dd-8b1c-be0beb488490/Fun/cata-js/data/dbc/enUS")
    args = parser.parse_args()
    for name in (args.golden, args.base, args.clean, args.replay):
        if not re.fullmatch(r"\w+", name):
            parser.error(f"invalid schema name {name!r}")
    c, u, p = args.container, "root", args.password
    sorts = quest_sorts(args.dbc_dir, args.zone)

    clone(args.golden, [args.base, args.clean])
    from starter_content_inventory import zone_quest_candidates
    candidates = zone_quest_candidates(c, u, p, "tdb_world", sorts, args.zone, args.map)
    native = {r[0] for r in run(c, u, p, f"SELECT ID FROM {args.base}.quest_template")}
    todo = [q for q in candidates if q not in native]
    summary = {"zone": args.zone, "quest_sorts": sorts, "candidates": len(candidates),
               "already_native": len(candidates) - len(todo)}

    # Quests whose objective has no template in TDB or AC cannot land.
    skip, reasons = set(), {}
    for quest, *objectives in run(c, u, p, f"SELECT ID,{','.join(OBJECTIVES)} FROM tdb_world.quest_template "
                                           f"WHERE ID IN ({','.join(todo) or '0'})"):
        for value in map(int, objectives):
            table, entry = ("creature_template", value) if value > 0 else ("gameobject_template", -value)
            if value and not scalar(c, u, p, f"SELECT COUNT(*) FROM tdb_world.{table} WHERE entry={entry}") \
                    and not scalar(c, u, p, f"SELECT COUNT(*) FROM {args.base}.{table} WHERE entry={entry}"):
                skip.add(quest)
                reasons.setdefault(quest, []).append(f"{table} {entry} missing")

    # Dry runs find the quest items; items the client DB2 lacks take their quests out, repeat until stable.
    item_ids, absent = set(), set()
    for _ in range(6):
        report = importer(args, args.clean, args.base, skip, False, sorts)
        needed = set(report["closure"]["missing_items"]) - item_ids
        sql, lacking = convert_items(needed, args.dbc_dir)
        item_ids |= needed - set(lacking)
        absent |= set(lacking)
        blocked = quests_using(c, u, p, "tdb_world", [q for q in todo if q not in skip], set(lacking))
        for quest in blocked:
            reasons.setdefault(quest, []).append("items absent from the client DB2")
        skip |= blocked
        if not lacking:
            break
    else:
        raise SystemExit("skip list did not stabilise")

    item_file = PENDING / sh(["bash", str(PENDING / "create_sql.sh")]).stdout.strip().removeprefix("File created: ")
    landing_file = PENDING / sh(["bash", str(PENDING / "create_sql.sh")]).stdout.strip().removeprefix("File created: ")
    sql, _ = convert_items(item_ids, args.dbc_dir)
    if sql:
        item_file.write_text(sql, encoding="utf-8")
        for schema in (args.base, args.clean):
            mysql_file(c, args.password, schema, item_file)
    else:  # every needed item is already in the target; leave no empty file behind
        item_file.unlink()

    # Apply, then add the loot items the import brought in; items the client DB2 lacks stay reported.
    report = importer(args, args.clean, args.base, skip, True, sorts)
    extra = {x["id"] for x in report["unresolved_items"]} - absent
    if extra:
        sql, lacking = convert_items(extra, args.dbc_dir)
        absent |= set(lacking)
        for schema in (args.base, args.clean):
            tmp = item_file.with_suffix(".extra")
            tmp.write_text(sql, encoding="utf-8")
            mysql_file(c, args.password, schema, tmp)
            tmp.unlink()
        item_ids |= extra - set(lacking)
        item_sql = convert_items(item_ids, args.dbc_dir)[0]
        if item_sql:
            item_file.write_text(item_sql, encoding="utf-8")
        report = importer(args, args.clean, args.base, skip, True, sorts)
    summary["dangling"] = report["dangling"]
    summary["preexisting_dangling"] = report.get("preexisting_dangling", [])
    summary["items_absent_from_client_db2"] = sorted(absent, key=int)
    summary["unresolved_items"] = [x for x in report["unresolved_items"] if x["id"] not in absent]
    summary["stripped_script_names"] = report["imported_closure"].get("stripped_script_names", [])
    summary["cleared_quest_links"] = report.get("cleared_quest_links", [])
    summary["skipped_quests"] = {q: reasons[q] for q in sorted(skip, key=int)}

    skipped = ",".join(sorted(skip, key=int))
    item_note = (" Needs the item rows of the item_template file in this directory."
                 if item_file.exists() else "")
    header = (f"{args.label} (zone {args.zone}, map {args.map}) starter content from TDB 434 (#203, #{args.issue}). "
              f"Generated by apps/cata/land_zone.py with emit_pending_sql.py; spawn guids +10000000, waypoint path "
              f"ids +100000000."
              + (f" Skipped quests (items absent from the client data or an objective with no template): {skipped}."
                 if skipped else "") + item_note)
    emitted = sh([sys.executable, str(HERE / "emit_pending_sql.py"), "--baseline", args.base, "--after", args.clean,
                  "--header", header, "--out", str(landing_file)])
    if emitted.returncode:
        raise SystemExit(f"emit failed: {emitted.stderr[-400:]}")
    lint = sh([sys.executable, "apps/codestyle/codestyle-sql.py"])
    summary["lint_ok"] = lint.returncode == 0
    clone(args.base, [args.replay])
    mysql_file(c, args.password, args.replay, landing_file)
    again = sh([sys.executable, str(HERE / "emit_pending_sql.py"), "--baseline", args.replay, "--after", args.clean])
    summary["replay_diff_bytes"] = len(again.stdout.strip()) if again.returncode == 0 else -1
    from starter_content_inventory import check_relations
    summary["relation_problems"] = check_relations(c, u, p, args.replay, args.zone, args.map, sorts, candidates)
    summary["files"] = {"items": item_file.name if item_file.exists() else None,
                        "landing": landing_file.name,
                        "landing_bytes": landing_file.stat().st_size}
    print(json.dumps(summary, indent=2))
    ok = (not summary["dangling"] and summary["lint_ok"] and summary["replay_diff_bytes"] == 0
          and not summary["relation_problems"])
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
