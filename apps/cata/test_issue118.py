#!/usr/bin/env python3
"""Manual issue 118 replay using the preserved local fixture; archive before any reset."""

import argparse
import hashlib
import json
import subprocess
import tarfile
from datetime import datetime, timezone
from pathlib import Path

import run_real_client_authentication as harness


def archive(manifest_path: Path) -> None:
    if not manifest_path.is_file():
        return
    generation = harness.active_generation(harness.load_manifest(manifest_path))
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    destination = manifest_path.parent / f"manual-g{generation['number']}-{stamp}.tar.gz"
    paths = generation["paths"]
    with tarfile.open(destination, "x:gz") as bundle:
        bundle.add(manifest_path, arcname="manifest.json")
        for name, source in (
            ("logs", Path(paths["logs"])),
            ("evidence", Path(paths["raw_evidence"])),
            ("client-logs", Path(paths["client"]) / "Logs"),
            ("client-errors", Path(paths["client"]) / "Errors"),
        ):
            if source.exists():
                bundle.add(source, arcname=name)
        source_diff = subprocess.check_output(["git", "diff", "HEAD"], cwd=harness.REPO_ROOT)
        diff_path = manifest_path.parent / "manual-source.diff"
        diff_path.write_bytes(source_diff)
        bundle.add(diff_path, arcname="source.diff")
    with destination.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    destination.with_suffix(destination.suffix + ".sha256").write_text(f"{digest}  {destination.name}\n")
    print(f"Saved evidence: {destination}", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build", action="store_true", help="Build worldserver with the current working-tree changes first")
    parser.add_argument("--root", type=Path, default=Path(
        "/mnt/e1384d9e-bede-40dd-8b1c-be0beb488490/Fun/.plan11-runs/auth-15595"))
    args = parser.parse_args()
    root = args.root.resolve()
    manifest_path = root / "issue118-debug/manifest.json"
    source = json.loads((root / "manifest.json").read_text())
    inputs = next(g for g in source["generations"] if g["number"] == 110)["inputs"]
    build = harness.REPO_ROOT / "var/build-plan7"
    if args.build:
        subprocess.run(["cmake", "-S", str(harness.REPO_ROOT), "-B", str(build)], check=True)
        subprocess.run(["cmake", "--build", str(build), "--target", "worldserver", "-j", "2"], check=True)
    else:
        print("Using existing binaries; this does not test unbuilt source changes. Use --build for the fix.", flush=True)

    archive(manifest_path)
    if manifest_path.exists():
        generation = harness.active_generation(harness.load_manifest(manifest_path))
        if generation["state"] != "reset":
            harness.reset(harness.parser().parse_args(["reset", "--manifest", str(manifest_path)]))
    prepare = ["prepare", "--manifest", str(manifest_path),
               "--authserver", str(build / "src/server/apps/authserver"),
               "--worldserver", str(build / "src/server/apps/worldserver"),
               "--unit-tests", str(build / "src/test/unit_tests"),
               "--mode", "in-world-control-bootstrap"]
    for field in ("client_root", "data_root", "server_dbc_root", "wine_runner", "personal_bottle", "display"):
        prepare.extend(["--" + field.replace("_", "-"), str(inputs[field])])
    if inputs.get("xauthority"):
        prepare.extend(["--xauthority", str(inputs["xauthority"])])
    harness.prepare(harness.parser().parse_args(prepare))
    print("Select Cataplan and enter the world. First try moving backward from the starting point.\n"
          "For packet capture, enter '.packetlog Cataplan on' in chat before moving.\n"
          "When done or after a crash, press Ctrl+C in this terminal. The logs will be archived.", flush=True)
    try:
        harness.run_client(harness.parser().parse_args([
            "run", "--manifest", str(manifest_path), "--auto-login", "--manual",
        ]))
    finally:
        archive(manifest_path)


if __name__ == "__main__":
    main()
