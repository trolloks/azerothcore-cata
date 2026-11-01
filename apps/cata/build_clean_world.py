#!/usr/bin/env python3
"""Build the clean Cataclysm world database from TDB 434 and AC support data (#274).

Each phase copies the columns the pinned TDB schema and the AC schema share (INSERT IGNORE ...
SELECT), renames the columns the two schemas spell differently, and derives the tables AC splits
out of TDB's flat creature_template. Phases are re-runnable; the target schema must already exist
and hold the AC support data that is not world content (*_dbc, acore_string, player stats, spell
overrides).

Templates come first: worldserver's Wintergrasp setup dereferences a gameobject template, and every
spawn, quest and loot phase refers to creature and gameobject templates.
"""

import argparse
import os
from pathlib import Path
import re
import struct
import sys

from import_starter_content import (SMART_EVENT_PARAM_COUNTS, UNSUPPORTED_SMART_ACTIONS, WARNINGS,
                                    columns, insert_count, run)
from starter_content_inventory import registered_script_names, table_exists

GUID_OFFSET = 10_000_000
# Loot ids stay below 200k in TDB; allocated quest-only templates live in an unused range.
QUEST_LOOT_ID_BASE = 900_000_000
# TDB keeps a few columns under a different name; the rest of AC's spawn tables match.
RENAMES = {
    "broadcast_text": {"MaleText": "Text", "FemaleText": "Text1", "SoundEntriesId": "SoundEntriesID"},
    "vehicle_seat_addon": {"SeatOrientation": "SeatOffsetO"},
    "creature": {"wander_distance": "spawndist"},
    "creature_addon": {"path_id": "waypointPathId"},
    "creature_template_addon": {"path_id": "waypointPathId"},
    "creature_formations": {"leaderGUID": "LeaderGUID", "memberGUID": "MemberGUID",
                            "angle": "FollowAngle", "dist": "FollowDistance"},
}
# TDB writes "no formation waypoint" as -1; AC's unsigned columns use 0.
# TDB keeps a spawn's phase id and phase group in separate columns; the target has both too
# (PhaseId is always a Phase.dbc id, PhaseGroup a PhaseXPhaseGroup.dbc id, so id collisions such as
# 367 keep their identity). See #348.
EXPRESSIONS = {("creature_formations", "point_1"): "GREATEST(`InversionPoint1`,0)",
               ("creature_formations", "point_2"): "GREATEST(`InversionPoint2`,0)",
               # TDB 434 type 26 is a 4.3.4 phase/group id; AC reserves 26 for the legacy mask.
               ("conditions", "ConditionTypeOrReference"):
                   "IF(`ConditionTypeOrReference`=26,108,`ConditionTypeOrReference`)"}
SPAWN_TABLES = ("creature", "gameobject", "creature_addon", "gameobject_addon",
                "creature_formations", "waypoint_data", "waypoint_scripts", "creature_text",
                "game_event_creature", "game_event_gameobject")
SPAWN_GUID_COLUMNS = {"creature": {"guid"}, "gameobject": {"guid"}, "creature_addon": {"guid"},
                      "gameobject_addon": {"guid"}, "game_event_creature": {"guid"},
                      "game_event_gameobject": {"guid"},
                      "creature_formations": {"leaderGUID", "memberGUID"}}


def copy_table(container, user, password, target, source, table, renamed_columns=None, shifted=(),
               replace=False, source_table=None, where=None):
    """Copy the columns both schemas share, shifting spawn guids into the imported range."""
    if replace:
        run(container, user, password, f"DELETE FROM `{target}`.`{table}`")
    renames = renamed_columns or {}
    target_columns = columns(container, user, password, target, table)
    source_table = source_table or table
    source_columns = {name for name, _ in columns(container, user, password, source, source_table)}
    names, selected = [], []
    for name, auto in target_columns:
        expression = EXPRESSIONS.get((table, name))
        source_name = renames.get(name, name)
        # A guid we shift is written explicitly even though the target column is auto-increment.
        if (auto and name not in shifted) or (not expression and source_name not in source_columns):
            continue
        names.append(f"`{name}`")
        selected.append(expression or f"`{source_name}`" + (f"+{GUID_OFFSET}" if name in shifted else ""))
    if not names:
        return 0
    sql = (f"INSERT IGNORE INTO `{target}`.`{table}` ({','.join(names)}) "
           f"SELECT {','.join(selected)} FROM `{source}`.`{source_table}`")
    if where:
        sql += f" WHERE {where}"
    return insert_count(container, user, password, sql)


def phase_group_only(dbc_dir):
    """Phase ids that are only phase groups (PhaseXPhaseGroup minus Phase.dbc), from the client DBCs."""
    if not dbc_dir:
        return set()
    base = Path(dbc_dir)

    def rows(name):
        data = (base / name).read_bytes()
        if data[:4] != b"WDBC":
            raise SystemExit(f"{name} in {dbc_dir} is not a WDBC file")
        records, fields, record_size, _ = struct.unpack_from("<4I", data, 4)
        return [struct.unpack_from(f"<{fields}I", data, 20 + i * record_size) for i in range(records)]

    phases = {row[0] for row in rows("Phase.dbc")}
    groups = {row[2] for row in rows("PhaseXPhaseGroup.dbc")}
    return groups - phases


def load_world_safe_locs(dbc_dir):
    """Read the client WorldSafeLocs.dbc rows used by game_graveyard."""
    data = (Path(dbc_dir) / "WorldSafeLocs.dbc").read_bytes()
    if data[:4] != b"WDBC":
        raise SystemExit(f"WorldSafeLocs.dbc in {dbc_dir} is not a WDBC file")
    records, fields, record_size, string_size = struct.unpack_from("<4I", data, 4)
    string_start = 20 + records * record_size
    strings = data[string_start:string_start + string_size]

    def string_at(offset):
        end = strings.find(b"\0", offset)
        return strings[offset:end].decode("utf-8", "replace") if 0 <= offset < len(strings) else ""

    rows = []
    for i in range(records):
        entry, map_id, x, y, z, name_offset = struct.unpack_from(
            "<IIfffI", data, 20 + i * record_size)
        rows.append((entry, map_id, x, y, z, string_at(name_offset)))
    return rows


