"""Check the recorded Plan 30 artifacts without starting a client, server, or database."""
from collections import Counter
import gzip
import hashlib
import json
from pathlib import Path
import struct

ROOT = Path(__file__).resolve().parent


def read(name):
    return json.loads((ROOT / name).read_text())


def at(row, index):
    if isinstance(row, list):
        return row[index - 1] if index <= len(row) else None
    return row.get(str(index))


snapshots = read("state-snapshots.json")
events = [json.loads(line) for line in (ROOT / "server-state.jsonl").read_text().splitlines()]
client = read("client-events-and-state.json")
counts = Counter(event["event"] for event in events)
assert not any("error" in key for event in events for key in event)
for name in ("kill", "release", "durability_loss", "resurrect", "reclaim_request"):
    assert counts[name + "_enter"] == counts[name + "_return"] == 2, name
assert counts["release_request_enter"] == counts["release_request_return"] == 1

for event in events:
    if "player" in event:
        player = event["player"]
        # SetPower is also observed while login is still constructing the base stats.
        if event["event"] != "set_power":
            assert player["max_health"] == 60
        assert player["power_fields"][1:] == player["max_power_fields"][1:] == [0] * 4
        assert player["max_power_fields"][0] == 1000
    if event["event"] == "resurrect_return":
        assert player["health"] == 30 and player["death_state"] == 0 and player["player_flags"] == 0
    if event["event"] == "power_packet":
        packet = bytes.fromhex(event["hex"])
        offset = 1 + packet[0].bit_count()
        assert struct.unpack_from("<I", packet, offset)[0] == 1
        assert len(packet) == offset + 9 and packet[offset + 4] == 1

groups = []
current = []
for event in events:
    if event["event"] == "incoming_swing":
        current.append(event)
    elif event["event"] == "kill_enter":
        groups.append(current)
        current = []
assert len(groups) == 2 and not current
for group, initial_health in zip(groups, [1, 30]):
    health = initial_health
    for swing in group:
        assert swing["player"]["health"] == health
        health = max(0, health - swing["damage"])
    assert health == 0

incoming = [event for event in client["events"]
            if at(event, 5) == "Diseased Young Wolf" and at(event, 8) == "0x0000000000000001"]
assert len({at(event, 4) for event in incoming}) == 1
assert all((int(at(event, 4), 16) >> 24) & 0xFFFFFF == 299 for event in incoming)
damage = [at(event, 12) if at(event, 2) == "SWING_DAMAGE" else 0 for event in incoming]
assert damage == [event["damage"] for event in events if event["event"] == "incoming_swing"]
assert Counter(state["event"] for state in client["states"])["PLAYER_DEAD"] == 2
assert Counter(state["event"] for state in client["states"])["PLAYER_UNGHOST"] == 2
last_entry = [state for state in client["states"] if state["event"] == "PLAYER_ENTERING_WORLD"][-1]
assert last_entry["health"] == 30 and last_entry["max"] == 60 and not last_entry.get("ghost")

expected_durability = {
    "slot-fix-start": 21, "slot-dead-logout": 19, "slot-dead-relog-ghost": 19,
    "slot-after-reclaim1": 19, "slot-ghost-logout": 17, "slot-ghost-relog": 17,
    "slot-alive-logout": 17, "slot-alive-relog": 17, "slot-final-logout": 17,
}
for name, expected in expected_durability.items():
    state = snapshots[name]
    assert len(state["characters"]) == 1 and len(state["items"]) == 5
    assert {item["itemEntry"]: item["durability"] for item in state["items"]
            if item["itemEntry"] in (39, 49778)} == {39: expected, 49778: expected}
for name in ("slot-dead-logout", "slot-dead-relog-ghost", "slot-ghost-logout", "slot-ghost-relog"):
    state = snapshots[name]
    character = state["characters"][0]
    assert character["health"] == 1 and character["playerFlags"] & 16
    assert character["map"] == 0 and len(state["corpses"]) == 1 and state["corpses"][0]["guid"] == 1
    assert abs(character["position_x"] + 8935.330078125) < 0.001
    assert abs(character["position_y"] + 188.64599609375) < 0.001
assert snapshots["slot-dead-logout"]["corpses"] == snapshots["slot-dead-relog-ghost"]["corpses"]
assert snapshots["slot-ghost-logout"]["corpses"] == snapshots["slot-ghost-relog"]["corpses"]
assert snapshots["slot-alive-logout"] == snapshots["slot-final-logout"]
for name in ("slot-alive-logout", "slot-alive-relog", "slot-final-logout"):
    state = snapshots[name]
    assert state["characters"][0]["health"] == 30 and state["characters"][0]["playerFlags"] == 0
    assert not state["corpses"]
