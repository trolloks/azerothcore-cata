#!/usr/bin/env python3
"""Per-zone starter-content inventory and relation checker for #175/#202.

Runs read-only queries against a disposable MySQL container (the run-owned
plan175-inventory-mysql instance) that holds the AC world schema and the
TDB 434 world schema. It reports, per zone, the denominators that the 1-20
landings must cover and the dangling relations that would break the import.

The checker is deliberately strict: any unresolved quest objective, reward,
starter/ender, loot reference or SmartAI link makes it exit non-zero.
"""

import argparse
import json
import subprocess
import sys


def query(container: str, user: str, password: str, schema: str, sql: str) -> list[list[str]]:
    result = subprocess.run(
        ["docker", "exec", container, "mysql", f"-u{user}", f"-p{password}", "-N", "-B", schema, "-e", sql],
        capture_output=True, text=True, check=False,
    )
    if result.returncode:
        raise RuntimeError(f"query failed: {sql}\n{result.stderr.strip()}")
    rows = []
    for line in result.stdout.splitlines():
        rows.append(line.split("\t"))
    return rows


def scalar(container, user, password, schema, sql) -> int:
    rows = query(container, user, password, schema, sql)
    return int(rows[0][0]) if rows and rows[0] and rows[0][0] not in ("", "NULL") else 0


def columns(container, user, password, schema, table) -> set[str]:
    rows = query(container, user, password, schema,
                 f"SELECT COLUMN_NAME FROM information_schema.columns "
                 f"WHERE TABLE_SCHEMA='{schema}' AND TABLE_NAME='{table}'")
    return {r[0] for r in rows if r}


def table_exists(container, user, password, schema, table) -> bool:
    rows = query(container, user, password, schema,
                 f"SELECT COUNT(*) FROM information_schema.tables "
                 f"WHERE TABLE_SCHEMA='{schema}' AND TABLE_NAME='{table}'")
    return bool(rows and rows[0] and rows[0][0] == "1")


def zone_creature_entries(container, user, password, schema, zone, map_id) -> set[int]:
    if not table_exists(container, user, password, schema, "creature"):
        return set()
    cols = columns(container, user, password, schema, "creature")
    zone_col = "zoneId" if "zoneId" in cols else ("zone" if "zone" in cols else None)
    # The AC base names the entry column id1; the worldserver's effective schema renames it to id.
    entry_col = "id" if "id" in cols else ("id1" if "id1" in cols else None)
    if not zone_col or not entry_col:
        return set()
    rows = query(container, user, password, schema,
                 f"SELECT DISTINCT {entry_col} FROM creature WHERE ({zone_col}={zone} OR areaId={zone}) AND map={map_id}")
    return {int(r[0]) for r in rows if r and r[0].isdigit()}


def creature_loot_ids(container, user, password, schema, zone, map_id, column="lootid") -> set[int]:
    if not (table_exists(container, user, password, schema, "creature")
            and table_exists(container, user, password, schema, "creature_template")):
        return set()
    cols = columns(container, user, password, schema, "creature")
    entry_col = "id" if "id" in cols else ("id1" if "id1" in cols else None)
    if not entry_col:
        return set()
    rows = query(container, user, password, schema,
                 f"SELECT DISTINCT ct.{column} FROM creature c "
                 f"JOIN creature_template ct ON ct.entry=c.{entry_col} "
                 f"WHERE (c.zoneId={zone} OR c.areaId={zone}) AND c.map={map_id} AND ct.{column}<>0")
    return {int(r[0]) for r in rows if r and r[0].isdigit() and int(r[0]) > 0}


def gameobject_loot_ids(container, user, password, schema, zone, map_id) -> set[int]:
    if not (table_exists(container, user, password, schema, "gameobject")
            and table_exists(container, user, password, schema, "gameobject_template")):
        return set()
    # GameObjectTemplate::GetLootId(): chest (3) and fishinghole (25) use Data1.
    rows = query(container, user, password, schema,
                 f"SELECT DISTINCT gt.Data1 FROM gameobject g "
                 f"JOIN gameobject_template gt ON gt.entry=g.id "
                 f"WHERE (g.zoneId={zone} OR g.areaId={zone}) AND g.map={map_id} AND gt.type IN (3,25) AND gt.Data1<>0")
    return {int(r[0]) for r in rows if r and r[0].isdigit() and int(r[0]) > 0}