def build_graveyards(container, user, password, target, source, dbc_dir):
    """Copy TDB zone links and add the Cata safe locations missing from the old TDB list."""
    applied = {"graveyard_zone": copy_table(
        container, user, password, target, source, "graveyard_zone", replace=True)}
    links = {int(row[0]) for row in run(
        container, user, password, f"SELECT DISTINCT ID FROM `{target}`.graveyard_zone")}
    existing = {int(row[0]) for row in run(
        container, user, password, f"SELECT ID FROM `{target}`.game_graveyard")}

    def quote(value):
        return "'" + value.replace("\\", "\\\\").replace("'", "''") + "'"

    values = []
    for entry, map_id, x, y, z, comment in load_world_safe_locs(dbc_dir):
        if entry in existing or (entry not in links and map_id != 654):
            continue
        values.append(f"({entry},{map_id},{x:.8f},{y:.8f},{z:.8f},{quote(comment)})")
    if values:
        applied["game_graveyard"] = insert_count(
            container, user, password,
            f"INSERT IGNORE INTO `{target}`.game_graveyard "
            f"(`ID`,`Map`,`x`,`y`,`z`,`Comment`) VALUES " + ",".join(values))
    else:
        applied["game_graveyard"] = 0
    return applied


def copy_smart_scripts(container, user, password, target, source):
    """Shift spawn-script guids and drop the rows AC's loader would reject."""
    target_columns = columns(container, user, password, target, "smart_scripts")
    source_columns = {name for name, _ in columns(container, user, password, source, "smart_scripts")}
    names, selected = [], []
    for name, auto in target_columns:
        if auto or name not in source_columns:
            continue
        names.append(f"`{name}`")
        if name == "entryorguid":
            selected.append(f"CASE WHEN s.source_type IN (0,1) AND s.entryorguid<0 "
                            f"THEN s.entryorguid-{GUID_OFFSET} ELSE s.entryorguid END")
        elif name == "target_param1":
            selected.append(f"CASE WHEN s.target_type IN (0,1,2) THEN 0 "
                            f"WHEN s.target_type IN (10,14) AND s.target_param1>0 "
                            f"THEN s.target_param1+{GUID_OFFSET} ELSE s.target_param1 END")
        elif name in ("target_param2", "target_param3"):
            selected.append(f"IF(s.target_type IN (0,1,2),0,s.`{name}`)")
        elif name == "action_param3":
            selected.append("IF(s.action_type=1 AND s.action_param3<>0,1,s.action_param3)")
        elif name == "action_param1":
            # TC's WP_START param1 is a run boolean; AC's ESCORT_START param1 is FORCED_MOVEMENT
            # (0 none, 1 walk, 2 run). Shift 0/1 so TC's walk stays walk (#331).
            selected.append("IF(s.action_type=53 AND s.action_param1 IN (0,1),"
                            "s.action_param1+1,s.action_param1)")
        elif name == "action_type":
            selected.append("IF(s.source_type=9 AND s.action_type=85 AND s.target_type=7,134,s.action_type)")
        elif name.startswith("event_param") and name[11:].isdigit():
            unused = ",".join(str(event) for count, events in SMART_EVENT_PARAM_COUNTS.items()
                              if count < int(name[11:]) for event in events)
            selected.append(f"IF(s.source_type<>9 AND s.event_type IN ({unused}),0,s.`{name}`)")
        else:
            selected.append(f"s.`{name}`")
    where = (
        f"(s.action_type NOT IN {UNSUPPORTED_SMART_ACTIONS} "
        f"OR (s.source_type=9 AND s.action_type=85 AND s.target_type=7)) "
        f"AND NOT (s.source_type=0 AND s.entryorguid>=0 AND NOT EXISTS "
        f"(SELECT 1 FROM `{source}`.creature_template t WHERE t.entry=s.entryorguid)) "
        f"AND NOT (s.source_type=1 AND s.entryorguid>=0 AND NOT EXISTS "
        f"(SELECT 1 FROM `{source}`.gameobject_template t WHERE t.entry=s.entryorguid)) "
        f"AND NOT (s.target_type=10 AND s.target_param1<>0 AND NOT EXISTS "
        f"(SELECT 1 FROM `{source}`.creature c WHERE c.guid=s.target_param1)) "
        f"AND NOT (s.target_type=14 AND s.target_param1<>0 AND NOT EXISTS "
        f"(SELECT 1 FROM `{source}`.gameobject g WHERE g.guid=s.target_param1))")
    return insert_count(container, user, password,
                        f"INSERT IGNORE INTO `{target}`.smart_scripts ({','.join(names)}) "
                        f"SELECT {','.join(selected)} FROM `{source}`.smart_scripts s WHERE {where}")


def copy_smart_waypoints(container, user, password, target, source):
    """Copy the waypoint paths used by the copied WP_START actions (TDB keeps them in waypoints)."""
    target_columns = columns(container, user, password, target, "waypoints")
    source_columns = {name for name, _ in columns(container, user, password, source, "waypoints")}
    names = [name for name, auto in target_columns if not auto and name in source_columns]
    columns_sql = ",".join(f"`{name}`" for name in names)
    return insert_count(container, user, password,
                        f"INSERT IGNORE INTO `{target}`.waypoints ({columns_sql}) "
                        f"SELECT {columns_sql} FROM `{source}`.waypoints w WHERE w.entry IN "
                        f"(SELECT DISTINCT action_param2 FROM `{target}`.smart_scripts "
                        f"WHERE action_type=53 AND action_param2<>0)")


def derive_templates(container, user, password, target, source):
    """Fill the AC tables that TDB keeps as columns on creature_template."""
    counts = {}

    def derive(table, target_columns, selects):
        counts[table] = insert_count(container, user, password,
                                     f"INSERT IGNORE INTO `{target}`.`{table}` ({target_columns}) "
                                     + " UNION ALL ".join(selects))

    # TDB keeps four model ids per entry; AC stores one row per display.
    derive("creature_template_model",
           "CreatureID,Idx,CreatureDisplayID,DisplayScale,Probability,VerifiedBuild",
           [f"SELECT entry,{idx},modelid{idx + 1},1,1,IFNULL(VerifiedBuild,0) "
            f"FROM `{source}`.creature_template WHERE modelid{idx + 1}<>0" for idx in range(4)])
    # resistance1..6 are spell schools 1..6; school 0 (physical) has no TDB column.
    derive("creature_template_resistance", "CreatureID,School,Resistance,VerifiedBuild",
           [f"SELECT entry,{school},resistance{school},IFNULL(VerifiedBuild,0) "
            f"FROM `{source}`.creature_template WHERE resistance{school}<>0" for school in range(1, 7)])
    derive("creature_template_spell", "CreatureID,`Index`,Spell,VerifiedBuild",
           [f"SELECT entry,{index},spell{index + 1},IFNULL(VerifiedBuild,0) "
            f"FROM `{source}`.creature_template WHERE spell{index + 1}<>0" for index in range(8)])
    return counts


