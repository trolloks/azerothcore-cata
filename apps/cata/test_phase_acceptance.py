"""Offline checks: python3 apps/cata/test_phase_acceptance.py."""

import hashlib
import itertools
import json
from pathlib import Path
from subprocess import CompletedProcess
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, patch

import run_real_client_authentication as runner


def check_phase_relogin() -> None:
    logout = "S->C: [SMSG_LOGOUT_COMPLETE"
    phase = "S->C: [SMSG_PHASE_SHIFT_CHANGE"
    with (
        patch.object(runner, "world_log_text", side_effect=["", logout, logout, logout + phase]),
        patch.object(runner, "automate_key_sequence") as keys,
        patch.object(runner.time, "monotonic", return_value=0),
        patch.object(runner.time, "sleep"),
    ):
        runner.automate_phase_relogin({}, 10)
        assert keys.call_args_list[0].args[1] == ("Return", "slash", *"logout", "Return")
        assert keys.call_args_list[1].args[1] == ("Return",)
    for logs, message in (("", "logout"), (logout, "login")):
        with (
            patch.object(runner, "world_log_text", side_effect=["", *([logs] * 10)]),
            patch.object(runner, "automate_key_sequence"),
            patch.object(runner.time, "monotonic", side_effect=itertools.count()),
            patch.object(runner.time, "sleep"),
        ):
            try:
                runner.automate_phase_relogin({}, 3)
            except RuntimeError as error:
                assert message in str(error)
            else:
                raise AssertionError("accepted missing logout/login evidence")


def check_cache_restore() -> None:
    with TemporaryDirectory() as root:
        cache = Path(root) / "database-cache.sql"
        dump = b"LOCK TABLES t WRITE;\nINSERT INTO t VALUES (1);\nUNLOCK TABLES;"
        cache.write_bytes(dump)
        manifest = {"root": root, "mysql_root_password": "synthetic", "database_cache": {}}
        generation = {"docker": {"container": "owned"}, "inputs": {"database_cache_key": "key"}}

        def restore(command, **kwargs):
            assert kwargs["stdin"].read() == b"SET autocommit=0;\n" + dump + b"\nCOMMIT;\n"
            return CompletedProcess(command, 0, b"", b"")

        with (
            patch.object(runner, "assert_container_owned"),
            patch.object(runner.subprocess, "run", side_effect=restore),
        ):
            runner.restore_database_cache(Path(root) / "manifest.json", manifest, generation, cache)
        assert cache.read_bytes() == dump
        assert list(Path(root).iterdir()) == [cache]
        with (
            patch.object(runner, "assert_container_owned"),
            patch.object(runner.subprocess, "run", return_value=CompletedProcess([], 1, b"", b"bad SQL")),
            patch.object(runner, "save_manifest"),
            patch.object(runner, "reconcile_database_cache") as reconcile,
        ):
            try:
                runner.restore_database_cache(Path(root) / "manifest.json", manifest, generation, cache)
            except RuntimeError as error:
                assert "bad SQL" in str(error)
            else:
                raise AssertionError("failed restore was accepted")
            assert manifest["database_cache"]["state"] == "purging"
            reconcile.assert_called_once()


def check_repeat_archives_evidence(state: str = "observed") -> None:
    with TemporaryDirectory() as root:
        paths = {name: str(Path(root) / name) for name in ("raw_evidence", "logs", "client")}
        for name in ("raw_evidence", "logs"):
            directory = Path(paths[name])
            directory.mkdir()
            (directory / "old.txt").write_text(name)
        for name in ("Logs", "Errors"):
            directory = Path(paths["client"]) / name
            directory.mkdir(parents=True)
            (directory / "old.txt").write_text(name)
        generation = {
            "state": state, "mode": runner.IN_WORLD_CONTROL_MODE, "processes": [],
            "root": root, "paths": paths, "issue97_stages": ["old"],
        }
        args = runner.parser().parse_args(["run", "--manifest", str(Path(root) / "manifest.json")])
        with (
            patch.object(runner, "load_manifest", return_value={"generations": [generation]}),
            patch.object(runner, "stop_servers"),
            patch.object(runner, "assert_container_owned", side_effect=RuntimeError("stop before launch")),
        ):
            try:
                runner.run_client(args)
            except RuntimeError as error:
                assert str(error) == "stop before launch"
            else:
                raise AssertionError("unexpectedly launched a client")
        archive = Path(root) / "previous-runs/1"
        for name in ("raw_evidence", "logs"):
            assert (archive / name / "old.txt").read_text() == name
        for name in ("Logs", "Errors"):
            assert (archive / f"client-{name}" / "old.txt").read_text() == name
            assert not list((Path(paths["client"]) / name).iterdir())
        assert json.loads((archive / "generation.json").read_text())["state"] == state
        assert generation["state"] == "prepared"
        assert "issue97_stages" not in generation
        assert not list(Path(paths["raw_evidence"]).iterdir())


def check_update_bookkeeping() -> None:
    with TemporaryDirectory() as root:
        directory = Path(root)
        contents = b"DELIMITER $$\nSELECT 1$$\n"
        (directory / "update.sql").write_bytes(contents)
        with patch.object(runner, "mysql", side_effect=["", ""]) as mysql:
            assert runner.apply_released_updates({}, {}, directory, "world") == ["update.sql"]
        payload = mysql.call_args.args[2]
        assert payload.startswith(contents + b"\nDELIMITER ;\nINSERT INTO `updates`")
        assert hashlib.sha1(contents).hexdigest().upper().encode() in payload
        assert mysql.call_count == 2  # One inventory query and one update, including its record.
        with patch.object(runner, "mysql", side_effect=["", RuntimeError("SQL failed")]):
            try:
                runner.apply_released_updates({}, {}, directory, "world")
            except RuntimeError:
                pass
            else:
                raise AssertionError("failed update was reported as applied")


