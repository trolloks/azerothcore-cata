"""Run with python3 apps/cata/test_ground_movement_verification.py. No client or server involved."""

from unittest.mock import patch

import run_real_client_authentication as runner


def _log(lines: list[str]) -> str:
    return (
        "Finished object update bootstrap after adding to map\n"
        + runner.IN_WORLD_CONTROL_MARKER + "\n"
        + "\n".join(lines) + "\n"
    )


def _marker(opcode: str, x: float, y: float, z: float, o: float) -> str:
    return f"{runner.GROUND_MOVEMENT_MARKER} [{opcode} 0x00B5 (181)] after movement validation: x={x}, y={y}, z={z}, o={o}"


def _heartbeat(x: float, y: float, z: float, o: float) -> str:
    return f"{runner.MOVEMENT_HEARTBEAT_MARKER}: x={x}, y={y}, z={z}, o={o}"


def check_parses_samples_and_accepts_correct_deltas() -> None:
    # Facing east (o=0): forward increases x, backward decreases it, strafe-left increases y,
    # strafe-right decreases y, turn-left increases o, turn-right decreases it.
    lines = [
        _marker("MSG_MOVE_START_FORWARD", 0, 0, 0, 0), _marker("MSG_MOVE_STOP", 1, 0, 0, 0),
        _marker("MSG_MOVE_START_BACKWARD", 1, 0, 0, 0), _marker("MSG_MOVE_STOP", 0, 0, 0, 0),
        _marker("MSG_MOVE_START_STRAFE_LEFT", 0, 0, 0, 0), _marker("MSG_MOVE_STOP_STRAFE", 0, 1, 0, 0),
        _marker("MSG_MOVE_START_STRAFE_RIGHT", 0, 1, 0, 0), _marker("MSG_MOVE_STOP_STRAFE", 0, 0, 0, 0),
        _marker("MSG_MOVE_START_TURN_LEFT", 0, 0, 0, 0), _marker("MSG_MOVE_STOP_TURN", 0, 0, 0, 1),
        _marker("MSG_MOVE_START_TURN_RIGHT", 0, 0, 0, 1), _marker("MSG_MOVE_STOP_TURN", 0, 0, 0, 0),
        _heartbeat(0, 0, 0, 0), _heartbeat(0, 0, 0, 0),
    ]
    generation = {}
    with patch.object(runner, "world_log_text", return_value=_log(lines)):
        samples = runner.ground_movement_samples(generation)
        assert [sample["opcode"] for sample in samples] == list(runner.GROUND_MOVEMENT_OPCODES)
        assert runner.ground_movement_deltas_are_action_appropriate(samples)
        assert runner.ground_movement_is_stable_after_final_stop(generation, samples)


def check_rejects_wrong_direction_delta() -> None:
    # Forward reported but position never actually moved along the facing vector: must fail, not
    # pass just because an MSG_MOVE_START_FORWARD/MSG_MOVE_STOP pair was logged.
    lines = [
        _marker("MSG_MOVE_START_FORWARD", 0, 0, 0, 0), _marker("MSG_MOVE_STOP", 0, 0, 0, 0),
    ]
    generation = {}
    with patch.object(runner, "world_log_text", return_value=_log(lines)):
        samples = runner.ground_movement_samples(generation)
        assert not runner.ground_movement_deltas_are_action_appropriate(samples)


def check_rejects_drift_during_stability_hold() -> None:
    lines = [
        _marker("MSG_MOVE_START_TURN_RIGHT", 0, 0, 0, 1), _marker("MSG_MOVE_STOP_TURN", 0, 0, 0, 0),
        _heartbeat(0, 0, 0, 0), _heartbeat(0.5, 0, 0, 0),  # drifted after the final stop
    ]
    generation = {}
    with patch.object(runner, "world_log_text", return_value=_log(lines)):
        samples = runner.ground_movement_samples(generation)
        assert not runner.ground_movement_is_stable_after_final_stop(generation, samples)


if __name__ == "__main__":
    check_parses_samples_and_accepts_correct_deltas()
    check_rejects_wrong_direction_delta()
    check_rejects_drift_during_stability_hold()
    print("Ground movement acceptance checks passed")
