#!/usr/bin/env python3
"""TDB 434 -> AC starter-content import pipeline (#203).

For one zone, imports the missing quests (quest_template plus reward, request
and addon companion rows), then the templates, spawns, loot and SmartAI their
objectives, required-item suppliers and givers reference, including display
models, creature text, waypoint paths and condition reference templates.
AC-native rows win: existing loot, script, text and path blocks are not merged,
and existing spawns at the same position are not duplicated. Remaining item
references are reported; TDB 434 carries no item data, so those come from the
client Item.db2/Item-sparse.db2 conversion instead.

Dry-run by default; --apply performs the inserts. Run only against the
run-owned disposable database.
"""

import argparse
import json
import subprocess
import sys


def run(container, user, password, sql):
    result = subprocess.run(
        ["docker", "exec", container, "mysql", f"-u{user}", f"-p{password}", "-N", "-B", "-e", sql],
        capture_output=True, text=True, check=False,
    )
    if result.returncode:
        raise RuntimeError(f"query failed: {sql}\n{result.stderr.strip()}")
    return [line.split("\t") for line in result.stdout.splitlines() if line]


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


def quote(name):
    return f"`{name}`"


def import_table(container, user, password, target, source, table, where_col, ids, dry_run):
    common = common_columns(container, user, password, target, source, table)
    if not common:
        return 0, f"-- no common columns for {table}"
    id_list = ",".join(str(i) for i in ids)
    cols = ",".join(quote(c) for c in common)
    sql = (
        f"INSERT IGNORE INTO {quote(target)}.{quote(table)} ({cols}) "
        f"SELECT {cols} FROM {quote(source)}.{quote(table)} WHERE {quote(where_col)} IN ({id_list})"
    )
    if dry_run:
        return 0, sql
    rows = run(container, user, password, sql + "; SELECT ROW_COUNT()")
    return (int(rows[-1][0]) if rows else 0), sql


GUID_OFFSET = 10_000_000


def fetch(container, user, password, sql):
    return [r[0] for r in run(container, user, password, sql) if r]


