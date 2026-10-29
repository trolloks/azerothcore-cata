#!/usr/bin/env python3
"""Check active opcode values against the pinned TrinityCore-Cata 4.3.4 table.

Issue #168: several opcodes the fork renamed kept their 3.3.5a numbers, so the server sent packets
under an opcode build 15595 does not decode. Every active opcode (registered CMSG, or SMSG the
server references) must carry a value the pinned table defines. Opcodes that intentionally stay on
the 3.3.5a number until their owning conversion phase runs are listed in the allowlist.

Usage:
    python apps/cata/check_opcode_values.py --trinity-repo /path/to/TrinityCore
    python apps/cata/check_opcode_values.py --trinity-repo ... --write-manifest
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

PIN = "c699217775d90794158422387b07a917e161b582"
OPCODES_H = "src/server/game/Server/Protocol/Opcodes.h"
OPCODES_CPP = "src/server/game/Server/Protocol/Opcodes.cpp"
ALLOWLIST = "plan/04-opcode-legacy-allowlist.tsv"
MANIFEST = "plan/04-opcode-values.tsv"
ALLOWLIST_HEADER = ("direction", "name", "owner", "reason")

ENUM_RE = re.compile(r"enum\s+(\w+)\s*(?::\s*uint16)?\s*\{(.*?)\n\};", re.DOTALL)
DECL_RE = re.compile(r"^\s*([A-Z][A-Z0-9_]*)\s*=\s*(0x[0-9A-Fa-f]+|\d+)\s*,?", re.MULTILINE)
CLIENT_REGISTRATION_RE = re.compile(r"DEFINE_HANDLER\(\s*([A-Z][A-Z0-9_]*)\s*,")
SERVER_REGISTRATION_RE = re.compile(r"DEFINE_SERVER_OPCODE_HANDLER\(\s*([A-Z][A-Z0-9_]*)\s*,")
SENT_RE = re.compile(r"\b(?:SMSG|MSG)_[A-Z0-9_]+\b")
COMMENT_RE = re.compile(r"//[^\n]*|/\*.*?\*/", re.DOTALL)

# TrinityCore enum name -> wire directions it can carry.
TC_ENUM_DIRECTIONS = {
    "OpcodeClient": ("c2s",),
    "OpcodeServer": ("s2c",),
    "OpcodeServerClient": ("c2s", "s2c"),
}
AC_ENUM_DIRECTIONS = {
    "OpcodeClient": "c2s",
    "OpcodeServer": "s2c",
}


def strip_comments(text: str) -> str:
    return COMMENT_RE.sub(lambda m: "".join("\n" if c == "\n" else " " for c in m.group()), text)


def parse_declarations(text: str) -> dict[str, dict[str, int]]:
    """name -> {enum name: value}; MSG_ opcodes may be declared once and aliased per direction."""
    declarations: dict[str, dict[str, int]] = {}
    for enum_name, body in ENUM_RE.findall(text):
        for name, raw_value in DECL_RE.findall(body):
            declarations.setdefault(name, {}).setdefault(enum_name, int(raw_value, 0))
    return declarations


def canonical_values(text: str) -> dict[str, set[int]]:
    values: dict[str, set[int]] = {"c2s": set(), "s2c": set()}
    for enum_name, body in ENUM_RE.findall(text):
        directions = TC_ENUM_DIRECTIONS.get(enum_name)
        if not directions:
            continue
        for _, raw_value in DECL_RE.findall(body):
            value = int(raw_value, 0)
            if not value:
                continue
            for direction in directions:
                values[direction].add(value)
    return values


def normalized_name(name: str) -> str:
    """Pin spellings differ by underscores only (SMSG_LEVELUP_INFO vs SMSG_LEVEL_UP_INFO)."""
    return name.replace("_", "").lower()


def canonical_names(text: str) -> dict[tuple[str, str], tuple[str, int]]:
    """(direction, normalized pin name) -> (pin name, value) for the rename audit."""
    names: dict[tuple[str, str], tuple[str, int]] = {}
    for enum_name, body in ENUM_RE.findall(text):
        directions = TC_ENUM_DIRECTIONS.get(enum_name)
        if not directions:
            continue
        for name, raw_value in DECL_RE.findall(body):
            value = int(raw_value, 0)
            if not value:
                continue
            for direction in directions:
                names.setdefault((direction, normalized_name(name)), (name, value))
    return names


def active_opcodes(repo_root: Path) -> dict[tuple[str, str], int]:
    """(direction, name) -> value for registered CMSG and referenced SMSG/MSG opcodes."""
    header = (repo_root / OPCODES_H).read_text(encoding="utf-8")
    source = strip_comments((repo_root / OPCODES_CPP).read_text(encoding="utf-8"))
    declarations = parse_declarations(header)

    active: dict[tuple[str, str], int] = {}
    for name in CLIENT_REGISTRATION_RE.findall(source):
        value = declaration_value(declarations, name, "c2s")
        if value:
            active[("c2s", name)] = value
    for name in SERVER_REGISTRATION_RE.findall(source):
        value = declaration_value(declarations, name, "s2c")
        if value:
            active[("s2c", name)] = value

    sent: set[str] = set()
    for path in (repo_root / "src/server").rglob("*"):
        if path.suffix not in (".cpp", ".h") or path.name in ("Opcodes.h", "Opcodes.cpp"):
            continue
        sent.update(SENT_RE.findall(path.read_text(encoding="utf-8", errors="ignore")))
    for name in sent:
        value = declaration_value(declarations, name, "s2c")
        if value:
            active[("s2c", name)] = value

    return active


def declaration_value(declarations: dict[str, dict[str, int]], name: str, direction: str) -> int:
    enums = declarations.get(name)
    if not enums:
        return 0
    for enum_name, value in enums.items():
        if AC_ENUM_DIRECTIONS.get(enum_name) == direction:
            return value
    # MSG_ opcodes are declared once and aliased into the opposite direction.
    if name.startswith("MSG_"):
        return next(iter(enums.values()))
    return 0


def parse_allowlist(text: str) -> dict[tuple[str, str], tuple[str, str]]:
    rows: dict[tuple[str, str], tuple[str, str]] = {}
    lines = text.splitlines()
    if not lines or tuple(lines[0].split("\t")) != ALLOWLIST_HEADER:
        raise ValueError(f"{ALLOWLIST} header must be: {' '.join(ALLOWLIST_HEADER)}")
    for number, line in enumerate(lines[1:], start=2):
        if not line or line.startswith("#"):
            continue
        fields = line.split("\t")
        if len(fields) != len(ALLOWLIST_HEADER):
            raise ValueError(f"{ALLOWLIST} line {number} must have {len(ALLOWLIST_HEADER)} tab-separated fields")
        direction, name, owner, reason = fields
        if direction not in ("c2s", "s2c"):
            raise ValueError(f"{ALLOWLIST} line {number} has invalid direction {direction!r}")
        if not name or not owner.startswith("#") or not reason:
            raise ValueError(f"{ALLOWLIST} line {number} needs a name, #issue owner and reason")
        rows[(direction, name)] = (owner, reason)
    return rows


def render_manifest(header: str) -> str:
    declarations = parse_declarations(header)
    rows = {}
    for name, enums in declarations.items():
        for enum_name, value in enums.items():
            if enum_name in AC_ENUM_DIRECTIONS:
                rows[name] = value
    return "".join(f"{name}\t0x{rows[name]:04X}\n" for name in sorted(rows))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", default=str(Path(__file__).resolve().parents[2]))
    parser.add_argument("--trinity-repo", required=True, help="checkout that contains the pinned commit")
    parser.add_argument("--trinity-ref", default=PIN)
    parser.add_argument("--write-manifest", action="store_true", help="rewrite plan/04-opcode-values.tsv")
    args = parser.parse_args(argv)

    repo_root = Path(args.repo_root)
    header = (repo_root / OPCODES_H).read_text(encoding="utf-8")
    manifest = render_manifest(header)
    if args.write_manifest:
        (repo_root / MANIFEST).write_text(manifest, encoding="utf-8")
        print(f"wrote {MANIFEST}")

    try:
        trinity_header = subprocess.run(
            ["git", "show", f"{args.trinity_ref}:{OPCODES_H}"],
            cwd=args.trinity_repo,
            capture_output=True,
            text=True,
            check=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError) as error:
        print(f"cannot read {OPCODES_H} at {args.trinity_ref} from {args.trinity_repo}: {error}", file=sys.stderr)
        return 2

    values = canonical_values(trinity_header)
    names = canonical_names(trinity_header)
    active = active_opcodes(repo_root)
    allowlist = parse_allowlist((repo_root / ALLOWLIST).read_text(encoding="utf-8"))

    findings: list[str] = []
    for (direction, name), value in sorted(active.items()):
        pinned = names.get((direction, normalized_name(name)))
        if pinned and pinned[1] != value and (direction, name) not in allowlist:
            findings.append(
                f"{direction} {name} 0x{value:04X} maps to {pinned[0]} 0x{pinned[1]:04X} in "
                f"{args.trinity_ref[:10]} but keeps a different value"
            )
            continue
        if value in values[direction]:
            continue
        if (direction, name) in allowlist:
            continue
        findings.append(
            f"{direction} {name} 0x{value:04X} is not a {args.trinity_ref[:10]} 4.3.4 value "
            f"and is not in {ALLOWLIST}"
        )

    for direction, name in sorted(allowlist):
        if (direction, name) not in active:
            findings.append(f"{ALLOWLIST}: {direction} {name} is no longer an active opcode")
        elif active[(direction, name)] in values[direction]:
            findings.append(f"{ALLOWLIST}: {direction} {name} now matches the pinned 4.3.4 table; drop the row")

    if (repo_root / MANIFEST).read_text(encoding="utf-8") != manifest:
        findings.append(f"{MANIFEST} is stale; rerun with --write-manifest")

    for finding in findings:
        print(finding)
    print(f"checked {len(active)} active opcodes against {len(values['c2s'])}/{len(values['s2c'])} pinned values")
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
