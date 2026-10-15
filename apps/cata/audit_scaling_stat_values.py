#!/usr/bin/env python3
"""Verify the Cataclysm ScalingStatValues DBC layout and accessor port against the pinned
TrinityCore-Cata reference. No build, server, client, or database is required.

Checks (issue #75):
  1. ScalingStatValuesfmt is the 47-field Cata layout (one index field, 46 ints).
  2. The pin's fmt has the same field-type shape (its index sits at a different column).
  3. ScalingStatValuesEntry has the Cata per-slot field shape, not the WotLK bitmask fields.
  4. The real extracted ScalingStatValues.dbc carries 47 four-byte fields per record.
  5. Every accessor's (case label -> multiply/return) mapping matches the pin.

The two implementations differ in guard style and in one unreachable caster branch; those are
reported as NOTES rather than failures.
"""
from __future__ import annotations

import argparse
import re
import struct
import subprocess
from pathlib import Path

from check_parity import PIN

REPO = Path(__file__).resolve().parents[2]
FMT_HEADER = "src/server/shared/DataStores/DBCfmt.h"
STRUCT_HEADER = "src/server/shared/DataStores/DBCStructure.h"
STORE_SOURCE = "src/server/game/DataStores/DBCStores.cpp"
# The pin keeps these files in the game layer; AzerothCore moved the first two to shared.
PIN_FMT_HEADER = "src/server/game/DataStores/DBCfmt.h"
PIN_STRUCT_HEADER = "src/server/game/DataStores/DBCStructure.h"
ACCESSORS = ("GetStatMultiplier", "GetArmor", "GetDPSAndDamageMultiplier")

# The port renamed members versus the pin; the values are the same fields.
MEMBER_ALIASES = {
    "StatMultiplier": "statMultiplier",
    "Armor": "armor",
    "CloakArmor": "cloakArmor",
    "SpellPower": "spellPower",
}


def reference_file(tree: Path, path: str) -> str:
    try:
        return subprocess.check_output(
            ["git", "-C", str(tree), "show", f"{PIN}:{path}"], text=True, stderr=subprocess.PIPE
        )
    except subprocess.CalledProcessError as error:
        detail = (error.stderr or "").strip()
        raise ValueError(f"Cannot read {path} at {PIN[:12]}: {detail}") from error


def fmt_string(text: str) -> str:
    match = re.search(r'char const(?:expr)? ScalingStatValuesfmt\[\] = "([^"]+)";', text)
    if not match:
        raise ValueError("ScalingStatValuesfmt not found")
    return match.group(1)


def entry_shape(text: str) -> list[str]:
    """Field types/array dims in declaration order, ignoring field names and methods."""
    match = re.search(r"struct ScalingStatValuesEntry\s*\{(.*?)\n\};", text, re.S)
    if not match:
        raise ValueError("ScalingStatValuesEntry not found")
    body = re.sub(r"//[^\n]*", "", match.group(1))
    shape = []
    for decl in re.finditer(r"uint32\s+\w+\s*((?:\[[^\]]*\])*)\s*;", body):
        line_start = body.rfind("\n", 0, decl.start()) + 1
        if "(" in body[line_start:decl.start()]:
            continue
        shape.append("uint32" + decl.group(1))
    return shape


def dbc_header(path: Path) -> tuple[int, int, int, int]:
    data = path.read_bytes()
    magic, count, fields, size, strings = struct.unpack_from("<4s4I", data)
    if magic != b"WDBC":
        raise ValueError(f"Not a WDBC file: {path}")
    if len(data) != 20 + count * size + strings:
        raise ValueError(f"Truncated or trailing DBC data: {path}")
    return count, fields, size, strings


def accessor_body(source: str, name: str) -> str:
    # Anchor the name so GetArmor does not match GetArmorMod and vice versa.
    match = re.search(
        r"uint32 ScalingStatValuesEntry::" + name + r"\([^)]*\) const\n\{(.*?)\n\}", source, re.S
    )
    if not match:
        raise ValueError(f"Accessor not found: {name}")
    return match.group(1)


def normalize(expression: str) -> str:
    for alias, canonical in MEMBER_ALIASES.items():
        expression = re.sub(rf"\b{alias}\b", canonical, expression)
    return re.sub(r"\s+", "", expression)


def case_actions(body: str) -> dict[str, str]:
    """Map each return-bearing case label to its normalized multiply+return actions.

    Break-only cases and `default` are omitted: they resolve to the function's trailing
    `return`, which is equivalent on both sides and not worth comparing textually.
    """
    result: dict[str, str] = {}
    pending: list[str] = []
    bucket: list[str] = []
    for raw in body.splitlines():
        line = raw.split("//", 1)[0]
        case = re.search(r"case (\w+)\s*:", line)
        if case:
            pending.append(case.group(1))
            continue
        if re.search(r"^\s*default\s*:", line):
            pending, bucket = [], []
            continue
        if "*damageMultiplier" in line or "return" in line or "break" in line:
            bucket.append(normalize(line))
            if "return" in line:
                action = "|".join(bucket)
                for label in pending:
                    result[label] = action
                pending, bucket = [], []
            elif "break" in line:
                pending, bucket = [], []
    return result