def check_phase_walk(drift: bool = False, stalled: bool = False) -> None:
    generation = {}
    samples = []
    elapsed = 0.0
    x = -9054.0
    packets = 1

    def sleep(seconds):
        nonlocal elapsed
        elapsed += seconds

    def tap(_generation, keys, **kwargs):
        nonlocal x, packets
        before = x < -9067
        x += 0 if stalled else (-2 if keys == ("w",) else 2.5)
        packets += (x < -9067) != before
        samples.append({"opcode": "MSG_MOVE_STOP", "x": x, "y": -30 if drift else -36.5})
        sleep(kwargs["hold_seconds"] + kwargs["gap_seconds"])

    with (
        patch.object(runner, "CHARACTER_POSITION", (-9054.0, -36.5, 88.8)),
        patch.object(runner, "ground_movement_samples", side_effect=lambda _: samples.copy()),
        patch.object(runner, "world_log_text", side_effect=lambda _: "S->C: [SMSG_PHASE_SHIFT_CHANGE\n" * packets),
        patch.object(runner, "automate_key_sequence", side_effect=tap),
        patch.object(runner, "capture_desktop_screenshot"),
        patch.object(runner, "automate_phase_chest_click"),
        patch.object(runner, "automate_phase_relogin"),
        patch.object(runner.time, "monotonic", side_effect=lambda: elapsed),
        patch.object(runner.time, "sleep", side_effect=sleep),
    ):
        if drift or stalled:
            try:
                runner.automate_issue97_phases(generation, 60)
            except RuntimeError as error:
                assert ("corridor" if drift else "stalled") in str(error)
            else:
                raise AssertionError("accepted a walk outside the measured corridor")
        else:
            runner.automate_issue97_phases(generation, 60)
            stages = generation["issue97_stages"]
            assert [stage["phase_packet_count"] for stage in stages] == [2, 3, 4]
            assert [stage["expected_phases"] for stage in stages] == [[125], [], [125]]
            assert stages[0]["stop"]["x"] <= -9070
            assert stages[1]["stop"]["x"] >= -9066.3
            assert stages[2]["stop"]["x"] <= -9070
            assert elapsed < 30


def check_short_login_wait(movie: bool) -> None:
    elapsed = 0.0
    connection = MagicMock()
    connection.screen().root.get_full_property.return_value.value = [0x200]

    def sleep(seconds):
        nonlocal elapsed
        elapsed += seconds

    def processes(_):
        return [{"cmdline": ["MovieProxy.exe"]}] if movie and elapsed < 1 else []

    generation = {"paths": {"wine_prefix": "/tmp/owned-prefix"}, "inputs": {"display": ":99"}}
    with (
        patch.object(runner, "focus_owned_window", return_value=("0x200", 0, 0, 800, 600)),
        patch.object(runner, "find_wine_processes", side_effect=processes),
        patch.object(runner.time, "monotonic", side_effect=lambda: elapsed),
        patch.object(runner.time, "sleep", side_effect=sleep),
        patch("Xlib.display.Display", return_value=connection),
        patch("Xlib.ext.xtest.fake_input") as input_event,
    ):
        runner.automate_client_login(generation)
        assert input_event.call_count > 0
        assert elapsed < 6


def check_capture_focus_loss() -> None:
    with TemporaryDirectory() as root:
        screenshot = Path(root) / "phase.png"
        screenshot.write_bytes(b"unowned screenshot")
        generation = {"paths": {"raw_evidence": root}}
        with (
            patch.object(runner.shutil, "which", return_value="gnome-screenshot"),
            patch.object(runner, "focus_owned_window", return_value=("0x200", 0, 0, 800, 600)),
            patch.object(runner, "wine_environment", return_value={}),
            patch.object(runner, "run_command", side_effect=[
                CompletedProcess([], 0, b"", b""),
                CompletedProcess([], 0, b"_NET_ACTIVE_WINDOW(WINDOW): window id # 0x999", b""),
            ]),
        ):
            runner.capture_desktop_screenshot(generation, "phase.png")
        assert not screenshot.exists()


def check_changed_binary_is_rejected() -> None:
    with TemporaryDirectory() as root:
        binary = Path(root) / "authserver"
        binary.write_bytes(b"changed binary")
        generation = {
            "state": "prepared", "mode": runner.IN_WORLD_CONTROL_MODE,
            "inputs": {"authserver": {"path": str(binary), "sha256": "previous hash"}},
        }
        args = runner.parser().parse_args(["run", "--manifest", str(Path(root) / "manifest.json")])
        with (
            patch.object(runner, "load_manifest", return_value={"generations": [generation]}),
            patch.object(runner, "assert_container_owned"),
            patch.object(runner, "ensure_client_base") as client,
        ):
            try:
                runner.run_client(args)
            except RuntimeError as error:
                assert "authserver binary changed" in str(error)
            else:
                raise AssertionError("launched with a changed binary")
            client.assert_not_called()


if __name__ == "__main__":
    check_repeat_archives_evidence()
    check_repeat_archives_evidence(state="accepted")
    check_cache_restore()
    check_phase_relogin()
    check_update_bookkeeping()
    check_phase_walk()
    check_phase_walk(drift=True)
    check_phase_walk(stalled=True)
    check_short_login_wait(movie=False)
    check_short_login_wait(movie=True)
    check_capture_focus_loss()
    check_changed_binary_is_rejected()
    state = {"state": "inconclusive"}
    runner.set_state(state, "accepted")
    assert state["state"] == "accepted"
    print("Phase walk and update checks passed")
