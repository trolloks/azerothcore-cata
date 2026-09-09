"""Parse the native Cataclysm Item.db2 (WDB2) and print ItemEntry rows for given item IDs.

Field layout confirmed against TrinityCore-Cata's ItemEntry struct
(src/server/game/DataStores/DB2Structure.h, pinned commit c699217775d90794158422387b07a917e161b582):
ID, ClassID, SubclassID, SoundOverrideSubclassID, Material, DisplayInfoID, InventoryType, SheatheType.
"""
import argparse
import struct

FIELDS = ("ID", "ClassID", "SubclassID", "SoundOverrideSubclassID", "Material",
          "DisplayInfoID", "InventoryType", "SheatheType")


def db2_records(data):
    if data[:4] != b"WDB2":
        raise ValueError("Not a WDB2 file")
    record_count, field_count, record_size, string_size = struct.unpack_from("<4I", data, 4)
    if field_count != len(FIELDS) or record_size != len(FIELDS) * 4:
        raise ValueError(f"Unexpected Item.db2 layout: fields={field_count} size={record_size}")
    off = 20 + 12  # WDBC header + tableHash/build/unk1
    _, max_index, _, _ = struct.unpack_from("<4i", data, off)
    off += 16
    if max_index != 0:
        raise ValueError("Item.db2 index-lookup table is present but not implemented")
    records_start = off
    expected_size = records_start + record_size * record_count + string_size
    if len(data) != expected_size:
        raise ValueError(f"Truncated or trailing Item.db2 data: {len(data)} != {expected_size}")
    return {values[0]: dict(zip(FIELDS, values))
            for i in range(record_count)
            for values in (struct.unpack_from("<8i", data, records_start + i * record_size),)}


def self_test():
    header = struct.pack("<4s4I3I4i", b"WDB2", 2, 8, 32, 0, 0, 15595, 0, 0, 0, 0, 0)
    rows = [(58231, 4, 1, -1, 7, 33310, 5, 0), (39, 4, 1, -1, 7, 9892, 7, 0)]
    data = header + b"".join(struct.pack("<8i", *row) for row in rows)
    records = db2_records(data)
    assert records[58231]["ClassID"] == 4 and records[58231]["DisplayInfoID"] == 33310
    assert records[39]["InventoryType"] == 7
    try:
        db2_records(b"WDB3" + data[4:])
        raise AssertionError("expected rejection of non-WDB2 magic")
    except ValueError:
        pass
    print("Item.db2 audit self-test PASS.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("item_db2", nargs="?", type=argparse.FileType("rb"))
    parser.add_argument("item_ids", nargs="*", type=int)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return
    if not args.item_db2:
        parser.error("item_db2 is required unless --self-test is given")
    records = db2_records(args.item_db2.read())
    for item_id in args.item_ids:
        print(item_id, records.get(item_id, "NOT FOUND"))


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError) as error:
        raise SystemExit(str(error)) from error
