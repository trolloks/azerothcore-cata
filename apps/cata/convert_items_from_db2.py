#!/usr/bin/env python3
"""Convert native Cataclysm Item.db2 + Item-sparse.db2 rows into AC item_template SQL (#210, slice 1).

Structural fields only. The 4.3.4 client stores no per-item armor, damage, durability or block values;
those derive from the ItemArmor*/ItemDamage* tables and are not computed here. The columns left at their
schema default are listed in the output header so the gap stays visible (slice 2).

Prints DELETE + INSERT SQL for the given item IDs to stdout; it never touches a database.
"""

import argparse
import sys

from audit_native_item_sparse import db2_records as sparse_records
from audit_native_items import db2_records as item_records

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
    "delay": "Delay", "RangedModRange": "RangedModRange", "bonding": "Bonding", "PageText": "PageText",
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

UNRESOLVED = ["armor", "block", "MaxDurability", "dmg_min1", "dmg_max1", "dmg_type1", "dmg_min2",
              "dmg_max2", "dmg_type2", "ScalingStatValue", "RequiredDisenchantSkill", "DisenchantID"]


def sql_value(value):
    if isinstance(value, str):
        return "'" + value.replace("\\", "\\\\").replace("'", "''") + "'"
    if isinstance(value, float):
        return repr(round(value, 6))
    return str(value)


def convert(item_rows, sparse_rows, item_ids):
    rows = []
    for item_id in item_ids:
        if item_id not in item_rows or item_id not in sparse_rows:
            raise KeyError(f"item {item_id} missing from Item.db2 or Item-sparse.db2")
        item, sparse = item_rows[item_id], sparse_rows[item_id]
        row = {"entry": item_id, "class": item["ClassID"], "subclass": item["SubclassID"],
               "SoundOverrideSubclass": item["SoundOverrideSubclassID"], "name": sparse["Display"],
               "displayid": item["DisplayInfoID"], "description": sparse["Description"]}
        row.update({ac: sparse[field] for ac, field in SIMPLE.items()})
        for i in range(1, 11):  # the client marks an empty stat slot -1; AC uses 0
            if row[f"stat_type{i}"] < 0:
                row[f"stat_type{i}"] = row[f"stat_value{i}"] = 0
        rows.append(row)
    return rows


def to_sql(rows, build):
    columns = list(rows[0]) + ["VerifiedBuild"]
    ids = ",".join(str(row["entry"]) for row in rows)
    lines = [f"-- Unresolved (derived from ItemArmor*/ItemDamage*, left at schema default): "
             f"{', '.join(UNRESOLVED)}",
             f"DELETE FROM `item_template` WHERE `entry` IN ({ids});",
             f"INSERT INTO `item_template` ({','.join(f'`{c}`' for c in columns)}) VALUES"]
    values = [f"({','.join(sql_value(row[c]) for c in columns[:-1])},{build})" for row in rows]
    lines.append(",\n".join(values) + ";")
    return "\n".join(lines) + "\n"


def self_test():
    item = {1: {"ClassID": 4, "SubclassID": 1, "SoundOverrideSubclassID": -1, "DisplayInfoID": 9}}
    sparse = {1: {"Display": "Bob's Vest", "Description": "", **{field: 0 for field in SIMPLE.values()}}}
    rows = convert(item, sparse, [1])
    sql = to_sql(rows, 15595)
    assert rows[0]["name"] == "Bob's Vest" and "'Bob''s Vest'" in sql
    sparse[1]["ItemStatType0"], sparse[1]["ItemStatValue0"] = -1, 5
    assert convert(item, sparse, [1])[0]["stat_type1"] == 0 == convert(item, sparse, [1])[0]["stat_value1"]
    assert sql.count("DELETE FROM") == 1 and "VALUES" in sql and sql.rstrip().endswith(";")
    try:
        convert(item, sparse, [2])
        raise AssertionError("expected KeyError for a missing item")
    except KeyError:
        pass
    print("convert_items_from_db2 self-test PASS.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("item_db2", nargs="?", type=argparse.FileType("rb"))
    parser.add_argument("item_sparse_db2", nargs="?", type=argparse.FileType("rb"))
    parser.add_argument("item_ids", nargs="*", type=int)
    parser.add_argument("--build", type=int, default=15595)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        return self_test()
    if not (args.item_db2 and args.item_sparse_db2 and args.item_ids):
        parser.error("item_db2, item_sparse_db2 and at least one item id are required")
    rows = convert(item_records(args.item_db2.read()), sparse_records(args.item_sparse_db2.read()),
                   sorted(set(args.item_ids)))
    sys.stdout.write(to_sql(rows, args.build))


if __name__ == "__main__":
    sys.exit(main())
