"""Check Gilneas campaign spell admission against native client data, without a server."""

import argparse
import hashlib
import json
from pathlib import Path
import struct

from audit_character_starting_data import dbc_rows
from audit_native_spells import supported_effect

REQUIRED = {68630, 68992, 68996, 72788, 81040, 93477, 94293, 97709, 98274}


def audit(dbc_dir):
    paths = {name: dbc_dir / (name + ".dbc") for name in ("Spell", "SpellEffect")}
    spells = {struct.unpack_from("<I", row)[0] for row in dbc_rows(paths["Spell"], 48, 192)}
    effects = [struct.unpack("<27I", row) for row in dbc_rows(paths["SpellEffect"], 27, 108)]
    rejected = sorted({row[24] for row in effects if row[24] in REQUIRED and not supported_effect(row)})
    return {"missing": sorted(REQUIRED - spells), "rejected": rejected,
            "required": sorted(REQUIRED),
            "sha256": {name: hashlib.sha256(path.read_bytes()).hexdigest() for name, path in paths.items()}}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dbc-dir", required=True, type=Path)
    args = parser.parse_args()
    result = audit(args.dbc_dir)
    print(json.dumps(result, indent=2))
    raise SystemExit(bool(result["missing"] or result["rejected"]))