assert read("character-identity.json") == {"guid": 1, "race": 1, "class": 1, "gender": 0, "level": 1}

packets = (ROOT / "packet-events.log").read_text()
for name in ("CMSG_REPOP_REQUEST", "MSG_MOVE_TELEPORT_ACK", "CMSG_RECLAIM_CORPSE"):
    assert name in packets
cleanup = read("cleanup.json")
assert cleanup["state"] == "reset" and cleanup["container_removed"] and cleanup["volume_removed"]
assert cleanup["active_owned_processes"] == cleanup["owned_wine_processes"] == 0
assert cleanup["protected_inputs_unchanged"] and cleanup["recorded_ports_unused"]
assert read("runtime-inputs.json")["no_current_run_crash_reports"]
acceptance = read("acceptance.json")
for name, metadata in acceptance["logs"].items():
    raw = gzip.decompress((ROOT / name).read_bytes())
    assert hashlib.sha256(raw).hexdigest() == metadata["uncompressed_sha256"]
    assert b"[  FAILED  ]" not in raw
unit = gzip.decompress((ROOT / "unit-tests.log.gz").read_bytes())
assert b"11493 tests" in unit and b"6013 tests" in unit
assert b"18 tests" in (ROOT / "focused-tests.log").read_bytes()


healer = [json.loads(line) for line in (ROOT / "healer-server-state.jsonl").read_text().splitlines()]
assert not any("error" in key for event in healer for key in event)
hcounts = Counter(event["event"] for event in healer)
for name in ("kill", "release", "release_request", "resurrect", "spirit_resurrect", "healer_activate"):
    assert hcounts[name + "_enter"] == hcounts[name + "_return"] == 1, name
assert hcounts["durability_loss_enter"] == hcounts["durability_loss_return"] == 2
health = 30
swings = [event for event in healer if event["event"] == "incoming_swing"]
for swing in swings:
    assert swing["player"]["health"] == health
    health = max(0, health - swing["damage"])
assert health == 0
for event in healer:
    if "player" in event:
        player = event["player"]
        assert player["power_fields"][1:] == player["max_power_fields"][1:] == [0] * 4
        assert player["max_power_fields"][0] == 1000
    if event["event"] == "resurrect_return":
        assert player["health"] == 30 and player["death_state"] == 0 and player["player_flags"] == 0
hc = read("healer-client-events-and-state.json")
assert hc["loaded"] and all(s["attempt"] == "spirit-healer-final" for s in hc["states"])
native = [e for e in hc["events"] if at(e, 5) == "Diseased Young Wolf" and at(e, 8) == "0x0000000000000001"]
assert [at(e, 12) if at(e, 2) == "SWING_DAMAGE" else 0 for e in native] == [s["damage"] for s in swings]
assert len({at(e, 4) for e in native}) == 1
assert all((int(at(e, 4), 16) >> 24) & 0xFFFFFF == 299 for e in native)
assert Counter(s["event"] for s in hc["states"])["PLAYER_DEAD"] == 1
assert Counter(s["event"] for s in hc["states"])["PLAYER_UNGHOST"] == 1
last = [s for s in hc["states"] if s["event"] == "PLAYER_ENTERING_WORLD"][-1]
assert last["health"] == 30 and last["max"] == 60 and not last.get("ghost")
assert not any(s.get("sickness") for s in hc["states"])
hs = read("healer-state-snapshots.json")
for name, durability in {"start": 17, "ghost-logout": 15, "ghost-relog": 15,
                         "alive-logout": 9, "alive-relog": 9, "final-logout": 9}.items():
    state = hs["healer-final-" + name]
    assert {i["itemEntry"]: i["durability"] for i in state["items"] if i["itemEntry"] in (39, 49778)} == {39: durability, 49778: durability}
    if name.startswith("ghost"):
        assert len(state["corpses"]) == 1 and state["corpses"][0]["guid"] == 1
        assert state["characters"][0]["health"] == 1 and state["characters"][0]["playerFlags"] & 16
    else:
        assert not state["corpses"]
        assert state["characters"][0]["health"] == 30 and state["characters"][0]["playerFlags"] == 0
assert hs["healer-final-ghost-logout"]["corpses"] == hs["healer-final-ghost-relog"]["corpses"]
assert hs["healer-final-alive-logout"] == hs["healer-final-final-logout"]
assert hs["healer-final-final-logout"]["characters"][0]["online"] == 0
hp = (ROOT / "healer-packet-events.log").read_text()
for name in ("CMSG_REPOP_REQUEST", "MSG_MOVE_TELEPORT_ACK", "SMSG_SPIRIT_HEALER_CONFIRM", "CMSG_SPIRIT_HEALER_ACTIVATE"):
    assert name in hp
