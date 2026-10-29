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
import re
import struct
import subprocess
import sys
from pathlib import Path


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
                 f"SELECT DISTINCT {entry_col} FROM creature "
                 f"WHERE ({zone_col}={zone} OR areaId={zone}) AND map={map_id}")
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
                              f"SELECT COUNT(*) FROM gameobject "
                              f"WHERE (zoneId={zone} OR areaId={zone}) AND map={map_id}"),
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


_DBC_ID_CACHE: dict[tuple[str, float], set[int]] = {}


def dbc_ids(path: Path) -> set[int]:
    """First-field ids of a client WDBC file (Spell, Faction, AreaTable, ...)."""
    key = (str(path), path.stat().st_mtime)
    if key not in _DBC_ID_CACHE:
        data = path.read_bytes()
        magic, records, _fields, record_size, _string_size = struct.unpack("<4sIIII", data[:20])
        if magic != b"WDBC":
            raise ValueError(f"{path} is not a WDBC file")
        _DBC_ID_CACHE[key] = {struct.unpack_from("<I", data, 20 + index * record_size)[0]
                              for index in range(records)}
    return _DBC_ID_CACHE[key]


def registered_script_names() -> set[str]:
    """Read the same source registrations used by the importer's ScriptName filter."""
    registered = "\n".join(path.read_text(errors="replace")
                           for path in (Path(__file__).resolve().parents[2] / "src/server/scripts").rglob("*.cpp"))
    registered = re.sub(r'"(?:\\.|[^"\\])*"|//[^\n]*|/\*.*?\*/',
                        lambda m: m[0] if m[0].startswith('"') else "", registered, flags=re.S)
    names = set(re.findall(r'\bRegister\w*AI(?:WithFactory)?\s*\(\s*(\w+)', registered))
    names.update(re.findall(r'\b(?:Creature|GameObject)Script\s*\(\s*"([^"\\]+)"', registered))
    names.update(re.findall(r'\bConditionScript\s*\(\s*"([^"\\]+)"', registered))
    return names