# TDB 434 carries TrinityCore class names for a few AIs; the fork has the same behaviour under
# its own registration names. Map them before stripping anything (#298).
SCRIPT_ALIASES = {
    "npc_vesperon": "boss_sartharion_vesperon",
    "npc_shadron": "boss_sartharion_shadron",
    "npc_tenebron": "boss_sartharion_tenebron",
    "npc_feral_defender": "npc_auriaya_feral_defender",
    "boss_prince_taldaram": "boss_taldaram",
    # TDB 434 adds the encounter, dungeon or quest qualifier to the fork's registration name.
    # Verified by the NPC entry each fork script handles; names TDB also uses on the Cata
    # remake of the same encounter (e.g. Mandokir 11382/52151) are deliberately not mapped.
    "npc_alchemist_finklestein": "npc_finklestein",          # 28205
    "npc_chromie_start": "npc_cos_chromie_start",            # 26527, Culling of Stratholme
    "npc_chromie_middle": "npc_cos_chromie_middle",          # 27915, Culling of Stratholme
    "npc_iron_sludge": "boss_sjonnir_iron_sludge",           # 28165, Halls of Stone
    "npc_malformed_ooze": "boss_sjonnir_malformed_ooze",     # 27981, Halls of Stone
    "npc_spirit_bomb": "npc_lk_spirit_bomb",                 # 39189, Icecrown Citadel
    "npc_unstable_sphere": "npc_oculus_unstable_sphere",     # 28166, The Oculus
}


def strip_unregistered_scripts(container, user, password, target):
    """Map known TDB aliases, strip unknown script names and report what is left.

    Must run after the spawn tables are copied: the spawn-level ScriptName values live there and
    were previously never checked because the strip ran in the template phase.
    """
    registered = registered_script_names()
    tables = ("creature_template", "gameobject_template", "creature", "gameobject")
    for tdb_name, ac_name in SCRIPT_ALIASES.items():
        if ac_name not in registered:
            continue
        literal = f"X'{tdb_name.encode().hex()}'"
        for table in tables:
            run(container, user, password,
                f"UPDATE `{target}`.`{table}` SET ScriptName='{ac_name}' "
                f"WHERE BINARY ScriptName={literal}")
    spawns = {}
    for template_table, spawn_table in (("creature_template", "creature"),
                                        ("gameobject_template", "gameobject")):
        for name, count in run(container, user, password,
                f"SELECT t.ScriptName, COUNT(*) FROM `{target}`.`{template_table}` t "
                f"JOIN `{target}`.`{spawn_table}` s ON s.id=t.entry "
                f"WHERE t.ScriptName<>'' GROUP BY t.ScriptName"):
            spawns.setdefault(template_table, {})[name] = int(count)
    stripped = {}
    for table in tables:
        names = {row[0]: int(row[1]) for row in run(
            container, user, password,
            f"SELECT ScriptName, COUNT(*) FROM `{target}`.`{table}` "
            f"WHERE ScriptName<>'' GROUP BY ScriptName")}
        unknown = sorted(set(names) - registered)
        if not unknown:
            continue
        literals = ",".join(f"X'{name.encode().hex()}'" for name in unknown)
        run(container, user, password,
            f"UPDATE `{target}`.`{table}` SET ScriptName='' "
            f"WHERE BINARY ScriptName IN ({literals})")
        for name in unknown:
            record = {"rows": names[name]}
            if name in spawns.get(table, {}):
                record["spawns"] = spawns[table][name]
            stripped.setdefault(table, {})[name] = record
    return stripped


PET_LEVEL_CAP = 85


def build_pet_levels(container, user, password, target, source):
    """Extend pet_levelstats to the 4.3.4 level cap.

    TDB 434 carries the 81-85 stats but no damage columns; a 3.3.5a-only pet with no TDB row
    repeats its level-80 row. Pet damage is computed in C++ on 4.3.4, so the data-only bridge
    keeps the level-80 base and scales it linearly with level (TC's base formula is linear).
    """
    copied = copy_table(container, user, password, target, source, "pet_levelstats",
                        where=f"`level` BETWEEN 81 AND {PET_LEVEL_CAP}")
    extended = 0
    for level in range(81, PET_LEVEL_CAP + 1):
        rows = run(container, user, password,
                   f"INSERT IGNORE INTO `{target}`.pet_levelstats "
                   f"(creature_entry, `level`, hp, mana, armor, str, agi, sta, inte, spi, "
                   f"min_dmg, max_dmg) "
                   f"SELECT p.creature_entry, {level}, p.hp, p.mana, p.armor, p.str, p.agi, p.sta, "
                   f"p.inte, p.spi, ROUND(p.min_dmg*{level}/80), ROUND(p.max_dmg*{level}/80) "
                   f"FROM `{target}`.pet_levelstats p WHERE p.`level`=80; SELECT ROW_COUNT()")
        extended += int(rows[-1][0]) if rows else 0
    rows = run(container, user, password,
               f"UPDATE `{target}`.pet_levelstats p "
               f"JOIN `{target}`.pet_levelstats l "
               f"ON l.creature_entry=p.creature_entry AND l.`level`=80 "
               f"SET p.min_dmg=ROUND(l.min_dmg*p.`level`/80), p.max_dmg=ROUND(l.max_dmg*p.`level`/80) "
               f"WHERE p.`level` BETWEEN 81 AND {PET_LEVEL_CAP} AND p.min_dmg=0 AND p.max_dmg=0 "
               f"AND (l.min_dmg>0 OR l.max_dmg>0); SELECT ROW_COUNT()")
    damage = int(rows[-1][0]) if rows else 0
    return {"copied": copied, "extended": extended, "damage": damage}