assert "CMSG_RECLAIM_CORPSE" not in hp
runtime = read("healer-runtime-inputs.json")
assert runtime["script_configuration"]["SCRIPTS_SPELLS"] == "static"
assert runtime["loaded_cpp_scripts"] > 3 and runtime["no_current_run_crash_reports"]
hcleanup = read("healer-cleanup.json")
assert hcleanup["state"] == "reset" and hcleanup["container_removed"] and hcleanup["volume_removed"]
assert hcleanup["active_owned_processes"] == hcleanup["owned_wine_processes"] == 0
assert hcleanup["protected_inputs_unchanged"] and hcleanup["recorded_ports_unused"]
assert acceptance["result"] == acceptance["spirit_healer_result"] == "PASS"
print("PASS: native corpse reclaim and spirit-healer recovery, persistence, combat damage, power slots, cleanup.")

# Final replay isolates the result-code and ghost-login corrections on the final source.
audit = read("spell-result-audit.json")
assert audit["local_values_match_reference"] and audit["matched_values"] == 205
assert audit["changed_existing_values"] == audit["condition_mappings"] == 180
assert audit["ghost_wire_codes"] == {
    "SPELL_FAILED_CASTER_DEAD": 24, "SPELL_FAILED_CONFUSED": 27, "SPELL_FAILED_DONT_REPORT": 28,
}
migration = read("condition-migration-check.json")
assert migration["exact_expected_counts"] and migration["rows_checked"] == 99
replay = read("spell-result-client-events-and-state.json")
assert replay["loaded"]
replay_incoming = [event for event in replay["events"]
                   if at(event, 5) == "Diseased Young Wolf" and at(event, 8) == "0x0000000000000001"]
replay_damage = [int(at(event, 12)) if at(event, 2) == "SWING_DAMAGE" else 0 for event in replay_incoming]
server_damage = read("spell-result-incoming-damage.json")
assert len(replay_damage) == 63 and sum(replay_damage) == 60
assert replay_damage == [event["damage"] for event in server_damage]
recovery = [state for state in replay["states"] if state["event"] == "PLAYER_UNGHOST"]
assert len(recovery) == 1 and recovery[0]["health"] == 30 and recovery[0]["max"] == 60
assert not recovery[0].get("ghost") and not recovery[0].get("sickness")
replay_counts = Counter(state["event"] for state in replay["states"])
assert replay_counts["PLAYER_DEAD"] == replay_counts["PLAYER_UNGHOST"] == 1
assert replay_counts["PLAYER_ENTERING_WORLD"] == replay_counts["PLAYER_LEAVING_WORLD"] == 3
assert not any("confused" in str(error).lower() for error in replay.get("errors", []))
replay_saved = read("spell-result-state-snapshots.json")
for name in ("spell-final-ghost-logout", "spell-final-ghost-relog"):
    state = replay_saved[name]
    assert len(state["corpse"]) == 1
    assert state["characters"][0]["health"] == 1
    assert state["characters"][0]["playerFlags"] & 16
assert replay_saved["spell-final-ghost-logout"]["corpse"] == replay_saved["spell-final-ghost-relog"]["corpse"]
for name in ("spell-final-alive-logout", "spell-final-final-logout"):
    state = replay_saved[name]
    assert state["characters"][0]["online"] == 0
    assert state["characters"][0]["health"] == 30
    assert state["characters"][0]["playerFlags"] == 0 and not state["corpse"]
    assert [item["durability"] for item in state["item_instance"] if item["itemEntry"] in (39, 49778)] == [17, 17]
first = {k: v for k, v in replay_saved["spell-final-alive-logout"].items() if k != "epoch"}
last = {k: v for k, v in replay_saved["spell-final-final-logout"].items() if k != "epoch"}
assert first == last
packets = (ROOT / "spell-result-packet-events.log").read_text()
for opcode in ("CMSG_REPOP_REQUEST", "CMSG_MOVE_WATER_WALK_ACK", "SMSG_SPIRIT_HEALER_CONFIRM", "CMSG_SPIRIT_HEALER_ACTIVATE"):
    assert opcode in packets
assert "CMSG_RECLAIM_CORPSE" not in packets
runtime = read("spell-result-runtime-inputs.json")
assert runtime["source_commit"] == acceptance["final_source"]
assert runtime["no_current_run_crash_reports"]
cleanup = read("spell-result-cleanup.json")
assert cleanup["state"] == "reset" and cleanup["container_removed"] and cleanup["volume_removed"]
assert cleanup["active_owned_processes"] == cleanup["owned_wine_processes"] == 0
assert cleanup["protected_inputs_unchanged"] and cleanup["recorded_ports_unused"]
assert acceptance["spell_result_replay_result"] == "PASS"
print("PASS: final-source native release, ghost relog, spirit-healer recovery and living persistence; no confused UI error.")