def check_loader_relations(container, user, password, schema, creatures, gameobjects, creature_guids, go_guids,
                           quests, loot_entries) -> list[dict[str, str]]:
    """Check SQL-backed loader dependencies in the imported closure; client stores are not SQL tables."""
    problems = []
    metadata = {}
    known = {}

    def cols(table):
        if table not in metadata:
            metadata[table] = (columns(container, user, password, schema, table)
                               if table_exists(container, user, password, schema, table) else set())
        return metadata[table]

    def ids(table, key):
        if (table, key) not in known:
            known[table, key] = ({r[0] for r in query(container, user, password, schema,
                                                    f"SELECT DISTINCT {key} FROM {table}")}
                                 if key in cols(table) else None)
        return known[table, key]

    def check(kind, value, table, key, owner, optional=True):
        values = ids(table, key)
        if values is not None and (not optional or value != "0") and value not in values:
            problems.append({"kind": kind, "id": value, "from": owner})

    def scope(key, values):
        return f"s.{key} IN ({','.join(map(str, sorted(values)))})" if values else "0"

    def relation(table, key, column, target, target_key, where, kind, match=None, optional=True):
        if not {key, column} <= cols(table) or target_key not in cols(target):
            return
        match = match or f"t.{target_key}=s.{column}"
        nonzero = f" AND s.{column}<>0" if optional else ""
        for owner, value in query(container, user, password, schema,
                                   f"SELECT DISTINCT s.{key}, s.{column} FROM {table} s WHERE ({where}){nonzero} "
                                   f"AND NOT EXISTS (SELECT 1 FROM {target} t WHERE {match})"):
            problems.append({"kind": kind, "id": value, "from": f"{table}.{owner}.{column}"})

    creature_scope = scope("entry", creatures)
    go_scope = scope("entry", gameobjects)
    for column in ("difficulty_entry_1", "difficulty_entry_2", "difficulty_entry_3", "KillCredit1", "KillCredit2"):
        relation("creature_template", "entry", column, "creature_template", "entry", creature_scope,
                 "creature_template_reference")
    relation("creature_template", "entry", "entry", "creature_template_model", "CreatureID", creature_scope,
             "creature_model_missing", optional=False)
    relation("creature_template_model", "CreatureID", "CreatureID", "creature_template", "entry",
             scope("CreatureID", creatures), "creature_model_owner", optional=False)
    relation("creature_template_model", "CreatureID", "CreatureDisplayID", "creature_model_info", "DisplayID",
             scope("CreatureID", creatures), "creature_model_info_missing", optional=False)
    model_ids = set()
    if {"CreatureID", "CreatureDisplayID"} <= cols("creature_template_model"):
        model_ids = {r[0] for r in query(container, user, password, schema,
                                        "SELECT CreatureDisplayID FROM creature_template_model s WHERE "
                                        + scope("CreatureID", creatures))}
    # Follow alternate genders too: ChooseCreatureModelRandomGender reads their model-info rows at runtime.
    pending = model_ids.copy()
    while pending and {"DisplayID", "DisplayID_Other_Gender"} <= cols("creature_model_info"):
        batch, pending = pending, set()
        for display, other in query(container, user, password, schema,
                                     "SELECT DisplayID, DisplayID_Other_Gender FROM creature_model_info s WHERE "
                                     + scope("DisplayID", batch)):
            check("creature_model_other_gender", other, "creature_model_info", "DisplayID", display)
            if other != "0" and other not in model_ids:
                model_ids.add(other)
                pending.add(other)

    path_ids = set()
    for table, key, owners, target, target_key in (
        ("creature_template_addon", "entry", creatures, "creature_template", "entry"),
        ("creature_addon", "guid", creature_guids, "creature", "guid"),
        ("gameobject_addon", "guid", go_guids, "gameobject", "guid"),
        ("npc_spellclick_spells", "npc_entry", creatures, "creature_template", "entry"),
    ):
        relation(table, key, key, target, target_key, scope(key, owners), table + "_owner", optional=False)
        if table.startswith("creature_") and "addon" in table:
            relation(table, key, "path_id", "waypoint_data", "id", scope(key, owners), "creature_addon_path")
            if {key, "path_id"} <= cols(table):
                path_ids.update(r[0] for r in query(container, user, password, schema,
                                f"SELECT path_id FROM {table} s WHERE {scope(key, owners)} AND path_id<>0"))
    relation("waypoint_data", "id", "action", "waypoint_scripts", "id", scope("id", path_ids),
             "waypoint_script")
    if {"id", "equipment_id"} <= cols("creature") and "ID" in cols("creature_equip_template"):
        relation("creature", "guid", "equipment_id", "creature_equip_template", "CreatureID",
                 scope("guid", creature_guids), "creature_equipment",
                 "t.CreatureID=s.id AND (s.equipment_id=-1 OR t.ID=s.equipment_id)")
    for table, owners, spawn_table in (("game_event_creature", creature_guids, "creature"),
                                       ("game_event_gameobject", go_guids, "gameobject")):
        relation(table, "guid", "guid", spawn_table, "guid", scope("guid", owners),
                 table + "_spawn", optional=False)
        relation(table, "guid", "eventEntry", "game_event", "eventEntry", scope("guid", owners),
                 table + "_event", "t.eventEntry=ABS(s.eventEntry)", optional=False)
    formation_scope = f"{scope('leaderGUID', creature_guids)} OR {scope('memberGUID', creature_guids)}"
    for column in ("leaderGUID", "memberGUID"):
        if {"leaderGUID", "memberGUID"} <= cols("creature_formations"):
            relation("creature_formations", "memberGUID", column, "creature", "guid", formation_scope,
                     "formation_spawn", optional=False)
    for table in ("quest_template_addon", "quest_offer_reward", "quest_request_items"):
        relation(table, "ID", "ID", "quest_template", "ID", scope("ID", quests),
                 "quest_companion_owner", optional=False)
    if "type" in cols("gameobject_template"):
        for go_type, column in ((1, "Data3"), (3, "Data7"), (8, "Data2"), (10, "Data12")):
            relation("gameobject_template", "entry", column, "gameobject_template", "entry",
                     f"{go_scope} AND s.type={go_type}", "gameobject_linked_trap")
        relation("gameobject_template", "entry", "Data7", "page_text", "ID",
                 f"{go_scope} AND s.type=10", "gameobject_page")
    relation("creature_equip_template", "CreatureID", "CreatureID", "creature_template", "entry",
             scope("CreatureID", creatures), "creature_equipment_owner", optional=False)
    page_ids = set()
    if {"entry", "type", "Data7"} <= cols("gameobject_template"):
        page_ids = {r[0] for r in query(container, user, password, schema,
                                       "SELECT Data7 FROM gameobject_template s WHERE " + go_scope
                                       + " AND type=10 AND Data7<>0")}
    pending_pages = page_ids.copy()
    while pending_pages and {"ID", "NextPageID"} <= cols("page_text"):
        batch, pending_pages = pending_pages, set()
        for page, next_page in query(container, user, password, schema,
                                      "SELECT ID, NextPageID FROM page_text s WHERE " + scope("ID", batch)):
            check("page_next", next_page, "page_text", "ID", page)
            if next_page != "0" and next_page not in page_ids:
                page_ids.add(next_page)
                pending_pages.add(next_page)
    relation("creature_text", "CreatureID", "BroadcastTextId", "broadcast_text", "ID",
             scope("CreatureID", creatures), "creature_text_broadcast")

    registrations = None
    for table, key, owners in (("creature_template", "entry", creatures),
                                ("gameobject_template", "entry", gameobjects),
                                ("creature", "guid", creature_guids), ("gameobject", "guid", go_guids)):
        if not owners or not {key, "ScriptName"} <= cols(table):
            continue
        for owner, name in query(container, user, password, schema,
                                 f"SELECT {key}, ScriptName FROM {table} s WHERE {scope(key, owners)} "
                                 "AND ScriptName<>''"):
            if registrations is None:
                registrations = registered_script_names()
            if name not in registrations:
                problems.append({"kind": "script_name", "id": name, "from": f"{table}.{owner}"})

    script_keys = {(str(e), "0") for e in creatures} | {(str(-g), "0") for g in creature_guids}
    script_keys |= {(str(e), "1") for e in gameobjects} | {(str(-g), "1") for g in go_guids}
    script_cols = (["entryorguid", "source_type", "id", "link", "event_type"]
                   + [f"event_param{i}" for i in range(1, 5)] + ["action_type"]
                   + [f"action_param{i}" for i in range(1, 7)] + ["target_type"]
                   + [f"target_param{i}" for i in range(1, 3)])
    scripts = {}
    if set(script_cols) <= cols("smart_scripts"):
        for row in query(container, user, password, schema, f"SELECT {','.join(script_cols)} FROM smart_scripts"):
            scripts.setdefault((row[0], row[1]), []).append(dict(zip(script_cols, row)))
    if quests and {"npcflag"} <= cols("creature_template") and table_exists(
            container, user, password, schema, "creature_queststarter"):
        quest_list = ",".join(sorted(quests))  # a giver without the QUESTGIVER bit never offers its quests
        for entry, in query(container, user, password, schema,
                            f"SELECT DISTINCT t.entry FROM creature_template t WHERE (t.npcflag&2)=0 AND t.entry IN "
                            f"(SELECT id FROM creature_queststarter WHERE quest IN ({quest_list}) "
                            f"UNION SELECT id FROM creature_questender WHERE quest IN ({quest_list}))"):
            problems.append({"kind": "quest_giver_flag", "id": entry})
    link_cols = {"event_type", "event_param1", "event_param2", "event_param3", "event_param4"}
    if script_keys and link_cols <= cols("smart_scripts"):
        for owner, source_type, script_id in query(
                container, user, password, schema,
                "SELECT entryorguid, source_type, id FROM smart_scripts WHERE event_type=61 AND "
                "(event_param1<>0 OR event_param2<>0 OR event_param3<>0 OR event_param4<>0)"):
            if (owner, source_type) in script_keys:
                problems.append({"kind": "smart_link_event_params", "id": f"{owner}.{source_type}.{script_id}"})
    pending_keys = [(key, key) for key in script_keys]
    visited = set()
    visited_contexts = set()
    while pending_keys:
        key, root = pending_keys.pop()
        if (key, root) in visited_contexts:
            continue
        visited_contexts.add((key, root))
        visited.add(key)
        events = {r["id"]: r["event_type"] for r in scripts.get(root, [])}
        for r in scripts.get(key, []):
            owner = f"smart_scripts.{key[0]}.{key[1]}.{r['id']}"
            action, event, target = (int(r[c]) for c in ("action_type", "event_type", "target_type"))
            # Timed lists coerce event_type to UPDATE_OOC; their stored event parameters are timers.
            if key[1] != "9":
                for types, param, table, target_key in (
                    ((5,), 4, "creature_template", "entry"),
                    ((17, 35, 42, 82, 107), 1, "creature_template", "entry"),
                    ((19, 20), 1, "quest_template", "ID"),
                    ((46,), 1, "areatrigger", "entry"),
                    ((68, 69), 1, "game_event", "eventEntry"),
                    ((75,), 1, "creature", "guid"), ((75,), 2, "creature_template", "entry"),
                    ((76,), 1, "gameobject", "guid"), ((76,), 2, "gameobject_template", "entry"),
                ):
                    if event in types:
                        check("smartai_event_reference", r[f"event_param{param}"], table, target_key, owner)
            for types, param, table, target_key, optional in (
                ((3, 43), 1, "creature_template", "entry", True),
                ((12, 33, 36), 1, "creature_template", "entry", False),
                ((6, 7, 15, 26), 1, "quest_template", "ID", False),
                ((50,), 1, "gameobject_template", "entry", False),
                ((53,), 2, "waypoints", "entry", False),
                ((53,), 4, "quest_template", "ID", True),
                ((55,), 2, "quest_template", "ID", True),
                ((111, 112), 1, "game_event", "eventEntry", False),
                ((131, 132), 1, "spawn_group_template", "groupId", False),
            ):
                if action in types:
                    check("smartai_action_reference", r[f"action_param{param}"], table, target_key, owner, optional)
            if action != 58:  # INSTALL_AI_TEMPLATE bypasses target validation.
                for types, param, table in (((9, 11, 19, 204), 1, "creature_template"),
                                            ((10, 206), 2, "creature_template"),
                                            ((13, 15, 20), 1, "gameobject_template"),
                                            ((14,), 2, "gameobject_template")):
                    if target in types:
                        check("smartai_target_reference", r[f"target_param{param}"], table, "entry", owner)
                if target == 207 and r["target_param1"] in ("1", "2"):
                    table = "creature_template" if r["target_param1"] == "1" else "gameobject_template"
                    check("smartai_target_reference", r["target_param2"], table, "entry", owner)
            # FindLinkedEvent searches the calling owner's events, even for a timed-list action.
            if (key[1] == "9" and r["link"] not in ("0", r["id"])
                    and events.get(r["link"]) != "61"):
                problems.append({"kind": "smartai_link", "id": r["link"],
                                 "from": f"{owner}.owner.{root[0]}.{root[1]}"})
            if action in (80, 87, 88):
                if action == 80:
                    lists = [r["action_param1"]]
                elif action == 87:
                    lists = [r[f"action_param{i}"] for i in range(1, 7) if r[f"action_param{i}"] != "0"]
                else:
                    lists = (str(i) for i in range(int(r["action_param1"]), int(r["action_param2"]) + 1))
                for value in lists:
                    if (value, "9") not in scripts:
                        problems.append({"kind": "smartai_timed_list", "id": value, "from": owner})
                    else:
                        pending_keys.append(((value, "9"), root))
            equip_id = (int(r["action_param1"]) + 128) % 256 - 128
            if action == 71 and key[1] == "0" and equip_id != 0:
                if {"CreatureID", "ID"} <= cols("creature_equip_template"):
                    match = "" if equip_id == -1 else f" AND ID={equip_id}"
                    equipment = query(container, user, password, schema,
                                      "SELECT ID FROM creature_equip_template "
                                      f"WHERE CreatureID={key[0]}{match}")
                    if not equipment:
                        problems.append({"kind": "smartai_equipment", "id": r["action_param1"], "from": owner})
            if action in (1, 84) and key[1] == "0" and target not in (9, 11, 19):
                entry = key[0]
                if event == 52:
                    entry = r["event_param2"]
                elif int(entry) < 0 and {"guid", "id"} <= cols("creature"):
                    spawn = query(container, user, password, schema,
                                  f"SELECT id FROM creature WHERE guid={-int(entry)}")
                    entry = spawn[0][0] if spawn else "0"
                if {"CreatureID", "GroupID"} <= cols("creature_text"):
                    text = query(container, user, password, schema,
                                 f"SELECT GroupID FROM creature_text WHERE CreatureID={entry} "
                                 f"AND GroupID={int(r['action_param1']) & 255}")
                    if not text:
                        problems.append({"kind": "smartai_text", "id": r["action_param1"], "from": owner})

    if visited and {"action_type", "target_type", "target_param1"} <= cols("smart_scripts"):
        # AC's SmartAIMgr skips rows with unsupported actions or params on no-param targets.
        # TDB 434 predates target_param4, so only test the columns that exist.
        target_params = [f"target_param{i}" for i in range(1, 5) if f"target_param{i}" in cols("smart_scripts")]
        for owner, source_type, script_id, kind in query(
                container, user, password, schema,
                "SELECT entryorguid, source_type, id, IF(action_type IN (16,85,119,120,127,128,129,130,133), "
                "'smart_unsupported_action', 'smart_unused_target_param') FROM smart_scripts WHERE "
                "action_type IN (16,85,119,120,127,128,129,130,133) OR (target_type IN (0,1,2) AND ("
                + " OR ".join(f"{param}<>0" for param in target_params) + "))"):
            if (owner, source_type) in visited:
                problems.append({"kind": kind, "id": f"{owner}.{source_type}.{script_id}"})

    condition_cols = ["SourceTypeOrReferenceId", "SourceGroup", "SourceEntry", "SourceId",
                      "ConditionTypeOrReference", "ConditionValue1", "ConditionValue2", "ConditionValue3", "ScriptName"]
    conditions = []
    if set(condition_cols) <= cols("conditions"):
        conditions = [dict(zip(condition_cols, row)) for row in
                      query(container, user, password, schema, f"SELECT {','.join(condition_cols)} FROM conditions")]
    templates = {}
    pending = []
    for r in conditions:
        source, group, entry, source_id = (r[c] for c in condition_cols[:4])
        if int(source) < 0:
            templates.setdefault(str(-int(source)), []).append(r)
        elif ((source == "19" and entry in quests)
              or (source == "22" and (entry, source_id) in visited)
              or (source in loot_entries and group in loot_entries[source])):
            pending.append(r)
    visited_refs = set()
    while pending:
        r = pending.pop()
        owner = ".".join(r[c] for c in condition_cols[:5])
        kind, v1, v2, v3 = (int(r[c]) for c in condition_cols[4:8])
        if r["ScriptName"]:
            if registrations is None:
                registrations = registered_script_names()
            if r["ScriptName"] not in registrations:
                problems.append({"kind": "script_name", "id": r["ScriptName"], "from": owner})
        source, group, entry = (r[c] for c in condition_cols[:3])
        if source == "19" and kind >= 0:
            check("condition_source_quest", entry, "quest_template", "ID", owner, optional=False)
        if source in loot_entries:
            table = {"1": "creature", "4": "gameobject", "8": "pickpocketing",
                     "10": "reference", "11": "skinning"}[source] + "_loot_template"
            if {"Entry", "Item", "Reference"} <= cols(table):
                loot = query(container, user, password, schema,
                             f"SELECT Reference FROM {table} WHERE Entry={group} AND Item={entry}")
                if kind >= 0 and not any(row[0] != "0" for row in loot):
                    check("condition_source_item", entry, "item_template", "entry", owner, optional=False)
                if not loot:
                    problems.append({"kind": "condition_source_loot", "id": entry, "from": owner})
        if kind < 0:
            ref = str(-kind)
            if ref not in templates:
                problems.append({"kind": "condition_reference", "id": ref, "from": owner})
            elif ref not in visited_refs:
                visited_refs.add(ref)
                pending.extend(templates[ref])
            continue
        for types, table, key in (((2, 3), "item_template", "entry"),
                                  ((8, 9, 14, 28, 43, 48, 101), "quest_template", "ID"),
                                  ((12,), "game_event", "eventEntry"),
                                  ((29,), "creature_template", "entry"),
                                  ((30,), "gameobject_template", "entry")):
            if kind in types:
                check("condition_value_reference", str(v1), table, key, owner, optional=False)
        if kind == 31 and v1 in (3, 5):
            table = "creature" if v1 == 3 else "gameobject"
            check("condition_object_entry", str(v2), table + "_template", "entry", owner)
            if v3 > (1 if v1 == 3 else 0):
                check("condition_object_guid", str(v3), table, "guid", owner, optional=False)
                if v2 and {"id", "guid"} <= cols(table):
                    spawn = query(container, user, password, schema, f"SELECT id FROM {table} WHERE guid={v3}")
                    if spawn and spawn[0][0] != str(v2):
                        problems.append({"kind": "condition_object_entry_guid", "id": str(v3), "from": owner})
    return problems