def sync_cata_stats(container, user, password, target, source):
    """TDB 434 only fills basehp3/damage_exp3 from level 80 on.

    The table copy uses INSERT IGNORE, so an existing (level, class) row keeps its old values;
    refresh the two Cataclysm columns from the source first, then copy the WotLK value where TDB
    leaves them empty so the loader does not log and fall back per row (#315).
    """
    present = {name for name, _ in columns(container, user, password, target, "creature_classlevelstats")}
    if not {"basehp3", "damage_exp3"} <= present:
        raise RuntimeError("creature_classlevelstats lacks basehp3/damage_exp3; "
                           "apply the pending #315 update first")
    synced = int(run(container, user, password,
                     f"UPDATE `{target}`.creature_classlevelstats g "
                     f"JOIN `{source}`.creature_classlevelstats t "
                     f"ON t.level=g.level AND t.class=g.class "
                     f"SET g.basehp3=t.basehp3, g.damage_exp3=t.damage_exp3; SELECT ROW_COUNT()")[-1][0])
    health = int(run(container, user, password,
                     f"UPDATE `{target}`.creature_classlevelstats "
                     f"SET basehp3=basehp2 WHERE basehp3<=1 AND basehp2>1; SELECT ROW_COUNT()")[-1][0])
    damage = int(run(container, user, password,
                     f"UPDATE `{target}`.creature_classlevelstats "
                     f"SET damage_exp3=damage_exp2 WHERE damage_exp3=0 AND damage_exp2>0; "
                     f"SELECT ROW_COUNT()")[-1][0])
    return {"synced": synced, "health": health, "damage": damage}


def build_templates(container, user, password, target, source):
    applied = {table: copy_table(container, user, password, target, source, table,
                                 RENAMES.get(table))
               for table in ("gameobject_template", "creature_model_info", "creature_template",
                             "creature_equip_template", "creature_template_addon",
                             "gameobject_template_addon", "creature_classlevelstats",
                             "npc_spellclick_spells", "vehicle_template_accessory",
                             "vehicle_seat_addon")}
    # Spellclick eligibility is stored as source-type-18 conditions, not on the
    # npc_spellclick_spells row; without them every restored vehicle becomes open.
    applied["spellclick_conditions"] = copy_table(
        container, user, password, target, source, "conditions",
        where="`SourceTypeOrReferenceId`=18")
    applied.update(derive_templates(container, user, password, target, source))
    applied["pet_levelstats"] = build_pet_levels(container, user, password, target, source)
    applied["creature_classlevelstats_cata"] = sync_cata_stats(container, user, password, target, source)
    # Only the hover rows matter; the rest of TDB's movement table would change gravity/flight
    # defaults the fork has not modelled.
    applied["creature_template_movement"] = copy_table(
        container, user, password, target, source, "creature_template_movement",
        where="`Ground`=2")
    excluded = ",".join(str(entry) for entry in VALKYR_SCRIPTED)
    if VALKYR_DISPLAYS:
        displays = ",".join(str(display) for display in VALKYR_DISPLAYS)
        applied["valkyr_movement"] = insert_count(
            container, user, password,
            f"INSERT IGNORE INTO `{target}`.creature_template_movement (CreatureId, Ground) "
            f"SELECT entry, 2 FROM `{source}`.creature_template "
            f"WHERE (modelid1 IN ({displays}) OR modelid2 IN ({displays}) "
            f"OR modelid3 IN ({displays}) OR modelid4 IN ({displays})) "
            f"AND entry NOT IN ({excluded})")
    # This fork's client applies UNIT_FIELD_HOVERHEIGHT without requiring the hover flag, so a
    # template height is only meaningful where something actually hovers the creature: a movement
    # template, a template/spawn hover aura, a SmartAI hover cast or a script. Heights other than
    # the default 1 are authored intent (Svala 3, Brundir 10, dragons up to 35), so only the 1s
    # are normalized.
    scripted = ",".join(str(entry) for entry in SCRIPTED_HOVERERS)
    hover_template = " AND ".join(
        f"CONCAT(' ', a.auras, ' ') NOT LIKE '% {spell} %'" for spell in HOVER_AURAS)
    hover_template_positive = " OR ".join(
        f"CONCAT(' ', a.auras, ' ') LIKE '% {spell} %'" for spell in HOVER_AURAS)
    hover_spawn = " OR ".join(
        f"CONCAT(' ', ca.auras, ' ') LIKE '% {spell} %'" for spell in HOVER_AURAS)
    hover_cast = " OR ".join(
        f"(ss.action_type=11 AND ss.action_param1={spell})" for spell in HOVER_AURAS)
    applied["hover_height"] = update_count(
        container, user, password,
        f"UPDATE `{target}`.creature_template t "
        f"LEFT JOIN `{target}`.creature_template_movement m ON m.CreatureId=t.entry "
        f"LEFT JOIN `{target}`.creature_template_addon a ON a.entry=t.entry "
        f"LEFT JOIN (SELECT DISTINCT c.id FROM `{source}`.creature c "
        f"JOIN `{source}`.creature_addon ca ON ca.guid=c.guid WHERE {hover_spawn}) sh "
        f"ON sh.id=t.entry "
        f"LEFT JOIN (SELECT DISTINCT entryOrGuid FROM `{source}`.smart_scripts ss "
        f"WHERE ss.source_type=0 AND ({hover_cast})) sa ON sa.entryOrGuid=t.entry "
        f"SET t.HoverHeight=0 "
        f"WHERE t.HoverHeight=1 AND COALESCE(m.Ground,0)<>2 AND sh.id IS NULL "
        f"AND sa.entryOrGuid IS NULL "
        f"AND (a.auras IS NULL OR ({hover_template})) "
        f"AND t.entry NOT IN ({scripted})")
    # A difficulty template inherits its base's script/aura hover, and Creature::InitEntry reads
    # the difficulty height, so restore the source height for the variants of every base that has
    # an actual hover source. A nondefault height alone is not a source (flight-only bosses keep
    # one), so select the bases by source, not by HoverHeight<>0.
    hover_source = (f"COALESCE(m.Ground,0)=2 OR sh.id IS NOT NULL OR sa.entryOrGuid IS NOT NULL "
                    f"OR (a.auras IS NOT NULL AND ({hover_template_positive})) "
                    f"OR t.entry IN ({scripted})")
    diffs = run(container, user, password,
                f"SELECT difficulty_entry_1, difficulty_entry_2, difficulty_entry_3 "
                f"FROM `{target}`.creature_template t "
                f"LEFT JOIN `{target}`.creature_template_movement m ON m.CreatureId=t.entry "
                f"LEFT JOIN `{target}`.creature_template_addon a ON a.entry=t.entry "
                f"LEFT JOIN (SELECT DISTINCT c.id FROM `{source}`.creature c "
                f"JOIN `{source}`.creature_addon ca ON ca.guid=c.guid WHERE {hover_spawn}) sh "
                f"ON sh.id=t.entry "
                f"LEFT JOIN (SELECT DISTINCT entryOrGuid FROM `{source}`.smart_scripts ss "
                f"WHERE ss.source_type=0 AND ({hover_cast})) sa ON sa.entryOrGuid=t.entry "
                f"WHERE ({hover_source}) AND (t.difficulty_entry_1<>0 OR t.difficulty_entry_2<>0 "
                f"OR t.difficulty_entry_3<>0)")
    variant_ids = sorted({int(value) for row in diffs for value in row if value and int(value)})
    if variant_ids:
        applied["hover_difficulty"] = update_count(
            container, user, password,
            f"UPDATE `{target}`.creature_template t "
            f"JOIN `{source}`.creature_template s ON s.entry=t.entry "
            f"SET t.HoverHeight=s.HoverHeight "
            f"WHERE t.entry IN ({','.join(map(str, variant_ids))}) AND t.HoverHeight=0")
    return applied


