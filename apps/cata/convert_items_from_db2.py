#!/usr/bin/env python3
"""Convert native Cataclysm Item.db2 + Item-sparse.db2 rows into AC item_template SQL (#210).

The 4.3.4 client stores no per-item armor, weapon damage or durability; with --dbc-dir these are derived
from the ItemArmor*/ItemDamage* tables (item_derived_stats.py). Without it, or for the columns that stay
underived (block, second damage, scaling value, disenchant), the schema default is kept and the output
header lists them so the gap stays visible. Item page text is not converted: the pages are world data, and
TDB 434 lacks Cata's later pages (a reference would be dangling). Cata-only stat types are translated where AC
has a column:
50 extra armor -> armor, 51-56 resistances -> *_res; 49 mastery has no AC counterpart and is dropped
rather than left for the loader to reinterpret as mana.

Item ids come from the command line or, for any zone, from an import_starter_content.py JSON report
(--ids-from-report): its closure.missing_items lists the item ids the zone's quests reference that
item_template lacks.

Prints DELETE + INSERT SQL for the given item IDs to stdout (--insert-only: a plain INSERT for new ids);
it never touches a database.
"""

import argparse
import json
import sys

from audit_native_item_sparse import db2_records as sparse_records
from audit_native_items import db2_records as item_records
from item_derived_stats import effective_armor, load_tables, max_durability, weapon_damage

SIMPLE = {  # AC column <- Item-sparse field
    "Quality": "Quality", "Flags": "Flags", "FlagsExtra": "Flags2", "BuyCount": "BuyCount",
    "BuyPrice": "BuyPrice", "SellPrice": "SellPrice", "InventoryType": "InventoryType",
    "AllowableClass": "AllowableClass", "AllowableRace": "AllowableRace", "ItemLevel": "ItemLevel",
    "RequiredLevel": "RequiredLevel", "RequiredSkill": "RequiredSkill",
    "RequiredSkillRank": "RequiredSkillRank", "requiredspell": "RequiredSpell",
    "requiredhonorrank": "RequiredHonorRank", "RequiredCityRank": "RequiredCityRank",
    "RequiredReputationFaction": "RequiredReputationFaction",
    "RequiredReputationRank": "RequiredReputationRank", "maxcount": "MaxCount", "stackable": "Stackable",
    "ContainerSlots": "ContainerSlots", "ScalingStatDistribution": "ScalingStatDistribution",
    "delay": "Delay", "RangedModRange": "RangedModRange", "bonding": "Bonding",
    "LanguageID": "LanguageID", "PageMaterial": "PageMaterial", "startquest": "StartQuest",
    "lockid": "LockID", "Material": "Material", "sheath": "SheatheType",
    "RandomProperty": "RandomProperty", "RandomSuffix": "RandomSuffix", "itemset": "ItemSet",
    "area": "AreaID", "Map": "MapID", "BagFamily": "BagFamily", "TotemCategory": "TotemCategory",
    "socketBonus": "SocketBonus", "GemProperties": "GemProperties",
    "ArmorDamageModifier": "ArmorDamageModifier", "duration": "Duration",
    "ItemLimitCategory": "ItemLimitCategory", "HolidayId": "HolidayID",
}
for i in range(10):
    SIMPLE[f"stat_type{i + 1}"] = f"ItemStatType{i}"
    SIMPLE[f"stat_value{i + 1}"] = f"ItemStatValue{i}"
for i in range(5):
    for ac, sparse in (("spellid", "SpellID"), ("spelltrigger", "SpellTrigger"),
                       ("spellcharges", "SpellCharges"), ("spellcooldown", "SpellCooldown"),
                       ("spellcategory", "SpellCategory"),
                       ("spellcategorycooldown", "SpellCategoryCooldown")):
        SIMPLE[f"{ac}_{i + 1}"] = f"{sparse}{i}"