def import_closure(container, user, password, target, source, zone, map_id, quests, dry_run):
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
            f"JOIN {source}.creature_loot_template lt ON lt.Reference = rt.Entry "
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
            f"JOIN {source}.gameobject_loot_template lt ON lt.Reference = rt.Entry "
            f"JOIN {source}.gameobject_template gt ON gt.Data1 = lt.Entry AND gt.type IN (3,25) "
            f"JOIN {source}.gameobject g ON g.id = gt.entry AND g.map={map_id} "
            f"AND (g.zoneId={zone} OR g.areaId={zone}) WHERE rt.Item IN ({items})"))

    creature_ids = ",".join(sorted(creature_entries)) or "0"
    gameobject_ids = ",".join(sorted(gameobject_entries)) or "0"
    known_creatures = set(fetch(container, user, password, f"SELECT entry FROM {target}.creature_template"))
    known_gameobjects = set(fetch(container, user, password, f"SELECT entry FROM {target}.gameobject_template"))
    new_creatures = sorted(creature_entries - known_creatures)
    new_gameobjects = sorted(gameobject_entries - known_gameobjects)
    report["new_templates"] = {"creature_template": len(new_creatures),
                               "gameobject_template": len(new_gameobjects)}
    for table, entries in (("creature_template", new_creatures),
                           ("gameobject_template", new_gameobjects)):
        if entries:
            report[table], _ = import_table(container, user, password, target, source, table,
                                            "entry", entries, dry_run)

    model_columns = [c for c in ("modelid1", "modelid2", "modelid3", "modelid4")
                     if c in {name for name, _ in columns(container, user, password,
                                                          source, "creature_template")}]
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
                    values.append(f"({row[0]},{idx},{model},1,1,{row[-1] or 0})")
        if values:
            if not dry_run:
                run(container, user, password,
                    f"INSERT IGNORE INTO {target}.creature_template_model "
                    f"(CreatureID,Idx,CreatureDisplayID,DisplayScale,Probability,VerifiedBuild) "
                    f"VALUES {','.join(values)}")
            report["creature_template_model"] = len(values)

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
               f"WHERE s.{id_column} IN ({ids}) AND s.map={map_id} "
               f"AND (s.zoneId={zone} OR s.areaId={zone}) "
               f"AND NOT EXISTS (SELECT 1 FROM {target}.{table} t WHERE t.guid=s.guid+{GUID_OFFSET}) "
               f"AND NOT EXISTS (SELECT 1 FROM {target}.{table} t WHERE t.{id_column}=s.{id_column} "
               f"AND t.map=s.map AND t.position_x=s.position_x AND t.position_y=s.position_y)")
        if dry_run:
            return 0, sql
        rows = run(container, user, password, sql + "; SELECT ROW_COUNT()")
        return int(rows[-1][0]) if rows else 0, sql

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
               f"WHERE s.id IN ({ids}) AND s.map={map_id} AND (s.zoneId={zone} OR s.areaId={zone}) "
               f"AND EXISTS (SELECT 1 FROM {target}.{spawn_table} t WHERE t.guid=a.guid+{GUID_OFFSET})")
        if not dry_run:
            run(container, user, password, sql)

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

    # Movement paths referenced by creature and template addons. Colliding path
    # ids are reallocated above the target max and the addon rows are updated.
    path_targets: dict[str, list[tuple[str, str]]] = {}
    if creature_entries and table_exists(container, user, password, source, "creature_addon"):
        for guid, path_id in run(container, user, password,
                                 f"SELECT ca.guid, ca.waypointPathId FROM {source}.creature_addon ca "
                                 f"JOIN {source}.creature c ON c.guid=ca.guid "
                                 f"WHERE c.id IN ({creature_ids}) AND c.map={map_id} "
                                 f"AND (c.zoneId={zone} OR c.areaId={zone}) AND ca.waypointPathId<>0 "
                                 f"AND EXISTS (SELECT 1 FROM {target}.creature_addon ta "
                                 f"WHERE ta.guid=ca.guid+{GUID_OFFSET})"):
            path_targets.setdefault(path_id, []).append(
                ("creature_addon", str(int(guid) + GUID_OFFSET)))
    if (creature_entries
            and table_exists(container, user, password, source, "creature_template_addon")):
        template_addons = set(fetch(container, user, password,
                                    f"SELECT DISTINCT entry FROM {target}.creature_template_addon "
                                    f"WHERE entry IN ({creature_ids})"))
        for entry, path_id in run(container, user, password,
                                  f"SELECT entry, waypointPathId FROM {source}.creature_template_addon "
                                  f"WHERE entry IN ({creature_ids}) AND waypointPathId<>0"):
            if entry in template_addons:
                path_targets.setdefault(path_id, []).append(("creature_template_addon", entry))
    if path_targets and table_exists(container, user, password, source, "waypoint_data"):
        waypoint_columns = [c for c in common_columns(container, user, password, target, source,
                                                      "waypoint_data") if c != "id"]
        names = ",".join(quote(c) for c in waypoint_columns)
        imported_paths = 0
        for path_id, consumers in sorted(path_targets.items()):
            satisfied = True
            for consumer_table, key in consumers:
                key_column = "guid" if consumer_table == "creature_addon" else "entry"
                rows = run(container, user, password,
                           f"SELECT path_id FROM {target}.{consumer_table} "
                           f"WHERE {key_column}={key} LIMIT 1")
                current = rows[0][0] if rows else "0"
                if (not current or not current.isdigit() or int(current) == 0
                        or scalar(container, user, password,
                                  f"SELECT COUNT(*) FROM {target}.waypoint_data WHERE id={current}") == 0):
                    satisfied = False
                    break
            if satisfied:
                continue
            target_path = str(scalar(container, user, password,
                                     f"SELECT IFNULL(MAX(id),0)+1 FROM {target}.waypoint_data"))
            if not dry_run:
                grouped: dict[str, list[str]] = {}
                for consumer_table, key in consumers:
                    grouped.setdefault(consumer_table, []).append(key)
                for consumer_table, keys in grouped.items():
                    key_column = "guid" if consumer_table == "creature_addon" else "entry"
                    run(container, user, password,
                        f"UPDATE {target}.{consumer_table} SET path_id={target_path} "
                        f"WHERE {key_column} IN ({','.join(keys)})")
                run(container, user, password,
                    f"INSERT IGNORE INTO {target}.waypoint_data (`id`,{names}) "
                    f"SELECT {target_path},{names} FROM {source}.waypoint_data WHERE id={path_id}")
            imported_paths += 1
        report["waypoint_data"] = imported_paths

    # Source GUID -> target GUID, for scripts that target a specific spawn.
    guid_map: list[tuple[int, int, int]] = []
    guid_owner: dict[str, str] = {}
    source_guids: list[str] = []
    if creature_entries:
        source_spawns = run(container, user, password,
                            f"SELECT guid, id, position_x, position_y FROM {source}.creature "
                            f"WHERE id IN ({creature_ids}) AND map={map_id} "
                            f"AND (zoneId={zone} OR areaId={zone})")
        for guid, entry, position_x, position_y in source_spawns:
            guid_owner[guid] = entry
            imported = fetch(container, user, password,
                             f"SELECT guid FROM {target}.creature WHERE guid={int(guid) + GUID_OFFSET}")
            if imported:
                source_guids.append(guid)
                guid_map.append((0, int(guid), int(imported[0])))
            else:
                native = run(container, user, password,
                             f"SELECT guid FROM {target}.creature WHERE id={entry} AND map={map_id} "
                             f"AND position_x={position_x} AND position_y={position_y} LIMIT 1")
                if native:
                    guid_map.append((0, int(guid), int(native[0][0])))
    source_go_guids = fetch(container, user, password,
                            f"SELECT guid FROM {source}.gameobject WHERE id IN ({gameobject_ids}) "
                            f"AND map={map_id} AND (zoneId={zone} OR areaId={zone})")
    if gameobject_entries:
        source_go_spawns = run(container, user, password,
                               f"SELECT guid, id, position_x, position_y FROM {source}.gameobject "
                               f"WHERE id IN ({gameobject_ids}) AND map={map_id} "
                               f"AND (zoneId={zone} OR areaId={zone})")
        for guid, entry, position_x, position_y in source_go_spawns:
            imported = fetch(container, user, password,
                             f"SELECT guid FROM {target}.gameobject WHERE guid={int(guid) + GUID_OFFSET}")
            if imported:
                guid_map.append((1, int(guid), int(imported[0])))
            else:
                native = run(container, user, password,
                             f"SELECT guid FROM {target}.gameobject WHERE id={entry} AND map={map_id} "
                             f"AND position_x={position_x} AND position_y={position_y} LIMIT 1")
                if native:
                    guid_map.append((1, int(guid), int(native[0][0])))

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

    loot_ids = sorted({r[1] for r in run(container, user, password,
                                         f"SELECT entry, lootid FROM {source}.creature_template "
                                         f"WHERE entry IN ({creature_ids}) AND lootid<>0")})
    gameobject_loot = sorted({r[0] for r in run(container, user, password,
                                                f"SELECT Data1 FROM {source}.gameobject_template "
                                                f"WHERE entry IN ({gameobject_ids}) AND type IN (3,25) "
                                                f"AND Data1<>0")})
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
    if gameobject_loot:
        report["gameobject_loot_template"], _ = import_table(container, user, password, target, source,
                                                            "gameobject_loot_template", "Entry",
                                                            gameobject_loot, dry_run)

    refs: set[str] = set()
    if loot_ids:
        refs.update(fetch(container, user, password,
                          f"SELECT DISTINCT Reference FROM {source}.creature_loot_template "
                          f"WHERE Entry IN ({','.join(loot_ids)}) AND Reference<>0"))
    if gameobject_loot:
        refs.update(fetch(container, user, password,
                          f"SELECT DISTINCT Reference FROM {source}.gameobject_loot_template "
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
                          f"SELECT DISTINCT Reference FROM {source}.reference_loot_template "
                          f"WHERE Entry IN ({','.join(new)}) AND Reference<>0"))
    if inserted_refs:
        report["reference_loot_template"] = len(inserted_refs)

    # AC-native scripts win: only fill scripts the target does not have. Only
    # entries that actually carry a source script are candidates, so an orphaned
    # condition cannot be imported for an entry with no script.
    script_filters: list[tuple[str, int]] = []
    source_creature_scripts = set(fetch(container, user, password,
                                        f"SELECT DISTINCT entryorguid FROM {source}.smart_scripts "
                                        f"WHERE source_type=0 AND entryorguid IN ({creature_ids})"))
    script_creatures = {e for e in creature_entries if e in source_creature_scripts
                        and e not in set(fetch(container, user, password,
                                               f"SELECT DISTINCT entryorguid FROM {target}.smart_scripts "
                                               f"WHERE source_type=0 AND entryorguid IN ({creature_ids})"))}
    if script_creatures:
        script_filters.append((f"source_type=0 AND entryorguid IN "
                               f"({','.join(sorted(script_creatures))})", 0))
    source_gameobject_scripts = set(fetch(container, user, password,
                                          f"SELECT DISTINCT entryorguid FROM {source}.smart_scripts "
                                          f"WHERE source_type=1 AND entryorguid IN ({gameobject_ids})"))
    script_gameobjects = {e for e in gameobject_entries if e in source_gameobject_scripts
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
    if source_go_guids:
        imported = set(fetch(container, user, password,
                             f"SELECT guid-{GUID_OFFSET} FROM {target}.gameobject "
                             f"WHERE guid >= {GUID_OFFSET} AND guid-{GUID_OFFSET} IN "
                             f"({','.join(source_go_guids)})"))
        shifted = ",".join(str(-(int(guid) + GUID_OFFSET)) for guid in imported) or "0"
        present = set(fetch(container, user, password,
                            f"SELECT DISTINCT entryorguid FROM {target}.smart_scripts "
                            f"WHERE source_type=1 AND entryorguid IN ({shifted})"))
        script_go_guids = [guid for guid in imported
                           if str(-(int(guid) + GUID_OFFSET)) not in present]
    if script_go_guids:
        negative = ",".join(str(-int(guid)) for guid in script_go_guids)
        script_filters.append((f"source_type=1 AND entryorguid IN ({negative})", GUID_OFFSET))

    # Discovery covers every source creature script, not only the rows inserted
    # on this run, so text/waypoint dependencies of preserved scripts are still
    # filled when they are missing.
    all_creature_filters: list[tuple[str, int]] = []
    if creature_entries:
        all_creature_filters.append((f"source_type=0 AND entryorguid IN ({creature_ids})", 0))
    if source_guids:
        negative = ",".join(str(-int(guid)) for guid in source_guids)
        all_creature_filters.append((f"source_type=0 AND entryorguid IN ({negative})", GUID_OFFSET))
    all_source_filters = list(all_creature_filters)
    if gameobject_entries:
        all_source_filters.append((f"source_type=1 AND entryorguid IN ({gameobject_ids})", 0))
    if source_go_guids:
        negative = ",".join(str(-int(guid)) for guid in source_go_guids)
        all_source_filters.append((f"source_type=1 AND entryorguid IN ({negative})", GUID_OFFSET))

    def insert_scripts(where, shift):
        columns_ = common_columns(container, user, password, target, source, "smart_scripts")
        names = ",".join(quote(c) for c in columns_)
        selected = []
        for column in columns_:
            if shift and column == "entryorguid":
                selected.append(f"(s.{quote(column)} - {shift})")
            elif column == "target_param1":
                selected.append("CASE WHEN s.target_type=10 THEN IFNULL(mc.dst_guid, s.target_param1) "
                                "WHEN s.target_type=14 THEN IFNULL(mg.dst_guid, s.target_param1) "
                                "ELSE s.target_param1 END")
            else:
                selected.append(f"s.{quote(column)}")
        run(container, user, password,
            f"INSERT IGNORE INTO {target}.smart_scripts ({names}) "
            f"SELECT {','.join(selected)} FROM {source}.smart_scripts s "
            f"LEFT JOIN {target}.{map_table} mc ON mc.kind=0 AND mc.src_guid=s.target_param1 "
            f"LEFT JOIN {target}.{map_table} mg ON mg.kind=1 AND mg.src_guid=s.target_param1 "
            f"WHERE {where}")

    imported_scripts = 0
    for where, shift in script_filters:
        if not dry_run:
            insert_scripts(where, shift)
        imported_scripts += scalar(container, user, password,
                                   f"SELECT COUNT(*) FROM {source}.smart_scripts WHERE {where}")

    if not dry_run:
        scripted_creatures = set(fetch(container, user, password,
                                       f"SELECT DISTINCT entryorguid FROM {target}.smart_scripts "
                                       f"WHERE source_type=0 AND entryorguid IN ({creature_ids})"))
        scripted_gameobjects = set(fetch(container, user, password,
                                         f"SELECT DISTINCT entryorguid FROM {target}.smart_scripts "
                                         f"WHERE source_type=1 AND entryorguid IN ({gameobject_ids})"))
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
    handled_timed: set[str] = set()
    imported_timed: set[str] = set()
    while timed - handled_timed:
        batch = sorted(timed - handled_timed)
        present = set(fetch(container, user, password,
                            f"SELECT DISTINCT entryorguid FROM {target}.smart_scripts "
                            f"WHERE source_type=9 AND entryorguid IN ({','.join(batch)})"))
        new = [entry for entry in batch if entry not in present]
        handled_timed.update(batch)
        if not new or dry_run:
            continue
        insert_scripts(f"source_type=9 AND entryorguid IN ({','.join(new)})", 0)
        imported_timed.update(new)
        nested_owners: set[str] = set()
        for entry in new:
            nested_owners.update(timed_owners.get(entry, set()))
        timed.update(timed_references(f"source_type=9 AND entryorguid IN ({','.join(new)})",
                                      nested_owners))
    if imported_scripts or imported_timed:
        report["smart_scripts"] = imported_scripts
        report["smart_scripts_timed"] = len(imported_timed)

    text_groups: set[tuple[str, str]] = set()
    for where, _shift in all_creature_filters:
        for owner, group in run(container, user, password,
                                f"SELECT entryorguid, action_param1 FROM {source}.smart_scripts "
                                f"WHERE {where} AND action_type=1"):
            mapped_owner = guid_owner.get(owner[1:], "") if owner.startswith("-") else owner
            if mapped_owner:
                text_groups.add((mapped_owner, group))
    for timed_id in sorted(handled_timed):
        for group, in run(container, user, password,
                          f"SELECT action_param1 FROM {source}.smart_scripts "
                          f"WHERE source_type=9 AND entryorguid={timed_id} AND action_type=1"):
            for owner in timed_owners.get(timed_id, set()):
                text_groups.add((owner, group))
    if text_groups:
        creature_list = ",".join(sorted({owner for owner, _group in text_groups}))
        present_text = {(row[0], row[1]) for row in
                        run(container, user, password,
                            f"SELECT DISTINCT CreatureID, GroupID FROM {target}.creature_text "
                            f"WHERE CreatureID IN ({creature_list})")}
        missing_text = [(owner, group) for owner, group in text_groups
                        if (owner, group) not in present_text]
        if missing_text and not dry_run:
            where = " OR ".join(f"(CreatureID={owner} AND GroupID={group})"
                                for owner, group in sorted(missing_text))
            columns_ = common_columns(container, user, password, target, source, "creature_text")
            names = ",".join(quote(c) for c in columns_)
            run(container, user, password,
                f"INSERT IGNORE INTO {target}.creature_text ({names}) "
                f"SELECT {names} FROM {source}.creature_text WHERE {where}")
        report["creature_text_groups"] = len(missing_text)

    path_ids: set[str] = set()
    for where, _shift in all_creature_filters:
        path_ids.update(fetch(container, user, password,
                              f"SELECT DISTINCT action_param2 FROM {source}.smart_scripts "
                              f"WHERE {where} AND action_type=53 AND action_param2<>0"))
    for timed_id in sorted(handled_timed):
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
                                "IFNULL(mg3.dst_guid, c.ConditionValue3)) "
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
                f"WHERE {where}")
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
            f"WHERE SourceTypeOrReferenceId IN ({negative_ids})")
        imported_condition_refs += len(new)
        referenced_conditions.update(
            fetch(container, user, password,
                  f"SELECT DISTINCT -ConditionTypeOrReference FROM {source}.conditions "
                  f"WHERE SourceTypeOrReferenceId IN ({negative_ids}) AND ConditionTypeOrReference<0"))
    if imported_condition_refs:
        report["condition_reference_templates"] = imported_condition_refs

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
    parser.add_argument("--map", type=int, default=0)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    c, u, p, src, dst = args.container, args.user, args.password, args.source, args.target

    existing = {r[0] for r in run(c, u, p, f"SELECT ID FROM {dst}.quest_template") if r}
    candidates = [r[0] for r in run(c, u, p,
                  f"SELECT ID FROM {src}.quest_template WHERE QuestSortID={args.zone} ORDER BY ID") if r]
    missing = [q for q in candidates if q not in existing]
    report = {"zone": args.zone, "candidates": len(candidates), "missing": len(missing), "applied": {}, "sql": {}}

    tables = [("quest_template", "ID", missing)]
    for table, key in (("quest_offer_reward", "ID"), ("quest_request_items", "ID"),
                       ("quest_template_addon", "ID"),
                       ("creature_queststarter", "quest"), ("creature_questender", "quest"),
                       ("gameobject_queststarter", "quest"), ("gameobject_questender", "quest")):
        if table_exists(c, u, p, src, table):
            tables.append((table, key, candidates))
    for table, key, ids in tables:
        if not ids:
            continue
        count, sql = import_table(c, u, p, dst, src, table, key, ids, not args.apply)
        report["applied"][table] = count
        report["sql"][table] = sql

    if args.apply and candidates:
        race_ids = ",".join(candidates)
        rows = run(c, u, p,
                   f"UPDATE {dst}.quest_template q JOIN {src}.quest_template_addon a ON a.ID=q.ID "
                   f"SET q.AllowableRaces=a.AllowableRaces, q.TimeAllowed=a.TimeAllowed "
                   f"WHERE q.ID IN ({race_ids}) AND q.AllowableRaces=0 AND a.AllowableRaces<>0; "
                   f"SELECT ROW_COUNT()")
        report["race_time_rows"] = int(rows[-1][0]) if rows else 0

    zone_quests = [r[0] for r in run(c, u, p,
                                       f"SELECT ID FROM {dst}.quest_template WHERE QuestSortID={args.zone}")]
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
                          f"{','.join(f'RewardChoiceItemID{i}' for i in range(1,7))}")
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
                                                    closure_quests, not args.apply)

    if not args.apply:
        report["mode"] = "dry-run"
    print(json.dumps(report, indent=2, default=str))


if __name__ == "__main__":
    sys.exit(main())
