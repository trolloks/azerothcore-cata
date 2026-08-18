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
OPCODE_VALUES_MANIFEST = "plan/04-opcode-values.tsv"
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
        r"(enum OpcodeClient : uint16)",
        "enum OpcodeClient : uint16",
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
    if name.startswith("SMSG_") or name.startswith("TC9_SMSG_"):
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
    text = re.sub(
        r"//[^\n]*|/\*.*?\*/",
        lambda match: "".join("\n" if char == "\n" else " " for char in match.group()),
        text,
        flags=re.DOTALL,
    )
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


def render_opcode_value_manifest(rows: Sequence[OpcodeDeclaration]) -> str:
    values = declarations_by_name(rows)
    return "".join(f"{name}\t0x{values[name].value:04X}\n" for name in sorted(values))


def opcode_model_issues(header: str, source: str, manifest: str) -> tuple[str, ...]:
    issues: list[str] = []
    declarations = parse_opcode_declarations(
        header,
        {"OpcodeClient": ("c2s",), "OpcodeServer": ("s2c",)},
    )
    registrations = parse_opcode_registrations(source)
    owners = {row.name: row.enum_name for row in declarations}

    if re.search(r"enum\s+Opcodes\b|typedef\s+Opcodes\b", header):
        issues.append("combined-opcode-type-remains")
    if sum(row.enum_name == "OpcodeClient" for row in declarations) != 727:
        issues.append("client-declaration-count-changed")
    if sum(row.enum_name == "OpcodeServer" for row in declarations) != 588:
        issues.append("server-declaration-count-changed")
    if sum(row.direction == "c2s" for row in registrations) != 727:
        issues.append("client-registration-count-changed")
    if sum(row.direction == "s2c" for row in registrations) != 588:
        issues.append("server-registration-count-changed")
    if render_opcode_value_manifest(declarations) != manifest:
        issues.append("opcode-value-manifest-changed")

    aliases = {
        (match.group(1), match.group(2), match.group(3), match.group(4))
        for match in re.finditer(
            r"inline\s+constexpr\s+(OpcodeClient|OpcodeServer)\s+([A-Z][A-Z0-9_]*)\s*=\s*"
            r"static_cast<(OpcodeClient|OpcodeServer)>\(([A-Z][A-Z0-9_]*)\);",
            header,
            re.DOTALL,
        )
    }
    expected_aliases: set[tuple[str, str, str, str]] = set()
    expected_names: set[tuple[str, str, str]] = set()
    for name, owner in owners.items():
        if not name.startswith("MSG_"):
            continue
        if owner == "OpcodeClient":
            expected_aliases.add(("OpcodeServer", f"{name}_SERVER", "OpcodeServer", name))
            expected_names.add((name, name, f"{name}_SERVER"))
        else:
            expected_aliases.add(("OpcodeClient", f"{name}_CLIENT", "OpcodeClient", name))
            expected_names.add((name, f"{name}_CLIENT", name))
    if aliases != expected_aliases or len(aliases) != 105:
        issues.append("bidirectional-opcode-aliases-incomplete")

    names = {
        match.groups()
        for match in re.finditer(
            r"DEFINE_BIDIRECTIONAL_OPCODE\(\s*([A-Z][A-Z0-9_]*)\s*,\s*"
            r"([A-Z][A-Z0-9_]*)\s*,\s*([A-Z][A-Z0-9_]*)\s*\)",
            source,
        )
    }
    if names != expected_names or len(names) != 105:
        issues.append("bidirectional-opcode-names-incomplete")

    required_header = (
        "operator[](OpcodeClient index)",
        "operator[](OpcodeServer index)",
        "ClientOpcodeHandler* _internalTableClient",
        "ServerOpcodeHandler* _internalTableServer",
        "char const* _internalTableClientNames",
        "char const* _internalTableServerNames",
        "GetIncomingOpcode(uint16 opcode)",
        "GetOpcodeNameForLogging(OpcodeClient opcode)",
        "GetOpcodeNameForLogging(OpcodeServer opcode)",
    )
    if any(token not in header for token in required_header):
        issues.append("directional-opcode-storage-or-lookup-missing")
    if "new ServerOpcodeHandler(name, status)" not in source or "Handle_ServerSide" in source:
        issues.append("server-opcode-handler-not-directional")
    client_name_fallback = "_internalTableClientNames[value] ? _internalTableClientNames[value] : _internalTableServerNames[value]"
    if client_name_fallback not in source or "GetIncomingOpcode(uint16 opcode)" not in source:
        issues.append("incoming-opcode-compatibility-path-missing")

    return tuple(issues)


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
    file_hashes: Mapping[str, str],
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
            expected = ledger.files[path].evidence.removeprefix("sha256:")
            if not ledger.files[path].evidence.startswith("sha256:"):
                findings.append(Finding("error", "UNPINNED_FILE_COVERAGE", path, "file coverage needs a SHA-256"))
            elif file_hashes.get(path) != expected:
                findings.append(Finding("error", "STALE_FILE_HASH", path, "file coverage hash does not match HEAD"))

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
        prefix_direction = prefix_directions(registration.name)
        if len(prefix_direction) == 1 and registration.direction not in prefix_direction:
            findings.append(
                Finding(
                    "warning",
                    "DIRECTION_CONTRADICTION",
                    registration.name,
                    f"prefix implies {prefix_direction}, registered {registration.direction}",
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


def byte_buffer_bit_issues(source: str) -> tuple[str, ...]:
    issues: list[str] = []
    member_state = re.search(r"uint8\s+_curbitval\s*\{\s*0\s*\}\s*;", source)
    move_ctor = re.search(r"ByteBuffer\(ByteBuffer&& buf\) noexcept\s*:(.*?)\n\s*\{(.*?)\n\s*\}", source, re.DOTALL)
    move_assign = re.search(r"operator=\(ByteBuffer&& right\).*?\{(.*?)\n\s*\}", source, re.DOTALL)
    clear = re.search(r"void clear\(\)\s*\{(.*?)\n\s*\}", source, re.DOTALL)
    resize = re.search(r"void resize\(std::size_t newsize\)\s*\{(.*?)\n\s*\}", source, re.DOTALL)

    if member_state is None:
        issues.append("default-curbitval-uninitialized")
    if move_ctor is None or any(
        token not in "".join(move_ctor.groups())
        for token in ("_bitpos(buf._bitpos)", "_curbitval(buf._curbitval)", "buf.clear()")
    ):
        issues.append("move-constructor-bit-state-not-preserved")
    if move_assign is None or any(
        token not in move_assign.group(1)
        for token in ("_bitpos = right._bitpos", "_curbitval = right._curbitval", "right.clear()")
    ):
        issues.append("move-assignment-bit-state-not-preserved")
    for name, body in (("clear", clear), ("resize", resize)):
        if body is None or any(token not in body.group(1) for token in ("_bitpos = InitialBitPos", "_curbitval = 0")):
            issues.append(f"{name}-bit-state-not-reset")

    return tuple(issues)


def world_socket_compression_issues(source: str) -> tuple[str, ...]:
    issues: list[str] = []
    constructor = re.search(
        r"WorldSocket::WorldSocket\(IoContextTcpSocket&& socket\)\s*:(.*?)\n\s*\{",
        source,
        re.DOTALL,
    )
    initializer = re.search(
        r"void WorldSocket::InitializeHandler\(.*?\)\s*\{(.*?)\n\s*\}\s*\n\s*bool WorldSocket::Update\(\)",
        source,
        re.DOTALL,
    )
    destructor = re.search(
        r"WorldSocket::~WorldSocket\(\)\s*\{(.*?)\n\s*\}\s*\n\s*void WorldSocket::Start\(\)",
        source,
        re.DOTALL,
    )

    if constructor is None or "_compressionStream(nullptr)" not in constructor.group(1):
        issues.append("compression-stream-default-state-not-null")

    initialize_body = initializer.group(1) if initializer else ""
    allocation_token = "std::make_unique<z_stream>()"
    publication_token = "_compressionStream = compressionStream.release();"
    allocation = initialize_body.find(allocation_token)
    init_call = initialize_body.find("deflateInit(compressionStream.get(),")
    publication = initialize_body.find(publication_token)
    repeat_guard = re.search(
        r"if\s*\(_compressionStream\)\s*\{\s*CloseSocket\(\);\s*return;\s*\}",
        initialize_body,
        re.DOTALL,
    )
    failure = re.search(
        r"if\s*\(z_res != Z_OK\)\s*\{(.*?)\n\s*\}",
        initialize_body,
        re.DOTALL,
    )

    if repeat_guard is None or allocation < 0 or repeat_guard.start() > allocation:
        issues.append("compression-stream-repeat-init-not-rejected")

    failure_is_safe = (
        failure is not None
        and "CloseSocket();" in failure.group(1)
        and "return;" in failure.group(1)
        and 0 <= init_call < failure.start() < failure.end() < publication
    )
    if not failure_is_safe:
        issues.append("compression-stream-init-failure-not-null")

    publication_is_safe = (
        initialize_body.count(allocation_token) == 1
        and initialize_body.count(publication_token) == 1
        and failure is not None
        and 0 <= allocation < init_call < failure.start() < failure.end() < publication
    )
    if not publication_is_safe:
        issues.append("compression-stream-success-not-owned")

    destructor_body = destructor.group(1) if destructor else ""
    cleanup_guard = re.search(
        r"if\s*\(_compressionStream\)\s*\{(.*?)\n\s*\}",
        destructor_body,
        re.DOTALL,
    )
    cleanup_body = cleanup_guard.group(1) if cleanup_guard else ""
    finalize = cleanup_body.find("deflateEnd(_compressionStream);")
    release = cleanup_body.find("delete _compressionStream;")
    if finalize < 0 or source.count("deflateEnd(_compressionStream);") != 1:
        issues.append("compression-stream-not-finalized-once")
    if release < 0 or release < finalize or source.count("delete _compressionStream;") != 1:
        issues.append("compression-stream-not-released-once")

    return tuple(issues)


def authentication_codec_issues(
    header: str,
    source: str,
    socket_header: str,
    socket_source: str,
    auth_handler: str,
    session_source: str,
    session_mgr: str,
    tests: str,
) -> tuple[str, ...]:
    issues: list[str] = []

    if any(token not in header for token in (
        "class AuthChallenge final : public ServerPacket",
        "class AuthSession final : public ClientPacket",
        "class AuthResponse final : public ServerPacket",
        "std::optional<AuthSuccessInfo> SuccessInfo",
        "std::optional<AuthWaitInfo> WaitInfo",
    )):
        issues.append("authentication-packet-types-missing")

    if any(token not in source for token in (
        "uint32 accountLength = _worldPacket.ReadBits(12);",
        "addonInfoSize > _worldPacket.size() - _worldPacket.rpos()",
        "accountLength > _worldPacket.size() - _worldPacket.rpos()",
        "_worldPacket.rpos() != _worldPacket.size()",
    )):
        issues.append("auth-session-boundary-checks-missing")

    query = socket_source.find("LOGIN_SEL_ACCOUNT_INFO_BY_NAME")
    read = socket_source.find("authSession->Read();")
    if (
        "std::shared_ptr<WorldPackets::Auth::AuthSession>" not in socket_header
        or "std::make_shared<WorldPackets::Auth::AuthSession>" not in socket_source
        or not 0 <= read < query
    ):
        issues.append("auth-session-typed-ownership-missing")

    raw_sources = "\n".join((socket_source, auth_handler, session_source))
    if "struct ClientAuthSession" in socket_source or re.search(
        r"WorldPacket\s+\w+\s*\(SMSG_AUTH_(?:CHALLENGE|RESPONSE)", raw_sources
    ):
        issues.append("raw-authentication-codec-remains")

    if any(token not in raw_sources + session_mgr for token in (
        "WorldPackets::Auth::AuthChallenge packet;",
        "WorldPackets::Auth::AuthResponse packet(code);",
        "SendAuthResponse(position == 0 ? AUTH_OK : AUTH_WAIT_QUEUE, position == 0, position);",
        "SendAuthResponse(AUTH_SYSTEM_ERROR, true);",
        "SendAuthResponse(AUTH_OK, false, GetQueuePos(session));",
    )):
        issues.append("auth-response-routing-incomplete")

    if any(token not in tests for token in (
        "WritesAuthChallenge",
        "ReadsAuthSession",
        "RejectsMalformedAuthSession",
        "KeepsAllTwelveAccountLengthBits",
        "WritesAuthResponses",
        "A00000000003000000000300000000000C07000000",
    )):
        issues.append("authentication-fixtures-incomplete")

    return tuple(issues)


def build_15595_admission_issues(migration: str) -> tuple[str, ...]:
    issues: list[str] = []
    required = (
        "DELETE FROM `build_info` WHERE `build` = 15595;",
        "`build`, `majorVersion`, `minorVersion`, `bugfixVersion`, `hotfixVersion`, `winAuthSeed`, "
        "`win64AuthSeed`, `mac64AuthSeed`, `winChecksumSeed`, `macChecksumSeed`",
        "(15595, 4, 3, 4, NULL, NULL, NULL, NULL, NULL, NULL);",
        "ALTER TABLE `realmlist` ALTER COLUMN `gamebuild` SET DEFAULT 15595;",
    )
    if any(token not in migration for token in required):
        issues.append("build-15595-admission-incomplete")
    if re.search(r"UPDATE\s+`?realmlist`?", migration, re.IGNORECASE):
        issues.append("build-15595-rewrites-existing-realm")
    return tuple(issues)


def cataclysm_map_format_issues(grid: str, extractor: str, terrain_builder: str) -> tuple[str, ...]:
    issues: list[str] = []
    if "const uint32 MapVersionMagic      = 10;" not in grid:
        issues.append("world-map-format-is-not-cataclysm")
    if "static uint32 const MAP_VERSION_MAGIC = 10;" not in extractor:
        issues.append("map-extractor-format-is-not-cataclysm")
    if "uint32 const MAP_VERSION_MAGIC = 10;" not in terrain_builder:
        issues.append("mmap-generator-format-is-not-cataclysm")
    return tuple(issues)


def authentication_handoff_issues(socket_header: str, tests: str, runner: str) -> tuple[str, ...]:
    issues: list[str] = []
    if socket_header.count("friend class WorldAuthenticationHandoffTest;") != 1:
        issues.append("world-authentication-test-seam-missing")
    if any(token not in tests for token in (
        "HandleAuthSessionCallback(session, std::move(result))",
        "LoginDatabase.AsyncQuery(statement)",
        "databaseKey, expectedKey",
        'unknown->Payload, "0015"',
        'wrongRealm->Payload, "0027"',
        'badDigest->Payload, "000D"',
        'success->Payload, "400000000003000000000300000000000C"',
        "sWorldSessionMgr->UpdateSessions(1)",
        "PLAN6_WORLD_TRANSCRIPT",
    )):
        issues.append("world-authentication-handoff-fixture-incomplete")
    if any(token not in runner for token in (
        'MYSQL_IMAGE = "mysql:8.4"',
        'response = recv_exact(connection, 32)',
        'challenge_packet(15596, ACCOUNT)',
        '"peer_k_equals_database_k": True',
        '"database_k_equals_world_query_k": True',
        '"org.azerothcore.plan": "6"',
        "assert_owned(manifest, generation",
        "def prepare(",
        "def execute(",
        "def inspect_run(",
        "def reset(",
        "def self_check(",
        "args.compare_last_two",
    )):
        issues.append("authentication-handoff-runner-incomplete")
    return tuple(issues)


def real_client_authentication_issues(runner: str, fixture: str, plan_index: str) -> tuple[str, ...]:
    issues: list[str] = []
    if any(token not in runner for token in (
        '"self-check"',
        '"prepare"',
        '"run"',
        '"verify"',
        '"replay"',
        '"reset"',
        '"compare-last-two"',
        '"--server-dbc-root"',
        '"--purge-client-base"',
        '"--purge-database-cache"',
        "database_cache_key",
        "reconcile_database_cache",
        "DATABASE_CACHE_VERSION",
        '"mysqldump"',
        "WRITABLE_CLIENT_DIRS",
        "127.0.0.1",
        "3724",
        "connection.log",
        "network.opcode",
        CLIENT_SHA256,
    )):
        issues.append("real-client-authentication-runner-incomplete")

    try:
        proof = json.loads(fixture)
    except (json.JSONDecodeError, TypeError):
        proof = None
    required_milestones = (
        "LOGIN_OK",
        "WORLD_CONNECTED",
        "COP_AUTHENTICATE_AUTH_OK_TRUE",
        "COP_GET_CHARACTERS_INITIATING",
        "CMSG_AUTH_SESSION",
        "SMSG_AUTH_RESPONSE",
    )
    if not isinstance(proof, dict) or any((
        proof.get("schema") != 1,
        proof.get("plan") != 7,
        proof.get("client_build") != 15595,
        proof.get("client_sha256") != CLIENT_SHA256,
        proof.get("verdict") != "PASS",
        proof.get("milestones") != list(required_milestones),
        proof.get("fresh_runs") != 2,
        proof.get("matching_runs") is not True,
        proof.get("plan6_replay_status") != "PASS",
        proof.get("protected_inputs_unchanged") is not True,
        proof.get("reset") != "PASS",
    )):
        issues.append("real-client-authentication-fixture-incomplete")

    if any(row not in plan_index for row in (
        "08\t18\tclosed\tPlan 8: typed empty character enumeration and stable build 15595 screen\t"
        "https://github.com/trolloks/azerothcore-cata/issues/18",
        "09\t19\tclosed\tPlan 9: one database-backed build 15595 character\t"
        "https://github.com/trolloks/azerothcore-cata/issues/19",
        "10\t20\tclosed\tPlan 10: select the enumerated build 15595 character\t"
        "https://github.com/trolloks/azerothcore-cata/issues/20",
    )):
        issues.append("plan-issue-index-incomplete")
    return tuple(issues)


def character_enumeration_issues(
    packet_header: str, packet_source: str, handler: str, session_header: str,
    tests: str, runner: str, fixture: str,
) -> tuple[str, ...]:
    issues: list[str] = []
    if any(token not in packet_header for token in (
        "class EnumCharacters final : public ClientPacket",
        "class EnumCharactersResult final : public ServerPacket",
        "struct RestrictedFactionChangeRuleInfo",
        "std::array<VisualItemInfo, 23>",
        '#include "Position.h"',
        "#include <array>",
    )) or "void WorldPackets::Character::EnumCharacters::Read()" not in packet_source:
        issues.append("character-enumeration-packet-types-missing")
    if any(token not in packet_source for token in (
        'ByteBufferInvalidValueException("character enumeration", "trailing bytes")',
        "_worldPacket.WriteBits(FactionChangeRestrictions.size(), 23);",
        "_worldPacket.WriteBit(Success);",
        "_worldPacket.WriteBits(Characters.size(), 17);",
        "_worldPacket.FlushBits();",
    )):
        issues.append("character-enumeration-wire-shape-missing")
    if "WorldPackets::Character::EnumCharacters&" not in handler or "SendPacket(charEnum.Write());" not in handler:
        issues.append("character-enumeration-typed-handler-missing")
    if "void HandleCharEnumOpcode(WorldPackets::Character::EnumCharacters& packet);" not in session_header:
        issues.append("character-enumeration-session-declaration-missing")
    if any(token in handler for token in (
        "WorldPacket data(SMSG_CHAR_ENUM",
        "std::vector<CharacterInfo>",
    )) or "struct CharacterInfo" in session_header:
        issues.append("character-enumeration-raw-model-remains")
    if any(token not in tests for token in (
        "ReadsEmptyEnumCharacters",
        "RejectsTrailingEnumCharactersBody",
        "WritesSuccessfulEmptyEnumCharacters",
        "WritesFailedEmptyEnumCharacters",
        '"000001000000"',
        '"000000000000"',
    )):
        issues.append("character-enumeration-fixtures-incomplete")
    if any(token not in runner for token in (
        '"character-screen"',
        '"--stability-seconds"',
        '"--confirm-expected-screen"',
        "CHARACTER_MILESTONES",
        "characters_completed",
        "character_row_count",
        "FORBIDDEN_CHARACTER_OPCODES",
        "EMPTY_CHARACTER_ENUM_PAYLOAD",
        "compare-last-two",
    )):
        issues.append("character-screen-runner-incomplete")
    try:
        proof = json.loads(fixture)
    except (json.JSONDecodeError, TypeError):
        proof = None
    if not isinstance(proof, dict) or any((
        proof.get("schema") != 1,
        proof.get("plan") != 8,
        proof.get("client_build") != 15595,
        proof.get("verdict") != "PASS",
        proof.get("mode") != "character-screen",
        proof.get("response_body") != "000001000000",
        proof.get("character_rows") != 0,
        proof.get("fresh_runs") != 2,
        proof.get("matching_runs") is not True,
        proof.get("protected_inputs_unchanged") is not True,
        proof.get("reset") != "PASS",
    )):
        issues.append("character-screen-live-fixture-missing")
    return tuple(issues)


def populated_character_list_issues(
    character_database: str, handler: str, tests: str, runner: str, fixture: str,
) -> tuple[str, ...]:
    issues: list[str] = []
    if character_database.count("COALESCE(c.order, 0)") != 2 or any(token in character_database for token in (
        "cb.guid, c.extra_flags ", "cb.guid, c.extra_flags, cd.genitive",
    )):
        issues.append("populated-character-list-query-mapping-missing")
    if any(token not in handler for token in (
        "COALESCE(characters.order, 0)",
        'LOG_INFO("network.opcode", "Enumerated account {} character {} at list position {}.",',
        "charInfo.ListPosition = fields[24].Get<uint8>();",
    )):
        issues.append("populated-character-list-handler-evidence-missing")
    if any(token not in tests for token in (
        "WritesSuccessfulPopulatedEnumCharacters",
        "PopulatedEnumFixtureRejectsWireMutations",
        "PopulatedCharacterPayload",
        "278 * 2",
        "0000010000C080460001",
        "43617461706C616E000503CDD70BC6000101020C000000",
    )):
        issues.append("populated-character-list-packet-fixture-missing")
    if any(token not in runner for token in (
        'POPULATED_MODE = "populated-character-list"',
        "populated_character_seed_sql",
        "verify_populated_character_seed",
        "realm_character_count",
        "enumerated_characters",
        "POPULATED_CHARACTER_ENUM_PAYLOAD",
        '"populated_character_list_pass"',
        "CHARACTER_GUID = 0x01020304",
        "CHARACTER_LIST_POSITION = 7",
    )):
        issues.append("populated-character-list-runner-missing")
    try:
        proof = json.loads(fixture)
    except (json.JSONDecodeError, TypeError):
        proof = None
    expected_character = {
        "guid_low": 16909060, "name": "Cataplan", "race": 1, "class": 1, "gender": 0,
        "level": 1, "map": 0, "zone": 12, "list_position": 7, "flags": 0, "flags2": 0,
        "visual_items_nonzero": 0,
    }
    if not isinstance(proof, dict) or any((
        proof.get("schema") != 1,
        proof.get("plan") != 9,
        proof.get("client_build") != 15595,
        proof.get("verdict") != "PASS",
        proof.get("mode") != "populated-character-list",
        proof.get("response_body") != runner_populated_payload(runner),
        proof.get("character_rows") != 1,
        proof.get("realm_character_count") != 1,
        proof.get("enumerated") != expected_character,
        proof.get("fresh_runs") != 2,
        proof.get("matching_runs") is not True,
        proof.get("protected_inputs_unchanged") is not True,
        proof.get("reset") != "PASS",
    )):
        issues.append("populated-character-list-live-fixture-missing")
    return tuple(issues)


def runner_populated_payload(runner: str) -> str | None:
    match = re.search(
        r"POPULATED_CHARACTER_ENUM_PAYLOAD\s*=\s*\((.*?)\)\s*\nPOPULATED_MODE",
        runner, re.DOTALL,
    )
    return "".join(re.findall(r'"([0-9a-fA-F]+)"', match.group(1))) if match else None


def player_login_admission_issues(
    packet_source: str, handler: str, tests: str, runner: str, fixture: str,
) -> tuple[str, ...]:
    issues: list[str] = []
    if any(token not in packet_source for token in (
        "Guid[2] = _worldPacket.ReadBit();",
        "Guid[7] = _worldPacket.ReadBit();",
        "_worldPacket.ReadByteSeq(Guid[2]);",
        "_worldPacket.ReadByteSeq(Guid[4]);",
    )):
        issues.append("player-login-parser-order-missing")
    if not re.search(
        r"void WorldSession::HandlePlayerLoginFromDB\(LoginQueryHolder const& holder\)\n"
        r"\{\n    LOG_INFO\(\"network\.opcode\", \"Player login callback for account \{\} character \{\}\.\",\n"
        r"        GetAccountId\(\), holder\.GetGuid\(\)\.ToString\(\)\);",
        handler,
    ):
        issues.append("player-login-callback-boundary-missing")
    if any(token not in tests for token in (
        "ReadsBuild15595PlayerLoginGuid",
        "RejectsTruncatedBuild15595PlayerLoginGuid",
        "Build15595PlayerLoginGuidRejectsWireMutations",
        "0xE2, 0x03, 0x05, 0x00, 0x02",
        "16909060u",
    )):
        issues.append("player-login-packet-tests-missing")
    if any(token not in runner for token in (
        'CHARACTER_SELECTION_MODE = "character-selection"',
        "automate_character_selection",
        "player_login_callbacks",
        "selection_proof_is_complete",
        '"seeded_guid_low"',
        '"callback_guid_low"',
        '"load-returned-false"',
    )):
        issues.append("player-login-selection-runner-missing")
    try:
        proof = json.loads(fixture)
    except (json.JSONDecodeError, TypeError):
        proof = None
    expected_selection = {
        "action": "enter",
        "seeded_guid_low": 16909060,
        "enumerated_guid_low": 16909060,
        "request_guid_low": 16909060,
        "callback_guid_low": 16909060,
        "legit_characters_admission": True,
    }
    if not isinstance(proof, dict) or any((
        proof.get("schema") != 1,
        proof.get("plan") != 10,
        proof.get("client_build") != 15595,
        proof.get("verdict") != "PASS",
        proof.get("mode") != "character-selection",
        proof.get("request_payload") != "E203050002",
        proof.get("selection") != expected_selection,
        proof.get("fresh_runs") != 2,
        proof.get("matching_runs") is not True,
        proof.get("protected_inputs_unchanged") is not True,
        proof.get("reset") != "PASS",
        proof.get("downstream_diagnostic") not in {"load-returned-false", "load-returned-true", "not-observed"},
    )):
        issues.append("player-login-live-fixture-missing")
    return tuple(issues)

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
    bit_issues = byte_buffer_bit_issues(byte_buffer)
    bit_row = ledger.anchors.get("protocol.byte-buffer-bits")
    if bit_row is None:
        findings.append(
            Finding("error", "MISSING_ANCHOR", "protocol.byte-buffer-bits", "named anchor has no ledger row")
        )
    elif (bit_row.state == "converted" or bit_row.implementation == "converted") and (
        bit_issues or bit_row.fixture != "pass"
    ):
        findings.append(
            Finding(
                "error",
                "CONVERTED_ANCHOR_WITHOUT_PROOF",
                "protocol.byte-buffer-bits",
                "converted bit-buffer anchor lacks deterministic source state or fixture proof",
            )
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
    compression_issues = world_socket_compression_issues(world_socket)
    compression_row = ledger.anchors.get("protocol.compression")
    if compression_row is None:
        findings.append(Finding("error", "MISSING_ANCHOR", "protocol.compression", "named anchor has no ledger row"))
    elif (compression_row.state == "converted" or compression_row.implementation == "converted") and (
        compression_issues or compression_row.fixture != "pass"
    ):
        findings.append(
            Finding(
                "error",
                "CONVERTED_ANCHOR_WITHOUT_PROOF",
                "protocol.compression",
                "converted compression anchor lacks lifecycle-safe source or recorded checker proof",
            )
        )
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

    authentication_header = read_ref_file(
        inputs.repo_root, inputs.head_ref, "src/server/game/Server/Packets/AuthenticationPackets.h"
    )
    authentication_source = read_ref_file(
        inputs.repo_root, inputs.head_ref, "src/server/game/Server/Packets/AuthenticationPackets.cpp"
    )
    world_socket_header = read_ref_file(inputs.repo_root, inputs.head_ref, "src/server/game/Server/WorldSocket.h")
    auth_handler = read_ref_file(inputs.repo_root, inputs.head_ref, "src/server/game/Handlers/AuthHandler.cpp")
    world_session = read_ref_file(inputs.repo_root, inputs.head_ref, "src/server/game/Server/WorldSession.cpp")
    world_session_mgr = read_ref_file(inputs.repo_root, inputs.head_ref, "src/server/game/Server/WorldSessionMgr.cpp")
    authentication_tests = read_ref_file(
        inputs.repo_root, inputs.head_ref, "src/test/server/game/Server/Packets/AuthenticationPacketsTest.cpp"
    )
    authentication_issues = authentication_codec_issues(
        authentication_header,
        authentication_source,
        world_socket_header,
        world_socket,
        auth_handler,
        world_session,
        world_session_mgr,
        authentication_tests,
    )
    authentication_row = ledger.anchors.get("protocol.authentication-codecs")
    if authentication_row is None:
        findings.append(
            Finding("error", "MISSING_ANCHOR", "protocol.authentication-codecs", "named anchor has no ledger row")
        )
    elif (authentication_row.state == "converted" or authentication_row.implementation == "converted") and (
        authentication_issues or authentication_row.fixture != "pass"
    ):
        findings.append(
            Finding(
                "error",
                "CONVERTED_ANCHOR_WITHOUT_PROOF",
                "protocol.authentication-codecs",
                "converted authentication anchor lacks typed routing or exact fixture proof",
            )
        )
    anchors.append(
        {
            "key": "protocol.authentication-codecs",
            "path": "src/server/game/Server/Packets/AuthenticationPackets.{h,cpp}",
            "current": ",".join(authentication_issues) if authentication_issues else "typed-authentication-codecs",
            "target": "typed-authentication-codecs",
            "matched_target": not authentication_issues,
            "plan": "World authentication",
            "state": authentication_row.state if authentication_row else "open",
            "evidence": authentication_row.evidence if authentication_row else "-",
        }
    )

    admission_migration = read_ref_file(
        inputs.repo_root, inputs.head_ref, "data/sql/updates/pending_db_auth/rev_1786964293354831242.sql"
    )
    admission_issues = build_15595_admission_issues(admission_migration)
    admission_row = ledger.anchors.get("database.build-15595-admission")
    if admission_row is None:
        findings.append(Finding("error", "MISSING_ANCHOR", "database.build-15595-admission", "named anchor has no ledger row"))
    elif (admission_row.state == "converted" or admission_row.implementation == "converted") and (
        admission_issues or admission_row.fixture != "pass"
    ):
        findings.append(Finding("error", "CONVERTED_ANCHOR_WITHOUT_PROOF", "database.build-15595-admission",
                                "converted build admission lacks the pending migration or disposable-database proof"))
    anchors.append({
        "key": "database.build-15595-admission",
        "path": "data/sql/updates/pending_db_auth/rev_1786964293354831242.sql",
        "current": ",".join(admission_issues) if admission_issues else "build-15595-admitted",
        "target": "build-15595-admitted",
        "matched_target": not admission_issues,
        "plan": "Authentication handoff",
        "state": admission_row.state if admission_row else "open",
        "evidence": admission_row.evidence if admission_row else "-",
    })

    grid_terrain = read_ref_file(inputs.repo_root, inputs.head_ref, "src/server/game/Grids/GridTerrainData.h")
    map_extractor = read_ref_file(inputs.repo_root, inputs.head_ref, "src/tools/map_extractor/System.cpp")
    terrain_builder = read_ref_file(inputs.repo_root, inputs.head_ref, "src/tools/mmaps_generator/TerrainBuilder.cpp")
    map_issues = cataclysm_map_format_issues(grid_terrain, map_extractor, terrain_builder)
    map_row = ledger.anchors.get("client-data.map-format")
    if map_row is None:
        findings.append(Finding("error", "MISSING_ANCHOR", "client-data.map-format", "named anchor has no ledger row"))
    elif (map_row.state == "converted" or map_row.implementation == "converted") and (
        map_issues or map_row.fixture != "pass"
    ):
        findings.append(Finding("error", "CONVERTED_ANCHOR_WITHOUT_PROOF", "client-data.map-format",
                                "converted map format lacks matching server and extractor constants"))
    anchors.append({
        "key": "client-data.map-format",
        "path": "src/server/game/Grids/GridTerrainData.h;src/tools/{map_extractor,mmaps_generator}",
        "current": ",".join(map_issues) if map_issues else "cataclysm-map-format-10",
        "target": "cataclysm-map-format-10",
        "matched_target": not map_issues,
        "plan": "Real client authentication",
        "state": map_row.state if map_row else "open",
        "evidence": map_row.evidence if map_row else "-",
    })

    handoff_runner = read_ref_file(inputs.repo_root, inputs.head_ref, "apps/cata/run_authentication_handoff.py")
    handoff_issues = authentication_handoff_issues(world_socket_header, authentication_tests, handoff_runner)
    handoff_row = ledger.anchors.get("protocol.authentication-handoff")
    if handoff_row is None:
        findings.append(Finding("error", "MISSING_ANCHOR", "protocol.authentication-handoff", "named anchor has no ledger row"))
    elif (handoff_row.state == "converted" or handoff_row.implementation == "converted") and (
        handoff_issues or handoff_row.fixture != "pass"
    ):
        findings.append(Finding("error", "CONVERTED_ANCHOR_WITHOUT_PROOF", "protocol.authentication-handoff",
                                "converted authentication handoff lacks its executable boundary proof"))
    anchors.append({
        "key": "protocol.authentication-handoff",
        "path": "apps/cata/run_authentication_handoff.py",
        "current": ",".join(handoff_issues) if handoff_issues else "authserver-world-handoff",
        "target": "authserver-world-handoff",
        "matched_target": not handoff_issues,
        "plan": "Authentication handoff",
        "state": handoff_row.state if handoff_row else "open",
        "evidence": handoff_row.evidence if handoff_row else "-",
    })

    client_runner = read_ref_file(inputs.repo_root, inputs.head_ref, "apps/cata/run_real_client_authentication.py")
    client_fixture = read_ref_file(
        inputs.repo_root, inputs.head_ref, "apps/cata/fixtures/plan7-client-authentication.json"
    )
    plan_index = read_ref_file(inputs.repo_root, inputs.head_ref, "plan/github-issues.tsv")
    client_issues = real_client_authentication_issues(client_runner, client_fixture, plan_index)
    client_row = ledger.anchors.get("protocol.real-client-authentication")
    if client_row is None:
        findings.append(Finding("error", "MISSING_ANCHOR", "protocol.real-client-authentication",
                                "named anchor has no ledger row"))
    elif (client_row.state == "converted" or client_row.implementation == "converted") and (
        client_issues or client_row.fixture != "pass" or client_row.client != "pass"
    ):
        findings.append(Finding("error", "CONVERTED_ANCHOR_WITHOUT_PROOF", "protocol.real-client-authentication",
                                "converted real-client authentication lacks two-run client and replay proof"))
    anchors.append({
        "key": "protocol.real-client-authentication",
        "path": "apps/cata/run_real_client_authentication.py",
        "current": ",".join(client_issues) if client_issues else "real-client-authentication-accepted",
        "target": "real-client-authentication-accepted",
        "matched_target": not client_issues,
        "plan": "Real client authentication",
        "state": client_row.state if client_row else "open",
        "evidence": client_row.evidence if client_row else "-",
    })

    character_header = read_ref_file(
        inputs.repo_root, inputs.head_ref, "src/server/game/Server/Packets/CharacterPackets.h"
    )
    character_source = read_ref_file(
        inputs.repo_root, inputs.head_ref, "src/server/game/Server/Packets/CharacterPackets.cpp"
    )
    character_handler = read_ref_file(
        inputs.repo_root, inputs.head_ref, "src/server/game/Handlers/CharacterHandler.cpp"
    )
    character_session = read_ref_file(
        inputs.repo_root, inputs.head_ref, "src/server/game/Server/WorldSession.h"
    )
    character_tests = read_ref_file(
        inputs.repo_root, inputs.head_ref, "src/test/server/game/Server/Packets/CharacterPacketsTest.cpp"
    )
    try:
        character_fixture = read_ref_file(
            inputs.repo_root, inputs.head_ref, "apps/cata/fixtures/plan8-character-screen.json"
        )
    except AuditError:
        character_fixture = ""
    character_issues = character_enumeration_issues(
        character_header, character_source, character_handler, character_session,
        character_tests, client_runner, character_fixture,
    )
    character_row = ledger.anchors.get("protocol.character-enumeration")
    if character_row is None:
        findings.append(Finding("error", "MISSING_ANCHOR", "protocol.character-enumeration", "named anchor has no ledger row"))
    elif character_row.state == "converted" and (
        character_issues or character_row.fixture != "pass" or character_row.client != "pass"
    ):
        findings.append(
            Finding(
                "error", "CONVERTED_ANCHOR_WITHOUT_PROOF", "protocol.character-enumeration",
                "converted character enumeration lacks typed packet, runner, and two-run screen proof",
            )
        )
    anchors.append({
        "key": "protocol.character-enumeration",
        "path": "src/server/game/Server/Packets/CharacterPackets.{h,cpp};apps/cata/run_real_client_authentication.py",
        "current": ",".join(character_issues) if character_issues else "typed-character-enumeration",
        "target": "typed-character-enumeration",
        "matched_target": not character_issues,
        "plan": "Build 15595 character screen",
        "state": character_row.state if character_row else "open",
        "evidence": character_row.evidence if character_row else "-",
    })

    character_database = read_ref_file(
        inputs.repo_root, inputs.head_ref,
        "src/server/database/Database/Implementation/CharacterDatabase.cpp",
    )
    try:
        populated_fixture = read_ref_file(
            inputs.repo_root, inputs.head_ref, "apps/cata/fixtures/plan9-populated-character-list.json"
        )
    except AuditError:
        populated_fixture = ""
    populated_issues = populated_character_list_issues(
        character_database, character_handler, character_tests, client_runner, populated_fixture,
    )
    populated_row = ledger.anchors.get("protocol.populated-character-list")
    if populated_row is None:
        findings.append(Finding(
            "error", "MISSING_ANCHOR", "protocol.populated-character-list", "named anchor has no ledger row",
        ))
    elif populated_row.state == "converted" and (
        populated_issues or populated_row.fixture != "pass" or populated_row.client != "pass"
    ):
        findings.append(Finding(
            "error", "CONVERTED_ANCHOR_WITHOUT_PROOF", "protocol.populated-character-list",
            "converted populated character list lacks query, packet, runner, and two-run client proof",
        ))
    anchors.append({
        "key": "protocol.populated-character-list",
        "path": "CharacterDatabase.cpp;CharacterHandler.cpp;CharacterPacketsTest.cpp;run_real_client_authentication.py",
        "current": ",".join(populated_issues) if populated_issues else "populated-character-list-accepted",
        "target": "populated-character-list-accepted",
        "matched_target": not populated_issues,
        "plan": "Build 15595 populated character list",
        "state": populated_row.state if populated_row else "open",
        "evidence": populated_row.evidence if populated_row else "-",
    })

    try:
        player_login_fixture = read_ref_file(
            inputs.repo_root, inputs.head_ref, "apps/cata/fixtures/plan10-character-selection.json"
        )
    except AuditError:
        player_login_fixture = ""
    player_login_issues = player_login_admission_issues(
        character_source, character_handler, character_tests, client_runner, player_login_fixture,
    )
    player_login_row = ledger.anchors.get("protocol.player-login-admission")
    if player_login_row is None:
        findings.append(Finding(
            "error", "MISSING_ANCHOR", "protocol.player-login-admission", "named anchor has no ledger row",
        ))
    elif player_login_row.state == "converted" and (
        player_login_issues or player_login_row.fixture != "pass" or player_login_row.client != "pass"
    ):
        findings.append(Finding(
            "error", "CONVERTED_ANCHOR_WITHOUT_PROOF", "protocol.player-login-admission",
            "converted player login admission lacks parser, callback, runner, or two-run client proof",
        ))
    anchors.append({
        "key": "protocol.player-login-admission",
        "path": "CharacterPackets.cpp;CharacterHandler.cpp;CharacterPacketsTest.cpp;run_real_client_authentication.py",
        "current": ",".join(player_login_issues) if player_login_issues else "player-login-admission-accepted",
        "target": "player-login-admission-accepted",
        "matched_target": not player_login_issues,
        "plan": "Build 15595 character selection",
        "state": player_login_row.state if player_login_row else "open",
        "evidence": player_login_row.evidence if player_login_row else "-",
    })

    opcode_header = read_ref_file(inputs.repo_root, inputs.head_ref, OPCODES_H)
    opcode_source = read_ref_file(inputs.repo_root, inputs.head_ref, OPCODES_CPP)
    opcode_manifest = read_ref_file(inputs.repo_root, inputs.head_ref, OPCODE_VALUES_MANIFEST)
    opcode_issues = opcode_model_issues(opcode_header, opcode_source, opcode_manifest)
    opcode_row = ledger.anchors.get("protocol.opcode-model")
    if opcode_row and (opcode_row.state == "converted" or opcode_row.implementation == "converted") and (
        opcode_issues or opcode_row.fixture != "pass"
    ):
        findings.append(
            Finding(
                "error",
                "CONVERTED_ANCHOR_WITHOUT_PROOF",
                "protocol.opcode-model",
                "converted opcode model lacks direction-safe source, the exact value manifest, or fixture proof",
            )
        )
    anchors = [row for row in anchors if row["key"] != "protocol.opcode-model"]
    anchors.append(
        {
            "key": "protocol.opcode-model",
            "path": f"{OPCODES_H};{OPCODES_CPP};{OPCODE_VALUES_MANIFEST}",
            "current": ",".join(opcode_issues) if opcode_issues else "direction-safe-registry",
            "target": "direction-safe-registry",
            "matched_target": not opcode_issues,
            "plan": "Protocol foundation",
            "state": opcode_row.state if opcode_row else "open",
            "evidence": opcode_row.evidence if opcode_row else "-",
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

    current_declarations = parse_opcode_declarations(
        current_h,
        {"OpcodeClient": ("c2s",), "OpcodeServer": ("s2c",)},
    )
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
    file_hashes = {
        path: hashlib.sha256(read_ref_file(inputs.repo_root, inputs.head_ref, path).encode()).hexdigest()
        for path in ledger.files
        if path != ledger_relative
    }
    findings.extend(
        validate_ledger(ledger, hunks, ledger_relative, file_hashes, active_keys, canonical_by_direction)
    )
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
        // DEFINE_HANDLER(CMSG_COMMENTED, STATUS_AUTHED, PROCESS_INPLACE, &WorldSession::HandleCommented);
        """
    )
    assert all(row.name != "CMSG_COMMENTED" for row in registrations)
    collisions = detect_collisions(
        registrations,
        {"CMSG_ONE": 0x10, "SMSG_ONE": 0x10, "CMSG_TWO": 0x20, "CMSG_THREE": 0x20},
    )
    assert len(collisions["same_direction"]) == 1
    assert len(collisions["opposite_direction"]) == 1

    repo_root = Path(__file__).resolve().parents[2]
    opcode_header = (repo_root / OPCODES_H).read_text(encoding="utf-8")
    opcode_source = (repo_root / OPCODES_CPP).read_text(encoding="utf-8")
    opcode_manifest = (repo_root / OPCODE_VALUES_MANIFEST).read_text(encoding="utf-8")
    assert opcode_model_issues(opcode_header, opcode_source, opcode_manifest) == ()
    assert "combined-opcode-type-remains" in opcode_model_issues(
        opcode_header + "\nenum Opcodes : uint16\n{\n};\n", opcode_source, opcode_manifest
    )
    assert "opcode-value-manifest-changed" in opcode_model_issues(
        opcode_header, opcode_source, opcode_manifest.replace("0x0205", "0x0206", 1)
    )
    assert "bidirectional-opcode-aliases-incomplete" in opcode_model_issues(
        opcode_header.replace("inline constexpr OpcodeServer MSG_MOVE_START_FORWARD_SERVER", "broken", 1),
        opcode_source,
        opcode_manifest,
    )
    assert "bidirectional-opcode-names-incomplete" in opcode_model_issues(
        opcode_header,
        opcode_source.replace(
            "DEFINE_BIDIRECTIONAL_OPCODE(MSG_MOVE_START_FORWARD, MSG_MOVE_START_FORWARD, "
            "MSG_MOVE_START_FORWARD_SERVER);",
            "",
            1,
        ),
        opcode_manifest,
    )
    assert "directional-opcode-storage-or-lookup-missing" in opcode_model_issues(
        opcode_header.replace("ServerOpcodeHandler* _internalTableServer", "OpcodeHandler* broken", 1),
        opcode_source,
        opcode_manifest,
    )
    assert "server-opcode-handler-not-directional" in opcode_model_issues(
        opcode_header,
        opcode_source.replace("new ServerOpcodeHandler(name, status)", "nullptr", 1),
        opcode_manifest,
    )
    assert "incoming-opcode-compatibility-path-missing" in opcode_model_issues(
        opcode_header,
        opcode_source.replace(
            "_internalTableClientNames[value] ? _internalTableClientNames[value] : _internalTableServerNames[value]",
            "_internalTableClientNames[value]",
            1,
        ),
        opcode_manifest,
    )

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

    broken_byte_buffer = """
    ByteBuffer(ByteBuffer&& buf) noexcept : _bitpos(buf._bitpos), _storage(std::move(buf._storage))
    {
        buf._rpos = 0;
    }
    ByteBuffer& operator=(ByteBuffer&& right) noexcept
    {
        _storage = std::move(right._storage);
    }
    void clear()
    {
        _storage.clear();
    }
    void resize(std::size_t newsize)
    {
        _storage.resize(newsize);
    }
    uint8 _curbitval;
    """
    fixed_byte_buffer = """
    ByteBuffer(ByteBuffer&& buf) noexcept :
        _bitpos(buf._bitpos), _curbitval(buf._curbitval), _storage(std::move(buf._storage))
    {
        buf.clear();
    }
    ByteBuffer& operator=(ByteBuffer&& right) noexcept
    {
        _bitpos = right._bitpos;
        _curbitval = right._curbitval;
        right.clear();
    }
    void clear()
    {
        _bitpos = InitialBitPos;
        _curbitval = 0;
    }
    void resize(std::size_t newsize)
    {
        _bitpos = InitialBitPos;
        _curbitval = 0;
    }
    uint8 _curbitval{0};
    """
    assert byte_buffer_bit_issues(broken_byte_buffer)
    assert byte_buffer_bit_issues(fixed_byte_buffer) == ()

    broken_world_socket = """
    WorldSocket::WorldSocket(IoContextTcpSocket&& socket) : Socket(std::move(socket))
    {
    }

    WorldSocket::~WorldSocket() = default;

    void WorldSocket::Start()
    {
    }

    void WorldSocket::InitializeHandler(boost::system::error_code error, std::size_t transferedBytes)
    {
        _compressionStream = new z_stream();
        int32 z_res = deflateInit(_compressionStream, compressionLevel);
        if (z_res != Z_OK)
        {
            CloseSocket();
            return;
        }
    }

    bool WorldSocket::Update()
    {
        return true;
    }
    """
    fixed_world_socket = """
    WorldSocket::WorldSocket(IoContextTcpSocket&& socket)
        : Socket(std::move(socket)), _compressionStream(nullptr)
    {
    }

    WorldSocket::~WorldSocket()
    {
        if (_compressionStream)
        {
            deflateEnd(_compressionStream);
            delete _compressionStream;
        }
    }

    void WorldSocket::Start()
    {
    }

    void WorldSocket::InitializeHandler(boost::system::error_code error, std::size_t transferedBytes)
    {
        if (_compressionStream)
        {
            CloseSocket();
            return;
        }

        auto compressionStream = std::make_unique<z_stream>();
        int32 z_res = deflateInit(compressionStream.get(), compressionLevel);
        if (z_res != Z_OK)
        {
            CloseSocket();
            return;
        }
        _compressionStream = compressionStream.release();
    }

    bool WorldSocket::Update()
    {
        return true;
    }
    """
    assert world_socket_compression_issues(broken_world_socket) == (
        "compression-stream-default-state-not-null",
        "compression-stream-repeat-init-not-rejected",
        "compression-stream-init-failure-not-null",
        "compression-stream-success-not-owned",
        "compression-stream-not-finalized-once",
        "compression-stream-not-released-once",
    )
    assert world_socket_compression_issues(fixed_world_socket) == ()
    assert "compression-stream-default-state-not-null" in world_socket_compression_issues(
        fixed_world_socket.replace("_compressionStream(nullptr)", "_compressionStream")
    )
    assert "compression-stream-repeat-init-not-rejected" in world_socket_compression_issues(
        fixed_world_socket.replace(
            """        if (_compressionStream)
        {
            CloseSocket();
            return;
        }

""",
            "",
        )
    )
    assert "compression-stream-init-failure-not-null" in world_socket_compression_issues(
        fixed_world_socket.replace(
            """        if (z_res != Z_OK)
        {
            CloseSocket();
            return;
        }
""",
            "",
        )
    )
    assert "compression-stream-success-not-owned" in world_socket_compression_issues(
        fixed_world_socket.replace("        _compressionStream = compressionStream.release();\n", "")
    )
    assert "compression-stream-not-finalized-once" in world_socket_compression_issues(
        fixed_world_socket.replace(
            "            deflateEnd(_compressionStream);",
            "            deflateEnd(_compressionStream);\n            deflateEnd(_compressionStream);",
        )
    )
    assert "compression-stream-not-released-once" in world_socket_compression_issues(
        fixed_world_socket.replace(
            "            delete _compressionStream;",
            "            delete _compressionStream;\n            delete _compressionStream;",
        )
    )

    auth_header = (repo_root / "src/server/game/Server/Packets/AuthenticationPackets.h").read_text(encoding="utf-8")
    auth_source = (repo_root / "src/server/game/Server/Packets/AuthenticationPackets.cpp").read_text(encoding="utf-8")
    socket_header = (repo_root / "src/server/game/Server/WorldSocket.h").read_text(encoding="utf-8")
    socket_source = (repo_root / "src/server/game/Server/WorldSocket.cpp").read_text(encoding="utf-8")
    auth_handler = (repo_root / "src/server/game/Handlers/AuthHandler.cpp").read_text(encoding="utf-8")
    session_source = (repo_root / "src/server/game/Server/WorldSession.cpp").read_text(encoding="utf-8")
    session_mgr = (repo_root / "src/server/game/Server/WorldSessionMgr.cpp").read_text(encoding="utf-8")
    auth_tests = (repo_root / "src/test/server/game/Server/Packets/AuthenticationPacketsTest.cpp").read_text(
        encoding="utf-8"
    )
    auth_inputs = (auth_header, auth_source, socket_header, socket_source, auth_handler, session_source, session_mgr, auth_tests)
    assert authentication_codec_issues(*auth_inputs) == ()
    assert "authentication-packet-types-missing" in authentication_codec_issues(
        auth_header.replace("class AuthChallenge final", "class BrokenAuthChallenge final", 1), *auth_inputs[1:]
    )
    assert "auth-session-boundary-checks-missing" in authentication_codec_issues(
        auth_header, auth_source.replace("ReadBits(12)", "ReadBits(8)", 1), *auth_inputs[2:]
    )
    assert "auth-session-typed-ownership-missing" in authentication_codec_issues(
        auth_header,
        auth_source,
        socket_header.replace("std::shared_ptr<WorldPackets::Auth::AuthSession>", "std::shared_ptr<void>", 1),
        *auth_inputs[3:],
    )
    assert "raw-authentication-codec-remains" in authentication_codec_issues(
        auth_header, auth_source, socket_header, socket_source + "\nWorldPacket packet(SMSG_AUTH_RESPONSE);", *auth_inputs[4:]
    )
    assert "auth-response-routing-incomplete" in authentication_codec_issues(
        *auth_inputs[:6], session_mgr.replace("SendAuthResponse(AUTH_OK, false", "SendAuthResponse(AUTH_WAIT_QUEUE, false", 1), auth_tests
    )
    assert "authentication-fixtures-incomplete" in authentication_codec_issues(
        *auth_inputs[:-1], auth_tests.replace("KeepsAllTwelveAccountLengthBits", "BrokenAccountLengthTest", 1)
    )

    character_header = (repo_root / "src/server/game/Server/Packets/CharacterPackets.h").read_text(encoding="utf-8")
    character_source = (repo_root / "src/server/game/Server/Packets/CharacterPackets.cpp").read_text(encoding="utf-8")
    character_handler = (repo_root / "src/server/game/Handlers/CharacterHandler.cpp").read_text(encoding="utf-8")
    character_session = (repo_root / "src/server/game/Server/WorldSession.h").read_text(encoding="utf-8")
    character_tests = (repo_root / "src/test/server/game/Server/Packets/CharacterPacketsTest.cpp").read_text(encoding="utf-8")
    client_runner = (repo_root / "apps/cata/run_real_client_authentication.py").read_text(encoding="utf-8")
    character_inputs = (character_header, character_source, character_handler, character_session, character_tests, client_runner)
    assert "character-screen-live-fixture-missing" in character_enumeration_issues(*character_inputs, "")
    assert "character-enumeration-wire-shape-missing" in character_enumeration_issues(
        character_header, character_source.replace("_worldPacket.WriteBit(Success);", "", 1),
        character_handler, character_session, character_tests, client_runner, "",
    )
    assert "character-screen-runner-incomplete" in character_enumeration_issues(
        character_header, character_source, character_handler, character_session, character_tests,
        client_runner.replace('"--stability-seconds"', '"--hold-seconds"', 1), "",
    )

    character_database = (
        repo_root / "src/server/database/Database/Implementation/CharacterDatabase.cpp"
    ).read_text(encoding="utf-8")
    populated_inputs = (character_database, character_handler, character_tests, client_runner)
    assert "populated-character-list-live-fixture-missing" in populated_character_list_issues(
        *populated_inputs, "",
    )
    assert "populated-character-list-query-mapping-missing" in populated_character_list_issues(
        character_database.replace("COALESCE(c.order, 0)", "c.extra_flags", 1),
        character_handler, character_tests, client_runner, "",
    )
    assert "populated-character-list-packet-fixture-missing" in populated_character_list_issues(
        character_database, character_handler,
        character_tests.replace("WritesSuccessfulPopulatedEnumCharacters", "BrokenPopulatedFixture", 1),
        client_runner, "",
    )
    assert "populated-character-list-runner-missing" in populated_character_list_issues(
        character_database, character_handler, character_tests,
        client_runner.replace('POPULATED_MODE = "populated-character-list"', 'POPULATED_MODE = "broken"', 1),
        "",
    )
    populated_fixture = json.dumps({
        "schema": 1, "plan": 9, "client_build": 15595, "verdict": "PASS",
        "mode": "populated-character-list", "response_body": runner_populated_payload(client_runner),
        "character_rows": 1, "realm_character_count": 1,
        "enumerated": {
            "guid_low": 16909060, "name": "Cataplan", "race": 1, "class": 1, "gender": 0,
            "level": 1, "map": 0, "zone": 12, "list_position": 7, "flags": 0, "flags2": 0,
            "visual_items_nonzero": 0,
        },
        "fresh_runs": 2, "matching_runs": True, "protected_inputs_unchanged": True, "reset": "PASS",
    })
    assert populated_character_list_issues(*populated_inputs, populated_fixture) == ()

    player_login_inputs = (character_source, character_handler, character_tests, client_runner)
    assert "player-login-live-fixture-missing" in player_login_admission_issues(*player_login_inputs, "")
    assert "player-login-packet-tests-missing" in player_login_admission_issues(
        character_source, character_handler,
        character_tests.replace("ReadsBuild15595PlayerLoginGuid", "BrokenPlayerLogin", 1), client_runner, "",
    )
    assert "player-login-callback-boundary-missing" in player_login_admission_issues(
        character_source, character_handler.replace("Player login callback", "Broken callback", 1),
        character_tests, client_runner, "",
    )
    player_login_fixture = json.dumps({
        "schema": 1, "plan": 10, "client_build": 15595, "verdict": "PASS",
        "mode": "character-selection", "request_payload": "E203050002",
        "selection": {
            "action": "enter", "seeded_guid_low": 16909060, "enumerated_guid_low": 16909060,
            "request_guid_low": 16909060, "callback_guid_low": 16909060, "legit_characters_admission": True,
        },
        "fresh_runs": 2, "matching_runs": True, "protected_inputs_unchanged": True,
        "reset": "PASS", "downstream_diagnostic": "load-returned-false",
    })
    assert player_login_admission_issues(*player_login_inputs, player_login_fixture) == ()

    admission = (repo_root / "data/sql/updates/pending_db_auth/rev_1786964293354831242.sql").read_text()
    handoff_runner = (repo_root / "apps/cata/run_authentication_handoff.py").read_text()
    assert build_15595_admission_issues(admission) == ()
    assert "build-15595-admission-incomplete" in build_15595_admission_issues(
        admission.replace("SET DEFAULT 15595", "SET DEFAULT 12340", 1)
    )
    assert "build-15595-rewrites-existing-realm" in build_15595_admission_issues(
        admission + "\nUPDATE realmlist SET gamebuild=15595;\n"
    )
    map_sources = (
        "const uint32 MapVersionMagic      = 10;",
        "static uint32 const MAP_VERSION_MAGIC = 10;",
        "uint32 const MAP_VERSION_MAGIC = 10;",
    )
    assert cataclysm_map_format_issues(*map_sources) == ()
    assert cataclysm_map_format_issues(map_sources[0].replace("= 10", "= 9"), *map_sources[1:]) == (
        "world-map-format-is-not-cataclysm",
    )
    assert "map-extractor-format-is-not-cataclysm" in cataclysm_map_format_issues(
        map_sources[0], map_sources[1].replace("= 10", "= 9"), map_sources[2]
    )
    assert "mmap-generator-format-is-not-cataclysm" in cataclysm_map_format_issues(
        map_sources[0], map_sources[1], map_sources[2].replace("= 10", "= 9")
    )
    assert authentication_handoff_issues(socket_header, auth_tests, handoff_runner) == ()
    assert "world-authentication-test-seam-missing" in authentication_handoff_issues(
        socket_header.replace("friend class WorldAuthenticationHandoffTest;", "", 1), auth_tests, handoff_runner
    )
    assert "world-authentication-handoff-fixture-incomplete" in authentication_handoff_issues(
        socket_header, auth_tests.replace("PLAN6_WORLD_TRANSCRIPT", "BROKEN_TRANSCRIPT", 1), handoff_runner
    )
    assert "authentication-handoff-runner-incomplete" in authentication_handoff_issues(
        socket_header, auth_tests, handoff_runner.replace("recv_exact(connection, 32)", "recv_exact(connection, 34)", 1)
    )

    client_runner = "\n".join((
        '"self-check"', '"prepare"', '"run"', '"verify"', '"replay"', '"reset"', '"compare-last-two"',
        '"--server-dbc-root"', '"--purge-client-base"', '"--purge-database-cache"',
        "database_cache_key", "reconcile_database_cache", "DATABASE_CACHE_VERSION",
        '"mysqldump"', "WRITABLE_CLIENT_DIRS",
        "127.0.0.1", "3724", "connection.log", "network.opcode", CLIENT_SHA256,
    ))
    client_fixture = stable_json({
        "schema": 1,
        "plan": 7,
        "client_build": 15595,
        "client_sha256": CLIENT_SHA256,
        "verdict": "PASS",
        "milestones": [
            "LOGIN_OK", "WORLD_CONNECTED", "COP_AUTHENTICATE_AUTH_OK_TRUE",
            "COP_GET_CHARACTERS_INITIATING", "CMSG_AUTH_SESSION", "SMSG_AUTH_RESPONSE",
        ],
        "fresh_runs": 2,
        "matching_runs": True,
        "plan6_replay_status": "PASS",
        "protected_inputs_unchanged": True,
        "reset": "PASS",
    })
    plan_index = (
        "08\t18\tclosed\tPlan 8: typed empty character enumeration and stable build 15595 screen\t"
        "https://github.com/trolloks/azerothcore-cata/issues/18\n"
        "09\t19\tclosed\tPlan 9: one database-backed build 15595 character\t"
        "https://github.com/trolloks/azerothcore-cata/issues/19\n"
        "10\t20\tclosed\tPlan 10: select the enumerated build 15595 character\t"
        "https://github.com/trolloks/azerothcore-cata/issues/20\n"
    )
    assert real_client_authentication_issues(client_runner, client_fixture, plan_index) == ()
    assert "real-client-authentication-runner-incomplete" in real_client_authentication_issues(
        client_runner.replace('"reset"', '"remove"', 1), client_fixture, plan_index
    )
    assert "real-client-authentication-runner-incomplete" in real_client_authentication_issues(
        client_runner.replace('"--purge-database-cache"', '"--keep-database-cache"', 1),
        client_fixture, plan_index,
    )
    assert "real-client-authentication-fixture-incomplete" in real_client_authentication_issues(
        client_runner, client_fixture.replace('"fresh_runs":2', '"fresh_runs":1', 1), plan_index
    )
    assert "real-client-authentication-fixture-incomplete" in real_client_authentication_issues(
        client_runner, "", plan_index
    )
    assert "plan-issue-index-incomplete" in real_client_authentication_issues(
        client_runner, client_fixture, plan_index.replace("\t18\tclosed\t", "\t18\topen\t", 1)
    )

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