# Client spell names: Generic Quest Invisibility 1/2/3 and its matching Detection 1/2/3.
INVISIBILITY_DETECTION = {"49414": "49416", "49415": "49417", "60921": "60922"}

# Authored starter transitions where TDB has no detection spell_area (#282). Detection 49416
# makes a spawn carrying invisibility 49414 visible; the quest states are the authored predicate.
STARTER_DETECTIONS = (
    # area, detection spell, quest_start, quest_end, quest_start_status, quest_end_status
    (5692, 49416, 0, 24959, 0, 41),  # Deathknell: Agatha visible until 24959 is rewarded
)

# Val'kyr displays, derived from the client's CreatureModelData model paths at build time; the
# 4.x Ground=2 hover mode is the data the core turns into MOVEMENTFLAG_HOVER.
VALKYR_DISPLAYS: tuple[int, ...] = ()

# Val'kyr whose scripts drive hover/fly themselves (Ingvar's Annhylde, the Scarlet Battle-maiden,
# Svala, the Twin Val'kyr, the Lich King Shadowguard and ICC Svalna). A movement-template hover
# would offset their scripted Z, so their script keeps control.
VALKYR_SCRIPTED = (24068, 31655, 28534, 26668, 30810, 34496, 34497, 35347, 35348, 35349, 35350,
                   35351, 35352, 36609, 39120, 39121, 39122, 37126, 38258)

# Client-DBC SPELL_AURA_HOVER (106) spells found on TDB template or spawn addons: the Floating
# Drowned (36550), template auras on 44442/52362/54476/54751 (86137/96851/102602) and the Mage
# Hunter Ascendant spawns (50195). Add others when the data uses them.
HOVER_AURAS = (36550, 86137, 96851, 102602, 50195)

# Scripts that call SetHover(true) on the creature itself and are bound in the clean world after
# the TDB->AC aliases (#298), so their template height is needed (only the height-1 entries matter;
# non-1 heights are kept regardless): Svala, Illidan, Ahn'kahet Taldaram/Jedoga, the Sartharion
# drakes, Brundir, the Feral Defender, the ICC Frostbinder and the Blood Prince Council's Ball of
# Flame/Dark Nucleus helpers. The Light's Vengeance vehicle stays out because its hover caller
# (npc_q24545_lich_king) is not bound. Keep this separate from VALKYR_SCRIPTED, whose flight-only
# entries must not stay hovered.
SCRIPTED_HOVERERS = (26668, 30810, 22917, 29308, 29310, 30449, 30451, 30452, 32857, 34035, 37127,
                     38332, 38369, 38451)


def load_valkyr_displays(dbc_dir: Path) -> tuple[int, ...]:
    """Display ids whose model path is a Val'kyr model (CreatureModelData -> CreatureDisplayInfo)."""
    data = (dbc_dir / 'CreatureModelData.dbc').read_bytes()
    n, fields, rec, sblk = struct.unpack('<IIII', data[4:20])
    strings = 20 + n * rec
    models = set()
    for i in range(n):
        off = 20 + i * rec
        model_id = struct.unpack_from('<I', data, off)[0]
        for field in range(1, fields):
            value = struct.unpack_from('<I', data, off + 4 * field)[0]
            if 0 < value < sblk:
                end = data.find(b'\0', strings + value)
                text = data[strings + value:end].decode(errors='ignore').upper()
                if 'VALKIER' in text or 'VALKYR' in text:
                    models.add(model_id)
                    break
    data = (dbc_dir / 'CreatureDisplayInfo.dbc').read_bytes()
    n, fields, rec, sblk = struct.unpack('<IIII', data[4:20])
    displays = set()
    for i in range(n):
        display_id, model_id = struct.unpack_from('<II', data, 20 + i * rec)
        if model_id in models:
            displays.add(display_id)
    return tuple(sorted(displays))


def update_count(container, user, password, sql):
    """Run an UPDATE and return its ROW_COUNT (insert_count is for INSERT statements)."""
    rows = run(container, user, password, sql + "; SELECT ROW_COUNT()")
    return int(rows[-1][0]) if rows else 0


def restore_addon_auras(container, user, password, target, source):
    """An older build cleared every addon aura; the INSERT IGNORE copies cannot put them back."""
    counts = {"creature_addon": update_count(
        container, user, password,
        f"UPDATE `{target}`.creature_addon a JOIN `{source}`.creature_addon t "
        f"ON a.guid=t.guid+{GUID_OFFSET} SET a.auras=t.auras")}
    counts["creature_template_addon"] = update_count(
        container, user, password,
        f"UPDATE `{target}`.creature_template_addon a JOIN `{source}`.creature_template_addon t "
        f"ON a.entry=t.entry SET a.auras=t.auras")
    return counts


def apply_starter_detections(container, user, password, target):
    """Add the authored detection auras that make TDB's invisibility variants visible (#282)."""
    applied = {}
    for area, spell, quest_start, quest_end, start_status, end_status in STARTER_DETECTIONS:
        applied[f"detection_{spell}_{area}"] = insert_count(
            container, user, password,
            f"INSERT IGNORE INTO `{target}`.spell_area "
            f"(spell, area, quest_start, quest_end, aura_spell, racemask, gender, autocast, "
            f"quest_start_status, quest_end_status) "
            f"VALUES ({spell}, {area}, {quest_start}, {quest_end}, 0, 0, 2, 1, {start_status}, {end_status})")
    return applied


