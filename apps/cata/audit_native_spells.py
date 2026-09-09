"""Audit native starting-spell dependencies without a build, server, or database."""

import argparse
import hashlib
import json
from pathlib import Path
import re
import struct

from audit_character_starting_data import PIN, dbc_rows

ROOT = Path(__file__).resolve().parents[2]
JOINS = {
    31: "SpellScaling", 32: "SpellAuraOptions", 33: "SpellAuraRestrictions",
    34: "SpellCastingRequirements", 35: "SpellCategories", 36: "SpellClassOptions",
    37: "SpellCooldowns", 39: "SpellEquippedItems", 40: "SpellInterrupts",
    41: "SpellLevels", 42: "SpellPower", 43: "SpellReagents", 44: "SpellShapeshift",
    45: "SpellTargetRestrictions", 46: "SpellTotems",
}
AUXILIARY_JOINS = {
    12: "SpellCastTimes", 13: "SpellDuration", 15: "SpellRange", 26: "SpellRuneCost",
}
FORMATS = {
    "Spell": "SpellEntryfmt", "SpellEffect": "SpellEffectEntryfmt",
    **{name: name + "Entryfmt" for name in JOINS.values()},
    "SpellCastTimes": "SpellCastTimefmt", "SpellDuration": "SpellDurationfmt",
    "SpellRange": "SpellRangefmt", "SpellRuneCost": "SpellRuneCostfmt",
    "SpellRadius": "SpellRadiusfmt", "SpellCategory": "SpellCategoryfmt",
    "SkillLine": "SkillLinefmt", "SkillLineAbility": "SkillLineAbilityfmt",
    "SkillRaceClassInfo": "SkillRaceClassInfofmt", "SkillTiers": "SkillTiersfmt",
}


def matches(mask, value):
    return not mask or bool(mask & (1 << (value - 1)))


def initial_skills(tables, race, class_id, level):
    result = {}
    ignored = set()
    for row in tables["SkillRaceClassInfo"].values():
        if (row[5] != 1 or row[6] > level or not matches(row[2], race)
                or not matches(row[3], class_id)):
            continue
        skill = tables["SkillLine"].get(row[1])
        if skill is None:
            ignored.add(row[1])
            continue
        tier = tables["SkillTiers"].get(row[7])
        if tier:
            step, maximum = 1, tier[17]
            if not maximum:
                raise ValueError(f"Empty initial tier for skill {row[1]}")
            value = maximum if row[4] & 0x10 else 1
        elif skill[1] == 10:
            step, value, maximum = 0, 300, 300
        elif skill[1] == 8 or row[1] == 776:
            step, value, maximum = 0, 1, 1
        else:
            step, maximum = 0, level * 5
            value = maximum if row[4] & 0x10 else 1
        result[row[1]] = {"step": step, "value": value, "max": maximum}
    return result, sorted(ignored)


def grants(tables, skills, race, class_id, level):
    result, missing = set(), set()
    for row in tables["SkillLineAbility"].values():
        if (row[1] not in skills or row[9] not in (1, 2)
                or not matches(row[3], race) or not matches(row[4], class_id)):
            continue
        if row[9] == 1 and skills[row[1]]["value"] < row[7]:
            continue
        spell = tables["Spell"].get(row[2])
        if spell is None:
            missing.add(row[2])
            continue
        levels = tables["SpellLevels"].get(spell[41])
        if levels and max(levels[1], levels[3]) > level:
            continue
        result.add(row[2])
    return result, missing


def sql_additions(path):
    source = path.read_text()
    result = {}
    for table, width in (("spell_cata_dbc", 48), ("spelleffect_dbc", 27)):
        match = re.search(r"INSERT INTO `" + table + r"` \((.*?)\) VALUES \((.*?)\);", source, re.S)
        if not match:
            raise ValueError(f"Missing native SQL addition: {table}")
        columns = re.findall(r"`([^`]+)`", match[1])
        values = [v.strip() for v in match[2].split(",")]
        if len(columns) != width or len(values) != width:
            raise ValueError(f"Wrong native SQL layout: {table}")
        result[table] = [0 if v == "''" else float(v) if "." in v else int(v) for v in values]
    return result


