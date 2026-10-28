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
from pathlib import Path
import re
import sys

from import_starter_content import (SMART_EVENT_PARAM_COUNTS, UNSUPPORTED_SMART_ACTIONS, WARNINGS,
                                    columns, insert_count, run)
from starter_content_inventory import registered_script_names

GUID_OFFSET = 10_000_000
# TDB keeps a few columns under a different name; the rest of AC's spawn tables match.
RENAMES = {
    "broadcast_text": {"MaleText": "Text", "FemaleText": "Text1"},
    "creature": {"wander_distance": "spawndist"},
    "creature_addon": {"path_id": "waypointPathId"},
    "creature_formations": {"leaderGUID": "LeaderGUID", "memberGUID": "MemberGUID",
                            "angle": "FollowAngle", "dist": "FollowDistance"},
}
# TDB writes "no formation waypoint" as -1; AC's unsigned columns use 0.
# TDB splits a spawn's phase into PhaseId and PhaseGroup; the fork stores either in PhaseId and
# expands groups through PhaseXPhaseGroup (validated: every TDB id is a client phase or group).
EXPRESSIONS = {("creature_formations", "point_1"): "GREATEST(`InversionPoint1`,0)",
               ("creature_formations", "point_2"): "GREATEST(`InversionPoint2`,0)",
               ("creature", "PhaseId"): "IF(`PhaseId`=0, `PhaseGroup`, `PhaseId`)",
               ("gameobject", "PhaseId"): "IF(`PhaseId`=0, `PhaseGroup`, `PhaseId`)"}
SPAWN_TABLES = ("creature", "gameobject", "creature_addon", "gameobject_addon",
                "creature_formations", "waypoint_data", "creature_text",
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


def strip_unregistered_scripts(container, user, password, target):
    """TDB ScriptName values are TrinityCore classes; an unknown name makes the loader skip the AI."""
    registered = registered_script_names()
    stripped = 0
    for table in ("creature_template", "gameobject_template", "creature", "gameobject"):
        names = {row[0] for row in run(container, user, password,
                                       f"SELECT DISTINCT ScriptName FROM `{target}`.`{table}` "
                                       f"WHERE ScriptName<>''")}
        unknown = sorted(names - registered)
        if unknown:
            literals = ",".join(f"X'{name.encode().hex()}'" for name in unknown)
            run(container, user, password,
                f"UPDATE `{target}`.`{table}` SET ScriptName='' "
                f"WHERE BINARY ScriptName IN ({literals})")
            stripped += len(unknown)
    return stripped


def build_templates(container, user, password, target, source):
    applied = {table: copy_table(container, user, password, target, source, table)
               for table in ("gameobject_template", "creature_model_info", "creature_template",
                             "creature_equip_template", "creature_template_addon",
                             "gameobject_template_addon")}
    applied.update(derive_templates(container, user, password, target, source))
    applied["stripped_script_names"] = strip_unregistered_scripts(container, user, password, target)
    return applied


# Client spell names: Generic Quest Invisibility 1/2/3 and its matching Detection 1/2/3.
INVISIBILITY_DETECTION = {"49414": "49416", "49415": "49417", "60921": "60922"}


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

    Spawn PhaseId values arrive with the spawn copy (PhaseGroup mapped through EXPRESSIONS); an
    unphased player sees the default phase (169), see Player::IsUnphased.
    """
    applied = {"phase_area": copy_table(container, user, password, target, source, "phase_area"),
               "phase_conditions": copy_table(container, user, password, target, source, "conditions",
                                              where="SourceTypeOrReferenceId=26"),
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
    # phases explicitly by the shifted GUID mapping.
    for table in ("creature", "gameobject"):
        rows = run(container, user, password,
                   f"UPDATE `{target}`.`{table}` x JOIN `{source}`.`{table}` t "
                   f"ON x.guid=t.guid+{GUID_OFFSET} "
                   f"SET x.PhaseId=IF(t.PhaseId=0,t.PhaseGroup,t.PhaseId) "
                   f"WHERE x.PhaseId<>IF(t.PhaseId=0,t.PhaseGroup,t.PhaseId); SELECT ROW_COUNT()")
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
    applied.update(restore_addon_auras(container, user, password, target, source))
    applied.update(import_phase_data(container, user, password, target, source))
    applied.update(strip_uncovered_invisibility(container, user, password, target))
    applied["variants_suppressed"] = suppress_spawns(container, user, password, target)
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
          "text": build_text, "services": build_services, "pools": build_pools,
          "interactions": build_interactions}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--container", default="plan175-inventory-mysql")
    parser.add_argument("--user", default="root")
    parser.add_argument("--password", default="plan175ro")
    parser.add_argument("--source", default="tdb_world")
    parser.add_argument("--target", required=True)
    parser.add_argument("--phase", choices=(*PHASES, "all"), default="all")
    args = parser.parse_args()
    for name in (args.source, args.target):  # spliced into SQL unquoted
        if not re.fullmatch(r"\w+", name):
            parser.error(f"invalid schema name: {name!r}")

    phases = PHASES if args.phase == "all" else {args.phase: PHASES[args.phase]}
    for name, build in phases.items():
        print(f"== {name}")
        for table, count in build(args.container, args.user, args.password,
                                  args.target, args.source).items():
            print(f"{table}: {count}")
    for warning in WARNINGS:
        print(warning, file=sys.stderr)


if __name__ == "__main__":
    main()