def zone_gameobject_entries(container, user, password, schema, zone, map_id) -> set[int]:
    if not table_exists(container, user, password, schema, "gameobject"):
        return set()
    cols = columns(container, user, password, schema, "gameobject")
    zone_col = "zoneId" if "zoneId" in cols else ("zone" if "zone" in cols else None)
    if not zone_col:
        return set()
    rows = query(container, user, password, schema,
                 f"SELECT DISTINCT id FROM gameobject WHERE ({zone_col}={zone} OR areaId={zone}) AND map={map_id}")
    return {int(r[0]) for r in rows if r and r[0].isdigit()}


def zone_guids(container, user, password, schema, table, zone, map_id) -> set[int]:
    if not table_exists(container, user, password, schema, table):
        return set()
    cols = columns(container, user, password, schema, table)
    zone_col = "zoneId" if "zoneId" in cols else ("zone" if "zone" in cols else None)
    if not zone_col:
        return set()
    rows = query(container, user, password, schema,
                 f"SELECT guid FROM {table} WHERE ({zone_col}={zone} OR areaId={zone}) AND map={map_id}")
    return {int(r[0]) for r in rows if r and r[0].isdigit()}


def inventory(container, user, password, schema, zone, map_id) -> dict[str, int]:
    if not table_exists(container, user, password, schema, "quest_template"):
        return {"tables": 0}
    quest_cols = columns(container, user, password, schema, "quest_template")
    sort_col = "QuestSortID" if "QuestSortID" in quest_cols else "ZoneOrSort"
    result = {
        "quests": scalar(container, user, password, schema,
                         f"SELECT COUNT(*) FROM quest_template WHERE {sort_col}={zone}"),
        "creatures": scalar(container, user, password, schema,
                            f"SELECT COUNT(*) FROM creature WHERE (zoneId={zone} OR areaId={zone}) AND map={map_id}"),
        "gameobjects": scalar(container, user, password, schema,
                              f"SELECT COUNT(*) FROM gameobject WHERE (zoneId={zone} OR areaId={zone}) AND map={map_id}"),
    }

    creature_entries = zone_creature_entries(container, user, password, schema, zone, map_id)
    gameobject_entries = zone_gameobject_entries(container, user, password, schema, zone, map_id)
    creature_guids = zone_guids(container, user, password, schema, "creature", zone, map_id)
    gameobject_guids = zone_guids(container, user, password, schema, "gameobject", zone, map_id)

    def count_linked(table: str, column: str, ids: set[int]) -> int:
        if not table_exists(container, user, password, schema, table) or not ids:
            return 0
        joined = ",".join(str(e) for e in sorted(ids))
        return scalar(container, user, password, schema,
                      f"SELECT COUNT(*) FROM {table} WHERE {column} IN ({joined})")

    creature_loot = creature_loot_ids(container, user, password, schema, zone, map_id)
    go_loot = gameobject_loot_ids(container, user, password, schema, zone, map_id)
    result["creature_loot"] = count_linked("creature_loot_template", "Entry", creature_loot)
    result["gameobject_loot"] = count_linked("gameobject_loot_template", "Entry", go_loot)
    creature_keys = {str(e) for e in creature_entries} | {str(-g) for g in creature_guids}
    go_keys = {str(e) for e in gameobject_entries} | {str(-g) for g in gameobject_guids}
    if table_exists(container, user, password, schema, "smart_scripts"):
        parts = []
        if creature_keys:
            parts.append(f"(entryOrGuid IN ({','.join(sorted(creature_keys))}) AND source_type=0)")
        if go_keys:
            parts.append(f"(entryOrGuid IN ({','.join(sorted(go_keys))}) AND source_type=1)")
        result["smart_scripts"] = scalar(
            container, user, password, schema,
            "SELECT COUNT(*) FROM smart_scripts WHERE " + " OR ".join(parts)) if parts else 0
    else:
        result["smart_scripts"] = 0
    entries = creature_entries | gameobject_entries
    zone_quest_ids = set()
    if table_exists(container, user, password, schema, "quest_template"):
        zone_quest_ids = {r[0] for r in query(container, user, password, schema,
                                              f"SELECT ID FROM quest_template WHERE {sort_col}={zone}") if r}
    condition_parts = []
    def condition_part(source_type: int, column: str, ids: set[int]) -> str:
        joined = ",".join(str(i) for i in sorted(ids))
        return f"(SourceTypeOrReferenceId={source_type} AND {column} IN ({joined}))"
    if zone_quest_ids:
        condition_parts.append(condition_part(19, "SourceEntry", zone_quest_ids))
    if creature_loot:
        condition_parts.append(condition_part(1, "SourceGroup", creature_loot))
    if creature_entries:
        condition_parts.append(condition_part(23, "SourceGroup", creature_entries))
    if go_loot:
        condition_parts.append(condition_part(4, "SourceGroup", go_loot))
    if creature_keys:
        joined = ",".join(sorted(creature_keys))
        condition_parts.append(f"(SourceTypeOrReferenceId=22 AND SourceEntry IN ({joined}) AND SourceId=0)")
    if go_keys:
        joined = ",".join(sorted(go_keys))
        condition_parts.append(f"(SourceTypeOrReferenceId=22 AND SourceEntry IN ({joined}) AND SourceId=1)")
    if condition_parts and table_exists(container, user, password, schema, "conditions"):
        result["conditions"] = scalar(container, user, password, schema,
                                      "SELECT COUNT(*) FROM conditions WHERE " + " OR ".join(condition_parts))
    else:
        result["conditions"] = 0
    if table_exists(container, user, password, schema, "phase_area"):
        result["phase_area"] = scalar(container, user, password, schema,
                                       f"SELECT COUNT(*) FROM phase_area WHERE AreaId={zone}")
    else:
        result["phase_area"] = 0
    if table_exists(container, user, password, schema, "npc_vendor") and creature_entries:
        joined = ",".join(str(e) for e in creature_entries)
        result["vendor_rows"] = scalar(container, user, password, schema,
                                       f"SELECT COUNT(*) FROM npc_vendor WHERE entry IN ({joined})")
    else:
        result["vendor_rows"] = 0
    if result["creatures"] == 0 and table_exists(container, user, password, schema, "creature"):
        result["creatures_note"] = "zoneId unset in this schema (AC WotLK spawns); TDB is the source denominator"
    result["tables"] = 1
    return result