def zone_quest_candidates(container, user, password, schema, sorts, zone, map_id) -> list[str]:
    """Quest ids sorted under the zone's sorts plus any quest that starts inside the zone.

    Sub-areas use their own sort ids (Coldridge Valley 132, Echo Isles 368), so a zone id alone
    misses a starting experience; the starter spawn is the reliable link.
    """
    sort_ids = ",".join(str(value) for value in sorts)
    ids = {r[0] for r in query(container, user, password, schema,
                               f"SELECT ID FROM {schema}.quest_template WHERE QuestSortID IN ({sort_ids})")}
    for starter, spawn in (("creature_queststarter", "creature"), ("gameobject_queststarter", "gameobject")):
        if not (table_exists(container, user, password, schema, starter)
                and table_exists(container, user, password, schema, spawn)):
            continue
        ids.update(r[0] for r in query(container, user, password, schema,
                                       f"SELECT DISTINCT q.ID FROM {schema}.quest_template q "
                                       f"JOIN {schema}.{starter} s ON s.quest=q.ID "
                                       f"JOIN {schema}.{spawn} o ON o.id=s.id "
                                       f"WHERE o.map={map_id} "
                                       f"AND (o.zoneId={zone} OR o.areaId={zone})"))
    return sorted(ids, key=int)


