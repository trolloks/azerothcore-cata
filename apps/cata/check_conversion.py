#!/usr/bin/env python3

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import re
import subprocess
import sys
from collections import defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


TRINITY_PIN = "c699217775d90794158422387b07a917e161b582"
CATA_JS_PIN = "ab964a0e8dfe50a44fa92716ed05438f4a14dfd3"
CLIENT_SHA256 = "0ea9cc0458cb137f4e0767e8a1896e2042480a4a22397480d7ee64c2e696192a"
OPCODES_H = "src/server/game/Server/Protocol/Opcodes.h"
OPCODES_CPP = "src/server/game/Server/Protocol/Opcodes.cpp"
CATA_JS_OPCODES = "src/server/game/server/opcodes.ts"

LEDGER_COLUMNS = (
    "kind",
    "key",
    "direction",
    "classification",
    "disposition",
    "canonical_name",
    "implementation",
    "payload",
    "fixture",
    "client",
    "owner",
    "cata_ref",
    "cata_js_ref",
    "plan",
    "state",
    "evidence",
    "notes",
)

KINDS = {"meta", "defaults", "block", "file", "opcode", "anchor"}
DIRECTIONS = {"c2s", "s2c", "none", "*"}
CLASSIFICATIONS = {"required-cata", "upstream-adaptation", "accidental-drift", "unverified-wip", "-"}
DISPOSITIONS = {"retain", "relocate", "rewrite", "disable", "delete", "defer", "-"}
IMPLEMENTATIONS = {"wotlk", "experimental", "mapped", "converted", "deferred", "obsolete", "not-applicable", "-"}
PAYLOAD_STATES = {"wotlk", "cata-unverified", "cata-matched", "not-applicable", "-"}
PROOF_STATES = {"missing", "pass", "fail", "inconclusive", "not-required", "-"}
ROW_STATES = {"open", "converted", "deferred", "classified", "1", "-"}


class AuditError(RuntimeError):
    pass


@dataclass(frozen=True)
class Finding:
    severity: str
    code: str
    subject: str
    message: str


@dataclass(frozen=True)
class DiffHunk:
    id: str
    path: str
    header: str
    added: int
    removed: int
    block_hash: str


@dataclass(frozen=True)
class OpcodeDeclaration:
    name: str
    value: int
    declared_directions: tuple[str, ...]
    enum_name: str
    line: int


@dataclass(frozen=True)
class OpcodeRegistration:
    name: str
    direction: str
    status: str
    processing: str
    handler: str
    line: int


@dataclass(frozen=True)
class LedgerRow:
    kind: str
    key: str
    direction: str
    classification: str
    disposition: str
    canonical_name: str
    implementation: str
    payload: str
    fixture: str
    client: str
    owner: str
    cata_ref: str
    cata_js_ref: str
    plan: str
    state: str
    evidence: str
    notes: str


@dataclass(frozen=True)
class Ledger:
    schema: str
    defaults: LedgerRow
    blocks: Mapping[str, LedgerRow]
    files: Mapping[str, LedgerRow]
    opcodes: Mapping[tuple[str, str], LedgerRow]
    anchors: Mapping[str, LedgerRow]


@dataclass(frozen=True)
class Inputs:
    repo_root: Path
    base_ref: str
    head_ref: str
    trinity_repo: Path
    trinity_ref: str
    cata_js_repo: Path
    cata_js_ref: str
    client_exe: Path | None
    client_sha256: str
    ledger_path: Path
    output_format: str


@dataclass(frozen=True)
class AnchorSpec:
    key: str
    path: str
    pattern: str
    target: str
    plan: str


ANCHOR_SPECS = (
    AnchorSpec(
        "build.packet-log",
        "src/server/game/Server/Protocol/PacketLog.cpp",
        r"header\.Build\s*=\s*(\d+)",
        "15595",
        "Protocol foundation",
    ),
    AnchorSpec(
        "build.auth-row",
        "data/sql/base/db_auth/build_info.sql",
        r"\((12340),\s*3,\s*3,\s*5,",
        "15595,4,3,4",
        "Database strategy",
    ),
    AnchorSpec(
        "build.realm-default",
        "data/sql/base/db_auth/realmlist.sql",
        r"gamebuild` int unsigned NOT NULL DEFAULT '(\d+)'",
        "15595",
        "Database strategy",
    ),
    AnchorSpec(
        "build.realm-seed",
        "data/sql/base/db_auth/realmlist.sql",
        r"8085,0,2,1,0,0,(\d+)\);",
        "15595",
        "Database strategy",
    ),
    AnchorSpec(
        "build.expansion",
        "src/server/apps/worldserver/worldserver.conf.dist",
        r"^Expansion\s*=\s*(\d+)$",
        "3",
        "Protocol foundation",
    ),
    AnchorSpec(
        "player.max-level",
        "src/server/shared/DataStores/DBCEnums.h",
        r"DEFAULT_MAX_LEVEL\s+(\d+)",
        "85",
        "Player progression",
    ),
    AnchorSpec(
        "objects.update-fields",
        "src/server/game/Entities/Object/Updates/UpdateFields.h",
        r"Auto generated for version ([^\n]+)",
        "4, 3, 4, 15595",
        "Object model",
    ),
    AnchorSpec(
        "protocol.opcode-model",
        OPCODES_H,
        r"(typedef Opcodes OpcodeClient;)",
        "separate client and server tables",
        "Protocol foundation",
    ),
    AnchorSpec(
        "objects.guid-layout",
        "src/server/game/Entities/Object/ObjectGuid.h",
        r"uint64\(entry\) << (\d+)\) \| \(uint64\(hi\) << 48",
        "Cata object GUID layout",
        "Object model",
    ),
)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audit the Cataclysm conversion baseline")
    parser.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--base-ref", default="upstream/master")
    parser.add_argument("--head-ref", default="HEAD")
    parser.add_argument("--trinity-repo", type=Path)
    parser.add_argument("--trinity-ref", default=TRINITY_PIN)
    parser.add_argument("--cata-js-repo", type=Path)
    parser.add_argument("--cata-js-ref", default=CATA_JS_PIN)
    parser.add_argument("--client-exe", type=Path)
    parser.add_argument("--client-sha256", default=CLIENT_SHA256)
    parser.add_argument("--ledger", type=Path, default=Path("plan/conversion-status.tsv"))
    parser.add_argument("--format", choices=("text", "json"), default="text")
    parser.add_argument("--self-check", action="store_true")
    return parser.parse_args(argv)


