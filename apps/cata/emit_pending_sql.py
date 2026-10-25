#!/usr/bin/env python3
"""Emit pending_db_world SQL for the rows an import_starter_content.py run added (#203).

Compares a baseline AC world schema with the imported one in the run-owned disposable database and writes,
for every table, the rows whose primary key is new, as `DELETE ... ; INSERT ...` (the DELETE keeps the file
re-runnable; the SQL linter exempts creature_template, gameobject_template, item_template and quest_template
from both). The importer never modifies native rows, so the tool refuses to emit if a row that exists in both
schemas differs, except the fill-only columns in FILL (a native template's empty AIName or lootid), which become
guarded UPDATEs.

The file assumes the default SQL mode and a utf8mb4 client (the AC updater's). Tables with new rows must not
have ENUM, SET, JSON or TIMESTAMP columns. Prints SQL to stdout, or to --out. Never writes to a database.
"""

import argparse
import re
import sys

from import_starter_content import run, scalar

NO_DELETE = {"creature_template", "gameobject_template", "item_template", "quest_template"}
SPAWN = {"creature", "gameobject"}
SKIP = {"_plan203_guid_map"}
NUMERIC = {"tinyint", "smallint", "mediumint", "int", "bigint", "double", "decimal"}
IDENT = re.compile(r"\w+")
# Not lossless as text (ENUM/SET index 0, typed JSON) or session-dependent (TIMESTAMP time zone); the world database
# only has them in tables an import never touches, so a table with new rows is refused instead of guessed at.
UNSAFE_TYPES = {"enum", "set", "json", "timestamp"}
CHUNK = 500
# Native template columns the import may fill (never overwrite): column -> the value that counts as empty.
# They are emitted as guarded UPDATEs; any other difference in a shared row still refuses.
FILL = {"creature_template": {"AIName": "''", "lootid": "0"}, "gameobject_template": {"AIName": "''"}}
FILL_AI = ("SmartAI", "SmartGameObjectAI")


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
    if kind == "float":  # CAST(float AS CHAR) keeps ~6 digits; use the exact double text when that loses any
        return (f"IFNULL(IF(CAST(CAST(a.`{column}` AS CHAR) AS FLOAT)=a.`{column}`,CAST(a.`{column}` AS CHAR),"
                f"CAST(a.`{column}`+0e0 AS CHAR)),'NULL')")
    if kind == "bit":
        return f"IFNULL(CAST(a.`{column}`+0 AS CHAR),'NULL')"
    if kind in ("blob", "tinyblob", "mediumblob", "longblob", "binary", "varbinary"):
        return f"IFNULL(CONCAT(\"X'\",HEX(a.`{column}`),\"'\"),'NULL')"
    # mysql -B shows tab/newline as \\t/\\n text and doubles backslashes (undone in unbatch()); keep the
    # literal on one line by escaping the control characters ourselves.
    return (f"REPLACE(REPLACE(REPLACE(QUOTE(a.`{column}`),CHAR(10),'\\\\n'),CHAR(13),'\\\\r'),"
            f"CHAR(9),'\\\\t')")


def unbatch(value):
    return value.replace("\\\\", "\\")


def same_row(kinds, names, left, right):
    """Null-safe equality of the named columns; text is compared as binary (the collation hides case/accents)."""
    return " AND ".join(
        f"{left}.`{n}` <=> {right}.`{n}`" if kinds[n] in NUMERIC | {"float"}
        else f"BINARY {left}.`{n}` <=> BINARY {right}.`{n}`" for n in names)


def join_on(keys):
    return " AND ".join(f"b.`{k}` <=> a.`{k}`" for k in keys)


def delete_sql(table, keys, rows):
    """rows: list of tuples of key values (strings, already SQL literals)."""
    if table == "smart_scripts":  # a script is replaced as a whole (entryorguid, source_type) block
        blocks = ",".join(sorted({f"({row[0]},{row[1]})" for row in rows}))
        return f"DELETE FROM `{table}` WHERE (`entryorguid`, `source_type`) IN ({blocks});"
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
    if not IDENT.fullmatch(table):
        raise SystemExit(f"unsafe table name {table!r}")
    keys = primary_key(c, u, p, after, table)
    cols = table_columns(c, u, p, after, table)
    if any(not IDENT.fullmatch(name) for name, _ in cols):
        raise SystemExit(f"{table}: unsafe column name")
    base_cols = {name for name, _ in table_columns(c, u, p, base, table)}
    if not base_cols:
        raise SystemExit(f"{table}: missing in the baseline; cannot diff safely")
    fill = FILL.get(table, {})
    shared = [name for name, _ in cols if name in base_cols and name not in fill]
    fill_sql = ""
    if keys:
        same = same_row(dict(cols), shared, "a", "b")
        gone = scalar(c, u, p, f"SELECT COUNT(*) FROM {base}.`{table}` b WHERE NOT EXISTS "
                               f"(SELECT 1 FROM {after}.`{table}` a WHERE {join_on(keys)})")
        if gone:
            raise SystemExit(f"{table}: {gone} baseline rows are missing after the import")
        changed = scalar(c, u, p, f"SELECT COUNT(*) FROM {after}.`{table}` a JOIN {base}.`{table}` b ON "
                                  f"{join_on(keys)} WHERE NOT ({same})")
        if changed:
            raise SystemExit(f"{table}: {changed} rows that exist in the baseline differ; the import must not "
                             f"modify native rows")
        fill_sql = emit_fill(c, u, p, base, after, table, keys, fill)
    else:  # no primary key: new rows cannot be replayed safely, so the contents must be identical
        same = same_row(dict(cols), shared, "x", "y")

        def unmatched(left, right):
            return scalar(c, u, p, f"SELECT COUNT(*) FROM {left}.`{table}` x WHERE NOT EXISTS "
                                   f"(SELECT 1 FROM {right}.`{table}` y WHERE {same})")

        total_rows = scalar(c, u, p, f"SELECT COUNT(*) FROM {after}.`{table}`")
        if (total_rows != scalar(c, u, p, f"SELECT COUNT(*) FROM {base}.`{table}`")
                or unmatched(after, base) or unmatched(base, after)):
            raise SystemExit(f"{table}: has no primary key and its contents changed; refusing to diff")
        return ""
    new_from = (f"FROM {after}.`{table}` a WHERE NOT EXISTS "
                f"(SELECT 1 FROM {base}.`{table}` b WHERE {join_on(keys)})")
    total = scalar(c, u, p, f"SELECT COUNT(*) {new_from}")
    if not total:
        return fill_sql
    if table == "smart_scripts":  # the block DELETE would drop native rows of a partly-new block
        partial = scalar(c, u, p, f"SELECT COUNT(*) FROM {base}.`{table}` b WHERE EXISTS (SELECT 1 {new_from} "
                                  f"AND a.`entryorguid`=b.`entryorguid` AND a.`source_type`=b.`source_type`)")
        if partial:
            raise SystemExit(f"{table}: {partial} native rows share a block with new rows; refusing a block delete")
    unsafe = [name for name, kind in cols if kind in UNSAFE_TYPES]
    if unsafe:
        raise SystemExit(f"{table}: new rows with {', '.join(unsafe)} (enum/set/json/timestamp) cannot be "
                         f"emitted exactly")
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
    if fill_sql:
        out.append(fill_sql)
    return "\n".join(out)


