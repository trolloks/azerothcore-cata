#!/usr/bin/env python3
"""TDB 434 -> AC starter-content import pipeline (#203).

For one zone, imports the missing quests (quest_template plus reward, request
and addon companion rows), then the templates, spawns, loot and SmartAI their
objectives, required-item suppliers and givers reference, including display
models, creature text, waypoint paths and condition reference templates.
AC-native rows win: existing loot, script and path blocks are not merged, a
creature_text group is filled line by line, and existing spawns at the same
position are not duplicated. Remaining item references are reported; TDB 434
carries no item data, so those come from the client Item.db2/Item-sparse.db2
conversion instead.

Dry-run by default; --apply performs the inserts. Run only against the
run-owned disposable database.
"""

import argparse
import json
import re
import struct
import subprocess
import sys
from pathlib import Path

from starter_content_inventory import check_relations, registered_script_names, zone_quest_candidates


# SmartEvent union sizes in SmartScriptMgr.h, as selected by CheckUnusedEventParams; others use all six.
SMART_EVENT_PARAM_COUNTS = {
    0: (4, 6, 7, 21, 25, 30, 36, 37, 41, 43, 47, 48, 49, 50, 51, 54, 61, 63, 65, 73),
    1: (29, 42, 44, 46, 59, 64, 66, 68, 69, 70, 71),
    2: (27, 28, 39, 40, 52, 55, 56, 57, 58, 62, 108, 109),
    3: (11, 13, 15, 17, 19, 20, 22, 34, 35, 45, 72, 82, 107),
    4: (5, 8, 14, 23, 24, 31, 38, 75, 76, 77),
    5: (10, 16, 26, 101, 102, 103, 104),
}


# TDB 434 and the 3.3.5a world DB place the same NPC up to a hub apart; anything closer than this
# is the same spawn and must not be imported a second time. GUID remapping still matches at 0.5 m.
SPAWN_DEDUPE_RADIUS_M = 100.0


def spawn_match(radius_m: float = 0.5):
    """Same template, map and phase within radius_m in 3D; shared by import and GUID remapping."""
    distance_sq = radius_m * radius_m
    return ("t.id=s.id AND t.map=s.map AND t.phaseMask=s.phaseMask "
            "AND (t.position_x-s.position_x)*(t.position_x-s.position_x) "
            "+ (t.position_y-s.position_y)*(t.position_y-s.position_y) "
            "+ (t.position_z-s.position_z)*(t.position_z-s.position_z)"
            f"<={distance_sq:g}")


def spawn_scope(source, table, alias, zone, map_id, quest_ids):
    """Include out-of-zone quest givers on this map, such as city notice boards."""
    return (f"{alias}.map={map_id} AND ({alias}.zoneId={zone} OR {alias}.areaId={zone} "
            f"OR {alias}.id IN (SELECT id FROM {source}.{table}_queststarter WHERE quest IN ({quest_ids}) "
            f"UNION SELECT id FROM {source}.{table}_questender WHERE quest IN ({quest_ids})))")


def spell_effects(dbc_dir, creature_effects=(28,)):
    """Return (creature entries by spell for the requested effects, trigger spells by spell) from SpellEffect.dbc."""
    if not dbc_dir:
        return {}, {}
    data = (Path(dbc_dir) / "SpellEffect.dbc").read_bytes()
    if data[:4] != b"WDBC":
        raise SystemExit(f"SpellEffect.dbc in {dbc_dir} is not a WDBC file")
    records, fields, record_size, _ = struct.unpack_from("<4I", data, 4)
    if fields < 26:  # ID..EffectMiscValue..EffectTriggerSpell..SpellID, EffectIndex
        raise SystemExit("SpellEffect.dbc has an unexpected layout")
    summoned, triggers = {}, {}
    for i in range(records):
        row = struct.unpack_from(f"<{fields}I", data, 20 + i * record_size)
        if row[1] in creature_effects and row[12] > 0:  # Effect type, EffectMiscValue
            summoned.setdefault(row[24], set()).add(str(row[12]))
        if row[21] > 0:  # EffectTriggerSpell
            triggers.setdefault(row[24], set()).add(row[21])
    return summoned, triggers


def run(container, user, password, sql):
    result = subprocess.run(
        ["docker", "exec", container, "mysql", f"-u{user}", f"-p{password}", "--default-character-set=utf8mb4",
         "-N", "-B", "-e", sql],
        capture_output=True, text=True, encoding="utf-8", check=False,
    )
    if result.returncode:
        raise RuntimeError(f"query failed: {sql}\n{result.stderr.strip()}")
    # split on "\n" only: splitlines() also breaks on VT, FF, U+0085, U+2028 inside text values
    return [line.split("\t") for line in result.stdout.split("\n") if line]


def scalar(container, user, password, sql):
    rows = run(container, user, password, sql)
    return int(rows[0][0]) if rows and rows[0] and rows[0][0] not in ("", "NULL") else 0


def table_exists(container, user, password, schema, table):
    rows = run(container, user, password,
               f"SELECT COUNT(*) FROM information_schema.tables "
               f"WHERE TABLE_SCHEMA='{schema}' AND TABLE_NAME='{table}'")
    return bool(rows and rows[0] and rows[0][0] == "1")


def columns(container, user, password, schema, table):
    rows = run(container, user, password,
               f"SELECT COLUMN_NAME, EXTRA FROM information_schema.columns "
               f"WHERE TABLE_SCHEMA='{schema}' AND TABLE_NAME='{table}' ORDER BY ORDINAL_POSITION")
    return [(name, "auto_increment" in extra) for name, extra in rows]


def common_columns(container, user, password, target, source, table, include_auto=False):
    target_cols = columns(container, user, password, target, table)
    source_names = {name for name, _ in columns(container, user, password, source, table)}
    return [name for name, auto in target_cols if name in source_names and (include_auto or not auto)]


WARNINGS: list[str] = []  # INSERT IGNORE truncations and dropped values, reported by main()


def insert_count(container, user, password, sql):
    """Run an INSERT IGNORE and return its row count; its warnings go to WARNINGS, not into the void."""
    # SELECT ROW_COUNT() would clear the diagnostics, so capture the count first and print it last.
    rows = run(container, user, password,
               sql + "; GET DIAGNOSTICS @insert_count = ROW_COUNT; SHOW WARNINGS; SELECT @insert_count")
    # A duplicate key is the intended fill-only skip; a truncation or dropped value is not.
    WARNINGS.extend(" ".join(row) for row in rows[:-1]
                    if row[0] in ("Warning", "Error") and "Duplicate entry" not in row[-1])
    return int(rows[-1][0]) if rows else 0


def quote(name):
    return f"`{name}`"


def import_table(container, user, password, target, source, table, where_col, ids, dry_run):
    common = common_columns(container, user, password, target, source, table)
    if not common or where_col not in common:
        return 0, f"-- no compatible key {where_col} for {table}"
    id_list = ",".join(str(i) for i in ids)
    cols = ",".join(quote(c) for c in common)
    sql = (
        f"INSERT IGNORE INTO {quote(target)}.{quote(table)} ({cols}) "
        f"SELECT {cols} FROM {quote(source)}.{quote(table)} WHERE {quote(where_col)} IN ({id_list})"
    )
    if dry_run:
        return 0, sql
    return insert_count(container, user, password, sql), sql


# SmartAI actions AC's SmartAIMgr rejects as unsupported on this core (16, 85, 119, 120, 127..130, 133); rows using them
# are dropped at import so the landing carries no script the loader would only log and skip.
UNSUPPORTED_SMART_ACTIONS = (16, 85, 119, 120, 127, 128, 129, 130, 133)
GUID_OFFSET = 10_000_000
# Movement path ids (waypoint_data.id) are source id + PATH_OFFSET so replay is deterministic and pending SQL can
# carry fixed ids. TDB path ids top out at 5.3M and native AC data at 53M, so the shifted range is free.
PATH_OFFSET = 100_000_000


def fetch(container, user, password, sql):
    return [r[0] for r in run(container, user, password, sql) if r]


def import_missing_rows(container, user, password, target, source, table, key, ids, dry_run, next_column=None):
    """Fill missing IDs and follow native links in preference to source links."""
    if not all(table_exists(container, user, password, database, table) for database in (source, target)):
        return 0
    table_columns = {database: {name for name, _ in columns(container, user, password, database, table)}
                     for database in (source, target)}
    if any(key not in names for names in table_columns.values()):
        return 0
    present = set(fetch(container, user, password, f"SELECT {key} FROM {target}.{table}"))
    pending = set(ids)
    handled = set()
    count = 0
    while pending - handled:
        batch = pending - handled
        handled.update(batch)
        missing = sorted(batch - present)
        if missing:
            inserted, _ = import_table(container, user, password, target, source, table, key, missing, dry_run)
            count += inserted
        if next_column:
            for database, entries in ((target, sorted(batch & present)), (source, missing)):
                if entries and next_column in table_columns[database]:
                    pending.update(fetch(container, user, password,
                                         f"SELECT {next_column} FROM {database}.{table} "
                                         f"WHERE {key} IN ({','.join(entries)}) AND {next_column}<>0"))
    return count