def build_inputs(args: argparse.Namespace) -> Inputs:
    repo_root = args.repo_root.resolve()
    if args.trinity_repo is None or args.cata_js_repo is None:
        raise AuditError("--trinity-repo and --cata-js-repo are required")

    ledger_path = args.ledger if args.ledger.is_absolute() else repo_root / args.ledger
    return Inputs(
        repo_root=repo_root,
        base_ref=args.base_ref,
        head_ref=args.head_ref,
        trinity_repo=args.trinity_repo.resolve(),
        trinity_ref=args.trinity_ref,
        cata_js_repo=args.cata_js_repo.resolve(),
        cata_js_ref=args.cata_js_ref,
        client_exe=args.client_exe.resolve() if args.client_exe else None,
        client_sha256=args.client_sha256.lower(),
        ledger_path=ledger_path,
        output_format=args.format,
    )


def run_git(repo: Path, *args: str) -> str:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=repo,
            check=True,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except (OSError, subprocess.CalledProcessError) as error:
        detail = error.stderr.strip() if isinstance(error, subprocess.CalledProcessError) else str(error)
        raise AuditError(f"git {' '.join(args)} failed in {repo}: {detail}") from error
    return result.stdout


def resolve_commit(repo: Path, ref: str) -> str:
    return run_git(repo, "rev-parse", "--verify", f"{ref}^{{commit}}").strip()


def read_ref_file(repo: Path, ref: str, path: str) -> str:
    return run_git(repo, "show", f"{ref}:{path}")