for i in range(3):
    SIMPLE[f"socketColor_{i + 1}"] = f"SocketColor{i}"
    SIMPLE[f"socketContent_{i + 1}"] = f"SocketContent{i}"

UNSIGNED = ["requiredspell", "startquest", "lockid", "itemset", "area"]
DERIVED = ["armor", "MaxDurability", "dmg_min1", "dmg_max1", "dmg_type1"]
UNRESOLVED = ["block", "dmg_min2", "dmg_max2", "dmg_type2", "ScalingStatValue", "RequiredDisenchantSkill",
              "DisenchantID", "PageText"]
# Cata stat types above AC's highest ItemModType (48) that have their own item_template column. Mastery (49)
# has no AC counterpart and is dropped instead of passed through, where the loader would zero the type and
# apply the value as mana.
RESISTANCES = {51: "fire_res", 52: "frost_res", 53: "holy_res", 54: "shadow_res", 55: "nature_res",
               56: "arcane_res"}
# A DELETE + INSERT resets these AC-owned columns, so only run it for ids absent from item_template.
RESET_NOTE = ("ScriptName, FoodType, flagsCustom, minMoneyLoot, maxMoneyLoot, spellppmRate_*, "
              "StatScalingFactor/ItemStatAllocation (scaling items lose their stats)")


def sql_value(value):
    if isinstance(value, str):
        return "'" + value.replace("\\", "\\\\").replace("'", "''") + "'"
    if isinstance(value, float):
        return repr(round(value, 6))
    return str(value)


def derive(row, sparse, tables):
    """Fill armor, durability and weapon damage at the item's base level (no owner scaling)."""
    args = (row["class"], row["subclass"], row["InventoryType"], row["Quality"], row["ItemLevel"])
    row["armor"] = effective_armor(tables, *args)
    row["MaxDurability"] = max_durability(*args)
    damage = weapon_damage(tables, *args, row["FlagsExtra"], row["delay"], sparse["StatScalingFactor"],
                           sparse["ArmorDamageModifier"])
    row["dmg_min1"], row["dmg_max1"], row["dmg_type1"] = (*damage, sparse["DamageType"]) if damage else (0, 0, 0)


def convert(item_rows, sparse_rows, item_ids, tables=None):
    rows = []
    for item_id in item_ids:
        if item_id not in item_rows or item_id not in sparse_rows:
            raise KeyError(f"item {item_id} missing from Item.db2 or Item-sparse.db2")
        item, sparse = item_rows[item_id], sparse_rows[item_id]
        row = {"entry": item_id, "class": item["ClassID"], "subclass": item["SubclassID"],
               "SoundOverrideSubclass": item["SoundOverrideSubclassID"], "name": sparse["Display"],
               "displayid": item["DisplayInfoID"], "description": sparse["Description"]}
        row.update({ac: sparse[field] for ac, field in SIMPLE.items()})
        for flags in ("Flags", "FlagsExtra"):  # bit 31 reads back negative from the signed parser
            row[flags] &= 0xFFFFFFFF
        if item["ClassID"] != 4:  # AC subtracts this from armor; on Cata weapons it is a damage modifier
            row["ArmorDamageModifier"] = 0
        for column in UNSIGNED:
            if row[column] < 0:
                raise ValueError(f"item {item_id}: {column}={row[column]} does not fit an unsigned column")
        for i in range(1, 11):  # the client marks an empty stat slot -1; AC uses 0
            if row[f"stat_type{i}"] < 0:
                row[f"stat_type{i}"] = row[f"stat_value{i}"] = 0
        for column in RESISTANCES.values():
            row[column] = 0
        if tables:
            derive(row, sparse, tables)
        else:
            row["armor"] = 0
        for i in range(1, 11):
            stat_type, value = row[f"stat_type{i}"], row[f"stat_value{i}"]
            if stat_type in RESISTANCES:
                row[RESISTANCES[stat_type]] += value
            elif stat_type == 50:  # extra armor
                row["armor"] += value
            elif stat_type != 49 and stat_type <= 56:
                continue
            row[f"stat_type{i}"] = row[f"stat_value{i}"] = 0
        rows.append(row)
    return rows


