#!/usr/bin/env bash
# Import the Cataclysm starter-zone quests into the clean world schema (#274).
# DBC_DIR must point at the 4.3.4 client dbc directory; the reports go to OUT_DIR (/tmp by default).
set -uo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
target="${1:-acore_world_cata}"
: "${DBC_DIR:?set DBC_DIR to the 4.3.4 client dbc directory}"
out="${OUT_DIR:-/tmp}"

# Kezan (goblin) and Gilneas (worgen) are imported for data completeness, including their
# sub-areas (Lost Isles 4720, Gilneas City 4755), but their start experiences need phasing (#180);
# they are not part of the current in-world pass.
while read -r zone map label sorts; do
    [ -z "${zone:-}" ] && continue
    case "$zone" in \#*) continue ;; esac
    args=(--zone "$zone" --map "$map" --target "$target" --dbc-dir "$DBC_DIR" --apply)
    [ -n "${sorts:-}" ] && args+=(--quest-sorts "$sorts")
    echo "== $label"
    python3 "$here/import_starter_content.py" "${args[@]}" > "$out/clean-$label.json"
    status=$?
    # 2 means the zone imported but loader-level dangling references were reported (kept in the JSON).
    if [ "$status" -ne 0 ] && [ "$status" -ne 2 ]; then
        echo "zone import failed for $label (exit $status)" >&2
        exit "$status"
    fi
done <<'ZONES'
14 1 durotar 368
215 1 mulgore
4737 648 kezan 4720
4714 654 gilneas 4755
12 0 elwynn
40 0 westfall
44 0 redridge
1 0 dunmorogh
38 0 lochmodan
141 1 teldrassil
148 1 darkshore
3524 530 azuremyst
3525 530 bloodmyst
17 1 northernbarrens
85 0 tirisfal
130 0 silverpine
3430 530 eversong
3433 530 ghostlands
16 1 azshara
ZONES

echo "== syncing quest_template_addon"
if ! python3 "$here/import_starter_content.py" --target "$target" --sync-addon --apply \
        > "$out/clean-links.json"; then
    echo "quest addon sync failed" >&2
    exit 1
fi
