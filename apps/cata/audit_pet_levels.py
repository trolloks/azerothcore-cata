"""Audit pet_levelstats coverage against the 4.3.4 level cap.

The clean world used to stop at level 80: LoadPetLevelInfo filled 81-85 by copying level 80
and logged one error per creature per level. The builder now imports TDB 434's 81-85 stats and
extends the 3.3.5a-only pets, so every entry must cover level 1..cap, and the new rows must
keep the damage base of level 80.

python3 apps/cata/audit_pet_levels.py [--cap 85] [--schema acore_world_gold]
"""

import argparse
import os
import subprocess


def db_query(host: str, port: str, user: str, password: str, sql: str) -> list[str]:
    result = subprocess.run(["mysql", "-h", host, "-P", port, "-u", user, "-N", "-e", sql],
                            capture_output=True, text=True, check=False,
                            env={**os.environ, "MYSQL_PWD": password})
    if result.returncode:
        raise SystemExit(f"database query failed: {result.stderr.strip()}")
    return [line for line in result.stdout.splitlines() if line.strip()]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default="43306")
    parser.add_argument("--user", default="root")
    parser.add_argument("--password", default="plan175ro")
    parser.add_argument("--schema", default="acore_world_gold")
    parser.add_argument("--cap", type=int, default=85)
    args = parser.parse_args()

    table = f"{args.schema}.pet_levelstats"
    entries, rows, max_level = (int(value) for value in db_query(
        args.host, args.port, args.user, args.password,
        f"SELECT COUNT(DISTINCT creature_entry), COUNT(*), MAX(`level`) FROM {table}")[0].split())
    assert max_level == args.cap, f"pet_levelstats stops at level {max_level}; the cap is {args.cap}"

    outside = int(db_query(args.host, args.port, args.user, args.password,
                           f"SELECT COUNT(*) FROM {table} "
                           f"WHERE `level` NOT BETWEEN 1 AND {args.cap}")[0])
    assert outside == 0, f"{outside} rows outside levels 1..{args.cap}"

    gaps = db_query(args.host, args.port, args.user, args.password,
                    f"SELECT creature_entry, COUNT(*) FROM {table} "
                    f"WHERE `level` BETWEEN 1 AND {args.cap} "
                    f"GROUP BY creature_entry HAVING COUNT(*) < {args.cap}")
    assert not gaps, (f"{len(gaps)} pet entries miss levels 1..{args.cap}: "
                      + ", ".join(row.split()[0] for row in gaps[:10]))

    wrong = int(db_query(args.host, args.port, args.user, args.password,
                         f"SELECT COUNT(*) FROM {table} p JOIN {table} l "
                         f"ON l.creature_entry=p.creature_entry AND l.`level`=80 "
                         f"WHERE p.`level` BETWEEN 81 AND {args.cap} "
                         f"AND (p.min_dmg<>ROUND(l.min_dmg*p.`level`/80) "
                         f"OR p.max_dmg<>ROUND(l.max_dmg*p.`level`/80))")[0])
    assert wrong == 0, f"{wrong} rows above level 80 do not keep the level-80 damage base"

    assert rows == entries * args.cap, (
        f"{rows} rows for {entries} entries; expected {entries * args.cap}")

    print(f"pet_levelstats: {entries} entries x levels 1..{args.cap} ({rows} rows)")
    print(f"level-80 damage base kept on levels 81..{args.cap}")


if __name__ == "__main__":
    main()
