#!/usr/bin/env python3
"""Run the isolated Plan 7 build-15595 real-client authentication proof."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import secrets
import shutil
import signal
import socket
import struct
import subprocess
import sys
import tempfile
import time
from typing import Any, Literal, TypedDict
import uuid


REPO_ROOT = Path(__file__).resolve().parents[2]
MYSQL_IMAGE = "mysql:8.4"
DATABASE_CACHE_VERSION = 1
CLIENT_BUILD = 15595
CLIENT_SHA256 = "0ea9cc0458cb137f4e0767e8a1896e2042480a4a22397480d7ee64c2e696192a"
ACCOUNT = "PLAN6USER"
PASSWORD = "PLAN6PASS"
ACCOUNT_ID = 900000
REALM_ID = 42
AUTH_PORT = 3724
WRITABLE_CLIENT_DIRS = ("WTF", "Cache", "Logs", "Errors", "Screenshots")
SRP_N = int("894B645E89E1535BBDAD5B8B290650530801B18EBFBF5E8FAB3C82872A3E9BB7", 16)
SRP_G = 7
DEFAULT_MIGRATION = REPO_ROOT / "data/sql/updates/pending_db_auth/rev_1786964293354831242.sql"
DEFAULT_FIXTURE = REPO_ROOT / "apps/cata/fixtures/plan7-client-authentication.json"
PLAN6_RUNNER = REPO_ROOT / "apps/cata/run_authentication_handoff.py"
DATABASE_SQL_DIRECTORIES = tuple(
    (f"{kind}-{database}", REPO_ROOT / f"data/sql/{kind}/db_{database}")
    for kind in ("base", "updates") for database in ("auth", "characters", "world")
)

State = Literal[
    "allocating", "prepared", "servers_ready", "client_running", "observed", "accepted",
    "failed", "inconclusive", "replayed", "reset",
]


class ProcessIdentity(TypedDict, total=False):
    kind: str
    pid: int
    start_ticks: int
    boot_id: str
    exe: str
    cmdline: list[str]
    cwd: str
    config: str
    prefix: str
    process_group: int
    session: int
    active: bool


class Generation(TypedDict, total=False):
    number: int
    mode: str
    state: State
    completed_state: str
    root: str
    prefix: str
    ports: dict[str, int]
    docker: dict[str, str | None]
    schemas: dict[str, str]
    processes: list[ProcessIdentity]
    inputs: dict[str, object]
    paths: dict[str, str]
    released_updates: dict[str, list[str]]
    database_cache: dict[str, str]
    issue97_stages: list[dict[str, object]]
    evidence: dict[str, object] | None
    failure: dict[str, str] | None
    isolation_unchanged: bool
    replay_passed: bool


class Manifest(TypedDict, total=False):
    version: int
    run_id: str
    repo_commit: str
    root: str
    mysql_root_password: str
    mysql_user: str
    mysql_password: str
    baseline: dict[str, object]
    client_base: dict[str, object]
    database_cache: dict[str, object]
    generations: list[Generation]


TRANSITIONS: dict[str, set[str]] = {
    "allocating": {"prepared", "failed", "inconclusive", "reset"},
    "prepared": {"servers_ready", "failed", "inconclusive", "reset"},
    "servers_ready": {"client_running", "failed", "inconclusive", "reset"},
    "client_running": {"observed", "failed", "inconclusive", "reset"},
    "observed": {"accepted", "failed", "inconclusive", "reset"},
    "accepted": {"replayed", "failed", "inconclusive", "reset"},
    "failed": {"reset"},
    "inconclusive": {"accepted", "failed", "inconclusive", "reset"},
    "replayed": {"reset"},
    "reset": set(),
}

CLIENT_MILESTONES = (
    ("auth_login_ok", re.compile(r"LOGIN_STATE_AUTHENTICATED.*LOGIN_OK", re.IGNORECASE)),
    ("world_connected", re.compile(r"COP_CONNECT.*RESPONSE_CONNECTED.*TRUE", re.IGNORECASE)),
    ("world_auth_ok", re.compile(r"COP_AUTHENTICATE.*AUTH_OK.*TRUE", re.IGNORECASE)),
    ("characters_started", re.compile(r"Initiating: COP_GET_CHARACTERS", re.IGNORECASE)),
)
CHARACTER_MILESTONES = CLIENT_MILESTONES[:3] + (
    ("characters_completed", re.compile(r"Completed: COP_GET_CHARACTERS.*TRUE", re.IGNORECASE)),
)
FORBIDDEN_CHARACTER_OPCODES = frozenset({
    "CMSG_CHAR_CREATE", "CMSG_CHAR_DELETE", "CMSG_CHAR_CUSTOMIZE", "CMSG_CHAR_FACTION_CHANGE",
    "CMSG_CHAR_RACE_CHANGE", "CMSG_CHAR_RENAME", "CMSG_PLAYER_LOGIN",
})
EMPTY_CHARACTER_ENUM_PAYLOAD = "000001000000"
POPULATED_CHARACTER_ENUM_PAYLOAD = (
    "0000010000C08046000100000000000000000000000000000000000000000000000000000000000000000000000000000000"
    "0000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000"
    "0000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000"
    "0000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000"
    "000000000000000000000000000000000000000000070000000000000000000000000000F90FA7420000000000357E04C300"
    "000000000043617461706C616E000503CDD70BC6000101020C000000"
)
POPULATED_MODE = "populated-character-list"
CHARACTER_SELECTION_MODE = "character-selection"
INITIAL_POST_LOAD_PACKETS_MODE = "initial-post-load-packets"
MAP_INSERTION_MODE = "map-insertion-object-bootstrap"
IN_WORLD_CONTROL_MODE = "in-world-control-bootstrap"
MAGE_COMBAT_MODE = "mage-combat"
MAGE_FOLLOWUP_MODE = "mage-combat-followup"
MAGE_MODES = frozenset({MAGE_COMBAT_MODE, MAGE_FOLLOWUP_MODE})
BASIC_MOVEMENT_MODE = "basic-movement"
RUN_SPEED_MODE = "run-speed-change"
GROUND_MOVEMENT_MODE = "ground-movement"
JUMP_FALL_LAND_MODE = "jump-fall-land"
WALK_RUN_MODE_SWITCH_MODE = "walk-run-mode-switch"
CHARACTER_CREATION_MODE = "character-creation"
BASIC_SPELL_CAST_MODE = "basic-spell-cast"
RUN_SPEED_AURA = 2983
RUN_SPEED_AURA_AMOUNT = 50
RUN_SPEED_AURA_DURATION_MS = 60000
# Evocation: self-only (no enemy target required, so a bare keypress can cast it) and free
# (0 mana cost, so it works regardless of the fixture's class/power pool). Channeled with a
# real duration, so the round trip still exercises SMSG_SPELL_START (channel begin) as well as
# SMSG_SPELL_GO (channel start-of-effect), not just an instant-cast fire-and-forget. Fireball
# (id 133, used by SpellCastPacketFormatTest) was tried first but is enemy-targeted: with no
# target selected the client never sends CMSG_CAST_SPELL at all (confirmed empirically -
# generation 5 showed zero CMSG_CAST_SPELL/SMSG_CAST_FAILED packets after the in-world-control
# marker). The fixture character does not otherwise know any spells (see
# populated_character_seed_sql), so this is granted directly via character_spell/character_action
# rather than through class/race starting-spell data (see basic_spell_cast_seed_sql).
BASIC_SPELL_CAST_SPELL_ID = 12051
BASIC_SPELL_CAST_ACTION_BUTTON = 0
BASIC_SPELL_CAST_CLASS = 8  # Mage: Evocation's skill line requires this, see basic_spell_cast_seed_sql
TARGET_TARGETED_SPELL_CAST_MODE = "target-targeted-spell-cast"
# Fireball (id 133, used by SpellCastPacketFormatTest): enemy-only-targeted with a real,
# non-zero cast time (unlike the self-only Evocation basic-spell-cast uses), which lets
# this mode cover both remaining issue #85 acceptance items in one pass: a target-targeted cast
# that completes (SMSG_SPELL_GO) and a cast-time cancellation. Cancelling mid-cast is the client's
# built-in Escape behavior, which sends CMSG_CANCEL_CAST; server-side that hits Spell::cancel's
# SPELL_STATE_PREPARING branch, which calls SendCastResult(SPELL_FAILED_INTERRUPTED) - a genuine
# SMSG_CAST_FAILED, not a client-only UI cancel. Requires a live, Tab-targetable, in-range hostile
# unit near the fixture's spawn point -
# see target_targeted_spell_cast_world_seed_sql. An earlier version of this mode pinned the
# nearby Rabbit (guid 79947) stationary instead, but generation 17 showed zero
# CMSG_CAST_SPELL/SMSG_CAST_FAILED/SMSG_SPELL_GO packets after the marker: the Rabbit's faction
# (31, a critter faction) is not hostile, so "target nearest enemy" never selects it and the
# enemy-only Fireball hotkey silently goes into targeting-reticle mode instead of casting.
TARGET_TARGETED_SPELL_CAST_SPELL_ID = 133
TARGET_TARGETED_SPELL_CAST_ACTION_BUTTON = 0
# Entry 2673 "Target Dummy" (ScriptName npc_target_dummy) was tried first for the hostile fixture
# below, but generation 18 still showed zero SMSG_CAST_FAILED after the marker: that AI's Reset()
# schedules a KillSelf() 15s after every spawn (npcs_special.cpp) - it's meant for a dummy a player
# summons and is not designed to persist as a static DB spawn, so by the time the harness logs in
# and reaches the cast sequence it is already dead and un-Tab-targetable. Entry 2809 "Boar" (faction
# 14, same hostile-to-all monster faction, no ScriptName/special AI) has no such lifespan and is
# inserted fresh near CHARACTER_POSITION instead, rather than reusing a creature already spawned
# nearby - the only ones near this fixture's spawn point are friendly NPCs and non-hostile critters
# (see above).
TARGET_TARGETED_SPELL_CAST_DUMMY_GUID = 9990001
TARGET_TARGETED_SPELL_CAST_DUMMY_ENTRY = 2809
PLAN32_GEOMETRY_MODE = "plan32-geometry"
# One acceptance mode carries the remaining Plan 32 stages; each generation runs one stage from a
# pinned seed, so no travel between pinned points is needed. Stages arrive in this order; only the
# implemented ones are accepted by prepare/run/verify.
PLAN32_GEOMETRY_STAGES = ("blocked", "clear", "chase", "relog")
# The fixture is an entry with no static spawns anywhere near the seeds and a name no other nearby
# unit shares: Tab-nearest is not deterministic in the abbey (ambient hostiles wander through Tab
# range), so targeting goes through the client-local "/target <name>" macro instead (the level-up
# mode's proven mechanism), and verify still requires the selected GUID to equal the seeded one.
# Saytr (361, faction 14 hostile-to-all like the boar, level 1, no ScriptName) has zero rows
# anywhere in the world DB. Every Z below is the artifact terrain height at that point, read out
# of the staged root before any run.
PLAN32_GEOMETRY_DUMMY_ENTRY = 361
PLAN32_GEOMETRY_DUMMY_NAME = "saytr"
PLAN32_GEOMETRY_SEEDS = {
    "blocked": {
        "character": (-8830.95, -198.96, 82.801, 2.766),
        "dummy": (-8854.766, -189.576, 80.422),
    },
    "clear": {
        "character": (-8775.13, -220.95, 85.254, 2.766),
        "dummy": (-8798.946, -211.566, 82.845),
    },
    # Chase: the player pulls the fixture across the abbey's inner courtyard wall, then walks a
    # two-tap right turn (north 6 m then east 6 m along the cardinal facing) that the follower
    # cannot take straight. Its spawn stands 1.2 m west of that wall (Nsabbey.wmo group 6
    # triangle 649), its straight line to the player's end point breaks on the wall at both
    # heights, and the staged mmap walks it 28.6 m around (ratio 3.37) for the 8.5 m the straight
    # line covers. The pull ray from the player's start is clear at both heights with the fixture
    # dead ahead, so the aggro cast lands before the corner is turned.
    "chase": {
        "character": (-8876.0, -208.0, 81.313, math.pi / 2),
        "dummy": (-8876.0, -196.0, 80.221),
    },
}
# Relog walks the clear stage's route and seeds and then drops the session instead of casting: an
# engaged fixture would hold the character in combat, and the client refuses to log out in combat,
# so this stage seeds the same pair but never targets or casts at it.
PLAN32_GEOMETRY_SEEDS["relog"] = PLAN32_GEOMETRY_SEEDS["clear"]
# Chase observation contract, all pinned before the run. The follower path must be decisively
# longer than its straight line (a clipper walks ratio ~1.0), must end at melee range, must be
# continuous (no teleports), and must include a melee engagement; any of those missing fails the
# run instead of passing.
PLAN32_CHASE_P2 = (-8870, -202)
PLAN32_CHASE_START_TOLERANCE = 4.0
PLAN32_CHASE_END_TOLERANCE = 6.0
PLAN32_CHASE_DETOUR_RATIO = 1.25
PLAN32_CHASE_ARRIVAL_DISTANCE = 6.0
# The arrival distance is inclusive - the contract is "within 6 m", and a grazing pass sits exactly
# on that boundary - but six-figure world coordinates cannot promise an exact comparison. This
# margin is far below any ground measurement; it keeps a grazing pass an arrival instead of a
# rounding-error miss, and keeps the verdict the same either way. It replaces the tighter 3 m
# arrival window: the arrival is solved on the walked path and never dated before the player's own
# stop, so a move that ends at the corner the player turned cannot close the window early.
PLAN32_CHASE_ARRIVAL_EPSILON = 0.001
PLAN32_CHASE_MAX_SPLINE_GAP = 10.0
# A spline is only executed up to the next packet for its mover, and that next spline starts where
# the packet says the mover really is. The two must therefore agree to within this; a larger join
# is a discontinuity (a teleport, or movement the capture never carried) and fails the chase
# rather than being silently dropped as if the mover had been continuous.
PLAN32_CHASE_JOIN_TOLERANCE = 2.0
# Aggro casts per stage: the LOS stages cast twice (Codex requires both attempts uncancelled);
# the chase needs one aggro cast before the run leg; the relog stage casts nothing.
PLAN32_EXPECTED_COUNTS = {"blocked": [1, 2], "clear": [1, 2], "chase": [1], "relog": []}
# What the staged vmaps must say about each stage's own cast ray: the blocked stage's line has to
# be broken by the pinned obstacle, the clear and chase stages' has to be whole.
PLAN32_STAGE_RAY_EXPECTATION = {"blocked": "blocked", "clear": "clear", "chase": "clear"}
# Relog contract: the walk ran along the pinned clear line, so the stop it reached and the
# position the next login arrives at agree to this, and both are at least this far from the
# seeded login point — the server only holds that position from the row it saved at logout.
PLAN32_RELOG_TOLERANCE = 0.5
PLAN32_RELOG_MIN_TRAVEL = 1.0
# The server's own completion of the drop-out, exactly as the phase-restoration relogin reads it.
LOGOUT_COMPLETE_PATTERN = re.compile(r"S->C:.*\[SMSG_LOGOUT_COMPLETE\b")
# The chase's route through the staged mmap must exist, detour and still be short enough for the
# arrival window; the tool's own route cost is an upper bound (polygon centres), so 45 m is room.
PLAN32_CHASE_ROUTE_MAX = 45.0
# Runtime z must reproduce the seeded terrain height within this; the accepted geometry run settled
# to 1 mm, so 10 cm leaves room for packet float rounding without hiding a wrong surface.
PLAN32_GEOMETRY_HEIGHT_TOLERANCE = 0.1
# Every accepted movement sample of a stage has to stand on the staged terrain within this: the
# real stages departed by 0.00-1.47 m (1.47 m is the chase stepping up onto the courtyard wall),
# so 2 m admits the mapped surface while a teleport or a vertical spike fails.
PLAN32_STAGE_TERRAIN_TOLERANCE = 2.0
PLAN32_GEOMETRY_SPELL_ID = TARGET_TARGETED_SPELL_CAST_SPELL_ID
PLAN32_GEOMETRY_ACTION_BUTTON = TARGET_TARGETED_SPELL_CAST_ACTION_BUTTON
# Client-local "/target <name>" macro: resolves against the client's own object cache, needs no
# server permission, still sends a genuine CMSG_SET_SELECTION (level-up mode precedent).
PLAN32_GEOMETRY_TARGET_KEYS = (
    "Return", "slash", *tuple("target"), "space", *tuple(PLAN32_GEOMETRY_DUMMY_NAME), "Return",
)
INVALID_TARGET_SPELL_CAST_MODE = "invalid-target-spell-cast"
# Issue #85's remaining "invalid-target negative case" acceptance item, exercised via the action
# bar like the other cast modes above. Reuses the Fireball grant from target_targeted_spell_cast_
# seed_sql (enemy-only-targeted). Rather than a GM `.cast` command (the fixture account has no
# RBAC/GM grant, and the issue calls for the action bar specifically), targeting is done with the
# client-local "/target <name>" macro: it resolves against the client's own object cache and needs
# no server permission, so it works for an ordinary player. Self-targeting sends a real
# CMSG_SET_SELECTION, but generation 1 showed the client itself rejects the follow-up hotkey press
# ("Invalid target" flashes on screen, action button flares red) and never sends CMSG_CAST_SPELL at
# all: unlike range/cooldown/reagents, the 4.3.4 UI already knows the spell's implicit-target type
# and the current target's reaction from data it has locally, so friend/foe target-type mismatches
# are one of the checks it pre-validates instead of leaving to the server. That client-side refusal
# (no CMSG_CAST_SPELL, no SMSG_SPELL_GO, no SMSG_CAST_FAILED - the cast simply never happens) is
# itself the acceptance evidence for this negative case.
CHAT_MESSAGE_MODE = "chat-message"
# Plain lowercase letters only: automate_key_sequence maps each character straight to its own
# X11 keysym name (no digits/space/shift handling needed), and no chat command prefix, so the
# real client submits an ordinary CMSG_MESSAGECHAT_SAY - the packet that disconnected the real
# client outright before issue #89's opcode split fix (server saw it as an unknown opcode).
CHAT_MESSAGE_TEXT = "chatcheck"
# Plan 31 / issue #102: level 1 -> 2 through a normal XP source. The fixture character is already
# seeded at level 1 with a 400-XP threshold (player_xp_for_level row (1, 400)), so this mode only
# has to make a real kill: a hostile creature is seeded beside the player, selected with Tab and
# killed with the main-hand attack, which routes XP through KillRewarder -> Player::GiveXP.
# No GM level command and no character-row edit (see .agents/docs/cataclysm-conversion-goal.md).
LEVEL_UP_MODE = "level-up-progression"
LEVEL_UP_XP_TARGET_GUID = 9991001
# Entry 2809 "Boar": faction 14 (hostile to all), no ScriptName/special AI, so it neither dies on
# its own nor needs a lifespan limit. Same creature the targeted-spell-cast modes already seed.
# Overridable because the fixture character is level 1 and the target's level comes from
# creature_template, not the spawn row: if the chosen entry out-levels the character the kill will
# not land, and picking a lower entry is a run parameter rather than a code change.
LEVEL_UP_XP_TARGET_ENTRY = int(os.environ.get("LEVEL_UP_XP_TARGET_ENTRY", "2809"))
# The seeded target's name as the client knows it, used for "/target <name>" selection.
LEVEL_UP_TARGET_NAME = os.environ.get("LEVEL_UP_TARGET_NAME", "Boar")
LEVEL_UP_XP_TARGET_OFFSET = (3, 3)
# Spell 6603 is "Attack", the auto-attack melee classes start with - the same spell Plan 29's
# passing melee acceptance pinned (native_dbc.Spell.dbc:6603). Button 0 is the one the main-hand
# keypress reaches. (78 also sits on the warrior's starting bar but is not the auto-attack.)
LEVEL_UP_ATTACK_SPELL = 6603
LEVEL_UP_ATTACK_BUTTON = 0
# (item instance guid, item entry, bag, slot, durability) - the real Human Warrior starting kit.
# Equipment slots are bag 0: CHEST 4, LEGS 6, FEET 7, MAINHAND 15; the hearthstone goes in the
# backpack. Without the weapon an unarmed level-1 warrior cannot kill even a level-1 creature.
LEVEL_UP_GEAR = (
    (99000001, 58231, 0, 4, 100),   # Recruit's Vest  -> CHEST
    (99000002, 39, 0, 6, 100),      # Recruit's Pants -> LEGS
    (99000003, 40, 0, 7, 100),      # Recruit's Boots -> FEET
    (99000004, 49778, 0, 15, 100),  # Worn Greatsword -> MAINHAND (3-5 dmg)
    (99000005, 6948, 0, 23, 0),     # Hearthstone     -> backpack
)
LEVEL_UP_MAINHAND_ENTRY = 49778  # Worn Greatsword
VENDOR_TRADE_MODE = "vendor-trade"
# Plan 33 acceptance: Godric Rothgar (creature entry 1213, spawn guid 79952, vendor|repair) in
# Northshire. The fixture stands within interaction range, buys his cheapest row, sells a seeded
# bracer, buys it back and repairs a seeded damaged vest: one native round trip per handler.
VENDOR_TRADE_NPC_NAME = "Godric Rothgar"
VENDOR_TRADE_ENTRY = 1213
VENDOR_TRADE_CHARACTER_POSITION = (-8900.4, -121.6, 82.0)
VENDOR_TRADE_CHARACTER_ORIENTATION = 0.68     # faces the NPC, so the screenshots show his frame
VENDOR_TRADE_MONEY = 50000                    # 5 gold: a 36c shield, the repair and the 7c buyback
VENDOR_TRADE_BUY_ENTRY = 17184                # Small Shield, the vendor's MuID 8 row
VENDOR_TRADE_BUYBACK_SLOT_START = 74          # BUYBACK_SLOT_START: the first sale lands here
VENDOR_TRADE_BUY_MUID = 8
VENDOR_TRADE_SELL_ITEM_GUID = 99000301
VENDOR_TRADE_SELL_ENTRY = 2384                # Tarnished Chain Bracers, SellPrice 7
VENDOR_TRADE_SELL_SLOT = (0, 23)              # bag 0 (backpack), first backpack slot
VENDOR_TRADE_SELL_DURABILITY = 18
VENDOR_TRADE_DAMAGED_ITEM_GUID = 99000302
VENDOR_TRADE_DAMAGED_ENTRY = 2379             # Tarnished Chain Vest, MaxDurability 55
VENDOR_TRADE_DAMAGED_SLOT = (0, 4)            # equipped chest
VENDOR_TRADE_DAMAGED_DURABILITY = 5
VENDOR_TRADE_DAMAGED_MAX_DURABILITY = 55
VENDOR_TRADE_TIMEOUT = 20                     # seconds to wait for each client-driven marker
VENDOR_TRADE_LOGOUT_TIMEOUT = 45               # the normal logout timer (20 game seconds) plus the save
# The client has no default keybind for "interact with target"; SetBinding via /script is the
# ordinary unprotected config setter the walk/run mode already relies on, and the harness then
# presses the real key through the normal secure keybind path.
VENDOR_TRADE_BIND_KEYS = (
    "Return", "slash", *tuple("script"), "space",
    *tuple('SetBinding("G","INTERACTTARGET")SaveBindings(GetCurrentBindingSet())'), "Return",
)
def vendor_trade_name_keys(name: str) -> tuple[str, ...]:
    """A name as keystrokes: its words separated by the client's own "space" keysym name.

    A literal ' ' is not a keysym name, so passing the name as one character tuple types the words
    together ("GodricRothgar") and the target macro silently matches nothing. The phase and walk/run
    modes type their names word by word with "space" for the same reason.
    """
    keys: list[str] = []
    for index, word in enumerate(name.split()):
        if index:
            keys.append("space")
        keys.extend(word)
    return tuple(keys)


VENDOR_TRADE_TARGET_KEYS = (
    "Return", "slash", *tuple("targetexact"), "space", *vendor_trade_name_keys(VENDOR_TRADE_NPC_NAME),
    "Return",
)
VENDOR_TRADE_PROBE_KEYS = ("Return", *tuple("chatcheck"), "Return")
VENDOR_TRADE_PROBE_TIMEOUT = 20
VENDOR_TRADE_INTERACT_KEYS = ("g",)
VENDOR_TRADE_BACKPACK_KEYS = ("b",)
# Click positions as fractions of the owned window's current geometry, derived from the 4.3.4
# MerchantFrame.xml (the frame anchors at UIParent's TOPLEFT -104 y; its purchase grid is two columns
# of six cells, 165x52 design px each; the repair buttons sit at the frame's bottom-left) and
# calibrated on the 1800x1042 reference window.
# shortcut: default UI scale/layout and camera; the screenshots named in automate_vendor_trade are
# the calibration evidence, recalibrate these after changing either.
VENDOR_TRADE_ITEM_GRID_ORIGIN = (0.0405, 0.2255)  # item 1's item-button centre
VENDOR_TRADE_ITEM_COLUMN_WIDTH = 0.0804
VENDOR_TRADE_ITEM_ROW_HEIGHT = 0.0531
VENDOR_TRADE_GOSSIP_OPTION_ORIGIN = (0.775, 0.596)
VENDOR_TRADE_GOSSIP_OPTION_HEIGHT = 0.0218
VENDOR_TRADE_GOSSIP_OPTION_ROW = 1
VENDOR_TRADE_FIRST_BAG_SLOT = (0.9094, 0.7476)
VENDOR_TRADE_PURCHASE_TAB = (0.0361, 0.5806)
VENDOR_TRADE_BUYBACK_TAB = (0.0761, 0.5806)
# The buyback page re-anchors the same twelve buttons into the buyback frame's own two-column grid
# (wider cells) and hides the recent-sale control, so its first cell is not the purchase page's first
# cell: measured on the run's vendor-buyback-tab.png.
VENDOR_TRADE_BUYBACK_ITEM = (0.0306, 0.2111)
# The right-hand of the two anvil buttons is repair-all; the left one enters single-item repair mode.
VENDOR_TRADE_REPAIR_BUTTON = (0.0872, 0.5154)

VENDOR_NEGATIVE_MODE = "vendor-negative"
# One generation per rejection case; VENDOR_CASE freezes the case at prepare, like Plan 32's
# PLAN32_STAGE, so the seeded money/items and the case's expectations cannot drift apart.
VENDOR_NEGATIVE_CASES = ("no-money", "full-bags", "repeat-buy")
VENDOR_NEGATIVE_MONEY = {
    "no-money": 0,
    "full-bags": VENDOR_TRADE_MONEY,
    "repeat-buy": VENDOR_TRADE_MONEY,
}
VENDOR_NEGATIVE_FILL_ENTRY = VENDOR_TRADE_SELL_ENTRY     # Tarnished Chain Bracers, one per backpack slot
VENDOR_NEGATIVE_FILL_GUIDS = tuple(99000400 + index for index in range(16))

TRAINER_MODE = "trainer-learn"
# Plan 34: Llane Beshere (creature 911, spawn 79964, trainer table id 2, Class/Warrior) in Northshire.
# His only level-1 offer for the fixture is Battle Shout (6673): 10 copper listed, no prerequisites.
TRAINER_NPC_NAME = "Llane Beshere"
TRAINER_NPC_ENTRY = 911
TRAINER_ID = 2
TRAINER_SPELL_ID = 6673
TRAINER_SPELL_NAME = "Battle Shout"
TRAINER_MONEY = 50000
TRAINER_SPELL_BASE_COST = 10      # trainer_spell.MoneyCost for 6673 in the fixture data
TRAINER_SPELL_LISTED_PRICE = 9    # the discounted price the accepted run's list carried
TRAINER_CHARACTER_POSITION = (-8920.6, -210.1, 82.3)
TRAINER_CHARACTER_ORIENTATION = 0.85
TRAINER_TIMEOUT = 20
TRAINER_LOGOUT_TIMEOUT = 45
TRAINER_TARGET_KEYS = (
    "Return", "slash", *tuple("targetexact"), "space", *vendor_trade_name_keys(TRAINER_NPC_NAME), "Return",
)
# Gossip option that opens the trainer (Llane gives no quests, so "I require warrior training." is the
# first row) and the trainer list's spell rows, both as window fractions; the run's own
# trainer-gossip.png / trainer-open.png screenshots are the calibration evidence.
TRAINER_GOSSIP_OPTION_ORIGIN = (0.775, 0.596)
TRAINER_GOSSIP_OPTION_HEIGHT = 0.0218
TRAINER_GOSSIP_OPTION_ROW = 1
TRAINER_SPELL_ROW_ORIGIN = (0.0405, 0.2255)
TRAINER_SPELL_ROW_HEIGHT = 0.0531
TRAINER_SPELL_ROW = 1
# The 4.x trainer frame selects a spell row first; the purchase itself is the "Train" button at the
# frame's bottom bar (clicking the row alone only highlights it); measured from the run's own
# trainer-selected.png in the screenshot's pixels.
TRAINER_TRAIN_BUTTON = (0.170, 0.531)
# 4.x trainer-list state byte: Known = 0, Available = 1, Unavailable = 2 (the WotLK order was the
# other way round, which made the client hide a learnable spell).
TRAINER_STATE_AVAILABLE = 1
TRAINER_STATE_UNAVAILABLE = 2


POPULATED_CHARACTER_MODES = frozenset({
    POPULATED_MODE, CHARACTER_SELECTION_MODE, INITIAL_POST_LOAD_PACKETS_MODE, MAP_INSERTION_MODE,
    IN_WORLD_CONTROL_MODE, BASIC_MOVEMENT_MODE, RUN_SPEED_MODE, GROUND_MOVEMENT_MODE, JUMP_FALL_LAND_MODE,
    WALK_RUN_MODE_SWITCH_MODE, BASIC_SPELL_CAST_MODE, TARGET_TARGETED_SPELL_CAST_MODE, CHAT_MESSAGE_MODE,
    INVALID_TARGET_SPELL_CAST_MODE, LEVEL_UP_MODE, PLAN32_GEOMETRY_MODE, VENDOR_TRADE_MODE,
    VENDOR_NEGATIVE_MODE, TRAINER_MODE,
})
CHARACTER_MODES = frozenset({"character-screen", CHARACTER_CREATION_MODE, *MAGE_MODES,
                             *POPULATED_CHARACTER_MODES})
CHARACTER_GUID = 0x01020304
CHARACTER_NAME = "Cataplan"
CHARACTER_LIST_POSITION = 7
CHARACTER_POSITION = (-8949.95, -132.493, 83.5312)
CHARACTER_RACE = 1
CHARACTER_CLASS = 1
CHARACTER_MAP = 0
CHARACTER_ZONE = 12
CHARACTER_ORIENTATION = 0
# Opt-in Plan 24 acceptance starts beside the measured area 9/12 boundary, facing west.
ISSUE97 = os.environ.get("ISSUE97") == "1"
if ISSUE97:
    CHARACTER_POSITION = (-9054.0, -36.5, 88.8)
    CHARACTER_ORIENTATION = 3.14159
ISSUE97_PHASED_CREATURE_GUID, ISSUE97_CONTROL_CREATURE_GUID, ISSUE97_PHASED_GO_GUID = 9970001, 9970002, 9970001
ISSUE97_CREATURE_ENTRY, ISSUE97_CONTROL_CREATURE_ENTRY = 197, 823
ISSUE97_GO_ENTRY, ISSUE97_PHASE_ID = 2843, 125
# Plan 32 geometry stages seed from the table above, but only for that mode: a stray exported
# PLAN32_STAGE must not relocate another mode's character. prepare freezes the stage into the
# generation inputs and run refuses an environment that names a different one, so the seeded
# position, the fixture row and the stage label cannot drift apart.


def apply_vendor_trade_seed(mode: str) -> None:
    """Move the fixture next to Godric Rothgar (Plan 33 vendor mode only)."""
    global CHARACTER_POSITION, CHARACTER_ORIENTATION
    if mode != VENDOR_TRADE_MODE:
        return
    CHARACTER_POSITION = VENDOR_TRADE_CHARACTER_POSITION
    CHARACTER_ORIENTATION = VENDOR_TRADE_CHARACTER_ORIENTATION


def vendor_trade_gossip_option(row: int = VENDOR_TRADE_GOSSIP_OPTION_ROW) -> tuple[float, float]:
    """Where the gossip frame draws option `row` (1-based): the first row plus one row height."""
    fx, fy = VENDOR_TRADE_GOSSIP_OPTION_ORIGIN
    return fx, fy + (row - 1) * VENDOR_TRADE_GOSSIP_OPTION_HEIGHT


def vendor_trade_item_row(muid: int) -> tuple[float, float]:
    """Where the merchant frame draws `muid` (1-based, the numbering SMSG_VENDOR_INVENTORY sends).

    MerchantFrame.xml anchors MerchantItem1..12 as two columns of six filled left to right, top to
    bottom: item 2 is right of item 1, item 3 below it, and the eighth item sits in the right
    column's fourth row. A single-column model would buy the wrong item.
    """
    row, column = divmod(max(muid, 1) - 1, 2)
    fx, fy = VENDOR_TRADE_ITEM_GRID_ORIGIN
    return fx + column * VENDOR_TRADE_ITEM_COLUMN_WIDTH, fy + row * VENDOR_TRADE_ITEM_ROW_HEIGHT


def vendor_trade_seed_sql() -> str:
    """The fixture's money plus the two items the round trip needs: a damaged equipped vest for the
    repair and a bracer in the first backpack slot for the sell and buyback."""
    rows = ", ".join((
        f"({VENDOR_TRADE_SELL_ITEM_GUID},{VENDOR_TRADE_SELL_ENTRY},{CHARACTER_GUID},1,"
        f"{VENDOR_TRADE_SELL_DURABILITY},'0 0 0 0 0 ','{'0 ' * 36}')",
        f"({VENDOR_TRADE_DAMAGED_ITEM_GUID},{VENDOR_TRADE_DAMAGED_ENTRY},{CHARACTER_GUID},1,"
        f"{VENDOR_TRADE_DAMAGED_DURABILITY},'0 0 0 0 0 ','{'0 ' * 36}')",
    ))
    slots = ", ".join((
        f"({CHARACTER_GUID},{VENDOR_TRADE_SELL_SLOT[0]},{VENDOR_TRADE_SELL_SLOT[1]},{VENDOR_TRADE_SELL_ITEM_GUID})",
        f"({CHARACTER_GUID},{VENDOR_TRADE_DAMAGED_SLOT[0]},{VENDOR_TRADE_DAMAGED_SLOT[1]},"
        f"{VENDOR_TRADE_DAMAGED_ITEM_GUID})",
    ))
    return (
        populated_character_seed_sql()
        + f"UPDATE `characters` SET `money`={VENDOR_TRADE_MONEY} WHERE `guid`={CHARACTER_GUID};"
        f"DELETE FROM `character_inventory` WHERE `guid`={CHARACTER_GUID};"
        f"DELETE FROM `item_instance` WHERE `owner_guid`={CHARACTER_GUID};"
        "INSERT INTO `item_instance` "
        "(`guid`,`itemEntry`,`owner_guid`,`count`,`durability`,`charges`,`enchantments`) "
        f"VALUES {rows};"
        "INSERT INTO `character_inventory` (`guid`,`bag`,`slot`,`item`) "
        f"VALUES {slots};"
    )


def trainer_seed_sql() -> str:
    """The fixture's money, with any previously learned spell and action bar cleared for a retry."""
    return (
        populated_character_seed_sql()
        + f"UPDATE `characters` SET `money`={TRAINER_MONEY} WHERE `guid`={CHARACTER_GUID};"
        f"DELETE FROM `character_inventory` WHERE `guid`={CHARACTER_GUID};"
        f"DELETE FROM `item_instance` WHERE `owner_guid`={CHARACTER_GUID};"
        f"DELETE FROM `character_spell` WHERE `guid`={CHARACTER_GUID};"
        f"DELETE FROM `character_action` WHERE `guid`={CHARACTER_GUID};"
    )


def trainer_db_evidence(manifest: Manifest, generation: Generation) -> dict[str, int]:
    """The saved spellbook/action state and money after the run's logout."""
    schema = generation["schemas"]["characters"]

    def scalar(sql: str) -> int:
        output = mysql(manifest, generation, sql, schema)
        try:
            return int(output)
        except ValueError as error:
            raise RuntimeError(f"owned character database returned a non-numeric value: {output!r}") from error

    return {
        "known_spell_count": scalar(
            f"SELECT COUNT(*) FROM `character_spell` WHERE `guid`={CHARACTER_GUID} "
            f"AND `spell`={TRAINER_SPELL_ID};"),
        "action_count": scalar(
            f"SELECT COUNT(*) FROM `character_action` WHERE `guid`={CHARACTER_GUID} "
            f"AND `action`={TRAINER_SPELL_ID} AND `type`=0;"),
        "money": scalar(f"SELECT `money` FROM `characters` WHERE `guid`={CHARACTER_GUID};"),
    }


def trainer_spell_row() -> tuple[float, float]:
    """Where the trainer frame draws spell row `TRAINER_SPELL_ROW` (1-based)."""
    fx, fy = TRAINER_SPELL_ROW_ORIGIN
    return fx, fy + (TRAINER_SPELL_ROW - 1) * TRAINER_SPELL_ROW_HEIGHT


def trainer_gossip_option() -> tuple[float, float]:
    fx, fy = TRAINER_GOSSIP_OPTION_ORIGIN
    return fx, fy + (TRAINER_GOSSIP_OPTION_ROW - 1) * TRAINER_GOSSIP_OPTION_HEIGHT


def apply_trainer_seed(mode: str) -> None:
    """Move the fixture next to Llane Beshere (trainer mode only)."""
    global CHARACTER_POSITION, CHARACTER_ORIENTATION
    if mode != TRAINER_MODE:
        return
    CHARACTER_POSITION = TRAINER_CHARACTER_POSITION
    CHARACTER_ORIENTATION = TRAINER_CHARACTER_ORIENTATION


def vendor_negative_seed_sql(case: str) -> str:
    """The fixture's starting money, plus a full backpack for the `full-bags` case."""
    items: list[str] = []
    slots: list[str] = []
    if case == "full-bags":
        for index, guid in enumerate(VENDOR_NEGATIVE_FILL_GUIDS):
            items.append(
                f"({guid},{VENDOR_NEGATIVE_FILL_ENTRY},{CHARACTER_GUID},1,{VENDOR_TRADE_SELL_DURABILITY},"
                f"'0 0 0 0 0 ','{'0 ' * 36}')")
            slots.append(f"({CHARACTER_GUID},0,{VENDOR_TRADE_SELL_SLOT[1] + index},{guid})")
    sql = (
        populated_character_seed_sql()
        + f"UPDATE `characters` SET `money`={VENDOR_NEGATIVE_MONEY[case]} WHERE `guid`={CHARACTER_GUID};"
        f"DELETE FROM `character_inventory` WHERE `guid`={CHARACTER_GUID};"
        f"DELETE FROM `item_instance` WHERE `owner_guid`={CHARACTER_GUID};"
    )
    if items:
        sql += (
            "INSERT INTO `item_instance` "
            "(`guid`,`itemEntry`,`owner_guid`,`count`,`durability`,`charges`,`enchantments`) "
            f"VALUES {', '.join(items)};"
            "INSERT INTO `character_inventory` (`guid`,`bag`,`slot`,`item`) "
            f"VALUES {', '.join(slots)};"
        )
    return sql


def vendor_negative_db_evidence(manifest: Manifest, generation: Generation) -> dict[str, object]:
    """The saved inventory and money after the case.

    Owned item instances are read without an inner join so an owned-but-unlinked row (an orphan) cannot
    hide, and every link carries its owner guid so a link pointing at somebody else's item is visible.
    The inventory links are read by the character's own guid, not by the item's owner.
    """
    schema = generation["schemas"]["characters"]
    owned_text = mysql(
        manifest, generation,
        f"SELECT `guid`, `itemEntry`, `count` FROM `item_instance` WHERE `owner_guid`={CHARACTER_GUID} "
        "ORDER BY `guid`;",
        schema,
    )
    links_text = mysql(
        manifest, generation,
        "SELECT ic.`bag`, ic.`slot`, ii.`guid`, ii.`itemEntry`, ii.`count`, ii.`owner_guid` "
        "FROM `character_inventory` ic LEFT JOIN `item_instance` ii ON ii.`guid` = ic.`item` "
        f"WHERE ic.`guid`={CHARACTER_GUID} ORDER BY ic.`bag`, ic.`slot`;",
        schema,
    )

    def rows(text: str, fields: tuple[str, ...]) -> list[dict[str, int | None]]:
        parsed: list[dict[str, int | None]] = []
        for line in text.splitlines():
            if not line.strip():
                continue
            values = line.split("\t")
            parsed.append({
                name: (int(value) if value != "NULL" else None) for name, value in zip(fields, values)
            })
        return parsed

    output = mysql(manifest, generation, f"SELECT `money` FROM `characters` WHERE `guid`={CHARACTER_GUID};", schema)
    try:
        money = int(output)
    except ValueError as error:
        raise RuntimeError(f"owned character database returned a non-numeric value: {output!r}") from error
    return {
        "owned_items": rows(owned_text, ("guid", "entry", "count")),
        "links": rows(links_text, ("bag", "slot", "item_guid", "entry", "count", "owner_guid")),
        "money": money,
    }


def vendor_trade_db_evidence(manifest: Manifest, generation: Generation) -> dict[str, int]:
    """Character-row state after the run's logout: what the round trip left behind.

    The bought shield must be owned, the sold bracer must be back in a backpack slot (not merely
    sitting in a buyback slot), the damaged vest must be repaired to its template maximum and the
    money must have gone down without being wiped.
    """
    schema = generation["schemas"]["characters"]

    def scalar(sql: str) -> int:
        output = mysql(manifest, generation, sql, schema)
        try:
            return int(output)
        except ValueError as error:
            raise RuntimeError(f"owned character database returned a non-numeric value: {output!r}") from error

    return {
        "bought_item_count": scalar(
            f"SELECT COUNT(*) FROM `item_instance` WHERE `owner_guid`={CHARACTER_GUID} "
            f"AND `itemEntry`={VENDOR_TRADE_BUY_ENTRY};"),
        "buyback_item_count": scalar(
            "SELECT COUNT(*) FROM `character_inventory` ic JOIN `item_instance` ii ON ii.`guid`=ic.`item` "
            f"WHERE ii.`owner_guid`={CHARACTER_GUID} AND ii.`itemEntry`={VENDOR_TRADE_SELL_ENTRY} "
            "AND ic.`slot` BETWEEN 23 AND 38;"),
        "repaired_durability": scalar(
            f"SELECT `durability` FROM `item_instance` WHERE `guid`={VENDOR_TRADE_DAMAGED_ITEM_GUID};"),
        "money": scalar(f"SELECT `money` FROM `characters` WHERE `guid`={CHARACTER_GUID};"),
    }


def vendor_negative_case() -> str:
    """The rejection case the environment names; empty when none was."""
    return os.environ.get("VENDOR_CASE", "")


def vendor_negative_case_or_fail() -> str:
    """The environment's case, refused unless it names an implemented one."""
    case = vendor_negative_case()
    if case not in VENDOR_NEGATIVE_CASES:
        raise RuntimeError(f"vendor-negative needs VENDOR_CASE in {VENDOR_NEGATIVE_CASES}; got {case!r}")
    return case


def vendor_negative_frozen_case(generation: Generation) -> str:
    """The case prepare froze into this generation, refused when the environment disagrees."""
    frozen = generation["inputs"].get("vendor_case")
    if frozen not in VENDOR_NEGATIVE_CASES:
        raise RuntimeError("the prepared generation has no frozen vendor case; prepare it again")
    requested = vendor_negative_case()
    if requested and requested != frozen:
        raise RuntimeError(
            f"generation is prepared for vendor case {frozen!r}; VENDOR_CASE names {requested!r}")
    return str(frozen)


def apply_vendor_negative_seed(mode: str) -> None:
    """Move the fixture next to Godric (vendor-negative mode only)."""
    global CHARACTER_POSITION, CHARACTER_ORIENTATION
    if mode != VENDOR_NEGATIVE_MODE:
        return
    CHARACTER_POSITION = VENDOR_TRADE_CHARACTER_POSITION
    CHARACTER_ORIENTATION = VENDOR_TRADE_CHARACTER_ORIENTATION


def apply_plan32_stage_seed(mode: str, stage: str) -> None:
    """Move the fixture character onto the stage's pinned point (Plan 32 geometry mode only)."""
    global CHARACTER_POSITION, CHARACTER_ORIENTATION
    if mode != PLAN32_GEOMETRY_MODE or stage not in PLAN32_GEOMETRY_SEEDS:
        return
    CHARACTER_POSITION = PLAN32_GEOMETRY_SEEDS[stage]["character"][:3]
    CHARACTER_ORIENTATION = PLAN32_GEOMETRY_SEEDS[stage]["character"][3]


def plan32_stage() -> str:
    """The Plan 32 stage the environment names; empty when none was (or when verify reads a
    generation whose record already carries it)."""
    return os.environ.get("PLAN32_STAGE", "")


def plan32_stage_or_fail() -> str:
    """The environment's Plan 32 stage, refused unless it names an implemented one."""
    stage = plan32_stage()
    if stage not in PLAN32_GEOMETRY_SEEDS:
        raise RuntimeError(
            f"plan32-geometry needs PLAN32_STAGE in {sorted(PLAN32_GEOMETRY_SEEDS)}; got {stage!r}"
        )
    return stage


def plan32_frozen_stage(generation: Generation) -> str:
    """The stage prepare froze into this generation, refused when the environment disagrees.

    Binding run to the prepared stage is what keeps the seeded character position, the fixture row
    and the stage label describing one geometry: an environment naming another stage would move the
    fixture under a player still standing at the other stage's pinned point.
    """
    frozen = generation["inputs"].get("plan32_stage")
    if frozen not in PLAN32_GEOMETRY_SEEDS:
        raise RuntimeError("the prepared generation has no frozen Plan 32 stage; prepare it again")
    requested = plan32_stage()
    if requested and requested != frozen:
        raise RuntimeError(
            f"generation is prepared for Plan 32 stage {frozen!r}; PLAN32_STAGE names {requested!r}"
        )
    return str(frozen)


def plan_number(mode: str) -> str:
    if mode == LEVEL_UP_MODE:
        return "31"
    if mode == CHAT_MESSAGE_MODE:
        return "89"
    if mode in {BASIC_SPELL_CAST_MODE, TARGET_TARGETED_SPELL_CAST_MODE, INVALID_TARGET_SPELL_CAST_MODE}:
        return "25"
    if mode == CHARACTER_CREATION_MODE:
        return "22"
    if mode in MAGE_MODES:
        return "35"
    if mode in {GROUND_MOVEMENT_MODE, JUMP_FALL_LAND_MODE, WALK_RUN_MODE_SWITCH_MODE}:
        return "23"
    if mode == PLAN32_GEOMETRY_MODE:
        return "32"
    if mode == VENDOR_TRADE_MODE:
        return "33"
    if mode == VENDOR_NEGATIVE_MODE:
        return "33"
    if mode == TRAINER_MODE:
        return "34"
    if mode == RUN_SPEED_MODE:
        return "16"
    if mode == BASIC_MOVEMENT_MODE:
        return "15"
    if mode == IN_WORLD_CONTROL_MODE:
        return "13"
    if mode == MAP_INSERTION_MODE:
        return "12"
    if mode == INITIAL_POST_LOAD_PACKETS_MODE:
        return "11"
    if mode == CHARACTER_SELECTION_MODE:
        return "10"
    if mode == POPULATED_MODE:
        return "9"
    return "8" if mode == "character-screen" else "7"


def run_command(
    args: list[str], *, input_bytes: bytes | None = None, check: bool = True,
    timeout: int = 120, env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[bytes]:
    result = subprocess.run(
        args, input=input_bytes, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        timeout=timeout, env=env,
    )
    if check and result.returncode:
        stderr = result.stderr.decode(errors="replace").strip()
        stdout = result.stdout.decode(errors="replace").strip()
        raise RuntimeError(f"{args[0]} failed with exit {result.returncode}: {stderr or stdout}")
    return result


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def database_cache_key(image_id: str, directories: tuple[tuple[str, Path], ...]) -> str:
    digest = hashlib.sha256(f"plan7-database-cache-v{DATABASE_CACHE_VERSION}\0{image_id}\0".encode())
    for label, directory in directories:
        paths = sorted(directory.glob("*.sql"))
        if not paths:
            raise RuntimeError(f"SQL directory contains no files: {directory}")
        for path in paths:
            digest.update(f"{label}/{path.name}\0".encode())
            digest.update(bytes.fromhex(sha256(path)))
    return digest.hexdigest()


def srp_registration(username: str, password: str, salt: bytes) -> bytes:
    identity = hashlib.sha1(f"{username}:{password}".encode()).digest()
    exponent = int.from_bytes(hashlib.sha1(salt + identity).digest(), "little")
    return pow(SRP_G, exponent, SRP_N).to_bytes(32, "little")


def tree_metadata(path: Path) -> str | None:
    if not path.exists():
        return None
    digest = hashlib.sha256()
    paths = [path]
    if path.is_dir():
        paths.extend(sorted(path.rglob("*")))
    for entry in paths:
        stat = entry.lstat()
        relative = "." if entry == path else entry.relative_to(path).as_posix()
        digest.update(
            f"{relative}\0{stat.st_mode}\0{stat.st_uid}\0{stat.st_gid}\0{stat.st_size}\0"
            f"{stat.st_ino}\0{stat.st_mtime_ns}\0{stat.st_ctime_ns}\n".encode()
        )
    return digest.hexdigest()


def tree_content_hash(path: Path) -> str:
    if not path.is_dir():
        raise RuntimeError(f"data directory is absent: {path}")
    digest = hashlib.sha256()
    files = sorted(item for item in path.rglob("*") if item.is_file())
    if not files:
        raise RuntimeError(f"data directory contains no files: {path}")
    for item in files:
        relative = item.relative_to(path).as_posix()
        digest.update(f"{relative}\0{item.stat().st_size}\0".encode())
        digest.update(bytes.fromhex(sha256(item)))
    return digest.hexdigest()


def docker_inventory() -> dict[str, list[str]]:
    containers = run_command(
        ["docker", "ps", "-a", "--no-trunc", "--format", "{{.ID}} {{.Names}}"],
    ).stdout.decode().splitlines()
    volumes = run_command(["docker", "volume", "ls", "--format", "{{.Name}}"]).stdout.decode().splitlines()
    return {"containers": sorted(containers), "volumes": sorted(volumes)}


def unused_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def require_unused(port: int) -> None:
    with socket.socket() as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind(("127.0.0.1", port))
        except OSError as error:
            raise RuntimeError(f"loopback port {port} is already in use") from error


def nearest_existing_parent(path: Path) -> Path:
    current = path.resolve()
    while not current.exists():
        if current.parent == current:
            raise RuntimeError(f"no existing parent for {path}")
        current = current.parent
    return current


def require_external_root(path: Path) -> Path:
    resolved = path.resolve()
    if resolved == REPO_ROOT or REPO_ROOT in resolved.parents:
        raise RuntimeError("the Plan 7 run root must be outside the Git worktree")
    if resolved == Path("/") or resolved == Path.home().resolve():
        raise RuntimeError(f"refusing broad run root: {resolved}")
    return resolved


def load_manifest(path: Path) -> Manifest:
    if not path.is_file():
        raise RuntimeError(f"manifest does not exist: {path}")
    value = json.loads(path.read_text())
    if value.get("version") != 1 or not isinstance(value.get("generations"), list):
        raise RuntimeError("unsupported or malformed Plan 7 manifest")
    if Path(value["root"]).resolve() != path.parent.resolve():
        raise RuntimeError("manifest root does not match its parent directory")
    return value


def save_manifest(path: Path, manifest: Manifest) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(descriptor, "w") as handle:
            json.dump(manifest, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        os.chmod(path, 0o600)
    finally:
        temporary.unlink(missing_ok=True)


def active_generation(manifest: Manifest) -> Generation:
    generations = manifest.get("generations", [])
    if not generations:
        raise RuntimeError("manifest has no generation")
    return generations[-1]


def set_state(generation: Generation, new_state: State) -> None:
    old_state = generation["state"]
    if new_state not in TRANSITIONS[old_state]:
        raise RuntimeError(f"invalid Plan 7 state transition: {old_state} -> {new_state}")
    generation["state"] = new_state


def record_failure(manifest_path: Path, manifest: Manifest, error: BaseException) -> None:
    generation = active_generation(manifest)
    generation["failure"] = {"phase": generation["state"], "message": str(error)}
    if generation["state"] not in ("failed", "inconclusive", "reset"):
        set_state(generation, "failed")
    save_manifest(manifest_path, manifest)


def copy_tree(source: Path, destination: Path) -> None:
    if destination.exists():
        raise RuntimeError(f"copy destination already exists: {destination}")
    shutil.copytree(source, destination, symlinks=False, copy_function=shutil.copy2)


def make_tree_read_only(path: Path) -> None:
    for entry in (path, *path.rglob("*")):
        if not entry.is_symlink():
            entry.chmod(entry.stat().st_mode & ~0o222)


def make_tree_writable(path: Path) -> None:
    for entry in (path, *path.rglob("*")):
        if not entry.is_symlink():
            entry.chmod(entry.stat().st_mode | 0o200)


def link_client_base(base: Path, destination: Path) -> None:
    if destination.exists():
        raise RuntimeError(f"client link destination already exists: {destination}")
    destination.mkdir()
    for entry in base.iterdir():
        if entry.name == "Data":
            link_client_data(entry, destination / entry.name)
            continue
        if entry.name not in WRITABLE_CLIENT_DIRS:
            (destination / entry.name).symlink_to(entry, target_is_directory=entry.is_dir())
    for name in WRITABLE_CLIENT_DIRS:
        (destination / name).mkdir()


# Data/Cache is writable: the client rewrites SoundCache-*.MPQ there at runtime. It has
# to be a real local directory, never a symlink to the protected source tree -- otherwise
# the client writes its sound cache straight back into the user's real installation, which
# both violates the isolation contract and trips the protected-tree guard on the next
# prepare. WRITABLE_CLIENT_DIRS covers the top-level Cache dir; this covers Data/Cache.
WRITABLE_CLIENT_DATA_DIRS = ("Cache",)


def link_client_data(source: Path, destination: Path) -> None:
    destination.mkdir()
    for entry in source.iterdir():
        if entry.name == "enUS" or entry.name in WRITABLE_CLIENT_DATA_DIRS:
            continue
        (destination / entry.name).symlink_to(entry, target_is_directory=entry.is_dir())

    for name in WRITABLE_CLIENT_DATA_DIRS:
        (destination / name).mkdir(exist_ok=True)

    locale_source = source / "enUS"
    locale_destination = destination / "enUS"
    locale_destination.mkdir()
    for entry in locale_source.iterdir():
        if entry.name != "realmlist.wtf":
            (locale_destination / entry.name).symlink_to(entry, target_is_directory=entry.is_dir())


def write_client_config(client: Path) -> None:
    (client / "Logs").mkdir(exist_ok=True)
    (client / "WTF").mkdir(exist_ok=True)
    (client / "WTF/Config.wtf").write_text(
        'SET locale "enUS"\nSET realmlist "127.0.0.1"\nSET patchlist "127.0.0.1"\n'
        'SET readTOS "1"\nSET readEULA "1"\nSET movie "0"\nSET playIntroMovie "0"\nSET accounttype "CT"\n'
        'SET gxWindow "1"\nSET gxMaximize "0"\n'
    )


def install_dxvk(generation: Generation) -> None:
    inputs = generation["inputs"]
    source = Path(str(inputs.get("dxvk_d3d9") or (
        Path(str(inputs["wine_runner"])) / "lib/wine/dxvk/x86_64-windows/d3d9.dll"
    )))
    if not source.is_file():
        raise RuntimeError(f"installed Wine runner has no bundled DXVK d3d9.dll: {source}")
    destination = Path(generation["paths"]["client"]) / "d3d9.dll"
    if destination.is_symlink():
        destination.unlink()
    destination.unlink(missing_ok=True)
    shutil.copy2(source, destination)


def ensure_client_base(manifest: Manifest, generation: Generation) -> None:
    client = Path(generation["paths"]["client"])
    source = Path(str(generation["inputs"]["client_root"]))
    executable = client / "Wow-64.exe"
    if not executable.is_symlink() or executable.resolve() != source / "Wow-64.exe":
        raise RuntimeError("run client is not the expected read-only source-client symlink overlay")


def require_owned_path(path: Path, generation: Generation) -> Path:
    resolved = path.resolve()
    root = Path(generation["root"]).resolve()
    if resolved == root or root not in resolved.parents:
        raise RuntimeError(f"refusing path outside owned generation: {resolved}")
    return resolved


def remove_owned_path(path: Path, generation: Generation) -> None:
    resolved = require_owned_path(path, generation)
    if resolved.is_dir() and not resolved.is_symlink():
        shutil.rmtree(resolved)
    else:
        resolved.unlink(missing_ok=True)


def container_details(name: str) -> dict:
    result = run_command(["docker", "inspect", name], check=False)
    if result.returncode:
        raise RuntimeError(f"owned container is absent: {name}")
    return json.loads(result.stdout)[0]


def assert_container_owned(manifest: Manifest, generation: Generation, schema: str | None = None) -> dict:
    docker = generation["docker"]
    details = container_details(str(docker["container"]))
    labels = details["Config"].get("Labels") or {}
    expected = {
        "org.azerothcore.plan": plan_number(str(generation["mode"])),
        "org.azerothcore.run_id": manifest["run_id"],
        "org.azerothcore.generation": str(generation["number"]),
    }
    if details["Id"] != docker["container_id"]:
        raise RuntimeError("owned MySQL container ID changed")
    if any(labels.get(key) != value for key, value in expected.items()):
        raise RuntimeError("owned MySQL container labels changed")
    binding = details["NetworkSettings"]["Ports"].get("3306/tcp")
    mysql_port = generation["ports"]["mysql"]
    if not binding or binding[0]["HostIp"] != "127.0.0.1" or int(binding[0]["HostPort"]) != mysql_port:
        raise RuntimeError("owned MySQL loopback binding changed")
    volumes = {mount["Name"] for mount in details["Mounts"] if mount["Type"] == "volume"}
    if volumes != {docker["volume"]}:
        raise RuntimeError("owned MySQL volume attachment changed")
    image_id = generation.get("inputs", {}).get("mysql_image_id")
    if image_id and details["Image"] != image_id:
        raise RuntimeError("owned MySQL container image changed")
    if schema is not None and schema not in generation["schemas"].values():
        raise RuntimeError(f"refusing unowned schema: {schema}")
    return details


def mysql(
    manifest: Manifest, generation: Generation, sql: str | bytes, schema: str | None = None,
    timeout: int = 600,
) -> str:
    assert_container_owned(manifest, generation, schema)
    command = [
        "docker", "exec", "-i", str(generation["docker"]["container"]), "mysql", "--batch",
        "--skip-column-names", "-uroot", f"-p{manifest['mysql_root_password']}",
    ]
    if schema:
        command.extend(["-D", schema])
    payload = sql.encode() if isinstance(sql, str) else sql
    return run_command(command, input_bytes=payload, timeout=timeout).stdout.decode().rstrip("\n")


def verify_run_speed_aura_source(dbc_root: Path) -> None:
    # These are the current server's flattened Spell.dbc columns, not the client's split Cata tables.
    data = (dbc_root / "Spell.dbc").read_bytes()
    magic, count, fields, size, _ = struct.unpack_from("<4s4I", data)
    if (magic, fields, size) != (b"WDBC", 234, 936):
        raise RuntimeError("run-speed fixture requires the current 234-field server Spell.dbc")
    for offset in range(20, 20 + count * size, size):
        row = struct.unpack_from("<234I", data, offset)
        if row[0] == RUN_SPEED_AURA:
            if (row[71], row[95], row[80] + 1) != (6, 31, RUN_SPEED_AURA_AMOUNT):
                raise RuntimeError("Sprint effect 0 no longer matches the saved run-speed aura fixture")
            return
    raise RuntimeError("Sprint is absent from the server Spell.dbc")


def run_speed_aura_seed_sql() -> str:
    # The extended saved duration lets normal aura expiry trigger the handshake after world entry.
    return (
        f"DELETE FROM `character_aura` WHERE `guid`={CHARACTER_GUID} AND `spell`={RUN_SPEED_AURA};"
        "INSERT INTO `character_aura` "
        "(`guid`,`casterGuid`,`spell`,`effectMask`,`recalculateMask`,`stackCount`,"
        "`amount0`,`base_amount0`,`maxDuration`,`remainTime`) "
        f"VALUES ({CHARACTER_GUID},{CHARACTER_GUID},{RUN_SPEED_AURA},1,0,1,"
        f"{RUN_SPEED_AURA_AMOUNT},{RUN_SPEED_AURA_AMOUNT - 1},"
        f"{RUN_SPEED_AURA_DURATION_MS},{RUN_SPEED_AURA_DURATION_MS});"
    )


def spell_grant_seed_sql(spell_id: int, action_button: int) -> str:
    # The granted spell is a Mage spell, but the shared fixture (populated_character_seed_sql) is
    # a Human Warrior. Player::_LoadSpells (via CheckSkillLearnedBySpell) deletes any learned
    # spell whose skill line doesn't match the character's race/class at every login (confirmed
    # empirically: generation 5's WorldServer.log logged "has spell (12051) that teach skill
    # (237) which is invalid for the race/class combination (Race: 1, Class: 1). Will be
    # deleted." and character_spell/character_action were empty afterwards). Switching this
    # fixture to Mage makes the granted spell's skill line legitimate so it survives login.
    return (
        f"UPDATE `characters` SET `class`={BASIC_SPELL_CAST_CLASS} WHERE `guid`={CHARACTER_GUID};"
        f"DELETE FROM `character_spell` WHERE `guid`={CHARACTER_GUID} AND `spell`={spell_id};"
        "INSERT INTO `character_spell` (`guid`,`spell`,`specMask`) "
        f"VALUES ({CHARACTER_GUID},{spell_id},1);"
        f"DELETE FROM `character_action` WHERE `guid`={CHARACTER_GUID} AND `spec`=0 "
        f"AND `button`={action_button};"
        "INSERT INTO `character_action` (`guid`,`spec`,`button`,`action`,`type`) "
        f"VALUES ({CHARACTER_GUID},0,{action_button},{spell_id},0);"
    )


def basic_spell_cast_seed_sql() -> str:
    return spell_grant_seed_sql(BASIC_SPELL_CAST_SPELL_ID, BASIC_SPELL_CAST_ACTION_BUTTON)


def target_targeted_spell_cast_seed_sql() -> str:
    return spell_grant_seed_sql(TARGET_TARGETED_SPELL_CAST_SPELL_ID, TARGET_TARGETED_SPELL_CAST_ACTION_BUTTON)


def plan32_spell_seed_sql() -> str:
    return spell_grant_seed_sql(PLAN32_GEOMETRY_SPELL_ID, PLAN32_GEOMETRY_ACTION_BUTTON)


def plan32_world_seed_sql(stage: str) -> str:
    dummy_x, dummy_y, dummy_z = PLAN32_GEOMETRY_SEEDS[stage]["dummy"]
    return (
        "INSERT INTO `creature` "
        "(`guid`,`id`,`map`,`zoneId`,`areaId`,`position_x`,`position_y`,`position_z`,"
        "`wander_distance`,`MovementType`) "
        f"VALUES ({TARGET_TARGETED_SPELL_CAST_DUMMY_GUID},{PLAN32_GEOMETRY_DUMMY_ENTRY},"
        f"{CHARACTER_MAP},{CHARACTER_ZONE},{CHARACTER_ZONE},{dummy_x},{dummy_y},{dummy_z},0,0) "
        "ON DUPLICATE KEY UPDATE `position_x`=VALUES(`position_x`),`position_y`=VALUES(`position_y`),"
        "`position_z`=VALUES(`position_z`);"
    )


def verify_plan32_seeds(manifest: Manifest, generation: Generation) -> None:
    """The selected unit is the seeded fixture only if no other row of the fixture entry sits
    near either seed point. Runs in run() after seeding, so a stray same-entry row fails the run
    instead of silently admitting an ambient target. The run-owned DB is disposable; the check
    reads it, it never modifies shared data."""
    points = [
        PLAN32_GEOMETRY_SEEDS[stage]["character"][:2] for stage in PLAN32_GEOMETRY_SEEDS
    ] + [
        PLAN32_GEOMETRY_SEEDS[stage]["dummy"][:2] for stage in PLAN32_GEOMETRY_SEEDS
    ]
    conditions = " OR ".join(
        f"((`position_x`-({x}))*(`position_x`-({x}))+(`position_y`-({y}))*(`position_y`-({y}))) < 22500"
        for x, y in points
    )
    rows = mysql(
        manifest, generation,
        f"SELECT `guid`,`position_x`,`position_y` FROM `creature` "
        f"WHERE `id`={PLAN32_GEOMETRY_DUMMY_ENTRY} AND `map`={CHARACTER_MAP} AND ({conditions});",
        generation["schemas"]["world"],
    )
    found = [line.split("\t") for line in rows.splitlines() if line.strip()]
    expected = str(TARGET_TARGETED_SPELL_CAST_DUMMY_GUID)
    if len(found) != 1 or found[0][0] != expected:
        raise RuntimeError(
            f"plan32 fixture entry {PLAN32_GEOMETRY_DUMMY_ENTRY} is not unique near the seeds: {found}"
        )


def target_targeted_spell_cast_world_seed_sql() -> str:
    x, y, z = CHARACTER_POSITION
    dummy_x, dummy_y = x + 3, y + 3
    return (
        "INSERT INTO `creature` "
        "(`guid`,`id`,`map`,`zoneId`,`areaId`,`position_x`,`position_y`,`position_z`,"
        "`wander_distance`,`MovementType`) "
        f"VALUES ({TARGET_TARGETED_SPELL_CAST_DUMMY_GUID},{TARGET_TARGETED_SPELL_CAST_DUMMY_ENTRY},"
        f"{CHARACTER_MAP},{CHARACTER_ZONE},{CHARACTER_ZONE},{dummy_x},{dummy_y},{z},0,0) "
        "ON DUPLICATE KEY UPDATE `position_x`=VALUES(`position_x`),`position_y`=VALUES(`position_y`),"
        "`position_z`=VALUES(`position_z`);"
    )


def issue97_world_seed_sql() -> str:
    # Keep the scratch fixtures within interaction range of the measured inside stop.
    creature = (
        "INSERT INTO `creature` (`guid`,`id`,`map`,`zoneId`,`areaId`,`position_x`,`position_y`,`position_z`,"
        "`wander_distance`,`MovementType`,`PhaseId`) VALUES "
        f"({ISSUE97_PHASED_CREATURE_GUID},{ISSUE97_CREATURE_ENTRY},0,12,12,-9072,-39,88.7,0,0,{ISSUE97_PHASE_ID}),"
        f"({ISSUE97_CONTROL_CREATURE_GUID},{ISSUE97_CONTROL_CREATURE_ENTRY},0,12,12,-9072,-42,88.7,0,0,0);"
    )
    gameobject = (
        "INSERT INTO `gameobject` (`guid`,`id`,`map`,`zoneId`,`areaId`,`position_x`,`position_y`,`position_z`,"
        "`orientation`,`rotation0`,`rotation1`,`rotation2`,`rotation3`,`spawntimesecs`,`animprogress`,`state`,"
        f"`PhaseId`) VALUES ({ISSUE97_PHASED_GO_GUID},{ISSUE97_GO_ENTRY},0,12,12,-9072,-34,89.7,0,0,0,0,1,300,255,1,"
        f"{ISSUE97_PHASE_ID});"
    )
    return (
        f"DELETE FROM `creature` WHERE `id` IN ({ISSUE97_CREATURE_ENTRY},{ISSUE97_CONTROL_CREATURE_ENTRY},11260) "
        f"AND `guid` IN ({ISSUE97_PHASED_CREATURE_GUID},{ISSUE97_CONTROL_CREATURE_GUID});"
        f"DELETE FROM `gameobject` WHERE `id` IN ({ISSUE97_GO_ENTRY},21008) "
        f"AND `guid`={ISSUE97_PHASED_GO_GUID};" + creature + gameobject
    )


def level_up_action_seed_sql() -> str:
    """Gives the fixture character the auto-attack binding so the main-hand keypress attacks.

    populated_character_seed_sql inserts the `characters` row directly, so it grants no spells and
    no action bar - fine for the movement/cast modes (they bind what they need explicitly) but fatal
    here: with no bound Attack button the main-hand keypress sends no CMSG_ATTACK and the kill can
    never happen.

    Spell 78 is "Attack" (playercreateinfo_action row 1,1,73,78 for Human Warrior). It goes on
    button 0 rather than the starting button 73 because 73 sits on a later action-bar page that a
    single keypress cannot reach. Action type 0 is ACTION_BUTTON_TYPE_SPELL.
    """
    return (
        f"DELETE FROM `character_action` WHERE `guid`={CHARACTER_GUID} AND `spec`=0;"
        f"DELETE FROM `character_spell` WHERE `guid`={CHARACTER_GUID} AND `spell`={LEVEL_UP_ATTACK_SPELL};"
        f"INSERT INTO `character_spell` (`guid`,`spell`,`specMask`) "
        f"VALUES ({CHARACTER_GUID},{LEVEL_UP_ATTACK_SPELL},1);"
        f"INSERT INTO `character_action` (`guid`,`spec`,`button`,`action`,`type`) "
        f"VALUES ({CHARACTER_GUID},0,{LEVEL_UP_ATTACK_BUTTON},{LEVEL_UP_ATTACK_SPELL},0);"
    )


def level_up_tutorial_seed_sql() -> str:
    """Marks every tutorial as seen for the fixture account so no modal popup appears.

    The first run that reached the world was blocked by the modal "To fight your enemies, click on
    Attack" tutorial: it swallows keyboard input before it reaches the chat edit box, so the
    /target typing produced no CMSG_MESSAGECHAT and the attacks never happened. Tutorial state is
    account-keyed (WorldSession::m_Tutorials <-> account_tutorial), and SendTutorialsData ships the
    flags verbatim, so all 0xFFFFFFFF means "already seen".
    """
    values = ", ".join(["4294967295"] * 8)
    return (
        f"DELETE FROM `account_tutorial` WHERE `accountId`={ACCOUNT_ID};"
        f"INSERT INTO `account_tutorial` "
        "(`tut0`,`tut1`,`tut2`,`tut3`,`tut4`,`tut5`,`tut6`,`tut7`,`accountId`) "
        f"VALUES ({values},{ACCOUNT_ID});"
    )


def level_up_gear_seed_sql() -> str:
    """Equips the fixture with the real Human Warrior starting kit.

    populated_character_seed_sql inserts only the `characters` row, so the fixture is naked and
    unarmed - unlike a genuinely created level-1 warrior. Against even a level-1 creature an unarmed
    warrior cannot out-damage it, so the kill (and therefore any XP) never lands. These are the
    starting items the Plan 22 fixture also asserts (Recruit's Vest/Pants/Boots, Worn Greatsword,
    Hearthstone), placed in their real equipment slots.
    """
    rows = ", ".join(f"({guid},{entry},{CHARACTER_GUID},1,{durability},'0 0 0 0 0 ','{'0 ' * 36}')"
                     for guid, entry, _bag, _slot, durability in LEVEL_UP_GEAR)
    slots = ", ".join(f"({CHARACTER_GUID},{bag},{slot},{guid})"
                      for guid, _entry, bag, slot, _durability in LEVEL_UP_GEAR)
    return (
        f"DELETE FROM `character_inventory` WHERE `guid`={CHARACTER_GUID};"
        f"DELETE FROM `item_instance` WHERE `owner_guid`={CHARACTER_GUID};"
        "INSERT INTO `item_instance` "
        "(`guid`,`itemEntry`,`owner_guid`,`count`,`durability`,`charges`,`enchantments`) "
        f"VALUES {rows};"
        "INSERT INTO `character_inventory` (`guid`,`bag`,`slot`,`item`) "
        f"VALUES {slots};"
    )


def level_up_client_alive() -> bool:
    """True while the owned WoW client process exists.

    The run loop's own liveness check keys off Wine processes, and wineserver outlives the client,
    so waiting on that alone keeps the script sleeping long after the game has closed.
    """
    result = subprocess.run(
        ["pgrep", "-f", "Wow-64.exe"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
    )
    return result.returncode == 0


def level_up_world_seed_sql() -> str:
    """Spawns the hostile creatures Plan 31 kills for XP.

    One level-1 creature is worth far less than the 400 XP the level 1 -> 2 threshold needs (a real
    run granted 100 XP), so several are seeded and killed in turn. Faction 14 is hostile to the
    player, so the client-local name selection finds each one.
    """
    x, y, z = CHARACTER_POSITION
    rows = []
    for index in range(LEVEL_UP_XP_TARGET_COUNT):
        offset_x = LEVEL_UP_XP_TARGET_OFFSET[0] + (index % 3) * 2
        offset_y = LEVEL_UP_XP_TARGET_OFFSET[1] + (index // 3) * 2
        rows.append(
            f"({LEVEL_UP_XP_TARGET_GUID + index},{LEVEL_UP_XP_TARGET_ENTRY},"
            f"{CHARACTER_MAP},{CHARACTER_ZONE},{CHARACTER_ZONE},{x + offset_x},{y + offset_y},{z},0,0)"
        )
    guids = ", ".join(str(LEVEL_UP_XP_TARGET_GUID + index) for index in range(LEVEL_UP_XP_TARGET_COUNT))
    return (
        f"DELETE FROM `creature` WHERE `guid` IN ({guids});"
        "INSERT INTO `creature` "
        "(`guid`,`id`,`map`,`zoneId`,`areaId`,`position_x`,`position_y`,`position_z`,"
        "`wander_distance`,`MovementType`) "
        f"VALUES {', '.join(rows)};"
    )


def verify_level_up_seed(manifest: Manifest, generation: Generation) -> dict[str, object]:
    """Precondition check run before the client session.

    Three separate inconclusive runs were each a missing scene precondition, not a server problem:
    no attack binding, the wrong (friendly, nearer) target from Tab, and a modal tutorial swallowing
    input. Each was cheap to detect in the database and expensive to detect in a live session, so
    assert them here instead of discovering them from a run with zero packets.
    """
    characters = generation["schemas"]["characters"]
    world = generation["schemas"]["world"]
    character = mysql(
        manifest, generation,
        f"SELECT `level`,`race`,`class` FROM `characters` WHERE `guid`={CHARACTER_GUID};", characters,
    )
    if character != "1\t1\t1":
        raise RuntimeError(f"level-up fixture is not a level-1 Human Warrior: {character!r}")
    action = mysql(
        manifest, generation,
        f"SELECT `button`,`action`,`type` FROM `character_action` WHERE `guid`={CHARACTER_GUID} "
        "AND `spec`=0;",
        characters,
    )
    if action != f"{LEVEL_UP_ATTACK_BUTTON}\t{LEVEL_UP_ATTACK_SPELL}\t0":
        raise RuntimeError(f"auto-attack is not bound to button {LEVEL_UP_ATTACK_BUTTON}: {action!r}")
    weapon = mysql(
        manifest, generation,
        "SELECT ii.`itemEntry` FROM `character_inventory` ci "
        "JOIN `item_instance` ii ON ii.`guid`=ci.`item` "
        f"WHERE ci.`guid`={CHARACTER_GUID} AND ci.`bag`=0 AND ci.`slot`=15;",
        characters,
    )
    if weapon != str(LEVEL_UP_MAINHAND_ENTRY):
        raise RuntimeError(f"no weapon equipped in the main hand: {weapon!r}")
    tutorials = mysql(
        manifest, generation,
        f"SELECT `tut0`,`tut1`,`tut2`,`tut3`,`tut4`,`tut5`,`tut6`,`tut7` FROM `account_tutorial` "
        f"WHERE `accountId`={ACCOUNT_ID};",
        characters,
    )
    if tutorials.split("\t") != ["4294967295"] * 8:
        raise RuntimeError(f"tutorial flags were not cleared, a modal may swallow input: {tutorials!r}")
    target = mysql(
        manifest, generation,
        f"SELECT COUNT(*) FROM `creature` WHERE `guid` BETWEEN {LEVEL_UP_XP_TARGET_GUID} "
        f"AND {LEVEL_UP_XP_TARGET_GUID + LEVEL_UP_XP_TARGET_COUNT - 1};", world,
    )
    if target != str(LEVEL_UP_XP_TARGET_COUNT):
        raise RuntimeError(f"expected {LEVEL_UP_XP_TARGET_COUNT} XP targets, seeded {target!r}")
    return {
        "character": {"level": 1, "race": 1, "class": 1},
        "attack_binding": {"button": LEVEL_UP_ATTACK_BUTTON, "spell": LEVEL_UP_ATTACK_SPELL},
        "tutorial_flags_cleared": True,
        "xp_target": {"guid": LEVEL_UP_XP_TARGET_GUID, "entry": LEVEL_UP_XP_TARGET_ENTRY},
    }


def populated_character_seed_sql() -> str:
    x, y, z = CHARACTER_POSITION
    return (
        f"DELETE FROM `characters` WHERE `account`={ACCOUNT_ID} OR `guid`={CHARACTER_GUID} "
        f"OR `name`='{CHARACTER_NAME}';"
        # `health` must be seeded non-zero. Player::LoadFromDB restores it verbatim via
        # SetHealth(savedHealth), so leaving it at the column default of 0 loads the
        # character with no health -- the client then receives a 0/0-HP player in its own
        # CREATE_OBJECT2 block and never completes world entry (permanent 90% loading
        # screen). Seeded larger than any level-1 pool; LoadFromDB clamps to GetMaxHealth(),
        # so this simply means "spawn at full health".
        "INSERT INTO `characters` "
        "(`guid`,`account`,`name`,`race`,`class`,`gender`,`level`,`skin`,`face`,`hairStyle`,`hairColor`,"
        "`facialStyle`,`playerFlags`,`position_x`,`position_y`,`position_z`,`map`,`orientation`,`taximask`,"
        "`at_login`,`zone`,`extra_flags`,`equipmentCache`,`exploredZones`,`knownTitles`,`order`,`innTriggerId`,"
        # `cinematic`=1 marks the intro cinematic as already seen. Left at the 0 default the
        # server sends SMSG_TRIGGER_CINEMATIC just before SMSG_LOGIN_VERIFY_WORLD (matching
        # TrinityCore's `if (!getCinematic())` gate), and the real client parks in cinematic
        # mode: it never answers with CMSG_COMPLETE_CINEMATIC or CMSG_NEXT_CINEMATIC_CAMERA,
        # never reaches the normal in-world state, and so never sends CMSG_TIME_SYNC_RESP --
        # while still servicing every other opcode, which makes it look like a loading hang.
        # The cinematic path is not what this harness exercises, so skip it.
        "`health`,`cinematic`) "
        f"VALUES ({CHARACTER_GUID},{ACCOUNT_ID},'{CHARACTER_NAME}',{CHARACTER_RACE},{CHARACTER_CLASS},0,1,0,0,0,0,0,0,"
        f"{x},{y},{z},{CHARACTER_MAP},{CHARACTER_ORIENTATION},'',0,{CHARACTER_ZONE},0,'','','',"
        f"{CHARACTER_LIST_POSITION},0,10000,1);"
    )


def verify_populated_character_seed(manifest: Manifest, generation: Generation) -> dict[str, object]:
    x, y, z = CHARACTER_POSITION
    matches = mysql(
        manifest, generation,
        "SELECT COUNT(*) FROM `characters` WHERE "
        f"`guid`={CHARACTER_GUID} AND `account`={ACCOUNT_ID} AND `name`='{CHARACTER_NAME}' "
        f"AND `race`={CHARACTER_RACE} AND `class`={CHARACTER_CLASS} AND `gender`=0 AND `level`=1 "
        "AND `skin`=0 AND `face`=0 AND `hairStyle`=0 AND `hairColor`=0 AND `facialStyle`=0 "
        f"AND ABS(`position_x`-({x}))<0.001 AND ABS(`position_y`-({y}))<0.001 "
        f"AND ABS(`position_z`-({z}))<0.001 AND `map`={CHARACTER_MAP} AND `zone`={CHARACTER_ZONE} "
        f"AND ABS(`orientation`-({CHARACTER_ORIENTATION}))<0.001 "
        "AND `playerFlags`=0 AND `at_login`=0 AND `extra_flags`=0 AND COALESCE(`order`,0)=7 "
        "AND `taximask`='' AND `innTriggerId`=0 AND `equipmentCache`='' AND `exploredZones`='' "
        "AND `knownTitles`='' AND `cinematic`=1 AND `deleteDate` IS NULL;",
        generation["schemas"]["characters"],
    )
    if matches != "1":
        raise RuntimeError(f"owned populated character seed matched {matches!r} rows instead of one")
    return {
        "guid_low": CHARACTER_GUID, "name": CHARACTER_NAME, "race": CHARACTER_RACE,
        "class": CHARACTER_CLASS, "gender": 0,
        "level": 1, "map": CHARACTER_MAP, "zone": CHARACTER_ZONE,
        "list_position": CHARACTER_LIST_POSITION,
        "flags": 0, "flags2": 0, "visual_items_nonzero": 0,
    }


def verify_populated_character_identity(manifest: Manifest, generation: Generation) -> dict[str, object]:
    # Seeded modes insert the character with the synthetic CHARACTER_GUID fixture, but a
    # character made through real character creation gets a server-assigned GUID instead, so
    # this looks the row up by name/account rather than assuming the fixture GUID.
    guids = mysql(
        manifest, generation,
        f"SELECT `guid` FROM `characters` WHERE `account`={ACCOUNT_ID} AND `name`='{CHARACTER_NAME}' "
        "AND `deleteDate` IS NULL;",
        generation["schemas"]["characters"],
    )
    rows = guids.splitlines()
    if len(rows) != 1:
        raise RuntimeError(f"owned selected character matched {len(rows)} rows instead of one")
    return {
        "guid_low": int(rows[0]), "name": CHARACTER_NAME, "race": CHARACTER_RACE,
        "class": CHARACTER_CLASS, "gender": 0,
        "level": 1, "map": CHARACTER_MAP, "zone": CHARACTER_ZONE,
        "list_position": CHARACTER_LIST_POSITION,
        "flags": 0, "flags2": 0, "visual_items_nonzero": 0,
    }


def realm_character_count(manifest: Manifest, generation: Generation) -> int:
    output = mysql(
        manifest, generation,
        f"SELECT `numchars` FROM `realmcharacters` WHERE `realmid`={REALM_ID} AND `acctid`={ACCOUNT_ID};",
        generation["schemas"]["auth"],
    )
    try:
        return int(output)
    except ValueError as error:
        raise RuntimeError(f"owned auth database returned an invalid realm character count: {output!r}") from error


def import_sql_directory(
    manifest: Manifest, generation: Generation, directory: Path, schema: str,
) -> None:
    paths = sorted(directory.glob("*.sql"))
    if not paths:
        raise RuntimeError(f"SQL base directory contains no files: {directory}")
    for path in paths:
        mysql(manifest, generation, path.read_bytes(), schema)


def apply_released_updates(
    manifest: Manifest, generation: Generation, directory: Path, schema: str,
) -> list[str]:
    applied = set(mysql(manifest, generation, "SELECT `name` FROM `updates`;", schema).splitlines())
    added = []
    for path in sorted(directory.glob("*.sql")):
        if path.name in applied:
            continue
        contents = path.read_bytes()
        file_hash = hashlib.sha1(contents).hexdigest().upper()
        mysql(
            manifest, generation,
            contents + b"\nDELIMITER ;\n" + (
                "INSERT INTO `updates` (`name`,`hash`,`state`,`speed`) "
                f"VALUES ('{path.name}','{file_hash}','RELEASED',0);"
            ).encode(),
            schema,
        )
        added.append(path.name)
    return added


def wait_for_mysql(manifest: Manifest, generation: Generation) -> None:
    deadline = time.monotonic() + 90
    ready_since: float | None = None
    while time.monotonic() < deadline:
        result = run_command(
            [
                "docker", "exec", str(generation["docker"]["container"]), "mysql", "--batch",
                "--skip-column-names", "-uroot", f"-p{manifest['mysql_root_password']}",
                "-e", "SELECT 1;",
            ],
            check=False,
        )
        if result.returncode == 0 and result.stdout.strip() == b"1":
            if ready_since is None:
                ready_since = time.monotonic()
            elif time.monotonic() - ready_since >= 1:
                return
        else:
            ready_since = None
        time.sleep(0.25)
    raise RuntimeError("owned MySQL did not become ready within 90 seconds")


def database_cache_path(manifest: Manifest) -> Path:
    return Path(manifest["root"]) / "database-cache.sql"


def database_cache_staging_path(manifest: Manifest) -> Path:
    return Path(manifest["root"]) / ".database-cache.sql.building"


def remove_database_cache_files(manifest: Manifest) -> None:
    for path in (database_cache_path(manifest), database_cache_staging_path(manifest)):
        if path.is_symlink():
            raise RuntimeError(f"refusing symlinked database cache path: {path}")
        path.unlink(missing_ok=True)


def reconcile_database_cache(manifest_path: Path, manifest: Manifest, key: str) -> Path | None:
    record = manifest.get("database_cache")
    if not record:
        return None
    path = Path(str(record["path"]))
    if path != database_cache_path(manifest):
        raise RuntimeError("cached database dump is outside the manifest root")
    state = record.get("state")
    if state in {"building", "purging"}:
        remove_database_cache_files(manifest)
        manifest.pop("database_cache")
        save_manifest(manifest_path, manifest)
        return None
    if state != "ready":
        raise RuntimeError("cached database dump has an unknown lifecycle state")
    if path.is_symlink():
        raise RuntimeError("refusing a symlinked database cache")
    if record.get("key") != key or not path.is_file() or sha256(path) != record.get("sha256"):
        record["state"] = "purging"
        save_manifest(manifest_path, manifest)
        remove_database_cache_files(manifest)
        manifest.pop("database_cache")
        save_manifest(manifest_path, manifest)
        return None
    return path


def dump_database_cache(
    manifest_path: Path, manifest: Manifest, generation: Generation, key: str,
) -> Path:
    assert_container_owned(manifest, generation)
    path = database_cache_path(manifest)
    temporary = database_cache_staging_path(manifest)
    if path.exists() or path.is_symlink() or temporary.exists() or temporary.is_symlink():
        raise RuntimeError("refusing to overwrite an unrecorded database cache file")
    manifest["database_cache"] = {
        "version": DATABASE_CACHE_VERSION,
        "path": str(path),
        "key": key,
        "released_updates": generation["released_updates"],
        "state": "building",
    }
    save_manifest(manifest_path, manifest)
    command = [
        "docker", "exec", str(generation["docker"]["container"]), "mysqldump",
        "-uroot", f"-p{manifest['mysql_root_password']}", "--single-transaction",
        "--set-gtid-purged=OFF", "--databases", *generation["schemas"].values(),
    ]
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as handle:
        result = subprocess.run(
            command, stdout=handle, stderr=subprocess.PIPE, check=False, timeout=1800,
        )
        if result.returncode:
            stderr = result.stderr.decode(errors="replace").strip()
            raise RuntimeError(f"mysqldump failed with exit {result.returncode}: {stderr}")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)
    os.chmod(path, 0o600)
    manifest["database_cache"]["sha256"] = sha256(path)
    manifest["database_cache"]["state"] = "ready"
    save_manifest(manifest_path, manifest)
    return path


def restore_database_cache(
    manifest_path: Path, manifest: Manifest, generation: Generation, path: Path,
) -> None:
    assert_container_owned(manifest, generation)
    command = [
        "docker", "exec", "-i", str(generation["docker"]["container"]), "mysql",
        "-uroot", f"-p{manifest['mysql_root_password']}",
    ]
    # Group dump inserts between its table locks instead of flushing every insert separately.
    with tempfile.TemporaryFile(dir=manifest["root"]) as handle, path.open("rb") as source:
        handle.write(b"SET autocommit=0;\n")
        shutil.copyfileobj(source, handle)
        handle.write(b"\nCOMMIT;\n")
        handle.seek(0)
        result = subprocess.run(
            command, stdin=handle, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            check=False, timeout=1800,
        )
    if result.returncode:
        stderr = result.stderr.decode(errors="replace").strip()
        manifest["database_cache"]["state"] = "purging"
        save_manifest(manifest_path, manifest)
        reconcile_database_cache(manifest_path, manifest, str(generation["inputs"]["database_cache_key"]))
        raise RuntimeError(f"database cache restore failed with exit {result.returncode}: {stderr}")


def replace_config(source: str, replacements: dict[str, str]) -> str:
    lines = source.splitlines()
    seen: set[str] = set()
    for index, line in enumerate(lines):
        for key, value in replacements.items():
            if re.match(rf"^\s*#?\s*{re.escape(key)}\s*=", line):
                if key not in seen:
                    lines[index] = f"{key} = {value}"
                    seen.add(key)
                elif not line.lstrip().startswith("#"):
                    lines[index] = f"# disabled duplicate for Plan 7: {line}"
    missing = set(replacements) - seen
    if missing:
        raise RuntimeError(f"config template keys are missing: {sorted(missing)}")
    return "\n".join(lines) + "\n"


def write_configs(manifest: Manifest, generation: Generation) -> None:
    paths = generation["paths"]
    geometry = bool(generation["inputs"].get("geometry"))
    config_dir = Path(paths["config"])
    log_dir = Path(paths["logs"])
    config_dir.mkdir(parents=True, exist_ok=True)
    log_dir.mkdir(parents=True, exist_ok=True)
    common = (
        f"127.0.0.1;{generation['ports']['mysql']};{manifest['mysql_user']};"
        f"{manifest['mysql_password']};"
    )
    auth_replacements = {
        "RealmServerPort": str(AUTH_PORT),
        "BindIP": '"127.0.0.1"',
        "LogsDir": f'"{log_dir}"',
        "PidFile": f'"{config_dir / "authserver.pid"}"',
        "RealmsStateUpdateDelay": "1",
        "LoginDatabaseInfo": f'"{common}{generation["schemas"]["auth"]}"',
        "Updates.EnableDatabases": "0",
        "Updates.AutoSetup": "0",
        "StrictVersionCheck": "0",
        "SourceDirectory": f'"{REPO_ROOT}"',
        "Appender.Auth": '2,5,0,AuthServer.log,w',
        "Logger.root": "4,Auth",
    }
    world_replacements = {
        "RealmID": str(REALM_ID),
        "WorldServerPort": str(generation["ports"]["world"]),
        "BindIP": '"127.0.0.1"',
        "LoginDatabaseInfo": f'"{common}{generation["schemas"]["auth"]}"',
        "WorldDatabaseInfo": f'"{common}{generation["schemas"]["world"]}"',
        "CharacterDatabaseInfo": f'"{common}{generation["schemas"]["characters"]}"',
        "DataDir": f'"{Path(paths["data"])}"',
        "LogsDir": f'"{log_dir}"',
        "PidFile": f'"{config_dir / "worldserver.pid"}"',
        "Console.Enable": "0",
        "Updates.EnableDatabases": "0",
        "Updates.AutoSetup": "0",
        "Expansion": "3",
        "MoveMaps.Enable": "1" if geometry else "0",
        "vmap.enableLOS": "1" if geometry else "0",
        "vmap.enableHeight": "1" if geometry else "0",
        "Warden.Enabled": "0",
        "Ra.Enable": "0",
        "SOAP.Enabled": "0",
        "Cluster.Enabled": "0",
        # The Plan 32 mode verifies against the wire payloads (cast counters, the CAST_FAILED
        # reason byte), so its generations always capture them. Filenames are relative to LogsDir;
        # empty everywhere else, which leaves packet logging off (sessions also need the flag the
        # socket reads from this same key).
        "PacketLogFile": (
            '"packets.pkt"'
            if generation["mode"] in (
                PLAN32_GEOMETRY_MODE, VENDOR_TRADE_MODE, VENDOR_NEGATIVE_MODE, TRAINER_MODE,
                MAGE_COMBAT_MODE, MAGE_FOLLOWUP_MODE,
            )
            else '""'
        ),
        "Appender.Server": '2,5,1,WorldServer.log,w',
        "Logger.network": (
            "5,Server" if ISSUE97 or generation["mode"] in {
                BASIC_MOVEMENT_MODE, RUN_SPEED_MODE, GROUND_MOVEMENT_MODE, JUMP_FALL_LAND_MODE,
                WALK_RUN_MODE_SWITCH_MODE, PLAN32_GEOMETRY_MODE, VENDOR_TRADE_MODE, VENDOR_NEGATIVE_MODE,
                TRAINER_MODE, MAGE_COMBAT_MODE, MAGE_FOLLOWUP_MODE,
            }
            else "4,Server"
        ),
        "Logger.network.opcode": "4,Server",
    }
    auth_source = (REPO_ROOT / "src/server/apps/authserver/authserver.conf.dist").read_text()
    world_source = (REPO_ROOT / "src/server/apps/worldserver/worldserver.conf.dist").read_text()
    Path(paths["auth_config"]).write_text(replace_config(auth_source, auth_replacements))
    Path(paths["world_config"]).write_text(replace_config(world_source, world_replacements))


def proc_stat(pid: int) -> tuple[int, int, int]:
    text = Path(f"/proc/{pid}/stat").read_text()
    tail = text[text.rfind(")") + 2:].split()
    return int(tail[19]), int(tail[2]), int(tail[3])


def proc_environment(pid: int) -> dict[str, str]:
    values: dict[str, str] = {}
    for field in Path(f"/proc/{pid}/environ").read_bytes().split(b"\0"):
        if b"=" in field:
            key, value = field.split(b"=", 1)
            values[key.decode(errors="replace")] = value.decode(errors="replace")
    return values


def process_identity(pid: int, kind: str, *, config: Path | None = None, prefix: Path | None = None) -> ProcessIdentity:
    start_ticks, process_group, session = proc_stat(pid)
    record: ProcessIdentity = {
        "kind": kind,
        "pid": pid,
        "start_ticks": start_ticks,
        "boot_id": Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
        "exe": str(Path(f"/proc/{pid}/exe").resolve()),
        "cmdline": [
            part.decode(errors="replace")
            for part in Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0") if part
        ],
        "cwd": str(Path(f"/proc/{pid}/cwd").resolve()),
        "process_group": process_group,
        "session": session,
        "active": True,
    }
    if config is not None:
        record["config"] = str(config.resolve())
    if prefix is not None:
        record["prefix"] = str(prefix.resolve())
    return record


def assert_process_owned(record: ProcessIdentity) -> None:
    pid = int(record["pid"])
    proc = Path(f"/proc/{pid}")
    if not proc.exists():
        raise RuntimeError(f"owned {record['kind']} PID {pid} is absent")
    try:
        current = process_identity(pid, record["kind"])
    except FileNotFoundError as error:
        raise RuntimeError(f"owned {record['kind']} PID {pid} exited during identity verification") from error
    for key in ("start_ticks", "boot_id", "exe"):
        if current[key] != record[key]:
            raise RuntimeError(f"refusing changed {record['kind']} PID {pid}")
    if "config" in record and str(record["config"]) not in current["cmdline"]:
        raise RuntimeError(f"owned {record['kind']} config no longer matches PID {pid}")
    if "prefix" in record:
        environment = proc_environment(pid)
        if Path(environment.get("WINEPREFIX", "")).resolve() != Path(record["prefix"]).resolve():
            raise RuntimeError(f"owned Wine prefix no longer matches PID {pid}")


def find_wine_processes(prefix: Path) -> list[ProcessIdentity]:
    expected = prefix.resolve()
    records = []
    for proc in Path("/proc").iterdir():
        if not proc.name.isdigit():
            continue
        try:
            environment = proc_environment(int(proc.name))
            value = environment.get("WINEPREFIX")
            if value and Path(value).resolve() == expected:
                records.append(process_identity(int(proc.name), "wine", prefix=prefix))
        except (FileNotFoundError, PermissionError, ProcessLookupError, ValueError):
            continue
    return sorted(records, key=lambda item: int(item["pid"]))


def add_processes(generation: Generation, records: list[ProcessIdentity]) -> None:
    known = {(int(item["pid"]), int(item["start_ticks"])) for item in generation["processes"]}
    for record in records:
        identity = (int(record["pid"]), int(record["start_ticks"]))
        if identity not in known:
            generation["processes"].append(record)
            known.add(identity)


def stop_process(record: ProcessIdentity, process: subprocess.Popen[bytes] | None = None) -> None:
    if not record.get("active", False):
        return
    pid = int(record["pid"])
    if not Path(f"/proc/{pid}").exists():
        record["active"] = False
        return
    assert_process_owned(record)
    os.kill(pid, signal.SIGTERM)
    deadline = time.monotonic() + 15
    while Path(f"/proc/{pid}").exists() and time.monotonic() < deadline:
        if process is not None and process.poll() is not None:
            break
        time.sleep(0.1)
    if process is not None and process.poll() is None:
        process.wait(timeout=1)
    if Path(f"/proc/{pid}").exists() and (process is None or process.poll() is None):
        raise RuntimeError(f"owned {record['kind']} PID {pid} did not stop")
    record["active"] = False


def stop_servers(generation: Generation, popens: dict[str, subprocess.Popen[bytes]] | None = None) -> None:
    popens = popens or {}
    for kind in ("worldserver", "authserver"):
        for record in reversed(generation["processes"]):
            if record["kind"] == kind and record.get("active", False):
                stop_process(record, popens.get(kind))


def wine_environment(generation: Generation) -> dict[str, str]:
    paths = generation["paths"]
    environment = os.environ.copy()
    environment.update({
        "WINEPREFIX": paths["wine_prefix"],
        "WINEARCH": "win64",
        "XDG_DATA_HOME": paths["xdg_data"],
        "XDG_CONFIG_HOME": paths["xdg_config"],
        "XDG_CACHE_HOME": paths["xdg_cache"],
        "XDG_STATE_HOME": paths["xdg_state"],
        "GSETTINGS_BACKEND": "memory",
        "DISPLAY": str(generation["inputs"]["display"]),
        "WINEDLLOVERRIDES": "d3d9=n,b",
        "http_proxy": "http://127.0.0.1:9",
        "https_proxy": "http://127.0.0.1:9",
        # +seh is cheap and is what surfaced the "stack overflow" warning that used
        # to be mistaken for a client crash (see issue #32) -- that warning turns out
        # to be an artifact of stop_wine()'s own `wineserver -k` teardown, not a real
        # in-game fault: it appears at a fixed address/size regardless of game state,
        # and only on runs where the client process was still alive when killed
        # (compare a run with no crash line -- its trace just stops mid-normal-trace,
        # same as the "crashed" runs stop mid-SEH-warning). Do not treat this warning
        # as a failure signal without first checking the client was still responsive.
        # +tid/+module are far too verbose/slow for routine runs, only useful for a
        # one-off manual diagnostic session.
        "WINEDEBUG": "+seh",
    })
    xauthority = str(generation["inputs"].get("xauthority") or "")
    if xauthority:
        environment["XAUTHORITY"] = xauthority
    else:
        environment.pop("XAUTHORITY", None)
    return environment


def configure_wine_proxy(generation: Generation) -> None:
    wine = str(generation["inputs"]["wine"])
    key = r"HKCU\Software\Microsoft\Windows\CurrentVersion\Internet Settings"
    environment = wine_environment(generation)
    run_command(
        [wine, "reg", "add", key, "/v", "ProxyEnable", "/t", "REG_DWORD", "/d", "1", "/f"],
        env=environment,
    )
    run_command(
        [wine, "reg", "add", key, "/v", "ProxyServer", "/t", "REG_SZ", "/d", "127.0.0.1:9", "/f"],
        env=environment,
    )


def stop_wine(generation: Generation) -> None:
    prefix = Path(generation["paths"]["wine_prefix"])
    current = find_wine_processes(prefix)
    add_processes(generation, current)
    for record in current:
        assert_process_owned(record)
    wineserver = Path(str(generation["inputs"]["wine_runner"])) / "bin/wineserver"
    if current and wineserver.is_file():
        run_command([str(wineserver), "-k"], check=False, timeout=30, env=wine_environment(generation))
        deadline = time.monotonic() + 15
        while find_wine_processes(prefix) and time.monotonic() < deadline:
            time.sleep(0.1)
    current_identities = {(int(item["pid"]), int(item["start_ticks"])): item for item in find_wine_processes(prefix)}
    for record in reversed(generation["processes"]):
        key = (int(record["pid"]), int(record["start_ticks"]))
        if record["kind"] == "wine" and key in current_identities:
            stop_process(record)
        elif record["kind"] == "wine":
            record["active"] = False


def wait_for_port(port: int, record: ProcessIdentity, timeout: int) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        assert_process_owned(record)
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                return
        except OSError:
            time.sleep(0.1)
    raise RuntimeError(f"owned server did not open loopback port {port} within {timeout} seconds")


def wait_for_world(manifest: Manifest, generation: Generation, record: ProcessIdentity) -> None:
    deadline = time.monotonic() + 300
    while time.monotonic() < deadline:
        assert_process_owned(record)
        try:
            with socket.create_connection(("127.0.0.1", generation["ports"]["world"]), timeout=0.2):
                listening = True
        except OSError:
            listening = False
        flag = mysql(
            manifest, generation, f"SELECT `flag` FROM `realmlist` WHERE `id`={REALM_ID};",
            generation["schemas"]["auth"],
        )
        if listening and flag == "0":
            return
        time.sleep(0.25)
    raise RuntimeError("worldserver did not reach listener and realm-online readiness")


def binary_identity(path: Path, head: str, *, reports_version: bool) -> dict[str, object]:
    resolved = path.resolve()
    if not resolved.is_file() or not os.access(resolved, os.X_OK):
        raise RuntimeError(f"required executable is absent: {resolved}")
    identity: dict[str, object] = {"path": str(resolved), "sha256": sha256(resolved)}
    if reports_version:
        output = run_command([str(resolved), "--version"], timeout=30)
        version = (output.stdout + output.stderr).decode(errors="replace").strip()
        if head[:12] not in version and head not in version:
            raise RuntimeError(f"binary does not report current HEAD {head[:12]}: {resolved}")
        identity["version"] = version
    return identity


def geometry_input_hashes(data_root: Path) -> dict[str, str]:
    """Content hashes for the vmaps and mmaps sets a geometry-enabled run stages.

    ``--geometry`` makes the server load collision and navmesh data, so both sets must be present
    under the data root. An absent or empty one is a configuration error, not a silently degraded
    run that would only report FileNotFound per tile.
    """
    hashes: dict[str, str] = {}
    for name in ("vmaps", "mmaps"):
        root = data_root / name
        if not root.is_dir() or not any(root.iterdir()):
            raise RuntimeError(f"--geometry requires a non-empty {name} directory under the data root")
        hashes[f"data_{name}_hash"] = tree_content_hash(root)
    return hashes


def geometry_inputs_differ(stored: dict, requested: dict) -> bool:
    """True when a prepared generation's staged inputs differ from the requested ones.

    Reusing a prepared generation is idempotent only for the same inputs. A geometry-enabled
    request must not silently continue a generation that was prepared without collision data (or
    with different sets), because nothing would be staged or reconfigured and the caller would get
    the geometry-free run it did not ask for. The frozen Plan 32 stage belongs to the same
    comparison: reusing the blocked generation for PLAN32_STAGE=clear would keep the player on the
    blocked point while the fixture moved to the clear one.
    """
    geometry = bool(requested.get("geometry"))
    if bool(stored.get("geometry")) != geometry:
        return True
    if stored.get("plan32_stage") != requested.get("plan32_stage"):
        return True
    if not geometry:
        return False
    return (
        stored.get("data_vmaps_hash") != requested.get("data_vmaps_hash")
        or stored.get("data_mmaps_hash") != requested.get("data_mmaps_hash")
    )


def preflight_prepare(args: argparse.Namespace) -> dict[str, object]:
    manifest_path = args.manifest.resolve()
    root = require_external_root(manifest_path.parent)
    if args.minimum_free_gib < 25:
        raise RuntimeError("--minimum-free-gib cannot weaken the 25 GiB Plan 7 safety floor")
    available = shutil.disk_usage(nearest_existing_parent(root)).free
    if available < args.minimum_free_gib * 1024 ** 3:
        raise RuntimeError(f"run filesystem has {available} bytes free; need {args.minimum_free_gib} GiB")
    require_unused(AUTH_PORT)
    image_result = run_command(["docker", "image", "inspect", MYSQL_IMAGE], check=False)
    if image_result.returncode:
        raise RuntimeError(f"required local image is absent: {MYSQL_IMAGE}")
    mysql_image_id = json.loads(image_result.stdout)[0]["Id"]
    for command in ("ss", "xprop", "wmctrl", "xwininfo"):
        if shutil.which(command) is None:
            raise RuntimeError(f"required evidence command is absent: {command}")
    head = run_command(["git", "rev-parse", "HEAD"], timeout=30).stdout.decode().strip()
    authserver = binary_identity(args.authserver, head, reports_version=True)
    worldserver = binary_identity(args.worldserver, head, reports_version=True)
    unit_tests = binary_identity(args.unit_tests, head, reports_version=False)
    client_root = args.client_root.resolve()
    client_executable = client_root / "Wow-64.exe"
    if not client_executable.is_file() or sha256(client_executable) != CLIENT_SHA256:
        raise RuntimeError("source Wow-64.exe does not match the pinned build-15595 SHA-256")
    data_root = args.data_root.resolve()
    dbc_root = args.server_dbc_root.resolve() if args.server_dbc_root else data_root / "dbc"
    if not dbc_root.is_dir() or not (data_root / "maps").is_dir():
        raise RuntimeError("data candidates must contain readable server DBC and Cataclysm maps directories")
    geometry = bool(getattr(args, "geometry", False))
    if args.mode == PLAN32_GEOMETRY_MODE and not geometry:
        # The LOS stages are only meaningful against the staged collision and navmesh data: without
        # --geometry the server would answer every cast from an empty world and the stage verdict
        # would describe nothing.
        raise RuntimeError("plan32-geometry requires --geometry (stages vmaps/ and mmaps/)")
    geometry_hashes = geometry_input_hashes(data_root) if geometry else {}
    if args.mode == RUN_SPEED_MODE:
        verify_run_speed_aura_source(dbc_root)
    wine_runner = args.wine_runner.resolve()
    wine = next(
        (path for path in (wine_runner / "bin/wine64", wine_runner / "bin/wine")
         if path.is_file() and os.access(path, os.X_OK)),
        None,
    )
    wineserver = wine_runner / "bin/wineserver"
    dxvk_d3d9 = wine_runner / "lib/wine/dxvk/x86_64-windows/d3d9.dll"
    if wine is None or not wineserver.is_file() or not os.access(wineserver, os.X_OK):
        raise RuntimeError(f"installed Wine runner is incomplete: {wine_runner}")
    if not dxvk_d3d9.is_file():
        raise RuntimeError(f"installed Wine runner has no bundled DXVK d3d9.dll: {wine_runner}")
    xauthority = args.xauthority.resolve() if args.xauthority else os.environ.get("XAUTHORITY", "")
    display_env = os.environ.copy()
    display_env["DISPLAY"] = args.display
    if xauthority:
        display_env["XAUTHORITY"] = str(xauthority)
    display = run_command(["xprop", "-root", "_NET_SUPPORTING_WM_CHECK"], check=False, env=display_env)
    if display.returncode:
        raise RuntimeError(f"cannot access requested X display {args.display}")
    migration = args.migration.resolve()
    if migration != DEFAULT_MIGRATION.resolve() or not migration.is_file():
        raise RuntimeError("--migration must name the Plan 6 build-15595 pending auth update")
    personal_bottle = args.personal_bottle.resolve()
    if not personal_bottle.exists():
        raise RuntimeError(f"personal Bottle baseline path is absent: {personal_bottle}")
    return {
        "root": str(root),
        "repo_commit": head,
        "mysql_image_id": mysql_image_id,
        "database_cache_key": database_cache_key(mysql_image_id, DATABASE_SQL_DIRECTORIES),
        "authserver": authserver,
        "worldserver": worldserver,
        "unit_tests": unit_tests,
        "client_root": str(client_root),
        "client_executable": str(client_executable),
        "client_tree": tree_metadata(client_root),
        "client_sha256": CLIENT_SHA256,
        "data_root": str(data_root),
        "server_dbc_root": str(dbc_root),
        "data_dbc_hash": tree_content_hash(dbc_root),
        "data_maps_hash": tree_content_hash(data_root / "maps"),
        "geometry": geometry,
        "data_vmaps_hash": geometry_hashes.get("data_vmaps_hash", ""),
        "data_mmaps_hash": geometry_hashes.get("data_mmaps_hash", ""),
        "wine_runner": str(wine_runner),
        "wine": str(wine),
        "wine_sha256": sha256(wine),
        "wineserver_sha256": sha256(wineserver),
        "dxvk_d3d9": str(dxvk_d3d9),
        "dxvk_d3d9_sha256": sha256(dxvk_d3d9),
        "personal_bottle": str(personal_bottle),
        "personal_bottle_tree": tree_metadata(personal_bottle),
        "migration": str(migration),
        "migration_sha256": sha256(migration),
        "display": args.display,
        "xauthority": str(xauthority),
    }


def prepare(args: argparse.Namespace) -> None:
    started = time.monotonic()
    inputs = preflight_prepare(args)
    if args.mode == PLAN32_GEOMETRY_MODE:
        # Freeze the stage here: run() refuses an environment that names another one, and verify()
        # requires the recorded stage to match the frozen value.
        stage = plan32_stage_or_fail()
        apply_plan32_stage_seed(args.mode, stage)
        inputs["plan32_stage"] = stage
    if args.mode == VENDOR_NEGATIVE_MODE:
        inputs["vendor_case"] = vendor_negative_case_or_fail()
    if args.mode == CHARACTER_CREATION_MODE:
        creation_class = creation_class_name()
        if creation_class not in CREATION_CLASSES:
            raise RuntimeError(f"CREATE_CLASS must be one of {sorted(CREATION_CLASSES)}; got {creation_class!r}")
        inputs["create_class"] = creation_class
    apply_vendor_trade_seed(args.mode)
    apply_vendor_negative_seed(args.mode)
    apply_trainer_seed(args.mode)
    apply_creation_class(args.mode, inputs.get("create_class", creation_class_name()))
    print(f"prepare preflight: {time.monotonic() - started:.1f}s", flush=True)
    manifest_path = args.manifest.resolve()
    if manifest_path.exists():
        manifest = load_manifest(manifest_path)
        latest = active_generation(manifest) if manifest["generations"] else None
        if latest is not None and latest["state"] != "reset":
            if latest["state"] == "prepared":
                if geometry_inputs_differ(latest.get("inputs", {}), inputs):
                    raise RuntimeError(
                        "the prepared generation used different geometry inputs (collision data or "
                        "navmesh presence/content changed); reset it or prepare a new generation"
                    )
                print(f"generation {latest['number']} is already prepared")
                return
            raise RuntimeError(f"cannot prepare after generation in state {latest['state']}")
        baseline = manifest["baseline"]
        current_protected = {
            "client_sha256": inputs["client_sha256"],
            "client_tree": inputs["client_tree"],
            "personal_bottle_tree": inputs["personal_bottle_tree"],
        }
        if any(baseline[key] != value for key, value in current_protected.items()):
            raise RuntimeError("protected client or personal Bottle changed between generations")
    else:
        manifest = {
            "version": 1,
            "run_id": uuid.uuid4().hex[:12],
            "repo_commit": str(inputs["repo_commit"]),
            "root": str(inputs["root"]),
            "mysql_root_password": secrets.token_hex(16),
            "mysql_user": "plan7",
            "mysql_password": secrets.token_hex(16),
            "baseline": {
                "docker": docker_inventory(),
                "client_sha256": inputs["client_sha256"],
                "client_tree": inputs["client_tree"],
                "personal_bottle_tree": inputs["personal_bottle_tree"],
            },
            "generations": [],
        }
    number = len(manifest["generations"]) + 1
    generation_root = Path(str(inputs["root"])) / f"generation-{number}"
    prefix = f"acore-cata-plan{plan_number(args.mode)}-{manifest['run_id']}-g{number}"
    paths = {
        "client": str(generation_root / "client"),
        "data": str(generation_root / "data"),
        "config": str(generation_root / "config"),
        "logs": str(generation_root / "logs"),
        "raw_evidence": str(generation_root / "evidence/raw"),
        "sanitized_evidence": str(generation_root / "evidence/sanitized.json"),
        "xdg_data": str(generation_root / "xdg/data"),
        "xdg_config": str(generation_root / "xdg/config"),
        "xdg_cache": str(generation_root / "xdg/cache"),
        "xdg_state": str(generation_root / "xdg/state"),
        "wine_prefix": str(generation_root / "wine-prefix"),
        "auth_config": str(generation_root / "config/authserver.conf"),
        "world_config": str(generation_root / "config/worldserver.conf"),
    }
    generation: Generation = {
        "number": number,
        "mode": args.mode,
        "stability_seconds": 10 if args.mode in CHARACTER_MODES else 0,
        "state": "allocating",
        "completed_state": "",
        "root": str(generation_root),
        "prefix": prefix,
        "ports": {"mysql": unused_port(), "auth": AUTH_PORT, "world": unused_port()},
        "docker": {
            "container": f"{prefix}-mysql", "container_id": None, "volume": f"{prefix}-mysql-data",
        },
        "schemas": {"auth": "acore_auth", "characters": "acore_characters", "world": "acore_world"},
        "processes": [],
        "inputs": inputs,
        "paths": paths,
        "released_updates": {},
        "evidence": None,
        "failure": None,
        "isolation_unchanged": False,
        "replay_passed": False,
    }
    manifest["generations"].append(generation)
    generation_root.mkdir(parents=True, exist_ok=False)
    save_manifest(manifest_path, manifest)
    try:
        labels = [
            "--label", f"org.azerothcore.plan={plan_number(args.mode)}",
            "--label", f"org.azerothcore.run_id={manifest['run_id']}",
            "--label", f"org.azerothcore.generation={number}",
        ]
        require_unused(generation["ports"]["mysql"])
        run_command(["docker", "volume", "create", *labels, str(generation["docker"]["volume"])])
        result = run_command([
            "docker", "run", "-d", "--name", str(generation["docker"]["container"]), *labels,
            "-e", f"MYSQL_ROOT_PASSWORD={manifest['mysql_root_password']}",
            "-p", f"127.0.0.1:{generation['ports']['mysql']}:3306",
            "-v", f"{generation['docker']['volume']}:/var/lib/mysql", str(inputs["mysql_image_id"]),
            "--skip-log-bin",  # This disposable database has no replication or point-in-time recovery.
        ])
        generation["docker"]["container_id"] = result.stdout.decode().strip()
        save_manifest(manifest_path, manifest)
        wait_for_mysql(manifest, generation)
        auth = generation["schemas"]["auth"]
        characters = generation["schemas"]["characters"]
        world = generation["schemas"]["world"]
        cache_key = str(inputs["database_cache_key"])
        cache = reconcile_database_cache(manifest_path, manifest, cache_key)
        if cache:
            restore_database_cache(manifest_path, manifest, generation, cache)
            generation["released_updates"] = dict(manifest["database_cache"]["released_updates"])
            generation["database_cache"] = {"key": cache_key, "result": "restored"}
        else:
            mysql(
                manifest, generation,
                f"CREATE DATABASE `{auth}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;"
                f"CREATE DATABASE `{characters}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;"
                f"CREATE DATABASE `{world}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;",
            )
            released = {}
            for key, schema in (("auth", auth), ("characters", characters), ("world", world)):
                import_sql_directory(manifest, generation, REPO_ROOT / f"data/sql/base/db_{key}", schema)
                released[key] = apply_released_updates(
                    manifest, generation, REPO_ROOT / f"data/sql/updates/db_{key}", schema,
                )
            generation["released_updates"] = released
            dump_database_cache(manifest_path, manifest, generation, cache_key)
            generation["database_cache"] = {"key": cache_key, "result": "built"}
        # Pending migrations aren't part of the database cache key (they're not released yet, so
        # they change independently of it) and must be applied fresh every prepare, whether the
        # schemas above came from a restored cache or a fresh import.
        for key, schema in (("auth", auth), ("characters", characters), ("world", world)):
            apply_released_updates(manifest, generation, REPO_ROOT / f"data/sql/updates/pending_db_{key}", schema)
        realm_count = 1 if args.mode in POPULATED_CHARACTER_MODES else 0
        mysql(
            manifest, generation,
            f"CREATE USER IF NOT EXISTS '{manifest['mysql_user']}'@'%' "
            f"IDENTIFIED BY '{manifest['mysql_password']}';"
            f"GRANT ALL ON `{auth}`.* TO '{manifest['mysql_user']}'@'%';"
            f"GRANT ALL ON `{characters}`.* TO '{manifest['mysql_user']}'@'%';"
            f"GRANT ALL ON `{world}`.* TO '{manifest['mysql_user']}'@'%';",
        )
        migration = Path(str(inputs["migration"]))
        mysql(manifest, generation, migration.read_bytes(), auth)
        build = mysql(
            manifest, generation,
            "SELECT CONCAT(`majorVersion`,'.',`minorVersion`,'.',`bugfixVersion`) "
            "FROM `build_info` WHERE `build`=15595;",
            auth,
        )
        if build != "4.3.4":
            raise RuntimeError(f"owned auth database reports unexpected build 15595 version: {build}")
        salt = bytes(range(1, 33))
        verifier = srp_registration(ACCOUNT, PASSWORD, salt)
        mysql(
            manifest, generation,
            f"DELETE FROM `realmcharacters` WHERE `acctid`={ACCOUNT_ID} OR `realmid`={REALM_ID};"
            f"DELETE FROM `account` WHERE `id`={ACCOUNT_ID} OR `username`='{ACCOUNT}';"
            f"DELETE FROM `realmlist` WHERE `id`={REALM_ID} OR `name`='Plan 7 Realm';"
            "INSERT INTO `account` (`id`,`username`,`salt`,`verifier`,`email`,`reg_mail`,`expansion`,`Flags`) "
            f"VALUES ({ACCOUNT_ID},'{ACCOUNT}',UNHEX('{salt.hex()}'),UNHEX('{verifier.hex()}'),"
            "'plan7@example.invalid','plan7@example.invalid',3,0);"
            "INSERT INTO `realmlist` (`id`,`name`,`address`,`localAddress`,`localSubnetMask`,`port`,`icon`,"
            "`flag`,`timezone`,`allowedSecurityLevel`,`population`,`gamebuild`) "
            f"VALUES ({REALM_ID},'Plan 7 Realm','127.0.0.1','127.0.0.1','255.255.255.0',"
            f"{generation['ports']['world']},0,0,1,0,0,{CLIENT_BUILD});"
            f"INSERT INTO `realmcharacters` (`realmid`,`acctid`,`numchars`) "
            f"VALUES ({REALM_ID},{ACCOUNT_ID},{realm_count});",
            auth,
        )
        if args.mode in POPULATED_CHARACTER_MODES:
            mysql(manifest, generation, populated_character_seed_sql(), characters)
            verify_populated_character_seed(manifest, generation)
            if character_row_count(manifest, generation) != 1 or realm_character_count(manifest, generation) != 1:
                raise RuntimeError("owned populated character seed counts do not equal one")
        print(f"prepare database ready: {time.monotonic() - started:.1f}s elapsed", flush=True)
        data_destination = Path(paths["data"])
        data_destination.mkdir()
        copy_tree(Path(str(inputs["server_dbc_root"])), data_destination / "dbc")
        copy_tree(Path(str(inputs["data_root"])) / "maps", data_destination / "maps")
        if tree_content_hash(data_destination / "dbc") != inputs["data_dbc_hash"]:
            raise RuntimeError("run-owned DBC copy differs from its candidate input")
        if tree_content_hash(data_destination / "maps") != inputs["data_maps_hash"]:
            raise RuntimeError("run-owned maps copy differs from its candidate input")
        if inputs.get("geometry"):
            for name in ("vmaps", "mmaps"):
                copy_tree(Path(str(inputs["data_root"])) / name, data_destination / name)
                if tree_content_hash(data_destination / name) != inputs[f"data_{name}_hash"]:
                    raise RuntimeError(f"run-owned {name} copy differs from its candidate input")
        client_destination = Path(paths["client"])
        link_client_base(Path(str(inputs["client_root"])), client_destination)
        if sha256(client_destination / "Wow-64.exe") != CLIENT_SHA256:
            raise RuntimeError("run client differs from the pinned source executable")
        for name in WRITABLE_CLIENT_DIRS:
            target = client_destination / name
            if target.exists():
                remove_owned_path(target, generation)
        write_client_config(client_destination)
        realmlist = client_destination / "Data/enUS/realmlist.wtf"
        realmlist.write_text("set realmlist 127.0.0.1\nset patchlist 127.0.0.1\n")
        install_dxvk(generation)
        for key in ("xdg_data", "xdg_config", "xdg_cache", "xdg_state", "raw_evidence"):
            Path(paths[key]).mkdir(parents=True, exist_ok=True)
        write_configs(manifest, generation)
        print(f"prepare files ready: {time.monotonic() - started:.1f}s elapsed", flush=True)
        run_command(
            [str(inputs["wine"]), "wineboot", "-u"], timeout=300, env=wine_environment(generation),
        )
        configure_wine_proxy(generation)
        stop_wine(generation)
        if find_wine_processes(Path(paths["wine_prefix"])):
            raise RuntimeError("fresh-prefix initialization left owned Wine processes running")
        set_state(generation, "prepared")
        save_manifest(manifest_path, manifest)
    except BaseException as error:
        record_failure(manifest_path, manifest, error)
        raise
    print(
        f"prepared Plan {plan_number(args.mode)} generation {number} in {generation_root} "
        f"(database cache {generation['database_cache']['result']}, {time.monotonic() - started:.1f}s)"
    )


def start_server(
    generation: Generation, kind: str, binary: Path, config: Path, stdout_path: Path,
) -> tuple[subprocess.Popen[bytes], ProcessIdentity, object]:
    output = stdout_path.open("wb")
    process = subprocess.Popen([str(binary), "-c", str(config)], stdout=output, stderr=subprocess.STDOUT)
    record = process_identity(process.pid, kind, config=config)
    generation["processes"].append(record)
    return process, record, output


def connection_log(generation: Generation) -> str:
    path = Path(generation["paths"]["client"]) / "Logs/connection.log"
    return path.read_text(errors="replace") if path.is_file() else ""


def matched_milestones(
    text: str, milestones: tuple[tuple[str, re.Pattern[str]], ...] = CLIENT_MILESTONES,
) -> list[str]:
    offset = 0
    found = []
    for name, pattern in milestones:
        match = pattern.search(text, offset)
        if match is None:
            break
        found.append(name)
        offset = match.end()
    return found


def capture_runtime(generation: Generation) -> None:
    raw = Path(generation["paths"]["raw_evidence"])
    raw.mkdir(parents=True, exist_ok=True)
    network = run_command(["ss", "-tpn"], check=False).stdout
    with (raw / "network.log").open("ab") as handle:
        handle.write(network)
        handle.write(b"\n")
    window = run_command(["wmctrl", "-lpGx"], check=False, env=wine_environment(generation))
    (raw / "windows.log").write_bytes(window.stdout + window.stderr)


def capture_owned_window(generation: Generation) -> None:
    if shutil.which("wmctrl") is None or shutil.which("xprop") is None or shutil.which("xwd") is None:
        return
    raw = Path(generation["paths"]["raw_evidence"])
    owned_pids = {int(item["pid"]) for item in generation["processes"] if item["kind"] == "wine"}
    result = run_command(["wmctrl", "-lpGx"], check=False, env=wine_environment(generation))
    for line in result.stdout.decode(errors="replace").splitlines():
        fields = line.split(None, 8)
        if len(fields) < 3 or not fields[2].isdigit() or int(fields[2]) not in owned_pids:
            continue
        window_id = fields[0]
        metadata = run_command(
            ["xprop", "-id", window_id, "_NET_WM_PID", "WM_CLASS", "WM_NAME"],
            check=False, env=wine_environment(generation),
        )
        (raw / "window.xprop").write_bytes(metadata.stdout + metadata.stderr)
        run_command(
            ["xwd", "-silent", "-id", window_id, "-out", str(raw / "window.xwd")],
            check=False, env=wine_environment(generation), timeout=30,
        )
        return


def owned_window_evidence(generation: Generation) -> bool:
    raw = Path(generation["paths"]["raw_evidence"])
    return all((raw / name).is_file() and (raw / name).stat().st_size > 0 for name in ("window.xprop", "window.xwd"))


def client_login_points(x: int, y: int, width: int, height: int) -> tuple[tuple[int, int], ...]:
    return (
        (x + round(width * 0.506), y + round(height * 0.536)),
        (x + round(width * 0.506), y + round(height * 0.623)),
        (x + round(width * 0.506), y + round(height * 0.752)),
    )


# X11 keysym names for punctuation used in Lua string literals typed via "/script" (e.g. the
# SetBinding("N","TOGGLERUN") call in WALK_RUN_BIND_SETUP_KEYS); "," has no keysym named "," so
# string_to_keysym(",") silently fails to resolve and the keystroke is dropped without error.
UNSHIFTED_PUNCTUATION_KEYSYM_NAMES = {",": "comma"}


def x_keysym_name(value: str) -> str:
    if value in UNSHIFTED_PUNCTUATION_KEYSYM_NAMES:
        return UNSHIFTED_PUNCTUATION_KEYSYM_NAMES[value]
    return value.lower() if len(value) == 1 and value.isalpha() else value


def owned_wow_window(output: str, owned_pids: set[int]) -> tuple[str, int, int, int, int] | None:
    for line in output.splitlines():
        fields = line.split(None, 9)
        if (
            len(fields) == 10 and fields[2].isdigit() and int(fields[2]) in owned_pids
            and fields[9] == "World of Warcraft"
        ):
            return fields[0], *(int(value) for value in fields[3:7])
    return None


def focus_owned_window(generation: Generation, timeout: float = 30) -> tuple[str, int, int, int, int]:
    environment = wine_environment(generation)
    deadline = time.monotonic() + timeout
    focused_since: float | None = None
    previous_window: str | None = None
    active_id: int | None = None
    while time.monotonic() < deadline:
        processes = find_wine_processes(Path(generation["paths"]["wine_prefix"]))
        add_processes(generation, processes)
        owned_pids = {int(item["pid"]) for item in processes}
        output = run_command(
            ["wmctrl", "-lpGx"], check=False, env=environment,
        ).stdout.decode(errors="replace")
        window = owned_wow_window(output, owned_pids)
        active = run_command(["xprop", "-root", "_NET_ACTIVE_WINDOW"], check=False, env=environment)
        match = re.search(rb"0x[0-9a-fA-F]+", active.stdout)
        active_id = int(match.group(), 16) if match else None
        window_id = window[0] if window else None
        if window and active_id == int(window_id, 16):
            if focused_since is None or previous_window != window_id:
                focused_since = time.monotonic()
            elif time.monotonic() - focused_since >= 0.5:
                # WM frame coordinates can include decorations; clicks need the actual client origin.
                geometry = run_command(
                    ["xwininfo", "-id", window_id], env=environment,
                ).stdout.decode(errors="replace")
                coordinates = []
                for label in ("Absolute upper-left X", "Absolute upper-left Y", "Width", "Height"):
                    value = re.search(rf"^\s*{label}:\s*(-?\d+)\s*$", geometry, re.MULTILINE)
                    if value is None:
                        raise RuntimeError(f"owned WoW client geometry missing {label}")
                    coordinates.append(int(value.group(1)))
                if coordinates[2] <= 0 or coordinates[3] <= 0:
                    raise RuntimeError("owned WoW client geometry has no usable area")
                return (window_id, *coordinates)
        else:
            focused_since = None
            if window:
                # Ask the window manager; forcing XSetInputFocus can reset the client's render window.
                run_command(["wmctrl", "-i", "-a", window_id], check=False, env=environment)
        previous_window = window_id
        time.sleep(0.25)
    raise RuntimeError(
        f"owned WoW window did not receive stable focus within {timeout}s "
        f"(window={previous_window}, active={active_id})"
    )


def capture_desktop_screenshot(generation: Generation, name: str) -> None:
    if shutil.which("gnome-screenshot") is None:
        return

    window_id, *_ = focus_owned_window(generation)
    raw = Path(generation["paths"]["raw_evidence"])
    result = run_command(
        ["gnome-screenshot", "-w", "-f", str(raw / name)],
        check=False, env=wine_environment(generation), timeout=30,
    )
    if result.returncode:
        raise RuntimeError(f"failed to capture {name}: {result.stderr.decode(errors='replace').strip()}")
    active = run_command(
        ["xprop", "-root", "_NET_ACTIVE_WINDOW"], check=False, env=wine_environment(generation),
    )
    match = re.search(r"0x[0-9a-fA-F]+", active.stdout.decode(errors="replace"))
    if not match or int(match.group(), 16) != int(window_id, 16):
        (raw / name).unlink(missing_ok=True)
        print(f"discarded {name}: owned window lost focus during capture", flush=True)


def automate_client_login(generation: Generation) -> None:
    try:
        from Xlib import X, XK, display
        from Xlib.ext import xtest
    except ImportError as error:
        raise RuntimeError("--auto-login requires the installed python3-xlib package") from error

    window_id, x, y, width, height = focus_owned_window(generation, timeout=90)

    connection = display.Display(str(generation["inputs"]["display"]))
    shift = connection.keysym_to_keycode(XK.string_to_keysym("Shift_L"))
    control = connection.keysym_to_keycode(XK.string_to_keysym("Control_L"))

    def require_focus() -> None:
        active = connection.screen().root.get_full_property(
            connection.intern_atom("_NET_ACTIVE_WINDOW"), X.AnyPropertyType,
        )
        if active is None or not len(active.value) or int(active.value[0]) != int(window_id, 16):
            raise RuntimeError("owned WoW window lost focus before input")

    def press(value: str, modifier: int | None = None) -> None:
        require_focus()
        symbol = XK.string_to_keysym(x_keysym_name(value))
        keycode = connection.keysym_to_keycode(symbol)
        if modifier:
            xtest.fake_input(connection, X.KeyPress, modifier)
        xtest.fake_input(connection, X.KeyPress, keycode)
        xtest.fake_input(connection, X.KeyRelease, keycode)
        if modifier:
            xtest.fake_input(connection, X.KeyRelease, modifier)

    def click(point: tuple[int, int]) -> None:
        require_focus()
        connection.screen().root.warp_pointer(*point)
        xtest.fake_input(connection, X.ButtonPress, 1)
        xtest.fake_input(connection, X.ButtonRelease, 1)
        connection.sync()
        time.sleep(0.2)

    def enter(value: str) -> None:
        for character in value:
            press(character, shift if character.isalpha() else None)
            time.sleep(0.05)

    movie_seen = False
    no_movie_deadline = time.monotonic() + 3
    movie_deadline = time.monotonic() + 240
    while time.monotonic() < movie_deadline:
        movie_active = any(
            any("MovieProxy.exe" in argument for argument in item.get("cmdline", []))
            for item in find_wine_processes(Path(generation["paths"]["wine_prefix"]))
        )
        if movie_active:
            movie_seen = True
            window_id, x, y, width, height = focus_owned_window(generation)
            press("Escape")
            connection.sync()
            time.sleep(1)
            continue
        if movie_seen or time.monotonic() >= no_movie_deadline:
            break
        time.sleep(1)
    window_id, x, y, width, height = focus_owned_window(generation)
    account_point, _, _ = client_login_points(x, y, width, height)
    click(account_point)
    press("a", control)
    press("BackSpace")
    enter(ACCOUNT)
    press("Tab")
    press("a", control)
    press("BackSpace")
    enter(PASSWORD)
    press("Return")
    connection.sync()
    connection.close()


def automate_character_selection(generation: Generation) -> None:
    try:
        from Xlib import X, XK, display
        from Xlib.ext import xtest
    except ImportError as error:
        raise RuntimeError("character selection requires the installed python3-xlib package") from error

    connection = display.Display(str(generation["inputs"]["display"]))
    focus_owned_window(generation)
    keycode = connection.keysym_to_keycode(XK.string_to_keysym("Return"))
    xtest.fake_input(connection, X.KeyPress, keycode)
    xtest.fake_input(connection, X.KeyRelease, keycode)
    connection.sync()
    connection.close()


# Default 4.3.4 keybinds for the six directional actions ground movement routing covers,
# paired with the key that ends that action (release-to-stop for forward/backward/strafe,
# a turn key's own release also emits MSG_MOVE_STOP_TURN in this default binding scheme).
GROUND_MOVEMENT_KEYS = ("w", "s", "q", "e", "a", "d")
# Default 4.3.4 keybind for jumping; a single press-release in place emits MSG_MOVE_JUMP, and the
# client sends MSG_MOVE_FALL_LAND on its own once the character lands.
JUMP_FALL_LAND_KEYS = ("space",)
# Default 4.3.4 keybind for action bar button 1 (index 0), matching BASIC_SPELL_CAST_ACTION_BUTTON.
BASIC_SPELL_CAST_KEYS = ("1",)


# string_to_keysym has no notion of a shifted character (typing "T" presses the same physical
# key as "t"), and it does not resolve bare punctuation like "(" to a keysym at all - it needs
# the X11 keysym name instead. "(" and ")" live on the shifted level of the "9"/"0" keys (US
# layout), so this maps to the *digit* keysym (not "parenleft"/"parenright") and forces Shift -
# keysym_to_keycode("parenleft") resolves to a spare/extra keycode that also carries that symbol
# unshifted (observed keycode 187 on this host), which wine's virtual keyboard driver silently
# drops instead of producing "(". Pressing the real "9" key with Shift held is what a physical
# keyboard actually does and is what wine recognizes. All of this is needed to type a literal
# Lua call such as SetBinding("N","TOGGLERUN") via the chat edit box (see WALK_RUN_BIND_SETUP_KEYS).
# '"' lives on the shifted level of the apostrophe key, so it maps to "apostrophe" with Shift held.
SHIFTED_KEYSYM_NAMES = {"(": "9", ")": "0", '"': "apostrophe"}


def automate_key_sequence(
    generation: Generation, keys: tuple[str, ...], hold_seconds: float = 0.6, gap_seconds: float = 0.4,
) -> None:
    try:
        from Xlib import X, XK, display
        from Xlib.ext import xtest
    except ImportError as error:
        raise RuntimeError("key automation requires the installed python3-xlib package") from error

    window_id, *_ = focus_owned_window(generation)
    connection = display.Display(str(generation["inputs"]["display"]))
    shift = connection.keysym_to_keycode(XK.string_to_keysym("Shift_L"))

    def require_focus() -> None:
        active = connection.screen().root.get_full_property(
            connection.intern_atom("_NET_ACTIVE_WINDOW"), X.AnyPropertyType,
        )
        if active is None or not len(active.value) or int(active.value[0]) != int(window_id, 16):
            raise RuntimeError("owned WoW window lost focus before input")

    for key in keys:
        require_focus()
        needs_shift = (len(key) == 1 and key.isalpha() and key.isupper()) or key in SHIFTED_KEYSYM_NAMES
        symbol = XK.string_to_keysym(SHIFTED_KEYSYM_NAMES.get(key, x_keysym_name(key)))
        keycode = connection.keysym_to_keycode(symbol)
        if needs_shift:
            xtest.fake_input(connection, X.KeyPress, shift)
        xtest.fake_input(connection, X.KeyPress, keycode)
        connection.sync()
        time.sleep(hold_seconds)
        xtest.fake_input(connection, X.KeyRelease, keycode)
        if needs_shift:
            xtest.fake_input(connection, X.KeyRelease, shift)
        connection.sync()
        time.sleep(gap_seconds)
    connection.close()


def automate_ground_movement(generation: Generation) -> None:
    automate_key_sequence(generation, GROUND_MOVEMENT_KEYS)


def automate_plan32_geometry(generation: Generation) -> None:
    # One walk tap (height and walk evidence at the seed), then name-target the seeded fixture and
    # two ordinary, uncancelled Fireball casts. No Escape: Codex's confirmation requires both
    # attempts to run through their observation windows so a failure cannot be a client-side cancel.
    capture_desktop_screenshot(generation, "desktop-before-target.png")
    automate_key_sequence(generation, ("w",), hold_seconds=0.6, gap_seconds=0.4)
    automate_key_sequence(
        generation, PLAN32_GEOMETRY_TARGET_KEYS, hold_seconds=0.05, gap_seconds=0.05,
    )
    time.sleep(1.0)  # let the client resolve the name and register the target
    capture_desktop_screenshot(generation, "desktop-after-target.png")
    automate_key_sequence(generation, ("1",), hold_seconds=0.05, gap_seconds=0.05)
    time.sleep(6.0)  # let the first cast resolve (Fireball has a real cast time)
    capture_desktop_screenshot(generation, "desktop-after-first-cast.png")
    automate_key_sequence(generation, ("1",), hold_seconds=0.05, gap_seconds=0.05)
    time.sleep(6.0)
    capture_desktop_screenshot(generation, "desktop-after-second-cast.png")


def automate_plan32_chase(generation: Generation) -> None:
    # One aggro cast at the fixture, then a two-tap right turn around the courtyard wall: north
    # 6 m, then east 6 m along the seeded cardinal facing (pi/2), so each tap is an exact axis
    # move. The fixture (1.2 m west of that wall) cannot follow straight and must walk the
    # pinned route around it. No Escape anywhere.
    #
    # The macro runs until the server logs the selection it produces: this automation types before
    # its first movement, so the initial keypresses can be lost to the window-focus handover (the
    # walk-first LOS stages never see this, their walk tap absorbs it) and a lost Return/slash
    # turns the macro into an ordinary chat message that selects nothing.
    capture_desktop_screenshot(generation, "desktop-before-target.png")
    selections = plan32_selection_count(generation)
    for _ in range(3):
        automate_key_sequence(
            generation, PLAN32_GEOMETRY_TARGET_KEYS, hold_seconds=0.05, gap_seconds=0.05,
        )
        time.sleep(1.0)  # let the client resolve the name and register the target
        if plan32_selection_count(generation) > selections:
            break
    else:
        raise RuntimeError("plan32 chase target macro did not select the fixture")
    capture_desktop_screenshot(generation, "desktop-after-target.png")
    automate_key_sequence(generation, ("1",), hold_seconds=0.05, gap_seconds=0.05)
    time.sleep(7.0)  # let the cast resolve; a hit aggros the fixture
    capture_desktop_screenshot(generation, "desktop-after-cast.png")
    time.sleep(8.0)  # let the fixture close to melee
    automate_key_sequence(generation, ("w",), hold_seconds=0.86, gap_seconds=0.4)
    automate_key_sequence(generation, ("e",), hold_seconds=0.86, gap_seconds=0.4)
    capture_desktop_screenshot(generation, "desktop-after-leg.png")
    time.sleep(40.0)  # arrival window for the detour
    capture_desktop_screenshot(generation, "desktop-after-wait.png")


def plan32_tapped_stop(generation: Generation) -> bool:
    """Whether a movement tap has been accepted to its stop since the in-world marker: the live
    signal a walk tap produced, checked because the first keypresses after the client comes up can
    be lost to the window-focus handover (see the chase's macro retry)."""
    return any(
        str(sample["opcode"]) == "MSG_MOVE_STOP" for sample in ground_movement_samples(generation)
    )


def plan32_landing_count(generation: Generation) -> int:
    """How many world-entry landings the client's accepted movement produced: one per session.

    The server's time-sync liveness line is not a per-session signal — it is logged only while a
    session has no clock delta yet, so an already-in-world character's second login does not add
    one — while the client lands once every time it enters the world."""
    return sum(
        1 for sample in ground_movement_samples(generation)
        if str(sample["opcode"]) == "MSG_MOVE_FALL_LAND"
    )


def automate_plan32_relog(generation: Generation) -> None:
    # Walk the pinned route, stop, then drop the session and take it up again: the position the
    # walk stopped at has to be the one the next login arrives at. No cast here — an engaged
    # fixture holds the character in combat, and the client refuses to log out in combat.
    capture_desktop_screenshot(generation, "desktop-before-walk.png")
    for _ in range(3):
        automate_key_sequence(generation, ("w",), hold_seconds=0.6, gap_seconds=0.4)
        time.sleep(1.0)  # let the stop land
        if plan32_tapped_stop(generation):
            break
    else:
        raise RuntimeError("plan32 relog walk did not reach its stop")
    capture_desktop_screenshot(generation, "desktop-after-walk.png")
    deadline = time.monotonic() + 60.0
    completed = len(LOGOUT_COMPLETE_PATTERN.findall(world_log_text(generation)))
    automate_key_sequence(
        generation, ("Return", "slash", *"logout", "Return"), hold_seconds=0.05, gap_seconds=0.05,
    )
    while time.monotonic() < deadline:
        if len(LOGOUT_COMPLETE_PATTERN.findall(world_log_text(generation))) > completed:
            break
        time.sleep(0.2)
    else:
        raise RuntimeError("plan32 relog logout did not complete")
    capture_desktop_screenshot(generation, "desktop-after-logout.png")
    # The character screen's own default action is Enter World; press it until the client has
    # landed in the world again (a second world-entry landing), which is what a session produces.
    landings = plan32_landing_count(generation)
    for _ in range(6):
        automate_character_selection(generation)
        time.sleep(3.0)
        if plan32_landing_count(generation) > landings:
            break
    else:
        raise RuntimeError("plan32 relog login did not complete")
    time.sleep(3.0)  # let the landing sample land
    capture_desktop_screenshot(generation, "desktop-after-relog.png")


def automate_issue97_phases(generation: Generation, deadline: float) -> None:
    # Measure accepted stops; startup input can cover much less distance than later taps.
    stages = []
    phase_packets = len(re.findall(r"S->C:.*\[SMSG_PHASE_SHIFT_CHANGE\b", world_log_text(generation)))
    for key, target, phases in (("w", -9070, [125]), ("s", -9066.3, []), ("w", -9070, [125])):
        stalled = 0
        while time.monotonic() < deadline:
            samples = ground_movement_samples(generation)
            previous = len(samples)
            previous_x = float(samples[-1]["x"]) if samples else CHARACTER_POSITION[0]
            automate_key_sequence(generation, (key,), hold_seconds=0.6, gap_seconds=0.1)
            stop_deadline = min(deadline, time.monotonic() + 3)
            sample = None
            while time.monotonic() < stop_deadline:
                stops = [s for s in ground_movement_samples(generation)[previous:] if s["opcode"] == "MSG_MOVE_STOP"]
                if stops:
                    sample = stops[-1]
                    break
                time.sleep(0.1)
            if sample is None:
                raise RuntimeError("phase walk did not produce an accepted movement stop")
            if abs(float(sample["y"]) - CHARACTER_POSITION[1]) > 3:
                raise RuntimeError("phase walk left the measured boundary corridor")
            stalled = stalled + 1 if abs(float(sample["x"]) - previous_x) < 0.1 else 0
            if stalled >= 2:
                raise RuntimeError("phase walk stalled before reaching its destination")
            if (key == "w" and float(sample["x"]) <= target) or (key == "s" and float(sample["x"]) >= target):
                break
        else:
            raise RuntimeError("phase walk timed out before reaching its destination")
        while time.monotonic() < deadline:
            count = len(re.findall(r"S->C:.*\[SMSG_PHASE_SHIFT_CHANGE\b", world_log_text(generation)))
            if count > phase_packets:
                phase_packets = count
                break
            time.sleep(0.1)
        else:
            raise RuntimeError("phase walk reached its destination without a phase change packet")
        stages.append({"expected_phases": phases, "stop": sample, "phase_packet_count": phase_packets})
        generation["issue97_stages"] = stages
        if len(stages) <= 2:
            automate_phase_relogin(generation, deadline)
            phase_packets = len(re.findall(r"S->C:.*\[SMSG_PHASE_SHIFT_CHANGE\b", world_log_text(generation)))
        automate_key_sequence(
            generation, ("Return", "slash", *'run', "space", *'SetBinding("G","INTERACTTARGET")', "Return"),
            hold_seconds=0.02, gap_seconds=0.02,
        )
        automate_key_sequence(
            generation, ("Return", "slash", *"targetexact", "space", *"Marshal", "space", *"McBride", "Return", "g"),
            hold_seconds=0.05, gap_seconds=0.05,
        )
        time.sleep(0.5)
        capture_desktop_screenshot(generation, f"phase-stage-{len(stages)}.png")
        automate_key_sequence(generation, ("Escape",), hold_seconds=0.05, gap_seconds=0.05)
        automate_phase_chest_click(generation)
        time.sleep(3.5)
        capture_desktop_screenshot(generation, f"phase-chest-{len(stages)}.png")
        automate_key_sequence(generation, ("Escape",), hold_seconds=0.05, gap_seconds=0.05)


def automate_phase_relogin(generation: Generation, deadline: float) -> None:
    before = len(re.findall(r"S->C:.*\[SMSG_LOGOUT_COMPLETE\b", world_log_text(generation)))
    automate_key_sequence(
        generation, ("Return", "slash", *"logout", "Return"), hold_seconds=0.05, gap_seconds=0.05,
    )
    while time.monotonic() < deadline:
        if len(re.findall(r"S->C:.*\[SMSG_LOGOUT_COMPLETE\b", world_log_text(generation))) > before:
            break
        time.sleep(0.2)
    else:
        raise RuntimeError("phase restoration logout did not complete")
    before = len(re.findall(r"S->C:.*\[SMSG_PHASE_SHIFT_CHANGE\b", world_log_text(generation)))
    time.sleep(1)
    automate_key_sequence(generation, ("Return",), hold_seconds=0.05, gap_seconds=0.05)
    while time.monotonic() < deadline:
        if len(re.findall(r"S->C:.*\[SMSG_PHASE_SHIFT_CHANGE\b", world_log_text(generation))) > before:
            time.sleep(1)
            return
        time.sleep(0.2)
    raise RuntimeError("phase restoration login did not send membership")


def click_owned_window(generation: Generation, fx: float, fy: float, button: int = 1) -> None:
    """Click a fraction of the owned window's current geometry, after verifying it still has focus."""
    try:
        from Xlib import X, display
        from Xlib.ext import xtest
    except ImportError as error:
        raise RuntimeError("window clicks require the installed python3-xlib package") from error

    window_id, x, y, width, height = focus_owned_window(generation)
    connection = display.Display(str(generation["inputs"]["display"]))
    try:
        active = connection.screen().root.get_full_property(
            connection.intern_atom("_NET_ACTIVE_WINDOW"), X.AnyPropertyType,
        )
        if active is None or not len(active.value) or int(active.value[0]) != int(window_id, 16):
            raise RuntimeError("owned WoW window lost focus before a click")
        xtest.fake_input(connection, X.MotionNotify, x=x + int(width * fx), y=y + int(height * fy))
        xtest.fake_input(connection, X.ButtonPress, button)
        xtest.fake_input(connection, X.ButtonRelease, button)
        connection.sync()
    finally:
        connection.close()


def automate_phase_chest_click(generation: Generation) -> None:
    # shortcut: click is calibrated for this fixture and camera; recalibrate after changing either.
    click_owned_window(generation, 0.74, 0.40, button=3)


def automate_jump_fall_land(generation: Generation) -> None:
    automate_key_sequence(generation, JUMP_FALL_LAND_KEYS)


# ToggleRun() has no default keybind in 4.3.4. Confirmed via a live "/script print(...)" probe
# against the real client that the walk/run toggle function is "ToggleRun" - "ToggleWalking" (the
# more commonly assumed name) is nil in this build. Calling it directly via "/script ToggleRun()"
# is rejected by the client with "A macro script has been blocked from an action only available to
# the Blizzard UI" - it is a protected action, not just untainted Lua, so no amount of scripting
# can invoke it. Instead SetBinding() (an ordinary, unprotected config setter used by every keybind
# addon) binds it to a real key once via "/script", the same client-local, no-RBAC-needed trick
# used for "/target <name>" in INVALID_TARGET_SPELL_CAST_KEYS; the harness then presses that real
# key (WALK_RUN_TOGGLE_KEYS) to trigger the toggle through the normal secure keybind path, exactly
# like a real player would.
WALK_RUN_BIND_KEY = "N"
WALK_RUN_BIND_SETUP_KEYS = (
    "Return", "slash", *tuple("script"), "space",
    *tuple(f'SetBinding("{WALK_RUN_BIND_KEY}","TOGGLERUN")SaveBindings(GetCurrentBindingSet())'),
    "Return",
)
WALK_RUN_TOGGLE_KEYS = (WALK_RUN_BIND_KEY.lower(),)
WALK_RUN_FORWARD_KEYS = ("w",)
# Same hold duration for both legs so the two distances travelled are directly comparable;
# real Cata run speed (7 yd/s) is close to 3x walk speed (2.5 yd/s), so 1s holds produce a
# clearly distinguishable delta even with automation timing jitter.
WALK_RUN_MODE_SWITCH_HOLD_SECONDS = 1.0


# Wider hold/gap than other chat sequences: this is the first sequence to hold Shift for
# uppercase letters and shifted punctuation, and wine's keyboard message pump needs more time
# than a native X client to latch the Shift modifier state before the paired keycode arrives.
WALK_RUN_TOGGLE_HOLD_SECONDS = 0.15
WALK_RUN_TOGGLE_GAP_SECONDS = 0.15


def automate_walk_run_mode_switch(generation: Generation) -> None:
    automate_key_sequence(
        generation, WALK_RUN_BIND_SETUP_KEYS,
        hold_seconds=WALK_RUN_TOGGLE_HOLD_SECONDS, gap_seconds=WALK_RUN_TOGGLE_GAP_SECONDS,
    )
    time.sleep(0.5)  # let the client apply and save the new keybind before using it
    capture_desktop_screenshot(generation, "desktop-before-walk-toggle.png")
    automate_key_sequence(
        generation, WALK_RUN_TOGGLE_KEYS,
        hold_seconds=WALK_RUN_TOGGLE_HOLD_SECONDS, gap_seconds=WALK_RUN_TOGGLE_GAP_SECONDS,
    )
    time.sleep(0.5)  # let the client apply the walk-mode toggle before moving
    capture_desktop_screenshot(generation, "desktop-after-walk-toggle.png")
    automate_key_sequence(
        generation, WALK_RUN_FORWARD_KEYS, hold_seconds=WALK_RUN_MODE_SWITCH_HOLD_SECONDS, gap_seconds=0.4,
    )
    capture_desktop_screenshot(generation, "desktop-after-walk-forward.png")
    automate_key_sequence(
        generation, WALK_RUN_TOGGLE_KEYS,
        hold_seconds=WALK_RUN_TOGGLE_HOLD_SECONDS, gap_seconds=WALK_RUN_TOGGLE_GAP_SECONDS,
    )
    time.sleep(0.5)  # let the client apply the run-mode toggle before moving
    capture_desktop_screenshot(generation, "desktop-after-run-toggle.png")
    automate_key_sequence(
        generation, WALK_RUN_FORWARD_KEYS, hold_seconds=WALK_RUN_MODE_SWITCH_HOLD_SECONDS, gap_seconds=0.4,
    )
    capture_desktop_screenshot(generation, "desktop-after-run-forward.png")


def automate_spell_cast(generation: Generation) -> None:
    automate_key_sequence(generation, BASIC_SPELL_CAST_KEYS)


# Default 4.3.4 keybind "Enter" opens the chat edit box defaulted to /say; a second "Enter" sends it.
CHAT_MESSAGE_KEYS = ("Return", *tuple(CHAT_MESSAGE_TEXT), "Return")


def automate_chat_message(generation: Generation) -> None:
    capture_desktop_screenshot(generation, "desktop-before-chat.png")
    automate_key_sequence(generation, CHAT_MESSAGE_KEYS, hold_seconds=0.05, gap_seconds=0.05)
    time.sleep(1.0)  # let the server echo (or reject) the message before capturing
    capture_desktop_screenshot(generation, "desktop-after-chat.png")


# Default 4.3.4 keybinds for "target nearest enemy", action bar button 1, and cancelling a cast.
# Action keys use short taps below so Escape reaches the client while Fireball is still preparing.
TARGET_TARGETED_SPELL_CAST_TARGET_KEY = "Tab"
TARGET_TARGETED_SPELL_CAST_CAST_KEYS = ("1",)
TARGET_TARGETED_SPELL_CAST_CANCEL_KEYS = ("Escape",)
TARGET_TARGETED_SPELL_CAST_COMPLETE_DELAY_SECONDS = 5.0


def automate_target_targeted_spell_cast(generation: Generation) -> None:
    capture_desktop_screenshot(generation, "desktop-before-target.png")
    automate_key_sequence(generation, (TARGET_TARGETED_SPELL_CAST_TARGET_KEY,))
    time.sleep(1.0)  # let the client register the new target before casting
    capture_desktop_screenshot(generation, "desktop-after-target.png")
    # Short taps deliver Escape while the first Fireball is still preparing.
    automate_key_sequence(
        generation, TARGET_TARGETED_SPELL_CAST_CAST_KEYS + TARGET_TARGETED_SPELL_CAST_CANCEL_KEYS,
        hold_seconds=0.05, gap_seconds=0.05,
    )
    capture_desktop_screenshot(generation, "desktop-after-cancel-input.png")
    automate_key_sequence(
        generation, TARGET_TARGETED_SPELL_CAST_CAST_KEYS, hold_seconds=0.05, gap_seconds=0.05,
    )
    capture_desktop_screenshot(generation, "desktop-after-recast-input.png")
    time.sleep(TARGET_TARGETED_SPELL_CAST_COMPLETE_DELAY_SECONDS)  # let the second cast complete


LEVEL_UP_ATTACK_KEYS = ("1",)  # main-hand attack (spell 78 "Attack") on action bar button 0
# Auto-attack only has to be STARTED: once it begins, the server swings on the weapon timer until
# the target dies or the player stops. Hammering the key 40 times added 40 focus-dependent input
# events for no benefit (one run landed 16 damage events, the next none at all). Start it, then
# confirm from the server log that the attack actually began and retry if it did not, rather than
# guessing with a fixed sleep.
LEVEL_UP_FIGHT_SECONDS = 45.0
# One level-1 creature grants ~100 XP against a 400 XP threshold, so several kills are needed.
LEVEL_UP_XP_TARGET_COUNT = 6
LEVEL_UP_MAX_KILLS = LEVEL_UP_XP_TARGET_COUNT
# Tab alone is not enough: it selects the nearest unit, and in the fixture's cell that is the
# friendly Deputy Willem (faction 12, ~2.3yd) rather than the seeded Boar (faction 14, ~4.2yd).
# Attacking a friendly target sends no CMSG_ATTACK, so the first run produced no attacks at all.
# The client-local "/target <name>" macro resolves against the client's own object cache (the same
# mechanism INVALID_TARGET_SPELL_CAST_MODE already uses), needs no GM rights, and still sends a
# genuine CMSG_SET_SELECTION.
LEVEL_UP_TARGET_KEYS = (
    "Return", "slash", *tuple("target"), "space", *tuple(LEVEL_UP_TARGET_NAME.lower()), "Return",
)


def automate_level_up(generation: Generation) -> None:
    """Kills the seeded hostiles one after another until the XP award crosses the level threshold.

    XP comes from the real KillRewarder -> Player::GiveXP path. One level-1 creature grants far less
    than the 400 XP required (a measured run granted 100), so each round selects the next creature,
    starts auto-attack once, and waits for that kill to grant XP before moving on.
    """
    capture_desktop_screenshot(generation, "desktop-before-level-up.png")
    for _round in range(LEVEL_UP_MAX_KILLS):
        if level_up_marker_count(generation) > 0:
            break
        if not level_up_client_alive():
            break
        automate_key_sequence(generation, LEVEL_UP_TARGET_KEYS, hold_seconds=0.05, gap_seconds=0.05)
        time.sleep(1.0)  # let the client resolve the name and register the target
        xp_before = level_up_xp_gain_count(generation)
        # Press the attack button ONCE. It is a toggle: a second press stops the attack, which is
        # what produced the earlier "ATTACKSTART then ATTACKSTOP exactly one retry-interval later"
        # pattern. Let the swing timer do the work and watch for the kill instead of re-pressing.
        automate_key_sequence(generation, LEVEL_UP_ATTACK_KEYS, hold_seconds=0.05, gap_seconds=0.05)
        deadline = time.monotonic() + LEVEL_UP_FIGHT_SECONDS
        while time.monotonic() < deadline:
            if level_up_xp_gain_count(generation) > xp_before:
                break  # this creature died and granted XP
            if level_up_marker_count(generation) > 0 or not level_up_client_alive():
                break
            time.sleep(2.0)
    capture_desktop_screenshot(generation, "desktop-after-level-up-attacks.png")
    generation["level_up_automation_done"] = True


# "/target <name>" (lowercase; the client's name lookup is case-insensitive) self-targets via the
# chat edit box, then action bar button 1 casts the enemy-only Fireball at that (friendly) target.
INVALID_TARGET_SPELL_CAST_KEYS = (
    "Return", "slash", *tuple("target"), "space", *tuple(CHARACTER_NAME.lower()), "Return",
)
INVALID_TARGET_SPELL_CAST_CAST_KEYS = ("1",)


def automate_invalid_target_spell_cast(generation: Generation) -> None:
    capture_desktop_screenshot(generation, "desktop-before-invalid-target.png")
    automate_key_sequence(generation, INVALID_TARGET_SPELL_CAST_KEYS, hold_seconds=0.05, gap_seconds=0.05)
    time.sleep(1.0)  # let the client resolve the self-target before casting
    capture_desktop_screenshot(generation, "desktop-after-invalid-target.png")
    automate_key_sequence(generation, INVALID_TARGET_SPELL_CAST_CAST_KEYS, hold_seconds=0.05, gap_seconds=0.05)
    time.sleep(2.0)  # let SMSG_CAST_FAILED arrive
    capture_desktop_screenshot(generation, "desktop-after-invalid-target-cast.png")


CREATION_CLASSES = {"warrior": ("class_warrior", 1), "mage": ("class_mage", 8)}
# Plan 35: a natively created level-1 Human Mage casts Fireball (133) from action button 0 at a pinned
# Saytr (361: hostile level 1 with no static spawns anywhere, so /targetexact resolves only ours).
# Creature guids must stay below 0xFFFFFF: the spawn-id allocator starts at MAX(guid) + 1.
MAGE_COMBAT_SPELL_ID = 133
MAGE_COMBAT_ACTION_BUTTON = 0
MAGE_COMBAT_TARGET_GUID = 9990003
MAGE_COMBAT_TARGET_ENTRY = 361
# Four yards from the mage start on the side the cast accepts: a seed on the far side of the mage was
# refused with SPELL_FAILED_UNIT_NOT_INFRONT (136). The arc test also admits targets whose radius
# reaches the boundary, so this is evidence of acceptance, not of the exact facing axis.
MAGE_COMBAT_TARGET_POSITION = (-8949.95, -128.5, 83.5312)
MAGE_COMBAT_TARGET_KEYS = (
    "Return", "slash", *tuple("targetexact"), "space", *vendor_trade_name_keys("Saytr"), "Return",
)
MAGE_COMBAT_PROBE_TIMEOUT = 150
# Plan 35 follow-up: a neutral, high-health dummy (entry 24792, faction 7) with no spawns near Northshire.
# It never fights back, so the mage leaves combat, regenerates mana and can log out normally.
MAGE_DUMMY_ENTRY = 24792
MAGE_DUMMY_GUID = 9990005
MAGE_DUMMY_NAME = "Advanced Training Dummy"
MAGE_DUMMY_POSITION = (-8949.95, -128.5, 83.5312)
MAGE_FOLLOWUP_KEYS = (
    "Return", "slash", *tuple("targetexact"), "space", *vendor_trade_name_keys(MAGE_DUMMY_NAME), "Return",
)
MAGE_FOLLOWUP_REGEN_TIMEOUT = 45
MAGE_FOLLOWUP_LOGOUT_TIMEOUT = 30
MAGE_FOLLOWUP_RELOGIN_TIMEOUT = 150
CREATION_CLASS_POINT = "class_warrior"


def creation_class_name() -> str:
    """The class the environment asks the native creation flow to pick (default warrior)."""
    return os.environ.get("CREATE_CLASS", "warrior")


def apply_creation_class(mode: str, name: str) -> None:
    """Select the class the native creation flow clicks and the class the verification expects."""
    global CREATION_CLASS_POINT, CHARACTER_CLASS
    if mode not in {CHARACTER_CREATION_MODE, *MAGE_MODES}:
        return
    if mode in MAGE_MODES:
        name = "mage"
    if name not in CREATION_CLASSES:
        raise RuntimeError(f"CREATE_CLASS must be one of {sorted(CREATION_CLASSES)}; got {name!r}")
    CREATION_CLASS_POINT, CHARACTER_CLASS = CREATION_CLASSES[name]


def mage_combat_world_seed_sql() -> str:
    """Spawns the pinned Saytr beside the mage's start position."""
    x, y, z = MAGE_COMBAT_TARGET_POSITION
    return (
        f"DELETE FROM `creature` WHERE `guid`={MAGE_COMBAT_TARGET_GUID};"
        "INSERT INTO `creature` (`guid`,`id`,`map`,`zoneId`,`areaId`,`position_x`,`position_y`,`position_z`,"
        "`wander_distance`,`MovementType`) "
        f"VALUES ({MAGE_COMBAT_TARGET_GUID},{MAGE_COMBAT_TARGET_ENTRY},0,12,9,{x},{y},{z},0,0);"
    )


def mage_combat_character_reset(manifest: Manifest, generation: Generation) -> None:
    """Deletes the fixture account's characters and their `character_*` rows.

    Character tables do not cascade and the 4.3.4 character list ignores `deleteDate`, so a retry has to
    remove the previous mage's dependent rows itself; otherwise a reused guid inherits them, including the
    saved action the acceptance reads.
    """
    schema = generation["schemas"]["characters"]
    guids = mysql(
        manifest, generation,
        f"SELECT GROUP_CONCAT(`guid`) FROM `characters` WHERE `account`={ACCOUNT_ID};", schema,
    ).strip()
    if not guids:
        return
    tables = mysql(
        manifest, generation,
        "SELECT `TABLE_NAME` FROM `information_schema`.`COLUMNS` WHERE `TABLE_SCHEMA`=DATABASE() "
        "AND `COLUMN_NAME`='guid' AND `TABLE_NAME` LIKE 'character\\_%';", schema,
    ).split()
    statements = [f"DELETE FROM `{table}` WHERE `guid` IN ({guids});" for table in tables]
    statements.append(f"DELETE FROM `characters` WHERE `account`={ACCOUNT_ID};")
    mysql(manifest, generation, "".join(statements), schema)


def mage_combat_db_evidence(manifest: Manifest, generation: Generation) -> dict[str, int]:
    """The created character's class/race and its action-bar slot, read by name and account."""
    schema = generation["schemas"]["characters"]

    def scalar(sql: str) -> int:
        output = mysql(manifest, generation, sql, schema)
        try:
            return int(output)
        except ValueError as error:
            raise RuntimeError(f"owned character database returned a non-numeric value: {output!r}") from error

    guid = scalar(
        f"SELECT `guid` FROM `characters` WHERE `account`={ACCOUNT_ID} AND `name`='{CHARACTER_NAME}' "
        "AND `deleteDate` IS NULL;")
    return {
        "guid": guid,
        "class": scalar(f"SELECT `class` FROM `characters` WHERE `guid`={guid};"),
        "race": scalar(f"SELECT `race` FROM `characters` WHERE `guid`={guid};"),
        "action_count": scalar(
            f"SELECT COUNT(*) FROM `character_action` WHERE `guid`={guid} "
            f"AND `button`={MAGE_COMBAT_ACTION_BUTTON} AND `action`={MAGE_COMBAT_SPELL_ID} AND `type`=0;"),
        "spell_count": scalar(
            f"SELECT COUNT(*) FROM `character_spell` WHERE `guid`={guid} "
            f"AND `spell`={MAGE_COMBAT_SPELL_ID};"),
    }


def automate_mage_combat(generation: Generation) -> None:
    """Cast Fireball at the seeded Saytr from the action bar."""
    time.sleep(4.0)
    vendor_trade_probe_input(generation, MAGE_COMBAT_PROBE_TIMEOUT)
    selections = vendor_trade_marker_count(generation, "c2s", "CMSG_SET_SELECTION")
    for _ in range(5):
        automate_key_sequence(generation, MAGE_COMBAT_TARGET_KEYS, hold_seconds=0.05, gap_seconds=0.05)
        time.sleep(1.2)  # let the client resolve the name and register the target
        if vendor_trade_marker_count(generation, "c2s", "CMSG_SET_SELECTION") > selections:
            break
    else:
        capture_desktop_screenshot(generation, "mage-after-target.png")
        raise RuntimeError("mage target macro did not select the Saytr")
    capture_desktop_screenshot(generation, "mage-target.png")

    # Both baselines precede the keypress: the responses can arrive before the helper returns.
    casts = vendor_trade_marker_count(generation, "c2s", "CMSG_CAST_SPELL") + 1
    goes = vendor_trade_marker_count(generation, "s2c", "SMSG_SPELL_GO") + 1
    automate_key_sequence(generation, ("1",), hold_seconds=0.05, gap_seconds=0.05)
    vendor_trade_wait(generation, "c2s", "CMSG_CAST_SPELL", "cast request", casts)
    vendor_trade_wait(generation, "s2c", "SMSG_SPELL_GO", "spell go", goes)
    # The Saytr keeps the mage in combat, which refuses /logout; the harness stops the client and the
    # action bar was saved when the character was created.
    time.sleep(1.0)  # let the damage log and power update land
    capture_desktop_screenshot(generation, "mage-after-cast.png")


def mage_followup_world_seed_sql() -> str:
    """Spawns the neutral training dummy beside the mage start."""
    x, y, z = MAGE_DUMMY_POSITION
    return (
        f"DELETE FROM `creature` WHERE `guid`={MAGE_DUMMY_GUID};"
        "INSERT INTO `creature` (`guid`,`id`,`map`,`zoneId`,`areaId`,`position_x`,`position_y`,`position_z`,"
        "`wander_distance`,`MovementType`) "
        f"VALUES ({MAGE_DUMMY_GUID},{MAGE_DUMMY_ENTRY},0,12,9,{x},{y},{z},0,0);"
    )


def mage_followup_select(generation: Generation, description: str) -> None:
    """Target the dummy by name and wait for the client's selection request."""
    selections = vendor_trade_marker_count(generation, "c2s", "CMSG_SET_SELECTION")
    for _ in range(5):
        automate_key_sequence(generation, MAGE_FOLLOWUP_KEYS, hold_seconds=0.05, gap_seconds=0.05)
        time.sleep(1.2)
        if vendor_trade_marker_count(generation, "c2s", "CMSG_SET_SELECTION") > selections:
            return
    capture_desktop_screenshot(generation, f"mage-followup-{description}-target.png")
    raise RuntimeError(f"mage follow-up could not select the dummy for {description}")


def mage_followup_mana(generation: Generation) -> list[int]:
    """The created mage's mana values in arrival order (the reset leaves the new character at guid 1)."""
    return [
        int(power[1]) for event in combat_packet_events(generation)
        if event["opcode"] == "SMSG_POWER_UPDATE" and int(event["guid"]) == 1
        for power in event["powers"] if int(power[0]) == 0
    ]


def mage_followup_wait_mana(generation: Generation, accept, description: str, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        values = mage_followup_mana(generation)
        if values and accept(values):
            return
        time.sleep(0.5)
    capture_desktop_screenshot(generation, "mage-followup-mana.png")
    raise RuntimeError(f"mage follow-up did not observe {description}")


def automate_mage_followup(generation: Generation) -> None:
    """Cast, let mana regenerate out of combat, log out, log back in and cast again."""
    time.sleep(4.0)
    vendor_trade_probe_input(generation, MAGE_COMBAT_PROBE_TIMEOUT)
    mage_followup_select(generation, "first")

    casts = vendor_trade_marker_count(generation, "c2s", "CMSG_CAST_SPELL") + 1
    goes = vendor_trade_marker_count(generation, "s2c", "SMSG_SPELL_GO") + 1
    automate_key_sequence(generation, ("1",), hold_seconds=0.05, gap_seconds=0.05)
    vendor_trade_wait(generation, "c2s", "CMSG_CAST_SPELL", "first cast request", casts)
    vendor_trade_wait(generation, "s2c", "SMSG_SPELL_GO", "first spell go", goes)
    capture_desktop_screenshot(generation, "mage-followup-cast.png")

    mage_followup_wait_mana(
        generation, lambda values: len(values) >= 2 and values[-1] > values[-2],
        "mana regeneration after the cast", MAGE_FOLLOWUP_REGEN_TIMEOUT,
    )
    capture_desktop_screenshot(generation, "mage-followup-regen.png")

    completed = vendor_trade_marker_count(generation, "s2c", "SMSG_LOGOUT_COMPLETE") + 1
    automate_key_sequence(
        generation, ("Return", "slash", *tuple("logout"), "Return"), hold_seconds=0.05, gap_seconds=0.05)
    vendor_trade_wait(generation, "s2c", "SMSG_LOGOUT_COMPLETE", "logout", completed,
                      MAGE_FOLLOWUP_LOGOUT_TIMEOUT)
    # The character screen needs a moment to rebuild after the logout, and its Enter press is the only
    # way back in; repeat it until the world reports the second login instead of firing once into a
    # screen that is not ready.
    deadline = time.monotonic() + MAGE_FOLLOWUP_RELOGIN_TIMEOUT
    while time.monotonic() < deadline and len(player_login_callbacks(generation)) < 2:
        automate_character_selection(generation)
        time.sleep(5.0)
    if len(player_login_callbacks(generation)) < 2:
        capture_desktop_screenshot(generation, "mage-followup-relogin.png")
        raise RuntimeError("the client did not re-enter the world after the logout")
    vendor_trade_probe_input(generation, MAGE_COMBAT_PROBE_TIMEOUT)
    mage_followup_select(generation, "second")

    casts = vendor_trade_marker_count(generation, "c2s", "CMSG_CAST_SPELL") + 1
    goes = vendor_trade_marker_count(generation, "s2c", "SMSG_SPELL_GO") + 1
    automate_key_sequence(generation, ("1",), hold_seconds=0.05, gap_seconds=0.05)
    vendor_trade_wait(generation, "c2s", "CMSG_CAST_SPELL", "second cast request", casts)
    vendor_trade_wait(generation, "s2c", "SMSG_SPELL_GO", "second spell go", goes)
    time.sleep(1.0)
    capture_desktop_screenshot(generation, "mage-followup-relog-cast.png")


def character_creation_points(x: int, y: int, width: int, height: int) -> dict[str, tuple[int, int]]:
    # Fractions calibrated against a 1792x1008 owned client window on the Cataclysm 15595 creation
    # screen: Alliance/Human is the first race portrait, Warrior the first class icon (both
    # already match CHARACTER_RACE/CHARACTER_CLASS), male is the left gender icon, matching the
    # gender=0 used by the populated-mode SQL fixtures.
    return {
        "race_human": (x + round(width * 0.0636), y + round(height * 0.0912)),
        "gender_male": (x + round(width * 0.0854), y + round(height * 0.5566)),
        "class_warrior": (x + round(width * 0.0346), y + round(height * 0.6449)),
        # class buttons are anchored as two rows of five (Warrior..Priest, then
        # Death Knight/Shaman/Mage/Warlock/Druid); a race hides the classes it cannot be, so the Mage
        # (classID 8) sits at row 2, column 3: calibrated from creation-screen.png
        "class_mage": (x + round(width * 0.1006), y + round(height * 0.6929)),
        "name_field": (x + round(width * 0.5000), y + round(height * 0.8974)),
        "accept": (x + round(width * 0.8972), y + round(height * 0.9357)),
    }


def automate_character_creation(generation: Generation) -> None:
    try:
        from Xlib import X, XK, display
        from Xlib.ext import xtest
    except ImportError as error:
        raise RuntimeError("character creation requires the installed python3-xlib package") from error

    connection = display.Display(str(generation["inputs"]["display"]))
    window_id, x, y, width, height = focus_owned_window(generation)
    shift = connection.keysym_to_keycode(XK.string_to_keysym("Shift_L"))

    def require_focus() -> None:
        active = connection.screen().root.get_full_property(
            connection.intern_atom("_NET_ACTIVE_WINDOW"), X.AnyPropertyType,
        )
        if active is None or not len(active.value) or int(active.value[0]) != int(window_id, 16):
            raise RuntimeError("owned WoW window lost focus before input")

    def click(point: tuple[int, int]) -> None:
        require_focus()
        connection.screen().root.warp_pointer(*point)
        xtest.fake_input(connection, X.ButtonPress, 1)
        xtest.fake_input(connection, X.ButtonRelease, 1)
        connection.sync()
        time.sleep(1.2)

    def press(value: str, modifier: int | None = None) -> None:
        require_focus()
        symbol = XK.string_to_keysym(x_keysym_name(value))
        keycode = connection.keysym_to_keycode(symbol)
        if modifier:
            xtest.fake_input(connection, X.KeyPress, modifier)
        xtest.fake_input(connection, X.KeyPress, keycode)
        xtest.fake_input(connection, X.KeyRelease, keycode)
        if modifier:
            xtest.fake_input(connection, X.KeyRelease, modifier)
        # Without an explicit sync, these fake_input requests sit in Xlib's local output
        # buffer until some later round-trip call (e.g. the next require_focus()) flushes
        # them, so the very last keystroke of a name never reaches the X server at all until
        # something else forces a flush -- which was previously the Accept click's own
        # require_focus(), landing right on top of it with no real settle time.
        connection.sync()

    def enter(value: str) -> None:
        for character in value:
            press(character, shift if character.isalpha() else None)
            time.sleep(0.05)

    # The creation screen needs a moment to become interactive after the window gains focus. The capture
    # is the calibration evidence for the race/class/gender click fractions.
    time.sleep(2)
    capture_desktop_screenshot(generation, "creation-screen.png")
    points = character_creation_points(x, y, width, height)
    click(points["race_human"])
    click(points["gender_male"])
    click(points[CREATION_CLASS_POINT])
    time.sleep(0.6)  # let the class panel redraw
    capture_desktop_screenshot(generation, "creation-class.png")
    click(points["name_field"])
    enter(CHARACTER_NAME)
    # Give the client a moment to render the committed name before Accept, matching the
    # settle delay already used between other clicks in this flow (see click() above).
    time.sleep(1.2)
    click(points["accept"])
    connection.sync()
    connection.close()


def vendor_trade_marker_count(generation: Generation, direction: str, opcode: str) -> int:
    """Opcode occurrences after the in-world marker, in the requested direction."""
    text = world_log_text(generation).partition(IN_WORLD_CONTROL_MARKER)[2]
    side = "C->S" if direction == "c2s" else "S->C"
    return len(re.findall(rf"\b{side}:\s+.*?\b{opcode}\b", text))


def vendor_trade_wait(
    generation: Generation, direction: str, opcode: str, description: str, target: int,
    timeout: float = VENDOR_TRADE_TIMEOUT,
) -> None:
    """Wait until at least `target` lines name this opcode in this direction.

    The caller captures `target` *before* sending the input: sampling the count afterwards would
    wait for a second response to an action that already succeeded, and on loopback the response
    usually arrives before the input helper returns.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if vendor_trade_marker_count(generation, direction, opcode) >= target:
            return
        time.sleep(0.2)
    raise RuntimeError(f"the vendor round trip did not reach the {description} ({direction} {opcode})")


def vendor_trade_wait_optional(
    generation: Generation, direction: str, opcode: str, target: int, timeout: float,
) -> bool:
    """Wait for a packet without failing: the caller retries with a different client state."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if vendor_trade_marker_count(generation, direction, opcode) >= target:
            return True
        time.sleep(0.2)
    return False


def vendor_trade_wait_any(generation: Generation, opcodes: tuple[str, ...], description: str) -> None:
    """Wait until any of these server-to-client opcodes has been logged at least once.

    Neither can appear before the interaction in this mode, so presence (not an increment) is the
    right signal: a vendor without gossip options can open straight from the interaction.
    """
    deadline = time.monotonic() + VENDOR_TRADE_TIMEOUT
    while time.monotonic() < deadline:
        if any(vendor_trade_marker_count(generation, "s2c", opcode) for opcode in opcodes):
            return
        time.sleep(0.2)
    raise RuntimeError(
        f"the vendor round trip did not reach the {description} ({' / '.join(opcodes)})")


def vendor_trade_probe_input(generation: Generation, timeout: float = VENDOR_TRADE_PROBE_TIMEOUT) -> None:
    """Type a plain chat message until the server echoes it.

    The loading screen's dismissal and the window-focus handover can swallow whole key bursts, and a
    swallowed macro only shows up as a missing selection minutes later. A plain message produces
    `SMSG_MESSAGECHAT`, so this fails early and names the real problem when the client is not taking
    input yet.
    """
    before = vendor_trade_marker_count(generation, "s2c", "SMSG_MESSAGECHAT")
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        automate_key_sequence(generation, VENDOR_TRADE_PROBE_KEYS, hold_seconds=0.05, gap_seconds=0.05)
        until = time.monotonic() + 4.0
        while time.monotonic() < until:
            if vendor_trade_marker_count(generation, "s2c", "SMSG_MESSAGECHAT") > before:
                return
            time.sleep(0.2)
    capture_desktop_screenshot(generation, "vendor-input-probe.png")
    raise RuntimeError("the client did not accept a chat probe; keyboard input is not reaching it")


def vendor_trade_open(generation: Generation) -> None:
    """Probe input, select Godric by name, bind the interaction key and open his vendor window.

    Shared by the happy-path and rejection modes: the in-world marker is the server's time-sync line,
    which can arrive while the client is still drawing its loading screen, so key input sent then is
    lost to the loading/focus handover (the behaviour Plan 32's chase macro retries around). The name
    macro doubles as the input probe: once a selection is reported, keys reach the client, so the
    interaction keybind is typed afterwards instead of before.
    """
    capture_desktop_screenshot(generation, "vendor-before-target.png")
    # The in-world marker is the server's time-sync line, which can arrive while the client is still
    # drawing its loading screen; key input sent then is lost to the loading/focus handover, the same
    # behaviour the Plan 32 chase macro retries around. Let the world draw first, then use the name
    # macro itself as the input probe: once the client reports a selection, keys reach it, so the
    # interaction keybind can be set afterwards instead of before.
    time.sleep(4.0)
    vendor_trade_probe_input(generation)
    selections = vendor_trade_marker_count(generation, "c2s", "CMSG_SET_SELECTION")
    for _ in range(5):
        automate_key_sequence(generation, VENDOR_TRADE_TARGET_KEYS, hold_seconds=0.05, gap_seconds=0.05)
        time.sleep(1.5)  # let the client resolve the name and register the target
        if vendor_trade_marker_count(generation, "c2s", "CMSG_SET_SELECTION") > selections:
            break
    else:
        capture_desktop_screenshot(generation, "vendor-after-target.png")
        raise RuntimeError("vendor target macro did not select Godric Rothgar")
    capture_desktop_screenshot(generation, "vendor-after-target.png")

    automate_key_sequence(generation, VENDOR_TRADE_BIND_KEYS, hold_seconds=0.05, gap_seconds=0.05)
    time.sleep(0.5)  # let the client save the binding before pressing it

    automate_key_sequence(generation, VENDOR_TRADE_INTERACT_KEYS, hold_seconds=0.05, gap_seconds=0.05)
    vendor_trade_wait_any(
        generation, ("SMSG_GOSSIP_MESSAGE", "SMSG_VENDOR_INVENTORY"), "gossip frame or vendor window",
    )
    capture_desktop_screenshot(generation, "vendor-gossip.png")

    if not vendor_trade_marker_count(generation, "s2c", "SMSG_VENDOR_INVENTORY"):
        # The gossip menu carries the vendor option at its DB-defined row, so the row is a
        # calibration constant rather than always the first one.
        inventory = vendor_trade_marker_count(generation, "s2c", "SMSG_VENDOR_INVENTORY") + 1
        click_owned_window(generation, *vendor_trade_gossip_option())
        vendor_trade_wait(generation, "s2c", "SMSG_VENDOR_INVENTORY", "vendor inventory", inventory)
    capture_desktop_screenshot(generation, "vendor-open.png")


def automate_vendor_trade(generation: Generation) -> None:
    """Drive the Plan 33 round trip through the real client's own UI: target Godric Rothgar by name,
    interact through the bound key, open the vendor from the gossip option, buy, sell, buy back and
    repair, then log out so the character row the verification reads back is saved.

    Every step waits for the opcode the server logs for that action, so a missed click fails here
    with the screenshots as calibration evidence instead of producing a silently empty run.
    """
    vendor_trade_open(generation)

    # Buy: right-clicking a vendor row buys one item of that row.
    purchases = vendor_trade_marker_count(generation, "s2c", "SMSG_BUY_ITEM") + 1
    click_owned_window(generation, *vendor_trade_item_row(VENDOR_TRADE_BUY_MUID), button=3)
    vendor_trade_wait(generation, "s2c", "SMSG_BUY_ITEM", "purchase result", purchases)
    capture_desktop_screenshot(generation, "vendor-after-buy.png")

    # Sell: with the merchant frame open, right-clicking an item in the backpack sells it. The bag
    # may already be open (the first-run bag tutorial opens it), so the click is tried as-is and then
    # once more after toggling the bag with B, which covers both starting states.
    capture_desktop_screenshot(generation, "vendor-backpack.png")
    for attempt, offset_x in enumerate((0.0, 0.0)):
        sales = vendor_trade_marker_count(generation, "c2s", "CMSG_SELL_ITEM") + 1
        click_owned_window(
            generation, VENDOR_TRADE_FIRST_BAG_SLOT[0] + offset_x, VENDOR_TRADE_FIRST_BAG_SLOT[1], button=3)
        if vendor_trade_wait_optional(generation, "c2s", "CMSG_SELL_ITEM", sales, 6.0):
            break
        automate_key_sequence(generation, VENDOR_TRADE_BACKPACK_KEYS, hold_seconds=0.05, gap_seconds=0.05)
        time.sleep(1.0)  # let the bag frame draw before clicking its first slot
        capture_desktop_screenshot(generation, f"vendor-backpack-{attempt}.png")
    else:
        raise RuntimeError("the vendor round trip did not reach the sell request (c2s CMSG_SELL_ITEM)")
    capture_desktop_screenshot(generation, "vendor-after-sell.png")

    # Buyback: the buyback page fills the same twelve grid cells with the recent sales and hides the
    # single recent-sale control (MerchantFrame_UpdateBuybackInfo), so the newest sale is grid cell 1
    # and any click on that cell calls BuybackItem.
    click_owned_window(generation, *VENDOR_TRADE_BUYBACK_TAB)
    time.sleep(0.5)
    capture_desktop_screenshot(generation, "vendor-buyback-tab.png")
    buybacks = vendor_trade_marker_count(generation, "c2s", "CMSG_BUYBACK_ITEM") + 1
    click_owned_window(generation, *VENDOR_TRADE_BUYBACK_ITEM)
    vendor_trade_wait(generation, "c2s", "CMSG_BUYBACK_ITEM", "buyback request", buybacks)
    capture_desktop_screenshot(generation, "vendor-after-buyback.png")

    # MerchantFrame_UpdateBuybackInfo hides both repair buttons, so the purchase tab is selected
    # again (and given a moment to redraw) before repair-all is clicked.
    click_owned_window(generation, *VENDOR_TRADE_PURCHASE_TAB)
    time.sleep(0.5)
    capture_desktop_screenshot(generation, "vendor-purchase-tab.png")

    # Repair: repair-all (the anvil button) repairs every damaged equipped item.
    repairs = vendor_trade_marker_count(generation, "c2s", "CMSG_REPAIR_ITEM") + 1
    click_owned_window(generation, *VENDOR_TRADE_REPAIR_BUTTON)
    vendor_trade_wait(generation, "c2s", "CMSG_REPAIR_ITEM", "repair request", repairs)
    capture_desktop_screenshot(generation, "vendor-after-repair.png")

    # Log out so the server saves the character row the verification reads back. An ordinary
    # fixture outside a resting area waits the full logout timer, so this gets its own deadline.
    completed = vendor_trade_marker_count(generation, "s2c", "SMSG_LOGOUT_COMPLETE") + 1
    automate_key_sequence(
        generation, ("Return", "slash", *tuple("logout"), "Return"), hold_seconds=0.05, gap_seconds=0.05,
    )
    vendor_trade_wait(
        generation, "s2c", "SMSG_LOGOUT_COMPLETE", "logout", completed, VENDOR_TRADE_LOGOUT_TIMEOUT,
    )
    capture_desktop_screenshot(generation, "vendor-after-logout.png")


def vendor_trade_packet_events(generation: Generation) -> list[dict[str, object]]:
    """The sale and buyback requests from this run's packet capture, in arrival order.

    Opcode names alone cannot prove which item moved: CMSG_SELL_ITEM carries the item GUID and
    CMSG_BUYBACK_ITEM the buyback slot, so both payloads are read here to bind them to the seeded
    bracer (AC reads both fields as full uint64/uint32, not packed GUIDs).
    """
    path = Path(generation["paths"]["logs"]) / "packets.pkt"
    if not path.is_file():
        return []
    data = path.read_bytes()
    events: list[dict[str, object]] = []
    offset = PKT_LOG_HEADER_SIZE
    packet = 0
    while offset + PKT_PACKET_HEADER_SIZE <= len(data):
        direction, _, ticks, optional_size, length = struct.unpack_from("<IIIII", data, offset)
        if optional_size != 20 or length > 65535 or length < 4:
            raise RuntimeError(f"vendor packet log desynchronised at byte {offset}")
        opcode = struct.unpack_from("<I", data, offset + 40)[0]
        payload = data[offset + 44:offset + 40 + length]
        if direction == 0x47534D43 and opcode == CMSG_TRAINER_BUY_SPELL_OPCODE and len(payload) >= 16:
            # TrainerBuySpell::Read: guid, uint32 trainer id, int32 spell
            events.append({
                "opcode": "CMSG_TRAINER_BUY_SPELL",
                "trainer_guid": struct.unpack_from("<Q", payload, 0)[0],
                "trainer_id": struct.unpack_from("<I", payload, 8)[0],
                "spell": struct.unpack_from("<i", payload, 12)[0],
                "ticks": ticks, "index": packet,
            })
        elif direction != 0x47534D43 and opcode == SMSG_TRAINER_LIST_OPCODE and len(payload) >= 20:
            # guid, uint32 type, uint32 trainer id, uint32 count, then 34-byte spell records
            trainer_id = struct.unpack_from("<I", payload, 12)[0]
            count = struct.unpack_from("<I", payload, 16)[0]
            spells: list[list[int]] = []
            cursor = 20
            for _ in range(min(count, 200)):
                if cursor + 34 > len(payload):
                    break
                spells.append([struct.unpack_from("<i", payload, cursor)[0],
                               struct.unpack_from("<I", payload, cursor + 5)[0],
                               payload[cursor + 4]])
                cursor += 34
            events.append({
                "opcode": "SMSG_TRAINER_LIST", "npc_guid": struct.unpack_from("<Q", payload, 0)[0],
                "trainer_id": trainer_id, "count": count, "spells": spells, "ticks": ticks, "index": packet,
            })
        elif direction != 0x47534D43 and opcode == SMSG_TRAINER_BUY_SUCCEEDED_OPCODE and len(payload) >= 12:
            events.append({
                "opcode": "SMSG_TRAINER_BUY_SUCCEEDED",
                "trainer_guid": struct.unpack_from("<Q", payload, 0)[0],
                "spell": struct.unpack_from("<i", payload, 8)[0],
                "ticks": ticks, "index": packet,
            })
        elif direction != 0x47534D43 and opcode == SMSG_LEARNED_SPELL_OPCODE and len(payload) >= 4:
            events.append({
                "opcode": "SMSG_LEARNED_SPELL", "spell": struct.unpack_from("<i", payload, 0)[0], "ticks": ticks, "index": packet,
            })
        elif direction == 0x47534D43 and opcode == CMSG_BUY_ITEM_OPCODE and len(payload) >= 30:
            # BuyItem::Read: guid, uint8 type, uint32 item, uint32 slot (the 1-based MuID), uint32 count,
            # then the destination bag guid and slot.
            events.append({
                "opcode": "CMSG_BUY_ITEM",
                "item_type": payload[8],
                "item": struct.unpack_from("<I", payload, 9)[0],
                "slot": struct.unpack_from("<I", payload, 13)[0],
                "count": struct.unpack_from("<I", payload, 17)[0],
                "ticks": ticks, "index": packet,
            })
        elif direction != 0x47534D43 and opcode == SMSG_BUY_FAILED_OPCODE and len(payload) >= 13:
            # SendBuyError writes guid, uint32 item, optional uint32 param, then the result byte last.
            events.append({
                "opcode": "SMSG_BUY_FAILED", "result": payload[-1], "ticks": ticks, "index": packet,
                "item": struct.unpack_from("<I", payload, 8)[0],
            })
        elif direction != 0x47534D43 and opcode == SMSG_INVENTORY_CHANGE_FAILURE_OPCODE and payload:
            # SendEquipError starts with the result byte.
            events.append({"opcode": "SMSG_INVENTORY_CHANGE_FAILURE", "result": payload[0], "ticks": ticks, "index": packet})
        elif direction == 0x47534D43 and opcode == CMSG_SELL_ITEM_OPCODE and len(payload) >= 16:
            item = struct.unpack_from("<Q", payload, 8)[0]
            events.append({"opcode": "CMSG_SELL_ITEM", "item_counter": item & 0xFFFFFFFF, "ticks": ticks, "index": packet})
        elif direction == 0x47534D43 and opcode == CMSG_BUYBACK_ITEM_OPCODE and len(payload) >= 12:
            slot = struct.unpack_from("<I", payload, 8)[0]
            events.append({"opcode": "CMSG_BUYBACK_ITEM", "slot": slot, "ticks": ticks, "index": packet})
        offset += PKT_PACKET_HEADER_SIZE + (length - 4)
        packet += 1
    return events


def automate_vendor_negative(generation: Generation, case: str) -> None:
    """Open Godric's window, perform the case's rejection action and log out.

    Both layers are valid rejecters: the client can refuse an unaffordable purchase or a full
    inventory locally (no `CMSG_BUY_ITEM`), and otherwise the server answers with `SMSG_BUY_FAILED`
    or `SMSG_INVENTORY_CHANGE_FAILURE`. The automation waits for whichever happens and leaves the
    layer that answered to the verification, which requires that nothing was granted or charged.
    """
    vendor_trade_open(generation)
    if case in {"no-money", "full-bags"}:
        requests = vendor_trade_marker_count(generation, "c2s", "CMSG_BUY_ITEM")
        click_owned_window(generation, *vendor_trade_item_row(VENDOR_TRADE_BUY_MUID), button=3)
        time.sleep(1.5)
        if vendor_trade_marker_count(generation, "c2s", "CMSG_BUY_ITEM") > requests:
            deadline = time.monotonic() + 10.0
            while time.monotonic() < deadline:
                if (vendor_trade_marker_count(generation, "s2c", "SMSG_BUY_FAILED")
                        or vendor_trade_marker_count(generation, "s2c", "SMSG_INVENTORY_CHANGE_FAILURE")):
                    break
                time.sleep(0.2)
        capture_desktop_screenshot(generation, f"negative-{case}-after-buy.png")
    elif case == "repeat-buy":
        for attempt in range(2):
            purchases = vendor_trade_marker_count(generation, "s2c", "SMSG_BUY_ITEM") + 1
            click_owned_window(generation, *vendor_trade_item_row(VENDOR_TRADE_BUY_MUID), button=3)
            vendor_trade_wait(generation, "s2c", "SMSG_BUY_ITEM", f"purchase {attempt + 1}", purchases)
            time.sleep(0.5)
        capture_desktop_screenshot(generation, "negative-repeat-buy-after-buy.png")
    else:
        raise RuntimeError(f"no automation for vendor case {case!r}")

    # Log out so the server saves the character row the verification reads back.
    completed = vendor_trade_marker_count(generation, "s2c", "SMSG_LOGOUT_COMPLETE") + 1
    automate_key_sequence(
        generation, ("Return", "slash", *tuple("logout"), "Return"), hold_seconds=0.05, gap_seconds=0.05,
    )
    vendor_trade_wait(
        generation, "s2c", "SMSG_LOGOUT_COMPLETE", "logout", completed, VENDOR_TRADE_LOGOUT_TIMEOUT,
    )
    capture_desktop_screenshot(generation, f"negative-{case}-after-logout.png")


def trainer_open(generation: Generation) -> None:
    """Probe input, select Llane Beshere, bind the interaction key and open his frame."""
    capture_desktop_screenshot(generation, "trainer-before-target.png")
    time.sleep(4.0)
    vendor_trade_probe_input(generation)
    selections = vendor_trade_marker_count(generation, "c2s", "CMSG_SET_SELECTION")
    for _ in range(5):
        automate_key_sequence(generation, TRAINER_TARGET_KEYS, hold_seconds=0.05, gap_seconds=0.05)
        time.sleep(1.5)  # let the client resolve the name and register the target
        if vendor_trade_marker_count(generation, "c2s", "CMSG_SET_SELECTION") > selections:
            break
    else:
        capture_desktop_screenshot(generation, "trainer-after-target.png")
        raise RuntimeError("trainer target macro did not select Llane Beshere")
    capture_desktop_screenshot(generation, "trainer-after-target.png")

    automate_key_sequence(generation, VENDOR_TRADE_BIND_KEYS, hold_seconds=0.05, gap_seconds=0.05)
    time.sleep(0.5)  # let the client save the binding before pressing it
    automate_key_sequence(generation, VENDOR_TRADE_INTERACT_KEYS, hold_seconds=0.05, gap_seconds=0.05)
    vendor_trade_wait_any(
        generation, ("SMSG_GOSSIP_MESSAGE", "SMSG_TRAINER_LIST"), "gossip frame or trainer list")
    capture_desktop_screenshot(generation, "trainer-gossip.png")
    if not vendor_trade_marker_count(generation, "s2c", "SMSG_TRAINER_LIST"):
        trainer_list = vendor_trade_marker_count(generation, "s2c", "SMSG_TRAINER_LIST") + 1
        click_owned_window(generation, *trainer_gossip_option())
        vendor_trade_wait(generation, "s2c", "SMSG_TRAINER_LIST", "trainer list", trainer_list)
    capture_desktop_screenshot(generation, "trainer-open.png")


def automate_trainer_learn(generation: Generation) -> None:
    """Buy Battle Shout from Llane, prove it in the spellbook and by casting it, then log out."""
    trainer_open(generation)
    purchases = vendor_trade_marker_count(generation, "c2s", "CMSG_TRAINER_BUY_SPELL") + 1
    click_owned_window(generation, *trainer_spell_row())
    time.sleep(0.5)  # let the frame select the row before Train is pressed
    capture_desktop_screenshot(generation, "trainer-selected.png")
    click_owned_window(generation, *TRAINER_TRAIN_BUTTON)
    vendor_trade_wait(generation, "s2c", "SMSG_TRAINER_BUY_SUCCEEDED", "trainer purchase", purchases)
    capture_desktop_screenshot(generation, "trainer-after-buy.png")

    automate_key_sequence(generation, ("p",), hold_seconds=0.05, gap_seconds=0.05)
    time.sleep(0.5)  # let the spellbook draw
    capture_desktop_screenshot(generation, "trainer-spellbook.png")
    automate_key_sequence(generation, ("p",), hold_seconds=0.05, gap_seconds=0.05)

    # Casting by name proves the learned spell is usable without an action-bar drag (the issue's
    # "if applicable" branch); Battle Shout needs no target.
    # Both baselines are captured before the cast is typed: sampling the spell-go count afterwards waits
    # for a second cast that never comes (the response can arrive before the key helper returns).
    casts = vendor_trade_marker_count(generation, "c2s", "CMSG_CAST_SPELL") + 1
    spell_goes = vendor_trade_marker_count(generation, "s2c", "SMSG_SPELL_GO") + 1
    automate_key_sequence(
        generation,
        ("Return", "slash", *tuple("cast"), "space", *vendor_trade_name_keys(TRAINER_SPELL_NAME), "Return"),
        hold_seconds=0.05, gap_seconds=0.05,
    )
    vendor_trade_wait(generation, "c2s", "CMSG_CAST_SPELL", "cast request", casts)
    vendor_trade_wait(generation, "s2c", "SMSG_SPELL_GO", "spell cast", spell_goes)
    capture_desktop_screenshot(generation, "trainer-after-cast.png")

    completed = vendor_trade_marker_count(generation, "s2c", "SMSG_LOGOUT_COMPLETE") + 1
    automate_key_sequence(
        generation, ("Return", "slash", *tuple("logout"), "Return"), hold_seconds=0.05, gap_seconds=0.05)
    vendor_trade_wait(
        generation, "s2c", "SMSG_LOGOUT_COMPLETE", "logout", completed, TRAINER_LOGOUT_TIMEOUT)
    capture_desktop_screenshot(generation, "trainer-after-logout.png")


def combat_packet_events(generation: Generation) -> list[dict[str, object]]:
    """Spell damage logs and power updates from this run's capture, in arrival order.

    Plan 35 checks mana and damage from the wire, not from a UI reading. The layouts are identical in AC
    and the 4.x pin: the damage log is packed target guid, packed attacker guid, uint32 spell, uint32 damage,
    uint32 overkill, uint8 school, then the absorb/resist/periodic/unused/blocked/hit-info tail; the power
    update is packed guid, uint32 count, then uint8 power type + int32 value per entry.
    """
    path = Path(generation["paths"]["logs"]) / "packets.pkt"
    if not path.is_file():
        return []
    data = path.read_bytes()
    events: list[dict[str, object]] = []
    offset = PKT_LOG_HEADER_SIZE
    packet = 0
    while offset + PKT_PACKET_HEADER_SIZE <= len(data):
        direction, _, ticks, optional_size, length = struct.unpack_from("<IIIII", data, offset)
        if optional_size != 20 or length > 65535 or length < 4:
            raise RuntimeError(f"combat packet log desynchronised at byte {offset}")
        opcode = struct.unpack_from("<I", data, offset + 40)[0]
        payload = data[offset + 44:offset + 40 + length]
        if direction != 0x47534D43 and opcode == SMSG_SPELLNONMELEEDAMAGELOG_OPCODE and len(payload) >= 24:
            target, cursor = read_packed_guid(payload, 0)
            attacker, cursor = read_packed_guid(payload, cursor)
            spell, damage, overkill = struct.unpack_from("<III", payload, cursor)
            events.append({
                "opcode": "SMSG_SPELLNONMELEEDAMAGELOG", "target": target, "attacker": attacker,
                "spell": spell, "damage": damage, "overkill": overkill, "school": payload[cursor + 12],
                "ticks": ticks, "index": packet,
            })
        elif direction != 0x47534D43 and opcode == SMSG_POWER_UPDATE_OPCODE and len(payload) >= 8:
            guid, cursor = read_packed_guid(payload, 0)
            count = struct.unpack_from("<I", payload, cursor)[0]
            cursor += 4
            powers: list[list[int]] = []
            for _ in range(min(count, 10)):
                if cursor + 5 > len(payload):
                    break
                powers.append([payload[cursor], struct.unpack_from("<i", payload, cursor + 1)[0]])
                cursor += 5
            events.append({
                "opcode": "SMSG_POWER_UPDATE", "guid": guid, "powers": powers, "ticks": ticks, "index": packet,
            })
        offset += PKT_PACKET_HEADER_SIZE + (length - 4)
        packet += 1
    return events


def character_row_count(manifest: Manifest, generation: Generation) -> int:
    output = mysql(
        manifest, generation,
        f"SELECT COUNT(*) FROM `characters` WHERE `account`={ACCOUNT_ID} AND `deleteDate` IS NULL;",
        generation["schemas"]["characters"],
    )
    try:
        return int(output)
    except ValueError as error:
        raise RuntimeError(f"owned character database returned a non-numeric row count: {output!r}") from error


def run_client(args: argparse.Namespace) -> None:
    manifest_path = args.manifest.resolve()
    manifest = load_manifest(manifest_path)
    generation = active_generation(manifest)
    if generation["mode"] in CHARACTER_MODES and args.stability_seconds < 5:
        raise RuntimeError("--stability-seconds must be at least 5")
    retryable = generation["state"] in {"observed", "accepted", "failed", "inconclusive"}
    if retryable:
        stop_servers(generation)
        if any(process.get("active", False) for process in generation["processes"]):
            raise RuntimeError("cannot retry a diagnostic generation with active processes")
        archives = Path(generation["root"]) / "previous-runs"
        archives.mkdir(exist_ok=True)
        archive = archives / str(len(list(archives.iterdir())) + 1)
        archive.mkdir()
        (archive / "generation.json").write_text(json.dumps(generation, indent=2) + "\n")
        for name in ("raw_evidence", "logs"):
            source = Path(generation["paths"][name])
            if source.exists():
                shutil.copytree(source, archive / name)
        for name in ("Logs", "Errors"):
            source = Path(generation["paths"]["client"]) / name
            if source.exists():
                shutil.copytree(source, archive / f"client-{name}")
        for path in (
            Path(generation["paths"]["raw_evidence"]),
            Path(generation["paths"]["client"]) / "Logs",
            Path(generation["paths"]["client"]) / "Errors",
        ):
            if path.exists():
                remove_owned_path(path, generation)
            path.mkdir(parents=True)
        generation["state"] = "prepared"
        generation["failure"] = None
        generation["evidence"] = None
        generation.pop("issue97_stages", None)
    if generation["state"] != "prepared":
        raise RuntimeError(f"cannot run from state {generation['state']}")
    assert_container_owned(manifest, generation)
    for name in ("authserver", "worldserver"):
        binary = generation["inputs"][name]
        if sha256(Path(str(binary["path"]))) != binary["sha256"]:
            raise RuntimeError(f"{name} binary changed; prepare a fresh generation")
    ensure_client_base(manifest, generation)
    save_manifest(manifest_path, manifest)
    write_client_config(Path(generation["paths"]["client"]))
    install_dxvk(generation)
    configure_wine_proxy(generation)
    stop_wine(generation)
    save_manifest(manifest_path, manifest)
    require_unused(AUTH_PORT)
    require_unused(generation["ports"]["world"])
    paths = generation["paths"]
    if generation["mode"] == RUN_SPEED_MODE:
        # Re-seed on a transient retry too; a previous login may have consumed the saved aura.
        mysql(manifest, generation, run_speed_aura_seed_sql(), generation["schemas"]["characters"])
    if generation["mode"] == BASIC_SPELL_CAST_MODE:
        mysql(manifest, generation, basic_spell_cast_seed_sql(), generation["schemas"]["characters"])
    if generation["mode"] == TARGET_TARGETED_SPELL_CAST_MODE:
        mysql(manifest, generation, target_targeted_spell_cast_seed_sql(), generation["schemas"]["characters"])
        mysql(manifest, generation, target_targeted_spell_cast_world_seed_sql(), generation["schemas"]["world"])
    if generation["mode"] == PLAN32_GEOMETRY_MODE:
        stage = plan32_frozen_stage(generation)
        generation["plan32_stage"] = stage
        # Re-seed the character row too: a transient retry must start from the frozen stage's pinned
        # point again, not from wherever the previous attempt's walk left the character.
        apply_plan32_stage_seed(generation["mode"], stage)
        mysql(manifest, generation, populated_character_seed_sql(), generation["schemas"]["characters"])
        mysql(manifest, generation, plan32_spell_seed_sql(), generation["schemas"]["characters"])
        mysql(manifest, generation, plan32_world_seed_sql(stage), generation["schemas"]["world"])
        verify_plan32_seeds(manifest, generation)
    if generation["mode"] == INVALID_TARGET_SPELL_CAST_MODE:
        mysql(manifest, generation, target_targeted_spell_cast_seed_sql(), generation["schemas"]["characters"])
    if generation["mode"] == VENDOR_TRADE_MODE:
        apply_vendor_trade_seed(generation["mode"])
        mysql(manifest, generation, vendor_trade_seed_sql(), generation["schemas"]["characters"])
    if generation["mode"] == VENDOR_NEGATIVE_MODE:
        case = vendor_negative_frozen_case(generation)
        generation["vendor_case"] = case
        apply_vendor_negative_seed(generation["mode"])
        mysql(manifest, generation, vendor_negative_seed_sql(case), generation["schemas"]["characters"])
    if generation["mode"] in {CHARACTER_CREATION_MODE, *MAGE_MODES}:
        apply_creation_class(generation["mode"], generation["inputs"].get("create_class", "warrior"))
        generation.pop("mage_combat_sent", None)
    if generation["mode"] == MAGE_COMBAT_MODE:
        mage_combat_character_reset(manifest, generation)
        mysql(manifest, generation, mage_combat_world_seed_sql(), generation["schemas"]["world"])
    if generation["mode"] == MAGE_FOLLOWUP_MODE:
        mage_combat_character_reset(manifest, generation)
        mysql(manifest, generation, mage_followup_world_seed_sql(), generation["schemas"]["world"])
    if generation["mode"] == TRAINER_MODE:
        apply_trainer_seed(generation["mode"])
        mysql(manifest, generation, trainer_seed_sql(), generation["schemas"]["characters"])
    if generation["mode"] == LEVEL_UP_MODE:
        mysql(manifest, generation, level_up_action_seed_sql(), generation["schemas"]["characters"])
        mysql(manifest, generation, level_up_gear_seed_sql(), generation["schemas"]["characters"])
        mysql(manifest, generation, level_up_tutorial_seed_sql(), generation["schemas"]["characters"])
        mysql(manifest, generation, level_up_world_seed_sql(), generation["schemas"]["world"])
        generation["level_up_seed"] = verify_level_up_seed(manifest, generation)
    if ISSUE97 and generation["mode"] == IN_WORLD_CONTROL_MODE:
        mysql(manifest, generation, populated_character_seed_sql(), generation["schemas"]["characters"])
        verify_populated_character_seed(manifest, generation)
        mysql(manifest, generation, issue97_world_seed_sql(), generation["schemas"]["world"])
    popens: dict[str, subprocess.Popen[bytes]] = {}
    outputs: list[object] = []
    try:
        auth_process, auth_record, auth_output = start_server(
            generation, "authserver", Path(str(generation["inputs"]["authserver"]["path"])),
            Path(paths["auth_config"]), Path(paths["logs"]) / "authserver.stdout.log",
        )
        popens["authserver"] = auth_process
        outputs.append(auth_output)
        save_manifest(manifest_path, manifest)
        wait_for_port(AUTH_PORT, auth_record, 60)
        world_process, world_record, world_output = start_server(
            generation, "worldserver", Path(str(generation["inputs"]["worldserver"]["path"])),
            Path(paths["world_config"]), Path(paths["logs"]) / "worldserver.stdout.log",
        )
        popens["worldserver"] = world_process
        outputs.append(world_output)
        save_manifest(manifest_path, manifest)
        wait_for_world(manifest, generation, world_record)
        set_state(generation, "servers_ready")
        save_manifest(manifest_path, manifest)
        wine = Path(str(generation["inputs"]["wine"]))
        client = Path(paths["client"]) / "Wow-64.exe"
        client_output = (Path(paths["logs"]) / "client.stdout.log").open("wb")
        outputs.append(client_output)
        launcher = subprocess.Popen(
            [str(wine), str(client)], stdout=client_output, stderr=subprocess.STDOUT,
            cwd=client.parent, env=wine_environment(generation), start_new_session=True,
        )
        try:
            generation["processes"].append(process_identity(launcher.pid, "wine", prefix=Path(paths["wine_prefix"])))
        except FileNotFoundError:
            pass
        set_state(generation, "client_running")
        save_manifest(manifest_path, manifest)
        print(f"owned client launched on {generation['inputs']['display']}")
        if args.auto_login and generation["mode"] in {"authentication", *CHARACTER_MODES}:
            automate_client_login(generation)
            print("synthetic credentials submitted automatically")
        elif generation["mode"] in {"authentication", *CHARACTER_MODES}:
            print(f"enter synthetic credentials manually: {ACCOUNT} / {PASSWORD}")
        deadline = time.monotonic() + (args.no_login_seconds if generation["mode"] == "no-login" else args.timeout)
        character_hold_started: float | None = None
        selection_sent = False
        ground_movement_sent = False
        sustained_forward_sent = False
        jump_fall_land_sent = False
        walk_run_mode_switch_sent = False
        spell_cast_sent = False
        target_targeted_spell_cast_sent = False
        plan32_geometry_sent = False
        chat_message_sent = False
        invalid_target_spell_cast_sent = False
        level_up_sent = False
        vendor_trade_sent = False
        vendor_negative_sent = False
        trainer_learn_sent = False
        post_marker_hold_started: float | None = None
        milestone_definition = CHARACTER_MILESTONES if generation["mode"] in CHARACTER_MODES else CLIENT_MILESTONES
        while time.monotonic() < deadline:
            add_processes(generation, find_wine_processes(Path(paths["wine_prefix"])))
            capture_runtime(generation)
            milestones = matched_milestones(connection_log(generation), milestone_definition)
            if generation["mode"] == "authentication" and len(milestones) == 4:
                break
            if generation["mode"] in CHARACTER_MODES:
                if len(milestones) == len(CHARACTER_MILESTONES) and character_hold_started is None:
                    character_hold_started = time.monotonic()
                if (
                    generation["mode"] in {CHARACTER_SELECTION_MODE, *POST_MARKER_MODES}
                    and character_hold_started is not None and not selection_sent
                ):
                    if generation["mode"] in {CHARACTER_CREATION_MODE, *MAGE_MODES}:
                        automate_character_creation(generation)
                        generation["selection_action"] = "create"
                    else:
                        automate_character_selection(generation)
                        generation["selection_action"] = "enter"
                    save_manifest(manifest_path, manifest)
                    selection_sent = True
                if (
                    generation["mode"] == CHARACTER_SELECTION_MODE and selection_sent
                    and player_login_callbacks(generation)
                ):
                    break
                if (
                    generation["mode"] == IN_WORLD_CONTROL_MODE and selection_sent and not sustained_forward_sent
                    and in_world_control_marker_count(generation) > 0
                ):
                    # Short repeated taps deliver movement reliably without a fixed 45-second walk.
                    if ISSUE97:
                        automate_issue97_phases(generation, deadline)
                    else:
                        automate_key_sequence(generation, ("w",) * 3, hold_seconds=0.6, gap_seconds=0.4)
                    sustained_forward_sent = True
                if (
                    generation["mode"] == GROUND_MOVEMENT_MODE and selection_sent and not ground_movement_sent
                    and in_world_control_marker_count(generation) > 0
                ):
                    automate_ground_movement(generation)
                    ground_movement_sent = True
                if (
                    generation["mode"] == JUMP_FALL_LAND_MODE and selection_sent and not jump_fall_land_sent
                    and in_world_control_marker_count(generation) > 0
                ):
                    automate_jump_fall_land(generation)
                    jump_fall_land_sent = True
                if (
                    generation["mode"] == WALK_RUN_MODE_SWITCH_MODE and selection_sent
                    and not walk_run_mode_switch_sent and in_world_control_marker_count(generation) > 0
                ):
                    automate_walk_run_mode_switch(generation)
                    walk_run_mode_switch_sent = True
                if (
                    generation["mode"] == BASIC_SPELL_CAST_MODE and selection_sent and not spell_cast_sent
                    and in_world_control_marker_count(generation) > 0
                ):
                    automate_spell_cast(generation)
                    spell_cast_sent = True
                if (
                    generation["mode"] == TARGET_TARGETED_SPELL_CAST_MODE and selection_sent
                    and not target_targeted_spell_cast_sent and in_world_control_marker_count(generation) > 0
                ):
                    automate_target_targeted_spell_cast(generation)
                    target_targeted_spell_cast_sent = True
                if (
                    generation["mode"] == PLAN32_GEOMETRY_MODE and selection_sent
                    and not plan32_geometry_sent and in_world_control_marker_count(generation) > 0
                ):
                    if generation.get("plan32_stage") == "chase":
                        automate_plan32_chase(generation)
                    elif generation.get("plan32_stage") == "relog":
                        automate_plan32_relog(generation)
                    else:
                        automate_plan32_geometry(generation)
                    plan32_geometry_sent = True
                if (
                    generation["mode"] == CHAT_MESSAGE_MODE and selection_sent and not chat_message_sent
                    and in_world_control_marker_count(generation) > 0
                ):
                    automate_chat_message(generation)
                    chat_message_sent = True
                if (
                    generation["mode"] == INVALID_TARGET_SPELL_CAST_MODE and selection_sent
                    and not invalid_target_spell_cast_sent and in_world_control_marker_count(generation) > 0
                ):
                    automate_invalid_target_spell_cast(generation)
                    invalid_target_spell_cast_sent = True
                if (
                    generation["mode"] == VENDOR_TRADE_MODE and selection_sent
                    and not vendor_trade_sent and in_world_control_marker_count(generation) > 0
                ):
                    automate_vendor_trade(generation)
                    vendor_trade_sent = True
                if (
                    generation["mode"] == VENDOR_NEGATIVE_MODE and selection_sent
                    and not vendor_negative_sent and in_world_control_marker_count(generation) > 0
                ):
                    automate_vendor_negative(generation, vendor_negative_frozen_case(generation))
                    vendor_negative_sent = True
                if (
                    generation["mode"] == TRAINER_MODE and selection_sent
                    and not trainer_learn_sent and in_world_control_marker_count(generation) > 0
                ):
                    automate_trainer_learn(generation)
                    trainer_learn_sent = True
                if (
                    generation["mode"] == LEVEL_UP_MODE and selection_sent and not level_up_sent
                    and in_world_control_marker_count(generation) > 0
                ):
                    automate_level_up(generation)
                    level_up_sent = True
                if generation["mode"] in POST_MARKER_MODES and selection_sent:
                    marker_count = POST_MARKER_COUNTERS[generation["mode"]](generation)
                    if marker_count and post_marker_hold_started is None:
                        capture_runtime(generation)
                        generation["post_marker_snapshots"] = 1
                        post_marker_hold_started = time.monotonic()
                        if generation["mode"] in {CHARACTER_CREATION_MODE, *MAGE_MODES}:
                            automate_character_selection(generation)
                    if generation["mode"] in {CHARACTER_CREATION_MODE, *MAGE_MODES}:
                        if post_marker_hold_started is not None and player_login_callbacks(generation):
                            if generation["mode"] == MAGE_COMBAT_MODE:
                                automate_mage_combat(generation)
                            elif generation["mode"] == MAGE_FOLLOWUP_MODE:
                                automate_mage_followup(generation)
                            capture_runtime(generation)
                            generation["post_marker_snapshots"] = 2
                            generation["post_marker_hold_seconds"] = time.monotonic() - post_marker_hold_started
                            break
                    elif (
                        post_marker_hold_started is not None
                        and time.monotonic() - post_marker_hold_started >= args.stability_seconds
                    ):
                        capture_runtime(generation)
                        generation["post_marker_snapshots"] = 2
                        generation["post_marker_hold_seconds"] = args.stability_seconds
                        break
                if (
                    generation["mode"] not in {CHARACTER_SELECTION_MODE, *POST_MARKER_MODES}
                    and character_hold_started is not None
                    and time.monotonic() - character_hold_started >= args.stability_seconds
                ):
                    break
            if generation["mode"] == "no-login" and time.monotonic() + 0.5 >= deadline:
                break
            if launcher.poll() is not None and not find_wine_processes(Path(paths["wine_prefix"])):
                break
            time.sleep(0.5)
        capture_runtime(generation)
        capture_owned_window(generation)
        if generation["mode"] in CHARACTER_MODES:
            generation["stability_seconds"] = args.stability_seconds if character_hold_started is not None else 0
        raw = Path(paths["raw_evidence"])
        source_log = Path(paths["client"]) / "Logs/connection.log"
        if source_log.is_file():
            shutil.copy2(source_log, raw / "connection.log")
        for name in ("WorldServer.log", "AuthServer.log"):
            source = Path(paths["logs"]) / name
            if source.is_file():
                shutil.copy2(source, raw / name)
        set_state(generation, "observed")
    except BaseException as error:
        record_failure(manifest_path, manifest, error)
        raise
    finally:
        cleanup_error: BaseException | None = None
        try:
            stop_wine(generation)
            stop_servers(generation, popens)
        except BaseException as error:
            cleanup_error = error
        finally:
            for output in outputs:
                output.close()
            save_manifest(manifest_path, manifest)
        if cleanup_error is not None:
            if generation["state"] not in ("failed", "inconclusive", "reset"):
                record_failure(manifest_path, manifest, cleanup_error)
            raise cleanup_error
    print(f"observed Plan {plan_number(str(generation['mode']))} generation {generation['number']}; run verify next")


def world_log_text(generation: Generation) -> str:
    paths = generation["paths"]
    candidates = [
        Path(paths["logs"]) / "WorldServer.log",
        Path(paths["logs"]) / "worldserver.stdout.log",
        Path(paths["raw_evidence"]) / "WorldServer.log",
    ]
    source = next((path for path in candidates if path.is_file()), None)
    return source.read_text(errors="replace") if source else ""


def server_transcript(generation: Generation) -> list[dict[str, str]]:
    transcript = []
    pattern = re.compile(r"\b(C->S|S->C):\s+.*?\b((?:CMSG|SMSG|MSG)_[A-Z0-9_]+)\b")
    for direction, opcode in pattern.findall(world_log_text(generation)):
        transcript.append({"direction": "c2s" if direction == "C->S" else "s2c", "opcode": opcode})
    return transcript


def enumerated_characters(generation: Generation) -> list[dict[str, int]]:
    pattern = re.compile(
        r"Enumerated account (\d+) character GUID Full: 0x[0-9a-fA-F]+ Type: Player Low: (\d+) "
        r"at list position (\d+)\."
    )
    return [
        {"account_id": int(account), "guid_low": int(guid), "list_position": int(position)}
        for account, guid, position in pattern.findall(world_log_text(generation))
    ]


def player_login_requests(generation: Generation) -> list[int]:
    pattern = re.compile(r"Account \(\d+\) is trying to login with character \(.*? Low: (\d+)\)")
    return [int(guid) for guid in pattern.findall(world_log_text(generation))]


def player_login_callbacks(generation: Generation) -> list[int]:
    pattern = re.compile(r"Player login callback for account \d+ character .*? Low: (\d+)")
    return [int(guid) for guid in pattern.findall(world_log_text(generation))]


def initial_packets_marker_count(generation: Generation) -> int:
    return world_log_text(generation).count("Finished sending initial packets before going to map")


def initial_packet_prefix(generation: Generation) -> list[str]:
    packet = re.compile(r"\b(C->S|S->C):\s+.*?\b((?:CMSG|SMSG|MSG)_[A-Z0-9_]+)\b")
    prefix: list[str] = []
    selected = False
    for line in world_log_text(generation).splitlines():
        match = packet.search(line)
        if match and match.group(1) == "C->S" and match.group(2) == "CMSG_PLAYER_LOGIN":
            selected = True
            continue
        if "Finished sending initial packets before going to map" in line:
            break
        if selected and match and match.group(1) == "S->C":
            prefix.append(match.group(2))
    return prefix


def map_insertion_marker_count(generation: Generation) -> int:
    return world_log_text(generation).count("Finished object update bootstrap after adding to map")


def map_insertion_packet_prefix(generation: Generation) -> list[str]:
    """Packets from the Plan 11 pre-map marker through the Plan 12 post-bootstrap
    marker: LOGIN_VERIFY_WORLD and the object/update bootstrap, not the pre-map
    prefix Plan 11 already proved and not anything after this marker (movement/
    control is Plan 13)."""
    packet = re.compile(r"\b(C->S|S->C):\s+.*?\b((?:CMSG|SMSG|MSG)_[A-Z0-9_]+)\b")
    prefix: list[str] = []
    selected = False
    for line in world_log_text(generation).splitlines():
        if "Finished sending initial packets before going to map" in line:
            selected = True
            continue
        if "Finished object update bootstrap after adding to map" in line:
            break
        match = packet.search(line)
        if selected and match and match.group(1) == "S->C":
            prefix.append(match.group(2))
    return prefix


IN_WORLD_CONTROL_MARKER = "Resolved client time sync response, first in-world control liveness signal"


def in_world_control_marker_count(generation: Generation) -> int:
    return world_log_text(generation).count(IN_WORLD_CONTROL_MARKER)


def in_world_control_packet_prefix(generation: Generation) -> list[str]:
    """Packets from the Plan 12 post-bootstrap marker through the first resolved
    CMSG_TIME_SYNC_RESP: whatever the server sends while waiting, plus the first
    client-initiated packet, proving the client is alive and ticking. Movement or
    other player-control packets are not awaited here; that is the Movement plan
    family, not this boundary."""
    packet = re.compile(r"\b(C->S|S->C):\s+.*?\b((?:CMSG|SMSG|MSG)_[A-Z0-9_]+)\b")
    prefix: list[str] = []
    selected = False
    for line in world_log_text(generation).splitlines():
        if "Finished object update bootstrap after adding to map" in line:
            selected = True
            continue
        if IN_WORLD_CONTROL_MARKER in line:
            break
        match = packet.search(line)
        if selected and match:
            prefix.append(f"{match.group(1)}:{match.group(2)}")
    return prefix


MOVEMENT_HEARTBEAT_MARKER = "Accepted Cataclysm movement heartbeat after movement validation"
MOVEMENT_SAMPLE_SUFFIX = r": x=([-+0-9.eE]+), y=([-+0-9.eE]+), z=([-+0-9.eE]+), o=([-+0-9.eE]+)"
HEARTBEAT_SAMPLE_PATTERN = re.compile(re.escape(MOVEMENT_HEARTBEAT_MARKER) + MOVEMENT_SAMPLE_SUFFIX)


def movement_heartbeat_count(generation: Generation) -> int:
    after_map = world_log_text(generation).partition("Finished object update bootstrap after adding to map")[2]
    after_sync = after_map.partition(IN_WORLD_CONTROL_MARKER)[2]
    return after_sync.count(MOVEMENT_HEARTBEAT_MARKER)


def run_speed_acknowledgements(generation: Generation) -> list[dict[str, int | float]]:
    after_map = world_log_text(generation).partition("Finished object update bootstrap after adding to map")[2]
    after_sync = after_map.partition(IN_WORLD_CONTROL_MARKER)[2]
    after_heartbeat = after_sync.partition(MOVEMENT_HEARTBEAT_MARKER)[2]
    pending: dict[int, float] = {}
    accepted = []
    pattern = (
        r"(Sent Cataclysm run-speed change|Accepted Cataclysm run-speed acknowledgement): "
        r"counter=(\d+), speed=([-+0-9.eE]+)(?=\s|$)"
    )
    for kind, counter_text, speed_text in re.findall(pattern, after_heartbeat):
        counter, speed = int(counter_text), float(speed_text)
        if kind.startswith("Sent"):
            pending[counter] = speed
        elif pending.pop(counter, None) == speed == 7.0:
            accepted.append({"counter": counter, "speed": speed})
    return accepted


def character_creation_marker_count(generation: Generation) -> int:
    return world_log_text(generation).count("[SMSG_CHAR_CREATE ")


GROUND_MOVEMENT_MARKER = "Accepted Cataclysm ground movement"
GROUND_MOVEMENT_OPCODES = (
    "MSG_MOVE_START_FORWARD", "MSG_MOVE_STOP",
    "MSG_MOVE_START_BACKWARD", "MSG_MOVE_STOP",
    "MSG_MOVE_START_STRAFE_LEFT", "MSG_MOVE_STOP_STRAFE",
    "MSG_MOVE_START_STRAFE_RIGHT", "MSG_MOVE_STOP_STRAFE",
    "MSG_MOVE_START_TURN_LEFT", "MSG_MOVE_STOP_TURN",
    "MSG_MOVE_START_TURN_RIGHT", "MSG_MOVE_STOP_TURN",
)
# The acceptance log line and marker text are shared with ground movement (both route through
# GetGroundMovementSequence), so ground_movement_samples/sequence/marker_count below are reused
# as-is for jump/fall-land; only the opcode sequence and delta-appropriateness check differ.
JUMP_FALL_LAND_OPCODES = ("MSG_MOVE_JUMP", "MSG_MOVE_FALL_LAND")


GROUND_MOVEMENT_SAMPLE_PATTERN = re.compile(
    re.escape(GROUND_MOVEMENT_MARKER) + r" \[?(MSG_MOVE_[A-Z_]+)(?: 0x[0-9A-Fa-f]+ \(\d+\))?\]? after movement "
    r"validation" + MOVEMENT_SAMPLE_SUFFIX
)


def ground_movement_marker_count(generation: Generation) -> int:
    return len(ground_movement_sequence(generation))


SPELL_CAST_PACKET = re.compile(r"\bS->C:\s+.*?\bSMSG_SPELL_GO\b")


def spell_cast_marker_count(generation: Generation) -> int:
    after_map = world_log_text(generation).partition("Finished object update bootstrap after adding to map")[2]
    after_sync = after_map.partition(IN_WORLD_CONTROL_MARKER)[2]
    return len(SPELL_CAST_PACKET.findall(after_sync))


CAST_FAILED_PACKET = re.compile(r"\bS->C:\s+.*?\bSMSG_CAST_FAILED\b")


def cast_failed_marker_count(generation: Generation) -> int:
    after_map = world_log_text(generation).partition("Finished object update bootstrap after adding to map")[2]
    after_sync = after_map.partition(IN_WORLD_CONTROL_MARKER)[2]
    return len(CAST_FAILED_PACKET.findall(after_sync))


CHAT_MESSAGE_PACKET = re.compile(r"\bS->C:\s+.*?\bSMSG_MESSAGECHAT\b")


def chat_message_marker_count(generation: Generation) -> int:
    after_map = world_log_text(generation).partition("Finished object update bootstrap after adding to map")[2]
    after_sync = after_map.partition(IN_WORLD_CONTROL_MARKER)[2]
    return len(CHAT_MESSAGE_PACKET.findall(after_sync))


def movement_samples_in(text: str) -> list[dict[str, str | float]]:
    return [
        {"opcode": opcode, "x": float(x), "y": float(y), "z": float(z), "o": float(o)}
        for opcode, x, y, z, o in GROUND_MOVEMENT_SAMPLE_PATTERN.findall(text)
    ]


def ground_movement_samples(generation: Generation) -> list[dict[str, str | float]]:
    after_map = world_log_text(generation).partition("Finished object update bootstrap after adding to map")[2]
    after_sync = after_map.partition(IN_WORLD_CONTROL_MARKER)[2]
    return movement_samples_in(after_sync)


PLAN32_LOGIN_RECEIVED_PATTERN = re.compile(r"Received CMSG_PLAYER_LOGIN for account \d+\.")


def plan32_relog_timeline(generation: Generation) -> dict[str, object]:
    """The relog run's movement samples, cut at the session boundaries the server logged itself.

    A landing alone is not enough: a session that falls twice would let a landing that happened
    before the logout stand in for the position the next login arrives at. So the log is read in
    order — the walk under test is what came before the server completed the logout, and the
    returned samples are those after a CMSG_PLAYER_LOGIN that followed it — and neither a landing
    outside the second login's interval nor a login that preceded the logout can satisfy the
    sequence this stage claims."""
    walk: list[dict[str, str | float]] = []
    returned: list[dict[str, str | float]] = []
    logouts = 0
    logins = 0
    logins_after_logout = 0
    for line in world_log_text(generation).splitlines():
        if LOGOUT_COMPLETE_PATTERN.search(line):
            logouts += 1
            continue
        if PLAN32_LOGIN_RECEIVED_PATTERN.search(line):
            logins += 1
            if logouts:
                logins_after_logout += 1
            continue
        samples = movement_samples_in(line)
        if not samples:
            continue
        if logouts == 0:
            walk.append(samples[0])
        elif logins_after_logout:
            returned.append(samples[0])
    return {
        "walk": walk,
        "returned": returned,
        "logout_completes": logouts,
        "login_requests": logins,
        "logins_after_logout": logins_after_logout,
    }


def plan32_relog_problems(generation: Generation) -> list[str]:
    """The relog stage's own predicate, as what is wrong with it (empty when it holds).

    The walk must have stopped before the session ended, exactly one logout must have completed,
    the client must have logged in again after that logout, and the first accepted movement after
    that login must be a landing agreeing with the stop: the position the server saved at logout —
    not a re-seed and not another landing inside the session that walked."""
    timeline = plan32_relog_timeline(generation)
    walked, returned = timeline["walk"], timeline["returned"]
    stop = walked[-1] if walked else None
    relogin = returned[0] if returned else None
    seed = PLAN32_GEOMETRY_SEEDS["relog"]["character"]
    problems: list[str] = []
    if timeline["logout_completes"] != 1:
        problems.append(f"expected one SMSG_LOGOUT_COMPLETE, saw {timeline['logout_completes']}")
    if timeline["login_requests"] != 2:
        problems.append(f"expected two CMSG_PLAYER_LOGIN requests, saw {timeline['login_requests']}")
    if timeline["logins_after_logout"] != 1:
        problems.append("no CMSG_PLAYER_LOGIN followed the completed logout")
    if plan32_landing_count(generation) != 2:
        problems.append(
            f"expected two world-entry landings, saw {plan32_landing_count(generation)}"
        )
    if stop is None or str(stop["opcode"]) != "MSG_MOVE_STOP":
        problems.append("the walk did not end in an accepted stop before the logout")
    if relogin is None or str(relogin["opcode"]) != "MSG_MOVE_FALL_LAND":
        problems.append("the session after the logout produced no world-entry landing")
    if stop is not None and relogin is not None:
        if math.hypot(float(relogin["x"]) - float(stop["x"]),
                      float(relogin["y"]) - float(stop["y"])) > PLAN32_RELOG_TOLERANCE:
            problems.append("the landing does not agree with the walked stop")
        if abs(float(relogin["z"]) - float(stop["z"])) > PLAN32_RELOG_TOLERANCE:
            problems.append("the landing's height does not agree with the walked stop")
    for name, sample in (("stop", stop), ("landing", relogin)):
        if sample is not None and math.hypot(
            float(sample["x"]) - seed[0], float(sample["y"]) - seed[1]
        ) < PLAN32_RELOG_MIN_TRAVEL:
            problems.append(f"the {name} sits on the seeded login point")
    return problems


def ground_movement_sequence(generation: Generation) -> list[str]:
    return [str(sample["opcode"]) for sample in ground_movement_samples(generation)]


# Wire numbers from src/server/game/Server/Protocol/Opcodes.h; payload layouts from
# WorldSession::HandleCastSpellOpcode (cast), Spell::WriteCastResultInfo (failed) and
# Spell::SendSpellGo (go). Only the Plan 32 mode reads the capture.
PKT_LOG_HEADER_SIZE = 66
PKT_PACKET_HEADER_SIZE = 44
CMSG_SET_SELECTION_OPCODE = 0x506
CMSG_SELL_ITEM_OPCODE = 0x4E15
CMSG_BUYBACK_ITEM_OPCODE = 0x6C17
CMSG_BUY_ITEM_OPCODE = 0x0736
CMSG_TRAINER_BUY_SPELL_OPCODE = 0x4415
SMSG_TRAINER_LIST_OPCODE = 0x4414
SMSG_SPELLNONMELEEDAMAGELOG_OPCODE = 0x4315
SMSG_POWER_UPDATE_OPCODE = 0x4A07
SMSG_TRAINER_BUY_SUCCEEDED_OPCODE = 0x6A05
SMSG_LEARNED_SPELL_OPCODE = 0x58A2
SMSG_BUY_FAILED_OPCODE = 0x6435
SMSG_INVENTORY_CHANGE_FAILURE_OPCODE = 0x2236
# BuyResult/EQUIP_ERR values the rejection cases expect (Item.h: BUY_ERR_NOT_ENOUGHT_MONEY,
# EQUIP_ERR_INVENTORY_FULL). Decoding the reason stops an unrelated refusal from passing.
VENDOR_NEGATIVE_BUY_ERR_NOT_ENOUGHT_MONEY = 2
VENDOR_NEGATIVE_EQUIP_ERR_INVENTORY_FULL = 50
CMSG_CAST_SPELL_OPCODE = 0x4C07
SMSG_CAST_FAILED_OPCODE = 0x4D16
SMSG_SPELL_GO_OPCODE = 0x6E16
SPELL_FAILED_LINE_OF_SIGHT_RESULT = 49
SMSG_ON_MONSTER_MOVE_OPCODE = 0x6E17
SMSG_ATTACKERSTATEUPDATE_OPCODE = 0x0B25
# The client's own forward tap (Opcodes.h MSG_MOVE_START_FORWARD): the chase's run leg starts
# here, so the chase observation is measured from this packet's arrival tick, not from the pull.
MSG_MOVE_START_FORWARD_OPCODE = 0x7814
# The client's own strafe stop (Opcodes.h MSG_MOVE_STOP_STRAFE), the pinned run leg's last tap:
# from its arrival tick the player stands at the run's end point, so until then nothing can be
# called the chase's arrival - the player is still on the way there.
MSG_MOVE_STOP_STRAFE_OPCODE = 0x3002
# MoveSplineFlag bits (MoveSplineFlag.h) needed to walk a monster-move body.
SPLINE_FLAG_CYCLIC = 0x00001000
SPLINE_FLAG_UNCOMPRESSED_PATH = 0x00400000
SPLINE_FLAG_ANIMATION = 0x01000000
SPLINE_FLAG_PARABOLIC = 0x02000000
SPLINE_FLAG_FINAL_POINT = 0x20000000
SPLINE_FLAG_FINAL_TARGET = 0x40000000
SPLINE_FLAG_FINAL_ANGLE = 0x80000000


def read_packed_guid(data: bytes, offset: int) -> tuple[int, int]:
    """One packed GUID: a mask byte followed by the non-zero bytes in order."""
    mask = data[offset]
    guid = 0
    cursor = offset + 1
    for index in range(8):
        if mask & (1 << index):
            guid |= data[cursor] << (index * 8)
            cursor += 1
    return guid, cursor


def plan32_packet_events(generation: Generation) -> list[dict[str, object]]:
    """The cast correlation for one Plan 32 run, in arrival order: selections (target GUID),
    casts (counter, spell, target GUID or None) and their outcomes (failed: counter, spell,
    result; go: caster, counter, spell), each with its packet index and arrival tick so callers
    can order them against the fixture's own spline packets and place them in time (a spline
    packet only advertises where its mover will be). Anything else on the wire is ignored."""
    path = Path(generation["paths"]["logs"]) / "packets.pkt"
    if not path.is_file():
        return []
    data = path.read_bytes()
    events: list[dict[str, object]] = []
    offset = PKT_LOG_HEADER_SIZE
    packet = 0
    while offset + PKT_PACKET_HEADER_SIZE <= len(data):
        direction, _, ticks, optional_size, length = struct.unpack_from("<IIIII", data, offset)
        if optional_size != 20 or length > 65535 or length < 4:
            raise RuntimeError(f"plan32 packet log desynchronised at byte {offset}")
        opcode = struct.unpack_from("<I", data, offset + 40)[0]
        payload = data[offset + 44:offset + 40 + length]
        side = "c2s" if direction == 0x47534D43 else "s2c"
        if opcode == CMSG_SET_SELECTION_OPCODE:
            # ObjectGuid's own stream operator reads a full uint64, unlike the packed GUIDs in
            # the cast packets below.
            target = struct.unpack_from("<Q", payload, 0)[0]
            events.append({"opcode": "CMSG_SET_SELECTION", "side": side, "index": packet,
                           "ticks": ticks, "target": target})
        elif opcode == CMSG_CAST_SPELL_OPCODE:
            count = payload[0]
            spell = struct.unpack_from("<I", payload, 1)[0]
            mask = struct.unpack_from("<I", payload, 10)[0]
            target = None
            if mask & 0x00000002:
                target, _ = read_packed_guid(payload, 14)
            events.append({
                "opcode": "CMSG_CAST_SPELL", "side": side, "index": packet, "ticks": ticks,
                "count": count, "spell": spell, "target": target,
            })
        elif opcode == SMSG_CAST_FAILED_OPCODE:
            events.append({
                "opcode": "SMSG_CAST_FAILED", "side": side, "index": packet, "ticks": ticks,
                "count": payload[0], "spell": struct.unpack_from("<I", payload, 1)[0],
                "result": payload[5],
            })
        elif opcode == SMSG_SPELL_GO_OPCODE:
            caster, cursor = read_packed_guid(payload, 0)
            _, cursor = read_packed_guid(payload, cursor)
            events.append({
                "opcode": "SMSG_SPELL_GO", "side": side, "index": packet, "ticks": ticks,
                "caster": caster, "count": payload[cursor],
                "spell": struct.unpack_from("<I", payload, cursor + 1)[0],
            })
        elif opcode == CMSG_CANCEL_CAST_OPCODE:
            events.append({"opcode": "CMSG_CANCEL_CAST", "side": side, "index": packet,
                           "ticks": ticks})
        offset += PKT_PACKET_HEADER_SIZE + (length - 4)
        packet += 1
    return events


def unpack_pack_xyz(packed: int) -> tuple[float, float, float]:
    """One appendPackXYZ word: 11-bit x, 11-bit y, 10-bit z, each in 0.25 steps, sign-extended."""
    x = packed & 0x7FF
    y = (packed >> 11) & 0x7FF
    z = (packed >> 22) & 0x3FF
    if x & 0x400:
        x -= 0x800
    if y & 0x400:
        y -= 0x800
    if z & 0x200:
        z -= 0x400
    return x * 0.25, y * 0.25, z * 0.25


def plan32_monster_moves(generation: Generation) -> list[dict[str, object]]:
    """Every SMSG_ON_MONSTER_MOVE as (mover GUID, world polyline, packet index, arrival tick and
    advertised duration): the mover, its real position, then the spline points, uncompressed or
    PackXYZ-compressed (offsets from the start/end midpoint, as the server writes them). A stop
    (PacketBuilder::WriteStopMovement) has no points: it closes the running spline's executed
    portion at its own tick. Layout from MoveSplineInit + PacketBuilder."""
    path = Path(generation["paths"]["logs"]) / "packets.pkt"
    if not path.is_file():
        return []
    data = path.read_bytes()
    moves: list[dict[str, object]] = []
    offset = PKT_LOG_HEADER_SIZE
    packet = 0
    while offset + PKT_PACKET_HEADER_SIZE <= len(data):
        _, _, ticks, optional_size, length = struct.unpack_from("<IIIII", data, offset)
        if optional_size != 20 or length > 65535 or length < 4:
            raise RuntimeError(f"plan32 packet log desynchronised at byte {offset}")
        opcode = struct.unpack_from("<I", data, offset + 40)[0]
        payload = data[offset + 44:offset + 40 + length]
        if opcode == SMSG_ON_MONSTER_MOVE_OPCODE:
            cursor = 0
            mover, cursor = read_packed_guid(payload, cursor)
            cursor += 1  # VehicleExitVoluntary
            start = struct.unpack_from("<fff", payload, cursor)
            cursor += 12
            cursor += 4  # splineId
            facing = payload[cursor]
            cursor += 1
            facing_target = None
            duration = 0
            points: list[tuple[float, float, float]] = []
            stop = facing == 1
            if stop:
                # WriteStopMovement writes flags, duration and an empty path, with no
                # destination: the mover stands where the running spline left it.
                cursor += 4 + 4 + 4
            else:
                if facing == 2:
                    cursor += 12
                elif facing == 3:
                    # FacingInfo.target is a raw uint64, not a packed GUID. A chase faces its
                    # victim, so this independently corroborates who the mover is after.
                    facing_target = struct.unpack_from("<Q", payload, cursor)[0]
                    cursor += 8
                elif facing == 4:
                    cursor += 4
                elif facing != 0:
                    raise RuntimeError(f"plan32 unknown monster-move facing {facing}")
                flags = struct.unpack_from("<I", payload, cursor)[0]
                cursor += 4
                if flags & SPLINE_FLAG_CYCLIC:
                    raise RuntimeError("plan32 cyclic spline path is not supported")
                if flags & SPLINE_FLAG_ANIMATION:
                    cursor += 1 + 4
                duration = struct.unpack_from("<I", payload, cursor)[0]
                cursor += 4
                if flags & SPLINE_FLAG_PARABOLIC:
                    cursor += 4 + 4
                points = [start]
                if flags & SPLINE_FLAG_UNCOMPRESSED_PATH:
                    count = struct.unpack_from("<I", payload, cursor)[0]
                    cursor += 4
                    for _ in range(count):
                        points.append(struct.unpack_from("<fff", payload, cursor))
                        cursor += 12
                else:
                    last_idx = struct.unpack_from("<I", payload, cursor)[0]
                    cursor += 4
                    dest = struct.unpack_from("<fff", payload, cursor)
                    cursor += 12
                    if last_idx > 1:
                        middle = tuple((a + b) / 2.0 for a, b in zip(start, dest))
                        for _ in range(1, last_idx):
                            packed = struct.unpack_from("<I", payload, cursor)[0]
                            cursor += 4
                            off = unpack_pack_xyz(packed)
                            points.append(tuple(m - o for m, o in zip(middle, off)))
                    points.append(dest)
            moves.append({"mover": mover, "points": points, "facing_target": facing_target,
                          "index": packet, "ticks": ticks, "duration": duration, "stop": stop})
        offset += PKT_PACKET_HEADER_SIZE + (length - 4)
        packet += 1
    return moves


def plan32_attackerstate(generation: Generation) -> list[dict[str, object]]:
    """Every SMSG_ATTACKERSTATEUPDATE as (attacker, victim, damage, arrival tick): HitInfo, both
    packed GUIDs, then the full damage. Any outcome counts — even a miss proves melee engagement
    range — and the tick places the swing after the run leg it has to prove."""
    path = Path(generation["paths"]["logs"]) / "packets.pkt"
    if not path.is_file():
        return []
    data = path.read_bytes()
    swings: list[dict[str, object]] = []
    offset = PKT_LOG_HEADER_SIZE
    while offset + PKT_PACKET_HEADER_SIZE <= len(data):
        _, _, ticks, optional_size, length = struct.unpack_from("<IIIII", data, offset)
        if optional_size != 20 or length > 65535 or length < 4:
            raise RuntimeError(f"plan32 packet log desynchronised at byte {offset}")
        opcode = struct.unpack_from("<I", data, offset + 40)[0]
        payload = data[offset + 44:offset + 40 + length]
        if opcode == SMSG_ATTACKERSTATEUPDATE_OPCODE:
            cursor = 4  # HitInfo
            attacker, cursor = read_packed_guid(payload, cursor)
            victim, cursor = read_packed_guid(payload, cursor)
            damage = struct.unpack_from("<I", payload, cursor)[0]
            swings.append({"attacker": attacker, "victim": victim, "damage": damage,
                           "ticks": ticks})
        offset += PKT_PACKET_HEADER_SIZE + (length - 4)
    return swings


SMSG_UPDATE_OBJECT_OPCODE = 0x4715
CMSG_CANCEL_CAST_OPCODE = 0x115
# Object.h UpdateType: only the create forms carry a movement block and update fields, and a live
# unit arrives as CREATE_OBJECT2 while a world-entry batch uses CREATE_OBJECT.
UPDATE_TYPE_VALUES = 0
UPDATE_TYPE_CREATE_OBJECT = 1
UPDATE_TYPE_CREATE_OBJECT2 = 2
UPDATE_TYPE_OUT_OF_RANGE = 3
UPDATE_TYPE_NEAR = 4
# SharedDefines.h TypeID, the HighGuid::Unit tag in a GUID's top 16 bits, and the update field
# index of OBJECT_FIELD_ENTRY (ObjectGuid.h's field order).
TYPEID_UNIT = 3
HIGHGUID_UNIT = 0xF130
OBJECT_FIELD_ENTRY = 5
# The fixture is seeded at its pinned point and does not move before the first cast, so its create
# position has to reproduce that point closely; the derived-line pins are compared at 25 cm, which
# is far above the millimetre residuals the derivation shows and far below a different wall.
PLAN32_FIXTURE_POSITION_TOLERANCE = 0.5
PLAN32_PIN_TOLERANCE = 0.25


class _Plan32Bits:
    """MSB-first bit reader over a create block's bits section (ByteBuffer's bit order)."""

    def __init__(self, data: bytes, position: int) -> None:
        self.data = data
        self.position = position
        self.bit = 0

    def read_bit(self) -> int:
        if self.position >= len(self.data):
            raise RuntimeError("plan32 update object: bit read past the end of the packet")
        value = (self.data[self.position] >> (7 - self.bit)) & 1
        self.bit += 1
        if self.bit == 8:
            self.bit = 0
            self.position += 1
        return value

    def read_bits(self, count: int) -> int:
        value = 0
        for _ in range(count):
            value = (value << 1) | self.read_bit()
        return value

    def flush(self) -> None:
        if self.bit:
            self.bit = 0
            self.position += 1


class _Plan32Reader:
    """Little-endian cursor over a create block's data section."""

    def __init__(self, data: bytes, position: int = 0) -> None:
        self.data = data
        self.position = position

    def _need(self, count: int) -> None:
        if self.position + count > len(self.data):
            raise RuntimeError("plan32 update object: read past the end of the packet")

    def u8(self) -> int:
        self._need(1)
        value = self.data[self.position]
        self.position += 1
        return value

    def u32(self) -> int:
        self._need(4)
        value = struct.unpack_from("<I", self.data, self.position)[0]
        self.position += 4
        return value

    def f32(self) -> float:
        self._need(4)
        value = struct.unpack_from("<f", self.data, self.position)[0]
        self.position += 4
        return value

    def skip(self, count: int) -> None:
        self._need(count)
        self.position += count


def _plan32_byte_seq(reader: _Plan32Reader, guid: int, index: int) -> None:
    """WriteByteSeq writes a byte only when that GUID byte is non-zero; the block's own GUID says
    which positions were written."""
    if (guid >> (8 * index)) & 0xFF:
        reader.skip(1)


def _plan32_transport_bits(bits: _Plan32Bits) -> dict[str, object]:
    trans_guid = bits.read_bit() << (5 * 8)
    trans_guid |= bits.read_bit() << (0 * 8)
    trans_guid |= bits.read_bit() << (3 * 8)
    trans_guid |= bits.read_bit() << (6 * 8)
    trans_guid |= bits.read_bit() << (1 * 8)
    trans_guid |= bits.read_bit() << (4 * 8)
    trans_guid |= bits.read_bit() << (2 * 8)
    has_time2 = bool(bits.read_bit())
    trans_guid |= bits.read_bit() << (7 * 8)
    return {"guid": trans_guid, "has_time2": has_time2}


def _plan32_create_flags(bits: _Plan32Bits) -> dict[str, int]:
    """UpdateObject's own flag order, PauseTimes included: the 24-bit count is always written even
    when it is zero, so it is skipped rather than read only when a flag says so."""
    flags = {}
    for name in ("PlayerHoverAnim", "SupressedGreetings", "Rotation", "AnimKit", "CombatVictim",
                 "ThisIsYou", "Vehicle", "MovementUpdate"):
        flags[name] = bits.read_bit()
    bits.read_bits(24)
    for name in ("NoBirthAnim", "MovementTransport", "Stationary", "AreaTrigger", "EnablePortals",
                 "ServerTime"):
        flags[name] = bits.read_bit()
    return flags


def _plan32_spline_create_bits(bits: _Plan32Bits) -> dict[str, object]:
    if not bits.read_bit():
        return {"active": False, "raw_flags": 0, "path_size": 0}
    mode = bits.read_bits(2)
    parabolic_or_animation = bits.read_bit()
    path_size = bits.read_bits(22)
    facing_code = bits.read_bits(2)
    facing_guid = 0
    if facing_code == 2:
        for index in (4, 3, 7, 2, 6, 1, 0, 5):
            facing_guid |= bits.read_bit() << (8 * index)
    parabolic_starts = bits.read_bit()
    raw_flags = bits.read_bits(25)
    return {
        "active": True, "mode": mode, "path_size": path_size, "facing_code": facing_code,
        "facing_guid": facing_guid,
        "parabolic_starts": bool(parabolic_or_animation and parabolic_starts),
        "raw_flags": raw_flags,
    }


def _plan32_movement_bits(bits: _Plan32Bits) -> dict[str, object]:
    movement: dict[str, object] = {}
    flags0_absent = bits.read_bit()
    movement["orientation_absent"] = bits.read_bit()
    for _ in range(3):  # guid[7], guid[3], guid[2]
        bits.read_bit()
    if not flags0_absent:
        bits.read_bits(30)
    bits.read_bit()  # hasSpline && !IsPlayer
    movement["has_pitch"] = bits.read_bit() == 0
    has_spline = bits.read_bit()
    movement["has_fall_data"] = bits.read_bit()
    movement["has_spline_elevation"] = bits.read_bit() == 0
    bits.read_bit()  # guid[5]
    has_transport = bits.read_bit()
    bits.read_bit()  # HasTime, hardcoded 0; the time word is written anyway
    movement["has_spline"] = has_spline
    movement["has_transport"] = has_transport
    if has_transport:
        trans_guid = bits.read_bit() << 8
        movement["transport_time2"] = bool(bits.read_bit())
        for index in (4, 0, 6):
            trans_guid |= bits.read_bit() << (8 * index)
        bits.read_bit()  # Has VehicleRecID, hardcoded 0
        for index in (7, 5, 3, 2):
            trans_guid |= bits.read_bit() << (8 * index)
        movement["trans_guid"] = trans_guid
    bits.read_bit()  # guid[4]
    movement["spline"] = _plan32_spline_create_bits(bits) if has_spline else None
    bits.read_bit()  # guid[6]
    if movement["has_fall_data"]:
        movement["fall_direction"] = bool(bits.read_bit())
    bits.read_bit()  # guid[0]
    bits.read_bit()  # guid[1]
    bits.read_bit()  # HeightChangeFailed, hardcoded 0
    if not bits.read_bit():  # flags1 absent
        bits.read_bits(12)
    return movement


def _plan32_spline_data(reader: _Plan32Reader, info: dict[str, object] | None) -> None:
    """The spline's own data words, then the final destination and id that are written whether or
    not the spline was active."""
    if info is not None and info.get("active"):
        raw = int(info["raw_flags"])
        if raw & SPLINE_FLAG_PARABOLIC and info.get("parabolic_starts"):
            reader.skip(4)  # vertical acceleration
        reader.skip(4)  # time passed
        facing = raw & (SPLINE_FLAG_FINAL_POINT | SPLINE_FLAG_FINAL_TARGET | SPLINE_FLAG_FINAL_ANGLE)
        if facing == SPLINE_FLAG_FINAL_ANGLE:
            reader.skip(4)
        elif facing == SPLINE_FLAG_FINAL_TARGET:
            for index in (5, 3, 7, 1, 6, 4, 2, 0):
                _plan32_byte_seq(reader, int(info["facing_guid"]), index)
        reader.skip(12 * int(info["path_size"]))  # path nodes
        if facing == SPLINE_FLAG_FINAL_POINT:
            reader.skip(12)
        reader.skip(4)  # duration mod next
        reader.skip(4)  # duration
        if raw & (SPLINE_FLAG_PARABOLIC | SPLINE_FLAG_ANIMATION):
            reader.skip(4)  # effect start time
        reader.skip(4)  # duration mod
    reader.skip(12)  # final destination
    reader.skip(4)  # spline id


def _plan32_movement_data(reader: _Plan32Reader, guid: int,
                          movement: dict[str, object]) -> None:
    _plan32_byte_seq(reader, guid, 4)
    reader.skip(4)  # run back speed
    if movement["has_fall_data"]:
        if movement.get("fall_direction"):
            reader.skip(12)
        reader.skip(4)  # fall time
        reader.skip(4)  # fall z speed
    reader.skip(4)  # swim back speed
    if movement["has_spline_elevation"]:
        reader.skip(4)
    if movement["has_spline"]:
        _plan32_spline_data(reader, movement.get("spline"))  # type: ignore[arg-type]
    position_z = reader.f32()
    _plan32_byte_seq(reader, guid, 5)
    if movement["has_transport"]:
        trans_guid = int(movement.get("trans_guid", 0))
        _plan32_byte_seq(reader, trans_guid, 5)
        _plan32_byte_seq(reader, trans_guid, 7)
        reader.skip(4)  # transport time
        reader.skip(4)  # offset o
        if movement.get("transport_time2"):
            reader.skip(4)
        reader.skip(4)  # offset y
        reader.skip(4)  # offset x
        _plan32_byte_seq(reader, trans_guid, 3)
        reader.skip(4)  # offset z
        _plan32_byte_seq(reader, trans_guid, 0)
        reader.skip(1)  # seat
        _plan32_byte_seq(reader, trans_guid, 1)
        _plan32_byte_seq(reader, trans_guid, 6)
        _plan32_byte_seq(reader, trans_guid, 2)
        _plan32_byte_seq(reader, trans_guid, 4)
    position_x = reader.f32()
    reader.skip(4)  # pitch rate
    _plan32_byte_seq(reader, guid, 3)
    _plan32_byte_seq(reader, guid, 0)
    reader.skip(4)  # swim speed
    position_y = reader.f32()
    _plan32_byte_seq(reader, guid, 7)
    _plan32_byte_seq(reader, guid, 1)
    _plan32_byte_seq(reader, guid, 2)
    reader.skip(4)  # walk speed
    reader.skip(4)  # time
    reader.skip(4)  # turn rate
    _plan32_byte_seq(reader, guid, 6)
    reader.skip(4)  # flight speed
    orientation = None
    if not movement["orientation_absent"]:
        orientation = reader.f32()
    reader.skip(4)  # run speed
    if movement["has_pitch"]:
        reader.skip(4)
    reader.skip(4)  # flight back speed
    movement["position"] = (position_x, position_y, position_z)
    movement["orientation"] = orientation


def _plan32_movement(reader: _Plan32Reader, guid: int) -> dict[str, object]:
    """Object::BuildMovementUpdate: the bits section, a FlushBits, then the data section."""
    bits = _Plan32Bits(reader.data, reader.position)
    flags = _plan32_create_flags(bits)
    movement = _plan32_movement_bits(bits) if flags["MovementUpdate"] else {}
    transport = _plan32_transport_bits(bits) if flags["MovementTransport"] else None
    victim_guid = 0
    if flags["CombatVictim"]:
        for index in (2, 7, 0, 4, 5, 6, 1, 3):
            victim_guid |= bits.read_bit() << (8 * index)
    bits.flush()
    reader.position = bits.position
    if flags["MovementUpdate"]:
        _plan32_movement_data(reader, guid, movement)
    if flags["Vehicle"]:
        reader.skip(8)
    if transport is not None:
        trans_guid = int(transport["guid"])
        for index in (0, 5, 3):
            _plan32_byte_seq(reader, trans_guid, index)
        reader.skip(4)  # offset x
        for index in (4, 6, 1):
            _plan32_byte_seq(reader, trans_guid, index)
        reader.skip(4)  # transport time
        reader.skip(4)  # offset y
        for index in (2, 7):
            _plan32_byte_seq(reader, trans_guid, index)
        reader.skip(4)  # offset z
        reader.skip(1)  # seat
        reader.skip(4)  # offset o
        if transport["has_time2"]:
            reader.skip(4)
    if flags["Rotation"]:
        reader.skip(8)  # packed world rotation
    if flags["AreaTrigger"]:
        reader.skip(4 * 4 + 1 + 4 * 12)
    if flags["Stationary"]:
        reader.skip(4 * 4)
    if flags["CombatVictim"]:
        for index in (4, 0, 3, 5, 7, 6, 2, 1):
            _plan32_byte_seq(reader, victim_guid, index)
    if flags["ServerTime"]:
        reader.skip(4)
    return movement


def _plan32_values(reader: _Plan32Reader) -> dict[int, int]:
    """UpdateMask + UpdateFieldData: block count, one mask per block, then the set fields' values
    in index order. OBJECT_FIELD_ENTRY is what names the unit without trusting its GUID."""
    block_count = reader.u8()
    masks = [reader.u32() for _ in range(block_count)]
    fields: dict[int, int] = {}
    for block_index, mask in enumerate(masks):
        for bit in range(32):
            if mask & (1 << bit):
                fields[block_index * 32 + bit] = reader.u32()
    return fields


def _plan32_update_blocks(payload: bytes, packet: int) -> list[dict[str, object]]:
    reader = _Plan32Reader(payload, 2)  # map id (uint16)
    blocks: list[dict[str, object]] = []
    block_count = reader.u32()
    for _ in range(block_count):
        update_type = reader.u8()
        if update_type in (UPDATE_TYPE_OUT_OF_RANGE, UPDATE_TYPE_NEAR):
            count = reader.u32()
            for _ in range(count):
                _, reader.position = read_packed_guid(reader.data, reader.position)
            continue
        guid, reader.position = read_packed_guid(reader.data, reader.position)
        if update_type in (UPDATE_TYPE_CREATE_OBJECT, UPDATE_TYPE_CREATE_OBJECT2):
            type_id = reader.u8()
            movement = _plan32_movement(reader, guid)
            fields = _plan32_values(reader)
            blocks.append({
                "packet": packet, "update_type": update_type, "guid": guid, "type_id": type_id,
                "entry": fields.get(OBJECT_FIELD_ENTRY),
                "position": movement.get("position"), "orientation": movement.get("orientation"),
            })
        elif update_type == UPDATE_TYPE_VALUES:
            _plan32_values(reader)
        else:
            raise RuntimeError(f"plan32 update object: unknown update type {update_type}")
    if reader.position != len(payload):
        raise RuntimeError(
            f"plan32 update object: packet {packet} parsed {reader.position} of {len(payload)} "
            "bytes; the block walk is out of step with the server's writer"
        )
    return blocks


def plan32_object_creates(generation: Generation) -> list[dict[str, object]]:
    """Every created object in the run's SMSG_UPDATE_OBJECT packets, in packet order: GUID, TypeID,
    entry and create position. The whole capture is read and every update packet must end exactly
    where its declared block count says, so a desynchronised walk fails the verdict instead of
    producing a witness that merely looks right."""
    path = Path(generation["paths"]["logs"]) / "packets.pkt"
    if not path.is_file():
        return []
    data = path.read_bytes()
    creates: list[dict[str, object]] = []
    offset = PKT_LOG_HEADER_SIZE
    packet = 0
    while offset + PKT_PACKET_HEADER_SIZE <= len(data):
        _, _, _, optional_size, length = struct.unpack_from("<IIIII", data, offset)
        if optional_size != 20 or length > 65535 or length < 4:
            raise RuntimeError(f"plan32 packet log desynchronised at byte {offset}")
        opcode = struct.unpack_from("<I", data, offset + 40)[0]
        payload = data[offset + 44:offset + 40 + length]
        if opcode == SMSG_UPDATE_OBJECT_OPCODE:
            try:
                creates.extend(_plan32_update_blocks(payload, packet))
            except (IndexError, struct.error) as error:
                raise RuntimeError(
                    f"plan32 update object packet {packet} did not parse: {error}"
                ) from error
        offset += PKT_PACKET_HEADER_SIZE + (length - 4)
        packet += 1
    return creates


def plan32_fixture_witness(generation: Generation, target: int) -> dict[str, object] | None:
    """The selected unit as the server itself described it: the create block's TypeID, entry and
    position for the selected GUID, with the entry cross-checked against the GUID's own bits (the
    HighGuid::Unit tag plus the creature entry) so a wrong selection cannot pass as the fixture."""
    create = next(
        (block for block in plan32_object_creates(generation) if block["guid"] == target), None
    )
    if create is None:
        return None
    return {
        "guid": target,
        "type_id": create.get("type_id"),
        "entry": create.get("entry"),
        "entry_from_guid": (target >> 24) & 0xFFFFFF,
        "guid_high": (target >> 48) & 0xFFFF,
        "position": [float(component) for component in create["position"]]
        if create.get("position") else None,
        "orientation": create.get("orientation"),
    }


def plan32_polyline_fractions(points: list[object]) -> list[float]:
    """Each vertex's share of the polyline's own 3D length: MoveSpline walks its path at a
    constant speed, so time spent is length covered."""
    walked = [0.0]
    for first, second in zip(points, points[1:]):
        walked.append(walked[-1] + math.dist(first, second))
    total = walked[-1]
    return [value / total if total else 0.0 for value in walked]


def plan32_polyline_point(points: list[object], fraction: float) -> list[float] | None:
    """The polyline position at a share of its length, so a spline packet can be read at the
    moment a cast arrives instead of only at the destination it advertises."""
    if not points:
        return None
    shares = plan32_polyline_fractions(points)
    share = min(max(float(fraction), 0.0), 1.0)
    for index, value in enumerate(shares):
        if value >= share or index + 1 == len(points):
            if index == 0:
                return [float(component) for component in points[0]]
            previous = shares[index - 1]
            span = value - previous
            offset = (share - previous) / span if span else 0.0
            return [
                float(a) + (float(b) - float(a)) * offset
                for a, b in zip(points[index - 1], points[index])
            ]
    return [float(component) for component in points[-1]]


def plan32_polyline_span_shares(points: list[object],
                                first: float, second: float) -> list[tuple[float, list[float]]]:
    """The polyline's own vertices between two shares of its length as (share, position) pairs,
    both ends included: the executed portion of a spline cut short by the next packet for its
    mover, each point dated by its share (a share of a spline of known duration is the tick it was
    walked at, which is how the arrival in the path is told from a position the mover reached
    before the player ever stopped)."""
    if not points:
        return []
    shares = plan32_polyline_fractions(points)
    start = min(max(float(first), 0.0), 1.0)
    end = min(max(float(second), 0.0), 1.0)
    if end < start:
        return []
    span: list[tuple[float, list[float]]] = [(start, plan32_polyline_point(points, start))]
    span += [
        (share, [float(component) for component in point])
        for point, share in zip(points[1:], shares[1:]) if start < share < end
    ]
    span.append((end, plan32_polyline_point(points, end)))
    return [(share, point) for share, point in span if point is not None]


def plan32_segment_entry(first: list[float], second: list[float], first_ticks: int, second_ticks: int,
                         target: tuple[float, float], radius: float,
                         floor: int) -> dict[str, object] | None:
    """The first moment at or after ``floor`` at which a constant-speed walk from ``first``
    (reached at ``first_ticks``) to ``second`` (reached at ``second_ticks``) stands within
    ``radius`` of ``target`` in the ground plane, and where it stood, or None when it never does.
    A walk can enter the radius part-way, so the crossing is solved on the segment instead of
    sampled at the two ends; the point returned sits just inside the radius, so the arrival cannot
    flip on the boundary, while a share the floor pushes to the interval's own edge stays there -
    the contract is inclusive."""
    start = max(first_ticks, floor)
    if second_ticks < start:
        return None
    span = second_ticks - first_ticks
    offset = (first[0] - target[0], first[1] - target[1])
    move = (second[0] - first[0], second[1] - first[1])
    quadratic = move[0] * move[0] + move[1] * move[1]
    if quadratic == 0.0:
        # The mover stands on this segment in the ground plane, so it is in range the whole way or
        # never, and any share the floor allows is an arrival.
        if math.hypot(offset[0], offset[1]) > radius + PLAN32_CHASE_ARRIVAL_EPSILON:
            return None
        nearest, reach = 0.5, 0.5
    else:
        # The distance to the target is convex in the walk's own share, so the shares it is in
        # range at form one interval around the closest approach. Reading the crossing off that
        # minimum keeps a grazing pass (distance == radius) a contact rather than a rounding-error
        # miss, which the raw quadratic cannot promise near its discriminant's root.
        nearest = -(offset[0] * move[0] + offset[1] * move[1]) / quadratic
        minimum = math.hypot(offset[0] + nearest * move[0], offset[1] + nearest * move[1])
        if minimum > radius + PLAN32_CHASE_ARRIVAL_EPSILON:
            return None
        reach = math.sqrt(max(radius * radius - minimum * minimum, 0.0)) / math.sqrt(quadratic)
    entry = max(nearest - reach, (start - first_ticks) / span if span else 0.0, 0.0)
    if entry > min(nearest + reach, 1.0):
        return None
    entry = min(entry + 1e-6, nearest + reach, 1.0)
    return {
        "ticks": max(first_ticks + int(round(entry * span)), start),
        "point": [first[axis] + entry * (second[axis] - first[axis]) for axis in range(3)],
    }


def plan32_fixture_position_at(moves: list[dict[str, object]], position: list[float],
                               at_ticks: int) -> list[float]:
    """Where the fixture was at a server tick. Its create position stands before any spline and a
    spline packet only advertises where the mover will be, so the running spline is read at the
    tick; a stop freezes the position where that spline had carried it."""
    current = [float(component) for component in position]
    active: dict[str, object] | None = None

    def spline_point(move: dict[str, object], tick: int) -> list[float] | None:
        duration = int(move["duration"])
        share = (tick - int(move["ticks"])) / duration if duration else 1.0
        return plan32_polyline_point(move["points"], share)

    for move in moves:
        if int(move["ticks"]) > at_ticks:
            break
        if move.get("stop"):
            if active is not None:
                current = spline_point(active, int(move["ticks"])) or current
                active = None
        else:
            active = move
    if active is not None:
        current = spline_point(active, at_ticks) or current
    return [float(component) for component in current]


def plan32_cast_fixture_positions(generation: Generation, target: int) -> list[dict[str, object]]:
    """The fixture's position when each cast request to it arrives: its create position advanced
    through its own spline packets to the cast's arrival tick, interpolated along the spline that
    was running, so a cast made while the fixture walked is checked against where it actually was
    rather than against the destination the spline had advertised."""
    create = next(
        (block for block in plan32_object_creates(generation) if block["guid"] == target), None
    )
    if create is None or not create.get("position"):
        return []
    moves = [move for move in plan32_monster_moves(generation) if move["mover"] == target]
    position = [float(component) for component in create["position"]]
    return [
        {"count": event["count"],
         "position": plan32_fixture_position_at(moves, position, int(event["ticks"]))}
        for event in plan32_packet_events(generation)
        if event["opcode"] == "CMSG_CAST_SPELL" and event.get("target") == target
    ]


def plan32_player_cast_point(stage: str, samples: list[dict[str, object]]) -> list[float] | None:
    """Where the player was when it cast. The stages cast from a standing position: the LOS stages
    after their single walk tap (their sample list must be exactly the landing, the start and the
    stop, so nothing moved after it), the chase before its run legs, which the automation sends
    after the cast and which the sample list carries afterwards."""
    if not samples:
        return None
    sample = samples[0] if stage == "chase" else samples[-1]
    return [float(sample["x"]), float(sample["y"]), float(sample["z"])]


_PLAN32_GEOMETRY_MODULE: Any = None


def plan32_geometry_module() -> Any:
    """apps/cata/check_collision_geometry.py: the vmap reader that pinned the frozen scenario.

    Loaded by path, because this runner is a script and the tool is only in play for this mode. The
    rays and routes below then come from the same reader that derived the pins, rather than from a
    second implementation that could agree with a bug of its own.
    """
    global _PLAN32_GEOMETRY_MODULE
    if _PLAN32_GEOMETRY_MODULE is None:
        spec = importlib.util.spec_from_file_location(
            "plan32_collision_geometry", Path(__file__).with_name("check_collision_geometry.py"),
        )
        if spec is None or spec.loader is None:
            raise RuntimeError("check_collision_geometry.py is not next to this runner")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _PLAN32_GEOMETRY_MODULE = module
    return _PLAN32_GEOMETRY_MODULE


def plan32_geometry_facts(generation: Generation) -> dict[str, object]:
    """Whether the run really had the staged terrain, collision and navmesh data in play: the
    hashes of the copies staged for this run (recomputed here, so a staged copy that changed after
    prepare is caught even though the source root no longer matters - the terrain the height gate
    reads is hashed with them), the config the server was started with - including the data
    directory those copies live in - and the server's own support line. A verdict about collision
    data means nothing without all of them."""
    inputs = generation["inputs"]
    paths = generation.get("paths", {})
    data_dir = Path(str(paths.get("data", "")))
    config: dict[str, str | None] = {}
    config_path = Path(str(paths.get("world_config", "")))
    if config_path.is_file():
        text = config_path.read_text(errors="replace")
        for key in ("DataDir", "MoveMaps.Enable", "vmap.enableLOS", "vmap.enableHeight"):
            match = re.search(
                rf"^\s*{re.escape(key)}\s*=\s*\"?([^\"]+?)\"?\s*$", text, re.MULTILINE,
            )
            config[key] = match.group(1) if match else None
    hashes: dict[str, str | None] = {}
    for name in ("maps", "vmaps", "mmaps"):
        root = data_dir / name
        try:
            hashes[f"{name}_hash"] = tree_content_hash(root) if root.is_dir() else None
        except RuntimeError:
            hashes[f"{name}_hash"] = None
    support = None
    stdout_path = Path(str(paths.get("logs", ""))) / "worldserver.stdout.log"
    if stdout_path.is_file():
        for line in stdout_path.read_text(errors="replace").splitlines():
            if "VMap support included" in line:
                support = line.strip()
    return {
        "data_root": inputs.get("data_root"),
        "data_dir": str(data_dir),
        "maps_hash": hashes["maps_hash"],
        "vmaps_hash": hashes["vmaps_hash"],
        "mmaps_hash": hashes["mmaps_hash"],
        "config": config,
        "support_line": support,
    }


def plan32_terrain_problems(terrain: dict[str, object]) -> list[str]:
    """Every accepted movement sample against the staged map height under it: a sample the staged
    terrain cannot account for - no tile, or a height the walk never touched - is a problem, so a
    teleport or a vertical discontinuity inside a stage fails instead of riding along unseen."""
    tolerance = float(terrain.get("tolerance") or PLAN32_STAGE_TERRAIN_TOLERANCE)
    problems: list[str] = []
    for sample in terrain.get("samples") or []:
        opcode = str(sample.get("opcode"))
        if sample.get("delta") is None:
            problems.append(f"{opcode} has no staged terrain height under it")
        elif abs(float(sample["delta"])) > tolerance:
            problems.append(
                f"{opcode} stands {float(sample['delta']):.3f} m off the staged terrain"
            )
    return problems


def plan32_line_pin(line: dict[str, object], seed: list[float], eye: float) -> dict[str, float]:
    """Where a pinned seed sits on a derived line: the distance along it and the distance off it.

    The fixture seeds are points on the line where the spawn stands, not its far endpoint, and the
    derivation lifts both endpoints by the unit collision height, so the seed's own z is compared
    against the first endpoint's z minus that height.
    """
    first = [float(component) for component in line["first"]]
    second = [float(component) for component in line["second"]]
    length = math.hypot(second[0] - first[0], second[1] - first[1])
    ux, uy = (second[0] - first[0]) / length, (second[1] - first[1]) / length
    vx, vy = float(seed[0]) - first[0], float(seed[1]) - first[1]
    return {
        "along": vx * ux + vy * uy,
        "perpendicular": abs(vx * uy - vy * ux),
        "endpoint_z_delta": first[2] - eye - float(seed[2]),
    }


def plan32_route_facts(data_root: Path, start: list[float], end: list[float]) -> dict[str, object]:
    """The staged mmap's own answer for a walk between two pinned points: the polygon under each and
    the route through the tile's links, costed at the polygon centres (an upper bound, as the tool's
    own route check says). Used for the chase, where rounding the obstacle is the expectation. Both
    ends are full 3D points: the query's own frame reads all three axes (``nav_coord`` indexes z)."""
    module = plan32_geometry_module()
    gx, gy = module.grid_coord(start[0], start[1])
    mmap = module.MmapFiles(data_root / "mmaps" / f"{CHARACTER_MAP:03d}.mmap",
                            data_root / "mmaps" / f"{CHARACTER_MAP:03d}{gx:02d}{gy:02d}.mmtile")
    verts, polygons, adjacency, problems = module.navmesh_graph(mmap)
    if problems:
        raise RuntimeError(f"plan32 mmap tile did not parse: {problems}")
    _, graph = module.creature_graph(polygons, adjacency, module.NAV_GROUND)
    first = module.navmesh_polygon(verts, polygons, module.nav_coord(start), start[2])
    second = module.navmesh_polygon(verts, polygons, module.nav_coord(end), end[2])
    path, cost = module.navmesh_route(verts, polygons, graph, first, second)
    straight = math.hypot(end[0] - start[0], end[1] - start[1])
    return {
        "start_polygon": first,
        "end_polygon": second,
        "route": cost if path else None,
        "hops": len(path) if path else 0,
        "straight": straight,
        "ratio": (cost / straight) if path and straight else None,
    }


def plan32_stage_geometry(generation: Generation, stage: str) -> dict[str, object]:
    """Every static-geometry fact a stage's verdict rests on, from the run's own staged artifacts
    only (``paths.data``: the copy prepare hash-verified and pointed the server's ``DataDir`` at,
    never the source root it was copied from).

    Derived side: the pinned blocked/clear pair is re-derived from the staged vmaps, the stage's
    seeds are placed on it and, for the chase, the walk from the player's end point back around the
    obstacle is routed through the staged mmap. Runtime side: both units' positions at each
    evaluated cast (the player's from its accepted movement samples, the fixture's from its create
    block read along its own spline packets at the cast's tick) and the ray between them at eye
    height, plus the same ray shortened to a low point on the fixture, so a verdict cannot hinge on
    which height the implementations offset by. Each accepted movement sample is also held against
    the staged terrain it stands on. Returns an ``error`` key instead of raising when the artifacts
    cannot answer, so the failure is recorded in the evidence rather than lost in a traceback.
    """
    try:
        module = plan32_geometry_module()
        data_root = Path(str(generation["paths"]["data"]))
        eye = float(module.DEFAULT_COLLISION_HEIGHT)
        seeds = PLAN32_GEOMETRY_SEEDS[stage]
        derived = module.derive_los_pair(data_root)
        gx, gy = module.grid_coord(seeds["character"][0], seeds["character"][1])
        spawns = module.tile_spawns(data_root, CHARACTER_MAP, gx, gy, 1)
        models = module.ModelCache(data_root)

        def ray(first: list[float], second: list[float]) -> list[str]:
            found, _ = module.segment_hits(spawns, models, tuple(first), tuple(second))
            return [hit["name"] for hit in found]

        samples = ground_movement_samples(generation)
        terrain = module.TerrainReader(data_root, CHARACTER_MAP)
        terrain_heights = []
        for sample in samples:
            height = terrain.height(float(sample["x"]), float(sample["y"]))
            terrain_heights.append({
                "opcode": sample["opcode"],
                "delta": None if height is None else round(float(sample["z"]) - height, 4),
            })
        # A relog run's flat sample list carries two sessions: the cast point is the last sample of
        # the one that did the walking, not the position the next login landed on.
        cast_samples = plan32_relog_timeline(generation)["walk"] if stage == "relog" else samples
        player = plan32_player_cast_point(stage, cast_samples)
        target = selections_target_fixture(plan32_packet_events(generation))
        fixture_positions = {
            int(item["count"]): [float(c) for c in item["position"]]
            for item in (plan32_cast_fixture_positions(generation, target) if target else [])
        }
        casts: list[dict[str, object]] = []
        for count in PLAN32_EXPECTED_COUNTS[stage]:
            fixture = fixture_positions.get(count)
            if player is None or fixture is None:
                casts.append({"count": count, "player": player, "fixture": fixture})
                continue
            casts.append({
                "count": count,
                "player": player,
                "fixture": fixture,
                "eye_hits": ray([player[0], player[1], player[2] + eye],
                                [fixture[0], fixture[1], fixture[2] + eye]),
                "low_hits": ray([player[0], player[1], player[2] + eye],
                                [fixture[0], fixture[1], fixture[2] + 0.5]),
            })
        lines = {
            "blocked": {
                "first": [float(c) for c in derived["blocked"]["first"]],
                "second": [float(c) for c in derived["blocked"]["second"]],
                "length": derived["blocked"]["length"],
                "offset_from_wall": derived["blocked"].get("offset_from_wall"),
            },
            "clear": {
                "first": [float(c) for c in derived["clear"]["first"]],
                "second": [float(c) for c in derived["clear"]["second"]],
                "length": derived["clear"]["length"],
                "offset_from_wall": derived["clear"].get("offset_from_wall"),
            },
        }
        report: dict[str, object] = {
            "eye_height": eye,
            "obstacle": derived.get("obstacle"),
            "derived": lines,
            "seeds": {key: list(value) for key, value in seeds.items()},
            "casts": casts,
            "terrain": {"tolerance": PLAN32_STAGE_TERRAIN_TOLERANCE, "samples": terrain_heights},
        }
        if stage in ("blocked", "clear", "relog"):
            line = lines["clear" if stage == "relog" else stage]
            report["pins"] = {
                "character": plan32_line_pin(line, list(seeds["character"]), eye),
                "dummy": plan32_line_pin(line, list(seeds["dummy"]), eye),
            }
        if stage == "chase":
            end = [float(PLAN32_CHASE_P2[0]), float(PLAN32_CHASE_P2[1])]
            end_sample = next(
                (sample for sample in reversed(ground_movement_samples(generation))
                 if abs(float(sample["x"]) - end[0]) <= PLAN32_CHASE_END_TOLERANCE
                 and abs(float(sample["y"]) - end[1]) <= PLAN32_CHASE_END_TOLERANCE),
                None,
            )
            end_z = float(end_sample["z"]) if end_sample else float(seeds["character"][2])
            spawn = [float(seeds["dummy"][0]), float(seeds["dummy"][1])]
            fixture_hits = ray([spawn[0], spawn[1], float(seeds["dummy"][2]) + eye],
                               [end[0], end[1], end_z + eye])
            report["chase"] = {
                "pull_hits": ray([float(seeds["character"][0]), float(seeds["character"][1]),
                                  float(seeds["character"][2]) + eye],
                                 [spawn[0], spawn[1], float(seeds["dummy"][2]) + eye]),
                "fixture_to_end_hits": fixture_hits,
                "route": plan32_route_facts(
                    data_root, [spawn[0], spawn[1], float(seeds["dummy"][2])], [end[0], end[1], end_z],
                ),
            }
        return report
    except (RuntimeError, ValueError, KeyError, IndexError, OSError) as error:
        return {"error": f"{type(error).__name__}: {error}"}


def plan32_correlation_problems(stage: str, target: int,
                                events: list[dict[str, object]]) -> list[str]:
    """The client's own cast counter is what ties a request to the server's answer: every request's
    count must come back on its own failed/go outcome, no other outcome may appear, and no cancel
    may sit between a request and its answer, because that would mean the client gave up on a cast
    the run claims to have completed."""
    problems: list[str] = []
    expected = PLAN32_EXPECTED_COUNTS.get(stage, [])
    pending: dict[str, object] | None = None
    seen: list[object] = []
    for event in events:
        opcode = event["opcode"]
        if opcode == "CMSG_CAST_SPELL" and event.get("target") == target:
            if pending is not None:
                problems.append(f"cast {pending['count']} had no answer before the next request")
            pending = event
            seen.append(event.get("count"))
        elif opcode == "CMSG_CANCEL_CAST" and pending is not None:
            problems.append(f"cast {pending['count']} was cancelled before its answer")
        elif opcode in ("SMSG_CAST_FAILED", "SMSG_SPELL_GO") and pending is not None:
            if opcode == "SMSG_SPELL_GO" and event.get("caster") != CHARACTER_GUID:
                continue
            if event.get("spell") != PLAN32_GEOMETRY_SPELL_ID:
                continue
            if event.get("count") != pending.get("count"):
                problems.append(
                    f"cast {pending.get('count')} was answered with counter {event.get('count')}"
                )
            pending = None
    if pending is not None:
        problems.append(f"cast {pending['count']} had no answer")
    if seen != list(expected):
        problems.append(f"cast counters {seen} are not the expected {list(expected)}")
    return problems


def plan32_packet_log_bounds(generation: Generation) -> dict[str, object]:
    """The capture's own clock: the arrival tick of its last packet (how far the observation ran,
    the only honest end for an executed path), every client MSG_MOVE_START_FORWARD tick (the
    forward taps the automation sent; the chase has exactly one, its run leg) and every client
    strafe-stop tick (the run leg's last tap, after which the player stands at its end point). A
    stop keeps its packet's own index as well, because millisecond ticks do not order two packets
    within the same one, and whether the stop or a superseding move came first decides whether the
    mover still stood where it had been."""
    path = Path(generation["paths"]["logs"]) / "packets.pkt"
    if not path.is_file():
        return {"end_ticks": None, "forward_ticks": [], "stop_ticks": [], "stop_packets": []}
    data = path.read_bytes()
    end_ticks = None
    forward_ticks: list[int] = []
    stop_ticks: list[int] = []
    stop_packets: list[dict[str, int]] = []
    offset = PKT_LOG_HEADER_SIZE
    packet = 0
    while offset + PKT_PACKET_HEADER_SIZE <= len(data):
        direction, _, ticks, optional_size, length = struct.unpack_from("<IIIII", data, offset)
        if optional_size != 20 or length > 65535 or length < 4:
            raise RuntimeError(f"plan32 packet log desynchronised at byte {offset}")
        opcode = struct.unpack_from("<I", data, offset + 40)[0]
        end_ticks = ticks
        if direction == 0x47534D43 and opcode == MSG_MOVE_START_FORWARD_OPCODE:
            forward_ticks.append(ticks)
        if direction == 0x47534D43 and opcode == MSG_MOVE_STOP_STRAFE_OPCODE:
            stop_ticks.append(ticks)
            stop_packets.append({"ticks": ticks, "index": packet})
        offset += PKT_PACKET_HEADER_SIZE + (length - 4)
        packet += 1
    return {"end_ticks": end_ticks, "forward_ticks": forward_ticks, "stop_ticks": stop_ticks,
            "stop_packets": stop_packets}

def plan32_chase_tracks(generation: Generation) -> dict[str, object]:
    """The chase observation, keyed to the agreed selection: the fixture's spline packets reduced
    to the path they actually executed, and its melee swings at the player. Reparsing the capture
    here keeps the evidence literal free of ordering constraints; the file is small.

    Only executed movement counts, on a timeline the capture's own packet order builds. The
    observation starts at the client's own forward tap (the run leg): an approach spline before it
    is not the chase. Every spline is read only between the tick it started and the tick the next
    packet for that mover arrived (a repath supersedes the old forecast) and never past the
    capture's last packet, so a straight replan does not manufacture a detour and an advertised
    destination nobody reached cannot stand in for arrival. Those executed pieces are walked as
    one walk per straight segment - inside a spline the mover keeps a constant speed - and one
    hold wherever no spline runs: from the end of a spline to the next packet for the mover, and
    to the capture's end after the last one. A walk that starts further than
    PLAN32_CHASE_JOIN_TOLERANCE from the point the previous hold left the mover at is recorded in
    ``joins`` and left for the gap check as well, so a teleport fails the stage instead of passing
    as continuous movement.

    Arrival is the run's own end: the player's strafe stop is the last tap of the run leg, and
    until it lands the player is still on the way to the end point, so no distance to that point
    can end the observation - an approach spline overlapping the tap is not the chase reaching it.
    From the stop onward the path is cut at the first moment it is within arrival range of the
    player's final position, the arrival the run waits for: solved on the walk leg that enters the
    range - a pass that only grazes it counts, the boundary is inclusive - because a walk enters
    it part-way and the vertex after it is not the arrival, or read off the hold leg it stands on.
    A hold only lasts to the next packet for the mover: where that packet places it somewhere
    else, a later packet in the same millisecond has already moved it, and only the capture's
    packet order can say whether an arrival at the hold's last tick was still the mover's own. A
    fixture whose victim drops then walks home, and that return walk must not be folded into the
    path this mode calls the chase - nor may the jump that starts it fail the stage, which is why
    only the joins the retained path still contains count. ``arrival_ticks`` records when the
    arrival happened, and a swing only counts as engagement at or after it and after the player's
    stop: the pull's own melee hit, or a hit landed while the fixture was still on its way, proves
    nothing about the chase reaching melee range. Cutting at the arrival can only shorten the
    measured path, never lengthen it, so the detour ratio stays a floor rather than an artefact."""
    events = [
        event for event in plan32_packet_events(generation)
        if event["opcode"] in {
            "CMSG_SET_SELECTION", "CMSG_CAST_SPELL", "SMSG_CAST_FAILED", "SMSG_SPELL_GO",
        }
    ]
    target = selections_target_fixture(events)
    samples = ground_movement_samples(generation)
    arrival = (float(samples[-1]["x"]), float(samples[-1]["y"])) if samples else None
    bounds = plan32_packet_log_bounds(generation)
    forward_ticks = [int(tick) for tick in bounds["forward_ticks"]]
    run_ticks = forward_ticks[0] if len(forward_ticks) == 1 else None
    stop_packets = [stop for stop in bounds["stop_packets"]]
    final_stop = stop_packets[-1] if stop_packets else None
    final_stop_ticks = int(final_stop["ticks"]) if final_stop is not None else None
    end_ticks = bounds["end_ticks"]
    executed: list[list[float]] = []
    legs: list[dict[str, object]] = []
    joins: list[dict[str, object]] = []
    arrival_ticks: int | None = None
    if target is not None and run_ticks is not None and end_ticks is not None:
        moves = [move for move in plan32_monster_moves(generation) if move["mover"] == target]
        # The executed timeline, in the capture's own packet order: one walk per straight segment
        # of every spline the mover was really on, then one hold at the point that spline left it.
        spans: list[dict[str, object]] = []
        for index, move in enumerate(moves):
            if move.get("stop") or not move["points"]:
                continue
            duration = int(move["duration"])
            spline_start = int(move["ticks"])
            spline_end = spline_start + duration
            if spline_end < run_ticks:
                continue
            first_ticks = max(spline_start, run_ticks)
            last_ticks = min(spline_end, int(end_ticks))
            if index + 1 < len(moves):
                last_ticks = min(last_ticks, int(moves[index + 1]["ticks"]))
            if last_ticks < first_ticks:
                continue
            records = plan32_polyline_span_shares(
                move["points"],
                (first_ticks - spline_start) / duration if duration else 1.0,
                (last_ticks - spline_start) / duration if duration else 1.0,
            )
            spans.append({
                "points": [point for _, point in records],
                "ticks": [
                    min(max(int(round(spline_start + share * duration)), first_ticks), last_ticks)
                    for share, _ in records
                ],
                "last_ticks": last_ticks,
                "index": int(move["index"]),
            })
        # Whether a packet later than this span got in at ``tick`` before the player's own stop:
        # any of them owns that instant, even one that lands back on the old point, so the span's
        # own position there can only be dated by the leg the later packet itself starts. A chain
        # that walks the mover away and back therefore keeps its jumps instead of reviving the old
        # leg just because the position it returns to is within the join tolerance.
        def superseding_tick(position: int, tick: int) -> bool:
            stop_index = int(final_stop["index"]) if final_stop is not None else None
            for candidate in spans[position + 1:]:
                if int(candidate["ticks"][0]) != tick:
                    continue
                if stop_index is not None and int(candidate["index"]) > stop_index:
                    continue
                return True
            return False

        for position, span in enumerate(spans):
            points, ticks = span["points"], span["ticks"]
            following = spans[position + 1] if position + 1 < len(spans) else None
            # The span's own endpoint is the mover's position only while no packet at that tick got
            # in first; the same question decides the occupancy that starts there, whose last
            # instant is the next packet's own tick. Only where the player's stop got there ahead
            # of that packet, which the capture's own order says, does the position still stand.
            endpoint_superseded = superseding_tick(position, int(span["last_ticks"]))
            if position:
                join = math.dist(spans[position - 1]["points"][-1], points[0])
                if join > PLAN32_CHASE_JOIN_TOLERANCE:
                    joins.append({"ticks": ticks[0], "gap": round(join, 3), "leg": len(legs)})
            walk_legs = len(legs)
            for index in range(len(points) - 1):
                legs.append({"from": points[index], "to": points[index + 1],
                             "first_ticks": ticks[index], "last_ticks": ticks[index + 1]})
            if len(legs) > walk_legs:
                legs[-1]["end_exclusive"] = endpoint_superseded
            hold_end = following["ticks"][0] if following else int(end_ticks)
            if hold_end >= int(span["last_ticks"]):
                superseded = superseding_tick(position, hold_end)
                legs.append({"point": points[-1], "first_ticks": span["last_ticks"],
                             "last_ticks": hold_end, "exclusive": superseded})
        if arrival is not None:
            floor = final_stop_ticks if final_stop_ticks is not None else 0
            cut: tuple[int, list[float], int] | None = None
            for position, leg in enumerate(legs):
                if "from" in leg:
                    hit = plan32_segment_entry(
                        leg["from"], leg["to"], leg["first_ticks"], leg["last_ticks"],
                        arrival, PLAN32_CHASE_ARRIVAL_DISTANCE, floor,
                    )
                    if hit is None:
                        continue
                    if leg.get("end_exclusive") and math.dist(hit["point"], leg["to"]) < 1e-9:
                        continue
                    cut = (position, hit["point"], max(int(hit["ticks"]), floor))
                else:
                    moment = max(int(leg["first_ticks"]), floor)
                    if moment > int(leg["last_ticks"]):
                        continue
                    if moment == int(leg["last_ticks"]) and leg["exclusive"]:
                        continue
                    if math.hypot(
                        leg["point"][0] - arrival[0], leg["point"][1] - arrival[1],
                    ) > PLAN32_CHASE_ARRIVAL_DISTANCE + PLAN32_CHASE_ARRIVAL_EPSILON:
                        continue
                    cut = (position, leg["point"], moment)
                break
            for position, leg in enumerate(legs):
                if cut is not None and position > cut[0]:
                    break
                if "from" in leg:
                    points = [leg["from"]]
                    points.append(cut[1] if cut is not None and position == cut[0] else leg["to"])
                else:
                    points = [cut[1] if cut is not None and position == cut[0] else leg["point"]]
                for point in points:
                    if not executed or any(a != b for a, b in zip(executed[-1], point)):
                        executed.append([float(value) for value in point])
            if cut is not None:
                arrival_ticks = cut[2]
                # A jump the retained path no longer contains is outside the observation: the
                # return walk that follows the arrival is not part of the chase, so it must not
                # fail the stage either - only the joins the retained path still carries do.
                joins = [join for join in joins if int(join["leg"]) <= cut[0]]
    if arrival_ticks is None and legs:
        arrival_ticks = int(legs[-1]["last_ticks"])
    engagement_ticks = max(final_stop_ticks or 0, arrival_ticks or 0)
    swings = [
        {"attacker": swing["attacker"], "victim": swing["victim"], "damage": swing["damage"],
         "ticks": swing["ticks"]}
        for swing in plan32_attackerstate(generation)
        if swing["attacker"] == target and swing["victim"] == CHARACTER_GUID
        and int(swing["ticks"]) >= engagement_ticks
    ] if target is not None else []
    return {"target": target, "executed": executed, "joins": joins, "swings": swings,
            "run_ticks": run_ticks, "final_stop_ticks": final_stop_ticks,
            "arrival_ticks": arrival_ticks}


def plan32_path_metrics(points: list[tuple[float, float, float]]) -> tuple[float, float, float]:
    """(polyline length, straight distance, max consecutive gap) after dropping points closer than
    PLAN32_CHASE_JOIN_TOLERANCE: repathed splines overlap, and the overlap must not inflate the
    length. The length and the straight distance are horizontal - the detour is a ground route -
    while the gaps are 3D, so a vertical teleport between two executed points is a gap rather than
    invisible."""
    simplified = [points[0]]
    for point in points[1:]:
        if math.dist(point, simplified[-1]) >= PLAN32_CHASE_JOIN_TOLERANCE:
            simplified.append(point)
    length = sum(
        math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(simplified, simplified[1:])
    )
    straight = math.hypot(
        simplified[-1][0] - simplified[0][0], simplified[-1][1] - simplified[0][1],
    )
    gaps = [math.dist(a, b) for a, b in zip(simplified, simplified[1:])]
    return length, straight, max(gaps) if gaps else 0.0


def ground_movement_action_samples(
    samples: list[dict[str, str | float]],
) -> list[dict[str, str | float]]:
    """The six automated key pairs, without the client's own world-entry landing.

    The client settles once onto the ground right after world entry and emits MSG_MOVE_FALL_LAND of
    its own before the automation presses anything - the same world-entry landing the jump/fall/land
    and walk/run-switch checks drop. Only that one marker-adjacent sample is excused, so the exact
    opcode list and all six directional pairs still have to hold and a landing anywhere else remains
    a failure rather than being sliced away with the actions.
    """
    if samples and str(samples[0]["opcode"]) == "MSG_MOVE_FALL_LAND":
        return samples[1:]
    return samples


def _normalized_angle_delta(delta: float) -> float:
    return (delta + math.pi) % (2 * math.pi) - math.pi


def ground_movement_deltas_are_action_appropriate(samples: list[dict[str, str | float]]) -> bool:
    """Each START opcode is paired with its terminating STOP opcode; verify the position/orientation
    delta between them moved in the direction implied by the key that was pressed (forward/backward
    along the facing vector, strafe along the perpendicular vector, turn changing orientation the
    expected way), rather than merely checking that some movement was logged."""
    epsilon = 0.05
    for index in range(0, len(samples) - 1, 2):
        start, stop = samples[index], samples[index + 1]
        dx = float(stop["x"]) - float(start["x"])
        dy = float(stop["y"]) - float(start["y"])
        orientation = float(start["o"])
        forward = (math.cos(orientation), math.sin(orientation))
        left = (-math.sin(orientation), math.cos(orientation))
        opcode = start["opcode"]
        if opcode == "MSG_MOVE_START_FORWARD":
            ok = dx * forward[0] + dy * forward[1] > epsilon
        elif opcode == "MSG_MOVE_START_BACKWARD":
            ok = dx * forward[0] + dy * forward[1] < -epsilon
        elif opcode == "MSG_MOVE_START_STRAFE_LEFT":
            ok = dx * left[0] + dy * left[1] > epsilon
        elif opcode == "MSG_MOVE_START_STRAFE_RIGHT":
            ok = dx * left[0] + dy * left[1] < -epsilon
        elif opcode == "MSG_MOVE_START_TURN_LEFT":
            ok = _normalized_angle_delta(float(stop["o"]) - orientation) > epsilon
        elif opcode == "MSG_MOVE_START_TURN_RIGHT":
            ok = _normalized_angle_delta(float(stop["o"]) - orientation) < -epsilon
        else:
            ok = False
        if not ok:
            return False
    return True


def jump_fall_land_action_pair(samples: list[dict[str, str | float]]) -> list[dict[str, str | float]]:
    """The client settles onto the ground right after world entry, which emits its own
    MSG_MOVE_FALL_LAND before the synthetic space-bar press; only the trailing JUMP/FALL_LAND
    pair is the automated action under test."""
    return samples[-2:]


def jump_fall_land_deltas_are_action_appropriate(samples: list[dict[str, str | float]]) -> bool:
    """A vertical in-place jump should land back close to where it started: little horizontal
    drift and a similar height, evidence the character returned to solid ground rather than
    falling through or teleporting."""
    pair = jump_fall_land_action_pair(samples)
    if len(pair) != 2:
        return False
    jump, land = pair
    if jump["opcode"] != "MSG_MOVE_JUMP" or land["opcode"] != "MSG_MOVE_FALL_LAND":
        return False
    tolerance = 2.0
    dx = float(land["x"]) - float(jump["x"])
    dy = float(land["y"]) - float(jump["y"])
    dz = float(land["z"]) - float(jump["z"])
    return math.hypot(dx, dy) < tolerance and abs(dz) < tolerance


def jump_fall_land_is_stable_after_landing(generation: Generation, samples: list[dict[str, str | float]]) -> bool:
    """A client standing idle after landing may send zero heartbeats (unlike sustained ground
    movement, heartbeats are not emitted while nothing is moving); any heartbeats that do appear
    must still match the landing position, but their absence is not itself evidence of drift."""
    if not samples:
        return False
    final = samples[-1]
    text = world_log_text(generation)
    after_final = text.rpartition(GROUND_MOVEMENT_MARKER)[2]
    heartbeats = HEARTBEAT_SAMPLE_PATTERN.findall(after_final)
    tolerance = 1e-3
    return all(
        math.isclose(float(x), float(final["x"]), abs_tol=tolerance)
        and math.isclose(float(y), float(final["y"]), abs_tol=tolerance)
        and math.isclose(float(z), float(final["z"]), abs_tol=tolerance)
        and math.isclose(float(o), float(final["o"]), abs_tol=tolerance)
        for x, y, z, o in heartbeats
    )


def ground_movement_is_stable_after_final_stop(generation: Generation, samples: list[dict[str, str | float]]) -> bool:
    """The real client only sends MSG_MOVE_HEARTBEAT while a movement flag is active, not while
    idle - confirmed by observing zero heartbeats for 12+ seconds after a real final stop. So the
    absence of any post-stop heartbeat is normal, not a failure; only a heartbeat that drifted away
    from the position/orientation the final STOP left the character at indicates instability."""
    if not samples:
        return False
    final = samples[-1]
    text = world_log_text(generation)
    after_final_stop = text.rpartition(GROUND_MOVEMENT_MARKER)[2]
    heartbeats = HEARTBEAT_SAMPLE_PATTERN.findall(after_final_stop)
    tolerance = 1e-3
    return all(
        math.isclose(float(x), float(final["x"]), abs_tol=tolerance)
        and math.isclose(float(y), float(final["y"]), abs_tol=tolerance)
        and math.isclose(float(z), float(final["z"]), abs_tol=tolerance)
        and math.isclose(float(o), float(final["o"]), abs_tol=tolerance)
        for x, y, z, o in heartbeats
    )


WALK_RUN_MODE_SWITCH_OPCODES = (
    "MSG_MOVE_SET_WALK_MODE", "MSG_MOVE_START_FORWARD", "MSG_MOVE_STOP",
    "MSG_MOVE_SET_RUN_MODE", "MSG_MOVE_START_FORWARD", "MSG_MOVE_STOP",
)


def walk_run_mode_switch_deltas_are_action_appropriate(samples: list[dict[str, str | float]]) -> bool:
    """Both legs hold the same forward key for the same duration; if walk mode genuinely
    reduced movement speed, the walk leg must cover materially less ground than the run leg
    rather than merely having its MSG_MOVE_SET_WALK_MODE/MSG_MOVE_SET_RUN_MODE toggles accepted."""
    if [sample["opcode"] for sample in samples] != list(WALK_RUN_MODE_SWITCH_OPCODES):
        return False
    walk_start, walk_stop, run_start, run_stop = samples[1], samples[2], samples[4], samples[5]
    walk_dx, walk_dy = float(walk_stop["x"]) - float(walk_start["x"]), float(walk_stop["y"]) - float(walk_start["y"])
    run_dx, run_dy = float(run_stop["x"]) - float(run_start["x"]), float(run_stop["y"]) - float(run_start["y"])
    walk_distance, run_distance = math.hypot(walk_dx, walk_dy), math.hypot(run_dx, run_dy)
    return walk_distance > 0.05 and run_distance > walk_distance * 1.5


TARGET_TARGETED_SPELL_CAST_SEQUENCE = re.compile(
    r"\bCMSG_SET_SELECTION\b.*?\bCMSG_CAST_SPELL\b.*?\bCMSG_CANCEL_CAST\b.*?"
    r"\bSMSG_CAST_FAILED\b.*?\bCMSG_CAST_SPELL\b.*?\bSMSG_SPELL_GO\b",
    re.DOTALL,
)
PLAN32_CAST_PACKET = re.compile(r"\bC->S:\s+.*?\bCMSG_CAST_SPELL\b")


def plan32_cast_marker_count(generation: Generation) -> int:
    after_map = world_log_text(generation).partition("Finished object update bootstrap after adding to map")[2]
    after_sync = after_map.partition(IN_WORLD_CONTROL_MARKER)[2]
    return len(PLAN32_CAST_PACKET.findall(after_sync))


def plan32_selection_count(generation: Generation) -> int:
    """How many selections the server has logged since the in-world marker: the live signal the
    chase's name macro produced, which the run can wait on (the first keypresses after the client
    comes up can be lost to the window-focus handover)."""
    after_map = world_log_text(generation).partition("Finished object update bootstrap after adding to map")[2]
    after_sync = after_map.partition(IN_WORLD_CONTROL_MARKER)[2]
    return len(re.findall(r"\bC->S:\s+.*?\bCMSG_SET_SELECTION\b", after_sync))


def plan32_progress_marker_count(generation: Generation) -> int:
    """What tells the run the mode's own actions have begun, which starts its stability hold: the
    casts of the casting stages. The relog stage casts nothing — its walk is the action under test
    — so its own accepted movement samples are the signal, and the hold then covers the logout and
    the return the automation drives."""
    if generation.get("plan32_stage") == "relog":
        return len(ground_movement_samples(generation))
    return plan32_cast_marker_count(generation)


def plan32_settle_z(generation: Generation) -> float | None:
    """The z of the first post-marker movement sample: the walk tap's START carries the spawn
    position (after any world-entry settle), so this is the runtime height at the pinned point
    even though the tap then walks away from it."""
    samples = ground_movement_samples(generation)
    return float(samples[0]["z"]) if samples else None


def selections_target_fixture(events: list[dict[str, object]]) -> int | None:
    """The single unit the automation selected by name.

    Static spawns run under server-assigned map-local lows (grid loading creates units with
    GenerateLowGuid, keeping the DB guid only as spawnId), so the fixture's wire GUID is not
    knowable a priori. Identity binds instead to the one selection the /target macro sends:
    every selection present must agree, and every cast must name that same unit. A differing
    second selection means the client retargeted on its own and fails the run.
    """
    selections = [
        event.get("target") for event in events if event.get("opcode") == "CMSG_SET_SELECTION"
    ]
    if not selections or any(target != selections[-1] for target in selections):
        return None
    return selections[-1]


def target_targeted_spell_cast_marker_count(generation: Generation) -> int:
    after_map = world_log_text(generation).partition("Finished object update bootstrap after adding to map")[2]
    after_sync = after_map.partition(IN_WORLD_CONTROL_MARKER)[2]
    return len(TARGET_TARGETED_SPELL_CAST_SEQUENCE.findall(after_sync))


INVALID_TARGET_SPELL_CAST_SEQUENCE = re.compile(r"\bC->S:\s+.*?\bCMSG_SET_SELECTION\b")


def invalid_target_spell_cast_marker_count(generation: Generation) -> int:
    # Proves the "/target" automation actually ran (a real self-selection reached the server);
    # the negative case itself is the *absence* of any CMSG_CAST_SPELL/SMSG_SPELL_GO/SMSG_CAST_FAILED
    # afterwards - see INVALID_TARGET_SPELL_CAST_MODE.
    after_map = world_log_text(generation).partition("Finished object update bootstrap after adding to map")[2]
    after_sync = after_map.partition(IN_WORLD_CONTROL_MARKER)[2]
    return len(INVALID_TARGET_SPELL_CAST_SEQUENCE.findall(after_sync))


LEVEL_UP_XP_PACKET = re.compile(r"\bS->C:\s+.*?\bSMSG_LOG_XPGAIN\b")
LEVEL_UP_INFO_PACKET = re.compile(r"\bS->C:\s+.*?\bSMSG_LEVELUP_INFO\b")


def level_up_settle_signal(generation: Generation) -> int:
    """Drives when the run may end.

    This must NOT be the level-up marker itself: SMSG_LEVELUP_INFO only appears when the run
    succeeds, so using it as the post-marker counter means a failed attempt never sets
    post_marker_hold_started and the loop runs to the full --timeout - long after the client is
    dead, with the harness's client-exit check defeated by the still-running Wine processes. Once
    the automation has finished (or the level-up happened) the scene is settled and the run can end
    after the normal stability window.
    """
    if generation.get("level_up_automation_done") or level_up_marker_count(generation):
        return 1
    return 0


def level_up_marker_count(generation: Generation) -> int:
    """Counts the level-up itself: SMSG_LEVELUP_INFO is only sent by Player::GiveLevel, so seeing it
    after the in-world sync marker means a real XP award crossed the threshold on the wire."""
    after_map = world_log_text(generation).partition("Finished object update bootstrap after adding to map")[2]
    after_sync = after_map.partition(IN_WORLD_CONTROL_MARKER)[2]
    return len(LEVEL_UP_INFO_PACKET.findall(after_sync))


def level_up_xp_gain_count(generation: Generation) -> int:
    after_map = world_log_text(generation).partition("Finished object update bootstrap after adding to map")[2]
    after_sync = after_map.partition(IN_WORLD_CONTROL_MARKER)[2]
    return len(LEVEL_UP_XP_PACKET.findall(after_sync))


POST_MARKER_MODES = frozenset({
    INITIAL_POST_LOAD_PACKETS_MODE, MAP_INSERTION_MODE, IN_WORLD_CONTROL_MODE, BASIC_MOVEMENT_MODE, RUN_SPEED_MODE,
    GROUND_MOVEMENT_MODE, JUMP_FALL_LAND_MODE, WALK_RUN_MODE_SWITCH_MODE, CHARACTER_CREATION_MODE,
    BASIC_SPELL_CAST_MODE, TARGET_TARGETED_SPELL_CAST_MODE, CHAT_MESSAGE_MODE, INVALID_TARGET_SPELL_CAST_MODE,
    LEVEL_UP_MODE, PLAN32_GEOMETRY_MODE, VENDOR_TRADE_MODE, VENDOR_NEGATIVE_MODE, TRAINER_MODE,
    MAGE_COMBAT_MODE, MAGE_FOLLOWUP_MODE,
})
POST_MARKER_COUNTERS = {
    INITIAL_POST_LOAD_PACKETS_MODE: initial_packets_marker_count,
    MAP_INSERTION_MODE: map_insertion_marker_count,
    IN_WORLD_CONTROL_MODE: in_world_control_marker_count,
    BASIC_MOVEMENT_MODE: movement_heartbeat_count,
    RUN_SPEED_MODE: lambda generation: len(run_speed_acknowledgements(generation)),
    GROUND_MOVEMENT_MODE: ground_movement_marker_count,
    JUMP_FALL_LAND_MODE: ground_movement_marker_count,
    WALK_RUN_MODE_SWITCH_MODE: ground_movement_marker_count,
    CHARACTER_CREATION_MODE: character_creation_marker_count,
    BASIC_SPELL_CAST_MODE: spell_cast_marker_count,
    TARGET_TARGETED_SPELL_CAST_MODE: target_targeted_spell_cast_marker_count,
    CHAT_MESSAGE_MODE: chat_message_marker_count,
    VENDOR_TRADE_MODE: lambda generation: vendor_trade_marker_count(
        generation, "s2c", "SMSG_VENDOR_INVENTORY"),
    VENDOR_NEGATIVE_MODE: lambda generation: vendor_trade_marker_count(
        generation, "s2c", "SMSG_VENDOR_INVENTORY"),
    TRAINER_MODE: lambda generation: vendor_trade_marker_count(generation, "s2c", "SMSG_TRAINER_LIST"),
    MAGE_COMBAT_MODE: character_creation_marker_count,
    MAGE_FOLLOWUP_MODE: character_creation_marker_count,
    INVALID_TARGET_SPELL_CAST_MODE: invalid_target_spell_cast_marker_count,
    LEVEL_UP_MODE: level_up_settle_signal,
    PLAN32_GEOMETRY_MODE: plan32_progress_marker_count,
}


def player_login_diagnostic(generation: Generation) -> str:
    text = world_log_text(generation)
    callback = text.rfind("Player login callback for account ")
    if callback < 0:
        return "not-observed"
    later = text[callback:]
    if "Player::LoadFromDB failed" in later:
        return "load-returned-false"
    if "logged in with character" in later:
        return "load-returned-true"
    return "not-observed"


def selection_proof_is_complete(
    selection: dict[str, object] | None, transcript: list[dict[str, str]],
    enum_events: list[dict[str, int]], logins: int = 1, enumerations: int | None = None,
) -> bool:
    """The selections, enumerations and logins the fixture character has to produce.

    `enumerations` defaults to `logins`; vendor mode logs in once and then returns to the character
    screen, whose own list refresh adds one more enumeration.
    """
    expected_enumerations = logins if enumerations is None else enumerations
    expected_enum = {
        "account_id": ACCOUNT_ID, "guid_low": CHARACTER_GUID, "list_position": CHARACTER_LIST_POSITION,
    }
    return (
        enum_events == [expected_enum] * expected_enumerations
        and selection == {
            "action": "enter",
            "seeded_guid_low": CHARACTER_GUID,
            "enumerated_guid_low": CHARACTER_GUID,
            "request_guid_low": CHARACTER_GUID,
            "callback_guid_low": CHARACTER_GUID,
            "legit_characters_admission": True,
        }
        and sum(item == {"direction": "c2s", "opcode": "CMSG_PLAYER_LOGIN"} for item in transcript) == logins
    )


def owned_established_lines(generation: Generation) -> list[str]:
    path = Path(generation["paths"]["raw_evidence"]) / "network.log"
    if not path.is_file():
        return []
    lines = path.read_text(errors="replace").splitlines()
    wine_pids = [str(item["pid"]) for item in generation["processes"] if item["kind"] == "wine"]
    return [
        line for line in lines
        if "ESTAB" in line and any(f"pid={pid}," in line for pid in wine_pids)
    ]


def endpoint_evidence(generation: Generation) -> bool:
    owned = owned_established_lines(generation)
    ports = (AUTH_PORT, generation["ports"]["world"])
    if not all(any(len(line.split()) > 4 and line.split()[4].endswith(f":{port}") for line in owned) for port in ports):
        return False
    allowed = {f":{port}" for port in ports}
    return all(len(line.split()) > 4 and any(line.split()[4].endswith(suffix) for suffix in allowed) for line in owned)


def protected_inputs_unchanged(manifest: Manifest, generation: Generation) -> bool:
    inputs = generation["inputs"]
    baseline = manifest["baseline"]
    client_root = Path(str(inputs["client_root"]))
    personal_bottle = Path(str(inputs["personal_bottle"]))
    base_record = manifest.get("client_base")
    base_unchanged = not base_record or base_record.get("purged", False) or (
        tree_metadata(Path(str(base_record["path"]))) == base_record["tree"]
    )
    return base_unchanged and (
        sha256(client_root / "Wow-64.exe") == baseline["client_sha256"]
        and tree_metadata(client_root) == baseline["client_tree"]
        and tree_metadata(personal_bottle) == baseline["personal_bottle_tree"]
    )


def purge_client_base(manifest: Manifest) -> None:
    record = manifest.get("client_base")
    if not record or record.get("purged", False):
        return
    if any(generation["state"] != "reset" for generation in manifest["generations"]):
        raise RuntimeError("all Plan 7 generations must be reset before purging the cached client")
    base = Path(str(record["path"]))
    expected = (Path(manifest["root"]) / "client-base").resolve()
    if base.resolve() != expected or tree_metadata(base) != record["tree"]:
        raise RuntimeError("refusing to purge a changed or unowned cached client base")
    make_tree_writable(base)
    shutil.rmtree(base)
    record["purged"] = True


def purge_database_cache(manifest_path: Path, manifest: Manifest) -> None:
    record = manifest.get("database_cache")
    if not record:
        return
    if any(generation["state"] != "reset" for generation in manifest["generations"]):
        raise RuntimeError("all Plan 7 generations must be reset before purging the database cache")
    path = Path(str(record["path"]))
    if path != database_cache_path(manifest):
        raise RuntimeError("refusing to purge a database cache outside the manifest root")
    if record.get("state") != "purging":
        record["state"] = "purging"
        save_manifest(manifest_path, manifest)
    remove_database_cache_files(manifest)
    manifest.pop("database_cache")
    save_manifest(manifest_path, manifest)


def sanitized_evidence(
    generation: Generation, milestones: list[str], transcript: list[dict[str, str]],
    *, character_rows: int | None = None, realm_count: int | None = None,
    enumerated: dict[str, object] | None = None, screen_confirmed: bool = False,
    selection: dict[str, object] | None = None, downstream_diagnostic: str | None = None,
    initial_packet_prefix_value: list[str] | None = None, pre_map_marker_count: int | None = None,
    map_insertion_prefix_value: list[str] | None = None, map_insertion_marker_count_value: int | None = None,
    in_world_control_prefix_value: list[str] | None = None, in_world_control_marker_count_value: int | None = None,
    movement_heartbeat_count_value: int | None = None, creation_marker_count_value: int | None = None,
    vendor_trade_evidence_value: dict[str, int] | None = None,
    vendor_negative_evidence_value: dict[str, object] | None = None,
    trainer_evidence_value: dict[str, int] | None = None,
    mage_combat_evidence_value: dict[str, int] | None = None,
    mage_followup_evidence_value: dict[str, int] | None = None,
    login_callback_count_value: int | None = None,
    logout_count_value: int | None = None,
) -> dict[str, object]:
    auth_index = next(
        (index for index, item in enumerate(transcript) if item["opcode"] == "SMSG_AUTH_RESPONSE"), None,
    )
    candidate = None
    if auth_index is not None:
        candidate = next(
            (item["opcode"] for item in transcript[auth_index + 1:] if item["direction"] == "s2c"), None,
        )
    allowlisted = [
        item for item in transcript
        if item["opcode"] in {
            "SMSG_AUTH_CHALLENGE", "CMSG_AUTH_SESSION", "SMSG_AUTH_RESPONSE",
            "CMSG_CHAR_ENUM", "SMSG_CHAR_ENUM", "SMSG_TUTORIAL_FLAGS", "SMSG_CLIENTCACHE_VERSION",
        }
    ]
    character_mode = generation["mode"] in CHARACTER_MODES
    populated_mode = generation["mode"] in POPULATED_CHARACTER_MODES
    selection_mode = generation["mode"] == CHARACTER_SELECTION_MODE
    initial_packets_mode = generation["mode"] == INITIAL_POST_LOAD_PACKETS_MODE
    map_insertion_mode = generation["mode"] == MAP_INSERTION_MODE
    run_speed_mode = generation["mode"] == RUN_SPEED_MODE
    creation_mode = generation["mode"] in {CHARACTER_CREATION_MODE, *MAGE_MODES}
    mage_combat_mode = generation["mode"] == MAGE_COMBAT_MODE
    mage_followup_mode = generation["mode"] == MAGE_FOLLOWUP_MODE
    basic_movement_mode = generation["mode"] in {BASIC_MOVEMENT_MODE, RUN_SPEED_MODE}
    ground_movement_mode = generation["mode"] == GROUND_MOVEMENT_MODE
    jump_fall_land_mode = generation["mode"] == JUMP_FALL_LAND_MODE
    walk_run_mode_switch_mode = generation["mode"] == WALK_RUN_MODE_SWITCH_MODE
    basic_spell_cast_mode = generation["mode"] == BASIC_SPELL_CAST_MODE
    target_targeted_spell_cast_mode = generation["mode"] == TARGET_TARGETED_SPELL_CAST_MODE
    chat_message_mode = generation["mode"] == CHAT_MESSAGE_MODE
    invalid_target_spell_cast_mode = generation["mode"] == INVALID_TARGET_SPELL_CAST_MODE
    level_up_mode = generation["mode"] == LEVEL_UP_MODE
    plan32_geometry_mode = generation["mode"] == PLAN32_GEOMETRY_MODE
    vendor_trade_mode = generation["mode"] == VENDOR_TRADE_MODE
    vendor_negative_mode = generation["mode"] == VENDOR_NEGATIVE_MODE
    trainer_mode = generation["mode"] == TRAINER_MODE
    vendor_marker_mode = vendor_trade_mode or vendor_negative_mode or trainer_mode
    in_world_control_mode = generation["mode"] in {
        IN_WORLD_CONTROL_MODE, BASIC_MOVEMENT_MODE, RUN_SPEED_MODE, GROUND_MOVEMENT_MODE, JUMP_FALL_LAND_MODE,
        WALK_RUN_MODE_SWITCH_MODE, BASIC_SPELL_CAST_MODE, TARGET_TARGETED_SPELL_CAST_MODE, CHAT_MESSAGE_MODE,
        INVALID_TARGET_SPELL_CAST_MODE, LEVEL_UP_MODE, PLAN32_GEOMETRY_MODE, VENDOR_TRADE_MODE,
        VENDOR_NEGATIVE_MODE, TRAINER_MODE,
    }
    login_mode = selection_mode or initial_packets_mode or map_insertion_mode or in_world_control_mode or creation_mode
    owned_window = owned_window_evidence(generation) if character_mode else (
        Path(generation["paths"]["raw_evidence"]) / "window.xprop"
    ).is_file()
    allowed_opcodes = set()
    if login_mode:
        allowed_opcodes.add("CMSG_PLAYER_LOGIN")
    if creation_mode:
        allowed_opcodes.add("CMSG_CHAR_CREATE")
    forbidden_opcodes = FORBIDDEN_CHARACTER_OPCODES - allowed_opcodes
    forbidden = sorted({item["opcode"] for item in transcript if item["opcode"] in forbidden_opcodes})
    # Parsed once: the cast correlation, the fixture witness and the geometry report all read the
    # same capture, and the evidence records what they read.
    plan32_events = plan32_packet_events(generation) if (
        plan32_geometry_mode or trainer_mode or mage_combat_mode or mage_followup_mode) else []
    vendor_events = vendor_trade_packet_events(generation) if vendor_marker_mode else []
    vendor_sells = [event for event in vendor_events if event["opcode"] == "CMSG_SELL_ITEM"]
    vendor_buybacks = [event for event in vendor_events if event["opcode"] == "CMSG_BUYBACK_ITEM"]
    plan32_target = selections_target_fixture(plan32_events) if plan32_geometry_mode else None
    return {
        "schema": 1,
        "build": CLIENT_BUILD,
        "mode": generation["mode"],
        "outcome": (
            "character_creation_candidate" if creation_mode and "characters_completed" in milestones
            else "run_speed_change_candidate" if run_speed_mode and "characters_completed" in milestones
            else "basic_spell_cast_pass_candidate" if basic_spell_cast_mode and "characters_completed" in milestones
            else "target_targeted_spell_cast_pass_candidate" if target_targeted_spell_cast_mode
            and "characters_completed" in milestones
            else "chat_message_pass_candidate" if chat_message_mode and "characters_completed" in milestones
            else "invalid_target_spell_cast_pass_candidate" if invalid_target_spell_cast_mode
            and "characters_completed" in milestones
            else "walk_run_mode_switch_pass_candidate" if walk_run_mode_switch_mode
            and "characters_completed" in milestones
            else "jump_fall_land_pass_candidate" if jump_fall_land_mode and "characters_completed" in milestones
            else "ground_movement_pass_candidate" if ground_movement_mode and "characters_completed" in milestones
            else "vendor_trade_pass_candidate" if vendor_trade_mode and "characters_completed" in milestones
            else "vendor_negative_pass_candidate" if vendor_negative_mode and "characters_completed" in milestones
            else "trainer_learn_pass_candidate" if trainer_mode and "characters_completed" in milestones
            else "mage_combat_pass_candidate" if mage_combat_mode and "characters_completed" in milestones
            else "mage_combat_followup_pass_candidate" if mage_followup_mode
            and "characters_completed" in milestones
            else "plan32_geometry_pass_candidate" if plan32_geometry_mode and "characters_completed" in milestones
            else "basic_movement_pass_candidate" if basic_movement_mode and "characters_completed" in milestones
            else "in_world_control_bootstrap_candidate" if in_world_control_mode and "characters_completed" in milestones
            else "map_insertion_object_bootstrap_candidate" if map_insertion_mode and "characters_completed" in milestones
            else "initial_post_load_packets_candidate" if initial_packets_mode and "characters_completed" in milestones
            else "character_selection_candidate" if selection_mode and "characters_completed" in milestones
            else "populated_character_list_candidate" if populated_mode and "characters_completed" in milestones
            else "character_screen_candidate" if character_mode and "characters_completed" in milestones
            else "world_auth_ok" if "world_auth_ok" in milestones else "not_accepted"
        ),
        "client_milestones": milestones,
        "server_milestones": allowlisted,
        "endpoint_ownership": endpoint_evidence(generation),
        "network_scope": "owned-auth-and-world-only" if endpoint_evidence(generation) else "inconclusive",
        "owned_window": owned_window,
        "screen_confirmed": screen_confirmed if character_mode else None,
        "character_rows": character_rows if character_mode else None,
        "realm_character_count": realm_count if populated_mode else None,
        "enumerated": enumerated if populated_mode else None,
        "selection": selection if login_mode else None,
        "downstream_diagnostic": downstream_diagnostic if selection_mode else None,
        "pre_map_packet_prefix": initial_packet_prefix_value if initial_packets_mode else None,
        "pre_map_marker_count": pre_map_marker_count if initial_packets_mode else None,
        "map_insertion_packet_prefix": map_insertion_prefix_value if map_insertion_mode else None,
        "map_insertion_marker_count": map_insertion_marker_count_value if map_insertion_mode else None,
        "in_world_control_packet_prefix": in_world_control_prefix_value if in_world_control_mode else None,
        "in_world_control_marker_count": in_world_control_marker_count_value if in_world_control_mode else None,
        "movement_heartbeat_count": movement_heartbeat_count_value if basic_movement_mode else None,
        "vendor_inventory_count": (
            vendor_trade_marker_count(generation, "s2c", "SMSG_VENDOR_INVENTORY") if vendor_marker_mode else None
        ),
        "vendor_buy_requests": (
            vendor_trade_marker_count(generation, "c2s", "CMSG_BUY_ITEM") if vendor_marker_mode else None
        ),
        "vendor_buy_results": (
            vendor_trade_marker_count(generation, "s2c", "SMSG_BUY_ITEM") if vendor_marker_mode else None
        ),
        "vendor_buy_failures": (
            vendor_trade_marker_count(generation, "s2c", "SMSG_BUY_FAILED") if vendor_marker_mode else None
        ),
        "vendor_sell_requests": (
            vendor_trade_marker_count(generation, "c2s", "CMSG_SELL_ITEM") if vendor_marker_mode else None
        ),
        "vendor_buyback_requests": (
            vendor_trade_marker_count(generation, "c2s", "CMSG_BUYBACK_ITEM") if vendor_marker_mode else None
        ),
        "vendor_repair_requests": (
            vendor_trade_marker_count(generation, "c2s", "CMSG_REPAIR_ITEM") if vendor_marker_mode else None
        ),
        "vendor_trade": vendor_trade_evidence_value if vendor_trade_mode else None,
        "vendor_inventory_failures": (
            vendor_trade_marker_count(generation, "s2c", "SMSG_INVENTORY_CHANGE_FAILURE")
            if vendor_negative_mode else None
        ),
        "vendor_buy_requests_detail": (
            [{"item": int(event["item"]), "slot": int(event["slot"]), "count": int(event["count"])}
             for event in vendor_events if event["opcode"] == "CMSG_BUY_ITEM"]
            if vendor_negative_mode else None
        ),
        "vendor_buy_failed_items": (
            [int(event["item"]) for event in vendor_events if event["opcode"] == "SMSG_BUY_FAILED"]
            if vendor_negative_mode else None
        ),
        "vendor_buy_failure_reasons": (
            sorted({int(event["result"]) for event in vendor_events if event["opcode"] == "SMSG_BUY_FAILED"})
            if vendor_negative_mode else None
        ),
        "vendor_inventory_failure_reasons": (
            sorted({int(event["result"]) for event in vendor_events
                    if event["opcode"] == "SMSG_INVENTORY_CHANGE_FAILURE"})
            if vendor_negative_mode else None
        ),
        "vendor_case": generation.get("vendor_case") if vendor_negative_mode else None,
        "trainer_list": (
            next((event for event in vendor_events if event["opcode"] == "SMSG_TRAINER_LIST"), None)
            if trainer_mode else None
        ),
        "trainer_buy_events": (
            [{"trainer_guid": int(event["trainer_guid"]), "trainer_id": int(event["trainer_id"]),
              "spell": int(event["spell"]), "index": int(event["index"])}
             for event in vendor_events if event["opcode"] == "CMSG_TRAINER_BUY_SPELL"]
            if trainer_mode else None
        ),
        "trainer_results": (
            [{"trainer_guid": int(event["trainer_guid"]), "spell": int(event["spell"]),
              "index": int(event["index"])}
             for event in vendor_events if event["opcode"] == "SMSG_TRAINER_BUY_SUCCEEDED"]
            if trainer_mode else None
        ),
        "trainer_learned_spells": (
            [{"spell": int(event["spell"]), "index": int(event["index"])}
             for event in vendor_events if event["opcode"] == "SMSG_LEARNED_SPELL"]
            if trainer_mode else None
        ),
        "trainer_buy_results": (
            vendor_trade_marker_count(generation, "s2c", "SMSG_TRAINER_BUY_SUCCEEDED")
            if trainer_mode else None
        ),
        "trainer_buy_failures": (
            vendor_trade_marker_count(generation, "s2c", "SMSG_TRAINER_BUY_FAILED")
            if trainer_mode else None
        ),
        "trainer": trainer_evidence_value if trainer_mode else None,
        "mage_combat": mage_combat_evidence_value if mage_combat_mode else None,
        "mage_followup": mage_followup_evidence_value if mage_followup_mode else None,
        "login_callback_count": login_callback_count_value,
        "logout_count": logout_count_value,
        "combat_events": (
            combat_packet_events(generation) if mage_combat_mode or mage_followup_mode else None
        ),
        "vendor_negative": vendor_negative_evidence_value if vendor_negative_mode else None,
        "vendor_sell_item_counter": vendor_sells[0]["item_counter"] if vendor_sells else None,
        "vendor_buyback_slot": vendor_buybacks[0]["slot"] if vendor_buybacks else None,
        "run_speed_acknowledgements": run_speed_acknowledgements(generation) if run_speed_mode else None,
        "ground_movement_sequence": ground_movement_sequence(generation) if ground_movement_mode else None,
        "jump_fall_land_sequence": ground_movement_sequence(generation) if jump_fall_land_mode else None,
        "walk_run_mode_switch_sequence": (
            ground_movement_sequence(generation) if walk_run_mode_switch_mode else None
        ),
        "spell_cast_marker_count": (
            spell_cast_marker_count(generation)
            if basic_spell_cast_mode or target_targeted_spell_cast_mode or invalid_target_spell_cast_mode else None
        ),
        "cast_failed_marker_count": (
            cast_failed_marker_count(generation)
            if target_targeted_spell_cast_mode or invalid_target_spell_cast_mode else None
        ),
        "target_targeted_spell_cast_sequence_count": (
            target_targeted_spell_cast_marker_count(generation) if target_targeted_spell_cast_mode else None
        ),
        "chat_message_marker_count": chat_message_marker_count(generation) if chat_message_mode else None,
        "level_up_marker_count": level_up_marker_count(generation) if level_up_mode else None,
        "level_up_xp_gain_count": level_up_xp_gain_count(generation) if level_up_mode else None,
        "invalid_target_spell_cast_sequence_count": (
            invalid_target_spell_cast_marker_count(generation) if invalid_target_spell_cast_mode else None
        ),
        "plan32_stage": generation.get("plan32_stage") if plan32_geometry_mode else None,
        "plan32_casts": (
            [
                {key: event[key] for key in ("opcode", "side", "count", "spell", "target", "result", "caster", "index")
                 if key in event}
                for event in plan32_events
                if event["opcode"] in {
                    "CMSG_SET_SELECTION", "CMSG_CAST_SPELL", "SMSG_CAST_FAILED", "SMSG_SPELL_GO",
                }
            ] if (plan32_geometry_mode or trainer_mode or mage_combat_mode or mage_followup_mode) else None
        ),
        "plan32_settle_z": plan32_settle_z(generation) if plan32_geometry_mode else None,
        "plan32_player_samples": (
            ground_movement_samples(generation) if plan32_geometry_mode else None
        ),
        "plan32_fixture": (
            plan32_fixture_witness(generation, plan32_target)
            if plan32_geometry_mode and plan32_target else None
        ),
        "plan32_geometry_facts": plan32_geometry_facts(generation) if plan32_geometry_mode else None,
        "plan32_geometry": (
            plan32_stage_geometry(generation, str(generation.get("plan32_stage")))
            if plan32_geometry_mode and generation.get("plan32_stage") in PLAN32_GEOMETRY_SEEDS
            else None
        ),
        "plan32_chase": (
            plan32_chase_tracks(generation)
            if plan32_geometry_mode and generation.get("plan32_stage") == "chase"
            else None
        ),
        "plan32_relog_problems": (
            plan32_relog_problems(generation)
            if plan32_geometry_mode and generation.get("plan32_stage") == "relog"
            else None
        ),
        "creation_marker_count": creation_marker_count_value if creation_mode else None,
        "post_marker_hold_seconds": (
            generation.get("post_marker_hold_seconds", 0) if generation["mode"] in POST_MARKER_MODES else None
        ),
        "post_marker_snapshots": (
            generation.get("post_marker_snapshots", 0) if generation["mode"] in POST_MARKER_MODES else None
        ),
        "empty_response_payload": EMPTY_CHARACTER_ENUM_PAYLOAD if generation["mode"] == "character-screen" else None,
        "response_body": POPULATED_CHARACTER_ENUM_PAYLOAD if populated_mode else None,
        "stability_seconds": generation.get("stability_seconds", 0) if character_mode else None,
        "forbidden_opcodes": forbidden if character_mode else [],
        "next_server_packet": {
            "opcode": candidate or "unknown",
            "status": "candidate/inconclusive" if candidate else "not-observed/inconclusive",
        },
        "payload_shape": "typed-auth-success; no raw payload retained",
        "inputs": {
            "client_sha256": CLIENT_SHA256,
            "authserver_sha256": generation["inputs"]["authserver"]["sha256"],
            "worldserver_sha256": generation["inputs"]["worldserver"]["sha256"],
            "dbc_tree_sha256": generation["inputs"]["data_dbc_hash"],
            "maps_tree_sha256": generation["inputs"]["data_maps_hash"],
            # Geometry is part of the identity: without these a repeatability comparison cannot tell
            # a collision-enabled run from a geometry-free one, or notice either artifact set
            # changing. Older geometry-free evidence projects to False with empty hashes.
            "geometry": bool(generation["inputs"].get("geometry")),
            "vmaps_tree_sha256": generation["inputs"].get("data_vmaps_hash", ""),
            "mmaps_tree_sha256": generation["inputs"].get("data_mmaps_hash", ""),
        },
    }


def mage_combat_accepted(evidence: dict[str, object]) -> bool:
    """The cast chain: request -> mana drop -> spell go -> fire damage, bound to the created mage."""
    mage = evidence["mage_combat"] or {}
    player_guid = int(mage.get("guid", 0))
    combat = evidence["combat_events"] or []
    casts = evidence["plan32_casts"] or []
    requests = [
        event for event in casts
        if event.get("opcode") == "CMSG_CAST_SPELL"
        and int(event.get("spell", 0)) == MAGE_COMBAT_SPELL_ID
    ]
    request = requests[-1] if requests else None
    request_index = int(request["index"]) if request else -1
    request_target = int(request.get("target", 0)) if request else 0
    selections = [
        event for event in casts
        if event.get("opcode") == "CMSG_SET_SELECTION" and int(event.get("index", 0)) < request_index
    ]
    selection = selections[-1] if selections else None
    spell_goes = [
        event for event in casts
        if event.get("opcode") == "SMSG_SPELL_GO"
        and int(event.get("caster", 0)) == player_guid
        and int(event.get("spell", 0)) == MAGE_COMBAT_SPELL_ID
        and int(event.get("index", 0)) > request_index
    ]
    go_index = int(spell_goes[0]["index"]) if spell_goes else -1
    damage = [
        event for event in combat
        if event["opcode"] == "SMSG_SPELLNONMELEEDAMAGELOG"
        and int(event["attacker"]) == player_guid
        and int(event["spell"]) == MAGE_COMBAT_SPELL_ID
        and int(event["target"]) == request_target
        and int(event["damage"]) >= 1
        and int(event["school"]) == 4
        and int(event["index"]) > go_index
    ]
    mana = [
        (int(event["index"]), int(power[1])) for event in combat
        if event["opcode"] == "SMSG_POWER_UPDATE" and int(event["guid"]) == player_guid
        for power in event["powers"] if int(power[0]) == 0
    ]
    # the cast must cost mana: compare the last value before the request with the first one the
    # cast produces (before the damage log). Login/creation initialization is excluded.
    mana_before = [value for index, value in mana if index < request_index]
    damage_index = int(damage[0]["index"]) if damage else -1
    mana_after = [value for index, value in mana if request_index < index < damage_index]
    return (
        player_guid != 0
        and int(mage.get("class", 0)) == CREATION_CLASSES["mage"][1]
        and int(mage.get("race", 0)) == CHARACTER_RACE
        and int(mage.get("action_count", 0)) == 1
        and request is not None
        and request_target != 0
        and selection is not None
        and int(selection.get("target", 0)) == request_target
        and ((request_target >> 24) & 0xFFFFFF) == MAGE_COMBAT_TARGET_ENTRY
        and bool(spell_goes)
        and bool(damage)
        and bool(mana_before)
        and bool(mana_after)
        and mana_after[0] < mana_before[-1]
    )

def mage_followup_accepted(evidence: dict[str, object]) -> bool:
    """Cast, out-of-combat regeneration, logout/relog and a second cast, bound to the created mage."""
    mage = evidence["mage_followup"] or {}
    player_guid = int(mage.get("guid", 0))
    casts = evidence["plan32_casts"] or []
    requests = [event for event in casts
                if event.get("opcode") == "CMSG_CAST_SPELL"
                and int(event.get("spell", 0)) == MAGE_COMBAT_SPELL_ID]
    spell_goes = [event for event in casts
                  if event.get("opcode") == "SMSG_SPELL_GO"
                  and int(event.get("caster", 0)) == player_guid
                  and int(event.get("spell", 0)) == MAGE_COMBAT_SPELL_ID]
    mana = [(int(event["index"]), int(power[1])) for event in (evidence["combat_events"] or [])
            if event["opcode"] == "SMSG_POWER_UPDATE" and int(event["guid"]) == player_guid
            for power in event["powers"] if int(power[0]) == 0]
    if len(requests) < 2 or len(spell_goes) < 2:
        return False
    first_index = int(requests[0]["index"])
    second_index = int(requests[1]["index"])
    before_first = [value for index, value in mana if index < first_index]
    after_first = [value for index, value in mana if first_index < index < second_index]
    after_second = [value for index, value in mana if index > second_index]
    return (
        player_guid != 0
        and int(mage.get("class", 0)) == CREATION_CLASSES["mage"][1]
        and int(mage.get("race", 0)) == CHARACTER_RACE
        and int(mage.get("action_count", 0)) == 1
        # starting spells are re-granted by Player::LoadFromDB -> LearnDefaultSkills(), so
        # character_spell is empty for a fresh mage; the second cast after the relog is the witness
        and int(spell_goes[0]["index"]) > first_index
        and int(spell_goes[1]["index"]) > second_index
        and bool(before_first) and bool(after_first)
        and after_first[0] < before_first[-1]          # the first cast cost mana
        and max(after_first) > after_first[0]          # out of combat, mana came back
        and bool(after_second) and after_second[0] < max(after_first)  # the second cast cost mana
        and int(evidence.get("login_callback_count") or 0) == 2
        and int(evidence.get("logout_count") or 0) >= 1
    )


def verify(args: argparse.Namespace) -> None:
    manifest_path = args.manifest.resolve()
    manifest = load_manifest(manifest_path)
    generation = active_generation(manifest)
    apply_creation_class(generation["mode"], generation["inputs"].get("create_class", "warrior"))
    if generation["state"] not in {"observed", "accepted", "inconclusive"}:
        raise RuntimeError(f"cannot verify from state {generation['state']}")
    raw_log = Path(generation["paths"]["raw_evidence"]) / "connection.log"
    text = raw_log.read_text(errors="replace") if raw_log.is_file() else ""
    milestone_definition = CHARACTER_MILESTONES if generation["mode"] in CHARACTER_MODES else CLIENT_MILESTONES
    milestones = matched_milestones(text, milestone_definition)
    transcript = server_transcript(generation)
    character_mode = generation["mode"] in CHARACTER_MODES
    populated_mode = generation["mode"] in POPULATED_CHARACTER_MODES
    selection_mode = generation["mode"] == CHARACTER_SELECTION_MODE
    initial_packets_mode = generation["mode"] == INITIAL_POST_LOAD_PACKETS_MODE
    map_insertion_mode = generation["mode"] == MAP_INSERTION_MODE
    run_speed_mode = generation["mode"] == RUN_SPEED_MODE
    creation_mode = generation["mode"] in {CHARACTER_CREATION_MODE, *MAGE_MODES}
    mage_combat_mode = generation["mode"] == MAGE_COMBAT_MODE
    mage_followup_mode = generation["mode"] == MAGE_FOLLOWUP_MODE
    basic_movement_mode = generation["mode"] in {BASIC_MOVEMENT_MODE, RUN_SPEED_MODE}
    ground_movement_mode = generation["mode"] == GROUND_MOVEMENT_MODE
    jump_fall_land_mode = generation["mode"] == JUMP_FALL_LAND_MODE
    walk_run_mode_switch_mode = generation["mode"] == WALK_RUN_MODE_SWITCH_MODE
    basic_spell_cast_mode = generation["mode"] == BASIC_SPELL_CAST_MODE
    target_targeted_spell_cast_mode = generation["mode"] == TARGET_TARGETED_SPELL_CAST_MODE
    chat_message_mode = generation["mode"] == CHAT_MESSAGE_MODE
    invalid_target_spell_cast_mode = generation["mode"] == INVALID_TARGET_SPELL_CAST_MODE
    level_up_mode = generation["mode"] == LEVEL_UP_MODE
    plan32_geometry_mode = generation["mode"] == PLAN32_GEOMETRY_MODE
    vendor_trade_mode = generation["mode"] == VENDOR_TRADE_MODE
    vendor_negative_mode = generation["mode"] == VENDOR_NEGATIVE_MODE
    trainer_mode = generation["mode"] == TRAINER_MODE
    vendor_marker_mode = vendor_trade_mode or vendor_negative_mode or trainer_mode
    in_world_control_mode = generation["mode"] in {
        IN_WORLD_CONTROL_MODE, BASIC_MOVEMENT_MODE, RUN_SPEED_MODE, GROUND_MOVEMENT_MODE, JUMP_FALL_LAND_MODE,
        WALK_RUN_MODE_SWITCH_MODE, BASIC_SPELL_CAST_MODE, TARGET_TARGETED_SPELL_CAST_MODE, CHAT_MESSAGE_MODE,
        INVALID_TARGET_SPELL_CAST_MODE, LEVEL_UP_MODE, PLAN32_GEOMETRY_MODE, VENDOR_TRADE_MODE,
        VENDOR_NEGATIVE_MODE, TRAINER_MODE,
    }
    login_mode = selection_mode or initial_packets_mode or map_insertion_mode or in_world_control_mode or creation_mode
    # The relog stage drives the whole login twice, so the login-shaped evidence below expects two
    # complete sessions that both name the same seeded character.
    expected_logins = 2 if (
        (plan32_geometry_mode and generation.get("plan32_stage") == "relog") or mage_followup_mode
    ) else 1
    # Returning to the character screen after /logout makes the client ask for its character list
    # again (CharacterSelect_OnShow calls GetCharacterListUpdate), so vendor mode sees one
    # enumeration per session plus that closing one while still logging in exactly once.
    expected_enumerations = 2 if (vendor_trade_mode or vendor_negative_mode or trainer_mode) else expected_logins
    rows = character_row_count(manifest, generation) if character_mode else None
    realm_count = realm_character_count(manifest, generation) if populated_mode else None
    seed = (
        verify_populated_character_identity(manifest, generation) if login_mode
        else verify_populated_character_seed(manifest, generation) if populated_mode else None
    )
    enum_events = enumerated_characters(generation) if populated_mode else []
    enumerated = None
    if seed is not None:
        enumerated = seed
    requests = player_login_requests(generation) if login_mode else []
    callbacks = player_login_callbacks(generation) if login_mode else []
    selection = {
        "action": generation.get("selection_action"),
        "seeded_guid_low": seed["guid_low"] if seed is not None else None,
        "enumerated_guid_low": enum_events[0]["guid_low"] if enum_events and len(set(
            event["guid_low"] for event in enum_events)) == 1 else None,
        "request_guid_low": (
            requests[0] if len(requests) == expected_logins and len(set(requests)) == 1 else None
        ),
        "callback_guid_low": (
            callbacks[0] if len(callbacks) == expected_logins and len(set(callbacks)) == 1 else None
        ),
        "legit_characters_admission": (
            len(callbacks) == expected_logins and len(set(callbacks)) == 1
        ),
    } if login_mode else None
    evidence = sanitized_evidence(
        generation, milestones, transcript, character_rows=rows, realm_count=realm_count, enumerated=enumerated,
        screen_confirmed=bool(args.confirm_expected_screen), selection=selection,
        downstream_diagnostic=player_login_diagnostic(generation) if selection_mode else None,
        initial_packet_prefix_value=initial_packet_prefix(generation) if initial_packets_mode else None,
        pre_map_marker_count=initial_packets_marker_count(generation) if initial_packets_mode else None,
        map_insertion_prefix_value=map_insertion_packet_prefix(generation) if map_insertion_mode else None,
        map_insertion_marker_count_value=map_insertion_marker_count(generation) if map_insertion_mode else None,
        in_world_control_prefix_value=(
            in_world_control_packet_prefix(generation) if in_world_control_mode else None
        ),
        in_world_control_marker_count_value=(
            in_world_control_marker_count(generation) if in_world_control_mode else None
        ),
        movement_heartbeat_count_value=movement_heartbeat_count(generation) if basic_movement_mode else None,
        creation_marker_count_value=character_creation_marker_count(generation) if creation_mode else None,
        vendor_trade_evidence_value=vendor_trade_db_evidence(manifest, generation) if vendor_trade_mode else None,
        vendor_negative_evidence_value=(
            vendor_negative_db_evidence(manifest, generation) if vendor_negative_mode else None
        ),
        trainer_evidence_value=trainer_db_evidence(manifest, generation) if trainer_mode else None,
        mage_combat_evidence_value=(
            mage_combat_db_evidence(manifest, generation) if mage_combat_mode else None
        ),
        mage_followup_evidence_value=(
            mage_combat_db_evidence(manifest, generation) if mage_followup_mode else None
        ),
        login_callback_count_value=(
            len(player_login_callbacks(generation)) if mage_followup_mode else None
        ),
        logout_count_value=(
            vendor_trade_marker_count(generation, "s2c", "SMSG_LOGOUT_COMPLETE")
            if mage_followup_mode else None
        ),
    )
    unchanged = protected_inputs_unchanged(manifest, generation)
    generation["isolation_unchanged"] = unchanged
    evidence["protected_inputs_unchanged"] = unchanged
    evidence["reset"] = "pending"
    server_ok = (
        any(item == {"direction": "c2s", "opcode": "CMSG_AUTH_SESSION"} for item in transcript)
        and any(item == {"direction": "s2c", "opcode": "SMSG_AUTH_RESPONSE"} for item in transcript)
    )
    if character_mode:
        character_ok = (
            len(milestones) == len(CHARACTER_MILESTONES)
            and any(item == {"direction": "c2s", "opcode": "CMSG_CHAR_ENUM"} for item in transcript)
            and any(item == {"direction": "s2c", "opcode": "SMSG_CHAR_ENUM"} for item in transcript)
            and rows == (1 if populated_mode or creation_mode else 0)
            and evidence["stability_seconds"] >= 5
            and evidence["endpoint_ownership"]
            and evidence["owned_window"]
            and evidence["screen_confirmed"]
            and not evidence["forbidden_opcodes"]
            and unchanged
        )
        if populated_mode:
            character_ok = character_ok and realm_count == 1 and enum_events == [{
                "account_id": ACCOUNT_ID, "guid_low": CHARACTER_GUID,
                "list_position": CHARACTER_LIST_POSITION,
            }] * expected_enumerations
        if selection_mode:
            character_ok = character_ok and selection_proof_is_complete(
                selection, transcript, enum_events, expected_logins, expected_enumerations)
        if initial_packets_mode:
            character_ok = character_ok and selection_proof_is_complete(
                selection, transcript, enum_events, expected_logins, expected_enumerations) and (
                evidence["pre_map_marker_count"] == 1
                and bool(evidence["pre_map_packet_prefix"])
                and evidence["post_marker_hold_seconds"] >= 5
                and evidence["post_marker_snapshots"] == 2
            )
        if map_insertion_mode:
            character_ok = character_ok and selection_proof_is_complete(
                selection, transcript, enum_events, expected_logins, expected_enumerations) and (
                evidence["map_insertion_marker_count"] == 1
                and bool(evidence["map_insertion_packet_prefix"])
                and "SMSG_LOGIN_VERIFY_WORLD" in evidence["map_insertion_packet_prefix"]
                and evidence["post_marker_hold_seconds"] >= 5
                and evidence["post_marker_snapshots"] == 2
            )
        if in_world_control_mode:
            # The marker is the server's time-sync liveness line, logged once while a session has
            # no clock delta yet: a relog's second session (an already-in-world character) does not
            # add another, so that stage only requires the first one and proves its two sessions
            # through the second enumeration, login request, callback and world-entry landing.
            marker_ok = (
                evidence["in_world_control_marker_count"] >= 1
                if plan32_geometry_mode and generation.get("plan32_stage") == "relog"
                else evidence["in_world_control_marker_count"] == expected_logins
            )
            character_ok = character_ok and selection_proof_is_complete(
                selection, transcript, enum_events, expected_logins, expected_enumerations
            ) and (
                marker_ok
                and bool(evidence["in_world_control_packet_prefix"])
                and "C->S:CMSG_TIME_SYNC_RESP" in evidence["in_world_control_packet_prefix"]
                and evidence["post_marker_hold_seconds"] >= 5
                and evidence["post_marker_snapshots"] == 2
            )
        if basic_movement_mode:
            character_ok = character_ok and evidence["movement_heartbeat_count"] > 0
        if ground_movement_mode:
            ground_movement_samples_value = ground_movement_action_samples(ground_movement_samples(generation))
            character_ok = character_ok and (
                [str(sample["opcode"]) for sample in ground_movement_samples_value]
                == list(GROUND_MOVEMENT_OPCODES)
                and ground_movement_deltas_are_action_appropriate(ground_movement_samples_value)
                and ground_movement_is_stable_after_final_stop(generation, ground_movement_samples_value)
            )
        if jump_fall_land_mode:
            jump_fall_land_samples_value = ground_movement_samples(generation)
            character_ok = character_ok and (
                [sample["opcode"] for sample in jump_fall_land_action_pair(jump_fall_land_samples_value)]
                == list(JUMP_FALL_LAND_OPCODES)
                and jump_fall_land_deltas_are_action_appropriate(jump_fall_land_samples_value)
                and jump_fall_land_is_stable_after_landing(generation, jump_fall_land_samples_value)
            )
        if walk_run_mode_switch_mode:
            # Like jump_fall_land_action_pair, drop the world-entry MSG_MOVE_FALL_LAND that
            # precedes every action under test; only the trailing toggle/move pair is under test.
            walk_run_mode_switch_samples_value = ground_movement_samples(generation)[
                -len(WALK_RUN_MODE_SWITCH_OPCODES):
            ]
            character_ok = character_ok and (
                evidence["walk_run_mode_switch_sequence"][-len(WALK_RUN_MODE_SWITCH_OPCODES):]
                == list(WALK_RUN_MODE_SWITCH_OPCODES)
                and walk_run_mode_switch_deltas_are_action_appropriate(walk_run_mode_switch_samples_value)
                and ground_movement_is_stable_after_final_stop(generation, walk_run_mode_switch_samples_value)
            )
        if run_speed_mode:
            character_ok = character_ok and bool(evidence["run_speed_acknowledgements"])
        if basic_spell_cast_mode:
            character_ok = character_ok and evidence["spell_cast_marker_count"] > 0
        if target_targeted_spell_cast_mode:
            character_ok = character_ok and (
                evidence["spell_cast_marker_count"] > 0
                and evidence["cast_failed_marker_count"] > 0
                and evidence["target_targeted_spell_cast_sequence_count"] > 0
            )
        if vendor_trade_mode:
            vendor = evidence["vendor_trade"] or {}
            character_ok = character_ok and (
                evidence["vendor_inventory_count"] >= 1
                and evidence["vendor_buy_requests"] >= 1
                and evidence["vendor_buy_results"] >= 1
                and evidence["vendor_buy_failures"] == 0
                and evidence["vendor_sell_requests"] >= 1
                and evidence["vendor_buyback_requests"] >= 1
                and evidence["vendor_repair_requests"] >= 1
                and evidence["vendor_sell_item_counter"] == VENDOR_TRADE_SELL_ITEM_GUID
                and evidence["vendor_buyback_slot"] == VENDOR_TRADE_BUYBACK_SLOT_START
                and vendor.get("bought_item_count", 0) >= 1
                and vendor.get("buyback_item_count", 0) >= 1
                and vendor.get("repaired_durability", 0) == VENDOR_TRADE_DAMAGED_MAX_DURABILITY
                and 0 < vendor.get("money", 0) < VENDOR_TRADE_MONEY
            )
        if mage_combat_mode:
            character_ok = character_ok and mage_combat_accepted(evidence)
        if mage_followup_mode:
            character_ok = character_ok and mage_followup_accepted(evidence)
        if trainer_mode:
            trainer = evidence["trainer"] or {}
            trainer_list = evidence["trainer_list"] or {}
            list_spells = {
                int(spell): (int(cost), int(state)) for spell, cost, state in trainer_list.get("spells") or []
            }
            buy_events = evidence["trainer_buy_events"] or []
            results = evidence["trainer_results"] or []
            learned = evidence["trainer_learned_spells"] or []
            list_guid = int(trainer_list.get("npc_guid", 0))
            # the runtime guid carries the creature entry in its high bits; the client and server share it
            # for the list, the buy and the success, so an arbitrary NPC cannot stand in
            list_entry = (list_guid >> 24) & 0xFFFFFF
            casts = [event for event in (evidence["plan32_casts"] or [])
                     if event.get("opcode") == "CMSG_CAST_SPELL"]
            goes = [event for event in (evidence["plan32_casts"] or [])
                    if event.get("opcode") == "SMSG_SPELL_GO"]
            failures = [event for event in (evidence["plan32_casts"] or [])
                        if event.get("opcode") == "SMSG_CAST_FAILED"]
            list_index = int(trainer_list.get("index", -1))
            buy_index = int(buy_events[0]["index"]) if len(buy_events) == 1 else -1
            result_index = int(results[0]["index"]) if len(results) == 1 else -1
            learned_index = int(learned[0]["index"]) if len(learned) == 1 else -1
            cast_event = next((event for event in casts if int(event.get("spell", 0)) == TRAINER_SPELL_ID), None)
            cast_index = int(cast_event["index"]) if cast_event else -1
            cast_counter = int(cast_event["count"]) if cast_event else None
            # the success must follow the request, not merely exist somewhere in the capture: an earlier
            # cast with the same byte counter must not certify a later request that never succeeds
            go_event = next((event for event in goes
                             if int(event.get("caster", 0)) == CHARACTER_GUID
                             and int(event.get("spell", 0)) == TRAINER_SPELL_ID
                             and int(event.get("count", -1)) == cast_counter
                             and int(event.get("index", -1)) > cast_index), None)
            cast_ok = (
                cast_event is not None
                and go_event is not None
                and cast_index > buy_index
                and not any(int(event.get("spell", 0)) == TRAINER_SPELL_ID
                            and int(event.get("count", -1)) == cast_counter for event in failures)
            )
            # the whole transaction has to be in order: the list first, then the buy, then its success and
            # the learned notification, then the usability cast started after the purchase
            ordered = (
                list_index >= 0
                and list_index < buy_index < result_index
                and buy_index < learned_index
            )
            character_ok = character_ok and (
                list_guid != 0
                and list_entry == TRAINER_NPC_ENTRY
                and int(trainer_list.get("trainer_id", -1)) == TRAINER_ID
                and TRAINER_SPELL_ID in list_spells
                and list_spells[TRAINER_SPELL_ID] == (TRAINER_SPELL_LISTED_PRICE, TRAINER_STATE_AVAILABLE)
                and any(state == TRAINER_STATE_UNAVAILABLE for _cost, state in list_spells.values())
                and [{"trainer_guid": event["trainer_guid"], "trainer_id": event["trainer_id"],
                      "spell": event["spell"]} for event in buy_events] == [{
                    "trainer_guid": list_guid, "trainer_id": TRAINER_ID, "spell": TRAINER_SPELL_ID,
                }]
                and [{"trainer_guid": event["trainer_guid"], "spell": event["spell"]}
                     for event in results] == [{"trainer_guid": list_guid, "spell": TRAINER_SPELL_ID}]
                and [event["spell"] for event in learned] == [TRAINER_SPELL_ID]
                and ordered
                and evidence["trainer_buy_failures"] == 0
                and cast_ok
                and trainer.get("known_spell_count", 0) == 1
                and trainer.get("money", -1) == TRAINER_MONEY - TRAINER_SPELL_LISTED_PRICE
            )
        if vendor_negative_mode:
            case = generation.get("vendor_case")
            vendor = evidence["vendor_negative"] or {}
            owned = vendor.get("owned_items") or []
            links = vendor.get("links") or []
            money = vendor.get("money", -1)
            requests = evidence["vendor_buy_requests_detail"] or []
            failed_items = evidence["vendor_buy_failed_items"] or []
            expected_request = {"item": VENDOR_TRADE_BUY_ENTRY, "slot": VENDOR_TRADE_BUY_MUID, "count": 1}
            owned_guids = sorted(str(row.get("guid")) for row in owned)
            linked_guids = sorted(str(link.get("item_guid")) for link in links)
            link_shape = [
                {
                    "bag": link.get("bag"), "slot": link.get("slot"), "entry": link.get("entry"),
                    "count": link.get("count"), "owner_guid": link.get("owner_guid"),
                }
                for link in links
            ]
            if case == "repeat-buy":
                case_ok = (
                    requests == [expected_request, expected_request]
                    and evidence["vendor_buy_results"] == 2
                    and evidence["vendor_buy_failures"] == 0
                    and sorted((row.get("entry"), row.get("count")) for row in owned)
                    == [(VENDOR_TRADE_BUY_ENTRY, 1), (VENDOR_TRADE_BUY_ENTRY, 1)]
                    and link_shape == [
                        {"bag": 0, "slot": 23, "entry": VENDOR_TRADE_BUY_ENTRY, "count": 1,
                         "owner_guid": CHARACTER_GUID},
                        {"bag": 0, "slot": 24, "entry": VENDOR_TRADE_BUY_ENTRY, "count": 1,
                         "owner_guid": CHARACTER_GUID},
                    ]
                    and linked_guids == owned_guids
                    and money == VENDOR_TRADE_MONEY - 2 * 34
                )
            elif case == "no-money":
                case_ok = (
                    requests == [expected_request]
                    and evidence["vendor_buy_results"] == 0
                    and failed_items == [VENDOR_TRADE_BUY_ENTRY]
                    and VENDOR_NEGATIVE_BUY_ERR_NOT_ENOUGHT_MONEY in (evidence["vendor_buy_failure_reasons"] or [])
                    and owned == []
                    and links == []
                    and money == 0
                )
            elif case == "full-bags":
                case_ok = (
                    requests == [expected_request]
                    and evidence["vendor_buy_results"] == 0
                    and VENDOR_NEGATIVE_EQUIP_ERR_INVENTORY_FULL
                    in (evidence["vendor_inventory_failure_reasons"] or [])
                    and owned == [
                        {"guid": 99000400 + index, "entry": VENDOR_NEGATIVE_FILL_ENTRY, "count": 1}
                        for index in range(16)
                    ]
                    and link_shape == [
                        {"bag": 0, "slot": 23 + index, "entry": VENDOR_NEGATIVE_FILL_ENTRY, "count": 1,
                         "owner_guid": CHARACTER_GUID}
                        for index in range(16)
                    ]
                    and linked_guids == owned_guids
                    and money == VENDOR_TRADE_MONEY
                )
            else:
                case_ok = False
            character_ok = character_ok and case_ok
        if plan32_geometry_mode:
            stage = generation.get("plan32_stage")
            casts = evidence["plan32_casts"] or []
            target = selections_target_fixture(casts)
            first_selection = next(
                (index for index, event in enumerate(casts)
                 if event["opcode"] == "CMSG_SET_SELECTION"), None,
            )
            after_selection = casts[first_selection:] if first_selection is not None else []
            sent = [event for event in after_selection if event["opcode"] == "CMSG_CAST_SPELL"]
            fails = [event for event in after_selection if event["opcode"] == "SMSG_CAST_FAILED"]
            goes = [
                event for event in after_selection
                if event["opcode"] == "SMSG_SPELL_GO" and event.get("spell") == PLAN32_GEOMETRY_SPELL_ID
                and event.get("caster") == CHARACTER_GUID
            ]
            # Every request names Fireball at the selected fixture with the client's own
            # counters, in order; the selection itself is pinned by name uniqueness (see
            # verify_plan32_seeds) and the target-frame screenshot.
            casts_ok = (
                target is not None
                and [event.get("count") for event in sent] == PLAN32_EXPECTED_COUNTS.get(str(stage))
                and all(event.get("spell") == PLAN32_GEOMETRY_SPELL_ID for event in sent)
                and all(event.get("target") == target for event in sent)
            )
            settle_z = evidence["plan32_settle_z"]
            expected_z = PLAN32_GEOMETRY_SEEDS.get(str(stage), {}).get("character", (None, None, None))[2]
            height_ok = (
                settle_z is not None and expected_z is not None
                and abs(float(settle_z) - expected_z) <= PLAN32_GEOMETRY_HEIGHT_TOLERANCE
            )
            # The fixture's identity as the server itself described it: a unit whose entry field,
            # whose GUID bits and whose create position all agree with the pinned spawn.
            fixture = evidence["plan32_fixture"] or {}
            dummy_seed = PLAN32_GEOMETRY_SEEDS.get(str(stage), {}).get("dummy")
            fixture_ok = (
                dummy_seed is not None
                and fixture.get("type_id") == TYPEID_UNIT
                and fixture.get("entry") == PLAN32_GEOMETRY_DUMMY_ENTRY
                and fixture.get("entry_from_guid") == PLAN32_GEOMETRY_DUMMY_ENTRY
                and fixture.get("guid_high") == HIGHGUID_UNIT
                and fixture.get("position") is not None
                and math.hypot(
                    fixture["position"][0] - dummy_seed[0], fixture["position"][1] - dummy_seed[1]
                ) <= PLAN32_FIXTURE_POSITION_TOLERANCE
                and abs(fixture["position"][2] - dummy_seed[2]) <= PLAN32_FIXTURE_POSITION_TOLERANCE
            )
            # The run had the staged geometry in play: the staged copies hash to what prepare
            # recorded for them, the server's own config points DataDir at those copies and sets
            # its support keys, and its support line claims line of sight. The hashes are
            # recomputed from the staged trees, so a copy that changed after prepare fails here
            # even though the recorded input hashes still agree with themselves.
            facts = evidence["plan32_geometry_facts"] or {}
            config = facts.get("config") or {}
            support_line = str(facts.get("support_line") or "")
            facts_ok = (
                bool(facts.get("data_root"))
                and str(facts.get("data_dir") or "") == str(generation["paths"]["data"])
                and facts.get("maps_hash") == generation["inputs"].get("data_maps_hash")
                and facts.get("vmaps_hash") == generation["inputs"].get("data_vmaps_hash")
                and facts.get("mmaps_hash") == generation["inputs"].get("data_mmaps_hash")
                and str(config.get("DataDir") or "") == str(generation["paths"]["data"])
                and all(
                    str(config.get(key)).strip().lower() in {"1", "true", "yes", "on"}
                    for key in ("MoveMaps.Enable", "vmap.enableLOS", "vmap.enableHeight")
                )
                and "VMap support included" in support_line and "LineOfSight:true" in support_line
            )
            # The walked stage stood on the staged terrain: every accepted movement sample was
            # read against the map height under it, so a teleport or a vertical spike fails here
            # even though the first sample's height is checked against the seed above.
            geometry = evidence["plan32_geometry"] or {}
            terrain_facts = geometry.get("terrain") or {}
            terrain_ok = (
                "error" not in geometry
                and len(terrain_facts.get("samples") or []) == len(evidence["plan32_player_samples"] or [])
                and bool(terrain_facts.get("samples"))
                and not plan32_terrain_problems(terrain_facts)
            )
            # The rays the casts were made along, from the staged vmaps: the blocked stage's has to
            # break on the pinned obstacle at both the eye point and a low point on the fixture; the
            # clear and chase stages' has to be whole at both.
            expectation = PLAN32_STAGE_RAY_EXPECTATION.get(str(stage))
            obstacle = geometry.get("obstacle")
            rays_ok = (
                "error" not in geometry
                and bool(geometry.get("casts"))
                and all(
                    cast.get("eye_hits") is not None and cast.get("low_hits") is not None
                    and (
                        (expectation == "blocked"
                         and cast["eye_hits"][:1] == [obstacle]
                         and cast["low_hits"][:1] == [obstacle])
                        or (expectation == "clear" and not cast["eye_hits"] and not cast["low_hits"])
                    )
                    for cast in geometry.get("casts", [])
                )
            )
            pins_ok = True
            if str(stage) in ("blocked", "clear", "relog"):
                line_key = "clear" if str(stage) == "relog" else str(stage)
                pins = geometry.get("pins") or {}
                character_pin, dummy_pin = pins.get("character"), pins.get("dummy")
                line_length = float((geometry.get("derived") or {}).get(line_key, {}).get("length") or 0)
                pins_ok = (
                    character_pin is not None and dummy_pin is not None
                    and character_pin["perpendicular"] <= PLAN32_PIN_TOLERANCE
                    and abs(character_pin["along"]) <= PLAN32_PIN_TOLERANCE
                    and abs(character_pin["endpoint_z_delta"]) <= PLAN32_PIN_TOLERANCE
                    and dummy_pin["perpendicular"] <= PLAN32_PIN_TOLERANCE
                    and 1.0 <= dummy_pin["along"] <= line_length - 1.0
                )
            correlation_ok = not plan32_correlation_problems(
                str(stage), target if target is not None else 0, plan32_packet_events(generation)
            )
            if stage == "blocked":
                stage_ok = (
                    casts_ok
                    and [
                        (event.get("count"), event.get("spell"), event.get("result"))
                        for event in fails
                    ] == [(1, PLAN32_GEOMETRY_SPELL_ID, SPELL_FAILED_LINE_OF_SIGHT_RESULT),
                          (2, PLAN32_GEOMETRY_SPELL_ID, SPELL_FAILED_LINE_OF_SIGHT_RESULT)]
                    and not goes
                )
            elif stage == "clear":
                stage_ok = casts_ok and len(goes) >= 1 and not fails
            elif stage == "relog":
                # No cast here: the walk is the action under test, and its predicate lives in
                # plan32_relog_problems so the synthetic check can exercise the same code. It reads
                # the log in order — walk before the completed logout, landing after the second
                # login — so a landing inside the first session cannot stand in for the return.
                stage_ok = not plan32_relog_problems(generation)
            elif stage == "chase":
                chase = evidence["plan32_chase"] or {}
                samples = evidence["plan32_player_samples"] or []
                ops = [str(sample["opcode"]) for sample in samples]
                # The run leg as driven: forward tap, stop, strafe tap, strafe stop, in that
                # order (a settle fall or extra heartbeats anywhere in between are fine).
                run_leg = [
                    "MSG_MOVE_START_FORWARD", "MSG_MOVE_STOP",
                    "MSG_MOVE_START_STRAFE_RIGHT", "MSG_MOVE_STOP_STRAFE",
                ]
                remaining = list(ops)
                leg_ok = True
                for want in run_leg:
                    if want in remaining:
                        remaining = remaining[remaining.index(want) + 1:]
                    else:
                        leg_ok = False
                        break
                start_ok = bool(samples) and math.hypot(
                    float(samples[0]["x"]) - PLAN32_GEOMETRY_SEEDS["chase"]["character"][0],
                    float(samples[0]["y"]) - PLAN32_GEOMETRY_SEEDS["chase"]["character"][1],
                ) <= PLAN32_CHASE_START_TOLERANCE
                end_ok = bool(samples) and math.hypot(
                    float(samples[-1]["x"]) - PLAN32_CHASE_P2[0],
                    float(samples[-1]["y"]) - PLAN32_CHASE_P2[1],
                ) <= PLAN32_CHASE_END_TOLERANCE
                # Only executed movement measures the detour: the tray of paths the fixture was
                # actually on, cut at the run leg, at every repath and at the capture's end. A
                # straight replan cannot manufacture a detour, and arrival is where the collector
                # cut the executed path reaching the player's final position, not an advertised
                # destination - compared with the same inclusive margin the collector stands on,
                # since a grazing pass sits exactly on the boundary.
                points = chase.get("executed", [])
                if len(points) >= 2:
                    length, straight, gap = plan32_path_metrics(points)
                    detour_ok = (
                        straight > 1.0
                        and length / straight >= PLAN32_CHASE_DETOUR_RATIO
                        and gap <= PLAN32_CHASE_MAX_SPLINE_GAP
                    )
                    arrival_ok = bool(samples) and math.hypot(
                        points[-1][0] - float(samples[-1]["x"]),
                        points[-1][1] - float(samples[-1]["y"]),
                    ) <= PLAN32_CHASE_ARRIVAL_DISTANCE + PLAN32_CHASE_ARRIVAL_EPSILON
                else:
                    detour_ok = False
                    arrival_ok = False
                # A swing proves melee range reached, but only a swing the collector kept: the
                # fixture has to have arrived (arrival_ticks) and the player to have stopped
                # (final_stop_ticks), so the pull's own hit and a hit landed on the way prove
                # nothing (plan32_chase_tracks bounds the swings to both ticks).
                engagement_ok = len(chase.get("swings", [])) >= 1
                # A spline that resumes anywhere but where the cut left the mover is a
                # discontinuity, not a repath: the collector records it if the retained
                # observation still contains it, and the stage rejects it.
                join_ok = not chase.get("joins")
                route = (geometry.get("chase") or {}).get("route") or {}
                # The detour is a requirement of the staged mmap, not an accident: the fixture's
                # straight line to the player's end point is broken by the pinned obstacle, the
                # mmap routes around it, and that route is a real detour within reach in time.
                chase_geometry_ok = (
                    (geometry.get("chase") or {}).get("pull_hits") == []
                    and ((geometry.get("chase") or {}).get("fixture_to_end_hits") or [])[:1] == [obstacle]
                    and route.get("start_polygon") is not None
                    and route.get("end_polygon") is not None
                    and route.get("route") is not None
                    and float(route["route"]) <= PLAN32_CHASE_ROUTE_MAX
                    and float(route.get("ratio") or 0.0) >= PLAN32_CHASE_DETOUR_RATIO
                )
                stage_ok = (
                    casts_ok and len(goes) >= 1 and not fails
                    and leg_ok and start_ok and end_ok
                    and detour_ok and arrival_ok and engagement_ok and join_ok
                    and chase_geometry_ok
                )
            else:
                stage_ok = False
            if stage == "relog":
                # This stage carries no cast and no fixture claim: the walk, the drop-out and the
                # return are the whole of it, on top of the loaded geometry and the pinned seeds.
                character_ok = character_ok and (
                    stage_ok and height_ok and terrain_ok and facts_ok and pins_ok
                )
            else:
                character_ok = character_ok and (
                    stage in PLAN32_GEOMETRY_SEEDS
                    and casts_ok
                    and stage_ok
                    and height_ok
                    and terrain_ok
                    and correlation_ok
                    and fixture_ok
                    and facts_ok
                    and rays_ok
                    and pins_ok
                )
        if chat_message_mode:
            character_ok = character_ok and evidence["chat_message_marker_count"] > 0
        if invalid_target_spell_cast_mode:
            character_ok = character_ok and (
                evidence["invalid_target_spell_cast_sequence_count"] > 0
                and evidence["spell_cast_marker_count"] == 0
                and evidence["cast_failed_marker_count"] == 0
            )
        if level_up_mode:
            # SMSG_LEVELUP_INFO is emitted only by Player::GiveLevel, so it proves a real XP award
            # crossed the threshold on the wire rather than a GM command or a row edit.
            character_ok = character_ok and (
                evidence["level_up_marker_count"] >= 1
                and evidence["level_up_xp_gain_count"] >= 1
                and evidence["post_marker_hold_seconds"] >= 5
                and evidence["post_marker_snapshots"] == 2
            )
        if creation_mode:
            character_ok = character_ok and (
                any(item == {"direction": "c2s", "opcode": "CMSG_CHAR_CREATE"} for item in transcript)
                and any(item == {"direction": "s2c", "opcode": "SMSG_CHAR_CREATE"} for item in transcript)
                and evidence["creation_marker_count"] == 1
                and selection is not None
                and selection["legit_characters_admission"]
                and selection["seeded_guid_low"] == selection["callback_guid_low"]
            )
        if character_ok:
            evidence["outcome"] = (
                "level_up_progression_pass" if level_up_mode
                else "mage_combat_pass" if mage_combat_mode
                else "mage_combat_followup_pass" if mage_followup_mode
                else "character_creation_pass" if creation_mode
                else "run_speed_change_pass" if run_speed_mode
                else "basic_spell_cast_pass" if basic_spell_cast_mode
                else "target_targeted_spell_cast_pass" if target_targeted_spell_cast_mode
                else "chat_message_pass" if chat_message_mode
                else "invalid_target_spell_cast_pass" if invalid_target_spell_cast_mode
                else "jump_fall_land_pass" if jump_fall_land_mode
                else "walk_run_mode_switch_pass" if walk_run_mode_switch_mode
                else "plan32_geometry_blocked_pass" if plan32_geometry_mode
                and generation.get("plan32_stage") == "blocked"
                else "plan32_geometry_clear_pass" if plan32_geometry_mode
                and generation.get("plan32_stage") == "clear"
                else "plan32_geometry_chase_pass" if plan32_geometry_mode
                and generation.get("plan32_stage") == "chase"
                else "plan32_geometry_relog_pass" if plan32_geometry_mode
                and generation.get("plan32_stage") == "relog"
                else "vendor_trade_pass" if vendor_trade_mode
                else "vendor_negative_pass" if vendor_negative_mode
                else "trainer_learn_pass" if trainer_mode
                else "ground_movement_pass" if ground_movement_mode
                else "basic_movement_pass" if basic_movement_mode
                else "in_world_control_bootstrap_pass" if in_world_control_mode
                else "map_insertion_object_bootstrap_pass" if map_insertion_mode
                else "initial_post_load_packets_pass" if initial_packets_mode
                else "character_selection_pass" if selection_mode
                else "populated_character_list_pass" if populated_mode else "character_screen_pass"
            )
            set_state(generation, "accepted")
        elif not unchanged:
            set_state(generation, "failed")
        else:
            evidence["outcome"] = "inconclusive"
            set_state(generation, "inconclusive")
    elif generation["mode"] == "no-login":
        started = any(item["kind"] == "wine" for item in generation["processes"])
        started = started and bool(evidence["owned_window"])
        network_safe = not owned_established_lines(generation)
        evidence["network_scope"] = "no-established-tcp" if network_safe else "unexpected-connection"
        evidence["outcome"] = "no_login_isolation_ok" if unchanged and started and network_safe else "inconclusive"
        if unchanged and started and network_safe:
            set_state(generation, "accepted")
        elif not unchanged:
            set_state(generation, "failed")
        else:
            set_state(generation, "inconclusive")
    elif len(milestones) == 4 and server_ok and evidence["endpoint_ownership"] and unchanged:
        if generation["state"] == "observed":
            set_state(generation, "accepted")
    else:
        evidence["outcome"] = "inconclusive"
        set_state(generation, "inconclusive")
    generation["evidence"] = evidence
    evidence_path = Path(generation["paths"]["sanitized_evidence"])
    evidence_path.parent.mkdir(parents=True, exist_ok=True)
    evidence_path.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n")
    save_manifest(manifest_path, manifest)
    print(json.dumps(evidence, indent=2, sort_keys=True))
    if generation["state"] != "accepted":
        raise RuntimeError(f"Plan {plan_number(str(generation['mode']))} evidence is incomplete; verdict is INCONCLUSIVE")


def normalized_replay(evidence: dict[str, object]) -> dict[str, object]:
    auth = evidence.get("auth", {})
    return {
        "build": evidence.get("build"),
        "m2_verified": auth.get("m2_verified") if isinstance(auth, dict) else None,
        "peer_k_equals_database_k": evidence.get("peer_k_equals_database_k"),
        "database_k_equals_world_query_k": evidence.get("database_k_equals_world_query_k"),
        "world": evidence.get("world"),
    }


def replay(args: argparse.Namespace) -> None:
    manifest_path = args.manifest.resolve()
    manifest = load_manifest(manifest_path)
    generation = active_generation(manifest)
    if generation.get("replay_passed", False):
        print(json.dumps(generation["evidence"], indent=2, sort_keys=True))
        return
    accepted_state = generation["state"] == "accepted"
    accepted_reset = generation["state"] == "reset" and generation.get("completed_state") in {
        "accepted", "replayed",
    }
    if (not accepted_state and not accepted_reset) or generation["mode"] != "authentication":
        raise RuntimeError("replay requires an accepted authentication generation")
    fixture_path = args.fixture.resolve()
    if not fixture_path.is_file():
        raise RuntimeError(f"sanitized Plan 7 fixture is absent: {fixture_path}")
    fixture = json.loads(fixture_path.read_text())
    if fixture.get("schema") != 1 or fixture.get("build") != CLIENT_BUILD:
        raise RuntimeError("sanitized Plan 7 fixture has the wrong schema or build")
    expected_client = fixture.get("client_milestones")
    expected_server = fixture.get("server_milestones")
    if generation["evidence"].get("client_milestones") != expected_client:
        raise RuntimeError("real-client milestones differ from the sanitized fixture")
    if generation["evidence"].get("server_milestones") != expected_server:
        raise RuntimeError("real-server milestones differ from the sanitized fixture")
    replay_root = Path(generation["root"]) / "evidence/plan6-replay"
    replay_root.mkdir(parents=True, exist_ok=True)
    replay_manifest = replay_root / "manifest.json"
    inputs = generation["inputs"]
    prepare_command = [
        sys.executable, str(PLAN6_RUNNER), "prepare", "--manifest", str(replay_manifest),
        "--migration", str(inputs["migration"]), "--client", str(Path(str(inputs["client_root"])) / "Wow-64.exe"),
        "--bottle", str(inputs["personal_bottle"]),
    ]
    normalized: dict[str, object] | None = None
    try:
        run_command(prepare_command, timeout=1200)
        run_command([
            sys.executable, str(PLAN6_RUNNER), "run", "--manifest", str(replay_manifest),
            "--authserver", str(inputs["authserver"]["path"]),
            "--unit-tests", str(inputs["unit_tests"]["path"]),
        ], timeout=600)
        replay_manifest_value = json.loads(replay_manifest.read_text())
        replay_evidence = replay_manifest_value["generations"][-1]["evidence"]
        normalized = normalized_replay(replay_evidence)
        if normalized != fixture.get("plan6_replay"):
            raise RuntimeError("Plan 6 semantic replay differs from the sanitized fixture")
    finally:
        if replay_manifest.is_file():
            run_command(
                [sys.executable, str(PLAN6_RUNNER), "reset", "--manifest", str(replay_manifest)],
                timeout=180,
            )
    if normalized is None:
        raise RuntimeError("Plan 6 semantic replay produced no normalized evidence")
    generation["evidence"]["plan6_replay"] = normalized
    generation["replay_passed"] = True
    if accepted_state:
        set_state(generation, "replayed")
    else:
        generation["completed_state"] = "replayed"
    save_manifest(manifest_path, manifest)
    print("Plan 7 semantic replay passed")


def reset(args: argparse.Namespace) -> None:
    manifest_path = args.manifest.resolve()
    manifest = load_manifest(manifest_path)
    generation = active_generation(manifest)
    if generation["state"] == "reset":
        purged = []
        if args.purge_client_base:
            purge_client_base(manifest)
            purged.append("client base")
        if args.purge_database_cache:
            purge_database_cache(manifest_path, manifest)
            purged.append("database cache")
        if purged:
            save_manifest(manifest_path, manifest)
            print(f"purged cached real-client {' and '.join(purged)}")
            return
        print(f"generation {generation['number']} is already reset")
        return
    completed_state = generation["state"]
    stop_wine(generation)
    stop_servers(generation)
    docker = generation["docker"]
    container = run_command(["docker", "inspect", str(docker["container"])], check=False)
    if container.returncode == 0:
        details = assert_container_owned(manifest, generation)
        run_command(["docker", "rm", "-f", details["Name"].removeprefix("/")])
    volume_result = run_command(["docker", "volume", "inspect", str(docker["volume"])], check=False)
    if volume_result.returncode == 0:
        volume = json.loads(volume_result.stdout)[0]
        labels = volume.get("Labels") or {}
        expected = {
            "org.azerothcore.plan": plan_number(str(generation["mode"])),
            "org.azerothcore.run_id": manifest["run_id"],
            "org.azerothcore.generation": str(generation["number"]),
        }
        if any(labels.get(key) != value for key, value in expected.items()):
            raise RuntimeError("refusing to remove a volume whose ownership labels changed")
        run_command(["docker", "volume", "rm", str(docker["volume"])])
    for key in ("client", "data", "xdg_data", "xdg_config", "xdg_cache", "xdg_state", "wine_prefix"):
        path = Path(generation["paths"][key])
        if path.exists():
            remove_owned_path(path, generation)
    unchanged = protected_inputs_unchanged(manifest, generation)
    generation["isolation_unchanged"] = unchanged
    if isinstance(generation.get("evidence"), dict):
        generation["evidence"]["protected_inputs_unchanged"] = unchanged
        generation["evidence"]["reset"] = "PASS" if unchanged else "FAIL"
    generation["completed_state"] = completed_state
    set_state(generation, "reset")
    if args.purge_client_base:
        purge_client_base(manifest)
    if args.purge_database_cache:
        purge_database_cache(manifest_path, manifest)
    save_manifest(manifest_path, manifest)
    for port in generation["ports"].values():
        require_unused(int(port))
    if not unchanged:
        raise RuntimeError("protected client or personal Bottle metadata changed during Plan 7")
    print(f"reset generation {generation['number']}; evidence remains under {generation['root']}")


def comparison_projection(evidence: dict[str, object]) -> dict[str, object]:
    return {
        "schema": evidence.get("schema"),
        "build": evidence.get("build"),
        "mode": evidence.get("mode"),
        "outcome": evidence.get("outcome"),
        "client_milestones": evidence.get("client_milestones"),
        "server_milestones": evidence.get("server_milestones"),
        "payload_shape": evidence.get("payload_shape"),
        "inputs": evidence.get("inputs"),
        "character_rows": evidence.get("character_rows"),
        "realm_character_count": evidence.get("realm_character_count"),
        "enumerated": evidence.get("enumerated"),
        "empty_response_payload": evidence.get("empty_response_payload"),
        "response_body": evidence.get("response_body"),
        "selection": evidence.get("selection"),
        "pre_map_packet_prefix": evidence.get("pre_map_packet_prefix"),
        "pre_map_marker_count": evidence.get("pre_map_marker_count"),
        "map_insertion_packet_prefix": evidence.get("map_insertion_packet_prefix"),
        "map_insertion_marker_count": evidence.get("map_insertion_marker_count"),
        # Background queries and NPC updates vary before the first resolved response.
        "in_world_control_response_observed": (
            "C->S:CMSG_TIME_SYNC_RESP" in (evidence.get("in_world_control_packet_prefix") or [])
        ),
        "in_world_control_marker_count": evidence.get("in_world_control_marker_count"),
        "movement_heartbeat_observed": (evidence.get("movement_heartbeat_count") or 0) > 0,
        "run_speed_acknowledgement_observed": bool(evidence.get("run_speed_acknowledgements")),
        "post_marker_hold_seconds": evidence.get("post_marker_hold_seconds"),
        "post_marker_snapshots": evidence.get("post_marker_snapshots"),
        "stability_seconds": evidence.get("stability_seconds"),
        "endpoint_ownership": evidence.get("endpoint_ownership"),
        "owned_window": evidence.get("owned_window"),
        "screen_confirmed": evidence.get("screen_confirmed"),
        "forbidden_opcodes": evidence.get("forbidden_opcodes"),
        "protected_inputs_unchanged": evidence.get("protected_inputs_unchanged"),
        "reset": evidence.get("reset"),
    }


def compare_last_two(args: argparse.Namespace) -> None:
    manifest = load_manifest(args.manifest.resolve())
    candidates = [
        item for item in manifest["generations"]
        if item.get("state") == "reset"
        and item.get("completed_state") in {"accepted", "replayed"} and item.get("isolation_unchanged")
        and item.get("evidence")
    ]
    if candidates:
        mode = candidates[-1].get("mode")
        candidates = [item for item in candidates if item.get("mode") == mode]
    if len(candidates) < 2:
        raise RuntimeError("two reset, accepted generations of the same mode are required")
    left, right = candidates[-2:]
    if left["number"] == right["number"]:
        raise RuntimeError("repeatability comparison requires distinct generations")
    if comparison_projection(left["evidence"]) != comparison_projection(right["evidence"]):
        raise RuntimeError("the last two sanitized results differ")
    print(json.dumps({
        "generations": [left["number"], right["number"]],
        "repeatable": True,
        "replay_passed": bool(left.get("replay_passed") or right.get("replay_passed")),
    }, sort_keys=True))


def self_check() -> None:
    sample = """one LOGIN_STATE_AUTHENTICATED LOGIN_OK
two COP_CONNECT RESPONSE_CONNECTED TRUE
three COP_AUTHENTICATE AUTH_OK TRUE
four Initiating: COP_GET_CHARACTERS
"""
    assert matched_milestones(sample) == [name for name, _ in CLIENT_MILESTONES]
    assert matched_milestones(sample.replace("AUTH_OK", "AUTH_FAILED")) == ["auth_login_ok", "world_connected"]
    character_sample = """one LOGIN_STATE_AUTHENTICATED LOGIN_OK
two COP_CONNECT RESPONSE_CONNECTED TRUE
three COP_AUTHENTICATE AUTH_OK TRUE
four Completed: COP_GET_CHARACTERS result=TRUE
"""
    assert matched_milestones(character_sample, CHARACTER_MILESTONES) == [name for name, _ in CHARACTER_MILESTONES]
    assert matched_milestones(character_sample.replace("result=TRUE", "result=FALSE"), CHARACTER_MILESTONES) == [
        "auth_login_ok", "world_connected", "world_auth_ok",
    ]
    assert stability_seconds("5") == 5
    try:
        stability_seconds("4")
    except argparse.ArgumentTypeError:
        pass
    else:
        raise AssertionError("stability below five seconds was accepted")
    assert len(srp_registration(ACCOUNT, PASSWORD, bytes(range(1, 33)))) == 32
    generation: Generation = {
        "mode": "authentication",
        "ports": {"world": 18085},
        "processes": [],
        "paths": {"raw_evidence": "/abs/private"},
        "inputs": {
            "authserver": {"sha256": "a" * 64}, "worldserver": {"sha256": "b" * 64},
            "data_dbc_hash": "c" * 64, "data_maps_hash": "d" * 64,
        },
    }
    transcript = [
        {"direction": "c2s", "opcode": "CMSG_AUTH_SESSION"},
        {"direction": "s2c", "opcode": "SMSG_AUTH_RESPONSE"},
        {"direction": "s2c", "opcode": "SMSG_CHAR_ENUM"},
    ]
    evidence = sanitized_evidence(generation, [name for name, _ in CLIENT_MILESTONES], transcript)
    encoded = json.dumps(evidence, sort_keys=True)
    assert "/abs/private" not in encoded and ACCOUNT not in encoded and PASSWORD not in encoded
    assert evidence["next_server_packet"] == {"opcode": "SMSG_CHAR_ENUM", "status": "candidate/inconclusive"}
    character_generation = dict(generation)
    character_generation["mode"] = "character-screen"
    character_generation["stability_seconds"] = 10
    character_evidence = sanitized_evidence(
        character_generation,
        [name for name, _ in CHARACTER_MILESTONES],
        [
            {"direction": "c2s", "opcode": "CMSG_CHAR_ENUM"},
            {"direction": "s2c", "opcode": "SMSG_CHAR_ENUM"},
        ],
        character_rows=0,
        screen_confirmed=True,
    )
    assert character_evidence["empty_response_payload"] == EMPTY_CHARACTER_ENUM_PAYLOAD
    assert character_evidence["forbidden_opcodes"] == []
    assert "CMSG_PLAYER_LOGIN" in sanitized_evidence(
        character_generation,
        [name for name, _ in CHARACTER_MILESTONES],
        [{"direction": "c2s", "opcode": "CMSG_PLAYER_LOGIN"}],
    )["forbidden_opcodes"]
    creation_generation = dict(character_generation)
    creation_generation["mode"] = CHARACTER_CREATION_MODE
    creation_transcript = [
        {"direction": "c2s", "opcode": "CMSG_CHAR_ENUM"},
        {"direction": "s2c", "opcode": "SMSG_CHAR_ENUM"},
        {"direction": "c2s", "opcode": "CMSG_CHAR_CREATE"},
        {"direction": "s2c", "opcode": "SMSG_CHAR_CREATE"},
    ]
    creation_evidence = sanitized_evidence(
        creation_generation, [name for name, _ in CHARACTER_MILESTONES], creation_transcript,
        character_rows=1, screen_confirmed=True, creation_marker_count_value=1,
    )
    assert creation_evidence["forbidden_opcodes"] == []
    assert creation_evidence["creation_marker_count"] == 1
    assert creation_evidence["outcome"] == "character_creation_candidate"
    assert "CMSG_CHAR_DELETE" in sanitized_evidence(
        creation_generation, [name for name, _ in CHARACTER_MILESTONES],
        [{"direction": "c2s", "opcode": "CMSG_CHAR_DELETE"}],
    )["forbidden_opcodes"]
    # Plan 35: the cast chain must hang together — the request names the victim, the cast costs mana,
    # the spell go belongs to the mage and the damage is fire on that same victim.
    target_guid = (MAGE_COMBAT_TARGET_ENTRY << 24) | 42

    def mage_chain(request=True, spell_go=True, caster=7, damage_target=target_guid, school=4,
                   cast_mana=True, selection=True, selection_target=target_guid):
        casts = []
        if selection:
            casts.append({"opcode": "CMSG_SET_SELECTION", "index": 9, "target": selection_target})
        if request:
            casts.append({"opcode": "CMSG_CAST_SPELL", "index": 10, "spell": MAGE_COMBAT_SPELL_ID,
                          "target": target_guid})
        if spell_go:
            casts.append({"opcode": "SMSG_SPELL_GO", "index": 12, "spell": MAGE_COMBAT_SPELL_ID,
                          "caster": caster})
        events = [
            {"opcode": "SMSG_POWER_UPDATE", "index": 5, "guid": 7, "powers": [[0, 100]]},
            {"opcode": "SMSG_SPELLNONMELEEDAMAGELOG", "index": 13, "attacker": 7,
             "spell": MAGE_COMBAT_SPELL_ID, "target": damage_target, "damage": 3, "school": school},
        ]
        if cast_mana:
            events.append({"opcode": "SMSG_POWER_UPDATE", "index": 11, "guid": 7, "powers": [[0, 96]]})
        return {
            "mage_combat": {"guid": 7, "class": 8, "race": 1, "action_count": 1},
            "plan32_casts": casts,
            "combat_events": events,
        }

    assert mage_combat_accepted(mage_chain())
    assert not mage_combat_accepted(mage_chain(request=False))
    assert not mage_combat_accepted(mage_chain(spell_go=False))
    assert not mage_combat_accepted(mage_chain(caster=8))
    assert not mage_combat_accepted(mage_chain(cast_mana=False))
    assert not mage_combat_accepted(mage_chain(damage_target=target_guid + 1))
    assert not mage_combat_accepted(mage_chain(school=2))
    assert not mage_combat_accepted(mage_chain(selection=False))
    assert not mage_combat_accepted(mage_chain(selection_target=target_guid + 1))
    def followup_chain(first_drop=96, regen=120, second_drop=110, login=2, logout=1):
        return {
            "mage_followup": {"guid": 7, "class": 8, "race": 1, "action_count": 1, "spell_count": 0},
            "plan32_casts": [
                {"opcode": "CMSG_CAST_SPELL", "index": 10, "spell": MAGE_COMBAT_SPELL_ID, "target": 5},
                {"opcode": "SMSG_SPELL_GO", "index": 11, "spell": MAGE_COMBAT_SPELL_ID, "caster": 7},
                {"opcode": "CMSG_CAST_SPELL", "index": 30, "spell": MAGE_COMBAT_SPELL_ID, "target": 5},
                {"opcode": "SMSG_SPELL_GO", "index": 31, "spell": MAGE_COMBAT_SPELL_ID, "caster": 7},
            ],
            "combat_events": [
                {"opcode": "SMSG_POWER_UPDATE", "index": 5, "guid": 7, "powers": [[0, 100]]},
                {"opcode": "SMSG_POWER_UPDATE", "index": 12, "guid": 7, "powers": [[0, first_drop]]},
                {"opcode": "SMSG_POWER_UPDATE", "index": 20, "guid": 7, "powers": [[0, regen]]},
                {"opcode": "SMSG_POWER_UPDATE", "index": 32, "guid": 7, "powers": [[0, second_drop]]},
            ],
            "login_callback_count": login,
            "logout_count": logout,
        }

    assert mage_followup_accepted(followup_chain())
    assert not mage_followup_accepted(followup_chain(second_drop=200))    # second cast must cost mana
    assert not mage_followup_accepted(followup_chain(regen=90))           # no regeneration
    assert not mage_followup_accepted(followup_chain(first_drop=100))     # first cast free
    assert not mage_followup_accepted(followup_chain(login=1))            # no relog
    assert not mage_followup_accepted(followup_chain(logout=0))           # no logout
    assert plan_number(CHARACTER_CREATION_MODE) == "22"
    populated_generation = dict(character_generation)
    populated_generation["mode"] = POPULATED_MODE
    expected_character = {
        "guid_low": CHARACTER_GUID, "name": CHARACTER_NAME, "race": CHARACTER_RACE,
        "class": CHARACTER_CLASS, "gender": 0,
        "level": 1, "map": CHARACTER_MAP, "zone": CHARACTER_ZONE,
        "list_position": CHARACTER_LIST_POSITION,
        "flags": 0, "flags2": 0, "visual_items_nonzero": 0,
    }
    populated_evidence = sanitized_evidence(
        populated_generation, [name for name, _ in CHARACTER_MILESTONES],
        [{"direction": "c2s", "opcode": "CMSG_CHAR_ENUM"},
         {"direction": "s2c", "opcode": "SMSG_CHAR_ENUM"}],
        character_rows=1, realm_count=1, enumerated=expected_character, screen_confirmed=True,
    )
    assert len(POPULATED_CHARACTER_ENUM_PAYLOAD) == 278 * 2
    assert populated_evidence["response_body"] == POPULATED_CHARACTER_ENUM_PAYLOAD
    assert populated_evidence["realm_character_count"] == 1
    assert populated_evidence["enumerated"] == expected_character
    assert plan_number(POPULATED_MODE) == "9"
    selection_generation = dict(populated_generation)
    selection_generation["mode"] = CHARACTER_SELECTION_MODE
    selection_evidence = sanitized_evidence(
        selection_generation, [name for name, _ in CHARACTER_MILESTONES],
        [{"direction": "c2s", "opcode": "CMSG_CHAR_ENUM"},
         {"direction": "s2c", "opcode": "SMSG_CHAR_ENUM"},
         {"direction": "c2s", "opcode": "CMSG_PLAYER_LOGIN"}],
        character_rows=1, realm_count=1, enumerated=expected_character, screen_confirmed=True,
        selection={
            "action": "enter", "seeded_guid_low": CHARACTER_GUID,
            "enumerated_guid_low": CHARACTER_GUID, "request_guid_low": CHARACTER_GUID,
            "callback_guid_low": CHARACTER_GUID, "legit_characters_admission": True,
        }, downstream_diagnostic="not-observed",
    )
    assert selection_evidence["forbidden_opcodes"] == []
    assert plan_number(CHARACTER_SELECTION_MODE) == "10"
    initial_packets_generation = dict(selection_generation)
    initial_packets_generation["mode"] = INITIAL_POST_LOAD_PACKETS_MODE
    initial_packets_generation["post_marker_hold_seconds"] = 5
    initial_packets_generation["post_marker_snapshots"] = 2
    initial_packets_evidence = sanitized_evidence(
        initial_packets_generation, [name for name, _ in CHARACTER_MILESTONES],
        [{"direction": "c2s", "opcode": "CMSG_CHAR_ENUM"},
         {"direction": "s2c", "opcode": "SMSG_CHAR_ENUM"},
         {"direction": "c2s", "opcode": "CMSG_PLAYER_LOGIN"}],
        character_rows=1, realm_count=1, enumerated=expected_character, screen_confirmed=True,
        selection={
            "action": "enter", "seeded_guid_low": CHARACTER_GUID,
            "enumerated_guid_low": CHARACTER_GUID, "request_guid_low": CHARACTER_GUID,
            "callback_guid_low": CHARACTER_GUID, "legit_characters_admission": True,
        }, initial_packet_prefix_value=["SMSG_SETUP_CURRENCY"], pre_map_marker_count=1,
    )
    assert initial_packets_evidence["forbidden_opcodes"] == []
    assert initial_packets_evidence["pre_map_packet_prefix"] == ["SMSG_SETUP_CURRENCY"]
    assert plan_number(INITIAL_POST_LOAD_PACKETS_MODE) == "11"
    map_insertion_generation = dict(selection_generation)
    map_insertion_generation["mode"] = MAP_INSERTION_MODE
    map_insertion_generation["post_marker_hold_seconds"] = 5
    map_insertion_generation["post_marker_snapshots"] = 2
    map_insertion_evidence = sanitized_evidence(
        map_insertion_generation, [name for name, _ in CHARACTER_MILESTONES],
        [{"direction": "c2s", "opcode": "CMSG_CHAR_ENUM"},
         {"direction": "s2c", "opcode": "SMSG_CHAR_ENUM"},
         {"direction": "c2s", "opcode": "CMSG_PLAYER_LOGIN"}],
        character_rows=1, realm_count=1, enumerated=expected_character, screen_confirmed=True,
        selection={
            "action": "enter", "seeded_guid_low": CHARACTER_GUID,
            "enumerated_guid_low": CHARACTER_GUID, "request_guid_low": CHARACTER_GUID,
            "callback_guid_low": CHARACTER_GUID, "legit_characters_admission": True,
        },
        map_insertion_prefix_value=["SMSG_LOGIN_VERIFY_WORLD", "SMSG_UPDATE_OBJECT"],
        map_insertion_marker_count_value=1,
    )
    assert map_insertion_evidence["forbidden_opcodes"] == []
    assert map_insertion_evidence["map_insertion_packet_prefix"] == ["SMSG_LOGIN_VERIFY_WORLD", "SMSG_UPDATE_OBJECT"]
    assert plan_number(MAP_INSERTION_MODE) == "12"
    in_world_control_generation = dict(selection_generation)
    in_world_control_generation["mode"] = IN_WORLD_CONTROL_MODE
    in_world_control_generation["post_marker_hold_seconds"] = 5
    in_world_control_generation["post_marker_snapshots"] = 2
    in_world_control_evidence = sanitized_evidence(
        in_world_control_generation, [name for name, _ in CHARACTER_MILESTONES],
        [{"direction": "c2s", "opcode": "CMSG_CHAR_ENUM"},
         {"direction": "s2c", "opcode": "SMSG_CHAR_ENUM"},
         {"direction": "c2s", "opcode": "CMSG_PLAYER_LOGIN"}],
        character_rows=1, realm_count=1, enumerated=expected_character, screen_confirmed=True,
        selection={
            "action": "enter", "seeded_guid_low": CHARACTER_GUID,
            "enumerated_guid_low": CHARACTER_GUID, "request_guid_low": CHARACTER_GUID,
            "callback_guid_low": CHARACTER_GUID, "legit_characters_admission": True,
        },
        in_world_control_prefix_value=["S->C:SMSG_TIME_SYNC_REQ", "C->S:CMSG_TIME_SYNC_RESP"],
        in_world_control_marker_count_value=1,
    )
    assert in_world_control_evidence["forbidden_opcodes"] == []
    assert in_world_control_evidence["in_world_control_packet_prefix"] == [
        "S->C:SMSG_TIME_SYNC_REQ", "C->S:CMSG_TIME_SYNC_RESP",
    ]
    assert plan_number(IN_WORLD_CONTROL_MODE) == "13"
    accepted_control_evidence = {
        **in_world_control_evidence, "outcome": "in_world_control_bootstrap_pass",
        "endpoint_ownership": True, "protected_inputs_unchanged": True, "reset": "PASS",
    }
    control_comparison = comparison_projection(accepted_control_evidence)
    assert control_comparison == comparison_projection({
        **accepted_control_evidence,
        "in_world_control_packet_prefix": [
            "S->C:SMSG_MONSTER_MOVE", "C->S:CMSG_CREATURE_QUERY", "C->S:CMSG_TIME_SYNC_RESP",
        ],
    })
    for key, value in (
        ("in_world_control_packet_prefix", ["S->C:SMSG_TIME_SYNC_REQ"]),
        ("in_world_control_marker_count", 0), ("in_world_control_marker_count", 2),
        ("post_marker_hold_seconds", 0), ("post_marker_snapshots", 1),
        ("endpoint_ownership", False), ("screen_confirmed", False),
        ("protected_inputs_unchanged", False), ("reset", "FAIL"),
        ("outcome", "inconclusive"), ("inputs", {}),
    ):
        assert control_comparison != comparison_projection({**accepted_control_evidence, key: value}), key
    assert not selection_proof_is_complete(selection_evidence["selection"], [], [])
    seed_sql = populated_character_seed_sql()
    assert "INSERT INTO `characters`" in seed_sql and "`order`,`innTriggerId`" in seed_sql
    assert "INSERT INTO `characters` VALUES" not in seed_sql
    assert replace_config("# Key = old\nOther=1\n", {"Key": "new"}) == "Key = new\nOther=1\n"
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        sql_root = root / "sql"
        sql_root.mkdir()
        (sql_root / "one.sql").write_text("SELECT 1;\n")
        cache_key = database_cache_key("mysql-image", (("auth-base", sql_root),))
        assert cache_key == database_cache_key("mysql-image", (("auth-base", sql_root),))
        assert cache_key != database_cache_key("other-image", (("auth-base", sql_root),))
        (sql_root / "one.sql").write_text("SELECT 2;\n")
        assert cache_key != database_cache_key("mysql-image", (("auth-base", sql_root),))
        changed_key = database_cache_key("mysql-image", (("auth-base", sql_root),))
        nested = sql_root / "nested"
        nested.mkdir()
        (nested / "ignored.sql").write_text("SELECT 3;\n")
        assert changed_key == database_cache_key("mysql-image", (("auth-base", sql_root),))
        dump = root / "database-cache.sql"
        dump.write_bytes(b"database cache")
        cache_manifest: Manifest = {
            "root": str(root),
            "generations": [{"state": "reset"}],
            "database_cache": {
                "path": str(dump), "sha256": sha256(dump), "purged": False,
            },
        }
        cache_manifest["database_cache"]["state"] = "ready"
        cache_manifest_path = root / "manifest.json"
        save_manifest(cache_manifest_path, cache_manifest)
        purge_database_cache(cache_manifest_path, cache_manifest)
        assert not dump.exists() and "database_cache" not in cache_manifest
        dump.write_bytes(b"partial final")
        database_cache_staging_path(cache_manifest).write_bytes(b"partial staging")
        cache_manifest["database_cache"] = {
            "path": str(dump), "key": "cache-key", "state": "building",
        }
        save_manifest(cache_manifest_path, cache_manifest)
        assert reconcile_database_cache(cache_manifest_path, cache_manifest, "cache-key") is None
        assert not dump.exists() and not database_cache_staging_path(cache_manifest).exists()
        dump.write_bytes(b"corrupt")
        cache_manifest["database_cache"] = {
            "path": str(dump), "key": "cache-key", "sha256": "0" * 64, "state": "ready",
        }
        save_manifest(cache_manifest_path, cache_manifest)
        assert reconcile_database_cache(cache_manifest_path, cache_manifest, "cache-key") is None
        assert not dump.exists() and "database_cache" not in cache_manifest
        base = root / "base"
        base.mkdir()
        (base / "Wow-64.exe").write_bytes(b"client")
        (base / "Data/enUS").mkdir(parents=True)
        (base / "Data/enUS/realmlist.wtf").write_text("source realm\n")
        (base / "Data/enUS/locale-enUS.MPQ").write_bytes(b"locale")
        link_client_base(base, root / "client")
        assert (root / "client/Wow-64.exe").is_symlink()
        assert not (root / "client/WTF").is_symlink()
        assert not (root / "client/Data").is_symlink()
        assert (root / "client/Data/enUS/locale-enUS.MPQ").is_symlink()
        assert not (root / "client/Data/enUS/realmlist.wtf").exists()
        raw = root / "raw"
        raw.mkdir()
        (raw / "WorldServer.log").write_text(
            "Enumerated account 900000 character GUID Full: 0x0000000001020304 "
            "Type: Player Low: 16909060 at list position 7.\n"
        )
        enum_generation: Generation = {"paths": {"raw_evidence": str(raw), "logs": str(root / "logs")}}
        assert enumerated_characters(enum_generation) == [{
            "account_id": ACCOUNT_ID, "guid_low": CHARACTER_GUID, "list_position": CHARACTER_LIST_POSITION,
        }]
        assert plan_number(BASIC_MOVEMENT_MODE) == "15"
        for log, expected in (
            (MOVEMENT_HEARTBEAT_MARKER, 0),
            (IN_WORLD_CONTROL_MARKER + "\n" + MOVEMENT_HEARTBEAT_MARKER, 0),
            ("Finished object update bootstrap after adding to map\n" + MOVEMENT_HEARTBEAT_MARKER +
             "\n" + IN_WORLD_CONTROL_MARKER, 0),
            ("Finished object update bootstrap after adding to map\n" + IN_WORLD_CONTROL_MARKER +
             "\n" + MOVEMENT_HEARTBEAT_MARKER, 1),
        ):
            (raw / "WorldServer.log").write_text(log)
            assert movement_heartbeat_count(enum_generation) == expected
        movement_evidence = {**accepted_control_evidence, "mode": BASIC_MOVEMENT_MODE, "movement_heartbeat_count": 1}
        projection = comparison_projection(movement_evidence)
        assert projection == comparison_projection({**movement_evidence, "movement_heartbeat_count": 2})
        assert projection != comparison_projection({**movement_evidence, "movement_heartbeat_count": 0})
        assert plan_number(RUN_SPEED_MODE) == "16"
        prefix = ("Finished object update bootstrap after adding to map\n" + IN_WORLD_CONTROL_MARKER +
                  "\n" + MOVEMENT_HEARTBEAT_MARKER + "\n")
        sent = "Sent Cataclysm run-speed change: counter=7, speed=7\n"
        accepted = "Accepted Cataclysm run-speed acknowledgement: counter=7, speed=7\n"
        for log, expected in (
            (prefix + accepted, []),
            (sent + accepted, []),
            (prefix + sent + accepted.replace("counter=7", "counter=8"), []),
            (prefix + sent + accepted.replace("speed=7", "speed=8"), []),
            (prefix + sent + accepted, [{"counter": 7, "speed": 7.0}]),
            (prefix + sent + accepted + accepted, [{"counter": 7, "speed": 7.0}]),
        ):
            (raw / "WorldServer.log").write_text(log)
            assert run_speed_acknowledgements(enum_generation) == expected
        row = [0] * 234
        row[0], row[71], row[95], row[80] = RUN_SPEED_AURA, 6, 31, RUN_SPEED_AURA_AMOUNT - 1
        dbc_header = struct.pack("<4s4I", b"WDBC", 1, 234, 936, 1)
        (raw / "Spell.dbc").write_bytes(dbc_header + struct.pack("<234I", *row) + b"\0")
        verify_run_speed_aura_source(raw)
        row[95] = 0
        (raw / "Spell.dbc").write_bytes(dbc_header + struct.pack("<234I", *row) + b"\0")
        try:
            verify_run_speed_aura_source(raw)
        except RuntimeError:
            pass
        else:
            raise AssertionError("non-speed aura fixture was accepted")
        assert plan_number(BASIC_SPELL_CAST_MODE) == "25"
        for log, expected in (
            ("", 0),
            (IN_WORLD_CONTROL_MARKER + "\nS->C: 1.2.3.4 [SMSG_SPELL_GO 0x0925 (2341)]\n", 0),
            ("Finished object update bootstrap after adding to map\n" + IN_WORLD_CONTROL_MARKER +
             "\nS->C: 1.2.3.4 [SMSG_SPELL_GO 0x0925 (2341)]\n", 1),
        ):
            (raw / "WorldServer.log").write_text(log)
            assert spell_cast_marker_count(enum_generation) == expected
        assert "INSERT INTO `character_spell`" in basic_spell_cast_seed_sql()
        assert f"VALUES ({CHARACTER_GUID},{BASIC_SPELL_CAST_SPELL_ID},1)" in basic_spell_cast_seed_sql()
        assert plan_number(TARGET_TARGETED_SPELL_CAST_MODE) == "25"
        assert plan_number(CHAT_MESSAGE_MODE) == "89"
        assert "INSERT INTO `character_spell`" in target_targeted_spell_cast_seed_sql()
        assert (
            f"VALUES ({CHARACTER_GUID},{TARGET_TARGETED_SPELL_CAST_SPELL_ID},1)"
            in target_targeted_spell_cast_seed_sql()
        )
        assert plan_number(LEVEL_UP_MODE) == "31"
        assert LEVEL_UP_MODE in POPULATED_CHARACTER_MODES
        assert LEVEL_UP_MODE in POST_MARKER_MODES
        assert LEVEL_UP_MODE in CHARACTER_MODES
        # Without a bound Attack button the main-hand keypress sends no CMSG_ATTACK and the kill
        # can never happen, which silently produced an all-zero-marker run.
        level_up_action = level_up_action_seed_sql()
        assert f"INSERT INTO `character_action` (`guid`,`spec`,`button`,`action`,`type`)" in level_up_action
        assert (
            f"VALUES ({CHARACTER_GUID},0,{LEVEL_UP_ATTACK_BUTTON},{LEVEL_UP_ATTACK_SPELL},0)"
            in level_up_action
        )
        assert LEVEL_UP_ATTACK_BUTTON == 0, "the main-hand keypress only reaches action button 0"
        # Tab selected the nearer friendly Deputy Willem, not the seeded hostile, so selection must
        # be by name rather than proximity.
        assert LEVEL_UP_TARGET_KEYS[0] == "Return" and "slash" in LEVEL_UP_TARGET_KEYS
        assert tuple(LEVEL_UP_TARGET_NAME.lower()) == LEVEL_UP_TARGET_KEYS[
            LEVEL_UP_TARGET_KEYS.index("space") + 1:
        ][:len(LEVEL_UP_TARGET_NAME)]
        assert LEVEL_UP_TARGET_KEYS[-1] == "Return"
        level_up_tutorial = level_up_tutorial_seed_sql()
        assert "INSERT INTO `account_tutorial`" in level_up_tutorial
        assert "4294967295" in level_up_tutorial and str(ACCOUNT_ID) in level_up_tutorial
        level_up_gear = level_up_gear_seed_sql()
        assert "INSERT INTO `item_instance`" in level_up_gear and "INSERT INTO `character_inventory`" in level_up_gear
        assert f"({CHARACTER_GUID},0,15,99000004)" in level_up_gear, "the sword must be in the main hand"
        level_up_seed = level_up_world_seed_sql()
        assert "INSERT INTO `creature`" in level_up_seed
        assert f"VALUES ({LEVEL_UP_XP_TARGET_GUID},{LEVEL_UP_XP_TARGET_ENTRY}," in level_up_seed
        # The XP target must be spawned next to the player, not at the world origin.
        seed_pattern = rf"{CHARACTER_MAP},{CHARACTER_ZONE},{CHARACTER_ZONE},(-?[0-9.]+),"
        target_x = float(re.search(seed_pattern, level_up_seed).group(1))
        assert target_x == CHARACTER_POSITION[0] + LEVEL_UP_XP_TARGET_OFFSET[0]
        assert str(TARGET_TARGETED_SPELL_CAST_DUMMY_GUID) in target_targeted_spell_cast_world_seed_sql()
        assert (
            f"VALUES ({TARGET_TARGETED_SPELL_CAST_DUMMY_GUID},{TARGET_TARGETED_SPELL_CAST_DUMMY_ENTRY},"
            in target_targeted_spell_cast_world_seed_sql()
        )
        assert "INSERT INTO `creature`" in target_targeted_spell_cast_world_seed_sql()
        for log, expected in (
            ("", 0),
            (
                "Finished object update bootstrap after adding to map\n" + IN_WORLD_CONTROL_MARKER +
                "\nC->S: CMSG_SET_SELECTION\nC->S: CMSG_CAST_SPELL\nC->S: CMSG_CANCEL_CAST"
                "\nS->C: SMSG_CAST_FAILED\nC->S: CMSG_CAST_SPELL\nS->C: SMSG_SPELL_GO\n", 1
            ),
        ):
            (raw / "WorldServer.log").write_text(log)
            assert target_targeted_spell_cast_marker_count(enum_generation) == expected
        assert plan_number(INVALID_TARGET_SPELL_CAST_MODE) == "25"
        assert "slash" in INVALID_TARGET_SPELL_CAST_KEYS and "space" in INVALID_TARGET_SPELL_CAST_KEYS
        for log, expected in (
            ("", 0),
            (
                "Finished object update bootstrap after adding to map\n" + IN_WORLD_CONTROL_MARKER +
                "\nC->S: 1.2.3.4 [CMSG_SET_SELECTION 0x0506 (1286)]\n", 1
            ),
        ):
            (raw / "WorldServer.log").write_text(log)
            assert invalid_target_spell_cast_marker_count(enum_generation) == expected
    auth_keys = (
        "RealmServerPort", "BindIP", "LogsDir", "PidFile", "RealmsStateUpdateDelay",
        "LoginDatabaseInfo", "Updates.EnableDatabases", "Updates.AutoSetup", "StrictVersionCheck",
        "SourceDirectory", "Appender.Auth", "Logger.root",
    )
    world_keys = (
        "RealmID", "WorldServerPort", "BindIP", "LoginDatabaseInfo", "WorldDatabaseInfo",
        "CharacterDatabaseInfo", "DataDir", "LogsDir", "PidFile", "Console.Enable",
        "Updates.EnableDatabases", "Updates.AutoSetup", "Expansion", "MoveMaps.Enable",
        "vmap.enableLOS", "vmap.enableHeight", "Warden.Enabled", "Ra.Enable", "SOAP.Enabled",
        "Cluster.Enabled", "Appender.Server", "Logger.network", "Logger.network.opcode",
    )
    replace_config(
        (REPO_ROOT / "src/server/apps/authserver/authserver.conf.dist").read_text(),
        {key: "test" for key in auth_keys},
    )
    replace_config(
        (REPO_ROOT / "src/server/apps/worldserver/worldserver.conf.dist").read_text(),
        {key: "test" for key in world_keys},
    )
    assert client_login_points(60, 1, 1800, 1042) == ((971, 560), (971, 650), (971, 785))
    assert x_keysym_name("A") == "a" and x_keysym_name("Escape") == "Escape"
    assert plan_number(WALK_RUN_MODE_SWITCH_MODE) == "23"
    assert WALK_RUN_TOGGLE_KEYS == ("n",)
    assert "TOGGLERUN" in "".join(WALK_RUN_BIND_SETUP_KEYS)
    assert SHIFTED_KEYSYM_NAMES["("] == "9" and SHIFTED_KEYSYM_NAMES[")"] == "0"
    assert SHIFTED_KEYSYM_NAMES['"'] == "apostrophe"
    assert UNSHIFTED_PUNCTUATION_KEYSYM_NAMES[","] == "comma"
    walk_run_samples = [
        {"opcode": opcode, "x": x, "y": y, "z": 0.0, "o": 0.0}
        for opcode, x, y in zip(
            WALK_RUN_MODE_SWITCH_OPCODES, (0, 0, 1, 1, 1, 1), (0, 0, 0, 0, 0, 4),
        )
    ]
    assert walk_run_mode_switch_deltas_are_action_appropriate(walk_run_samples)
    assert not walk_run_mode_switch_deltas_are_action_appropriate(
        [{**sample, "opcode": "MSG_MOVE_STOP"} for sample in walk_run_samples],
    )
    slow_run_samples = [dict(sample) for sample in walk_run_samples]
    slow_run_samples[5]["y"] = 1.3
    assert not walk_run_mode_switch_deltas_are_action_appropriate(slow_run_samples)
    ground_movement_samples_value = [
        {"opcode": opcode, "x": 0.0, "y": 0.0, "z": 0.0, "o": 0.0}
        for opcode in ("MSG_MOVE_FALL_LAND", *GROUND_MOVEMENT_OPCODES)
    ]
    assert [
        str(sample["opcode"]) for sample in ground_movement_action_samples(ground_movement_samples_value)
    ] == list(GROUND_MOVEMENT_OPCODES)
    actions_only = ground_movement_samples_value[-len(GROUND_MOVEMENT_OPCODES):]
    assert ground_movement_action_samples(actions_only) == actions_only
    assert ground_movement_action_samples([]) == []
    # only the world-entry landing is excused: one anywhere inside the actions stays in the list, so
    # the exact-sequence comparison still fails on it
    landing_inside = [
        {"opcode": opcode, "x": 0.0, "y": 0.0, "z": 0.0, "o": 0.0}
        for opcode in ("MSG_MOVE_START_FORWARD", "MSG_MOVE_FALL_LAND", "MSG_MOVE_STOP")
    ]
    assert ground_movement_action_samples(landing_inside) == landing_inside
    window_sample = "0x08400003 0 3597195 124 4 1800 1042 steam_proton.steam_proton host World of Warcraft"
    assert owned_wow_window(window_sample, {3597195}) == ("0x08400003", 124, 4, 1800, 1042)
    assert owned_wow_window(window_sample, {1}) is None
    state: Generation = {"state": "allocating"}
    set_state(state, "prepared")
    try:
        set_state(state, "accepted")
    except RuntimeError:
        pass
    else:
        raise AssertionError("invalid state transition was accepted")
    # geometry staging: the required sets are enforced, and the server settings follow --geometry
    with tempfile.TemporaryDirectory() as temporary:
        data_root = Path(temporary)
        try:
            geometry_input_hashes(data_root)
        except RuntimeError:
            pass
        else:
            raise AssertionError("geometry was accepted without vmaps and mmaps directories")
        (data_root / "vmaps").mkdir()
        try:
            geometry_input_hashes(data_root)
        except RuntimeError:
            pass
        else:
            raise AssertionError("geometry was accepted with an empty vmaps directory")
        (data_root / "vmaps" / "000.vmtree").write_bytes(b"VMAP_4.8")
        (data_root / "mmaps").mkdir()
        (data_root / "mmaps" / "000.mmap").write_bytes(bytes(28))
        hashes = geometry_input_hashes(data_root)
        assert set(hashes) == {"data_vmaps_hash", "data_mmaps_hash"}
        assert all(len(value) == 64 for value in hashes.values())

        for geometry, expected in ((False, "0"), (True, "1")):
            config_dir = Path(temporary) / f"config-{int(geometry)}"
            geometry_generation: Generation = {
                "mode": "authentication",
                "ports": {"mysql": 3306, "world": 18085},
                "schemas": {"auth": "auth", "world": "world", "characters": "characters"},
                "paths": {
                    "config": str(config_dir), "logs": str(config_dir / "logs"),
                    "data": str(config_dir / "data"),
                    "auth_config": str(config_dir / "authserver.conf"),
                    "world_config": str(config_dir / "worldserver.conf"),
                },
                "inputs": {"geometry": geometry} if geometry else {},
            }
            write_configs({"mysql_user": "u", "mysql_password": "p"}, geometry_generation)
            world_text = (config_dir / "worldserver.conf").read_text()
            for key in ("MoveMaps.Enable", "vmap.enableLOS", "vmap.enableHeight"):
                assert re.search(rf"^{key}\s*=\s*{expected}\s*$", world_text, re.MULTILINE), (key, geometry)

    # reusing a prepared generation is idempotent only for identical geometry inputs
    assert not geometry_inputs_differ({"geometry": False}, {"geometry": False})
    assert geometry_inputs_differ({"geometry": False}, {"geometry": True})
    assert geometry_inputs_differ({"geometry": True}, {"geometry": False})
    same_geometry = {"geometry": True, "data_vmaps_hash": "a" * 64, "data_mmaps_hash": "b" * 64}
    assert not geometry_inputs_differ(same_geometry, dict(same_geometry))
    assert geometry_inputs_differ(same_geometry, {**same_geometry, "data_vmaps_hash": "c" * 64})
    assert geometry_inputs_differ(same_geometry, {**same_geometry, "data_mmaps_hash": "c" * 64})

    # the exported identity has to distinguish geometry configurations
    def exported_inputs(geometry: bool) -> dict[str, object]:
        geometry_generation: Generation = {
            "mode": "authentication",
            "ports": {"world": 18085},
            "processes": [],
            "paths": {"raw_evidence": "/abs/private"},
            "inputs": {
                "authserver": {"sha256": "a" * 64}, "worldserver": {"sha256": "b" * 64},
                "data_dbc_hash": "c" * 64, "data_maps_hash": "d" * 64,
                "geometry": geometry,
                "data_vmaps_hash": "e" * 64 if geometry else "",
                "data_mmaps_hash": "f" * 64 if geometry else "",
            },
        }
        return sanitized_evidence(geometry_generation, [name for name, _ in CLIENT_MILESTONES], [])["inputs"]

    assert exported_inputs(False)["geometry"] is False
    assert exported_inputs(True)["geometry"] is True
    assert exported_inputs(False) != exported_inputs(True)
    assert exported_inputs(True)["vmaps_tree_sha256"] == "e" * 64

    # Plan 32 geometry mode: wire correlation against a synthetic capture
    assert PLAN32_GEOMETRY_MODE in POPULATED_CHARACTER_MODES
    assert PLAN32_GEOMETRY_MODE in POST_MARKER_MODES
    assert plan_number(PLAN32_GEOMETRY_MODE) == "32"
    # Server-assigned runtime lows come from the map's own sequence generator (grid loading
    # creates units with GenerateLowGuid, keeping the DB guid only as spawnId), so no formula can
    # predict the fixture's wire GUID. The synthetic value below only exercises the parser and the
    # selection-anchored correlation shape.
    fixture = 0xF13000016900064A
    assert set(PLAN32_GEOMETRY_SEEDS) == set(PLAN32_GEOMETRY_STAGES) == {"blocked", "clear", "chase", "relog"}
    assert "-8854.766" in plan32_world_seed_sql("blocked") and "80.422" in plan32_world_seed_sql("blocked")
    assert ",361,0,12,12," in plan32_world_seed_sql("blocked")
    assert ",2809," not in plan32_world_seed_sql("blocked")
    assert PLAN32_GEOMETRY_TARGET_KEYS[0] == "Return" and "slash" in PLAN32_GEOMETRY_TARGET_KEYS
    assert tuple(PLAN32_GEOMETRY_DUMMY_NAME) == PLAN32_GEOMETRY_TARGET_KEYS[
        PLAN32_GEOMETRY_TARGET_KEYS.index("space") + 1:
    ][:len(PLAN32_GEOMETRY_DUMMY_NAME)]
    assert "-8798.946" in plan32_world_seed_sql("clear") and "82.845" in plan32_world_seed_sql("clear")
    assert f"VALUES ({CHARACTER_GUID},0,0,{PLAN32_GEOMETRY_SPELL_ID},0)" in plan32_spell_seed_sql()

    def pack_guid(value: int) -> bytes:
        mask = 0
        body = b""
        for index in range(8):
            byte = (value >> (index * 8)) & 0xFF
            if byte:
                mask |= 1 << index
                body += bytes([byte])
        return bytes([mask]) + body

    def pkt(direction: int, opcode: int, payload: bytes, ticks: int = 0) -> bytes:
        return (
            struct.pack("<IIIII", direction, 0, ticks, 20, len(payload) + 4)
            + bytes(20) + struct.pack("<I", opcode) + payload
        )

    C2S, S2C = 0x47534D43, 0x47534D53
    player = pack_guid(CHARACTER_GUID)
    fixture_packed = pack_guid(fixture)
    fixture_full = struct.pack("<Q", fixture)
    # CMSG_CAST_SPELL as the client writes it: count, spell, misc, castFlags, then the target mask
    # and the packed GUID. The castFlags byte is easy to drop and silently moves the mask, which is
    # why the decoded target is asserted below.
    # A real single-block SMSG_UPDATE_OBJECT from the accepted generation 6 capture (a unit create
    # with its movement block): the witness's block walk and the cast-position ticks are pinned
    # against the server's own writer instead of against a hand-built block that could share a
    # misreading. The block's own GUID and create position are asserted below.
    real_create_block = bytes.fromhex(
        "00000100000001db8c05010130f1030100000002c91e80000000904000002040e4b7a5421f2f09c6c3f54840008d711c"
        "9740be5fe1c2f004000020407eb70300e00f4940310000e0403012b840d6ffbf400000904005730000060100c77b0026"
        "000000000000008000008c050001010030f109000000010100000000803f000100004700000047000000030000001900"
        "0000ffc400000008000000004000d0070000d0070000d34d423f0000b83ffb080000fb080000000000000000803f0000"
        "00000000803f"
    )
    synthetic = (
        b"PKT" + bytes(63)
        + pkt(C2S, CMSG_SET_SELECTION_OPCODE, fixture_full)
        + pkt(C2S, CMSG_CAST_SPELL_OPCODE,
              bytes([1]) + struct.pack("<I", 133) + bytes(4) + bytes([0])
              + struct.pack("<I", 2) + fixture_packed)
        + pkt(S2C, SMSG_CAST_FAILED_OPCODE, bytes([1]) + struct.pack("<I", 133) + bytes([49]))
        + pkt(C2S, CMSG_CAST_SPELL_OPCODE,
              bytes([2]) + struct.pack("<I", 133) + bytes(4) + bytes([0])
              + struct.pack("<I", 2) + fixture_packed)
        + pkt(S2C, SMSG_CAST_FAILED_OPCODE, bytes([2]) + struct.pack("<I", 133) + bytes([49]))
        + pkt(S2C, SMSG_SPELL_GO_OPCODE, player + player + bytes([3]) + struct.pack("<I", 133))
        + pkt(C2S, CMSG_CANCEL_CAST_OPCODE, bytes([0]) + struct.pack("<I", 133))
        + pkt(S2C, SMSG_UPDATE_OBJECT_OPCODE, real_create_block)
    )
    with tempfile.TemporaryDirectory() as temporary:
        logs = Path(temporary) / "logs"
        logs.mkdir()
        (logs / "packets.pkt").write_bytes(synthetic)
        probe_generation: Generation = {"paths": {"logs": str(logs)}}
        events = plan32_packet_events(probe_generation)
        creates = plan32_object_creates(probe_generation)
        real_guid = 0xF13000010100058C
        witness = plan32_fixture_witness(probe_generation, real_guid)
        absent_witness = plan32_fixture_witness(probe_generation, 0xF1300001010005D5)
    assert [event["opcode"] for event in events] == [
        "CMSG_SET_SELECTION", "CMSG_CAST_SPELL", "SMSG_CAST_FAILED",
        "CMSG_CAST_SPELL", "SMSG_CAST_FAILED", "SMSG_SPELL_GO", "CMSG_CANCEL_CAST",
    ]
    assert events[0]["target"] == fixture
    cast_requests = [
        (e.get("count"), e.get("spell")) for e in events if e["opcode"] == "CMSG_CAST_SPELL"
    ]
    assert cast_requests == [(1, 133), (2, 133)]
    # The castFlags byte sits between misc and the mask at offset 10: the target has to come back.
    assert all(event.get("target") == fixture for event in events
               if event["opcode"] == "CMSG_CAST_SPELL")
    failed = [
        (e.get("count"), e.get("spell"), e.get("result"))
        for e in events if e["opcode"] == "SMSG_CAST_FAILED"
    ]
    assert failed == [(1, 133, 49), (2, 133, 49)]
    assert events[5]["caster"] == CHARACTER_GUID and events[5]["spell"] == 133
    assert selections_target_fixture(events) == fixture
    assert selections_target_fixture([
        {"opcode": "CMSG_SET_SELECTION", "target": CHARACTER_GUID},
        {"opcode": "CMSG_SET_SELECTION", "target": fixture},
    ]) is None
    assert selections_target_fixture([]) is None
    # The update-object walk reproduces the real block's own numbers, and an unselected GUID has no
    # witness at all.
    assert any(block["guid"] == real_guid for block in creates)
    assert witness == {
        "guid": real_guid, "type_id": TYPEID_UNIT, "entry": 257, "entry_from_guid": 257,
        "guid_high": HIGHGUID_UNIT,
        "position": [-8779.7802734375, -112.68699645996094, 82.85916137695312],
        "orientation": 5.752220153808594,
    }
    assert absent_witness is None
    # Counter correlation: the synthetic capture's two answered casts are clean, a cancelled cast
    # and a mismatched answer are each reported, and an unanswered request is reported too.
    synthetic_events = events[:-1]
    assert plan32_correlation_problems("blocked", fixture, synthetic_events) == []
    assert plan32_correlation_problems("blocked", fixture, synthetic_events + [
        {"opcode": "CMSG_CANCEL_CAST", "side": "c2s", "index": 99},
    ]) == []
    cancelled = events[:2] + [{"opcode": "CMSG_CANCEL_CAST", "side": "c2s", "index": 2}] + events[2:]
    assert plan32_correlation_problems("blocked", fixture, cancelled) == [
        "cast 1 was cancelled before its answer",
    ]
    assert plan32_correlation_problems("blocked", fixture, synthetic_events[:-2] + [
        {"opcode": "SMSG_CAST_FAILED", "count": 7, "spell": 133, "result": 49},
    ]) == ["cast 2 was answered with counter 7"]
    assert plan32_correlation_problems("blocked", fixture, synthetic_events[:-2]) == [
        "cast 2 had no answer",
    ]
    assert plan32_correlation_problems("chase", fixture, synthetic_events) == [
        "cast counters [1, 2] are not the expected [1]",
    ]
    # Derived-line pins: a seed sitting on the line's first endpoint and one sitting along it are
    # both described, and the eye-height lift is compared against the seed's own z.
    line = {"first": [0.0, 0.0, 10.0], "second": [30.0, 0.0, 9.0], "length": 30.0}
    first_pin = plan32_line_pin(line, [-0.1, 0.0, 10.0 - 2.03128], 2.03128)
    assert first_pin["along"] == -0.1 and first_pin["perpendicular"] == 0.0
    assert abs(first_pin["endpoint_z_delta"]) < 1e-12
    along_pin = plan32_line_pin(line, [12.0, 0.2, 7.0], 2.03128)
    assert abs(along_pin["along"] - 12.0) < 1e-9 and abs(along_pin["perpendicular"] - 0.2) < 1e-9
    # Geometry facts: key parsing, the staged copies' own hashes, the data directory the server
    # was pointed at and the support line. The staged tree is tampered after the first read, so
    # the hash has to move with the bytes instead of echoing the recorded input.
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        (root / "logs").mkdir()
        (root / "data/vmaps").mkdir(parents=True)
        (root / "data/mmaps").mkdir(parents=True)
        (root / "data/maps").mkdir(parents=True)
        (root / "data/vmaps/a.vmo").write_bytes(b"v")
        (root / "data/mmaps/a.mmap").write_bytes(b"m")
        (root / "data/maps/0000000.map").write_bytes(b"t")
        staged_vmaps = tree_content_hash(root / "data/vmaps")
        staged_mmaps = tree_content_hash(root / "data/mmaps")
        staged_maps = tree_content_hash(root / "data/maps")
        (root / "worldserver.conf").write_text(
            f'DataDir = "{root / "data"}"\nMoveMaps.Enable = 1\nvmap.enableLOS = true\n'
        )
        (root / "logs" / "worldserver.stdout.log").write_text(
            "WORLD: VMap support included. LineOfSight:true, getHeight:true\n"
        )
        facts_generation: Generation = {
            "inputs": {"data_root": "/data", "data_vmaps_hash": staged_vmaps,
                       "data_mmaps_hash": staged_mmaps, "data_maps_hash": staged_maps},
            "paths": {"data": str(root / "data"), "world_config": str(root / "worldserver.conf"),
                      "logs": str(root / "logs")},
        }
        facts = plan32_geometry_facts(facts_generation)
        # The terrain the height gate reads is the maps tree, so it is hashed with the rest: a
        # maps-only change moves a checked fingerprint even though vmaps and mmaps are untouched.
        (root / "data/maps/0000000.map").write_bytes(b"different terrain")
        terrain_tampered = plan32_geometry_facts(facts_generation)
        (root / "data/vmaps/a.vmo").write_bytes(b"tampered")
        tampered = plan32_geometry_facts(facts_generation)
    assert facts["config"] == {
        "DataDir": str(root / "data"), "MoveMaps.Enable": "1", "vmap.enableLOS": "true",
        "vmap.enableHeight": None,
    }
    assert facts["data_dir"] == str(root / "data")
    assert facts["maps_hash"] == staged_maps
    assert facts["vmaps_hash"] == staged_vmaps and facts["mmaps_hash"] == staged_mmaps
    assert tampered["vmaps_hash"] != staged_vmaps
    assert terrain_tampered["maps_hash"] != staged_maps
    assert terrain_tampered["vmaps_hash"] == staged_vmaps and (
        terrain_tampered["mmaps_hash"] == staged_mmaps
    )
    assert "LineOfSight:true" in str(facts["support_line"])
    # Every accepted movement sample is held against the map height under it: the real stages
    # departed by up to 1.47 m (a step onto the courtyard wall), so those pass, while a spike or
    # a sample with no staged tile under it is a problem.
    assert plan32_terrain_problems({"samples": [
        {"opcode": "MSG_MOVE_FALL_LAND", "delta": 0.0},
        {"opcode": "MSG_MOVE_STOP", "delta": 1.47},
    ]}) == []
    assert plan32_terrain_problems({"samples": [
        {"opcode": "MSG_MOVE_START_FORWARD", "delta": 100.0},
    ]}) == ["MSG_MOVE_START_FORWARD stands 100.000 m off the staged terrain"]
    assert plan32_terrain_problems({"samples": [
        {"opcode": "MSG_MOVE_STOP", "delta": None},
    ]}) == ["MSG_MOVE_STOP has no staged terrain height under it"]
    # The staged mmap route the chase's detour requirement rests on: a three-polygon L of rooms on
    # disk (mmaps/000.mmap plus the grid tile) routes around the corner, longer than the straight
    # line between its ends, and both ends are handed in as full 3D points, the chase call site
    # included. The query's frame is dtPoint = (world y,
    # world z, world x), so the rooms are written in it (nav0 = world y, nav1 = world z,
    # nav2 = world x): A at y 31..32, B at y 32..33 x 31..32, C at y 32..33 x 32..33.
    mesh = [(31.0, 40.0, 31.0), (32.0, 40.0, 31.0), (32.0, 40.0, 32.0), (31.0, 40.0, 32.0),
            (33.0, 40.0, 31.0), (33.0, 40.0, 32.0), (33.0, 40.0, 33.0), (32.0, 40.0, 33.0)]
    module = plan32_geometry_module()
    room = [((0, 1, 2, 3), (0, 2, 0, 0), module.NAV_GROUND),
            ((1, 4, 5, 2), (0, 0, 3, 1), module.NAV_GROUND),
            ((2, 5, 6, 7), (2, 0, 0, 0), module.NAV_GROUND)]
    route_start, route_end = [31.5, 31.5, 40.0], [32.5, 32.5, 40.0]
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        (root / "mmaps").mkdir()
        gx, gy = module.grid_coord(route_start[0], route_start[1])
        (root / "mmaps" / f"{CHARACTER_MAP:03d}.mmap").write_bytes(module.build_mmap())
        (root / "mmaps" / f"{CHARACTER_MAP:03d}{gx:02d}{gy:02d}.mmtile").write_bytes(
            module.build_mmtile(0, 0, (-200.0, 0.0, -200.0), (200.0, 200.0, 200.0),
                                arrays=(mesh, room))
        )
        route = plan32_route_facts(root, route_start, route_end)
    assert route["start_polygon"] == 0 and route["end_polygon"] == 2, route
    assert route["route"] is not None and route["hops"] == 3, route
    assert route["ratio"] is not None and route["ratio"] > 1.0, route
    # A spline packet advertises a destination and a duration, so both a cast-time position and a
    # superseded spline's executed portion are read off its own constant-speed walk (a 3-4-5
    # triangle keeps the numbers exact).
    assert plan32_polyline_point([(0.0, 0.0, 0.0), (3.0, 4.0, 0.0)], 0.5) == [1.5, 2.0, 0.0]
    assert plan32_polyline_point([(0.0, 0.0, 0.0), (3.0, 4.0, 0.0)], 2.0) == [3.0, 4.0, 0.0]
    assert plan32_polyline_point([], 0.5) is None
    assert plan32_polyline_span_shares([(0.0, 0.0, 0.0), (3.0, 4.0, 0.0)], 0.0, 0.5) == [
        (0.0, [0.0, 0.0, 0.0]), (0.5, [1.5, 2.0, 0.0]),
    ]
    # The arrival is solved on the segment the walk is on: a leg enters the radius part-way, the
    # floor moves the entry later when the stop lands inside the leg, and a leg that has already
    # left the radius never reports an entry.
    assert plan32_segment_entry(
        [0.0, -10.0, 0.0], [0.0, 10.0, 0.0], 0, 1000, (0.0, 0.0), 6.0, 0,
    )["ticks"] == 200
    assert plan32_segment_entry(
        [0.0, -10.0, 0.0], [0.0, 10.0, 0.0], 0, 1000, (0.0, 0.0), 6.0, 500,
    )["ticks"] == 500
    assert plan32_segment_entry(
        [0.0, -10.0, 0.0], [0.0, 10.0, 0.0], 0, 1000, (0.0, 0.0), 6.0, 900,
    ) is None
    assert plan32_segment_entry(
        [0.0, -10.0, 0.0], [0.0, 10.0, 0.0], 0, 1000, (0.0, 0.0), 1.0, 0,
    )["ticks"] == 450
    # A ground track that does not move is in range the whole segment however its height changes,
    # the moment a walk crosses the radius is itself an arrival (the boundary is inclusive), and a
    # graze - exactly one touch, no crossing - is an arrival at the touch rather than a miss.
    assert plan32_segment_entry(
        [0.0, 0.0, 0.0], [0.0, 0.0, 10.0], 0, 1000, (0.0, 0.0), 6.0, 500,
    )["ticks"] == 500
    assert plan32_segment_entry(
        [0.0, -10.0, 0.0], [0.0, 10.0, 0.0], 0, 1000, (0.0, 0.0), 6.0, 800,
    )["ticks"] == 800
    assert plan32_segment_entry(
        [0.0, -10.0, 0.0], [0.0, 10.0, 0.0], 0, 1000, (0.0, 0.0), 6.0, 801,
    ) is None
    graze = plan32_segment_entry(
        [5.0, 10.0, 0.0], [15.0, 10.0, 0.0], 5700, 8700, (10.0, 4.0), 6.0, 3000,
    )
    assert graze is not None and graze["ticks"] == 7200 and graze["point"] == [10.0, 10.0, 0.0]
    # Chase pieces: monster-move bodies (compressed and uncompressed, stop form included),
    # attackerstate, metrics.
    assert PLAN32_EXPECTED_COUNTS == {
        "blocked": [1, 2], "clear": [1, 2], "chase": [1], "relog": [],
    }
    assert PLAN32_GEOMETRY_SEEDS["chase"]["character"] == (-8876.0, -208.0, 81.313, math.pi / 2)
    assert PLAN32_GEOMETRY_SEEDS["chase"]["dummy"] == (-8876.0, -196.0, 80.221)
    assert PLAN32_CHASE_P2 == (-8870, -202)
    mover_packed = pack_guid(fixture)

    def move_pkt(extra: bytes, flags: int = 0, duration: int = 1000,
                 start: tuple[float, float, float] = (0.0, 0.0, 0.0), facing: int = 0,
                 mover: bytes = mover_packed) -> bytes:
        # mover, exit byte, real position, spline id, facing, flags, duration, then the path
        return (mover + bytes([0]) + struct.pack("<fff", *start)
                + struct.pack("<I", 7) + bytes([facing]) + struct.pack("<I", flags)
                + struct.pack("<i", duration) + extra)

    def straight(destination: tuple[float, float, float],
                 start: tuple[float, float, float] = (0.0, 0.0, 0.0)) -> bytes:
        # A one-segment path: the compressed form with last_idx 1 and only the destination.
        return struct.pack("<I", 1) + struct.pack("<fff", *destination)

    def stop_pkt(mover: bytes = mover_packed) -> bytes:
        # PacketBuilder::WriteStopMovement: spline id, stop facing, empty flags/duration/path.
        return (mover + bytes([0]) + struct.pack("<fff", 0.0, 0.0, 0.0)
                + struct.pack("<I", 7) + bytes([1]) + struct.pack("<III", 0, 0, 0))

    def pack_xyz(x: float, y: float, z: float) -> bytes:
        packed = (int(x / 0.25) & 0x7FF) | ((int(y / 0.25) & 0x7FF) << 11) | ((int(z / 0.25) & 0x3FF) << 22)
        return struct.pack("<I", packed)

    def midpoint(start: tuple, end: tuple) -> tuple:
        return tuple((a + b) / 2.0 for a, b in zip(start, end))

    def chase_log(final: tuple[float, float]) -> str:
        return (
            "Finished object update bootstrap after adding to map\n" + IN_WORLD_CONTROL_MARKER
            + "\nAccepted Cataclysm ground movement [MSG_MOVE_STOP 0x320A (12810)] after "
            + f"movement validation: x={final[0]}, y={final[1]}, z=0.0, o=0.0\n"
        )

    # 11-bit x/y and 10-bit z at 0.25 steps, sign-extended: maxima are 1023 and 511 steps.
    assert unpack_pack_xyz(0x3FF | (0x3FF << 11) | (0x1FF << 22)) == (255.75, 255.75, 127.75)
    assert unpack_pack_xyz(0) == (0.0, 0.0, 0.0)
    assert unpack_pack_xyz(struct.unpack("<I", pack_xyz(-2.5, -1.0, -0.5))[0]) == (-2.5, -1.0, -0.5)
    # L-shaped detour: (0,0) -> (0,10) -> (10,10): length 20, straight ~14.14, ratio ~1.41, and
    # a purely vertical step is a gap even though its horizontal distance is zero.
    assert plan32_path_metrics([(0.0, 0.0, 0.0), (0.0, 10.0, 0.0), (10.0, 10.0, 0.0)])[0:2] == (
        20.0, 14.142135623730951,
    )
    assert plan32_path_metrics([(0.0, 0.0, 0.0), (0.0, 0.0, 100.0)])[2] == 100.0
    uncompressed = move_pkt(
        struct.pack("<I", 2) + struct.pack("<ffffff", 0, 10, 0, 10, 10, 0),
        SPLINE_FLAG_UNCOMPRESSED_PATH,
    )
    dest, off1, off2 = (10.0, 0.0, 0.0), (2.5, 0.0, 0.0), (7.5, 0.0, 0.0)
    middle = midpoint((0.0, 0.0, 0.0), dest)
    stored1 = tuple(middle[i] - off1[i] for i in range(3))
    stored2 = tuple(middle[i] - off2[i] for i in range(3))
    compressed = move_pkt(
        struct.pack("<I", 3) + struct.pack("<fff", *dest)
        + pack_xyz(*stored1) + pack_xyz(*stored2)
    )
    swing = (struct.pack("<I", 0) + pack_guid(fixture) + pack_guid(CHARACTER_GUID)
             + struct.pack("<I", 5))
    with tempfile.TemporaryDirectory() as temporary:
        logs = Path(temporary) / "logs"
        logs.mkdir()
        (logs / "packets.pkt").write_bytes(
            b"PKT" + bytes(63)
            + pkt(S2C, SMSG_ON_MONSTER_MOVE_OPCODE, uncompressed, ticks=1000)
            + pkt(S2C, SMSG_ON_MONSTER_MOVE_OPCODE, stop_pkt(), ticks=2500)
            + pkt(S2C, SMSG_ON_MONSTER_MOVE_OPCODE, compressed, ticks=3000)
            + pkt(S2C, SMSG_ATTACKERSTATEUPDATE_OPCODE, swing, ticks=4000)
        )
        parsed_generation: Generation = {"paths": {"logs": str(logs)}}
        parsed_moves = plan32_monster_moves(parsed_generation)
        parsed_swings = plan32_attackerstate(parsed_generation)
    # The stop form (WriteStopMovement) carries no destination and used to abort the whole parse;
    # it is a stop that closes the running spline at its own tick instead.
    assert [move["stop"] for move in parsed_moves] == [False, True, False]
    assert [len(move["points"]) for move in parsed_moves] == [3, 0, 4]
    assert [move["ticks"] for move in parsed_moves] == [1000, 2500, 3000]
    assert parsed_moves[1]["points"] == [] and parsed_moves[1]["mover"] == fixture
    assert parsed_moves[2]["points"][-1] == (10.0, 0.0, 0.0)
    assert len(parsed_swings) == 1 and parsed_swings[0]["damage"] == 5
    assert parsed_swings[0]["ticks"] == 4000
    assert parsed_swings[0]["attacker"] == fixture and parsed_swings[0]["victim"] == CHARACTER_GUID
    # The fixture's position at a cast's tick: its create position walked along its own spline
    # (halfway to a destination 100 m away, never the destination itself), then frozen at the
    # tick of a stop that interrupted that spline.
    create_position = (-8779.7802734375, -112.68699645996094, 82.85916137695312)
    walk = move_pkt(
        straight((create_position[0] + 100.0, create_position[1], create_position[2])),
        duration=10000, start=create_position, mover=pack_guid(real_guid),
    )
    real_cast = (bytes([1]) + struct.pack("<I", 133) + bytes(4) + bytes([0])
                 + struct.pack("<I", 2) + pack_guid(real_guid))
    with tempfile.TemporaryDirectory() as temporary:
        logs = Path(temporary) / "logs"
        logs.mkdir()
        (logs / "packets.pkt").write_bytes(
            b"PKT" + bytes(63)
            + pkt(S2C, SMSG_UPDATE_OBJECT_OPCODE, real_create_block)
            + pkt(S2C, SMSG_ON_MONSTER_MOVE_OPCODE, walk, ticks=1000)
            + pkt(C2S, CMSG_CAST_SPELL_OPCODE, real_cast, ticks=6000)
            + pkt(S2C, SMSG_ON_MONSTER_MOVE_OPCODE, stop_pkt(pack_guid(real_guid)), ticks=7000)
            + pkt(C2S, CMSG_CAST_SPELL_OPCODE, real_cast, ticks=8000)
        )
        cast_generation: Generation = {"paths": {"logs": str(logs)}}
        cast_positions = plan32_cast_fixture_positions(cast_generation, real_guid)
    assert [item["count"] for item in cast_positions] == [1, 1]
    halfway, frozen = (item["position"] for item in cast_positions)
    assert abs(halfway[0] - (create_position[0] + 50.0)) < 0.01
    assert abs(halfway[1] - create_position[1]) < 0.01 and halfway[2] == create_position[2]
    assert abs(frozen[0] - (create_position[0] + 60.0)) < 0.01
    # Plan 35: the spell-damage-log and power-update decoders (identical layouts in AC and the pin).
    damage_log = (
        pack_guid(CHARACTER_GUID) + pack_guid(real_guid)
        + struct.pack("<III", 133, 17, 0) + bytes([4]) + bytes(4 + 4 + 1 + 1 + 4 + 4 + 1)
    )
    power_log = pack_guid(CHARACTER_GUID) + struct.pack("<I", 1) + bytes([0]) + struct.pack("<i", 83)
    with tempfile.TemporaryDirectory() as temporary:
        logs = Path(temporary) / "logs"
        logs.mkdir()
        (logs / "packets.pkt").write_bytes(
            b"PKT" + bytes(63)
            + pkt(S2C, SMSG_SPELLNONMELEEDAMAGELOG_OPCODE, damage_log, ticks=10)
            + pkt(S2C, SMSG_POWER_UPDATE_OPCODE, power_log, ticks=11)
        )
        combat_events = combat_packet_events({"paths": {"logs": str(logs)}})
    assert [event["opcode"] for event in combat_events] == [
        "SMSG_SPELLNONMELEEDAMAGELOG", "SMSG_POWER_UPDATE"], combat_events
    assert combat_events[0]["attacker"] == real_guid and combat_events[0]["damage"] == 17
    assert combat_events[0]["spell"] == 133 and combat_events[0]["overkill"] == 0
    assert combat_events[1]["guid"] == CHARACTER_GUID and combat_events[1]["powers"] == [[0, 83]]
    # The chase window starts at the run leg: an approach spline ending within arrival range of
    # the player's final position is not the chase, so the executed path is the chase spline's
    # own L-shaped detour (30 m against a 10 m straight line). A swing only counts once the path
    # has reached the arrival and the player's own strafe stop has landed, so a hit landed while
    # the fixture was still on its way does not stand in for the chase reaching melee range.
    approach = move_pkt(straight((4.0, 0.0, 0.0)), duration=500)
    detour = move_pkt(
        struct.pack("<I", 3) + struct.pack("<fffffffff", 0, 10, 0, 10, 10, 0, 10, 0, 0),
        SPLINE_FLAG_UNCOMPRESSED_PATH, duration=800,
    )
    with tempfile.TemporaryDirectory() as temporary:
        logs = Path(temporary) / "logs"
        logs.mkdir()
        (logs / "WorldServer.log").write_text(chase_log((10.0, 0.0)))
        (logs / "packets.pkt").write_bytes(
            b"PKT" + bytes(63)
            + pkt(C2S, CMSG_SET_SELECTION_OPCODE, struct.pack("<Q", fixture))
            + pkt(S2C, SMSG_ON_MONSTER_MOVE_OPCODE, approach, ticks=100)
            + pkt(S2C, SMSG_ATTACKERSTATEUPDATE_OPCODE, swing, ticks=500)
            + pkt(C2S, MSG_MOVE_START_FORWARD_OPCODE, b"", ticks=1000)
            + pkt(S2C, SMSG_ON_MONSTER_MOVE_OPCODE, detour, ticks=1200)
            + pkt(S2C, SMSG_ATTACKERSTATEUPDATE_OPCODE, swing, ticks=1500)
            + pkt(C2S, MSG_MOVE_STOP_STRAFE_OPCODE, b"", ticks=2600)
            + pkt(S2C, SMSG_ATTACKERSTATEUPDATE_OPCODE, swing, ticks=2700)
            + pkt(S2C, 0x3914, b"", ticks=3000)
        )
        leg_generation: Generation = {"paths": {"logs": str(logs), "raw_evidence": str(logs)}}
        chase_leg = plan32_chase_tracks(leg_generation)
    assert chase_leg["target"] == fixture and chase_leg["run_ticks"] == 1000
    # The fixture stood on the arrival point through the stop, so the arrival is dated to the
    # stop (the earliest moment the player was there too) rather than to the spline's own end.
    assert chase_leg["final_stop_ticks"] == 2600 and chase_leg["arrival_ticks"] == 2600
    assert chase_leg["executed"][0] == [0.0, 0.0, 0.0]
    assert chase_leg["executed"][-1] == [10.0, 0.0, 0.0]
    assert chase_leg["joins"] == []
    leg_length, leg_straight, leg_gap = plan32_path_metrics([tuple(p) for p in chase_leg["executed"]])
    assert leg_length == 30.0 and leg_straight == 10.0 and leg_gap == 10.0
    assert len(chase_leg["swings"]) == 1 and chase_leg["swings"][0]["ticks"] == 2700
    # The path ends at the arrival rather than at the capture's end: a fixture that reaches the
    # player before the stop and then walks away is cut at the first measured point at or after
    # the stop that stands within arrival range, so the return walk is not the measured chase.
    home = move_pkt(straight((30.0, 0.0, 0.0), (10.0, 0.0, 0.0)), duration=1200,
                    start=(10.0, 0.0, 0.0))
    with tempfile.TemporaryDirectory() as temporary:
        logs = Path(temporary) / "logs"
        logs.mkdir()
        (logs / "WorldServer.log").write_text(chase_log((10.0, 0.0)))
        (logs / "packets.pkt").write_bytes(
            b"PKT" + bytes(63)
            + pkt(C2S, CMSG_SET_SELECTION_OPCODE, struct.pack("<Q", fixture))
            + pkt(C2S, MSG_MOVE_START_FORWARD_OPCODE, b"", ticks=1000)
            + pkt(S2C, SMSG_ON_MONSTER_MOVE_OPCODE, detour, ticks=1200)
            + pkt(C2S, MSG_MOVE_STOP_STRAFE_OPCODE, b"", ticks=2600)
            + pkt(S2C, SMSG_ON_MONSTER_MOVE_OPCODE, home, ticks=2800)
            + pkt(S2C, SMSG_ATTACKERSTATEUPDATE_OPCODE, swing, ticks=2900)
            + pkt(S2C, 0x3914, b"", ticks=4000)
        )
        arrival_generation: Generation = {"paths": {"logs": str(logs), "raw_evidence": str(logs)}}
        arrived = plan32_chase_tracks(arrival_generation)
    assert arrived["executed"][-1] == [10.0, 0.0, 0.0]
    assert arrived["arrival_ticks"] == 2600 and arrived["joins"] == []
    assert plan32_path_metrics([tuple(p) for p in arrived["executed"]])[0:2] == (30.0, 10.0)
    assert len(arrived["swings"]) == 1 and arrived["swings"][0]["ticks"] == 2900
    # An approach spline still running when the player starts is not the arrival either: its end
    # comes within arrival range of the player's final position, but the strafe stop lands after
    # it, so the chase leg that follows is still measured instead of being discarded.
    approach_end = (-8875.5, -204.2, 0.0)
    overlap = move_pkt(straight(approach_end, (-8876.0, -196.0, 0.0)), duration=1000,
                       start=(-8876.0, -196.0, 0.0))
    chase_leg2 = move_pkt(straight((-8870.0, -202.0, 0.0), approach_end), duration=1000,
                          start=approach_end)
    with tempfile.TemporaryDirectory() as temporary:
        logs = Path(temporary) / "logs"
        logs.mkdir()
        (logs / "WorldServer.log").write_text(chase_log((-8870.0, -202.0)))
        (logs / "packets.pkt").write_bytes(
            b"PKT" + bytes(63)
            + pkt(C2S, CMSG_SET_SELECTION_OPCODE, struct.pack("<Q", fixture))
            + pkt(S2C, SMSG_ON_MONSTER_MOVE_OPCODE, overlap, ticks=900)
            + pkt(C2S, MSG_MOVE_START_FORWARD_OPCODE, b"", ticks=1000)
            + pkt(S2C, SMSG_ON_MONSTER_MOVE_OPCODE, chase_leg2, ticks=2000)
            + pkt(C2S, MSG_MOVE_STOP_STRAFE_OPCODE, b"", ticks=3000)
            + pkt(S2C, SMSG_ATTACKERSTATEUPDATE_OPCODE, swing, ticks=3100)
            + pkt(S2C, 0x3914, b"", ticks=4000)
        )
        overlap_generation: Generation = {"paths": {"logs": str(logs), "raw_evidence": str(logs)}}
        overlapped = plan32_chase_tracks(overlap_generation)
    assert overlapped["executed"][-1] == [-8870.0, -202.0, 0.0]
    assert overlapped["arrival_ticks"] >= 3000 and overlapped["joins"] == []
    assert len(overlapped["swings"]) == 1 and overlapped["swings"][0]["ticks"] == 3100
    # Straight replans must not manufacture a detour: every spline is read only up to the tick
    # the next one started, so the executed path is the straight line the fixture walked.
    replan_a = move_pkt(straight((10.0, 0.0, 0.0)))
    replan_b = move_pkt(straight((15.0, 0.0, 0.0), (5.0, 0.0, 0.0)), start=(5.0, 0.0, 0.0))
    replan_c = move_pkt(straight((20.0, 0.0, 0.0), (10.0, 0.0, 0.0)), start=(10.0, 0.0, 0.0))
    with tempfile.TemporaryDirectory() as temporary:
        logs = Path(temporary) / "logs"
        logs.mkdir()
        (logs / "WorldServer.log").write_text(chase_log((0.0, 100.0)))
        (logs / "packets.pkt").write_bytes(
            b"PKT" + bytes(63)
            + pkt(C2S, CMSG_SET_SELECTION_OPCODE, struct.pack("<Q", fixture))
            + pkt(C2S, MSG_MOVE_START_FORWARD_OPCODE, b"", ticks=1000)
            + pkt(S2C, SMSG_ON_MONSTER_MOVE_OPCODE, replan_a, ticks=1200)
            + pkt(S2C, SMSG_ON_MONSTER_MOVE_OPCODE, replan_b, ticks=1700)
            + pkt(S2C, SMSG_ON_MONSTER_MOVE_OPCODE, replan_c, ticks=2200)
            + pkt(S2C, 0x3914, b"", ticks=5000)
        )
        replan_generation: Generation = {"paths": {"logs": str(logs), "raw_evidence": str(logs)}}
        replans = plan32_chase_tracks(replan_generation)
    assert replans["executed"][0] == [0.0, 0.0, 0.0]
    assert replans["executed"][-1] == [20.0, 0.0, 0.0]
    replan_length, replan_straight, _ = plan32_path_metrics(
        [tuple(point) for point in replans["executed"]]
    )
    assert abs(replan_length - 20.0) < 1e-9 and abs(replan_straight - 20.0) < 1e-9
    assert replan_length / replan_straight < PLAN32_CHASE_DETOUR_RATIO
    # A spline that resumes anywhere but where the cut left the mover is a discontinuity, not a
    # repath: its start stays in the path so the gap check sees the jump, and it is recorded.
    jump = move_pkt(straight((110.0, 0.0, 0.0), (100.0, 0.0, 0.0)), duration=500,
                    start=(100.0, 0.0, 0.0))
    with tempfile.TemporaryDirectory() as temporary:
        logs = Path(temporary) / "logs"
        logs.mkdir()
        (logs / "WorldServer.log").write_text(chase_log((0.0, 100.0)))
        (logs / "packets.pkt").write_bytes(
            b"PKT" + bytes(63)
            + pkt(C2S, CMSG_SET_SELECTION_OPCODE, struct.pack("<Q", fixture))
            + pkt(C2S, MSG_MOVE_START_FORWARD_OPCODE, b"", ticks=1000)
            + pkt(S2C, SMSG_ON_MONSTER_MOVE_OPCODE, replan_a, ticks=1200)
            + pkt(S2C, SMSG_ON_MONSTER_MOVE_OPCODE, jump, ticks=1700)
            + pkt(S2C, 0x3914, b"", ticks=5000)
        )
        jump_generation: Generation = {"paths": {"logs": str(logs), "raw_evidence": str(logs)}}
        discontinuous = plan32_chase_tracks(jump_generation)
    assert [100.0, 0.0, 0.0] in discontinuous["executed"]
    assert len(discontinuous["joins"]) == 1 and discontinuous["joins"][0]["ticks"] == 1700
    assert abs(float(discontinuous["joins"][0]["gap"]) - 95.0) < 1e-9
    assert plan32_path_metrics(
        [tuple(point) for point in discontinuous["executed"]]
    )[2] > PLAN32_CHASE_MAX_SPLINE_GAP
    # Arrival is dated where the walk entered the radius, not at the vertex after it: this leg
    # reaches the 6 m radius at 9200 ms, so the sole swing at 10000 counts even though the
    # spline's own last vertex - and its tick - come later.
    slow = move_pkt(
        struct.pack("<I", 3) + struct.pack("<fffffffff", 0, 10, 0, 10, 10, 0, 10, 0, 0),
        SPLINE_FLAG_UNCOMPRESSED_PATH, duration=10000,
    )
    with tempfile.TemporaryDirectory() as temporary:
        logs = Path(temporary) / "logs"
        logs.mkdir()
        (logs / "WorldServer.log").write_text(chase_log((10.0, 0.0)))
        (logs / "packets.pkt").write_bytes(
            b"PKT" + bytes(63)
            + pkt(C2S, CMSG_SET_SELECTION_OPCODE, struct.pack("<Q", fixture))
            + pkt(C2S, MSG_MOVE_START_FORWARD_OPCODE, b"", ticks=1000)
            + pkt(S2C, SMSG_ON_MONSTER_MOVE_OPCODE, slow, ticks=1200)
            + pkt(C2S, MSG_MOVE_STOP_STRAFE_OPCODE, b"", ticks=3000)
            + pkt(S2C, SMSG_ATTACKERSTATEUPDATE_OPCODE, swing, ticks=10000)
            + pkt(S2C, 0x3914, b"", ticks=12000)
        )
        walking_generation: Generation = {"paths": {"logs": str(logs), "raw_evidence": str(logs)}}
        walking = plan32_chase_tracks(walking_generation)
    assert walking["arrival_ticks"] == 9200
    assert len(walking["swings"]) == 1 and walking["swings"][0]["ticks"] == 10000
    assert math.dist(walking["executed"][-1], [10.0, 0.0, 0.0]) <= PLAN32_CHASE_ARRIVAL_DISTANCE
    # A short step inside a spline is still a step, not the mover standing there: this walk is
    # 7.3 m from the player when they stop and carries on to 7.4 m, so no arrival may be
    # fabricated out of the 1.9 m last leg - the chase has not reached them.
    stray = move_pkt(
        struct.pack("<I", 4)
        + struct.pack("<ffffffffffff", 0, 10, 0, 10, 10, 0, 10, 5.5, 0, 10, 7.4, 0),
        SPLINE_FLAG_UNCOMPRESSED_PATH, duration=26400,
    )
    with tempfile.TemporaryDirectory() as temporary:
        logs = Path(temporary) / "logs"
        logs.mkdir()
        (logs / "WorldServer.log").write_text(chase_log((10.0, 0.0)))
        (logs / "packets.pkt").write_bytes(
            b"PKT" + bytes(63)
            + pkt(C2S, CMSG_SET_SELECTION_OPCODE, struct.pack("<Q", fixture))
            + pkt(C2S, MSG_MOVE_START_FORWARD_OPCODE, b"", ticks=1000)
            + pkt(S2C, SMSG_ON_MONSTER_MOVE_OPCODE, stray, ticks=1200)
            + pkt(C2S, MSG_MOVE_STOP_STRAFE_OPCODE, b"", ticks=27500)
            + pkt(S2C, SMSG_ATTACKERSTATEUPDATE_OPCODE, swing, ticks=27600)
            + pkt(S2C, 0x3914, b"", ticks=30000)
        )
        stray_generation: Generation = {"paths": {"logs": str(logs), "raw_evidence": str(logs)}}
        strayed = plan32_chase_tracks(stray_generation)
    assert math.dist(strayed["executed"][-1], [10.0, 7.4, 0.0]) < 0.001
    assert math.dist(strayed["executed"][-1], [10.0, 0.0, 0.0]) > PLAN32_CHASE_ARRIVAL_DISTANCE
    # A jump the trimmed path no longer contains is outside the observation - the fixture's
    # return walk after the arrival must not fail the stage - while the same jump inside the
    # retained path still does.
    returned_reset = move_pkt(straight((110.0, 0.0, 0.0), (100.0, 0.0, 0.0)), duration=500,
                              start=(100.0, 0.0, 0.0))
    fast = move_pkt(
        struct.pack("<I", 3) + struct.pack("<fffffffff", 0, 10, 0, 10, 10, 0, 10, 0, 0),
        SPLINE_FLAG_UNCOMPRESSED_PATH, duration=900,
    )
    with tempfile.TemporaryDirectory() as temporary:
        logs = Path(temporary) / "logs"
        logs.mkdir()
        (logs / "WorldServer.log").write_text(chase_log((10.0, 0.0)))
        (logs / "packets.pkt").write_bytes(
            b"PKT" + bytes(63)
            + pkt(C2S, CMSG_SET_SELECTION_OPCODE, struct.pack("<Q", fixture))
            + pkt(C2S, MSG_MOVE_START_FORWARD_OPCODE, b"", ticks=1000)
            + pkt(S2C, SMSG_ON_MONSTER_MOVE_OPCODE, fast, ticks=1200)
            + pkt(C2S, MSG_MOVE_STOP_STRAFE_OPCODE, b"", ticks=2000)
            + pkt(S2C, SMSG_ATTACKERSTATEUPDATE_OPCODE, swing, ticks=2200)
            + pkt(S2C, SMSG_ON_MONSTER_MOVE_OPCODE, returned_reset, ticks=3000)
            + pkt(S2C, 0x3914, b"", ticks=6000)
        )
        returned_generation: Generation = {"paths": {"logs": str(logs), "raw_evidence": str(logs)}}
        returned = plan32_chase_tracks(returned_generation)
    assert returned["joins"] == [] and returned["arrival_ticks"] == 2000
    assert len(returned["swings"]) == 1 and returned["swings"][0]["ticks"] == 2200
    assert math.dist(returned["executed"][-1], [10.0, 0.0, 0.0]) <= PLAN32_CHASE_ARRIVAL_DISTANCE
    inside_jump = move_pkt(straight((10.0, 0.0, 0.0)), duration=300, start=(10.0, 20.0, 0.0))
    with tempfile.TemporaryDirectory() as temporary:
        logs = Path(temporary) / "logs"
        logs.mkdir()
        (logs / "WorldServer.log").write_text(chase_log((10.0, 0.0)))
        (logs / "packets.pkt").write_bytes(
            b"PKT" + bytes(63)
            + pkt(C2S, CMSG_SET_SELECTION_OPCODE, struct.pack("<Q", fixture))
            + pkt(C2S, MSG_MOVE_START_FORWARD_OPCODE, b"", ticks=1000)
            + pkt(S2C, SMSG_ON_MONSTER_MOVE_OPCODE, move_pkt(straight((0.0, 10.0, 0.0))),
                  ticks=1200)
            + pkt(S2C, SMSG_ON_MONSTER_MOVE_OPCODE, inside_jump, ticks=1800)
            + pkt(C2S, MSG_MOVE_STOP_STRAFE_OPCODE, b"", ticks=2000)
            + pkt(S2C, SMSG_ATTACKERSTATEUPDATE_OPCODE, swing, ticks=2200)
            + pkt(S2C, 0x3914, b"", ticks=6000)
        )
        inside_generation: Generation = {"paths": {"logs": str(logs), "raw_evidence": str(logs)}}
        jumped = plan32_chase_tracks(inside_generation)
    assert len(jumped["joins"]) == 1 and jumped["joins"][0]["ticks"] == 1800
    assert math.dist(jumped["executed"][-1], [10.0, 0.0, 0.0]) <= PLAN32_CHASE_ARRIVAL_DISTANCE
    # A join that belongs to a span the arrival trimmed is trimmed with it even when the two share
    # a millisecond tick: the stop came first in packet order, so the mover was still on the
    # detour's own endpoint when it landed, and the reset's 90 m jump is outside the observation.
    with tempfile.TemporaryDirectory() as temporary:
        logs = Path(temporary) / "logs"
        logs.mkdir()
        (logs / "WorldServer.log").write_text(chase_log((10.0, 0.0)))
        (logs / "packets.pkt").write_bytes(
            b"PKT" + bytes(63)
            + pkt(C2S, CMSG_SET_SELECTION_OPCODE, struct.pack("<Q", fixture))
            + pkt(C2S, MSG_MOVE_START_FORWARD_OPCODE, b"", ticks=1000)
            + pkt(S2C, SMSG_ON_MONSTER_MOVE_OPCODE, fast, ticks=1200)
            + pkt(C2S, MSG_MOVE_STOP_STRAFE_OPCODE, b"", ticks=2100)
            + pkt(S2C, SMSG_ON_MONSTER_MOVE_OPCODE, returned_reset, ticks=2100)
            + pkt(S2C, SMSG_ATTACKERSTATEUPDATE_OPCODE, swing, ticks=2200)
            + pkt(S2C, 0x3914, b"", ticks=6000)
        )
        tie_generation: Generation = {"paths": {"logs": str(logs), "raw_evidence": str(logs)}}
        tied = plan32_chase_tracks(tie_generation)
    assert tied["arrival_ticks"] == 2100 and tied["joins"] == []
    assert len(tied["swings"]) == 1 and tied["swings"][0]["ticks"] == 2200
    assert math.dist(tied["executed"][-1], [10.0, 0.0, 0.0]) <= PLAN32_CHASE_ARRIVAL_DISTANCE
    # The other order of the same tie: the reset precedes the stop, so by the time the stop lands
    # the mover is already elsewhere and the detour's endpoint is not its position any more. The
    # arrival and the engagement the chase claims would both be fabricated, and the jump the reset
    # left behind is the discontinuity that says so.
    with tempfile.TemporaryDirectory() as temporary:
        logs = Path(temporary) / "logs"
        logs.mkdir()
        (logs / "WorldServer.log").write_text(chase_log((10.0, 0.0)))
        (logs / "packets.pkt").write_bytes(
            b"PKT" + bytes(63)
            + pkt(C2S, CMSG_SET_SELECTION_OPCODE, struct.pack("<Q", fixture))
            + pkt(C2S, MSG_MOVE_START_FORWARD_OPCODE, b"", ticks=1000)
            + pkt(S2C, SMSG_ON_MONSTER_MOVE_OPCODE, fast, ticks=1200)
            + pkt(S2C, SMSG_ON_MONSTER_MOVE_OPCODE, returned_reset, ticks=3000)
            + pkt(C2S, MSG_MOVE_STOP_STRAFE_OPCODE, b"", ticks=3000)
            + pkt(S2C, SMSG_ATTACKERSTATEUPDATE_OPCODE, swing, ticks=3200)
            + pkt(S2C, 0x3914, b"", ticks=6000)
        )
        superseded_generation: Generation = {
            "paths": {"logs": str(logs), "raw_evidence": str(logs)},
        }
        superseded = plan32_chase_tracks(superseded_generation)
    assert len(superseded["joins"]) == 1 and superseded["joins"][0]["ticks"] == 3000
    assert abs(float(superseded["joins"][0]["gap"]) - 90.0) < 1e-9 and superseded["swings"] == []
    assert math.dist(superseded["executed"][-1], [10.0, 0.0, 0.0]) > PLAN32_CHASE_ARRIVAL_DISTANCE
    # The stop-first order on the other kind of boundary: the detour is over and the mover stands
    # at its endpoint, so the stop at 3000 lands while it is still there - the arrival is dated
    # 3000 at (10,0) and the reset's own jump stays outside the observation.
    with tempfile.TemporaryDirectory() as temporary:
        logs = Path(temporary) / "logs"
        logs.mkdir()
        (logs / "WorldServer.log").write_text(chase_log((10.0, 0.0)))
        (logs / "packets.pkt").write_bytes(
            b"PKT" + bytes(63)
            + pkt(C2S, CMSG_SET_SELECTION_OPCODE, struct.pack("<Q", fixture))
            + pkt(C2S, MSG_MOVE_START_FORWARD_OPCODE, b"", ticks=1000)
            + pkt(S2C, SMSG_ON_MONSTER_MOVE_OPCODE, fast, ticks=1200)
            + pkt(C2S, MSG_MOVE_STOP_STRAFE_OPCODE, b"", ticks=3000)
            + pkt(S2C, SMSG_ON_MONSTER_MOVE_OPCODE, returned_reset, ticks=3000)
            + pkt(S2C, SMSG_ATTACKERSTATEUPDATE_OPCODE, swing, ticks=3200)
            + pkt(S2C, 0x3914, b"", ticks=6000)
        )
        hold_generation: Generation = {"paths": {"logs": str(logs), "raw_evidence": str(logs)}}
        held = plan32_chase_tracks(hold_generation)
    assert held["arrival_ticks"] == 3000 and held["joins"] == []
    assert len(held["swings"]) == 1 and held["swings"][0]["ticks"] == 3200
    assert math.dist(held["executed"][-1], [10.0, 0.0, 0.0]) <= PLAN32_CHASE_ARRIVAL_DISTANCE
    # And the reset-first order at the walk's own endpoint: the packet that already moved the
    # mover is the one that says where it is, so the endpoint at 2100 must not be read as the
    # arrival - the observation keeps the 90 m jump it left behind and fails the stage on it.
    with tempfile.TemporaryDirectory() as temporary:
        logs = Path(temporary) / "logs"
        logs.mkdir()
        (logs / "WorldServer.log").write_text(chase_log((10.0, 0.0)))
        (logs / "packets.pkt").write_bytes(
            b"PKT" + bytes(63)
            + pkt(C2S, CMSG_SET_SELECTION_OPCODE, struct.pack("<Q", fixture))
            + pkt(C2S, MSG_MOVE_START_FORWARD_OPCODE, b"", ticks=1000)
            + pkt(S2C, SMSG_ON_MONSTER_MOVE_OPCODE, fast, ticks=1200)
            + pkt(S2C, SMSG_ON_MONSTER_MOVE_OPCODE, returned_reset, ticks=2100)
            + pkt(C2S, MSG_MOVE_STOP_STRAFE_OPCODE, b"", ticks=2100)
            + pkt(S2C, SMSG_ATTACKERSTATEUPDATE_OPCODE, swing, ticks=2200)
            + pkt(S2C, 0x3914, b"", ticks=6000)
        )
        left_first_generation: Generation = {"paths": {"logs": str(logs), "raw_evidence": str(logs)}}
        left_first = plan32_chase_tracks(left_first_generation)
    assert left_first["arrival_ticks"] == 6000, "the observation's own end, not a dated arrival"
    assert len(left_first["joins"]) == 1 and left_first["joins"][0]["ticks"] == 2100
    assert abs(float(left_first["joins"][0]["gap"]) - 90.0) < 1e-9 and left_first["swings"] == []
    assert math.dist(left_first["executed"][-1], [10.0, 0.0, 0.0]) > PLAN32_CHASE_ARRIVAL_DISTANCE
    # The same order where the reset interrupts the detour mid-spline instead: the position the
    # walk had interpolated to at the interruption tick is no more the mover's own than a
    # completed spline's last vertex is, so no arrival may be dated from it either.
    interrupted_detour = move_pkt(
        struct.pack("<I", 3) + struct.pack("<fffffffff", 0, 10, 0, 10, 10, 0, 10, 0, 0),
        SPLINE_FLAG_UNCOMPRESSED_PATH, duration=10000,
    )
    with tempfile.TemporaryDirectory() as temporary:
        logs = Path(temporary) / "logs"
        logs.mkdir()
        (logs / "WorldServer.log").write_text(chase_log((10.0, 0.0)))
        (logs / "packets.pkt").write_bytes(
            b"PKT" + bytes(63)
            + pkt(C2S, CMSG_SET_SELECTION_OPCODE, struct.pack("<Q", fixture))
            + pkt(C2S, MSG_MOVE_START_FORWARD_OPCODE, b"", ticks=1000)
            + pkt(S2C, SMSG_ON_MONSTER_MOVE_OPCODE, interrupted_detour, ticks=1200)
            + pkt(S2C, SMSG_ON_MONSTER_MOVE_OPCODE, returned_reset, ticks=10000)
            + pkt(C2S, MSG_MOVE_STOP_STRAFE_OPCODE, b"", ticks=10000)
            + pkt(S2C, SMSG_ATTACKERSTATEUPDATE_OPCODE, swing, ticks=10200)
            + pkt(S2C, 0x3914, b"", ticks=15000)
        )
        interrupted_generation: Generation = {"paths": {"logs": str(logs), "raw_evidence": str(logs)}}
        interrupted = plan32_chase_tracks(interrupted_generation)
    assert interrupted["arrival_ticks"] == 15000 and interrupted["swings"] == []
    assert len(interrupted["joins"]) == 1 and interrupted["joins"][0]["ticks"] == 10000
    assert abs(float(interrupted["joins"][0]["gap"]) - 90.072) < 0.01
    assert math.dist(interrupted["executed"][-1], [10.0, 0.0, 0.0]) > PLAN32_CHASE_ARRIVAL_DISTANCE
    # A chain of mover packets at the boundary tick: the first replan starts from the detour's own
    # endpoint and executes nothing, the reset behind it moves the same millisecond, and the stop
    # comes last - so the position the stop met is the reset's, not the endpoint the earlier legs
    # still advertise. Neither the walk's own endpoint nor the hold that follows may date an
    # arrival there, and the 90 m jump the reset left stays in the observation.
    replan = move_pkt(straight((10.0, 1.0, 0.0)), SPLINE_FLAG_UNCOMPRESSED_PATH,
                      duration=1000, start=(10.0, 0.0, 0.0))
    with tempfile.TemporaryDirectory() as temporary:
        logs = Path(temporary) / "logs"
        logs.mkdir()
        (logs / "WorldServer.log").write_text(chase_log((10.0, 0.0)))
        (logs / "packets.pkt").write_bytes(
            b"PKT" + bytes(63)
            + pkt(C2S, CMSG_SET_SELECTION_OPCODE, struct.pack("<Q", fixture))
            + pkt(C2S, MSG_MOVE_START_FORWARD_OPCODE, b"", ticks=1000)
            + pkt(S2C, SMSG_ON_MONSTER_MOVE_OPCODE, fast, ticks=1200)
            + pkt(S2C, SMSG_ON_MONSTER_MOVE_OPCODE, replan, ticks=2100)
            + pkt(S2C, SMSG_ON_MONSTER_MOVE_OPCODE, returned_reset, ticks=2100)
            + pkt(C2S, MSG_MOVE_STOP_STRAFE_OPCODE, b"", ticks=2100)
            + pkt(S2C, SMSG_ATTACKERSTATEUPDATE_OPCODE, swing, ticks=2200)
            + pkt(S2C, 0x3914, b"", ticks=15000)
        )
        chained_generation: Generation = {"paths": {"logs": str(logs), "raw_evidence": str(logs)}}
        chained = plan32_chase_tracks(chained_generation)
    assert chained["arrival_ticks"] == 15000 and chained["swings"] == []
    assert len(chained["joins"]) == 1 and chained["joins"][0]["ticks"] == 2100
    assert abs(float(chained["joins"][0]["gap"]) - 90.0) < 1e-9
    assert math.dist(chained["executed"][-1], [10.0, 0.0, 0.0]) > PLAN32_CHASE_ARRIVAL_DISTANCE
    # The same chain one hold later, where it is the occupancy the detour left that it supersedes.
    with tempfile.TemporaryDirectory() as temporary:
        logs = Path(temporary) / "logs"
        logs.mkdir()
        (logs / "WorldServer.log").write_text(chase_log((10.0, 0.0)))
        (logs / "packets.pkt").write_bytes(
            b"PKT" + bytes(63)
            + pkt(C2S, CMSG_SET_SELECTION_OPCODE, struct.pack("<Q", fixture))
            + pkt(C2S, MSG_MOVE_START_FORWARD_OPCODE, b"", ticks=1000)
            + pkt(S2C, SMSG_ON_MONSTER_MOVE_OPCODE, fast, ticks=1200)
            + pkt(S2C, SMSG_ON_MONSTER_MOVE_OPCODE, replan, ticks=3000)
            + pkt(S2C, SMSG_ON_MONSTER_MOVE_OPCODE, returned_reset, ticks=3000)
            + pkt(C2S, MSG_MOVE_STOP_STRAFE_OPCODE, b"", ticks=3000)
            + pkt(S2C, SMSG_ATTACKERSTATEUPDATE_OPCODE, swing, ticks=3200)
            + pkt(S2C, 0x3914, b"", ticks=15000)
        )
        chained_hold_generation: Generation = {"paths": {"logs": str(logs), "raw_evidence": str(logs)}}
        chained_hold = plan32_chase_tracks(chained_hold_generation)
    assert chained_hold["arrival_ticks"] == 15000 and chained_hold["swings"] == []
    assert len(chained_hold["joins"]) == 1 and chained_hold["joins"][0]["ticks"] == 3000
    assert abs(float(chained_hold["joins"][0]["gap"]) - 90.0) < 1e-9
    assert math.dist(chained_hold["executed"][-1], [10.0, 0.0, 0.0]) > PLAN32_CHASE_ARRIVAL_DISTANCE
    # The control: with the stop between the two packets the mover is still standing on the
    # detour's endpoint when it lands, so the arrival is its own and the jump after it is outside
    # the observation.
    with tempfile.TemporaryDirectory() as temporary:
        logs = Path(temporary) / "logs"
        logs.mkdir()
        (logs / "WorldServer.log").write_text(chase_log((10.0, 0.0)))
        (logs / "packets.pkt").write_bytes(
            b"PKT" + bytes(63)
            + pkt(C2S, CMSG_SET_SELECTION_OPCODE, struct.pack("<Q", fixture))
            + pkt(C2S, MSG_MOVE_START_FORWARD_OPCODE, b"", ticks=1000)
            + pkt(S2C, SMSG_ON_MONSTER_MOVE_OPCODE, fast, ticks=1200)
            + pkt(S2C, SMSG_ON_MONSTER_MOVE_OPCODE, replan, ticks=2100)
            + pkt(C2S, MSG_MOVE_STOP_STRAFE_OPCODE, b"", ticks=2100)
            + pkt(S2C, SMSG_ON_MONSTER_MOVE_OPCODE, returned_reset, ticks=2100)
            + pkt(S2C, SMSG_ATTACKERSTATEUPDATE_OPCODE, swing, ticks=2200)
            + pkt(S2C, 0x3914, b"", ticks=15000)
        )
        separated_generation: Generation = {"paths": {"logs": str(logs), "raw_evidence": str(logs)}}
        separated = plan32_chase_tracks(separated_generation)
    assert separated["arrival_ticks"] == 2100 and separated["joins"] == []
    assert len(separated["swings"]) == 1 and separated["swings"][0]["ticks"] == 2200
    assert math.dist(separated["executed"][-1], [10.0, 0.0, 0.0]) <= PLAN32_CHASE_ARRIVAL_DISTANCE
    # A chain that walks the mover away and then back to the old point within the tick: the later
    # packet owns the instant whether or not it lands within the join tolerance, so the jumps it
    # took are still in the observation. Out of range on arrival, the stage fails the arrival too;
    # back on the point, the arrival is honest and the two 90 m jumps fail the stage on their own.
    for tick, third, player, arrives in (
        (2100, [(11.0, 0.0, 0.0), (11.0, 1.0, 0.0)], (4.0, 0.0), False),
        (2100, [(10.0, 0.0, 0.0), (10.0, 1.0, 0.0)], (10.0, 0.0), True),
        (3000, [(11.0, 0.0, 0.0), (11.0, 1.0, 0.0)], (4.0, 0.0), False),
        (3000, [(10.0, 0.0, 0.0), (10.0, 1.0, 0.0)], (10.0, 0.0), True),
    ):
        with tempfile.TemporaryDirectory() as temporary:
            logs = Path(temporary) / "logs"
            logs.mkdir()
            (logs / "WorldServer.log").write_text(chase_log(player))
            (logs / "packets.pkt").write_bytes(
                b"PKT" + bytes(63)
                + pkt(C2S, CMSG_SET_SELECTION_OPCODE, struct.pack("<Q", fixture))
                + pkt(C2S, MSG_MOVE_START_FORWARD_OPCODE, b"", ticks=1000)
                + pkt(S2C, SMSG_ON_MONSTER_MOVE_OPCODE, fast, ticks=1200)
                + pkt(S2C, SMSG_ON_MONSTER_MOVE_OPCODE, returned_reset, ticks=tick)
                + pkt(S2C, SMSG_ON_MONSTER_MOVE_OPCODE,
                      move_pkt(straight(third[1]), SPLINE_FLAG_UNCOMPRESSED_PATH, duration=1000,
                               start=third[0]), ticks=tick)
                + pkt(C2S, MSG_MOVE_STOP_STRAFE_OPCODE, b"", ticks=tick)
                + pkt(S2C, SMSG_ATTACKERSTATEUPDATE_OPCODE, swing, ticks=tick + 100)
                + pkt(S2C, 0x3914, b"", ticks=15000)
            )
            away_generation: Generation = {"paths": {"logs": str(logs), "raw_evidence": str(logs)}}
            away = plan32_chase_tracks(away_generation)
        assert len(away["joins"]) == 2, away["joins"]
        assert abs(float(away["joins"][0]["gap"]) - 90.0) < 1e-9
        assert abs(float(away["joins"][1]["gap"]) - (89.0 if third[0][0] == 11.0 else 90.0)) < 1e-9
        if arrives:
            assert away["arrival_ticks"] == tick
            assert math.dist(away["executed"][-1], [10.0, 0.0, 0.0]) <= PLAN32_CHASE_ARRIVAL_DISTANCE
            assert len(away["swings"]) == 1
        else:
            assert away["arrival_ticks"] == 15000 and away["swings"] == []
            assert math.dist(away["executed"][-1], [4.0, 0.0, 0.0]) > PLAN32_CHASE_ARRIVAL_DISTANCE
    # A pass that only grazes the arrival radius is still an arrival, dated exactly where it
    # touches: the 6 m touch at (10,10) is at 7200, and the swing there proves melee range.
    grazing = move_pkt(
        struct.pack("<I", 4) + struct.pack("<ffffffffffff", 0, 10, 0, 5, 10, 0, 15, 10, 0, 20, 10, 0),
        SPLINE_FLAG_UNCOMPRESSED_PATH, duration=9000,
    )
    with tempfile.TemporaryDirectory() as temporary:
        logs = Path(temporary) / "logs"
        logs.mkdir()
        (logs / "WorldServer.log").write_text(chase_log((10.0, 4.0)))
        (logs / "packets.pkt").write_bytes(
            b"PKT" + bytes(63)
            + pkt(C2S, CMSG_SET_SELECTION_OPCODE, struct.pack("<Q", fixture))
            + pkt(C2S, MSG_MOVE_START_FORWARD_OPCODE, b"", ticks=1000)
            + pkt(S2C, SMSG_ON_MONSTER_MOVE_OPCODE, grazing, ticks=1200)
            + pkt(C2S, MSG_MOVE_STOP_STRAFE_OPCODE, b"", ticks=3000)
            + pkt(S2C, SMSG_ATTACKERSTATEUPDATE_OPCODE, swing, ticks=7200)
            + pkt(S2C, 0x3914, b"", ticks=12000)
        )
        graze_generation: Generation = {"paths": {"logs": str(logs), "raw_evidence": str(logs)}}
        grazed = plan32_chase_tracks(graze_generation)
    assert grazed["arrival_ticks"] == 7200
    assert len(grazed["swings"]) == 1 and grazed["swings"][0]["ticks"] == 7200
    assert abs(
        math.dist(grazed["executed"][-1], [10.0, 4.0, 0.0]) - PLAN32_CHASE_ARRIVAL_DISTANCE
    ) < 1e-6
    # An advertised detour the capture never saw completed cannot stand in for arrival: a spline
    # captured 200 ms into an 800 ms walk is only a quarter of the way in (7.5 m), outside
    # arrival range, and the sole swing before the run leg proves the pull, not the chase.
    with tempfile.TemporaryDirectory() as temporary:
        logs = Path(temporary) / "logs"
        logs.mkdir()
        (logs / "WorldServer.log").write_text(chase_log((10.0, 0.0)))
        (logs / "packets.pkt").write_bytes(
            b"PKT" + bytes(63)
            + pkt(C2S, CMSG_SET_SELECTION_OPCODE, struct.pack("<Q", fixture))
            + pkt(S2C, SMSG_ATTACKERSTATEUPDATE_OPCODE, swing, ticks=500)
            + pkt(C2S, MSG_MOVE_START_FORWARD_OPCODE, b"", ticks=1000)
            + pkt(S2C, SMSG_ON_MONSTER_MOVE_OPCODE, detour, ticks=1200)
            + pkt(S2C, 0x3914, b"", ticks=1400)
        )
        stalled_generation: Generation = {"paths": {"logs": str(logs), "raw_evidence": str(logs)}}
        unfinished = plan32_chase_tracks(stalled_generation)
    assert unfinished["executed"][-1] == [0.0, 7.5, 0.0]
    assert math.hypot(unfinished["executed"][-1][0] - 10.0, unfinished["executed"][-1][1]) > (
        PLAN32_CHASE_ARRIVAL_DISTANCE
    )
    assert unfinished["swings"] == []
    # Relog: the walk under test sits before the server's completed logout, and the landing after
    # the login that follows it carries the position that came back. The predicate reads the log in
    # order, so the two decoys a global count cannot catch - a second landing placed before the
    # logout, and both logins before the logout - must both be reported.
    with tempfile.TemporaryDirectory() as temporary:
        logs = Path(temporary) / "logs"
        logs.mkdir()

        def relog_log(order: str) -> None:
            def landing() -> str:
                return (
                    "Accepted Cataclysm ground movement [MSG_MOVE_FALL_LAND 0x380A (14346)] after "
                    "movement validation: x=-8779.03, y=-219.41, z=85.01, o=2.766"
                )

            lines = [
                "Finished object update bootstrap after adding to map",
                IN_WORLD_CONTROL_MARKER,
                "2026-10-18 19:36:20 Received CMSG_PLAYER_LOGIN for account 900000.",
                "Accepted Cataclysm ground movement [MSG_MOVE_FALL_LAND 0x380A (14346)] after movement "
                "validation: x=-8775.13, y=-220.95, z=85.254, o=2.766",
                "Accepted Cataclysm ground movement [MSG_MOVE_START_FORWARD 0x7814 (30740)] after "
                "movement validation: x=-8775.13, y=-220.95, z=85.254, o=2.766",
                "Accepted Cataclysm ground movement [MSG_MOVE_STOP 0x320A (12810)] after movement "
                "validation: x=-8779.03, y=-219.41, z=85.01, o=2.766",
            ]
            if order == "landing_before_logout":
                lines.append(landing())
            if order == "login_before_logout":
                lines.append("2026-10-18 19:36:51 Received CMSG_PLAYER_LOGIN for account 900000.")
            lines.append("2026-10-18 19:36:52 S->C: 127.0.0.1 [SMSG_LOGOUT_COMPLETE 0x2137 (8503)]")
            if order == "ordered":
                lines.append("2026-10-18 19:36:53 Received CMSG_PLAYER_LOGIN for account 900000.")
                lines.append(landing())
            (logs / "WorldServer.log").write_text("\n".join(lines) + "\n")

        relog_log("ordered")
        relog_generation: Generation = {"paths": {"logs": str(logs), "raw_evidence": str(logs)}}
        relog_timeline = plan32_relog_timeline(relog_generation)
        ordered_problems = plan32_relog_problems(relog_generation)
        relog_log("landing_before_logout")
        early_landing_problems = plan32_relog_problems(relog_generation)
        relog_log("login_before_logout")
        early_login_problems = plan32_relog_problems(relog_generation)
    assert len(relog_timeline["walk"]) == 3 and len(relog_timeline["returned"]) == 1
    assert relog_timeline["logout_completes"] == 1 and relog_timeline["login_requests"] == 2
    assert relog_timeline["logins_after_logout"] == 1
    assert str(relog_timeline["walk"][-1]["opcode"]) == "MSG_MOVE_STOP"
    assert relog_timeline["returned"][0]["x"] == relog_timeline["walk"][-1]["x"]
    assert relog_timeline["returned"][0]["y"] == relog_timeline["walk"][-1]["y"]
    assert ordered_problems == []
    assert early_landing_problems, "a landing before the logout must not pass as the return"
    assert early_login_problems, "a login before the logout must not pass as the return"
    print("Real-client runner self-check passed")


def stability_seconds(value: str) -> int:
    try:
        result = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("stability seconds must be an integer") from error
    if result < 5:
        raise argparse.ArgumentTypeError("stability seconds must be at least 5")
    return result


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    subcommands = result.add_subparsers(dest="command", required=True)
    subcommands.add_parser("self-check")
    prepare_parser = subcommands.add_parser("prepare")
    prepare_parser.add_argument("--manifest", type=Path, required=True)
    prepare_parser.add_argument("--authserver", type=Path, required=True)
    prepare_parser.add_argument("--worldserver", type=Path, required=True)
    prepare_parser.add_argument("--unit-tests", type=Path, required=True)
    prepare_parser.add_argument("--client-root", type=Path, required=True)
    prepare_parser.add_argument("--data-root", type=Path, required=True)
    prepare_parser.add_argument(
        "--geometry", action="store_true",
        help=("stage vmaps/ and mmaps/ from the data root and run the server with MoveMaps and vmap "
              "LOS/height enabled; without it the accepted modes stay geometry-free"),
    )
    prepare_parser.add_argument("--server-dbc-root", type=Path)
    prepare_parser.add_argument("--wine-runner", type=Path, required=True)
    prepare_parser.add_argument("--personal-bottle", type=Path, required=True)
    prepare_parser.add_argument("--migration", type=Path, default=DEFAULT_MIGRATION)
    prepare_parser.add_argument("--display", required=True)
    prepare_parser.add_argument("--xauthority", type=Path)
    prepare_parser.add_argument(
        "--mode", choices=(
            "no-login", "authentication", "character-screen", CHARACTER_CREATION_MODE, MAGE_COMBAT_MODE,
            MAGE_FOLLOWUP_MODE,
            POPULATED_MODE,
            CHARACTER_SELECTION_MODE,
            INITIAL_POST_LOAD_PACKETS_MODE, MAP_INSERTION_MODE, IN_WORLD_CONTROL_MODE, BASIC_MOVEMENT_MODE,
            RUN_SPEED_MODE, GROUND_MOVEMENT_MODE, JUMP_FALL_LAND_MODE, WALK_RUN_MODE_SWITCH_MODE,
            BASIC_SPELL_CAST_MODE, TARGET_TARGETED_SPELL_CAST_MODE, CHAT_MESSAGE_MODE,
            INVALID_TARGET_SPELL_CAST_MODE, LEVEL_UP_MODE, PLAN32_GEOMETRY_MODE, VENDOR_TRADE_MODE,
            VENDOR_NEGATIVE_MODE, TRAINER_MODE,
        ), default="authentication",
    )
    prepare_parser.add_argument("--minimum-free-gib", type=int, default=25)
    run_parser = subcommands.add_parser("run")
    run_parser.add_argument("--manifest", type=Path, required=True)
    run_parser.add_argument("--timeout", type=int, default=300)
    run_parser.add_argument("--no-login-seconds", type=int, default=20)
    run_parser.add_argument("--stability-seconds", type=stability_seconds, default=10)
    run_parser.add_argument("--auto-login", action="store_true")
    verify_parser = subcommands.add_parser("verify")
    verify_parser.add_argument("--manifest", type=Path, required=True)
    verify_parser.add_argument("--confirm-expected-screen", action="store_true")
    replay_parser = subcommands.add_parser("replay")
    replay_parser.add_argument("--manifest", type=Path, required=True)
    replay_parser.add_argument("--fixture", type=Path, default=DEFAULT_FIXTURE)
    reset_parser = subcommands.add_parser("reset")
    reset_parser.add_argument("--manifest", type=Path, required=True)
    reset_parser.add_argument("--purge-client-base", action="store_true")
    reset_parser.add_argument("--purge-database-cache", action="store_true")
    compare_parser = subcommands.add_parser("compare-last-two")
    compare_parser.add_argument("--manifest", type=Path, required=True)
    return result


def main() -> int:
    args = parser().parse_args()
    actions = {
        "prepare": prepare,
        "run": run_client,
        "verify": verify,
        "replay": replay,
        "reset": reset,
        "compare-last-two": compare_last_two,
    }
    try:
        if args.command == "self-check":
            self_check()
        else:
            actions[args.command](args)
        return 0
    except (OSError, RuntimeError, subprocess.SubprocessError, AssertionError, ValueError, KeyError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