def strip_uncovered_invisibility(container, user, password, target):
    """Remove the generic quest-invisibility auras where TDB has no `spell_area` detection for the area.

    Areas with detection (e.g. Shadowglen 188) keep them so the aura/detection pair works; areas
    without it (e.g. Deathknell 5692) would hide the NPC forever. Other auras in the same field stay.
    """
    def cleanup(column, spell):
        # Remove one token at a time so adjacent ids (e.g. "49414 49415") are both handled.
        return ("TRIM(REGEXP_REPLACE(REGEXP_REPLACE(CONCAT(' ', {col}, ' '), "
                f"' {spell} ', ' '), ' +', ' '))").format(col=column)

    counts = {"templates": 0, "spawns": 0}
    for invisibility, detection in INVISIBILITY_DETECTION.items():
        covered_area = (f"SELECT area FROM `{target}`.spell_area WHERE spell={detection}")
        counts["spawns"] += update_count(
            container, user, password,
            f"UPDATE `{target}`.creature_addon a JOIN `{target}`.creature c ON c.guid=a.guid "
            f"SET a.auras={cleanup('a.auras', invisibility)} WHERE a.auras LIKE '%{invisibility}%' "
            f"AND c.zoneId NOT IN ({covered_area}) AND c.areaId NOT IN ({covered_area})")
        # A template aura affects every spawn of the entry: keep it only when no spawn is uncovered.
        counts["templates"] += update_count(
            container, user, password,
            f"UPDATE `{target}`.creature_template_addon t SET t.auras={cleanup('t.auras', invisibility)} "
            f"WHERE t.auras LIKE '%{invisibility}%' AND EXISTS (SELECT 1 FROM `{target}`.creature c "
            f"WHERE c.id=t.entry AND c.zoneId NOT IN ({covered_area}) AND c.areaId NOT IN ({covered_area}))")
    return counts


def suppress_spawns(container, user, password, target):
    """Drop the reviewed alternate-variant spawns listed in clean_world_suppressed_spawns.tsv."""
    path = Path(__file__).with_name("clean_world_suppressed_spawns.tsv")
    if not path.exists():
        return 0
    count = 0
    for line in path.read_text().splitlines():
        if not line or line.startswith("#"):
            continue
        table, guid, *_ = line.split("\t")
        for name in (table, f"{table}_addon"):
            run(container, user, password, f"DELETE FROM `{target}`.`{name}` WHERE guid={int(guid)}")
        count += 1
    return count


def import_phase_data(container, user, password, target, source):
    """Area phase membership and the type-26 conditions that gate it (#281).

    Spawn PhaseId/PhaseGroup values arrive with the spawn copy; an unphased player sees the default
    phase (169), see Player::IsUnphased.
    """
    # phase_area is fully TDB-owned; replacing it drops rows an older import stored with the
    # group still in PhaseId (#348).
    applied = {"phase_area": copy_table(container, user, password, target, source, "phase_area",
                                        replace=True),
               "phase_conditions": copy_table(container, user, password, target, source, "conditions",
                                              where="SourceTypeOrReferenceId IN (25,26)"),
               "terrain_swap_defaults": copy_table(container, user, password, target, source,
                                                    "terrain_swap_defaults"),
               "terrain_worldmap": copy_table(container, user, password, target, source,
                                               "terrain_worldmap"),
               # Area auras drive quest-state detection (e.g. Shadowglen 49416/49417/60922).
               "spell_area": copy_table(container, user, password, target, source, "spell_area")}
    # TDB's spell_area has no autocast column. Enable it only for the invisibility-detection
    # spells this build relies on; other rows keep the schema default rather than auto-applying.
    detections = ",".join(sorted(INVISIBILITY_DETECTION.values()))
    rows = run(container, user, password,
               f"UPDATE `{target}`.spell_area SET autocast=1 WHERE spell IN ({detections}); "
               f"SELECT ROW_COUNT()")
    applied["spell_area_autocast"] = int(rows[-1][0]) if rows else 0
    # INSERT IGNORE leaves spawns already imported by an older build untouched, so restore their
    # phase id and group explicitly by the shifted GUID mapping.
    for table in ("creature", "gameobject"):
        rows = run(container, user, password,
                   f"UPDATE `{target}`.`{table}` x JOIN `{source}`.`{table}` t "
                   f"ON x.guid=t.guid+{GUID_OFFSET} "
                   f"SET x.PhaseId=t.PhaseId, x.PhaseGroup=t.PhaseGroup "
                   f"WHERE x.PhaseId<>t.PhaseId OR x.PhaseGroup<>t.PhaseGroup; SELECT ROW_COUNT()")
        applied[f"phase_restored_{table}"] = int(rows[-1][0]) if rows else 0
    return applied


def build_spawns(container, user, password, target, source):
    applied = {}
    # game_event stays the AC support copy: WorldState starts hardcoded event ids that TDB's shorter
    # schedule does not define, and the TDB links below only reference the shared holiday ids.
    for table in SPAWN_TABLES:
        applied[table] = copy_table(container, user, password, target, source, table,
                                    RENAMES.get(table), SPAWN_GUID_COLUMNS.get(table, set()))
    applied["smart_scripts"] = copy_smart_scripts(container, user, password, target, source)
    applied["waypoints"] = copy_smart_waypoints(container, user, password, target, source)
    applied.update(restore_addon_auras(container, user, password, target, source))
    applied.update(import_phase_data(container, user, password, target, source))
    applied.update(apply_starter_detections(container, user, password, target))
    applied.update(strip_uncovered_invisibility(container, user, password, target))
    applied["variants_suppressed"] = suppress_spawns(container, user, password, target)
    applied["stripped_script_names"] = strip_unregistered_scripts(container, user, password, target)
    return applied


def build_loot(container, user, password, target, source):
    """All loot tables are world data; the zone import only fills the quest-relevant rows."""
    return {table: copy_table(container, user, password, target, source, table)
            for table in ("creature_loot_template", "gameobject_loot_template",
                          "reference_loot_template", "pickpocketing_loot_template",
                          "skinning_loot_template", "disenchant_loot_template",
                          "prospecting_loot_template", "milling_loot_template",
                          "spell_loot_template", "item_loot_template", "mail_loot_template",
                          "fishing_loot_template")}


