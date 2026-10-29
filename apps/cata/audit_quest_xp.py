"""Audit the quest and exploration XP data against the 4.3.4 tables.

Checks the client QuestXP.dbc shape, the quest RewardXPDifficulty range in the
clean world, and that exploration_basexp is populated (the clean world used to
carry zero rows, so every area discovery rewarded no XP).

python3 apps/cata/audit_quest_xp.py [--dbc-dir DIR] [--container NAME]
"""

import argparse
import os
import struct
import subprocess
import sys
from pathlib import Path


def db_query(host: str, port: str, user: str, password: str, sql: str) -> list[str]:
    result = subprocess.run(["mysql", "-h", host, "-P", port, "-u", user, "-N", "-e", sql],
                            capture_output=True, text=True, check=False,
                            env={**os.environ, "MYSQL_PWD": password})
    if result.returncode:
        raise SystemExit(f"database query failed: {result.stderr.strip()}")
    return [line for line in result.stdout.splitlines() if line.strip()]


def read_dbc(path: Path) -> tuple[int, int, int]:
    header = path.read_bytes()[:20]
    magic, records, fields, record_size, _string_size = struct.unpack("<4sIIII", header)
    if magic != b"WDBC":
        raise SystemExit(f"{path} is not a WDBC file")
    return records, fields, record_size


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dbc-dir", type=Path, default=Path("/tmp/opencode/plan-clean-world/generation-4/data/dbc"))
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default="43306")
    parser.add_argument("--user", default="root")
    parser.add_argument("--password", default="plan175ro")
    parser.add_argument("--schema", default="acore_world_gold")
    args = parser.parse_args()

    quest_xp = args.dbc_dir / "QuestXP.dbc"
    if not quest_xp.exists():
        raise SystemExit(f"missing {quest_xp}; pass --dbc-dir")
    records, fields, record_size = read_dbc(quest_xp)
    assert records == 100 and fields == 11 and record_size == 44, (
        f"QuestXP.dbc is not the 4.3.4 100x11/44 layout: {records}x{fields}/{record_size}")

    difficulty = db_query(args.host, args.port, args.user, args.password, f"SELECT MAX(RewardXPDifficulty), COUNT(*) FROM {args.schema}.quest_template")
    max_difficulty, quests = (int(value) for value in difficulty[0].split())
    assert max_difficulty <= 9, f"RewardXPDifficulty {max_difficulty} exceeds QuestXP.dbc's 10 columns"

    exploration = int(db_query(args.host, args.port, args.user, args.password, f"SELECT COUNT(*) FROM {args.schema}.exploration_basexp")[0])
    assert exploration == 80, f"exploration_basexp has {exploration} rows; expected the 80-row 4.3.4 table"

    print(f"QuestXP.dbc: {records} records x {fields} fields ({record_size} bytes)")
    print(f"quests: {quests}, RewardXPDifficulty 0..{max_difficulty}")
    print(f"exploration_basexp: {exploration} rows")


if __name__ == "__main__":
    main()
