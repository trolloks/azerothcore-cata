"""Offline checks: no server, Wine process, or desktop input is started."""

import io
import json
import tarfile
import tempfile
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import MagicMock, patch

import run_real_client_authentication as runner
from test_issue118 import archive


def check_manual_session(root: Path) -> None:
    generation = {
        "number": 1, "state": "prepared", "mode": "no-login", "processes": [],
        "ports": {"world": 8085}, "inputs": {
            "wine": str(root / "wine"), "authserver": {"path": "authserver"},
            "worldserver": {"path": "worldserver"}, "display": ":0",
        },
        "paths": {key: str(root) for key in (
            "client", "logs", "raw_evidence", "wine_prefix", "auth_config", "world_config",
        )},
    }
    clock = [0]

    def sleep(_seconds: float) -> None:
        clock[0] += 100
        if clock[0] == 300:
            raise KeyboardInterrupt

    args = runner.parser().parse_args([
        "run", "--manifest", str(root / "manifest.json"), "--manual", "--no-login-seconds", "1",
    ])
    with ExitStack() as stack:
        for name in (
            "assert_container_owned", "ensure_client_base", "save_manifest", "write_client_config",
            "install_dxvk", "configure_wine_proxy", "require_unused", "wait_for_port", "wait_for_world",
            "add_processes", "capture_runtime", "record_failure", "process_identity",
        ):
            stack.enter_context(patch.object(runner, name))
        stack.enter_context(patch.object(runner, "load_manifest", return_value={"generations": [generation]}))
        stack.enter_context(patch.object(runner, "wine_environment", return_value={}))
        stack.enter_context(patch.object(runner, "start_server", side_effect=[
            (MagicMock(), {}, io.BytesIO()), (MagicMock(), {}, io.BytesIO()),
        ]))
        stack.enter_context(patch.object(runner.subprocess, "Popen"))
        stack.enter_context(patch.object(runner, "find_wine_processes", return_value=[{}]))
        stop_wine = stack.enter_context(patch.object(runner, "stop_wine"))
        stop_servers = stack.enter_context(patch.object(runner, "stop_servers"))
        stack.enter_context(patch.object(runner.time, "monotonic", side_effect=lambda: clock[0]))
        stack.enter_context(patch.object(runner.time, "sleep", side_effect=sleep))
        movement = stack.enter_context(patch.object(runner, "automate_key_sequence"))
        runner.run_client(args)
        assert clock[0] == 300, "manual session stopped at the one-second deadline"
        movement.assert_not_called()
        assert stop_wine.call_count == 2
        stop_servers.assert_called_once()

    (root / "Errors").mkdir()
    (root / "Errors/2026-09-26 Crash.txt").write_text("fatal fixture")
    manifest = root / "manifest.json"
    generation["processes"] = []
    # Keep archive input directories separate from the destination.
    logs = root / "logs"
    logs.mkdir()
    generation["paths"]["logs"] = generation["paths"]["raw_evidence"] = str(logs)
    manifest.write_text(json.dumps({"version": 1, "root": str(root), "generations": [generation]}))
    with patch("test_issue118.subprocess.check_output", return_value=b"diff fixture"):
        archive(manifest)
        archive(manifest)
    archives = list(root.glob("manual-*.tar.gz"))
    assert len(archives) == 2, "a later archive overwrote the earlier evidence"
    with tarfile.open(archives[0]) as bundle:
        assert bundle.extractfile("client-errors/2026-09-26 Crash.txt").read() == b"fatal fixture"


if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as directory:
        check_manual_session(Path(directory))
    print("Manual timeout, cleanup, and crash archive checks passed.")
