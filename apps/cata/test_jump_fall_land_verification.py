"""Run with python3 apps/cata/test_jump_fall_land_verification.py. No client or server involved."""

from unittest.mock import patch

import run_real_client_authentication as runner


def _log(lines: list[str]) -> str:
    return (
        "Finished object update bootstrap after adding to map\n"
        + runner.IN_WORLD_CONTROL_MARKER + "\n"
        + "\n".join(lines) + "\n"
    )


def _marker(opcode: str, x: float, y: float, z: float, o: float) -> str:
    return f"{runner.GROUND_MOVEMENT_MARKER} [{opcode} 0x00BB (187)] after movement validation: x={x}, y={y}, z={z}, o={o}"


def _heartbeat(x: float, y: float, z: float, o: float) -> str:
    return f"{runner.MOVEMENT_HEARTBEAT_MARKER}: x={x}, y={y}, z={z}, o={o}"


def check_ignores_spawn_settle_land_and_accepts_the_automated_pair() -> None:
    # World entry settles the character onto the ground with its own MSG_MOVE_FALL_LAND before
    # the synthetic space-bar press; only the trailing JUMP/FALL_LAND pair is under test.
    lines = [
        _marker("MSG_MOVE_FALL_LAND", 0, 0, 0, 0),
        _marker("MSG_MOVE_JUMP", 0, 0, 0, 0), _marker("MSG_MOVE_FALL_LAND", 0.1, 0, 0, 0),
        _heartbeat(0.1, 0, 0, 0), _heartbeat(0.1, 0, 0, 0),
    ]
    generation = {}
    with patch.object(runner, "world_log_text", return_value=_log(lines)):
        samples = runner.ground_movement_samples(generation)
        pair = runner.jump_fall_land_action_pair(samples)
        assert [sample["opcode"] for sample in pair] == list(runner.JUMP_FALL_LAND_OPCODES)
        assert runner.jump_fall_land_deltas_are_action_appropriate(samples)
        assert runner.jump_fall_land_is_stable_after_landing(generation, samples)


def check_accepts_a_silent_idle_landing_with_no_trailing_heartbeats() -> None:
    # An idle client emits no heartbeats at all while standing still; absence of heartbeats is
    # not evidence of drift and must not fail the stability check.
    lines = [
        _marker("MSG_MOVE_JUMP", 0, 0, 0, 0), _marker("MSG_MOVE_FALL_LAND", 0, 0, 0, 0),
    ]
    generation = {}
    with patch.object(runner, "world_log_text", return_value=_log(lines)):
        samples = runner.ground_movement_samples(generation)
        assert runner.jump_fall_land_is_stable_after_landing(generation, samples)


def check_rejects_landing_far_from_takeoff() -> None:
    # A land reported many yards away from the jump is not "falling state cleared correctly on
    # land" in place; the mover must have teleported or the log parsing must be wrong.
    lines = [
        _marker("MSG_MOVE_JUMP", 0, 0, 0, 0), _marker("MSG_MOVE_FALL_LAND", 50, 0, 0, 0),
    ]
    generation = {}
    with patch.object(runner, "world_log_text", return_value=_log(lines)):
        samples = runner.ground_movement_samples(generation)
        assert not runner.jump_fall_land_deltas_are_action_appropriate(samples)


def check_rejects_drift_during_stability_hold() -> None:
    lines = [
        _marker("MSG_MOVE_JUMP", 0, 0, 0, 0), _marker("MSG_MOVE_FALL_LAND", 0, 0, 0, 0),
        _heartbeat(0, 0, 0, 0), _heartbeat(0, 0, 5, 0),  # still falling/drifting after landing
    ]
    generation = {}
    with patch.object(runner, "world_log_text", return_value=_log(lines)):
        samples = runner.ground_movement_samples(generation)
        assert not runner.jump_fall_land_is_stable_after_landing(generation, samples)


if __name__ == "__main__":
    check_ignores_spawn_settle_land_and_accepts_the_automated_pair()
    check_accepts_a_silent_idle_landing_with_no_trailing_heartbeats()
    check_rejects_landing_far_from_takeoff()
    check_rejects_drift_during_stability_hold()
    print("Jump/fall-land acceptance checks passed")