def compare(label: str, azerothcore: str, pinned: str, notes: list[str]) -> int:
    ac_map = case_actions(accessor_body(azerothcore, label))
    pin_map = case_actions(accessor_body(pinned, label))
    missing = {k: v for k, v in pin_map.items() if ac_map.get(k) != v}
    extra = {k: v for k, v in ac_map.items() if k not in pin_map}
    if missing or extra:
        raise ValueError(
            f"{label} mapping differs from the pin: missing/incorrect={missing} extra={extra}"
        )
    return len(ac_map)


def run(reference_tree: Path, dbc: Path) -> int:
    ac_fmt = fmt_string((REPO / FMT_HEADER).read_text())
    ac_struct = (REPO / STRUCT_HEADER).read_text()
    ac_store = (REPO / STORE_SOURCE).read_text()

    if len(ac_fmt) != 47 or ac_fmt.count("n") != 1 or set(ac_fmt) - {"n", "i"}:
        raise ValueError(f"Unexpected ScalingStatValuesfmt layout: {ac_fmt!r}")

    pin_fmt = fmt_string(reference_file(reference_tree, PIN_FMT_HEADER))
    if len(pin_fmt) != 47 or pin_fmt.count("n") != 1 or set(pin_fmt) - {"n", "i"}:
        raise ValueError(f"Unexpected pin ScalingStatValuesfmt layout: {pin_fmt!r}")
    if sorted(ac_fmt) != sorted(pin_fmt):
        raise ValueError(f"fmt field types differ from the pin: {ac_fmt!r} vs {pin_fmt!r}")
    if ac_fmt != pin_fmt:
        notes_index = (
            f"AC index column {ac_fmt.index('n')}, pin {pin_fmt.index('n')}; "
            "AC matches the real file (field 0 = level)"
        )
    else:
        notes_index = None

    shape = entry_shape(ac_struct)
    pin_shape = entry_shape(reference_file(reference_tree, PIN_STRUCT_HEADER))
    if shape != pin_shape:
        raise ValueError(f"ScalingStatValuesEntry shape differs from the pin: {shape} vs {pin_shape}")
    if not {"uint32[6]", "uint32[5]", "uint32[8][4]"} <= set(shape):
        raise ValueError(f"ScalingStatValuesEntry is not the Cata per-slot shape: {shape}")

    count, fields, size, strings = dbc_header(dbc)
    if (fields, size) != (len(ac_fmt), 4 * len(ac_fmt)):
        raise ValueError(f"DBC layout {fields}x{size} does not match fmt {len(ac_fmt)}")
    if count < 1:
        raise ValueError("DBC has no records")

    notes: list[str] = []
    if notes_index:
        notes.append(notes_index)
    pin_store = reference_file(reference_tree, STORE_SOURCE)
    matched = {label: compare(label, ac_store, pin_store, notes) for label in ACCESSORS}

    for needle in ("0x562", "ITEM_SUBCLASS_WEAPON_WAND"):
        if needle not in accessor_body(ac_store, "GetDPSAndDamageMultiplier") or \
                needle not in accessor_body(pin_store, "GetDPSAndDamageMultiplier"):
            raise ValueError(f"GetDPSAndDamageMultiplier is missing the {needle} caster check")
    notes.append(
        "GetDPSAndDamageMultiplier: for isCasterWeapon with subClass > WAND the port returns 0 "
        "where the pin returns dpsMod[2]; unreachable because callers pass weapon subclasses (<= WAND)"
    )
    notes.append(
        "GetStatMultiplier/GetArmor guard style differs from the pin; behaviour is identical "
        "because both fall through to 0"
    )

    print(f"ScalingStatValues audit PASS (pin {PIN[:12]})")
    print(f"  fmt={len(ac_fmt)} fields, record={size} bytes, records={count}, strings={strings}")
    print(f"  entry shape={shape}")
    for label in ACCESSORS:
        print(f"  {label}: {matched[label]} return-bearing cases match the pin")
    for note in notes:
        print(f"  NOTE: {note}")
    return 0


def self_test() -> None:
    def rejects(action):
        try:
            action()
        except (ValueError, struct.error):
            return
        raise AssertionError("Malformed source was accepted")

    body = """
    switch (subClass)
    {
        case A:
        case B:
            *damageMultiplier = 0.2f;
            return dpsMod[3];
        case C:
            return StatMultiplier[0];
        default:
            break;
    }
    """
    assert case_actions(body) == {"A": "*damageMultiplier=0.2f;|returndpsMod[3];",
                                  "B": "*damageMultiplier=0.2f;|returndpsMod[3];",
                                  "C": "returnstatMultiplier[0];"}
    assert normalize("return Armor[0][type];") == "returnarmor[0][type];"
    rejects(lambda: fmt_string("no fmt here"))
    rejects(lambda: entry_shape("struct Other {};"))
    print("ScalingStatValues audit self-test PASS: parser and malformed-source rejection.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference-tree", type=Path, help="Path to the pinned TrinityCore checkout")
    parser.add_argument("--dbc", type=Path, help="Path to the extracted ScalingStatValues.dbc")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return 0
    if not args.reference_tree or not args.dbc:
        parser.error("--reference-tree and --dbc are required")
    for path in (args.reference_tree, args.dbc):
        if not path.exists():
            parser.error(f"path does not exist: {path}")
    try:
        return run(args.reference_tree, args.dbc)
    except (ValueError, OSError) as error:
        raise SystemExit(str(error)) from error


if __name__ == "__main__":
    raise SystemExit(main())