def to_sql(rows, build, derived=False, insert_only=False):
    columns = list(rows[0]) + ["VerifiedBuild"]
    ids = ",".join(str(row["entry"]) for row in rows)
    lines = [f"-- Also reset to default: {RESET_NOTE}. Apply only to ids not already in item_template.",
             f"-- {'Derived: ' + ', '.join(DERIVED) + '. ' if derived else 'Not derived (no --dbc-dir): '
                  + ', '.join(DERIVED) + '. '}"
             f"Left at schema default: {', '.join(UNRESOLVED)}",
             *([] if insert_only else ["START TRANSACTION;",
                                       f"DELETE FROM `item_template` WHERE `entry` IN ({ids});"]),
             f"INSERT INTO `item_template` ({','.join(f'`{c}`' for c in columns)}) VALUES"]
    values = [f"({','.join(sql_value(row[c]) for c in columns[:-1])},{build})" for row in rows]
    lines.append(",\n".join(values) + ";")
    if not insert_only:
        lines.append("COMMIT;")
    return "\n".join(lines) + "\n"


def report_item_ids(handle):
    """Item ids from an import report; log text (even with braces) may precede the JSON object."""
    text = handle.read()
    decoder = json.JSONDecoder()
    for start in (i for i, char in enumerate(text) if char == "{"):
        try:
            report, _ = decoder.raw_decode(text, start)
        except ValueError:
            continue
        if isinstance(report, dict):
            closure = report.get("closure") or {}
            return sorted(int(item) for item in closure.get("missing_items") or [])
    raise ValueError("no JSON report object found")


