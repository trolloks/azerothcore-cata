#!/usr/bin/env python3
"""Emit pending_db_world SQL for the rows an import_starter_content.py run added (#203).

Compares a baseline AC world schema with the imported one in the run-owned disposable database and writes,
for every table, the rows whose primary key is new, as `DELETE ... ; INSERT ...` (the DELETE keeps the file
re-runnable; the SQL linter exempts creature_template, gameobject_template, item_template and quest_template
from both). The importer never modifies native rows, so the tool refuses to emit if a row that exists in both
schemas differs.

Prints SQL to stdout, or to --out. Never writes to a database.
"""

import argparse
import re
import sys

from import_starter_content import run, scalar

NO_DELETE = {"creature_template", "gameobject_template", "item_template", "quest_template"}
SPAWN = {"creature", "gameobject"}
SKIP = {"_plan203_guid_map"}
NUMERIC = {"tinyint", "smallint", "mediumint", "int", "bigint", "float", "double", "decimal"}
CHUNK = 500


def table_columns(c, u, p, schema, table):
    return [(name, kind) for name, kind in run(
        c, u, p, f"SELECT COLUMN_NAME, DATA_TYPE FROM information_schema.columns "
                 f"WHERE TABLE_SCHEMA='{schema}' AND TABLE_NAME='{table}' ORDER BY ORDINAL_POSITION")]


def primary_key(c, u, p, schema, table):
    return [row[0] for row in run(
        c, u, p, f"SELECT COLUMN_NAME FROM information_schema.key_column_usage WHERE TABLE_SCHEMA='{schema}' "
                 f"AND TABLE_NAME='{table}' AND CONSTRAINT_NAME='PRIMARY' ORDER BY ORDINAL_POSITION")]


def literal(column, kind):
    if kind in NUMERIC:
        return f"IFNULL(CAST(a.`{column}` AS CHAR),'NULL')"
    if kind in ("blob", "tinyblob", "mediumblob", "longblob", "binary", "varbinary"):
        return f"IFNULL(CONCAT('0x',HEX(a.`{column}`)),'NULL')"
    # mysql -B shows tab/newline as \\t/\\n text and doubles backslashes (undone in unbatch()); keep the
    # literal on one line by escaping the control characters ourselves.
    return (f"REPLACE(REPLACE(REPLACE(QUOTE(a.`{column}`),CHAR(10),'\\\\n'),CHAR(13),'\\\\r'),"
            f"CHAR(9),'\\\\t')")


def unbatch(value):
    return value.replace("\\\\", "\\")


def join_on(keys):
    return " AND ".join(f"b.`{k}` <=> a.`{k}`" for k in keys)


def delete_sql(table, keys, rows):
    """rows: list of tuples of key values (strings, already SQL literals)."""
    if table in SPAWN:
        # The linter wants both `id` and `guid`; rows[...] holds (guid, id) for spawns.
        guids = ",".join(guid for guid, _ in rows)
        ids = ",".join(sorted({entry for _, entry in rows}))
        return f"DELETE FROM `{table}` WHERE `id` IN ({ids}) AND `guid` IN ({guids});"
    if len(keys) == 1:
        return f"DELETE FROM `{table}` WHERE `{keys[0]}` IN ({','.join(row[0] for row in rows)});"
    cols = ",".join(f"`{k}`" for k in keys)
    tuples = ",".join("(" + ",".join(row) + ")" for row in rows)
    return f"DELETE FROM `{table}` WHERE ({cols}) IN ({tuples});"


def emit_table(c, u, p, base, after, table):
    """Return the SQL for one table, "" when it has no new rows; raise if a shared row differs."""
    keys = primary_key(c, u, p, after, table)
    cols = table_columns(c, u, p, after, table)
    base_cols = {name for name, _ in table_columns(c, u, p, base, table)}
    if not base_cols:
        raise SystemExit(f"{table}: missing in the baseline; cannot diff safely")
    shared = [name for name, _ in cols if name in base_cols]
    if keys:
        same = " AND ".join(f"a.`{n}` <=> b.`{n}`" for n in shared)
        changed = scalar(c, u, p, f"SELECT COUNT(*) FROM {after}.`{table}` a JOIN {base}.`{table}` b ON "
                                  f"{join_on(keys)} WHERE NOT ({same})")
        if changed:
            raise SystemExit(f"{table}: {changed} rows that exist in the baseline differ; the import must not "
                             f"modify native rows")
    else:  # no primary key: a row is new when no baseline row matches on every column
        keys = [name for name, _ in cols]
    new_from = (f"FROM {after}.`{table}` a WHERE NOT EXISTS "
                f"(SELECT 1 FROM {base}.`{table}` b WHERE {join_on(keys)})")
    total = scalar(c, u, p, f"SELECT COUNT(*) {new_from}")
    if not total:
        return ""
    values = ",".join(literal(name, kind) for name, kind in cols)
    kinds = dict(cols)
    key_exprs = ("CAST(a.`guid` AS CHAR),CAST(a.`id` AS CHAR)" if table in SPAWN
                 else ",".join(literal(key, kinds[key]) for key in keys))
    order = ",".join(f"a.`{k}`" for k in keys)
    out = [f"-- {table}: {total} rows"]
    names = ",".join(f"`{name}`" for name, _ in cols)
    for offset in range(0, total, CHUNK):
        page = f"{new_from} ORDER BY {order} LIMIT {CHUNK} OFFSET {offset}"
        rows = [[unbatch(cell) for cell in row] for row in run(
            c, u, p, f"SELECT CONCAT('(',CONCAT_WS(',',{values}),')'), {key_exprs} {page}")]
        if table not in NO_DELETE:
            out.append(delete_sql(table, keys, [tuple(r[1:]) for r in rows]))
        out.append(f"INSERT INTO `{table}` ({names}) VALUES")
        out.append(",\n".join(r[0] for r in rows) + ";")
    return "\n".join(out)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--container", default="plan175-inventory-mysql")
    parser.add_argument("--user", default="root")
    parser.add_argument("--password", default="plan175ro")
    parser.add_argument("--baseline", required=True, help="schema before the import")
    parser.add_argument("--after", required=True, help="schema after the import")
    parser.add_argument("--header", default="", help="comment lines written at the top of the file")
    parser.add_argument("--out", help="write here instead of stdout")
    args = parser.parse_args()
    for name in (args.baseline, args.after):
        if not re.fullmatch(r"\w+", name):
            parser.error(f"invalid schema name: {name!r}")
    c, u, p = args.container, args.user, args.password
    tables = [row[0] for row in run(c, u, p, f"SELECT TABLE_NAME FROM information_schema.tables WHERE "
                                             f"TABLE_SCHEMA='{args.after}' AND TABLE_TYPE='BASE TABLE' "
                                             f"ORDER BY TABLE_NAME") if row[0] not in SKIP]
    parts = [emit_table(c, u, p, args.baseline, args.after, table) for table in tables]
    body = "\n\n".join(part for part in parts if part)
    header = "".join(f"-- {line}\n" for line in args.header.splitlines()) + ("\n" if args.header else "")
    text = header + body + "\n"
    if args.out:
        with open(args.out, "w") as handle:
            handle.write(text)
    else:
        sys.stdout.write(text)


if __name__ == "__main__":
    sys.exit(main())