def import_closure(container, user, password, target, source, zone, map_id, quests, dry_run,
                   dbc_dir=None, drop_items=()):
    """Import templates, spawns, loot and scripts the imported quests reference.

    Dependents are reconciled for every referenced entry, not only the entries
    whose template was missing. AC-native rows win: an existing loot table,
    reference entry, timed action list, position-matched spawn or entry script
    is left intact and only gaps are filled.

    Spawn GUIDs are shifted by GUID_OFFSET so replay in the disposable database
    is idempotent; a production landing must allocate a GUID block and emit
    pending SQL. A run interrupted after the spawn/script inserts should be
    replayed from the database dump; ownership is not persisted.
    """
    report = {}
    if not quests:
        return report
    summoned, triggers = spell_effects(dbc_dir)
    quest_ids = ",".join(quests)

    creature_entries, gameobject_entries = set(), set()
    objective_columns = ",".join(f"RequiredNpcOrGo{i}" for i in range(1, 5))
    for row in run(container, user, password,
                   f"SELECT {objective_columns} FROM {source}.quest_template WHERE ID IN ({quest_ids})"):
        for value in row:
            if not value:
                continue
            number = int(value)
            if number > 0:
                creature_entries.add(value)
            elif number < 0:
                gameobject_entries.add(str(-number))
    for table in ("creature_queststarter", "creature_questender"):
        creature_entries.update(fetch(container, user, password,
                                      f"SELECT id FROM {source}.{table} WHERE quest IN ({quest_ids})"))
    for table in ("gameobject_queststarter", "gameobject_questender"):
        gameobject_entries.update(fetch(container, user, password,
                                        f"SELECT id FROM {source}.{table} WHERE quest IN ({quest_ids})"))

    item_columns = [f"RequiredItemId{i}" for i in range(1, 7)]
    item_ids: set[str] = set()
    for row in run(container, user, password,
                   f"SELECT {','.join(item_columns)} FROM {source}.quest_template "
                   f"WHERE ID IN ({quest_ids})"):
        item_ids.update(value for value in row if value and value != "0")
    if item_ids:
        items = ",".join(sorted(item_ids))
        creature_entries.update(fetch(container, user, password,
            f"SELECT DISTINCT ct.entry FROM {source}.creature_loot_template lt "
            f"JOIN {source}.creature_template ct ON ct.lootid = lt.Entry "
            f"JOIN {source}.creature c ON c.id = ct.entry AND c.map={map_id} "
            f"AND (c.zoneId={zone} OR c.areaId={zone}) WHERE lt.Item IN ({items})"))
        creature_entries.update(fetch(container, user, password,
            f"SELECT DISTINCT ct.entry FROM {source}.reference_loot_template rt "
            f"JOIN {source}.creature_loot_template lt ON ABS(lt.Reference) = rt.Entry "
            f"JOIN {source}.creature_template ct ON ct.lootid = lt.Entry "
            f"JOIN {source}.creature c ON c.id = ct.entry AND c.map={map_id} "
            f"AND (c.zoneId={zone} OR c.areaId={zone}) WHERE rt.Item IN ({items})"))
        gameobject_entries.update(fetch(container, user, password,
            f"SELECT DISTINCT gt.entry FROM {source}.gameobject_loot_template lt "
            f"JOIN {source}.gameobject_template gt ON gt.Data1 = lt.Entry AND gt.type IN (3,25) "
            f"JOIN {source}.gameobject g ON g.id = gt.entry AND g.map={map_id} "
            f"AND (g.zoneId={zone} OR g.areaId={zone}) WHERE lt.Item IN ({items})"))
        gameobject_entries.update(fetch(container, user, password,
            f"SELECT DISTINCT gt.entry FROM {source}.reference_loot_template rt "
            f"JOIN {source}.gameobject_loot_template lt ON ABS(lt.Reference) = rt.Entry "
            f"JOIN {source}.gameobject_template gt ON gt.Data1 = lt.Entry AND gt.type IN (3,25) "
            f"JOIN {source}.gameobject g ON g.id = gt.entry AND g.map={map_id} "
            f"AND (g.zoneId={zone} OR g.areaId={zone}) WHERE rt.Item IN ({items})"))

    known_creatures = set(fetch(container, user, password, f"SELECT entry FROM {target}.creature_template"))
    known_gameobjects = set(fetch(container, user, password, f"SELECT entry FROM {target}.gameobject_template"))
    # New templates can reference templates that are neither quest objectives nor quest givers.
    for table, entries, known, selected in (
        ("creature_template", creature_entries, known_creatures,
         "difficulty_entry_1,difficulty_entry_2,difficulty_entry_3,KillCredit1,KillCredit2"),
        ("gameobject_template", gameobject_entries, known_gameobjects,
         "CASE type WHEN 1 THEN Data3 WHEN 3 THEN Data7 WHEN 8 THEN Data2 WHEN 10 THEN Data12 ELSE 0 END"),
    ):
        handled = set(known)
        while entries - handled:
            batch = entries - handled
            handled.update(batch)
            for row in run(container, user, password,
                           f"SELECT {selected} FROM {source}.{table} WHERE entry IN ({','.join(sorted(batch))})"):
                entries.update(value for value in row if int(value) > 0)
    # Scripts this run will import (entries without a native script) can summon creatures and gameobjects, directly
    # or through timed action lists; those templates must exist too. Follow them until nothing new appears.
    seen_lists: set[str] = set()
    while True:
        grew = False
        for source_type, entries in (("0", creature_entries), ("1", gameobject_entries)):
            owned = ",".join(sorted(entries)) or "0"
            native = set(fetch(container, user, password,
                               f"SELECT DISTINCT entryorguid FROM {target}.smart_scripts "
                               f"WHERE source_type={source_type} AND entryorguid IN ({owned})"))
            scripted = [e for e in entries if e not in native]
            if not scripted:
                continue
            clauses = [f"(source_type={source_type} AND entryorguid IN ({','.join(sorted(scripted))}))"]
            spawn = "creature" if source_type == "0" else "gameobject"
            # Spawn-level scripts of candidate templates are imported with their spawns; their
            # summons/targets need templates too, even though the spawns themselves are matched later.
            clauses.append(f"(source_type={source_type} AND entryorguid IN "
                           f"(SELECT -o.guid FROM {source}.{spawn} o WHERE o.id IN ({','.join(sorted(entries))}) "
                           f"AND {spawn_scope(source, spawn, 'o', zone, map_id, quest_ids)}))")
            if seen_lists:
                clauses.append(f"(source_type=9 AND entryorguid IN ({','.join(sorted(seen_lists))}))")
            for row in run(container, user, password,
                           f"SELECT action_type,action_param1,action_param2,action_param3,action_param4,"
                           f"action_param5,action_param6,target_type,target_param1,target_param2,"
                           f"target_param3 FROM {source}.smart_scripts "
                           f"WHERE {' OR '.join(clauses)}"):
                action, params = int(row[0]), [int(v) for v in row[1:7]]
                target_type, target_params = int(row[7]), [int(v) for v in row[8:11]]
                found_creatures = {str(params[0])} if action in (12, 33, 36) and params[0] > 0 else set()
                found_objects = {str(params[0])} if action == 50 and params[0] > 0 else set()
                if target_type in (9, 11, 204) and target_params[0] > 0:
                    found_creatures.add(str(target_params[0]))
                elif target_type in (10, 206) and target_params[1] > 0:
                    found_creatures.add(str(target_params[1]))
                elif target_type in (13, 15) and target_params[0] > 0:
                    found_objects.add(str(target_params[0]))
                elif target_type == 14 and target_params[1] > 0:
                    found_objects.add(str(target_params[1]))
                elif target_type in (19,) and target_params[0] > 0:
                    found_creatures.add(str(target_params[0]))
                elif target_type == 20 and target_params[0] > 0:
                    found_objects.add(str(target_params[0]))
                elif target_type == 207 and target_params[0] in (1, 2) and target_params[1] > 0:
                    (found_creatures if target_params[0] == 1 else found_objects).add(str(target_params[1]))
                if action == 11 and params[0] > 0:  # CAST can summon through the spell's effects (and their triggers)
                    queue, seen_spells = [params[0]], set()
                    while queue:
                        spell = queue.pop()
                        if spell in seen_spells:
                            continue
                        seen_spells.add(spell)
                        found_creatures.update(summoned.get(spell, ()))
                        queue.extend(triggers.get(spell, ()))
                lists = set()
                if action == 80 and params[0] > 0:
                    lists.add(str(params[0]))
                elif action == 87:
                    lists.update(str(v) for v in params if v > 0)
                elif action == 88 and params[0] > 0 and params[1] >= params[0]:
                    lists.update(fetch(container, user, password,
                                       f"SELECT DISTINCT entryorguid FROM {source}.smart_scripts WHERE source_type=9 "
                                       f"AND entryorguid BETWEEN {params[0]} AND {params[1]}"))
                found_creatures -= creature_entries
                found_objects -= gameobject_entries
                if found_creatures or found_objects or lists - seen_lists:
                    grew = True
                creature_entries.update(found_creatures)
                gameobject_entries.update(found_objects)
                seen_lists.update(lists)
        if not grew:
            break
        # newly added templates may themselves reference templates (difficulty, kill credit, linked objects)
        for table, entries, known, selected in (
            ("creature_template", creature_entries, known_creatures,
             "difficulty_entry_1,difficulty_entry_2,difficulty_entry_3,KillCredit1,KillCredit2"),
            ("gameobject_template", gameobject_entries, known_gameobjects,
             "CASE type WHEN 1 THEN Data3 WHEN 3 THEN Data7 WHEN 8 THEN Data2 WHEN 10 THEN Data12 ELSE 0 END"),
        ):
            handled = set(known)
            while entries - handled:
                batch = entries - handled
                handled.update(batch)
                for row in run(container, user, password,
                               f"SELECT {selected} FROM {source}.{table} WHERE entry IN ({','.join(sorted(batch))})"):
                    entries.update(value for value in row if int(value) > 0)
    creature_ids = ",".join(sorted(creature_entries)) or "0"
    gameobject_ids = ",".join(sorted(gameobject_entries)) or "0"
    new_creatures = sorted(creature_entries - known_creatures)
    new_gameobjects = sorted(gameobject_entries - known_gameobjects)
    report["new_templates"] = {"creature_template": len(new_creatures),
                               "gameobject_template": len(new_gameobjects)}
    for table, entries in (("creature_template", new_creatures),
                           ("gameobject_template", new_gameobjects)):
        if entries:
            report[table], _ = import_table(container, user, password, target, source, table,
                                            "entry", entries, dry_run)

    script_names: set[str] = set()
    for table, entries in (("creature_template", new_creatures),
                           ("gameobject_template", new_gameobjects)):
        if entries:
            script_names.update(fetch(container, user, password,
                                      f"SELECT DISTINCT HEX(ScriptName) FROM {source}.{table} "
                                      f"WHERE entry IN ({','.join(entries)}) AND ScriptName<>''"))
    script_names = {bytes.fromhex(name).decode("utf-8") for name in script_names}
    if script_names:
        report["script_names"] = sorted(script_names)
        # A ScriptName with no C++ registration is a startup error and hides the template's SmartAI.
        registered_names = registered_script_names()
        unregistered = sorted(script_names - registered_names)
        if unregistered:
            report["stripped_script_names"] = unregistered
            if not dry_run:
                # Hex literals keep source quotes and backslashes out of SQL syntax.
                names = ",".join(f"X'{name.encode().hex()}'" for name in unregistered)
                for table, entries in (("creature_template", new_creatures),
                                       ("gameobject_template", new_gameobjects)):
                    if entries:
                        run(container, user, password,
                            f"UPDATE {target}.{table} SET ScriptName='' WHERE entry IN ({','.join(entries)}) "
                            f"AND BINARY ScriptName IN ({names})")

    model_columns = [c for c in ("modelid1", "modelid2", "modelid3", "modelid4")
                     if c in {name for name, _ in columns(container, user, password,
                                                          source, "creature_template")}]
    proposed_displays = set()
    if model_columns and creature_entries:
        modelled = set(fetch(container, user, password,
                             f"SELECT DISTINCT CreatureID FROM {target}.creature_template_model "
                             f"WHERE CreatureID IN ({creature_ids})"))
        values = []
        for row in run(container, user, password,
                       f"SELECT entry,{','.join(model_columns)},VerifiedBuild "
                       f"FROM {source}.creature_template WHERE entry IN ({creature_ids})"):
            if row[0] in modelled:
                continue
            for idx, model in enumerate(row[1:-1]):
                if model and model != "0":
                    proposed_displays.add(model)
                    values.append(f"({row[0]},{idx},{model},1,1,{row[-1] or 0})")
        if values:
            if not dry_run:
                run(container, user, password,
                    f"INSERT IGNORE INTO {target}.creature_template_model "
                    f"(CreatureID,Idx,CreatureDisplayID,DisplayScale,Probability,VerifiedBuild) "
                    f"VALUES {','.join(values)}")
            report["creature_template_model"] = len(values)

    # Creature creation needs model info; alternate genders can reference further model-info rows.
    if creature_entries and table_exists(container, user, password, target, "creature_template_model"):
        displays = proposed_displays | set(fetch(container, user, password,
                             f"SELECT DISTINCT CreatureDisplayID FROM {target}.creature_template_model "
                             f"WHERE CreatureID IN ({creature_ids})"))
        report["creature_model_info"] = import_missing_rows(
            container, user, password, target, source, "creature_model_info", "DisplayID", displays, dry_run,
            "DisplayID_Other_Gender")
    if (creature_entries and table_exists(container, user, password, source, "creature_equip_template")
            and table_exists(container, user, password, target, "creature_equip_template")):
        report["creature_equip_template"], _ = import_table(
            container, user, password, target, source, "creature_equip_template", "CreatureID",
            sorted(creature_entries), dry_run)

    # Source GUIDs this importer owns in the target: guid + GUID_OFFSET at the source position.
    # Keyed off the target rather than this run's inserts, so a replay still repairs the addons,
    # paths, events and formations a failed earlier run left out (#213).
    imported_spawn_guids: dict[str, set[str]] = {"creature": set(), "gameobject": set()}
    report["phase_normalized"] = {"creature": 0, "gameobject": 0}

    def import_spawns(table, id_column, entries):
        if not entries:
            return 0, ""
        ids = ",".join(sorted(entries))
        target_names = {name for name, _ in columns(container, user, password, target, table)}
        source_names = {name for name, _ in columns(container, user, password, source, table)}
        mapping = [(c, c) for c in common_columns(container, user, password, target, source, table)
                   if c != "guid"]
        if (table == "creature" and "wander_distance" in target_names
                and "spawndist" in source_names):
            mapping.append(("wander_distance", "spawndist"))
        names = ",".join(quote(target_col) for target_col, _source_col in mapping)
        selected = ",".join(f"s.{quote(source_col)}" for _target_col, source_col in mapping)
        sql = (f"INSERT IGNORE INTO {target}.{table} (`guid`,{names}) "
               f"SELECT s.guid+{GUID_OFFSET},{selected} FROM {source}.{table} s "
               f"WHERE s.{id_column} IN ({ids}) AND {spawn_scope(source, table, 's', zone, map_id, quest_ids)} "
               f"AND NOT EXISTS (SELECT 1 FROM {target}.{table} t WHERE t.guid=s.guid+{GUID_OFFSET}) "
               f"AND NOT EXISTS (SELECT 1 FROM {target}.{table} t WHERE {spawn_match(SPAWN_DEDUPE_RADIUS_M)})")
        if dry_run:
            return 0, sql
        # Only spawns this run inserts get their undefined phase stored; an existing spawn keeps
        # whatever phase the world already gave it, even when its phase id is not in phase_area.
        before = (set(fetch(container, user, password,
                            f"SELECT guid FROM {target}.{table} WHERE guid>={GUID_OFFSET}"))
                  if "PhaseId" in target_names else set())
        count = insert_count(container, user, password, sql)
        imported_spawn_guids[table] = set(fetch(
            container, user, password,
            f"SELECT s.guid+{GUID_OFFSET} FROM {source}.{table} s "
            f"WHERE s.{id_column} IN ({ids}) AND {spawn_scope(source, table, 's', zone, map_id, quest_ids)} "
            f"AND EXISTS (SELECT 1 FROM {target}.{table} t WHERE t.guid=s.guid+{GUID_OFFSET} "
            f"AND {spawn_match()})"))
        new_guids = imported_spawn_guids[table] - before
        if new_guids:
            undefined = "PhaseId<>0"
            if table_exists(container, user, password, target, "phase_area"):
                undefined += f" AND PhaseId NOT IN (SELECT PhaseId FROM {target}.phase_area)"
            imported = ",".join(sorted(new_guids))
            rows = run(container, user, password,
                       f"UPDATE {target}.{table} SET PhaseId=0 "
                       f"WHERE guid IN ({imported}) AND {undefined}; SELECT ROW_COUNT()")
            report["phase_normalized"][table] = int(rows[-1][0]) if rows else 0
        if table == "creature" and not {"MovementType", "wander_distance"} - target_names:
            # The loader resets an unknown MovementType (TDB has 3) and an idle spawn's wander distance; store that.
            run(container, user, password,
                f"UPDATE {target}.creature SET MovementType=0, wander_distance=0 WHERE guid>={GUID_OFFSET} "
                f"AND (MovementType>=3 OR (MovementType=0 AND wander_distance<>0))")
        return count, sql

    for table, id_column, entries in (("creature", "id", creature_entries),
                                      ("gameobject", "id", gameobject_entries)):
        count, sql = import_spawns(table, id_column, entries)
        report[table] = count
        if dry_run:
            report[table + "_sql"] = sql

    for table, spawn_table, entries in (("creature_addon", "creature", creature_entries),
                                        ("gameobject_addon", "gameobject", gameobject_entries)):
        if not entries or not table_exists(container, user, password, source, table):
            continue
        if not dry_run and not imported_spawn_guids[spawn_table]:
            continue
        ids = ",".join(sorted(entries))
        target_names = {name for name, _ in columns(container, user, password, target, table)}
        source_names = {name for name, _ in columns(container, user, password, source, table)}
        mapping = [(c, c) for c in common_columns(container, user, password, target, source, table)
                   if c != "guid"]
        if table == "creature_addon" and "path_id" in target_names and "waypointPathId" in source_names:
            mapping.append(("path_id", "waypointPathId"))
        names = ",".join(quote(target_col) for target_col, _source_col in mapping)
        selected = ",".join(f"a.{quote(source_col)}" for _target_col, source_col in mapping)
        sql = (f"INSERT IGNORE INTO {target}.{table} (`guid`,{names}) "
               f"SELECT a.guid+{GUID_OFFSET},{selected} FROM {source}.{table} a "
               f"JOIN {source}.{spawn_table} s ON s.guid=a.guid "
               f"WHERE s.id IN ({ids}) AND {spawn_scope(source, spawn_table, 's', zone, map_id, quest_ids)} "
               f"AND EXISTS (SELECT 1 FROM {target}.{spawn_table} t WHERE t.guid=a.guid+{GUID_OFFSET} "
               f"AND t.id=s.id AND t.map=s.map AND t.position_x=s.position_x "
               f"AND t.position_y=s.position_y AND t.position_z=s.position_z) "
               f"AND a.guid+{GUID_OFFSET} IN ({','.join(sorted(imported_spawn_guids[spawn_table])) or '0'})")
        if not dry_run:
            run(container, user, password, sql)

    # Spawn-linked rows follow every mapped spawn, so a replay fills the game events and formations an
    # interrupted run left out; INSERT IGNORE makes the repeats no-ops. Link types not imported are reported.
    imported = {t: ",".join(sorted(imported_spawn_guids[t])) for t in ("creature", "gameobject")}
    sources = {t: ",".join(str(int(g) - GUID_OFFSET) for g in sorted(imported_spawn_guids[t]))
               for t in imported}
    if not dry_run:
        for kind in ("creature", "gameobject"):
            table = f"game_event_{kind}"
            if imported[kind] and table_exists(container, user, password, source, table):
                count = insert_count(
                    container, user, password,
                    f"INSERT IGNORE INTO {target}.{table} (`eventEntry`,`guid`) "
                    f"SELECT g.eventEntry, g.guid+{GUID_OFFSET} FROM {source}.{table} g "
                    f"WHERE g.guid IN ({sources[kind]}) AND EXISTS "
                    f"(SELECT 1 FROM {target}.game_event e WHERE e.eventEntry=ABS(g.eventEntry))")
                if count:
                    report[table] = count
        if imported["creature"] and table_exists(container, user, password, source, "creature_formations"):
            count = insert_count(
                container, user, password,
                f"INSERT IGNORE INTO {target}.creature_formations "
                f"(`leaderGUID`,`memberGUID`,`dist`,`angle`,`groupAI`,`point_1`,`point_2`) "
                f"SELECT f.LeaderGUID+{GUID_OFFSET}, f.MemberGUID+{GUID_OFFSET}, f.FollowDistance, "
                f"f.FollowAngle, f.GroupAI, "
                # AC tests point_1 first, so an inversion point that is the only one set moves there.
                f"IF(f.InversionPoint1 > 0, f.InversionPoint1, GREATEST(f.InversionPoint2, 0)), "
                f"IF(f.InversionPoint1 > 0, GREATEST(f.InversionPoint2, 0), 0) "
                f"FROM {source}.creature_formations f "
                f"WHERE f.LeaderGUID IN ({sources['creature']}) AND f.MemberGUID IN ({sources['creature']})")
            if count:
                report["creature_formations"] = count
    unsupported = {}
    if table_exists(container, user, password, source, "linked_respawn"):
        # linkType: 0 creature->creature, 1 creature->gameobject, 2 gameobject->gameobject, 3 gameobject->creature
        keys = {"creature": ((0, 1), (0, 3)), "gameobject": ((2, 3), (1, 2))}  # (guid types, linkedGuid types)
        conditions = [f"(l.linkType IN ({','.join(map(str, own))}) AND l.guid IN ({sources[kind]})) "
                      f"OR (l.linkType IN ({','.join(map(str, linked))}) AND l.linkedGuid IN ({sources[kind]}))"
                      for kind, (own, linked) in keys.items() if imported[kind]]
        if conditions:
            count = scalar(container, user, password,
                           f"SELECT COUNT(*) FROM {source}.linked_respawn l WHERE {' OR '.join(conditions)}")
            if count:
                unsupported["linked_respawn"] = count
    if imported["creature"] or imported["gameobject"]:
        if table_exists(container, user, password, source, "pool_members"):
            count = sum(scalar(container, user, password,
                               f"SELECT COUNT(*) FROM {source}.pool_members WHERE type={flag} "
                               f"AND spawnId IN ({sources[kind]})")
                        for kind, flag in (("creature", 0), ("gameobject", 1)) if imported[kind])
            if count:
                unsupported["pool_members"] = count
    if unsupported:
        report["unsupported_spawn_links"] = unsupported

    # Template addons carry the interaction bytes/auras; creature spell-click
    # rows keep the NPC's spell-click flag alive in the loader.
    if (creature_entries
            and table_exists(container, user, password, source, "creature_template_addon")):
        present = set(fetch(container, user, password,
                            f"SELECT DISTINCT entry FROM {target}.creature_template_addon "
                            f"WHERE entry IN ({creature_ids})"))
        candidates = sorted(creature_entries - present)
        if candidates:
            target_names = {name for name, _ in columns(container, user, password, target,
                                                        "creature_template_addon")}
            source_names = {name for name, _ in columns(container, user, password, source,
                                                        "creature_template_addon")}
            mapping = [(c, c) for c in common_columns(container, user, password, target, source,
                                                      "creature_template_addon") if c != "entry"]
            if "path_id" in target_names and "waypointPathId" in source_names:
                mapping.append(("path_id", "waypointPathId"))
            names = ",".join(quote(target_col) for target_col, _source_col in mapping)
            selected = ",".join(f"a.{quote(source_col)}" for _target_col, source_col in mapping)
            sql = (f"INSERT IGNORE INTO {target}.creature_template_addon (`entry`,{names}) "
                   f"SELECT a.entry,{selected} FROM {source}.creature_template_addon a "
                   f"WHERE a.entry IN ({','.join(candidates)})")
            if dry_run:
                report["creature_template_addon"] = 0
            else:
                rows = run(container, user, password, sql + "; SELECT ROW_COUNT()")
                report["creature_template_addon"] = int(rows[-1][0]) if rows else 0
    if creature_entries and table_exists(container, user, password, source, "npc_spellclick_spells"):
        present = set(fetch(container, user, password,
                            f"SELECT DISTINCT npc_entry FROM {target}.npc_spellclick_spells "
                            f"WHERE npc_entry IN ({creature_ids})"))
        candidates = sorted(creature_entries - present)
        if candidates:
            report["npc_spellclick_spells"], _ = import_table(
                container, user, password, target, source, "npc_spellclick_spells",
                "npc_entry", candidates, dry_run)

    # Movement paths referenced by creature and template addons. Each source path lands at
    # source id + PATH_OFFSET; an addon row is re-pointed only while it still carries the raw
    # source id, i.e. this importer inserted it and never migrated it. The clean world has no
    # spawns below GUID_OFFSET, so every addon is TDB-sourced and the raw id is its own source
    # path; a binding that already points elsewhere has been migrated and is left alone.
    path_targets: dict[str, list[tuple[str, str]]] = {}
    if creature_entries and table_exists(container, user, password, source, "creature_addon"):
        for guid, path_id in run(container, user, password,
                                 f"SELECT ca.guid, ca.waypointPathId FROM {source}.creature_addon ca "
                                 f"JOIN {source}.creature c ON c.guid=ca.guid "
                                 f"WHERE c.id IN ({creature_ids}) "
                                 f"AND {spawn_scope(source, 'creature', 'c', zone, map_id, quest_ids)} "
                                 f"AND ca.waypointPathId<>0 "
                                 f"AND EXISTS (SELECT 1 FROM {target}.creature_addon ta "
                                 f"JOIN {target}.creature t ON t.guid=ta.guid "
                                 f"WHERE ta.guid=ca.guid+{GUID_OFFSET} "
                                 f"AND ta.path_id=ca.waypointPathId "
                                 f"AND t.id=c.id AND t.map=c.map "
                                 f"AND t.position_x=c.position_x AND t.position_y=c.position_y "
                                 f"AND t.position_z=c.position_z)"):
            path_targets.setdefault(path_id, []).append(
                ("creature_addon", str(int(guid) + GUID_OFFSET)))
    if (creature_entries
            and table_exists(container, user, password, source, "creature_template_addon")):
        for entry, path_id in run(container, user, password,
                                  f"SELECT a.entry, a.waypointPathId FROM {source}.creature_template_addon a "
                                  f"WHERE a.entry IN ({creature_ids}) AND a.waypointPathId<>0 "
                                  f"AND EXISTS (SELECT 1 FROM {target}.creature_template_addon ta "
                                  f"WHERE ta.entry=a.entry AND ta.path_id=a.waypointPathId)"):
            path_targets.setdefault(path_id, []).append(("creature_template_addon", entry))
    if path_targets and table_exists(container, user, password, source, "waypoint_data"):
        waypoint_columns = [c for c in common_columns(container, user, password, target, source,
                                                      "waypoint_data") if c != "id"]
        names = ",".join(quote(c) for c in waypoint_columns)
        imported_paths = 0
        for path_id, consumers in sorted(path_targets.items()):
            target_path = str(int(path_id) + PATH_OFFSET)
            occupied = scalar(container, user, password,
                              f"SELECT COUNT(*) FROM {target}.waypoint_data WHERE id={target_path}")
            if occupied:  # only an identical block from an earlier run of this importer may sit there
                same = scalar(container, user, password,
                              f"SELECT COUNT(*) FROM {source}.waypoint_data w JOIN {target}.waypoint_data t "
                              f"ON t.id={target_path} AND t.point=w.point AND t.position_x=w.position_x "
                              f"AND t.position_y=w.position_y AND t.position_z=w.position_z WHERE w.id={path_id}")
                total = scalar(container, user, password,
                               f"SELECT COUNT(*) FROM {source}.waypoint_data WHERE id={path_id}")
                if occupied != total or same != total:
                    raise RuntimeError(f"waypoint_data id {target_path} is occupied by a different path "
                                       f"({occupied} rows, source path {path_id} has {total})")
            if not dry_run:
                grouped: dict[str, list[str]] = {}
                for consumer_table, key in consumers:
                    grouped.setdefault(consumer_table, []).append(key)
                # Copy the path before re-pointing, so a crash in between leaves the raw id for replay.
                run(container, user, password,
                    f"INSERT IGNORE INTO {target}.waypoint_data (`id`,{names}) "
                    f"SELECT {target_path},{names} FROM {source}.waypoint_data WHERE id={path_id}")
                for consumer_table, keys in grouped.items():
                    key_column = "guid" if consumer_table == "creature_addon" else "entry"
                    run(container, user, password,
                        f"UPDATE {target}.{consumer_table} SET path_id={target_path} "
                        f"WHERE {key_column} IN ({','.join(keys)})")
            imported_paths += 1
        report["waypoint_data"] = imported_paths

    # Source GUID -> target GUID, for scripts that target a specific spawn. Matching
    # is done in SQL (no float round-trip) on entry, map and position: a shifted
    # GUID this importer owns counts as imported, and a native spawn at the same
    # position is the fallback.
    def match_spawns(table, entries):
        rows = run(container, user, password,
                   f"SELECT s.guid, s.id, MAX(t.guid=s.guid+{GUID_OFFSET}), MIN(t.guid) "
                   f"FROM {source}.{table} s JOIN {target}.{table} t ON {spawn_match()} "
                   f"WHERE s.id IN ({entries}) AND {spawn_scope(source, table, 's', zone, map_id, quest_ids)} "
                   f"GROUP BY s.guid, s.id")
        imported, native = {}, {}
        for guid, entry, is_imported, native_guid in rows:
            if is_imported == "1" and str(int(guid) + GUID_OFFSET) in imported_spawn_guids[table]:
                imported[guid] = entry
            else:
                native[guid] = native_guid
        # A spawn the wider dedupe skipped has no 0.5 m twin; map it to the native spawn whose
        # placement it duplicates, or its scripts and conditions are dropped with the GUID.
        skipped = (set(fetch(container, user, password,
                             f"SELECT s.guid FROM {source}.{table} s WHERE s.id IN ({entries}) "
                             f"AND {spawn_scope(source, table, 's', zone, map_id, quest_ids)}"))
                   - imported.keys() - native.keys())
        if skipped:
            for guid, native_guid in run(container, user, password,
                                         f"SELECT s.guid, MIN(t.guid) FROM {source}.{table} s "
                                         f"JOIN {target}.{table} t ON {spawn_match(SPAWN_DEDUPE_RADIUS_M)} "
                                         f"WHERE s.guid IN ({','.join(sorted(skipped))}) AND t.guid<{GUID_OFFSET} "
                                         f"GROUP BY s.guid"):
                native[guid] = native_guid
        return imported, native

    guid_map: list[tuple[int, int, int]] = []
    guid_owner: dict[str, str] = {}
    go_guid_owner: dict[str, str] = {}
    source_guids: list[str] = []
    script_go_candidates: list[str] = []
    if creature_entries:
        imported, native = match_spawns("creature", creature_ids)
        guid_owner.update(imported)
        source_guids = list(imported)
        guid_map += [(0, int(g), int(g) + GUID_OFFSET) for g in imported]
        guid_map += [(0, int(g), int(n)) for g, n in native.items()]
    source_go_guids = fetch(container, user, password,
                            f"SELECT s.guid FROM {source}.gameobject s WHERE s.id IN ({gameobject_ids}) "
                            f"AND {spawn_scope(source, 'gameobject', 's', zone, map_id, quest_ids)}")
    if gameobject_entries:
        imported, native = match_spawns("gameobject", gameobject_ids)
        script_go_candidates = list(imported)
        go_guid_owner.update(imported)
        guid_map += [(1, int(g), int(g) + GUID_OFFSET) for g in imported]
        guid_map += [(1, int(g), int(n)) for g, n in native.items()]

    map_table = "_plan203_guid_map"
    if not dry_run:
        run(container, user, password, f"DROP TABLE IF EXISTS {target}.{map_table}")
        run(container, user, password,
            f"CREATE TABLE {target}.{map_table} "
            f"(kind TINYINT NOT NULL, src_guid BIGINT NOT NULL, dst_guid BIGINT NOT NULL, "
            f"PRIMARY KEY (kind, src_guid))")
        if guid_map:
            run(container, user, password,
                f"INSERT INTO {target}.{map_table} VALUES "
                + ",".join(f"({kind},{src},{dst})" for kind, src, dst in guid_map))

    # A native template keeps its own loot/Data1 pointer: importing the source block for it would only leave an
    # orphan loot entry the loader reports as useless. New templates take the source value.
    existing_creatures = set(fetch(container, user, password,
                                   f"SELECT entry FROM {target}.creature_template WHERE entry IN ({creature_ids})"))
    loot_ids = sorted({r[0] for r in run(container, user, password,
                                         f"SELECT DISTINCT lootid FROM {target}.creature_template "
                                         f"WHERE entry IN ({creature_ids}) AND lootid<>0")}
                      | {r[1] for r in run(container, user, password,
                                           f"SELECT entry, lootid FROM {source}.creature_template "
                                           f"WHERE entry IN ({creature_ids}) AND lootid<>0")
                         if r[0] not in existing_creatures})
    existing_gos = set(fetch(container, user, password,
                             f"SELECT entry FROM {target}.gameobject_template WHERE entry IN ({gameobject_ids})"))
    gameobject_loot = sorted({r[0] for r in run(container, user, password,
                                                f"SELECT Data1 FROM {target}.gameobject_template "
                                                f"WHERE entry IN ({gameobject_ids}) AND type IN (3,25) AND Data1<>0")}
                             | {r[1] for r in run(container, user, password,
                                                  f"SELECT entry, Data1 FROM {source}.gameobject_template "
                                                  f"WHERE entry IN ({gameobject_ids}) AND type IN (3,25) AND Data1<>0")
                                if r[0] not in existing_gos})
    if loot_ids:
        present = set(fetch(container, user, password,
                            f"SELECT DISTINCT Entry FROM {target}.creature_loot_template "
                            f"WHERE Entry IN ({','.join(loot_ids)})"))
        loot_ids = [entry for entry in loot_ids if entry not in present]
    if gameobject_loot:
        present = set(fetch(container, user, password,
                            f"SELECT DISTINCT Entry FROM {target}.gameobject_loot_template "
                            f"WHERE Entry IN ({','.join(gameobject_loot)})"))
        gameobject_loot = [entry for entry in gameobject_loot if entry not in present]
    if loot_ids:
        report["creature_loot_template"], _ = import_table(container, user, password, target, source,
                                                          "creature_loot_template", "Entry",
                                                          loot_ids, dry_run)
    if not dry_run:  # the loot block can also be shared with an existing native template
        run(container, user, password,
            f"UPDATE {target}.creature_template t JOIN {source}.creature_template s ON s.entry=t.entry "
            f"SET t.lootid=s.lootid WHERE t.entry IN ({creature_ids}) AND t.lootid=0 "
            f"AND EXISTS (SELECT 1 FROM {target}.creature_loot_template l WHERE l.Entry=s.lootid)")
    if gameobject_loot:
        report["gameobject_loot_template"], _ = import_table(container, user, password, target, source,
                                                            "gameobject_loot_template", "Entry",
                                                            gameobject_loot, dry_run)

    refs: set[str] = set()
    loot_blocks = [("creature_loot_template", loot_ids), ("gameobject_loot_template", gameobject_loot)]
    for column, table in (("pickpocketloot", "pickpocketing_loot_template"), ("skinloot", "skinning_loot_template")):
        if not new_creatures:
            continue
        entries = fetch(container, user, password,
                        f"SELECT DISTINCT {column} FROM {source}.creature_template "
                        f"WHERE entry IN ({','.join(new_creatures)}) AND {column}<>0")
        if not entries:
            continue
        present = set(fetch(container, user, password, f"SELECT DISTINCT Entry FROM {target}.{table}"))
        entries = sorted(set(entries) - present)
        if entries:
            report[table], _ = import_table(container, user, password, target, source, table, "Entry", entries, dry_run)
            loot_blocks.append((table, entries))
            refs.update(fetch(container, user, password,
                              f"SELECT DISTINCT ABS(Reference) FROM {source}.{table} "
                              f"WHERE Entry IN ({','.join(entries)}) AND Reference<>0"))
    if loot_ids:
        refs.update(fetch(container, user, password,
                          f"SELECT DISTINCT ABS(Reference) FROM {source}.creature_loot_template "
                          f"WHERE Entry IN ({','.join(loot_ids)}) AND Reference<>0"))
    if gameobject_loot:
        refs.update(fetch(container, user, password,
                          f"SELECT DISTINCT ABS(Reference) FROM {source}.gameobject_loot_template "
                          f"WHERE Entry IN ({','.join(gameobject_loot)}) AND Reference<>0"))
    imported_refs: set[str] = set()
    inserted_refs: set[str] = set()
    while refs - imported_refs:
        batch = sorted(refs - imported_refs)
        present = set(fetch(container, user, password,
                            f"SELECT DISTINCT Entry FROM {target}.reference_loot_template "
                            f"WHERE Entry IN ({','.join(batch)})"))
        new = [entry for entry in batch if entry not in present]
        imported_refs.update(batch)
        if not new:
            continue
        if not dry_run:
            import_table(container, user, password, target, source, "reference_loot_template",
                         "Entry", new, dry_run)
        inserted_refs.update(new)
        refs.update(fetch(container, user, password,
                          f"SELECT DISTINCT ABS(Reference) FROM {source}.reference_loot_template "
                          f"WHERE Entry IN ({','.join(new)}) AND Reference<>0"))
    if inserted_refs:
        report["reference_loot_template"] = len(inserted_refs)
        loot_blocks.append(("reference_loot_template", sorted(inserted_refs)))
    if drop_items and not dry_run:  # the client store has no row and the target cannot get one
        items = ",".join(drop_items)
        for table, entries in loot_blocks:
            if entries:
                run(container, user, password,
                    f"DELETE FROM {target}.{table} WHERE Entry IN ({','.join(entries)}) "
                    f"AND Reference=0 AND Item IN ({items})")
    if not dry_run:
        # Include unspawned template dependencies too; the zone checker only sees zone spawns.
        missing_items = set()
        for table, entries in loot_blocks:
            if entries:
                missing_items.update(fetch(container, user, password,
                    f"SELECT DISTINCT l.Item FROM {target}.{table} l WHERE l.Entry IN ({','.join(entries)}) "
                    f"AND l.Reference=0 AND l.Item>0 AND NOT EXISTS "
                    f"(SELECT 1 FROM {target}.item_template i WHERE i.entry=l.Item)"))
        report["missing_items"] = sorted(missing_items)

    # AC-native scripts win: only fill scripts the target does not have. Only
    # entries that actually carry a source script are candidates, so an orphaned
    # condition cannot be imported for an entry with no script.
    script_filters: list[tuple[str, int]] = []
    source_creature_scripts = set(fetch(container, user, password,
                                        f"SELECT DISTINCT entryorguid FROM {source}.smart_scripts "
                                        f"WHERE source_type=0 AND entryorguid IN ({creature_ids})"))
    # A native template bound to another AI or a registered C++ script keeps it: the AI selector prefers
    # ScriptName, so SmartAI rows for it would never run.
    native_ai = set(fetch(container, user, password,
                          f"SELECT entry FROM {target}.creature_template WHERE entry IN ({creature_ids}) "
                          f"AND (ScriptName<>'' OR AIName NOT IN ('','SmartAI'))")) - set(new_creatures)
    script_creatures = {e for e in creature_entries if e in source_creature_scripts
                        and e not in native_ai
                        and e not in set(fetch(container, user, password,
                                               f"SELECT DISTINCT entryorguid FROM {target}.smart_scripts "
                                               f"WHERE source_type=0 AND entryorguid IN ({creature_ids})"))}
    if script_creatures:
        script_filters.append((f"source_type=0 AND entryorguid IN "
                               f"({','.join(sorted(script_creatures))})", 0))
    source_gameobject_scripts = set(fetch(container, user, password,
                                          f"SELECT DISTINCT entryorguid FROM {source}.smart_scripts "
                                          f"WHERE source_type=1 AND entryorguid IN ({gameobject_ids})"))
    native_go_ai = set(fetch(container, user, password,
                             f"SELECT entry FROM {target}.gameobject_template WHERE entry IN ({gameobject_ids}) "
                             f"AND (ScriptName<>'' OR AIName NOT IN ('','SmartGameObjectAI'))")) - set(new_gameobjects)
    script_gameobjects = {e for e in gameobject_entries if e in source_gameobject_scripts
                          and e not in native_go_ai
                          and e not in set(fetch(container, user, password,
                                                 f"SELECT DISTINCT entryorguid FROM {target}.smart_scripts "
                                                 f"WHERE source_type=1 AND entryorguid IN ({gameobject_ids})"))}
    if script_gameobjects:
        script_filters.append((f"source_type=1 AND entryorguid IN "
                               f"({','.join(sorted(script_gameobjects))})", 0))
    script_guids = []
    if source_guids:
        shifted = ",".join(str(-(int(guid) + GUID_OFFSET)) for guid in source_guids)
        present = set(fetch(container, user, password,
                            f"SELECT DISTINCT entryorguid FROM {target}.smart_scripts "
                            f"WHERE source_type=0 AND entryorguid IN ({shifted})"))
        script_guids = [guid for guid in source_guids
                        if str(-(int(guid) + GUID_OFFSET)) not in present]
    if script_guids:
        negative = ",".join(str(-int(guid)) for guid in script_guids)
        script_filters.append((f"source_type=0 AND entryorguid IN ({negative})", GUID_OFFSET))
    script_go_guids = []
    if script_go_candidates:
        imported = script_go_candidates
        shifted = ",".join(str(-(int(guid) + GUID_OFFSET)) for guid in imported) or "0"
        present = set(fetch(container, user, password,
                            f"SELECT DISTINCT entryorguid FROM {target}.smart_scripts "
                            f"WHERE source_type=1 AND entryorguid IN ({shifted})"))
        script_go_guids = [guid for guid in imported
                           if str(-(int(guid) + GUID_OFFSET)) not in present]
    if script_go_guids:
        negative = ",".join(str(-int(guid)) for guid in script_go_guids)
        script_filters.append((f"source_type=1 AND entryorguid IN ({negative})", GUID_OFFSET))

    # Dependency discovery covers every source script of the imported entries and spawns, not only the
    # rows inserted on this run. The text/waypoint imports below are fill-only, so a replay repairs the
    # groups and paths an interrupted run left out while native rows keep their own values (#213).
    all_source_filters: list[tuple[str, int]] = []
    for source_type, entries, candidates in (("0", source_creature_scripts, creature_entries),
                                             ("1", source_gameobject_scripts, gameobject_entries)):
        discovered = sorted(entries & candidates)
        if discovered:
            all_source_filters.append(
                (f"source_type={source_type} AND entryorguid IN ({','.join(discovered)})", 0))
    for source_type, guids in (("0", source_guids), ("1", script_go_candidates)):
        if guids:
            all_source_filters.append(
                (f"source_type={source_type} AND entryorguid IN "
                 f"({','.join(str(-int(guid)) for guid in guids)})", GUID_OFFSET))
    all_creature_filters = [f for f in all_source_filters if f[0].startswith("source_type=0")]

    def insert_scripts(where, shift):
        columns_ = common_columns(container, user, password, target, source, "smart_scripts")
        names = ",".join(quote(c) for c in columns_)
        selected = []
        for column in columns_:
            if shift and column == "entryorguid":
                selected.append(f"(s.{quote(column)} - {shift})")
            elif column.startswith("event_param") and column[len("event_param"):].isdigit():
                unused = ",".join(str(event) for count, events in SMART_EVENT_PARAM_COUNTS.items()
                                  if count < int(column[len("event_param"):]) for event in events)
                # Timed action lists are forced to UPDATE_OOC by the loader, regardless of stored event_type.
                selected.append(f"IF(s.source_type<>9 AND s.event_type IN ({unused}), 0, s.{quote(column)})")
            elif column in ("target_param2", "target_param3", "target_param4"):
                # NONE/SELF/VICTIM take no params in AC; TC data may keep e.g. a max distance there
                selected.append(f"IF(s.target_type IN (0,1,2), 0, s.{quote(column)})")
            elif column == "target_param1":
                selected.append("CASE WHEN s.target_type IN (0,1,2) THEN 0 "
                                "WHEN s.target_type=10 THEN IFNULL(mc.dst_guid, s.target_param1) "
                                "WHEN s.target_type=14 THEN IFNULL(mg.dst_guid, s.target_param1) "
                                "ELSE s.target_param1 END")
            elif column == "action_param3":
                # TC 4.3.4 reads TALK's third param as a nonzero true value; AC validates it as a strict boolean
                selected.append("IF(s.action_type=1 AND s.action_param3<>0, 1, s.action_param3)")
            elif column == "action_param1":
                # TC's WP_START param1 is a run boolean; AC's ESCORT_START param1 is FORCED_MOVEMENT
                # (0 none, 1 walk, 2 run). Shift 0/1 so TC's walk stays walk (#331).
                selected.append("IF(s.action_type=53 AND s.action_param1 IN (0,1), "
                                "s.action_param1+1, s.action_param1)")
            elif column == "action_type":
                # SELF_CAST makes each target cast on itself; INVOKER_CAST is equivalent only for target 7.
                # AC rejects 85, so translate this timed-list case to 134.
                selected.append("IF(s.source_type=9 AND s.action_type=85 AND s.target_type=7, 134, s.action_type)")
            else:
                selected.append(f"s.{quote(column)}")
        run(container, user, password,
            f"INSERT IGNORE INTO {target}.smart_scripts ({names}) "
            f"SELECT {','.join(selected)} FROM {source}.smart_scripts s "
            f"LEFT JOIN {target}.{map_table} mc ON mc.kind=0 AND mc.src_guid=s.target_param1 "
            f"LEFT JOIN {target}.{map_table} mg ON mg.kind=1 AND mg.src_guid=s.target_param1 "
            f"WHERE {where} AND (s.action_type NOT IN {UNSUPPORTED_SMART_ACTIONS} "
            f"OR (s.source_type=9 AND s.action_type=85 AND s.target_type=7)) "
            f"AND NOT (s.target_type=10 AND s.target_param1<>0 AND mc.dst_guid IS NULL) "
            f"AND NOT (s.target_type=14 AND s.target_param1<>0 AND mg.dst_guid IS NULL)")

    imported_scripts = 0
    imported_owners = set()
    for where, shift in script_filters:
        if not dry_run:
            insert_scripts(where, shift)
        imported_scripts += scalar(container, user, password,
                                   f"SELECT COUNT(*) FROM {source}.smart_scripts WHERE {where}")
        for owner, source_type in run(container, user, password,
                                      f"SELECT DISTINCT entryorguid,source_type FROM {source}.smart_scripts "
                                      f"WHERE {where}"):
            imported_owners.add((int(owner) - shift, int(source_type)))

    if not dry_run and imported_owners:
        # Dropping a row (unsupported action, missing target mapping) can leave another row's link pointing at an
        # event id that no longer exists; keep the action and clear the dead link. Scoped to the scripts imported
        # above so native rows keep whatever links they had.
        owners = ",".join(f"({owner},{source_type})" for owner, source_type in sorted(imported_owners))
        run(container, user, password,
            f"UPDATE {target}.smart_scripts s LEFT JOIN {target}.smart_scripts l "
            f"ON l.entryorguid=s.entryorguid AND l.source_type=s.source_type AND l.id=s.link "
            f"SET s.link=0 WHERE s.link<>0 AND l.id IS NULL "
            f"AND (s.entryorguid,s.source_type) IN ({owners})")

    def guid_script_owners(guids, owner_map, source_type):
        """Entries whose imported GUID script landed, for the AI-name fill (a GUID script needs SmartAI on the template)."""
        if not guids:
            return set()
        present = set(fetch(container, user, password,
                            f"SELECT DISTINCT entryorguid FROM {target}.smart_scripts "
                            f"WHERE source_type={source_type} AND entryorguid IN "
                            f"({','.join(str(-(int(guid) + GUID_OFFSET)) for guid in guids)})"))
        return {owner_map[str(-int(owner) - GUID_OFFSET)] for owner in present
                if str(-int(owner) - GUID_OFFSET) in owner_map}

    if not dry_run:
        scripted_creatures = set(fetch(container, user, password,
                                       f"SELECT DISTINCT entryorguid FROM {target}.smart_scripts "
                                       f"WHERE source_type=0 AND entryorguid IN ({creature_ids})"))
        scripted_gameobjects = set(fetch(container, user, password,
                                         f"SELECT DISTINCT entryorguid FROM {target}.smart_scripts "
                                         f"WHERE source_type=1 AND entryorguid IN ({gameobject_ids})"))
        scripted_creatures &= script_creatures  # fill-only: AIName is set where it is still empty
        scripted_gameobjects &= script_gameobjects
        scripted_creatures |= guid_script_owners(script_guids, guid_owner, 0) - native_ai
        scripted_gameobjects |= guid_script_owners(script_go_guids, go_guid_owner, 1) - native_go_ai
        if scripted_creatures:
            run(container, user, password,
                f"UPDATE {target}.creature_template SET AIName='SmartAI' "
                f"WHERE entry IN ({','.join(sorted(scripted_creatures))}) "
                f"AND (AIName='' OR AIName IS NULL)")
        if scripted_gameobjects:
            run(container, user, password,
                f"UPDATE {target}.gameobject_template SET AIName='SmartGameObjectAI' "
                f"WHERE entry IN ({','.join(sorted(scripted_gameobjects))}) "
                f"AND (AIName='' OR AIName IS NULL)")
        if new_creatures:  # a new template copied TDB's AIName; drop it when no imported script can run
            run(container, user, password,
                f"UPDATE {target}.creature_template t SET t.AIName='' "
                f"WHERE t.entry IN ({','.join(new_creatures)}) AND t.AIName='SmartAI' "
                f"AND NOT EXISTS (SELECT 1 FROM {target}.smart_scripts s WHERE s.source_type=0 "
                f"AND (s.entryorguid=t.entry OR (s.entryorguid<0 AND -s.entryorguid IN "
                f"(SELECT c.guid FROM {target}.creature c WHERE c.id=t.entry))))")
        if new_gameobjects:
            run(container, user, password,
                f"UPDATE {target}.gameobject_template t SET t.AIName='' "
                f"WHERE t.entry IN ({','.join(new_gameobjects)}) AND t.AIName='SmartGameObjectAI' "
                f"AND NOT EXISTS (SELECT 1 FROM {target}.smart_scripts s WHERE s.source_type=1 "
                f"AND (s.entryorguid=t.entry OR (s.entryorguid<0 AND -s.entryorguid IN "
                f"(SELECT g.guid FROM {target}.gameobject g WHERE g.id=t.entry))))")

    if not dry_run:  # fill-only: a native giver the source flags as QUESTGIVER (2) needs the bit for its quests
        givers = (f"(SELECT id FROM {target}.creature_queststarter WHERE quest IN ({quest_ids}) "
                  f"UNION SELECT id FROM {target}.creature_questender WHERE quest IN ({quest_ids}))")
        run(container, user, password,
            f"UPDATE {target}.creature_template t JOIN {source}.creature_template s ON s.entry=t.entry "
            f"SET t.npcflag=t.npcflag|2 WHERE (s.npcflag&2)<>0 AND (t.npcflag&2)=0 AND t.entry IN {givers}")
        # difficulty rows are separate templates; mirror the bit or the loader reports a npcflag mismatch
        run(container, user, password,
            f"UPDATE {target}.creature_template d "
            f"JOIN {target}.creature_template t ON d.entry IN (t.difficulty_entry_1,t.difficulty_entry_2,"
            f"t.difficulty_entry_3) "
            f"SET d.npcflag=d.npcflag|2 WHERE (t.npcflag&2)<>0 AND (d.npcflag&2)=0 AND t.entry IN {givers}")

    def timed_references(where, owners=None):
        found: set[str] = set()
        for row in run(container, user, password,
                       f"SELECT entryorguid,action_type,action_param1,action_param2,action_param3,"
                       f"action_param4,action_param5,action_param6 FROM {source}.smart_scripts "
                       f"WHERE {where}"):
            owner, action, params = row[0], int(row[1]), row[2:]
            owner_entry = ""
            if owners is None:
                owner_entry = owner if not owner.startswith("-") else guid_owner.get(owner[1:], "")
            ids: set[str] = set()
            if action == 80 and params[0] and int(params[0]) > 0:
                ids.add(params[0])
            elif action == 87:
                ids.update(param for param in params if param and int(param) > 0)
            elif action == 88:
                low, high = int(params[0] or 0), int(params[1] or 0)
                if low and high and high >= low:
                    ids.update(fetch(container, user, password,
                                     f"SELECT DISTINCT entryorguid FROM {source}.smart_scripts "
                                     f"WHERE source_type=9 AND entryorguid BETWEEN {low} AND {high}"))
            for list_id in ids:
                if owners is not None:
                    timed_owners.setdefault(list_id, set()).update(owners)
                elif owner_entry:
                    timed_owners.setdefault(list_id, set()).add(owner_entry)
            found.update(ids)
        return found

    timed_owners: dict[str, set[str]] = {}
    timed: set[str] = set()
    for where, _shift in all_source_filters:
        timed.update(timed_references(where))
    imported_timed: set[str] = set()
    inserted: set[str] = set()
    expanded: dict[str, set[str]] = {}
    pending = set(timed)
    while pending:
        entry = pending.pop()
        if entry not in inserted:
            inserted.add(entry)
            present = set(fetch(container, user, password,
                                f"SELECT DISTINCT entryorguid FROM {target}.smart_scripts "
                                f"WHERE source_type=9 AND entryorguid={entry}"))
            if entry not in present and not dry_run:
                insert_scripts(f"source_type=9 AND entryorguid IN ({entry})", 0)
                imported_timed.add(entry)
        # An owner can arrive after this list was expanded. Propagate the grown owner set so the
        # nested lists (and their SELF/MAP talk groups) learn every owner, not only the first one.
        owners = timed_owners.get(entry, set())
        if entry in expanded and not owners - expanded[entry]:
            continue
        expanded.setdefault(entry, set()).update(owners)
        found = timed_references(f"source_type=9 AND entryorguid IN ({entry})", owners)
        timed.update(found)
        pending.update(found)
    if imported_scripts or imported_timed:
        report["smart_scripts"] = imported_scripts
        report["smart_scripts_timed"] = len(imported_timed)

    text_groups: set[tuple[str, str]] = set()
    for where, _shift in all_creature_filters:
        for owner, group, target_type, target_param1 in run(
                container, user, password,
                f"SELECT entryorguid, action_param1, target_type, target_param1 FROM {source}.smart_scripts "
                f"WHERE {where} AND action_type=1"):
            mapped_owner = guid_owner.get(owner[1:], "") if owner.startswith("-") else owner
            if mapped_owner:
                text_groups.add((mapped_owner, group))
            if int(target_type) in (9, 11, 19, 204) and int(target_param1) > 0:
                text_groups.add((target_param1, group))
    for timed_id in sorted(timed):
        for group, target_type, target_param1 in run(
                container, user, password,
                f"SELECT action_param1, target_type, target_param1 FROM {source}.smart_scripts "
                f"WHERE source_type=9 AND entryorguid={timed_id} AND action_type=1"):
            for owner in timed_owners.get(timed_id, set()):
                text_groups.add((owner, group))
            if int(target_type) in (9, 11, 19, 204) and int(target_param1) > 0:
                text_groups.add((target_param1, group))
    if text_groups:
        group_filter = " OR ".join(f"(s.CreatureID={owner} AND s.GroupID={group})"
                                   for owner, group in sorted(text_groups))
        columns_ = common_columns(container, user, password, target, source, "creature_text")
        names = ",".join(quote(c) for c in columns_)
        selected = ",".join(f"s.{quote(c)}" for c in columns_)
        added = 0
        if not dry_run:
            # Lines are keyed by (CreatureID, GroupID, ID): a native group keeps the lines it already
            # has and gains the missing ones instead of blocking the whole group (#213).
            rows = run(container, user, password,
                       f"INSERT INTO {target}.creature_text ({names}) "
                       f"SELECT {selected} FROM {source}.creature_text s "
                       f"LEFT JOIN {target}.creature_text t ON t.CreatureID=s.CreatureID "
                       f"AND t.GroupID=s.GroupID AND t.ID=s.ID "
                       f"WHERE ({group_filter}) AND t.CreatureID IS NULL; SELECT ROW_COUNT()")
            added = int(rows[-1][0]) if rows else 0
        report["creature_text"] = added
        report["creature_text_groups"] = len(text_groups)

    path_ids: set[str] = set()
    for where, _shift in all_creature_filters:
        path_ids.update(fetch(container, user, password,
                              f"SELECT DISTINCT action_param2 FROM {source}.smart_scripts "
                              f"WHERE {where} AND action_type=53 AND action_param2<>0"))
    for timed_id in sorted(timed):
        path_ids.update(fetch(container, user, password,
                              f"SELECT DISTINCT action_param2 FROM {source}.smart_scripts "
                              f"WHERE source_type=9 AND entryorguid={timed_id} "
                              f"AND action_type=53 AND action_param2<>0"))
    if path_ids:
        present_paths = set(fetch(container, user, password,
                                  f"SELECT DISTINCT entry FROM {target}.waypoints "
                                  f"WHERE entry IN ({','.join(sorted(path_ids))})"))
        new_paths = [path for path in sorted(path_ids) if path not in present_paths]
        if new_paths:
            report["waypoints"], _ = import_table(container, user, password, target, source,
                                                 "waypoints", "entry", new_paths, dry_run)

    condition_filters: list[tuple[str, int]] = []
    if loot_ids:
        condition_filters.append((f"SourceTypeOrReferenceId=1 AND SourceGroup IN "
                                  f"({','.join(loot_ids)})", 0))
    if inserted_refs:
        condition_filters.append((f"SourceTypeOrReferenceId=10 AND SourceGroup IN "
                                  f"({','.join(sorted(inserted_refs))})", 0))
    if gameobject_loot:
        condition_filters.append((f"SourceTypeOrReferenceId=4 AND SourceGroup IN "
                                  f"({','.join(gameobject_loot)})", 0))
    if script_creatures:
        condition_filters.append((f"SourceTypeOrReferenceId=22 AND SourceId=0 AND SourceEntry IN "
                                  f"({','.join(sorted(script_creatures))})", 0))
    if script_gameobjects:
        condition_filters.append((f"SourceTypeOrReferenceId=22 AND SourceId=1 AND SourceEntry IN "
                                  f"({','.join(sorted(script_gameobjects))})", 0))
    if script_guids:
        negative = ",".join(str(-int(guid)) for guid in script_guids)
        condition_filters.append((f"SourceTypeOrReferenceId=22 AND SourceId=0 AND SourceEntry IN "
                                  f"({negative})", GUID_OFFSET))
    if script_go_guids:
        negative = ",".join(str(-int(guid)) for guid in script_go_guids)
        condition_filters.append((f"SourceTypeOrReferenceId=22 AND SourceId=1 AND SourceEntry IN "
                                  f"({negative})", GUID_OFFSET))
    if imported_timed:
        condition_filters.append((f"SourceTypeOrReferenceId=22 AND SourceId=9 AND SourceEntry IN "
                                  f"({','.join(sorted(imported_timed))})", 0))
    if quests:
        present_quest_conditions = set(fetch(container, user, password,
                                             f"SELECT DISTINCT SourceEntry FROM {target}.conditions "
                                             f"WHERE SourceTypeOrReferenceId=19 AND SourceEntry IN "
                                             f"({quest_ids})"))
        quest_condition_entries = [quest for quest in quests
                                   if quest not in present_quest_conditions]
        if quest_condition_entries:
            condition_filters.append((f"SourceTypeOrReferenceId=19 AND SourceEntry IN "
                                      f"({','.join(quest_condition_entries)})", 0))
    def select_conditions(columns_, shift):
        selected = []
        for column in columns_:
            if shift and column == "SourceEntry":
                selected.append(f"(c.{quote(column)} - {shift})")
            elif column == "ConditionValue3":
                selected.append("CASE WHEN c.ConditionTypeOrReference=31 THEN "
                                "IF(c.ConditionValue1=3, IFNULL(mc3.dst_guid, c.ConditionValue3), "
                                "IF(c.ConditionValue1=5, IFNULL(mg3.dst_guid, c.ConditionValue3), "
                                "c.ConditionValue3)) "
                                "ELSE c.ConditionValue3 END")
            else:
                selected.append(f"c.{quote(column)}")
        return ",".join(selected)

    imported_conditions = 0
    for where, shift in condition_filters:
        columns_ = common_columns(container, user, password, target, source, "conditions")
        names = ",".join(quote(c) for c in columns_)
        if not dry_run:
            run(container, user, password,
                f"INSERT IGNORE INTO {target}.conditions ({names}) "
                f"SELECT {select_conditions(columns_, shift)} FROM {source}.conditions c "
                f"LEFT JOIN {target}.{map_table} mc3 ON mc3.kind=0 AND mc3.src_guid=c.ConditionValue3 "
                f"LEFT JOIN {target}.{map_table} mg3 ON mg3.kind=1 AND mg3.src_guid=c.ConditionValue3 "
                f"WHERE {where} AND NOT (c.ConditionTypeOrReference=31 AND c.ConditionValue3<>0 AND "
                f"((c.ConditionValue1=3 AND mc3.dst_guid IS NULL) OR "
                f"(c.ConditionValue1=5 AND mg3.dst_guid IS NULL))) "
                f"AND NOT EXISTS (SELECT 1 FROM {target}.conditions t "
                f"WHERE t.SourceTypeOrReferenceId=c.SourceTypeOrReferenceId "
                f"AND t.SourceGroup=c.SourceGroup AND t.SourceId=c.SourceId "
                f"AND t.SourceEntry=c.SourceEntry-({shift}))")
        imported_conditions += scalar(container, user, password,
                                      f"SELECT COUNT(*) FROM {source}.conditions WHERE {where}")
    if imported_conditions:
        report["conditions"] = imported_conditions

    referenced_conditions: set[str] = set()
    for where, _shift in condition_filters:
        referenced_conditions.update(fetch(container, user, password,
                                           f"SELECT DISTINCT -ConditionTypeOrReference FROM {source}.conditions "
                                           f"WHERE {where} AND ConditionTypeOrReference<0"))
    handled_condition_refs: set[str] = set()
    imported_condition_refs = 0
    while referenced_conditions - handled_condition_refs:
        batch = sorted(referenced_conditions - handled_condition_refs)
        negative_ids = ",".join(f"-{entry}" for entry in batch)
        present = set(fetch(container, user, password,
                            f"SELECT DISTINCT -SourceTypeOrReferenceId FROM {target}.conditions "
                            f"WHERE SourceTypeOrReferenceId IN ({negative_ids})"))
        new = [entry for entry in batch if entry not in present]
        handled_condition_refs.update(batch)
        if not new or dry_run:
            continue
        negative_ids = ",".join(f"-{entry}" for entry in new)
        columns_ = common_columns(container, user, password, target, source, "conditions")
        names = ",".join(quote(c) for c in columns_)
        run(container, user, password,
            f"INSERT IGNORE INTO {target}.conditions ({names}) "
            f"SELECT {select_conditions(columns_, 0)} FROM {source}.conditions c "
            f"LEFT JOIN {target}.{map_table} mc3 ON mc3.kind=0 AND mc3.src_guid=c.ConditionValue3 "
            f"LEFT JOIN {target}.{map_table} mg3 ON mg3.kind=1 AND mg3.src_guid=c.ConditionValue3 "
            f"WHERE SourceTypeOrReferenceId IN ({negative_ids}) "
            f"AND NOT (c.ConditionTypeOrReference=31 AND c.ConditionValue3<>0 AND "
            f"((c.ConditionValue1=3 AND mc3.dst_guid IS NULL) OR "
            f"(c.ConditionValue1=5 AND mg3.dst_guid IS NULL)))")
        imported_condition_refs += len(new)
        referenced_conditions.update(
            fetch(container, user, password,
                  f"SELECT DISTINCT -ConditionTypeOrReference FROM {source}.conditions "
                  f"WHERE SourceTypeOrReferenceId IN ({negative_ids}) AND ConditionTypeOrReference<0"))
    if imported_condition_refs:
        report["condition_reference_templates"] = imported_condition_refs

    page_items = set(item_ids)
    item_columns = [f"RewardItem{i}" for i in range(1, 5)] + [f"RewardChoiceItemID{i}" for i in range(1, 7)]
    item_columns += ["StartItem"] + [f"ItemDrop{i}" for i in range(1, 5)]
    for row in run(container, user, password,
                   f"SELECT {','.join(item_columns)} FROM {source}.quest_template WHERE ID IN ({quest_ids})"):
        page_items.update(value for value in row if value and value != "0")
    for table, key, owner_table, selected, where, next_column in (
        ("broadcast_text", "ID", "creature_text", "BroadcastTextId",
         f"CreatureID IN ({creature_ids}) AND BroadcastTextId<>0", None),
        ("page_text", "ID", "gameobject_template", "Data7",
         f"entry IN ({gameobject_ids}) AND type=10 AND Data7<>0", "NextPageID"),
        ("page_text", "ID", "item_template", "PageText",
         f"entry IN ({','.join(sorted(page_items)) or '0'}) AND PageText<>0", "NextPageID"),
    ):
        if selected not in {name for name, _ in columns(container, user, password, target, owner_table)}:
            continue
        pending = fetch(container, user, password, f"SELECT {selected} FROM {target}.{owner_table} WHERE {where}")
        report[table] = import_missing_rows(container, user, password, target, source, table, key,
                                            pending, dry_run, next_column)


    if not dry_run:
        run(container, user, password, f"DROP TABLE IF EXISTS {target}.{map_table}")

    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--container", default="plan175-inventory-mysql")
    parser.add_argument("--user", default="root")
    parser.add_argument("--password", default="plan175ro")
    parser.add_argument("--source", default="tdb_world")
    parser.add_argument("--target", default="acore_world")
    parser.add_argument("--zone", type=int, default=12)
    parser.add_argument("--quest-sorts", default="",
                        help="comma-separated quest sort ids for the zone (the zone id plus its subzones); defaults "
                             "to --zone")
    parser.add_argument("--map", type=int, default=0)
    parser.add_argument("--dbc-dir", help="directory with the client DBCs (SpellEffect.dbc for summon closure)")
    parser.add_argument("--skip-quests", default="",
                        help="comma-separated quest ids to leave out, e.g. quests that need items the client data "
                             "lacks or an objective template TDB has no row for (reported as skipped_quests)")
    parser.add_argument("--baseline", help="schema before the import; dangling relations it already had (native "
                                           "gaps, e.g. a quest objective TDB has no template for) do not fail the gate")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--sync-addon", action="store_true",
                        help="replace quest_template_addon for imported quests from the source; "
                             "run once after all zones")
    parser.add_argument("--drop-items", default="",
                        help="comma-separated item ids the client store lacks; imported loot rows for them are dropped")
    args = parser.parse_args()
    for name in (args.source, args.target, args.baseline or "x"):  # spliced into SQL unquoted
        if not re.fullmatch(r"\w+", name):
            parser.error(f"invalid schema name: {name!r}")
    c, u, p, src, dst = args.container, args.user, args.password, args.source, args.target

    if args.sync_addon:
        if not args.apply:
            parser.error("--sync-addon requires --apply")
        if src == dst:
            parser.error("--source and --target must differ")
        # The base schema's 3.3.5a addon rows survive the per-zone INSERT IGNORE; replace every row for an
        # imported quest so chains, gates and exclusive groups are the 4.3.4 ones (#311).
        common = common_columns(c, u, p, dst, src, "quest_template_addon")
        cols = ",".join(quote(name) for name in common)
        selected = ",".join(f"s.{quote(name)}" for name in common)
        run(c, u, p, f"DELETE FROM {dst}.quest_template_addon")
        count = insert_count(c, u, p,
                             f"INSERT INTO {dst}.quest_template_addon ({cols}) "
                             f"SELECT {selected} FROM {src}.quest_template_addon s "
                             f"JOIN {dst}.quest_template q ON q.ID=s.ID")
        # A link into a quest that was not imported makes the loader log and ignore it; clear it.
        cleared = 0
        for column in ("PrevQuestID", "NextQuestID", "BreadcrumbForQuestId"):
            cleared += int(run(
                c, u, p,
                f"UPDATE {dst}.quest_template_addon a SET a.{column}=0 "
                f"WHERE a.{column}<>0 AND NOT EXISTS "
                f"(SELECT 1 FROM {dst}.quest_template q WHERE q.ID=ABS(a.{column})); "
                f"SELECT ROW_COUNT()")[-1][0])
        run(c, u, p,
            f"UPDATE {dst}.quest_template q JOIN {src}.quest_template s ON s.ID=q.ID "
            f"JOIN {dst}.quest_template n ON n.ID=s.RewardNextQuest "
            f"SET q.RewardNextQuest=s.RewardNextQuest "
            f"WHERE q.RewardNextQuest=0 AND s.RewardNextQuest<>0")
        print(json.dumps({"quest_template_addon": count, "cleared_links": cleared}, indent=2))
        return 0

    existing = {r[0] for r in run(c, u, p, f"SELECT ID FROM {dst}.quest_template") if r}
    try:
        sorts = {args.zone} | ({int(value) for value in args.quest_sorts.split(",") if value}
                               if args.quest_sorts else set())
    except ValueError:
        parser.error("--quest-sorts takes comma-separated quest sort ids")
    sort_ids = ",".join(str(value) for value in sorted(sorts))
    candidates = zone_quest_candidates(c, u, p, src, sorted(sorts), args.zone, args.map)
    skipped = set(args.skip_quests.split(",")) - {""}
    if not all(q.isdigit() for q in skipped):
        parser.error("--skip-quests takes comma-separated quest ids")
    drop_items = args.drop_items.split(",") if args.drop_items else []
    if not all(item.isdigit() for item in drop_items):
        parser.error("--drop-items takes comma-separated item ids")
    missing = [q for q in candidates if q not in existing and q not in skipped]
    report = {"zone": args.zone, "candidates": len(candidates), "missing": len(missing), "applied": {}, "sql": {}}
    if skipped:
        report["skipped_quests"] = sorted(skipped & set(candidates), key=int)

    tables = [("quest_template", "ID")]
    for table, key in (("quest_offer_reward", "ID"), ("quest_request_items", "ID"),
                       ("quest_template_addon", "ID"),
                       ("creature_queststarter", "quest"), ("creature_questender", "quest"),
                       ("gameobject_queststarter", "quest"), ("gameobject_questender", "quest")):
        if table_exists(c, u, p, src, table):
            tables.append((table, key))
    for table, key in tables:
        # Check each table on its own so a run interrupted between two tables is repaired on rerun.
        present = (existing if table == "quest_template" else
                   {row[0] for row in run(c, u, p, f"SELECT DISTINCT {key} FROM {dst}.{table}") if row})
        ids = [q for q in candidates if q not in present and q not in skipped]
        if not ids:
            continue
        count, sql = import_table(c, u, p, dst, src, table, key, ids, not args.apply)
        report["applied"][table] = count
        report["sql"][table] = sql

    if args.apply and missing:
        race_ids = ",".join(missing)
        rows = run(c, u, p,
                   f"UPDATE {dst}.quest_template q JOIN {src}.quest_template_addon a ON a.ID=q.ID "
                   f"SET q.AllowableRaces=IF(q.AllowableRaces=0, a.AllowableRaces, q.AllowableRaces), "
                   f"q.TimeAllowed=IF(q.TimeAllowed=0, a.TimeAllowed, q.TimeAllowed) "
                   f"WHERE q.ID IN ({race_ids}) AND ((q.AllowableRaces=0 AND a.AllowableRaces<>0) "
                   f"OR (q.TimeAllowed=0 AND a.TimeAllowed<>0)); "
                   f"SELECT ROW_COUNT()")
        report["race_time_rows"] = int(rows[-1][0]) if rows else 0

        # Chain links into quests outside the target (class-quest chains of other landings) would make the
        # loader log an error and ignore the link anyway; clear them and report what was cleared.
        cleared = []
        for column in ("PrevQuestID", "NextQuestID", "BreadcrumbForQuestId"):
            dangling = (f"a.ID IN ({race_ids}) AND a.{column}<>0 AND NOT EXISTS "
                        f"(SELECT 1 FROM {dst}.quest_template q WHERE q.ID=ABS(a.{column}))")
            cleared += [f"{row[0]}.{column}={row[1]}" for row in run(
                c, u, p, f"SELECT a.ID, a.{column} FROM {dst}.quest_template_addon a WHERE {dangling}")]
            run(c, u, p, f"UPDATE {dst}.quest_template_addon a SET a.{column}=0 WHERE {dangling}")
        dangling = (f"q.ID IN ({race_ids}) AND q.RewardNextQuest<>0 AND NOT EXISTS "
                    f"(SELECT 1 FROM {dst}.quest_template n WHERE n.ID=q.RewardNextQuest)")
        cleared += [f"{row[0]}.RewardNextQuest={row[1]}" for row in run(
            c, u, p, f"SELECT q.ID, q.RewardNextQuest FROM {dst}.quest_template q WHERE {dangling}")]
        run(c, u, p, f"UPDATE {dst}.quest_template q LEFT JOIN {dst}.quest_template n ON n.ID=q.RewardNextQuest "
                     f"SET q.RewardNextQuest=0 WHERE q.ID IN ({race_ids}) AND q.RewardNextQuest<>0 AND n.ID IS NULL")
        # RewardNextQuest is a client hint; register it as a real alternative prerequisite when the next
        # quest already has one and does not point back here. An open target stays open, and a breadcrumb
        # never gets NextQuestID (the loader rejects that pairing).
        mirror = run(c, u, p,
                     f"SELECT a.ID, q.RewardNextQuest FROM {dst}.quest_template_addon a "
                     f"JOIN {dst}.quest_template q ON q.ID=a.ID "
                     f"LEFT JOIN {dst}.quest_template_addon n ON n.ID=q.RewardNextQuest "
                     f"WHERE q.ID IN ({race_ids}) AND q.RewardNextQuest<>0 AND a.NextQuestID=0 "
                     f"AND a.BreadcrumbForQuestId=0 AND COALESCE(n.PrevQuestID,0)<>q.ID "
                     f"AND (n.PrevQuestID<>0 OR EXISTS (SELECT 1 FROM {dst}.quest_template_addon p "
                     f"WHERE p.NextQuestID=q.RewardNextQuest))")
        for quest, next_quest in mirror:
            run(c, u, p, f"UPDATE {dst}.quest_template_addon SET NextQuestID={int(next_quest)} "
                         f"WHERE ID={int(quest)} AND NextQuestID=0")
        if mirror:
            report["next_quest_rows"] = len(mirror)
        if cleared:
            report["cleared_quest_links"] = cleared

    zone_quests = [r[0] for r in run(c, u, p,
                                       f"SELECT ID FROM {dst}.quest_template WHERE QuestSortID IN ({sort_ids})")]
    closure_quests = sorted(set(zone_quests) | set(missing))
    if closure_quests:
        closure_ids = ",".join(closure_quests)
        creatures = {r[0] for r in run(c, u, p, f"SELECT entry FROM {dst}.creature_template") if r}
        gos = {r[0] for r in run(c, u, p, f"SELECT entry FROM {dst}.gameobject_template") if r}
        items = {r[0] for r in run(c, u, p, f"SELECT entry FROM {dst}.item_template") if r}
        closure = {"missing_creature_templates": set(), "missing_gameobject_templates": set(),
                   "missing_items": set(), "missing_starter_creatures": set()}
        select_columns = (f"{','.join(f'RequiredNpcOrGo{i}' for i in range(1,5))},"
                          f"{','.join(f'RequiredItemId{i}' for i in range(1,7))},"
                          f"{','.join(f'RewardItem{i}' for i in range(1,5))},"
                          f"{','.join(f'RewardChoiceItemID{i}' for i in range(1,7))},StartItem,"
                          f"{','.join(f'ItemDrop{i}' for i in range(1,5))}")
        rows = []
        if missing:
            rows += run(c, u, p, f"SELECT {select_columns} FROM {src}.quest_template "
                                 f"WHERE ID IN ({','.join(missing)})")
        if zone_quests:
            rows += run(c, u, p, f"SELECT {select_columns} FROM {dst}.quest_template "
                                 f"WHERE ID IN ({','.join(zone_quests)})")
        for row in rows:
            for value in row[:4]:
                if value and int(value) > 0 and value not in creatures:
                    closure["missing_creature_templates"].add(value)
                elif value and int(value) < 0 and str(-int(value)) not in gos:
                    closure["missing_gameobject_templates"].add(str(-int(value)))
            for value in row[4:]:
                if value and int(value) > 0 and value not in items:
                    closure["missing_items"].add(value)
        for table in ("creature_queststarter", "creature_questender"):
            for entry, in run(c, u, p, f"SELECT id FROM {src}.{table} WHERE quest IN ({closure_ids})"):
                if entry and entry not in creatures:
                    closure["missing_starter_creatures"].add(entry)
        report["closure"] = {k: sorted(v) for k, v in closure.items()}
        report["imported_closure"] = import_closure(c, u, p, dst, src, args.zone, args.map,
                                                    closure_quests, not args.apply, args.dbc_dir, drop_items)
        report["closure"]["missing_items"] = sorted(set(report["closure"]["missing_items"]) |
                                                   set(report["imported_closure"].get("missing_items", [])))

    status = 0
    if WARNINGS:
        report["insert_warnings"] = sorted(set(WARNINGS))[:20]
    if args.apply:
        # Gate: the imported zone must pass the #202 relation checker. Item
        # references stay open (TDB 434 has no item data) and are reported only.
        problems = check_relations(c, u, p, dst, args.zone, args.map, sorted(sorts), candidates,
                                  dbc_dir=args.dbc_dir)
        item_kinds = ("quest_item", "loot_item")
        report["unresolved_items"] = [x for x in problems if x["kind"] in item_kinds]
        if "closure" in report:
            report["closure"]["missing_items"] = sorted(set(report["closure"]["missing_items"]) |
                                                       {x["id"] for x in report["unresolved_items"]})
        report["dangling"] = [x for x in problems if x["kind"] not in item_kinds]
        if args.baseline:
            native = {json.dumps(x, sort_keys=True)
                      for x in check_relations(c, u, p, args.baseline, args.zone, args.map, sorted(sorts), candidates,
                                                    dbc_dir=args.dbc_dir)}
            report["preexisting_dangling"] = [x for x in report["dangling"]
                                              if json.dumps(x, sort_keys=True) in native]
            report["dangling"] = [x for x in report["dangling"] if x not in report["preexisting_dangling"]]
        # 2 means "imported, dangling loader relations reported"; 1 stays for a failed run.
        status = 2 if report["dangling"] else 0
    else:
        report["mode"] = "dry-run"
    print(json.dumps(report, indent=2, default=str))
    return status


if __name__ == "__main__":
    sys.exit(main())