def check_relations(container, user, password, schema, zone, map_id) -> list[dict[str, str]]:
    """Return dangling relations for the zone's quests and spawns."""
    problems: list[dict[str, str]] = []
    if not table_exists(container, user, password, schema, "quest_template"):
        return [{"kind": "quest_template_missing", "id": schema}]

    quest_cols = columns(container, user, password, schema, "quest_template")
    sort_col = "QuestSortID" if "QuestSortID" in quest_cols else "ZoneOrSort"
    objective_cols = [f"RequiredNpcOrGo{i}" for i in range(1, 5) if f"RequiredNpcOrGo{i}" in quest_cols]
    item_cols = [f"RequiredItemId{i}" for i in range(1, 7) if f"RequiredItemId{i}" in quest_cols]
    reward_cols = [f"RewardItem{i}" for i in range(1, 5) if f"RewardItem{i}" in quest_cols]
    choice_cols = [f"RewardChoiceItemID{i}" for i in range(1, 7) if f"RewardChoiceItemID{i}" in quest_cols]
    start_cols = [c for c in ["StartItem"] + [f"ItemDrop{i}" for i in range(1, 5)] if c in quest_cols]
    wanted = ["ID"] + objective_cols + item_cols + reward_cols + choice_cols + start_cols
    rows = query(container, user, password, schema,
                 f"SELECT {','.join(wanted)} FROM quest_template WHERE {sort_col}={zone}")
    zone_quests = {row[0] for row in rows}
    creature_entries = {r[0] for r in query(container, user, password, schema,
                                            "SELECT entry FROM creature_template") if r}
    gameobject_entries = {r[0] for r in query(container, user, password, schema,
                                              "SELECT entry FROM gameobject_template") if r}
    item_entries = set()
    has_items = table_exists(container, user, password, schema, "item_template")
    if has_items:
        item_entries = {r[0] for r in query(container, user, password, schema,
                                            "SELECT entry FROM item_template") if r}

    for row in rows:
        quest = row[0]
        offset = 1
        for _ in objective_cols:
            value = int(row[offset])
            if value > 0 and str(value) not in creature_entries:
                problems.append({"kind": "quest_objective_creature", "quest": quest, "id": str(value)})
            if value < 0 and str(-value) not in gameobject_entries:
                problems.append({"kind": "quest_objective_gameobject", "quest": quest, "id": str(-value)})
            offset += 1
        for _ in item_cols + reward_cols + choice_cols + start_cols:
            value = int(row[offset])
            # TDB 434 has no item_template (the items come from Item-sparse.db2); the AC side
            # can validate items, the TDB side cannot without loading the DB2.
            if has_items and value > 0 and str(value) not in item_entries:
                problems.append({"kind": "quest_item", "quest": quest, "id": str(value)})
            offset += 1

    zone_creatures = zone_creature_entries(container, user, password, schema, zone, map_id)
    zone_gos = zone_gameobject_entries(container, user, password, schema, zone, map_id)
    all_quests = {r[0] for r in query(container, user, password, schema,
                                      "SELECT ID FROM quest_template") if r}
    if zone_quests:
        joined = ",".join(sorted(zone_quests))
        links = [("quest_template", "RewardNextQuest")]
        if table_exists(container, user, password, schema, "quest_template_addon"):
            links += [("quest_template_addon", column)
                      for column in ("PrevQuestID", "NextQuestID", "BreadcrumbForQuestId")
                      if column in columns(container, user, password, schema, "quest_template_addon")]
        for table, column in links:
            if column not in columns(container, user, password, schema, table):
                continue
            for quest, value in query(container, user, password, schema,
                                      f"SELECT ID, {column} FROM {table} WHERE ID IN ({joined}) AND {column}<>0"):
                if str(abs(int(value))) not in all_quests:
                    problems.append({"kind": "quest_link", "quest": quest, "id": value, "from": column})
    for table, column, label in (
        ("creature_queststarter", "id", "quest_starter_creature"),
        ("creature_questender", "id", "quest_ender_creature"),
        ("gameobject_queststarter", "id", "quest_starter_gameobject"),
        ("gameobject_questender", "id", "quest_ender_gameobject"),
    ):
        if not table_exists(container, user, password, schema, table):
            continue
        source = creature_entries if "creature" in table else gameobject_entries
        for entry, quest in query(container, user, password, schema,
                                  f"SELECT DISTINCT {column}, quest FROM {table}"):
            # Include relations that belong to a zone spawn even when the quest row is missing.
            try:
                entry_id = int(entry)
            except ValueError:
                entry_id = None
            spawn_entries = zone_creatures if "creature" in table else zone_gos
            if entry_id not in spawn_entries and quest not in zone_quests:
                continue
            if entry not in source:
                problems.append({"kind": label, "quest": quest, "id": entry})
            if quest not in all_quests:
                problems.append({"kind": label + "_quest", "quest": quest, "id": entry})

    zone_creature_guids = zone_guids(container, user, password, schema, "creature", zone, map_id)
    zone_go_guids = zone_guids(container, user, password, schema, "gameobject", zone, map_id)
    creature_keys = {str(e) for e in zone_creatures} | {str(-g) for g in zone_creature_guids}
    go_keys = {str(e) for e in zone_gos} | {str(-g) for g in zone_go_guids}

    if table_exists(container, user, password, schema, "creature_template"):
        known_creatures = {int(r[0]) for r in query(container, user, password, schema,
                                                    "SELECT entry FROM creature_template") if r and r[0].isdigit()}
        for entry in zone_creatures:
            if entry not in known_creatures:
                problems.append({"kind": "creature_template_missing", "id": str(entry)})
    if table_exists(container, user, password, schema, "gameobject_template"):
        known_gameobjects = {int(r[0]) for r in query(container, user, password, schema,
                                                      "SELECT entry FROM gameobject_template") if r and r[0].isdigit()}
        for entry in zone_gos:
            if entry not in known_gameobjects:
                problems.append({"kind": "gameobject_template_missing", "id": str(entry)})

    direct_refs: set[str] = set()
    for table, entries, label in (
        ("creature_loot_template", creature_loot_ids(container, user, password, schema, zone, map_id),
         "creature_loot"),
        ("pickpocketing_loot_template",
         creature_loot_ids(container, user, password, schema, zone, map_id, "pickpocketloot"), "pickpocket_loot"),
        ("skinning_loot_template",
         creature_loot_ids(container, user, password, schema, zone, map_id, "skinloot"), "skinning_loot"),
        ("gameobject_loot_template", gameobject_loot_ids(container, user, password, schema, zone, map_id),
         "gameobject_loot"),
    ):
        if not entries:
            continue
        if not table_exists(container, user, password, schema, table):
            for entry in entries:
                problems.append({"kind": label + "_template_missing", "id": str(entry)})
            continue
        joined = ",".join(str(e) for e in sorted(entries))
        present = {r[0] for r in query(container, user, password, schema,
                                       f"SELECT DISTINCT Entry FROM {table} WHERE Entry IN ({joined})") if r}
        for entry in entries:
            if str(entry) not in present:
                problems.append({"kind": label + "_template_missing", "id": str(entry)})
        if has_items:  # the loot validator drops rows whose item is unknown
            for item, in query(container, user, password, schema,
                               f"SELECT DISTINCT Item FROM {table} WHERE Entry IN ({joined}) AND Reference=0 "
                               f"AND Item>0"):
                if item not in item_entries:
                    problems.append({"kind": "loot_item", "id": item, "from": label})
        refs = {r[0] for r in query(container, user, password, schema,
                                    f"SELECT DISTINCT Reference FROM {table} WHERE Entry IN ({joined}) AND Reference<>0") if r}
        if refs and not table_exists(container, user, password, schema, "reference_loot_template"):
            problems.append({"kind": label + "_reference_table_missing", "id": "reference_loot_template"})
            continue
        if refs:
            known = {r[0] for r in query(container, user, password, schema,
                                         "SELECT Entry FROM reference_loot_template") if r}
            for ref in refs:
                if ref not in known:
                    problems.append({"kind": label + "_reference", "id": ref})
                else:
                    direct_refs.add(ref)

    if direct_refs and table_exists(container, user, password, schema, "reference_loot_template"):
        graph: dict[str, set[str]] = {}
        known_refs = {r[0] for r in query(container, user, password, schema,
                                          "SELECT Entry FROM reference_loot_template") if r}
        for entry, ref in query(container, user, password, schema,
                                "SELECT Entry, Reference FROM reference_loot_template WHERE Reference<>0"):
            graph.setdefault(entry, set()).add(ref)
        stack = list(direct_refs)
        visited: set[str] = set()
        while stack:
            current = stack.pop()
            if current in visited:
                continue
            visited.add(current)
            if has_items:
                for item, in query(container, user, password, schema,
                                   f"SELECT DISTINCT Item FROM reference_loot_template WHERE Entry={current} "
                                   f"AND Reference=0 AND Item>0"):
                    if item not in item_entries:
                        problems.append({"kind": "loot_item", "id": item, "from": current})
            for nested in graph.get(current, set()):
                if nested not in known_refs:
                    problems.append({"kind": "reference_loot_nested", "id": nested, "from": current})
                else:
                    stack.append(nested)

    if table_exists(container, user, password, schema, "smart_scripts") and (creature_keys or go_keys):
        parts = []
        if creature_keys:
            parts.append(f"(entryorguid IN ({','.join(sorted(creature_keys))}) AND source_type=0)")
        if go_keys:
            parts.append(f"(entryorguid IN ({','.join(sorted(go_keys))}) AND source_type=1)")
        link_rows = query(container, user, password, schema,
                          f"SELECT entryorguid, source_type, id, link, event_type FROM smart_scripts "
                          f"WHERE " + " OR ".join(parts))
        events_by_script: dict[tuple[str, str], dict[str, str]] = {}
        for guid, source_type, event_id, _link, event_type in link_rows:
            events_by_script.setdefault((guid, source_type), {})[event_id] = event_type
        for guid, source_type, event_id, link, _event_type in link_rows:
            # SmartScript::ProcessEvent follows e.link to an event with the same id; the target
            # must be a SMART_EVENT_LINK (61) row in the same script (entryOrGuid + source_type).
            if link in ("", "0"):
                continue
            if link == event_id:
                # SmartAIMgr::IsEventValid rejects a SMART_EVENT_LINK whose link is its own id.
                if _event_type == "61":
                    problems.append({"kind": "smartai_link_self", "guid": guid, "source_type": source_type,
                                     "event": event_id, "link": link})
                continue
            target = events_by_script.get((guid, source_type), {}).get(link)
            if target != "61":
                problems.append({"kind": "smartai_link", "guid": guid, "source_type": source_type,
                                 "event": event_id, "link": link})

    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--container", default="plan175-inventory-mysql")
    parser.add_argument("--user", default="root")
    parser.add_argument("--password", default="plan175ro")
    parser.add_argument("--ac-schema", default="acore_world")
    parser.add_argument("--tdb-schema", default="tdb_world")
    parser.add_argument("--zone", type=int, default=12)
    parser.add_argument("--map", type=int, default=0)
    parser.add_argument("--metadata-only", action="store_true",
                        help="skip the relation checker and print only the denominators")
    args = parser.parse_args()

    report = {
        "zone": args.zone,
        "map": args.map,
        "ac": inventory(args.container, args.user, args.password, args.ac_schema, args.zone, args.map),
        "tdb": inventory(args.container, args.user, args.password, args.tdb_schema, args.zone, args.map),
    }
    problems = [] if args.metadata_only else check_relations(
        args.container, args.user, args.password, args.tdb_schema, args.zone, args.map)
    report["tdb_dangling"] = problems
    print(json.dumps(report, indent=2))
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
