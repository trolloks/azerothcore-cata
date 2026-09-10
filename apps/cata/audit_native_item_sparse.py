"""Parse the native Cataclysm Item-sparse.db2 (WDB2, sparse/offset-indexed) and print
ItemSparseEntry rows for given item IDs.

Field layout confirmed against TrinityCore-Cata's ItemSparseEntry struct and ItemSparsefmt
format string (src/server/game/DataStores/DB2Structure.h and DB2fmt.h, pinned commit
c699217775d90794158422387b07a917e161b582): 133 fields (1 id + 122 int32 + 5 float + 5 string).

Unlike the plain Item.db2 (ItemEntry), this file's header carries a min/max ID range and,
when max_id != 0, an offset-map table of (max_id - min_id + 1) 6-byte slots (uint32 offset +
uint16 length, per id) sits between the header and the fixed-size record array — present so
the client can locate a record for a mostly-empty ID space, but its contents aren't needed
here since every record already carries its own ID as field 0; we just skip its bytes.
Records are followed immediately by the string block that the 5 string fields
(Display/Display1/Display2/Display3/Description) offset into.
"""
import argparse
import struct

FIELDS = (
    "ID", "Quality", "Flags", "Flags2", "PriceRandomValue", "PriceVariance", "BuyCount", "BuyPrice",
    "SellPrice", "InventoryType", "AllowableClass", "AllowableRace", "ItemLevel", "RequiredLevel",
    "RequiredSkill", "RequiredSkillRank", "RequiredSpell", "RequiredHonorRank", "RequiredCityRank",
    "RequiredReputationFaction", "RequiredReputationRank", "MaxCount", "Stackable", "ContainerSlots",
    *(f"ItemStatType{i}" for i in range(10)),
    *(f"ItemStatValue{i}" for i in range(10)),
    *(f"ItemStatAllocation{i}" for i in range(10)),
    *(f"ItemStatSocketCostMultiplier{i}" for i in range(10)),
    "ScalingStatDistribution", "DamageType", "Delay", "RangedModRange",
    *(f"SpellID{i}" for i in range(5)),
    *(f"SpellTrigger{i}" for i in range(5)),
    *(f"SpellCharges{i}" for i in range(5)),
    *(f"SpellCooldown{i}" for i in range(5)),
    *(f"SpellCategory{i}" for i in range(5)),
    *(f"SpellCategoryCooldown{i}" for i in range(5)),
    "Bonding", "Display", "Display1", "Display2", "Display3", "Description",
    "PageText", "LanguageID", "PageMaterial", "StartQuest", "LockID", "Material", "SheatheType",
    "RandomProperty", "RandomSuffix", "ItemSet", "AreaID", "MapID", "BagFamily", "TotemCategory",
    *(f"SocketColor{i}" for i in range(3)),
    *(f"SocketContent{i}" for i in range(3)),
    "SocketBonus", "GemProperties", "ArmorDamageModifier", "Duration", "ItemLimitCategory", "HolidayID",
    "StatScalingFactor", "CurrencySubstitutionID", "CurrencySubstitutionCount",
)
assert len(FIELDS) == 133
STRING_FIELDS = {"Display", "Display1", "Display2", "Display3", "Description"}
FLOAT_FIELDS = {"PriceRandomValue", "PriceVariance", "RangedModRange", "ArmorDamageModifier", "StatScalingFactor"}
RECORD_SIZE = len(FIELDS) * 4


def db2_records(data):
    if data[:4] != b"WDB2":
        raise ValueError("Not a WDB2 file")
    record_count, field_count, record_size, string_size = struct.unpack_from("<4I", data, 4)
    if field_count != len(FIELDS) or record_size != RECORD_SIZE:
        raise ValueError(f"Unexpected Item-sparse.db2 layout: fields={field_count} size={record_size}")
    off = 20 + 12  # WDBC header + tableHash/build/timestamp
    min_id, max_id, _locale, copy_table_size = struct.unpack_from("<4i", data, off)
    off += 16
    if copy_table_size:
        raise ValueError("Item-sparse.db2 copy table is present but not implemented")
    if max_id != 0:
        off += (max_id - min_id + 1) * 6  # offset-map table: skippable, records carry their own ID
    records_start = off
    strings_start = records_start + record_size * record_count
    expected_size = strings_start + string_size
    if len(data) != expected_size:
        raise ValueError(f"Truncated or trailing Item-sparse.db2 data: {len(data)} != {expected_size}")

    records = {}
    for i in range(record_count):
        base = records_start + i * record_size
        row = {}
        for fi, name in enumerate(FIELDS):
            field_off = base + fi * 4
            if name in STRING_FIELDS:
                str_off = struct.unpack_from("<i", data, field_off)[0]
                abs_off = strings_start + str_off
                end = data.find(b"\x00", abs_off)
                row[name] = data[abs_off:end].decode("utf-8", "replace")
            elif name in FLOAT_FIELDS:
                row[name] = struct.unpack_from("<f", data, field_off)[0]
            else:
                row[name] = struct.unpack_from("<i", data, field_off)[0]
        records[row["ID"]] = row
    return records


def self_test():
    name_block = b"\x00Recruit's Vest\x00Hearthstone\x00"
    display_off_1 = 1
    display_off_2 = name_block.index(b"Hearthstone")

    def make_row(item_id, quality, inv_type, buy_price, display_off):
        row = [0] * len(FIELDS)
        row[FIELDS.index("ID")] = item_id
        row[FIELDS.index("Quality")] = quality
        row[FIELDS.index("InventoryType")] = inv_type
        row[FIELDS.index("BuyPrice")] = buy_price
        row[FIELDS.index("AllowableClass")] = -1
        row[FIELDS.index("AllowableRace")] = -1
        row[FIELDS.index("Display")] = display_off
        for name in ("Display1", "Display2", "Display3", "Description"):
            row[FIELDS.index(name)] = 0
        packed = b""
        for fi, name in enumerate(FIELDS):
            if name in FLOAT_FIELDS:
                packed += struct.pack("<f", row[fi])
            else:
                packed += struct.pack("<i", row[fi])
        return packed

    rows = [make_row(58231, 1, 5, 7, display_off_1), make_row(6948, 1, 0, 0, display_off_2)]
    record_count = len(rows)
    min_id, max_id = 6948, 58231  # arbitrary non-zero range, forces offset-map skip
    offset_map = b"\x00" * ((max_id - min_id + 1) * 6)
    header = struct.pack("<4s4I3I4i", b"WDB2", record_count, len(FIELDS), RECORD_SIZE, len(name_block),
                          0, 15595, 0, min_id, max_id, 1, 0)
    data = header + offset_map + b"".join(rows) + name_block
    records = db2_records(data)
    assert records[58231]["Quality"] == 1 and records[58231]["Display"] == "Recruit's Vest"
    assert records[6948]["Display"] == "Hearthstone"
    try:
        db2_records(b"WDB3" + data[4:])
        raise AssertionError("expected rejection of non-WDB2 magic")
    except ValueError:
        pass
    print("Item-sparse.db2 audit self-test PASS.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("item_sparse_db2", nargs="?", type=argparse.FileType("rb"))
    parser.add_argument("item_ids", nargs="*", type=int)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return
    if not args.item_sparse_db2:
        parser.error("item_sparse_db2 is required unless --self-test is given")
    records = db2_records(args.item_sparse_db2.read())
    for item_id in args.item_ids:
        print(item_id, records.get(item_id, "NOT FOUND"))


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError) as error:
        raise SystemExit(str(error)) from error