def verify_ancestry(repo: Path, base_ref: str, head_ref: str) -> dict[str, Any]:
    base_sha = resolve_commit(repo, base_ref)
    head_sha = resolve_commit(repo, head_ref)
    ancestor = subprocess.run(
        ["git", "merge-base", "--is-ancestor", base_sha, head_sha],
        cwd=repo,
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    if ancestor.returncode not in (0, 1):
        raise AuditError(ancestor.stderr.decode().strip())
    counts = run_git(repo, "rev-list", "--left-right", "--count", f"{base_sha}...{head_sha}").split()
    return {
        "base_ref": base_ref,
        "base_sha": base_sha,
        "head_ref": head_ref,
        "head_sha": head_sha,
        "upstream_is_ancestor": ancestor.returncode == 0,
        "behind": int(counts[0]),
        "ahead": int(counts[1]),
    }


def parse_diff_hunks(diff_text: str) -> tuple[DiffHunk, ...]:
    path = ""
    header = ""
    changed_lines: list[str] = []
    raw_hunks: list[tuple[str, str, tuple[str, ...]]] = []

    def finish_hunk() -> None:
        nonlocal changed_lines
        if header:
            raw_hunks.append((path, header, tuple(changed_lines)))
        changed_lines = []

    for line in diff_text.splitlines():
        if line.startswith("diff --git "):
            finish_hunk()
            header = ""
            match = re.match(r"diff --git a/(.+) b/(.+)$", line)
            if not match:
                raise AuditError(f"unsupported diff path header: {line}")
            path = match.group(2)
        elif line.startswith("@@ "):
            finish_hunk()
            header = line
        elif header and ((line.startswith("+") and not line.startswith("+++")) or
                         (line.startswith("-") and not line.startswith("---"))):
            changed_lines.append(line)
    finish_hunk()

    occurrences: defaultdict[tuple[str, str], int] = defaultdict(int)
    hunks: list[DiffHunk] = []
    for hunk_path, hunk_header, lines in raw_hunks:
        digest = hashlib.sha256((hunk_path + "\0" + "\n".join(lines)).encode()).hexdigest()
        occurrences[(hunk_path, digest)] += 1
        ordinal = occurrences[(hunk_path, digest)]
        hunk_id = f"{hunk_path}@{digest[:16]}:{ordinal}"
        hunks.append(
            DiffHunk(
                id=hunk_id,
                path=hunk_path,
                header=hunk_header,
                added=sum(line.startswith("+") for line in lines),
                removed=sum(line.startswith("-") for line in lines),
                block_hash=digest,
            )
        )
    return tuple(hunks)


def collect_diff(repo: Path, base_ref: str, head_ref: str) -> tuple[DiffHunk, ...]:
    text = run_git(
        repo,
        "diff",
        "--no-color",
        "--no-ext-diff",
        "--no-renames",
        "--unified=0",
        f"{base_ref}...{head_ref}",
        "--",
    )
    return parse_diff_hunks(text)


def prefix_directions(name: str) -> tuple[str, ...]:
    if name.startswith("CMSG_") or name.startswith("TC9_CMSG_"):
        return ("c2s",)
    if name.startswith("SMSG_"):
        return ("s2c",)
    if name.startswith("MSG_") or name.startswith("UMSG_"):
        return ("c2s", "s2c")
    return ()


def parse_opcode_declarations(
    text: str,
    enum_directions: Mapping[str, tuple[str, ...]],
) -> tuple[OpcodeDeclaration, ...]:
    declarations: list[OpcodeDeclaration] = []
    enum_pattern = re.compile(r"enum\s+(\w+)\s*(?::\s*uint16)?\s*\{(.*?)\n\};", re.DOTALL)
    declaration_pattern = re.compile(
        r"^\s*([A-Z][A-Z0-9_]*)\s*=\s*(0x[0-9A-Fa-f]+|\d+)\s*,?",
        re.MULTILINE,
    )
    for enum_match in enum_pattern.finditer(text):
        enum_name = enum_match.group(1)
        if enum_name not in enum_directions:
            continue
        body = enum_match.group(2)
        body_start = enum_match.start(2)
        for match in declaration_pattern.finditer(body):
            name = match.group(1)
            directions = enum_directions[enum_name] or prefix_directions(name)
            if not directions:
                continue
            line = text.count("\n", 0, body_start + match.start()) + 1
            declarations.append(
                OpcodeDeclaration(
                    name=name,
                    value=int(match.group(2), 0),
                    declared_directions=tuple(directions),
                    enum_name=enum_name,
                    line=line,
                )
            )
    return tuple(declarations)


def parse_opcode_registrations(text: str) -> tuple[OpcodeRegistration, ...]:
    registrations: list[OpcodeRegistration] = []
    client_pattern = re.compile(
        r"DEFINE_HANDLER\(\s*([A-Z][A-Z0-9_]*)\s*,\s*([A-Z][A-Z0-9_]*)\s*,\s*"
        r"([A-Z][A-Z0-9_]*)\s*,\s*&?([A-Za-z0-9_:]+)\s*\)",
        re.DOTALL,
    )
    server_pattern = re.compile(
        r"DEFINE_SERVER_OPCODE_HANDLER\(\s*([A-Z][A-Z0-9_]*)\s*,\s*([A-Z][A-Z0-9_]*)"
        r"(?:\s*,\s*[A-Z][A-Z0-9_]*)?\s*\)",
        re.DOTALL,
    )
    for match in client_pattern.finditer(text):
        registrations.append(
            OpcodeRegistration(
                name=match.group(1),
                direction="c2s",
                status=match.group(2),
                processing=match.group(3),
                handler=match.group(4).removeprefix("&"),
                line=text.count("\n", 0, match.start()) + 1,
            )
        )
    for match in server_pattern.finditer(text):
        registrations.append(
            OpcodeRegistration(
                name=match.group(1),
                direction="s2c",
                status=match.group(2),
                processing="PROCESS_INPLACE",
                handler="-",
                line=text.count("\n", 0, match.start()) + 1,
            )
        )
    return tuple(sorted(registrations, key=lambda row: (row.line, row.name, row.direction)))


def parse_cata_js_opcodes(text: str) -> tuple[OpcodeDeclaration, ...]:
    declarations: list[OpcodeDeclaration] = []
    pattern = re.compile(r"^\s*([A-Z][A-Z0-9_]+)\s*:\s*(0x[0-9A-Fa-f]+|\d+)\s*,", re.MULTILINE)
    for match in pattern.finditer(text):
        directions = prefix_directions(match.group(1))
        if not directions:
            continue
        declarations.append(
            OpcodeDeclaration(
                name=match.group(1),
                value=int(match.group(2), 0),
                declared_directions=directions,
                enum_name="Opcodes",
                line=text.count("\n", 0, match.start()) + 1,
            )
        )
    return tuple(declarations)


def declarations_by_name(rows: Iterable[OpcodeDeclaration]) -> dict[str, OpcodeDeclaration]:
    result: dict[str, OpcodeDeclaration] = {}
    for row in rows:
        previous = result.get(row.name)
        if previous and previous.value != row.value:
            raise AuditError(f"opcode {row.name} has conflicting declaration values")
        result[row.name] = row
    return result


def declarations_by_direction(
    rows: Iterable[OpcodeDeclaration],
) -> dict[tuple[str, str], OpcodeDeclaration]:
    result: dict[tuple[str, str], OpcodeDeclaration] = {}
    for row in rows:
        for direction in row.declared_directions:
            key = (direction, row.name)
            previous = result.get(key)
            if previous and previous.value != row.value:
                raise AuditError(f"opcode {row.name} has conflicting {direction} values")
            result[key] = row
    return result


def detect_collisions(
    registrations: Iterable[OpcodeRegistration],
    values: Mapping[str, int],
) -> dict[str, tuple[dict[str, Any], ...]]:
    by_direction: defaultdict[tuple[str, int], list[str]] = defaultdict(list)
    by_value: defaultdict[int, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
    for registration in registrations:
        value = values.get(registration.name, 0)
        if value == 0:
            continue
        by_direction[(registration.direction, value)].append(registration.name)
        by_value[value][registration.direction].append(registration.name)

    same = [
        {"direction": direction, "value": value, "names": sorted(names)}
        for (direction, value), names in by_direction.items()
        if len(names) > 1
    ]
    opposite = [
        {
            "value": value,
            "c2s": sorted(directions["c2s"]),
            "s2c": sorted(directions["s2c"]),
        }
        for value, directions in by_value.items()
        if directions["c2s"] and directions["s2c"]
    ]
    return {
        "same_direction": tuple(sorted(same, key=lambda row: (row["value"], row["direction"]))),
        "opposite_direction": tuple(sorted(opposite, key=lambda row: row["value"])),
    }


def parse_ledger(text: str) -> Ledger:
    reader = csv.DictReader(io.StringIO(text), delimiter="\t")
    if reader.fieldnames != list(LEDGER_COLUMNS):
        raise AuditError(f"ledger header must be: {' '.join(LEDGER_COLUMNS)}")

    rows: list[LedgerRow] = []
    seen: set[tuple[str, str, str]] = set()
    for number, raw in enumerate(reader, start=2):
        if None in raw or any(value is None or value == "" for value in raw.values()):
            raise AuditError(f"ledger row {number} has the wrong field count or an empty cell")
        row = LedgerRow(**raw)
        if row.kind not in KINDS:
            raise AuditError(f"ledger row {number} has invalid kind {row.kind}")
        if row.direction not in DIRECTIONS:
            raise AuditError(f"ledger row {number} has invalid direction {row.direction}")
        if row.classification not in CLASSIFICATIONS:
            raise AuditError(f"ledger row {number} has invalid classification {row.classification}")
        if row.disposition not in DISPOSITIONS:
            raise AuditError(f"ledger row {number} has invalid disposition {row.disposition}")
        if row.implementation not in IMPLEMENTATIONS:
            raise AuditError(f"ledger row {number} has invalid implementation {row.implementation}")
        if row.payload not in PAYLOAD_STATES:
            raise AuditError(f"ledger row {number} has invalid payload {row.payload}")
        if row.fixture not in PROOF_STATES or row.client not in PROOF_STATES:
            raise AuditError(f"ledger row {number} has an invalid proof state")
        if row.state not in ROW_STATES:
            raise AuditError(f"ledger row {number} has invalid state {row.state}")
        identity = (row.kind, row.key, row.direction)
        if identity in seen:
            raise AuditError(f"ledger row {number} duplicates {identity}")
        seen.add(identity)
        rows.append(row)

    meta = [row for row in rows if row.kind == "meta" and row.key == "schema"]
    defaults = [row for row in rows if row.kind == "defaults" and row.key == "opcode"]
    if len(meta) != 1 or meta[0].state != "1":
        raise AuditError("ledger needs exactly one meta/schema row with state 1")
    if len(defaults) != 1:
        raise AuditError("ledger needs exactly one defaults/opcode row")

    return Ledger(
        schema=meta[0].state,
        defaults=defaults[0],
        blocks={row.key: row for row in rows if row.kind == "block"},
        files={row.key: row for row in rows if row.kind == "file"},
        opcodes={(row.direction, row.key): row for row in rows if row.kind == "opcode"},
        anchors={row.key: row for row in rows if row.kind == "anchor"},
    )


def validate_hunk_coverage(
    hunk_ids: set[str],
    covered_ids: set[str],
) -> tuple[str, ...]:
    return tuple(sorted(hunk_ids - covered_ids))


def validate_ledger(
    ledger: Ledger,
    hunks: Sequence[DiffHunk],
    ledger_relative_path: str,
    active_opcode_keys: set[tuple[str, str]],
    canonical: Mapping[tuple[str, str], OpcodeDeclaration],
) -> tuple[Finding, ...]:
    findings: list[Finding] = []
    current_hunks = {hunk.id: hunk for hunk in hunks}
    covered = {
        hunk.id
        for hunk in hunks
        if hunk.id in ledger.blocks or hunk.path in ledger.files
    }
    for hunk_id in validate_hunk_coverage(set(current_hunks), covered):
        findings.append(Finding("error", "UNCOVERED_HUNK", hunk_id, "committed hunk has no ledger row"))
    for hunk_id in sorted(set(ledger.blocks) - set(current_hunks)):
        findings.append(Finding("error", "STALE_BLOCK", hunk_id, "ledger block does not match the current diff"))
    for path in sorted(ledger.files):
        matching = [hunk for hunk in hunks if hunk.path == path]
        if not matching:
            findings.append(Finding("error", "STALE_FILE", path, "ledger file row is not in the current diff"))
        elif path != ledger_relative_path:
            findings.append(
                Finding("error", "COARSE_FILE_COVERAGE", path, "file coverage is allowed only for the ledger itself")
            )

    aliases: defaultdict[tuple[str, str], list[str]] = defaultdict(list)
    for key, row in sorted(ledger.opcodes.items()):
        if key not in active_opcode_keys:
            findings.append(Finding("error", "STALE_OPCODE", f"{key[0]}:{key[1]}", "opcode override is not active"))
        canonical_name = row.canonical_name if row.canonical_name != "-" else row.key
        if canonical_name != row.key:
            aliases[(row.direction, canonical_name)].append(row.key)
        if (row.direction, canonical_name) not in canonical:
            findings.append(
                Finding(
                    "error",
                    "STALE_ALIAS",
                    f"{row.direction}:{row.key}",
                    f"canonical opcode {canonical_name} does not exist in that direction",
                )
            )
        if row.state == "converted" or row.implementation == "converted":
            valid_fixture = row.fixture in {"pass", "not-required"}
            valid_client = row.client in {"pass", "not-required"}
            if row.payload != "cata-matched" or not valid_fixture or not valid_client:
                findings.append(
                    Finding(
                        "error",
                        "CONVERTED_WITHOUT_PROOF",
                        f"{row.direction}:{row.key}",
                        "converted opcode lacks payload, fixture, or client proof",
                    )
                )
    for key, fork_names in sorted(aliases.items()):
        if len(fork_names) > 1:
            findings.append(
                Finding(
                    "error",
                    "AMBIGUOUS_ALIAS",
                    f"{key[0]}:{key[1]}",
                    f"multiple fork names map here: {', '.join(sorted(fork_names))}",
                )
            )
    return tuple(findings)


def row_with_defaults(defaults: LedgerRow, override: LedgerRow | None) -> LedgerRow:
    return override or defaults


def materialize_opcodes(
    declarations: Sequence[OpcodeDeclaration],
    registrations: Sequence[OpcodeRegistration],
    canonical_rows: Sequence[OpcodeDeclaration],
    cata_js_rows: Sequence[OpcodeDeclaration],
    ledger: Ledger,
) -> tuple[tuple[dict[str, Any], ...], tuple[Finding, ...]]:
    findings: list[Finding] = []
    current = declarations_by_name(declarations)
    canonical = declarations_by_direction(canonical_rows)
    cata_js = declarations_by_direction(cata_js_rows)
    materialized: list[dict[str, Any]] = []

    for registration in registrations:
        declaration = current.get(registration.name)
        if declaration is None:
            findings.append(
                Finding("error", "REGISTRATION_WITHOUT_DECLARATION", registration.name, "registration has no declaration")
            )
            continue
        if declaration.value == 0:
            findings.append(
                Finding("warning", "ZERO_VALUE_REGISTRATION", registration.name, "registration is rejected at startup")
            )
            continue

        override = ledger.opcodes.get((registration.direction, registration.name))
        status = row_with_defaults(ledger.defaults, override)
        canonical_name = status.canonical_name if status.canonical_name != "-" else registration.name
        canonical_declaration = canonical.get((registration.direction, canonical_name))
        cata_js_declaration = cata_js.get((registration.direction, canonical_name))
        if canonical_declaration is None:
            findings.append(
                Finding(
                    "warning",
                    "MISSING_CANONICAL_OPCODE",
                    f"{registration.direction}:{registration.name}",
                    f"no canonical declaration for {canonical_name}",
                )
            )
        declared_match = registration.direction in declaration.declared_directions
        if not declared_match:
            findings.append(
                Finding(
                    "warning",
                    "DIRECTION_CONTRADICTION",
                    registration.name,
                    f"declared {declaration.declared_directions}, registered {registration.direction}",
                )
            )
        materialized.append(
            {
                "name": registration.name,
                "declared_directions": list(declaration.declared_directions),
                "registered_direction": registration.direction,
                "current_value": declaration.value,
                "status": registration.status,
                "processing": registration.processing,
                "handler": registration.handler,
                "source": {"declaration_line": declaration.line, "registration_line": registration.line},
                "canonical_name": canonical_name,
                "canonical_value": canonical_declaration.value if canonical_declaration else None,
                "canonical_match": bool(canonical_declaration and canonical_declaration.value == declaration.value),
                "cata_js_value": cata_js_declaration.value if cata_js_declaration else None,
                "cata_js_match": bool(cata_js_declaration and cata_js_declaration.value == declaration.value),
                "implementation": status.implementation,
                "payload": status.payload,
                "fixture": status.fixture,
                "client": status.client,
                "owner": status.owner,
                "plan": status.plan,
                "state": status.state,
                "evidence": status.evidence,
                "notes": status.notes,
            }
        )
    return tuple(sorted(materialized, key=lambda row: (row["registered_direction"], row["name"]))), tuple(findings)


def comparison_stats(
    current_rows: Sequence[OpcodeDeclaration],
    reference_rows: Sequence[OpcodeDeclaration],
) -> dict[str, int]:
    current = declarations_by_name(current_rows)
    reference = declarations_by_name(reference_rows)
    common = sorted(set(current) & set(reference))
    equal = sum(current[name].value == reference[name].value for name in common)
    return {
        "current": len(current),
        "reference": len(reference),
        "common": len(common),
        "equal": equal,
        "mismatched": len(common) - equal,
        "current_only": len(set(current) - set(reference)),
        "reference_only": len(set(reference) - set(current)),
    }


def scan_anchors(inputs: Inputs, ledger: Ledger) -> tuple[tuple[dict[str, Any], ...], tuple[Finding, ...]]:
    anchors: list[dict[str, Any]] = []
    findings: list[Finding] = []
    for spec in ANCHOR_SPECS:
        text = read_ref_file(inputs.repo_root, inputs.head_ref, spec.path)
        match = re.search(spec.pattern, text, re.MULTILINE)
        current = match.group(1).strip() if match else "not-found"
        row = ledger.anchors.get(spec.key)
        if row is None:
            findings.append(Finding("error", "MISSING_ANCHOR", spec.key, "named anchor has no ledger row"))
            state = "open"
            evidence = "-"
        else:
            state = row.state
            evidence = row.evidence
        anchors.append(
            {
                "key": spec.key,
                "path": spec.path,
                "current": current,
                "target": spec.target,
                "matched_target": current == spec.target,
                "plan": spec.plan,
                "state": state,
                "evidence": evidence,
            }
        )

    current_tree = run_git(inputs.repo_root, "ls-tree", "-r", "--name-only", inputs.head_ref, "src")
    cata_tree = run_git(inputs.trinity_repo, "ls-tree", "-r", "--name-only", inputs.trinity_ref, "src")
    current_db2 = sorted(path for path in current_tree.splitlines() if "DB2" in Path(path).name)
    cata_db2 = sorted(path for path in cata_tree.splitlines() if "DB2" in Path(path).name)
    db2_row = ledger.anchors.get("data.db2-files")
    if db2_row is None:
        findings.append(Finding("error", "MISSING_ANCHOR", "data.db2-files", "named anchor has no ledger row"))
    anchors.append(
        {
            "key": "data.db2-files",
            "path": "src/**/DB2*",
            "current": str(len(current_db2)),
            "target": str(len(cata_db2)),
            "matched_target": current_db2 == cata_db2,
            "plan": "Client data stores",
            "state": db2_row.state if db2_row else "open",
            "evidence": db2_row.evidence if db2_row else "-",
            "current_files": current_db2,
            "target_files": cata_db2,
        }
    )

    byte_buffer = read_ref_file(inputs.repo_root, inputs.head_ref, "src/server/shared/Packets/ByteBuffer.h")
    bit_issues = []
    default_ctor = re.search(r"ByteBuffer\(\)\s*\{(.*?)\n\s*\}", byte_buffer, re.DOTALL)
    move_assign = re.search(r"operator=\(ByteBuffer&& right\).*?\{(.*?)\n\s*\}", byte_buffer, re.DOTALL)
    if default_ctor and "_curbitval" not in default_ctor.group(1):
        bit_issues.append("default-curbitval-uninitialized")
    if move_assign and "_bitpos" not in move_assign.group(1):
        bit_issues.append("move-bit-state-not-copied")
    bit_row = ledger.anchors.get("protocol.byte-buffer-bits")
    if bit_row is None:
        findings.append(
            Finding("error", "MISSING_ANCHOR", "protocol.byte-buffer-bits", "named anchor has no ledger row")
        )
    anchors.append(
        {
            "key": "protocol.byte-buffer-bits",
            "path": "src/server/shared/Packets/ByteBuffer.h",
            "current": ",".join(bit_issues) if bit_issues else "no-known-issue",
            "target": "deterministic-bit-state",
            "matched_target": not bit_issues,
            "plan": "Protocol foundation",
            "state": bit_row.state if bit_row else "open",
            "evidence": bit_row.evidence if bit_row else "-",
        }
    )

    world_socket = read_ref_file(inputs.repo_root, inputs.head_ref, "src/server/game/Server/WorldSocket.cpp")
    compression_issues = []
    if "_compressionStream = new z_stream()" in world_socket:
        compression_issues.append("raw-stream-allocation")
    if "WorldSocket::~WorldSocket() = default" in world_socket:
        compression_issues.append("no-stream-finalizer")
    compression_row = ledger.anchors.get("protocol.compression")
    if compression_row is None:
        findings.append(Finding("error", "MISSING_ANCHOR", "protocol.compression", "named anchor has no ledger row"))
    anchors.append(
        {
            "key": "protocol.compression",
            "path": "src/server/game/Server/WorldSocket.cpp",
            "current": ",".join(compression_issues) if compression_issues else "owned-stream",
            "target": "owned-persistent-zlib-stream",
            "matched_target": not compression_issues,
            "plan": "Protocol foundation",
            "state": compression_row.state if compression_row else "open",
            "evidence": compression_row.evidence if compression_row else "-",
        }
    )
    return tuple(sorted(anchors, key=lambda row: row["key"])), tuple(findings)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_client(path: Path | None, expected: str) -> tuple[dict[str, Any], tuple[Finding, ...]]:
    if path is None:
        return {"checked": False, "expected_sha256": expected}, (
            Finding("warning", "CLIENT_NOT_CHECKED", "client", "no client executable was supplied"),
        )
    if not path.is_file():
        return {"checked": False, "path": str(path), "expected_sha256": expected}, (
            Finding("error", "CLIENT_MISSING", str(path), "client executable does not exist"),
        )
    actual = sha256_file(path)
    findings = () if actual == expected else (
        Finding("error", "CLIENT_HASH_MISMATCH", str(path), f"expected {expected}, got {actual}"),
    )
    return {
        "checked": True,
        "path": str(path),
        "expected_sha256": expected,
        "actual_sha256": actual,
        "match": actual == expected,
    }, findings


def stable_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n"


def finding_dict(row: Finding) -> dict[str, str]:
    return asdict(row)


def make_report(inputs: Inputs) -> dict[str, Any]:
    ancestry = verify_ancestry(inputs.repo_root, inputs.base_ref, inputs.head_ref)
    trinity_sha = resolve_commit(inputs.trinity_repo, inputs.trinity_ref)
    cata_js_sha = resolve_commit(inputs.cata_js_repo, inputs.cata_js_ref)
    ledger = parse_ledger(inputs.ledger_path.read_text(encoding="utf-8"))
    hunks = collect_diff(inputs.repo_root, inputs.base_ref, inputs.head_ref)

    current_h = read_ref_file(inputs.repo_root, inputs.head_ref, OPCODES_H)
    current_cpp = read_ref_file(inputs.repo_root, inputs.head_ref, OPCODES_CPP)
    upstream_h = read_ref_file(inputs.repo_root, inputs.base_ref, OPCODES_H)
    trinity_h = read_ref_file(inputs.trinity_repo, inputs.trinity_ref, OPCODES_H)
    cata_js_text = read_ref_file(inputs.cata_js_repo, inputs.cata_js_ref, CATA_JS_OPCODES)

    current_declarations = parse_opcode_declarations(current_h, {"Opcodes": ()})
    upstream_declarations = parse_opcode_declarations(upstream_h, {"Opcodes": ()})
    canonical_declarations = parse_opcode_declarations(
        trinity_h,
        {"OpcodeClient": ("c2s",), "OpcodeServer": ("s2c",), "OpcodeServerClient": ("c2s", "s2c")},
    )
    cata_js_declarations = parse_cata_js_opcodes(cata_js_text)
    registrations = parse_opcode_registrations(current_cpp)
    current_values = {row.name: row.value for row in current_declarations}
    active_keys = {
        (row.direction, row.name)
        for row in registrations
        if current_values.get(row.name, 0) != 0
    }
    canonical_by_direction = declarations_by_direction(canonical_declarations)

    findings: list[Finding] = []
    if not ancestry["upstream_is_ancestor"]:
        findings.append(Finding("error", "UPSTREAM_NOT_ANCESTOR", inputs.head_ref, "base is not an ancestor"))
    try:
        ledger_relative = inputs.ledger_path.relative_to(inputs.repo_root).as_posix()
    except ValueError:
        ledger_relative = str(inputs.ledger_path)
    findings.extend(validate_ledger(ledger, hunks, ledger_relative, active_keys, canonical_by_direction))
    opcodes, opcode_findings = materialize_opcodes(
        current_declarations,
        registrations,
        canonical_declarations,
        cata_js_declarations,
        ledger,
    )
    findings.extend(opcode_findings)
    collisions = detect_collisions(registrations, current_values)
    for row in collisions["same_direction"]:
        findings.append(
            Finding(
                "warning",
                "SAME_DIRECTION_COLLISION",
                f"{row['direction']}:0x{row['value']:04X}",
                ", ".join(row["names"]),
            )
        )
    for row in collisions["opposite_direction"]:
        findings.append(
            Finding(
                "warning",
                "OPPOSITE_DIRECTION_COLLISION",
                f"0x{row['value']:04X}",
                f"c2s={','.join(row['c2s'])}; s2c={','.join(row['s2c'])}",
            )
        )
    anchors, anchor_findings = scan_anchors(inputs, ledger)
    findings.extend(anchor_findings)
    client, client_findings = verify_client(inputs.client_exe, inputs.client_sha256)
    findings.extend(client_findings)

    zero_value = sorted(row.name for row in registrations if current_values.get(row.name, 0) == 0)
    errors = sum(row.severity == "error" for row in findings)
    warnings = sum(row.severity == "warning" for row in findings)
    return {
        "schema": 1,
        "inputs": {
            "repo_root": str(inputs.repo_root),
            "base_ref": inputs.base_ref,
            "head_ref": inputs.head_ref,
            "trinity_repo": str(inputs.trinity_repo),
            "trinity_ref": trinity_sha,
            "cata_js_repo": str(inputs.cata_js_repo),
            "cata_js_ref": cata_js_sha,
            "ledger": ledger_relative,
        },
        "ancestry": ancestry,
        "diff": {
            "files": len({hunk.path for hunk in hunks}),
            "hunks": len(hunks),
            "insertions": sum(hunk.added for hunk in hunks),
            "deletions": sum(hunk.removed for hunk in hunks),
            "blocks": [asdict(hunk) for hunk in sorted(hunks, key=lambda row: (row.path, row.id))],
        },
        "opcode_summary": {
            "declarations": len(current_declarations),
            "registrations": len(registrations),
            "active_registrations": len(active_keys),
            "usable_table_indices": len({current_values[name] for _, name in active_keys}),
            "zero_value_registrations": zero_value,
            "same_name_cata": comparison_stats(current_declarations, canonical_declarations),
            "same_name_upstream": comparison_stats(current_declarations, upstream_declarations),
            "cata_js_vs_cata": comparison_stats(cata_js_declarations, canonical_declarations),
        },
        "opcodes": opcodes,
        "collisions": collisions,
        "anchors": anchors,
        "client": client,
        "findings": [
            finding_dict(row)
            for row in sorted(findings, key=lambda row: (row.severity, row.code, row.subject, row.message))
        ],
        "result": {"errors": errors, "warnings": warnings, "pass": errors == 0},
    }


def report_as_text(report: Mapping[str, Any]) -> str:
    ancestry = report["ancestry"]
    diff = report["diff"]
    opcodes = report["opcode_summary"]
    collisions = report["collisions"]
    result = report["result"]
    lines = [
        f"head: {ancestry['head_sha']}",
        f"base: {ancestry['base_sha']}",
        f"ahead/behind: {ancestry['ahead']}/{ancestry['behind']}",
        f"diff: {diff['files']} files, {diff['hunks']} hunks, +{diff['insertions']}/-{diff['deletions']}",
        (
            "opcodes: "
            f"{opcodes['active_registrations']} active, "
            f"{opcodes['same_name_cata']['equal']} same-name Cata matches, "
            f"{opcodes['same_name_cata']['mismatched']} mismatches"
        ),
        (
            "collisions: "
            f"{len(collisions['same_direction'])} same-direction, "
            f"{len(collisions['opposite_direction'])} opposite-direction"
        ),
        f"result: {'PASS' if result['pass'] else 'FAIL'} ({result['errors']} errors, {result['warnings']} warnings)",
    ]
    for finding in report["findings"]:
        lines.append(f"{finding['severity'].upper()} {finding['code']} {finding['subject']}: {finding['message']}")
    return "\n".join(lines) + "\n"


def minimal_ledger(rows: Sequence[LedgerRow]) -> Ledger:
    defaults = next(row for row in rows if row.kind == "defaults")
    return Ledger(
        schema="1",
        defaults=defaults,
        blocks={row.key: row for row in rows if row.kind == "block"},
        files={row.key: row for row in rows if row.kind == "file"},
        opcodes={(row.direction, row.key): row for row in rows if row.kind == "opcode"},
        anchors={row.key: row for row in rows if row.kind == "anchor"},
    )


def self_check() -> None:
    declarations = parse_opcode_declarations(
        "\n".join(
            (
                "enum Opcodes : uint16",
                "{",
                "    CMSG_ONE = 0x10,",
                "    SMSG_ONE = 0x10,",
                "    CMSG_TWO = 0x20,",
                "};",
            )
        ),
        {"Opcodes": ()},
    )
    assert {row.name: row.value for row in declarations} == {
        "CMSG_ONE": 0x10,
        "SMSG_ONE": 0x10,
        "CMSG_TWO": 0x20,
    }
    registrations = parse_opcode_registrations(
        """
        DEFINE_HANDLER(CMSG_ONE, STATUS_AUTHED, PROCESS_INPLACE, &WorldSession::HandleOne);
        DEFINE_SERVER_OPCODE_HANDLER(SMSG_ONE, STATUS_NEVER);
        DEFINE_HANDLER(CMSG_TWO, STATUS_AUTHED, PROCESS_INPLACE, &WorldSession::HandleTwo);
        DEFINE_HANDLER(CMSG_THREE, STATUS_AUTHED, PROCESS_INPLACE, &WorldSession::HandleThree);
        """
    )
    collisions = detect_collisions(
        registrations,
        {"CMSG_ONE": 0x10, "SMSG_ONE": 0x10, "CMSG_TWO": 0x20, "CMSG_THREE": 0x20},
    )
    assert len(collisions["same_direction"]) == 1
    assert len(collisions["opposite_direction"]) == 1

    diff = """diff --git a/a.cpp b/a.cpp
--- a/a.cpp
+++ b/a.cpp
@@ -1 +1 @@ thing
-old
+new
"""
    hunk = parse_diff_hunks(diff)[0]
    assert hunk.added == 1 and hunk.removed == 1
    assert validate_hunk_coverage({hunk.id, "missing"}, {hunk.id}) == ("missing",)

    defaults = LedgerRow(
        "defaults", "opcode", "*", "unverified-wip", "defer", "-", "wotlk", "wotlk",
        "missing", "missing", "Protocol", "-", "-", "Protocol foundation", "open", "-", "-",
    )
    alias = LedgerRow(
        "opcode", "CMSG_OLD", "c2s", "required-cata", "rewrite", "CMSG_NEW", "mapped",
        "cata-unverified", "missing", "missing", "Protocol", "pin:Opcodes.h", "-",
        "Protocol foundation", "open", "source", "explicit alias",
    )
    canonical = parse_opcode_declarations(
        "enum OpcodeClient : uint16\n{\n CMSG_NEW = 0x10,\n};",
        {"OpcodeClient": ("c2s",)},
    )
    fork = parse_opcode_declarations(
        "enum Opcodes : uint16\n{\n CMSG_OLD = 0x10,\n};",
        {"Opcodes": ()},
    )
    fork_reg = parse_opcode_registrations(
        "DEFINE_HANDLER(CMSG_OLD, STATUS_AUTHED, PROCESS_INPLACE, &WorldSession::HandleOld);"
    )
    without_alias, _ = materialize_opcodes(fork, fork_reg, canonical, (), minimal_ledger((defaults,)))
    with_alias, _ = materialize_opcodes(fork, fork_reg, canonical, (), minimal_ledger((defaults, alias)))
    assert without_alias[0]["canonical_value"] is None
    assert with_alias[0]["canonical_match"] is True

    first = stable_json({"b": [2, 1], "a": {"d": 4, "c": 3}})
    second = stable_json({"a": {"c": 3, "d": 4}, "b": [2, 1]})
    assert first == second and first.endswith("\n")


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    if args.self_check:
        self_check()
        print("self-check: PASS")
        return 0

    inputs = build_inputs(args)
    report = make_report(inputs)
    output = stable_json(report) if inputs.output_format == "json" else report_as_text(report)
    sys.stdout.write(output)
    return 0 if report["result"]["pass"] else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (AssertionError, AuditError, OSError, UnicodeError, ValueError) as error:
        print(f"conversion check failed: {error}", file=sys.stderr)
        sys.exit(1)