def self_test():
    item = {1: {"ClassID": 4, "SubclassID": 1, "SoundOverrideSubclassID": -1, "DisplayInfoID": 9}}
    sparse = {1: {"Display": "Bob's Vest", "Description": "", **{field: 0 for field in SIMPLE.values()}}}
    rows = convert(item, sparse, [1])
    sql = to_sql(rows, 15595)
    assert rows[0]["name"] == "Bob's Vest" and "'Bob''s Vest'" in sql
    sparse[1]["ItemStatType0"], sparse[1]["ItemStatValue0"] = -1, 5
    assert convert(item, sparse, [1])[0]["stat_type1"] == 0 == convert(item, sparse, [1])[0]["stat_value1"]
    sparse[1].update({"ItemStatType0": 51, "ItemStatValue0": 18, "ItemStatType1": 56, "ItemStatValue1": 18,
                      "ItemStatType2": 50, "ItemStatValue2": 7, "ItemStatType3": 49, "ItemStatValue3": 5})
    translated = convert(item, sparse, [1])[0]
    assert (translated["fire_res"], translated["arcane_res"], translated["armor"]) == (18, 18, 7)
    assert [translated[f"stat_type{i}"] for i in range(1, 5)] == [0, 0, 0, 0]
    assert "DELETE" not in to_sql(rows, 15595, insert_only=True)
    assert sql.count("DELETE FROM") == 1 and "VALUES" in sql and sql.rstrip().endswith("COMMIT;")
    sparse[1]["Flags"] = -2147483648
    assert convert(item, sparse, [1])[0]["Flags"] == 2147483648
    sparse[1]["LockID"] = -1
    try:
        convert(item, sparse, [1])
        raise AssertionError("expected a negative unsigned value to be rejected")
    except ValueError:
        pass
    sparse[1]["LockID"] = 0
    try:
        convert(item, sparse, [2])
        raise AssertionError("expected KeyError for a missing item")
    except KeyError:
        pass
    flat = {name: {5: (5,) + (2.0,) * 7 + (5,)} for name in ("ItemArmorQuality", "ItemDamageRanged")}
    flat["ItemArmorTotal"] = {5: (5, 5, 10.0, 20.0, 30.0, 40.0)}
    flat["ArmorLocation"] = {7: (7, 0.5, 0.5, 0.5, 0.5, 0.5), 5: (5, 1.0, 1.0, 1.0, 1.0, 1.0)}
    assert effective_armor(flat, 4, 1, 20, 1, 5) == int(2.0 * 10.0 * 1.0 + 0.5)  # robe uses the chest row
    assert effective_armor(flat, 4, 2, 7, 1, 5) == int(2.0 * 20.0 * 0.5 + 0.5)
    shirt = {"ItemLevel": 5, "StatScalingFactor": 0.0, "ArmorDamageModifier": 0.0, "DamageType": 0}
    mixed = convert(item, {1: {**sparse[1], **shirt}}, [1], flat)  # an armor first row must keep the damage columns
    assert {"dmg_min1", "dmg_max1", "dmg_type1"} <= set(mixed[0])
    low, high = weapon_damage(flat, 2, 18, 26, 1, 5, 0, 2000, 0.0, 0.0)
    assert (low, high) == (4.0, 4.0)  # delay 2000ms * dps 2.0 / 1000
    assert max_durability(4, 1, 5, 1, 40) == 5 * int(23.0 * 1.0 * 1.0 + 0.5)
    import io
    assert report_item_ids(io.StringIO('log\n{"closure": {"missing_items": ["58363", "52558"]}}')) == [52558, 58363]
    assert report_item_ids(io.StringIO('{"zone": 12}')) == []
    assert report_item_ids(io.StringIO('opts={x} log\n{"closure": {"missing_items": ["7"]}}')) == [7]
    assert report_item_ids(io.StringIO('{"closure": null}')) == []
    for bad in ("no json", '{"closure": {"missing_items": ["abc"]}}'):
        try:
            report_item_ids(io.StringIO(bad))
            raise AssertionError("expected a bad report to be rejected")
        except ValueError:
            pass
    print("convert_items_from_db2 self-test PASS.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("item_db2", nargs="?", type=argparse.FileType("rb"))
    parser.add_argument("item_sparse_db2", nargs="?", type=argparse.FileType("rb"))
    parser.add_argument("item_ids", nargs="*", type=int)
    parser.add_argument("--build", type=int, default=15595)
    parser.add_argument("--all", action="store_true",
                        help="convert every item present in both DB2 files (world-wide item_template)")
    parser.add_argument("--insert-only", action="store_true",
                        help="plain INSERT for brand-new ids (the SQL linter forbids DELETE on item_template)")
    parser.add_argument("--ids-from-report", type=argparse.FileType("r"),
                        help="import_starter_content.py JSON report; its closure.missing_items are converted")
    parser.add_argument("--dbc-dir", help="directory with the ItemArmor*/ItemDamage*/ArmorLocation .dbc files")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        return self_test()
    try:
        report_ids = report_item_ids(args.ids_from_report) if args.ids_from_report else []
    except ValueError as error:
        parser.error(f"--ids-from-report: {error}")
    item_ids = sorted(set(args.item_ids) | set(report_ids))
    if not (args.item_db2 and args.item_sparse_db2 and (item_ids or args.all)):
        parser.error("item_db2, item_sparse_db2 and at least one item id, --all or a report with "
                     "missing items are required")
    item_rows = item_records(args.item_db2.read())
    sparse_rows = sparse_records(args.item_sparse_db2.read())
    if args.all:
        item_ids = sorted(set(item_rows) & set(sparse_rows))
    tables = load_tables(args.dbc_dir) if args.dbc_dir else None
    rows = convert(item_rows, sparse_rows, item_ids, tables)
    sys.stdout.write(to_sql(rows, args.build, bool(tables), args.insert_only))


if __name__ == "__main__":
    sys.exit(main())