def check_relations(container, user, password, schema, zone, map_id, quest_sorts=None,
                    extra_quests=None, dbc_dir=None) -> list[dict[str, str]]:
    """Return dangling relations for the zone's quests and spawns (quest_sorts defaults to the zone id)."""
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
    spell_cols = [c for c in ("RequiredSpell", "RewardSpell") if c in quest_cols]
    faction_cols = [c for c in ("RequiredFactionId1", "RequiredFactionId2", "RewardFactionID1",
                                "RewardFactionID2", "RewardFactionID3", "RewardFactionID4",
                                "RewardFactionID5") if c in quest_cols]
    title_cols = [c for c in ("RewardTitle",) if c in quest_cols]
    currency_cols = [c for c in [f"{prefix}CurrencyId{i}" for prefix in ("Required", "Reward")
                                 for i in range(1, 5)] if c in quest_cols]
    wanted = (["ID"] + objective_cols + item_cols + reward_cols + choice_cols + start_cols
              + spell_cols + faction_cols + title_cols + currency_cols)
    dbc: dict[str, set[int]] = {}
    if dbc_dir:
        dbc_path = Path(dbc_dir)
        spell_dbc = dbc_path / "Spell.dbc"
        if not spell_dbc.is_file():
            raise ValueError(f"missing {spell_dbc}")
        for name in ("Spell", "Faction", "FactionTemplate", "AreaTable", "CreatureDisplayInfo",
                     "GameObjectDisplayInfo", "SoundEntries", "Emotes", "CharTitles", "CreatureFamily",
                     "CurrencyTypes"):
            path = dbc_path / f"{name}.dbc"
            if path.is_file():
                dbc[name] = dbc_ids(path)
    spell_ids = dbc.get("Spell")
    where = f"{sort_col} IN ({','.join(str(value) for value in (quest_sorts or [zone]))})"
    if extra_quests:
        where += f" OR ID IN ({','.join(str(value) for value in extra_quests)})"
    rows = query(container, user, password, schema,
                 f"SELECT {','.join(wanted)} FROM quest_template WHERE {where}")
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
        for column in spell_cols:
            value = int(row[offset])
            offset += 1
            if spell_ids is not None and value > 0 and value not in spell_ids:
                problems.append({"kind": "quest_spell", "quest": quest, "column": column, "id": str(value)})
        for column in faction_cols:
            value = int(row[offset])
            offset += 1
            if "Faction" in dbc and value > 0 and value not in dbc["Faction"]:
                problems.append({"kind": "quest_faction", "quest": quest, "column": column, "id": str(value)})
        for column in title_cols:
            value = int(row[offset])
            offset += 1
            if "CharTitles" in dbc and value > 0 and value not in dbc["CharTitles"]:
                problems.append({"kind": "quest_title", "quest": quest, "id": str(value)})
        for column in currency_cols:
            value = int(row[offset])
            offset += 1
            if "CurrencyTypes" in dbc and value > 0 and value not in dbc["CurrencyTypes"]:
                problems.append({"kind": "quest_currency", "quest": quest, "column": column, "id": str(value)})

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
    for row in rows:
        for value in row[1:1 + len(objective_cols)]:
            if int(value) > 0:
                zone_creatures.add(int(value))
            elif int(value) < 0:
                zone_gos.add(-int(value))
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
            if entry_id is not None:
                spawn_entries.add(entry_id)
            if entry not in source:
                problems.append({"kind": label, "quest": quest, "id": entry})
            if quest not in all_quests:
                problems.append({"kind": label + "_quest", "quest": quest, "id": entry})

    # Difficulty/credit and linked-trap templates can have no spawn in this zone.
    for table, entries, references in (
        ("creature_template", zone_creatures,
         ["difficulty_entry_1", "difficulty_entry_2", "difficulty_entry_3", "KillCredit1", "KillCredit2"]),
        ("gameobject_template", zone_gos, ["Data3", "Data7", "Data2", "Data12"]),
    ):
        available = columns(container, user, password, schema, table)
        references = [column for column in references if column in available]
        handled = set()
        while entries - handled and references:
            batch = entries - handled
            handled.update(batch)
            selected = references if table == "creature_template" else ["type"] + references
            for row in query(container, user, password, schema,
                              f"SELECT {','.join(selected)} FROM {table} WHERE entry IN "
                              f"({','.join(map(str, sorted(batch)))})"):
                if table == "gameobject_template":
                    trap_column = {1: "Data3", 3: "Data7", 8: "Data2", 10: "Data12"}.get(int(row[0]))
                    values = [row[1 + references.index(trap_column)]] if trap_column in references else []
                else:
                    values = row
                entries.update(int(value) for value in values if int(value) > 0)

    zone_creature_guids = zone_guids(container, user, password, schema, "creature", zone, map_id)
    zone_go_guids = zone_guids(container, user, password, schema, "gameobject", zone, map_id)
    creature_keys = {str(e) for e in zone_creatures} | {str(-g) for g in zone_creature_guids}
    go_keys = {str(e) for e in zone_gos} | {str(-g) for g in zone_go_guids}
    if spell_ids is not None and table_exists(container, user, password, schema, "smart_scripts"):
        parts = []
        if creature_keys:
            keys = ",".join(sorted(creature_keys))
            parts.append(f"(entryorguid IN ({keys}) AND source_type=0)")
        if go_keys:
            keys = ",".join(sorted(go_keys))
            parts.append(f"(entryorguid IN ({keys}) AND source_type=1)")
        if parts:
            for owner, spell in query(container, user, password, schema,
                                      f"SELECT DISTINCT entryorguid, action_param1 FROM smart_scripts "
                                      f"WHERE action_type=11 AND action_param1<>0 AND (" + " OR ".join(parts) + ")"):
                if int(spell) not in spell_ids:
                    problems.append({"kind": "smartai_cast_spell", "entry": owner, "id": spell})

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
    loot_entries = {}
    loot_specs = []
    for table, template, owners, column, label, source_type in (
        ("creature_loot_template", "creature_template", zone_creatures, "lootid", "creature_loot", "1"),
        ("pickpocketing_loot_template", "creature_template", zone_creatures,
         "pickpocketloot", "pickpocket_loot", "8"),
        ("skinning_loot_template", "creature_template", zone_creatures, "skinloot", "skinning_loot", "11"),
        ("gameobject_loot_template", "gameobject_template", zone_gos, "Data1", "gameobject_loot", "4"),
    ):
        entries = set()
        if owners and column in columns(container, user, password, schema, template):
            where = " AND type IN (3,25)" if template == "gameobject_template" else ""
            entries = {int(r[0]) for r in query(container, user, password, schema,
                        f"SELECT DISTINCT {column} FROM {template} WHERE entry IN "
                        f"({','.join(map(str, sorted(owners)))}) AND {column}<>0{where}")}
        loot_entries[source_type] = {str(e) for e in entries}
        loot_specs.append((table, entries, label))
    for table, entries, label in loot_specs:
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
                               f"SELECT DISTINCT Item FROM {table} WHERE Entry IN ({joined}) AND Reference=0"):
                if item not in item_entries:
                    problems.append({"kind": "loot_item", "id": item, "from": label})
        refs = {r[0] for r in query(container, user, password, schema,
                                    f"SELECT DISTINCT ABS(Reference) FROM {table} WHERE Entry IN ({joined}) "
                                    f"AND Reference<>0") if r}
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
                                "SELECT Entry, ABS(Reference) FROM reference_loot_template WHERE Reference<>0"):
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
                                   f"AND Reference=0"):
                    if item not in item_entries:
                        problems.append({"kind": "loot_item", "id": item, "from": current})
            for nested in graph.get(current, set()):
                if nested not in known_refs:
                    problems.append({"kind": "reference_loot_nested", "id": nested, "from": current})
                else:
                    stack.append(nested)

    loot_entries["10"] = visited if direct_refs else set()

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

    if dbc:
        if "AreaTable" in dbc and zone_quests:
            joined = ",".join(sorted(str(q) for q in zone_quests))
            for quest, sort_id in query(container, user, password, schema,
                                        f"SELECT ID, {sort_col} FROM quest_template WHERE ID IN ({joined}) "
                                        f"AND {sort_col}>0"):
                if int(sort_id) not in dbc["AreaTable"]:
                    problems.append({"kind": "quest_area", "quest": quest, "id": str(sort_id)})
        if zone_creatures and table_exists(container, user, password, schema, "creature_template"):
            template_cols = columns(container, user, password, schema, "creature_template")
            selected = [c for c in ("entry", "faction", "family") if c in template_cols]
            if "entry" in selected:
                entries = ",".join(map(str, sorted(zone_creatures)))
                for row in query(container, user, password, schema,
                                 f"SELECT {','.join(selected)} FROM creature_template WHERE entry IN ({entries})"):
                    for column, value in zip(selected[1:], row[1:]):
                        value = int(value)
                        if column == "faction" and "FactionTemplate" in dbc:
                            if value == 0 or value not in dbc["FactionTemplate"]:
                                problems.append({"kind": "creature_faction", "entry": row[0], "id": str(value)})
                        elif column == "family" and "CreatureFamily" in dbc:
                            # 10 is the loader's accepted "none" sentinel and is not in the DBC.
                            if value not in (0, 10) and value not in dbc["CreatureFamily"]:
                                problems.append({"kind": "creature_family", "entry": row[0], "id": str(value)})
        if ("CreatureDisplayInfo" in dbc and zone_creatures
                and table_exists(container, user, password, schema, "creature_template_model")):
            entries = ",".join(map(str, sorted(zone_creatures)))
            for entry, display in query(container, user, password, schema,
                                        f"SELECT CreatureID, CreatureDisplayID FROM creature_template_model "
                                        f"WHERE CreatureID IN ({entries})"):
                if int(display) not in dbc["CreatureDisplayInfo"]:
                    problems.append({"kind": "creature_display", "entry": entry, "id": display})
        if "GameObjectDisplayInfo" in dbc and zone_gos:
            go_cols = columns(container, user, password, schema, "gameobject_template")
            if "displayId" in go_cols:
                entries = ",".join(map(str, sorted(zone_gos)))
                for entry, display in query(container, user, password, schema,
                                            f"SELECT entry, displayId FROM gameobject_template "
                                            f"WHERE entry IN ({entries}) AND displayId<>0"):
                    if int(display) not in dbc["GameObjectDisplayInfo"]:
                        problems.append({"kind": "gameobject_display", "entry": entry, "id": display})
        if zone_creatures and table_exists(container, user, password, schema, "creature_text"):
            entries = ",".join(map(str, sorted(zone_creatures)))
            for entry, sound, emote in query(container, user, password, schema,
                                             f"SELECT DISTINCT CreatureID, Sound, Emote FROM creature_text "
                                             f"WHERE CreatureID IN ({entries})"):
                if "SoundEntries" in dbc and int(sound) > 0 and int(sound) not in dbc["SoundEntries"]:
                    problems.append({"kind": "creature_text_sound", "entry": entry, "id": sound})
                if "Emotes" in dbc and int(emote) > 0 and int(emote) not in dbc["Emotes"]:
                    problems.append({"kind": "creature_text_emote", "entry": entry, "id": emote})

    problems.extend(check_loader_relations(container, user, password, schema, zone_creatures, zone_gos,
                                            zone_creature_guids, zone_go_guids, zone_quests, loot_entries))
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
    parser.add_argument("--dbc-dir", help="4.3.4 DBC directory for client-store id checks")
    args = parser.parse_args()

    report = {
        "zone": args.zone,
        "map": args.map,
        "ac": inventory(args.container, args.user, args.password, args.ac_schema, args.zone, args.map),
        "tdb": inventory(args.container, args.user, args.password, args.tdb_schema, args.zone, args.map),
    }
    problems = [] if args.metadata_only else check_relations(
        args.container, args.user, args.password, args.tdb_schema, args.zone, args.map,
        dbc_dir=args.dbc_dir)
    report["tdb_dangling"] = problems
    print(json.dumps(report, indent=2))
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