def audit(root, sql_path):
    source = (ROOT / "src/server/shared/DataStores/DBCfmt.h").read_text()
    formats = dict(re.findall(r'char constexpr (\w+)\[\] = "([^"]+)";', source))
    tables, hashes = {}, {}
    for name, key in FORMATS.items():
        fmt = formats[key]
        if set(fmt) - set("ndixfsS"):
            raise ValueError(f"Unsupported audit layout: {name}")
        path = root / (name + ".dbc")
        rows = [struct.unpack("<" + "I" * len(fmt), row) for row in dbc_rows(path, len(fmt), len(fmt) * 4)]
        tables[name] = {row[0]: row for row in rows}
        if len(tables[name]) != len(rows):
            raise ValueError(f"Duplicate native IDs: {name}")
        hashes[path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
    additions = sql_additions(sql_path)
    for table, name in (("spell_cata_dbc", "Spell"), ("spelleffect_dbc", "SpellEffect")):
        row = additions[table]
        if row[0] in tables[name]:
            raise ValueError(f"SQL addition collides with native {name}: {row[0]}")
        tables[name][row[0]] = row
    skills, ignored = initial_skills(tables, 1, 1, 1)
    direct, missing = grants(tables, skills, 1, 1, 1)
    effects, unsupported = {}, set()
    for row in tables["SpellEffect"].values():
        if row[24] not in tables["Spell"] or row[25] >= 3:
            raise ValueError(f"Invalid spell/effect join: {row[0]}")
        slots = effects.setdefault(row[24], {})
        if row[25] in slots:
            raise ValueError(f"Duplicate spell/effect slot: {row[24:26]}")
        slots[row[25]] = row
        if row[1] >= 165 or row[3] >= 317 or row[22] >= 111 or row[23] >= 111:
            unsupported.add(row[24])
    required, queue, edges = set(), list(direct), []
    while queue:
        spell_id = queue.pop()
        if spell_id in required:
            continue
        required.add(spell_id)
        if spell_id not in tables["Spell"]:
            missing.add(spell_id)
            continue
        for effect in effects.get(spell_id, {}).values():
            if effect[21]:
                edges.append([spell_id, effect[21]])
                queue.append(effect[21])
    unresolved = []
    for spell_id in sorted(required - missing):
        spell = tables["Spell"][spell_id]
        for field, name in (JOINS | AUXILIARY_JOINS).items():
            if spell[field] and spell[field] not in tables[name]:
                unresolved.append({"spell": spell_id, "table": name, "id": spell[field]})
        for effect in effects.get(spell_id, {}).values():
            for field in (15, 16):
                if effect[field] and effect[field] not in tables["SpellRadius"]:
                    unresolved.append({"spell": spell_id, "table": "SpellRadius", "id": effect[field]})
    return {
        "reference_commit": PIN,
        "source_provenance_manifest": "apps/cata/fixtures/plan22-starting-data-audit.json",
        "reproduction": [
            "python3 apps/cata/audit_native_spells.py --self-test",
            "python3 apps/cata/audit_native_spells.py --dbc-root <CATA_DBC_ROOT> --sql-update "
            + str(sql_path.relative_to(ROOT) if sql_path.is_absolute() else sql_path),
        ],
        "status": "INCONCLUSIVE",
        "limits": "Static input audit; AC loader, passive auras and persistence have not run. "
                  "Pinned TC defaults absent optional split records. Nonzero missing references "
                  "remain unresolved here; extraction evidence is linked from the provenance manifest.",
        "matrix": {"race": 1, "class": 1, "level": 1, "genders": [0, 1]},
        "initial_skills": skills, "ignored_obsolete_skills": ignored,
        "direct_spells": sorted(direct), "dependency_spells": sorted(required),
        "trigger_edges": sorted(edges), "missing_spells": sorted(missing),
        "unresolved_nonzero_joins": unresolved,
        "unsupported_required_spells": sorted(required & unsupported),
        "unsupported_native_spells": sorted(unsupported),
        "source_sha256": hashes,
        "sql_addition_sha256": hashlib.sha256(sql_path.read_bytes()).hexdigest(),
    }


def self_test():
    spell = [0] * 48
    spell[0], spell[41] = 100, 1
    ability = [1, 10, 100, 1, 1, 0, 0, 5, 0, 1, 0, 0, 0, 0]
    tables = {"SkillLineAbility": {1: ability}, "Spell": {100: spell}, "SpellLevels": {1: [1, 1, 0, 1]}}
    assert grants(tables, {10: {"value": 4}}, 1, 1, 1)[0] == set()
    assert grants(tables, {10: {"value": 5}}, 1, 1, 1)[0] == {100}
    assert grants(tables, {10: {"value": 5}}, 2, 1, 1)[0] == set()
    assert grants(tables, {10: {"value": 5}}, 1, 2, 1)[0] == set()
    tables["SpellLevels"][1][3] = 2
    assert grants(tables, {10: {"value": 5}}, 1, 1, 1)[0] == set()
    assert grants(tables, {10: {"value": 5}}, 1, 1, 2)[0] == {100}
    del tables["Spell"][100]
    assert grants(tables, {10: {"value": 5}}, 1, 1, 2)[1] == {100}
    print("native spell audit self-test passed")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dbc-root", type=Path)
    parser.add_argument("--sql-update", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return
    if not args.dbc_root or not args.sql_update:
        parser.error("--dbc-root and --sql-update are required")
    result = audit(args.dbc_root, args.sql_update)
    result["audit_exit_code"] = int(bool(result["missing_spells"] or result["unresolved_nonzero_joins"]
                                         or result["unsupported_required_spells"]))
    print(json.dumps(result, indent=2, sort_keys=True))
    raise SystemExit(result["audit_exit_code"])


if __name__ == "__main__":
    main()