def build_quest_items(container, user, password, target, source):
    """Turn the 4.x quest-item stores into loot rows AC's loot path reads.

    TDB keeps quest drops in creature_questitem/gameobject_questitem, which this core only sends in
    the creature/GO query response. A quest-required loot row drops only for a player who needs it.
    Loot is keyed by creature_template.lootid and by the chest/fishing-hole loot id (Data1), not by
    the entry itself; a carrier without one gets a dedicated id so no shared template is reused.
    """
    if not int(run(container, user, password,
                   f"SELECT COUNT(*) FROM `{target}`.creature_loot_template")[0][0]):
        raise RuntimeError("run the loot phase before quest_items")
    applied = {}
    # Restore the source loot id first so a rerun starts from the baked template, then give a
    # carrier without one an id from the unused range instead of its entry.
    applied["creature_loot_ids"] = update_count(
        container, user, password,
        f"UPDATE `{target}`.creature_template t JOIN `{source}`.creature_template s ON s.entry=t.entry "
        f"JOIN `{source}`.creature_questitem q ON q.CreatureEntry=t.entry SET t.lootid=s.lootid")
    applied["creature_loot_ids"] += update_count(
        container, user, password,
        f"UPDATE `{target}`.creature_template t JOIN `{source}`.creature_template s ON s.entry=t.entry "
        f"JOIN `{source}`.creature_questitem q ON q.CreatureEntry=t.entry "
        f"SET t.lootid={QUEST_LOOT_ID_BASE}+t.entry WHERE t.lootid=0")
    applied["creature_loot_template"] = insert_count(
        container, user, password,
        f"INSERT IGNORE INTO `{target}`.creature_loot_template "
        f"(Entry, Item, Reference, Chance, QuestRequired, LootMode, GroupId, MinCount, MaxCount, Comment) "
        f"SELECT t.lootid, q.ItemId, 0, 100, 1, 1, 0, 1, 1, 'quest item' "
        f"FROM `{source}`.creature_questitem q JOIN `{target}`.creature_template t "
        f"ON t.entry=q.CreatureEntry "
        f"LEFT JOIN `{target}`.creature_loot_template l ON l.Entry=t.lootid AND l.Item=q.ItemId "
        f"WHERE l.Entry IS NULL AND t.lootid<>0")
    # Gameobject loot is the chest/fishing-hole loot id, not the object entry.
    applied["gameobject_loot_ids"] = update_count(
        container, user, password,
        f"UPDATE `{target}`.gameobject_template g JOIN `{source}`.gameobject_questitem q "
        f"ON q.GameObjectEntry=g.entry SET g.Data1={QUEST_LOOT_ID_BASE}+g.entry "
        f"WHERE g.type IN (3,25) AND g.Data1=0")
    applied["gameobject_loot_template"] = insert_count(
        container, user, password,
        f"INSERT IGNORE INTO `{target}`.gameobject_loot_template "
        f"(Entry, Item, Reference, Chance, QuestRequired, LootMode, GroupId, MinCount, MaxCount, Comment) "
        f"SELECT g.Data1, q.ItemId, 0, 100, 1, 1, 0, 1, 1, 'quest item' "
        f"FROM `{source}`.gameobject_questitem q JOIN `{target}`.gameobject_template g "
        f"ON g.entry=q.GameObjectEntry "
        f"LEFT JOIN `{target}`.gameobject_loot_template l ON l.Entry=g.Data1 AND l.Item=q.ItemId "
        f"WHERE l.Entry IS NULL AND g.type IN (3,25) AND g.Data1<>0")
    applied["exploration_basexp"] = copy_table(container, user, password, target, source,
                                               "exploration_basexp")
    return applied


def build_text(container, user, password, target, source):
    return {table: copy_table(container, user, password, target, source, table,
                              RENAMES.get(table))
            for table in ("broadcast_text",)}


def build_pools(container, user, password, target, source):
    """TDB pool_members type 0/1/2 -> AC pool_creature/pool_gameobject/pool_pool."""
    applied = {"pool_template": copy_table(container, user, password, target, source, "pool_template")}
    for pool_type, table, columns, select in (
            (0, "pool_creature", "guid,pool_entry,chance,description",
             f"spawnId+{GUID_OFFSET},poolSpawnId,chance,description"),
            (1, "pool_gameobject", "guid,pool_entry,chance,description",
             f"spawnId+{GUID_OFFSET},poolSpawnId,chance,description"),
            (2, "pool_pool", "pool_id,mother_pool,chance,description",
             "spawnId,poolSpawnId,chance,description")):
        applied[table] = insert_count(
            container, user, password,
            f"INSERT IGNORE INTO `{target}`.{table} ({columns}) SELECT {select} "
            f"FROM `{source}`.pool_members WHERE type={pool_type}")
    return applied


def build_interactions(container, user, password, target, source):
    """Book/sign pages and gossip menus; TDB splits option actions and boxes into two tables."""
    # TDB splits each text's three emote slots into delay/emote pairs; AC stores six values.
    npc_text_renames = {f"em{i}_{2 * slot}": f"EmoteDelay{i}_{slot}"
                        for i in range(8) for slot in range(3)}
    npc_text_renames.update({f"em{i}_{2 * slot + 1}": f"Emote{i}_{slot}"
                             for i in range(8) for slot in range(3)})
    applied = {
        "npc_text": copy_table(container, user, password, target, source, "npc_text",
                               npc_text_renames),
        "page_text": copy_table(container, user, password, target, source, "page_text"),
        "gossip_menu": copy_table(container, user, password, target, source, "gossip_menu"),
        "gossip_menu_option": copy_table(
            container, user, password, target, source, "gossip_menu_option",
            {"MenuID": "MenuId", "OptionID": "OptionIndex",
             "OptionBroadcastTextID": "OptionBroadcastTextId", "OptionNpcFlag": "OptionNpcflag"}),
        # 14 = gossip menu, 15 = gossip menu option availability conditions; negative source
        # types are the condition templates those rows can reference.
        "gossip_conditions": copy_table(container, user, password, target, source, "conditions",
                                        where="SourceTypeOrReferenceId IN (14,15) "
                                              "OR SourceTypeOrReferenceId < 0"),
        "points_of_interest": copy_table(container, user, password, target, source,
                                         "points_of_interest"),
    }
    applied["gossip_action"] = update_count(
        container, user, password,
        f"UPDATE `{target}`.gossip_menu_option o JOIN `{source}`.gossip_menu_option_action a "
        f"ON a.MenuId=o.MenuID AND a.OptionIndex=o.OptionID "
        f"SET o.ActionMenuID=a.ActionMenuId, o.ActionPoiID=a.ActionPoiId "
        f"WHERE o.ActionMenuID=0 AND o.ActionPoiID=0")
    applied["gossip_box"] = update_count(
        container, user, password,
        f"UPDATE `{target}`.gossip_menu_option o JOIN `{source}`.gossip_menu_option_box b "
        f"ON b.MenuId=o.MenuID AND b.OptionIndex=o.OptionID "
        f"SET o.BoxCoded=b.BoxCoded, o.BoxMoney=b.BoxMoney, o.BoxText=b.BoxText, "
        f"o.BoxBroadcastTextID=b.BoxBroadcastTextId "
        f"WHERE o.BoxCoded=0 AND o.BoxMoney=0 AND (o.BoxText='' OR o.BoxText IS NULL) "
        f"AND o.BoxBroadcastTextID=0")
    return applied


