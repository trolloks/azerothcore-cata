#!/usr/bin/env python3
"""Validate a built clean world against the trusted golden schema (#176).

The client harness imports a world dump, so a stale or incomplete clean dump can
reach the client even when every source build and unit test passes. This checker
covers the content transforms that have caused real regressions: missing Cata
columns/tables, empty creature base stats, stripped script bindings, and the
SmartAI escort-action speed normalization.
"""

import argparse
import json
import re
import subprocess
import sys


def query(container: str, user: str, password: str, schema: str, sql: str) -> list[list[str]]:
    result = subprocess.run(
        ["docker", "exec", container, "mysql", f"-u{user}", f"-p{password}", "-N", "-B", schema, "-e", sql],
        capture_output=True, text=True, check=False,
    )
    if result.returncode:
        raise RuntimeError(f"query failed: {sql}\n{result.stderr.strip()}")
    return [line.split("\t") for line in result.stdout.splitlines()]


def scalar(container, user, password, schema, sql) -> int:
    rows = query(container, user, password, schema, sql)
    return int(rows[0][0]) if rows and rows[0] and rows[0][0] not in ("", "NULL") else 0


def columns(container, user, password, schema, table) -> set[str]:
    rows = query(container, user, password, schema,
                 "SELECT COLUMN_NAME FROM information_schema.COLUMNS "
                 f"WHERE TABLE_SCHEMA='{schema}' AND TABLE_NAME='{table}'")
    return {row[0] for row in rows if row}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--container", default="plan175-inventory-mysql")
    parser.add_argument("--user", default="root")
    parser.add_argument("--password", default="plan175ro")
    parser.add_argument("--clean", required=True, help="built clean world schema")
    parser.add_argument("--reference", default="acore_world_gold", help="trusted golden world schema")
    args = parser.parse_args()
    for name in (args.clean, args.reference):
        if not re.fullmatch(r"\w+", name):
            parser.error(f"invalid schema name: {name!r}")

    problems: list[str] = []
    report: dict[str, object] = {"clean": args.clean, "reference": args.reference, "problems": problems}

    required_columns = {
        ("creature", "PhaseGroup"),
        ("gameobject", "PhaseGroup"),
        ("phase_area", "PhaseGroup"),
        ("creature_classlevelstats", "basehp3"),
        ("creature_classlevelstats", "damage_exp3"),
    }
    for table, column in sorted(required_columns):
        if column not in columns(args.container, args.user, args.password, args.clean, table):
            problems.append(f"missing column {table}.{column}")

    counts = {}
    for table in ("creature", "gameobject", "creature_template", "creature_classlevelstats"):
        clean = scalar(args.container, args.user, args.password, args.clean, f"SELECT COUNT(*) FROM `{table}`")
        reference = scalar(args.container, args.user, args.password, args.reference, f"SELECT COUNT(*) FROM `{table}`")
        counts[table] = {"clean": clean, "reference": reference}
        if clean == 0:
            problems.append(f"{table} is empty")
        if table in ("creature", "gameobject", "creature_classlevelstats") and clean != reference:
            problems.append(f"{table} count differs: clean={clean}, reference={reference}")
    report["counts"] = counts

    missing_templates = scalar(
        args.container, args.user, args.password, args.clean,
        "SELECT COUNT(*) FROM "
        f"`{args.reference}`.creature_template g LEFT JOIN creature_template c ON c.entry=g.entry "
        "WHERE c.entry IS NULL",
    )
    report["missing_creature_templates"] = missing_templates
    if missing_templates:
        problems.append(f"{missing_templates} creature templates from {args.reference} are missing")

    script_mismatches = scalar(
        args.container, args.user, args.password, args.clean,
        "SELECT COUNT(*) FROM "
        f"`{args.reference}`.creature_template g LEFT JOIN creature_template c ON c.entry=g.entry "
        "WHERE c.entry IS NULL OR c.ScriptName<>g.ScriptName",
    )
    report["script_binding_mismatches"] = script_mismatches
    if script_mismatches:
        problems.append(f"{script_mismatches} ScriptName bindings differ from {args.reference}")

    escort = {}
    for param in (1, 2):
        clean = scalar(args.container, args.user, args.password, args.clean,
                       f"SELECT COUNT(*) FROM smart_scripts WHERE action_type=53 AND action_param1={param}")
        reference = scalar(args.container, args.user, args.password, args.reference,
                           f"SELECT COUNT(*) FROM smart_scripts WHERE action_type=53 AND action_param1={param}")
        escort[str(param)] = {"clean": clean, "reference": reference}
        if clean != reference:
            problems.append(f"SmartAI escort action_param1={param} differs: clean={clean}, reference={reference}")
    report["escort_action_counts"] = escort

    for table in ("phase_area", "terrain_swap_defaults", "terrain_worldmap"):
        count = scalar(args.container, args.user, args.password, args.clean, f"SELECT COUNT(*) FROM `{table}`")
        report[table] = count
        if count == 0:
            problems.append(f"{table} is empty")

    legacy_conditions = scalar(args.container, args.user, args.password, args.clean,
                               "SELECT COUNT(*) FROM `conditions` WHERE ConditionTypeOrReference=26")
    report["legacy_phase_conditions"] = legacy_conditions
    if legacy_conditions:
        problems.append(f"{legacy_conditions} legacy phase-mask conditions remain")

    # 59073 is the Gilneas phase-170 aura. Autocast is valid to disable for other
    # spell_area rows, but this phase must be applied automatically in every area.
    phase_autocast_mismatches = scalar(
        args.container, args.user, args.password, args.clean,
        "SELECT COUNT(*) FROM `spell_area` WHERE `spell`=59073 AND `autocast`<>1",
    )
    report["gilneas_phase_autocast_mismatches"] = phase_autocast_mismatches
    if phase_autocast_mismatches:
        problems.append(
            f"{phase_autocast_mismatches} Gilneas phase 59073 rows are not autocast")

    # A class trainer with several GOSSIP_OPTION_TRAINER rows shows duplicate
    # training entries. Profession trainers legitimately have more than one.
    duplicate_trainer_options = scalar(
        args.container, args.user, args.password, args.clean,
        "SELECT COUNT(*) FROM ("
        "SELECT g.MenuID FROM gossip_menu_option g "
        "JOIN creature_template c ON c.gossip_menu_id=g.MenuID "
        "JOIN creature_default_trainer cdt ON cdt.CreatureId=c.entry "
        "JOIN trainer t ON t.Id=cdt.TrainerId "
        "WHERE g.OptionType=5 AND t.Type=0 "
        "GROUP BY g.MenuID HAVING COUNT(DISTINCT g.OptionID)>1"
        ") AS duplicates",
    )
    report["duplicate_class_trainer_options"] = duplicate_trainer_options
    if duplicate_trainer_options:
        problems.append(
            f"{duplicate_trainer_options} class-trainer menus have duplicate training options")

    low_level_stats = scalar(args.container, args.user, args.password, args.clean,
                             "SELECT COUNT(*) FROM creature_classlevelstats "
                             "WHERE level<=5 AND (basehp0<=1 OR damage_base<=0)")
    report["bad_low_level_stats"] = low_level_stats
    if low_level_stats:
        problems.append(f"{low_level_stats} low-level class stats have 1 HP or zero damage")

    print(json.dumps(report, indent=2, sort_keys=True))
    if problems:
        print("clean world FAIL", file=sys.stderr)
        return 1
    print("clean world PASS")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, RuntimeError, ValueError) as error:
        print(f"clean world check failed: {error}", file=sys.stderr)
        sys.exit(1)
