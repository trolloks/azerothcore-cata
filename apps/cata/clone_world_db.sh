#!/usr/bin/env bash
# Clone one schema of the run-owned disposable MySQL container into another, server-side (#203).
# Replaying data/sql into a fresh schema takes ~15 minutes; build one golden schema from master
# once and clone it per test (baseline / import target / worldserver load check).
#   clone_world_db.sh <source_schema> <target_schema>      (target is dropped and recreated)
set -euo pipefail

SRC=${1:?source schema}
DST=${2:?target schema}
CONTAINER=${CONTAINER:-plan175-inventory-mysql}
PASSWORD=${PASSWORD:-plan175ro}
[[ $SRC =~ ^[A-Za-z0-9_]+$ && $DST =~ ^[A-Za-z0-9_]+$ ]] || { echo "invalid schema name" >&2; exit 2; }

mysql() { docker exec -i "$CONTAINER" mysql -uroot -p"$PASSWORD" "$@" 2> >(grep -v 'Using a password' >&2); }

[[ $SRC != "$DST" ]] || { echo "source and target must differ" >&2; exit 2; }
TABLES=$(mysql -N -B -e "SELECT COUNT(*) FROM information_schema.tables WHERE TABLE_SCHEMA='$SRC' AND TABLE_TYPE='BASE TABLE'")
[[ $TABLES -gt 0 ]] || { echo "source schema $SRC has no tables" >&2; exit 2; }

mysql -e "DROP DATABASE IF EXISTS \`$DST\`; CREATE DATABASE \`$DST\`"
{
  echo "SET foreign_key_checks=0, unique_checks=0;"
  mysql -N -B -e "SELECT TABLE_NAME FROM information_schema.tables WHERE TABLE_SCHEMA='$SRC' AND TABLE_TYPE='BASE TABLE'" |
    while read -r t; do
      echo "CREATE TABLE \`$DST\`.\`$t\` LIKE \`$SRC\`.\`$t\`;"
      echo "INSERT INTO \`$DST\`.\`$t\` SELECT * FROM \`$SRC\`.\`$t\`;"
    done
} | mysql
echo "cloned $SRC -> $DST"