def build_services(container, user, password, target, source):
    """Vendors and trainers. TDB names the creature-to-trainer table differently."""
    applied = {table: copy_table(container, user, password, target, source, table)
               for table in ("npc_vendor", "trainer", "trainer_spell")}
    applied["creature_default_trainer"] = copy_table(
        container, user, password, target, source, "creature_default_trainer",
        source_table="creature_trainer")
    return applied


PHASES = {"templates": build_templates, "spawns": build_spawns, "loot": build_loot,
          "quest_items": build_quest_items, "text": build_text, "services": build_services,
          "pools": build_pools, "interactions": build_interactions,
          "graveyards": build_graveyards}


def migrate_legacy_phase_masks(container, user, password, target):
    """The clean world is TDB-only, so rows imported with AC's legacy mask type 26 must move to
    CONDITION_CATA_PHASE_ID (108); otherwise both are evaluated and the stale mask can block
    content (#347). UPDATE IGNORE skips rows a previous run already converted; the delete drops
    those duplicates."""
    if not table_exists(container, user, password, target, "conditions"):
        return 0
    run(container, user, password,
        f"USE `{target}`; UPDATE IGNORE conditions SET ConditionTypeOrReference=108 "
        f"WHERE ConditionTypeOrReference=26")
    # Only drop a legacy row when the 108 row it conflicted with is semantically equivalent;
    # otherwise both meanings must be reviewed instead of silently losing one.
    run(container, user, password,
        f"USE `{target}`; DELETE t FROM conditions t JOIN conditions n "
        f"ON n.SourceTypeOrReferenceId=t.SourceTypeOrReferenceId AND n.SourceGroup=t.SourceGroup "
        f"AND n.SourceEntry=t.SourceEntry AND n.SourceId=t.SourceId AND n.ElseGroup=t.ElseGroup "
        f"AND n.ConditionTarget=t.ConditionTarget AND n.ConditionValue1=t.ConditionValue1 "
        f"AND n.ConditionValue2=t.ConditionValue2 AND n.ConditionValue3=t.ConditionValue3 "
        f"AND n.ConditionTypeOrReference=108 AND n.NegativeCondition=t.NegativeCondition AND n.ScriptName=t.ScriptName "
        f"WHERE t.ConditionTypeOrReference=26")
    return int(run(container, user, password,
                   f"SELECT COUNT(*) FROM `{target}`.conditions WHERE ConditionTypeOrReference=26")[-1][0])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--container", default="plan175-inventory-mysql")
    parser.add_argument("--user", default="root")
    parser.add_argument("--password", default="plan175ro")
    parser.add_argument("--source", default="tdb_world")
    parser.add_argument("--target", required=True)
    parser.add_argument("--phase", choices=(*PHASES, "all"), default="all")
    parser.add_argument("--dbc-dir", type=Path, default=os.environ.get("DBC_DIR"),
                        help="client DBC directory; the Val'kyr hover rows are derived from its model paths")
    args = parser.parse_args()
    for name in (args.source, args.target):  # spliced into SQL unquoted
        if not re.fullmatch(r"\w+", name):
            parser.error(f"invalid schema name: {name!r}")
    if args.source == args.target:
        parser.error("--source and --target must differ")

    global VALKYR_DISPLAYS
    if args.dbc_dir:
        VALKYR_DISPLAYS = load_valkyr_displays(args.dbc_dir)
        group_only = phase_group_only(args.dbc_dir)
        if group_only:
            ids = ",".join(map(str, sorted(group_only)))
            EXPRESSIONS[("conditions", "ConditionTypeOrReference")] = (
                f"IF(`ConditionTypeOrReference`=26, IF(`ConditionValue1` IN ({ids}),109,108), "
                f"`ConditionTypeOrReference`)")
    else:
        print("warning: --dbc-dir/DBC_DIR not set; skipping the Val'kyr hover rows", file=sys.stderr)

    if not args.dbc_dir:
        phase_conditions = int(run(args.container, args.user, args.password,
                                   f"SELECT COUNT(*) FROM `{args.source}`.conditions "
                                   f"WHERE ConditionTypeOrReference=26 AND "
                                   f"(SourceTypeOrReferenceId=26 OR SourceTypeOrReferenceId IN (14,15))")[-1][0])
        if phase_conditions:
            sys.exit("--dbc-dir is required to build phase conditions (#348)")

    conflicting = migrate_legacy_phase_masks(args.container, args.user, args.password, args.target)
    if conflicting:
        print(f"warning: {conflicting} legacy phase-mask conditions conflict with a 108 row; kept for review",
              file=sys.stderr)

    phases = PHASES if args.phase == "all" else {args.phase: PHASES[args.phase]}
    for name, build in phases.items():
        print(f"== {name}")
        if name == "graveyards":
            if not args.dbc_dir:
                parser.error("--dbc-dir is required to build graveyards")
            built = build(args.container, args.user, args.password, args.target,
                          args.source, args.dbc_dir)
        else:
            built = build(args.container, args.user, args.password, args.target, args.source)
        for table, count in built.items():
            print(f"{table}: {count}")
    for warning in WARNINGS:
        print(warning, file=sys.stderr)


if __name__ == "__main__":
    main()