def emit_fill(c, u, p, base, after, table, keys, fill):
    """Guarded UPDATEs for the columns of native rows the import filled; refuse any other change to them."""
    out = []
    key = keys[0]
    for column, empty in fill.items():
        pair = f"FROM {after}.`{table}` a JOIN {base}.`{table}` b ON {join_on(keys)} WHERE NOT (a.`{column}` <=> b.`{column}`)"
        allowed = f"b.`{column}`={empty}" + (
            " AND a.`AIName` IN (" + ",".join(f"'{v}'" for v in FILL_AI) + ")" if column == "AIName" else "")
        bad = scalar(c, u, p, f"SELECT COUNT(*) {pair} AND NOT ({allowed})")
        if bad:
            raise SystemExit(f"{table}: {bad} rows changed `{column}` away from its empty value")
        if column == "lootid":
            for entry, value in run(c, u, p, f"SELECT a.`{key}`, a.`{column}` {pair} ORDER BY a.`{key}`"):
                out.append(f"UPDATE `{table}` SET `{column}`={value} WHERE `{key}`={entry} AND `{column}`={empty};")
        else:
            for value in fetch_column(c, u, p, f"SELECT DISTINCT a.`{column}` {pair}"):
                entries = fetch_column(c, u, p, f"SELECT a.`{key}` {pair} AND a.`{column}`='{value}' "
                                                f"ORDER BY a.`{key}`")
                out.append(f"UPDATE `{table}` SET `{column}`='{value}' WHERE `{key}` IN ({','.join(entries)}) "
                           f"AND `{column}`={empty};")
    return "\n".join(out)


def fetch_column(c, u, p, sql):
    return [row[0] for row in run(c, u, p, sql)]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--container", default="plan175-inventory-mysql")
    parser.add_argument("--user", default="root")
    parser.add_argument("--password", default="plan175ro")
    parser.add_argument("--baseline", required=True, help="schema before the import")
    parser.add_argument("--after", required=True, help="schema after the import")
    parser.add_argument("--header", default="", help="comment lines written at the top of the file")
    parser.add_argument("--out", help="write here instead of stdout")
    parser.add_argument("--tables", help="comma-separated tables to compare (default: all); for checks")
    args = parser.parse_args()
    for name in (args.baseline, args.after):
        if not re.fullmatch(r"\w+", name):
            parser.error(f"invalid schema name: {name!r}")
    c, u, p = args.container, args.user, args.password
    tables = [row[0] for row in run(c, u, p, f"SELECT TABLE_NAME FROM information_schema.tables WHERE "
                                             f"TABLE_SCHEMA='{args.after}' AND TABLE_TYPE='BASE TABLE' "
                                             f"ORDER BY TABLE_NAME") if row[0] not in SKIP]
    missing = [row[0] for row in run(c, u, p, f"SELECT TABLE_NAME FROM information_schema.tables WHERE "
                                               f"TABLE_SCHEMA='{args.baseline}' AND TABLE_TYPE='BASE TABLE'")
               if row[0] not in SKIP and row[0] not in tables]
    if missing:
        raise SystemExit(f"tables only in the baseline: {', '.join(missing)}")
    if args.tables:  # after the baseline-only check, which must see every table
        tables = [t for t in args.tables.split(",") if t in tables]
    parts = [emit_table(c, u, p, args.baseline, args.after, table) for table in tables]
    body = "\n\n".join(part for part in parts if part)
    header = "".join(f"-- {line}\n" for line in args.header.splitlines()) + ("\n" if args.header else "")
    text = header + body + "\n"
    if args.out:
        with open(args.out, "w", encoding="utf-8") as handle:
            handle.write(text)
    else:
        sys.stdout.write(text)


if __name__ == "__main__":
    sys.exit(main())
