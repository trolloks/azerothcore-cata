#!/usr/bin/env python3
"""Run the isolated Plan 6 authserver-to-world authentication proof."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import secrets
import signal
import socket
import struct
import subprocess
import sys
import time
import uuid


REPO_ROOT = Path(__file__).resolve().parents[2]
MYSQL_IMAGE = "mysql:8.4"
ACCOUNT = "PLAN6USER"
PASSWORD = "PLAN6PASS"
ACCOUNT_ID = 900000
REALM_ID = 42
N = int("894B645E89E1535BBDAD5B8B290650530801B18EBFBF5E8FAB3C82872A3E9BB7", 16)
N_BYTES = N.to_bytes(32, "little")
G = 7
DEFAULT_CLIENT = Path(
    "/mnt/f79365ff-6a68-45da-925e-b9ddc6d5da6c/Blizzard Games/Battle.NET/drive_c/Games/"
    "Cataclysm-4.3.4.15595-enUS-x64/Wow-64.exe"
)
DEFAULT_BOTTLE = Path("/mnt/f79365ff-6a68-45da-925e-b9ddc6d5da6c/Blizzard Games/Battle.NET")


def run_command(
    args: list[str], *, input_bytes: bytes | None = None, check: bool = True, timeout: int = 120
) -> subprocess.CompletedProcess[bytes]:
    result = subprocess.run(args, input=input_bytes, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            check=False, timeout=timeout)
    if check and result.returncode:
        detail = result.stderr.decode(errors="replace").strip() or result.stdout.decode(errors="replace").strip()
        raise RuntimeError(f"{args[0]} failed with exit {result.returncode}: {detail}")
    return result


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


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
        digest.update(f"{relative}\0{stat.st_mode}\0{stat.st_size}\0{stat.st_mtime_ns}\n".encode())
    return digest.hexdigest()


def docker_inventory() -> dict[str, list[str]]:
    containers = run_command(["docker", "ps", "-a", "--no-trunc", "--format", "{{.ID}} {{.Names}}"]).stdout
    volumes = run_command(["docker", "volume", "ls", "--format", "{{.Name}}"]).stdout
    return {
        "containers": sorted(containers.decode().splitlines()),
        "volumes": sorted(volumes.decode().splitlines()),
    }


def unused_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def require_unused(port: int) -> None:
    with socket.socket() as probe:
        try:
            probe.bind(("127.0.0.1", port))
        except OSError as error:
            raise RuntimeError(f"loopback port {port} is already in use") from error


def load_manifest(path: Path) -> dict:
    if not path.exists():
        raise RuntimeError(f"manifest does not exist: {path}")
    return json.loads(path.read_text())


def save_manifest(path: Path, manifest: dict) -> None:
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
    finally:
        temporary.unlink(missing_ok=True)


def require_external_manifest(path: Path) -> None:
    resolved = path.resolve()
    if resolved == REPO_ROOT or REPO_ROOT in resolved.parents:
        raise RuntimeError("the Plan 6 manifest must be outside the Git worktree")


def active_generation(manifest: dict) -> dict:
    if not manifest["generations"]:
        raise RuntimeError("manifest has no prepared generation")
    return manifest["generations"][-1]


def container_details(name: str) -> dict:
    result = run_command(["docker", "inspect", name], check=False)
    if result.returncode:
        raise RuntimeError(f"owned container is absent: {name}")
    return json.loads(result.stdout)[0]


def assert_owned(manifest: dict, generation: dict, schema: str | None = None) -> dict:
    details = container_details(generation["container"])
    labels = details["Config"].get("Labels") or {}
    expected = {
        "org.azerothcore.plan": "6",
        "org.azerothcore.run_id": manifest["run_id"],
        "org.azerothcore.generation": str(generation["number"]),
    }
    if details["Id"] != generation["container_id"] or any(labels.get(key) != value for key, value in expected.items()):
        raise RuntimeError("container identity or ownership labels changed")
    binding = details["NetworkSettings"]["Ports"].get("3306/tcp")
    if not binding or binding[0]["HostIp"] != "127.0.0.1" or int(binding[0]["HostPort"]) != generation["mysql_port"]:
        raise RuntimeError("owned MySQL loopback port mapping changed")
    volumes = {mount["Name"] for mount in details["Mounts"] if mount["Type"] == "volume"}
    if volumes != {generation["volume"]}:
        raise RuntimeError("owned MySQL volume attachment changed")
    if schema is not None and schema not in (generation["auth_schema"], generation["character_schema"]):
        raise RuntimeError(f"refusing unowned schema: {schema}")
    return details


def mysql(manifest: dict, generation: dict, sql: str | bytes, schema: str | None = None) -> str:
    assert_owned(manifest, generation, schema)
    command = ["docker", "exec", "-i", generation["container"], "mysql", "--batch", "--skip-column-names",
               "-uroot", f"-p{manifest['mysql_root_password']}"]
    if schema:
        command.extend(["-D", schema])
    payload = sql.encode() if isinstance(sql, str) else sql
    result = run_command(command, input_bytes=payload, timeout=300)
    return result.stdout.decode().rstrip("\n")


def import_sql_directory(manifest: dict, generation: dict, directory: Path, schema: str) -> None:
    for path in sorted(directory.glob("*.sql")):
        mysql(manifest, generation, path.read_bytes(), schema)


def apply_released_updates(manifest: dict, generation: dict, directory: Path, schema: str) -> list[str]:
    applied = set(mysql(manifest, generation, "SELECT `name` FROM `updates`;", schema).splitlines())
    added = []
    for path in sorted(directory.glob("*.sql")):
        if path.name in applied:
            continue
        mysql(manifest, generation, path.read_bytes(), schema)
        file_hash = hashlib.sha1(path.read_bytes()).hexdigest().upper()
        mysql(manifest, generation,
              "INSERT INTO `updates` (`name`,`hash`,`state`,`speed`) "
              f"VALUES ('{path.name}','{file_hash}','RELEASED',0);", schema)
        added.append(path.name)
    return added


def srp_registration(username: str, password: str, salt: bytes) -> bytes:
    identity = hashlib.sha1(f"{username}:{password}".encode()).digest()
    x = int.from_bytes(hashlib.sha1(salt + identity).digest(), "little")
    return pow(G, x, N).to_bytes(32, "little")


def sha1_interleave(session_secret: bytes) -> bytes:
    first_nonzero = next((index for index, value in enumerate(session_secret) if value), len(session_secret))
    if first_nonzero & 1:
        first_nonzero += 1
    offset = first_nonzero // 2
    even = session_secret[0::2]
    odd = session_secret[1::2]
    left = hashlib.sha1(even[offset:]).digest()
    right = hashlib.sha1(odd[offset:]).digest()
    return b"".join(bytes((left[index], right[index])) for index in range(20))


def calculate_srp(
    username: str, password: str, server_b: bytes, salt: bytes, private_a: int = 0x123456789ABCDEF
) -> dict[str, bytes]:
    identity = hashlib.sha1(f"{username}:{password}".encode()).digest()
    x = int.from_bytes(hashlib.sha1(salt + identity).digest(), "little")
    a_bytes = pow(G, private_a, N).to_bytes(32, "little")
    u = int.from_bytes(hashlib.sha1(a_bytes + server_b).digest(), "little")
    base = (int.from_bytes(server_b, "little") - 3 * pow(G, x, N)) % N
    secret = pow(base, private_a + u * x, N).to_bytes(32, "little")
    session_key = sha1_interleave(secret)
    n_hash = hashlib.sha1(N_BYTES).digest()
    g_hash = hashlib.sha1(bytes((G,))).digest()
    xor_hash = bytes(left ^ right for left, right in zip(n_hash, g_hash))
    client_m = hashlib.sha1(
        xor_hash + hashlib.sha1(username.encode()).digest() + salt + a_bytes + server_b + session_key
    ).digest()
    return {
        "A": a_bytes,
        "M1": client_m,
        "K": session_key,
        "M2": hashlib.sha1(a_bytes + client_m + session_key).digest(),
    }


def recv_exact(connection: socket.socket, length: int) -> bytes:
    data = bytearray()
    while len(data) < length:
        chunk = connection.recv(length - len(data))
        if not chunk:
            raise RuntimeError(f"connection closed after {len(data)} of {length} bytes")
        data.extend(chunk)
    return bytes(data)


def challenge_packet(build: int, account: str) -> bytes:
    encoded = account.encode()
    body_size = 30 + len(encoded)
    return struct.pack("<BBH4sBBBH4s4s4sIIB", 0, 0, body_size, b"WoW\0", 4, 3, 4, build,
                       b"68x\0", b"niW\0", b"SUne", 0, 0, len(encoded)) + encoded


def unsupported_build(port: int) -> dict:
    with socket.create_connection(("127.0.0.1", port), timeout=5) as connection:
        connection.settimeout(5)
        connection.sendall(challenge_packet(15596, ACCOUNT))
        response = recv_exact(connection, 3)
    if response != b"\x00\x00\x09":
        raise RuntimeError(f"unsupported build response was {response.hex()}, expected 000009")
    return {"build": 15596, "response": response.hex().upper()}


def parse_realm_list(payload: bytes, expected_realm: int) -> dict:
    if len(payload) < 6:
        raise RuntimeError("realm list payload is truncated")
    offset = 4
    realm_count = struct.unpack_from("<H", payload, offset)[0]
    offset += 2
    found = None
    for _ in range(realm_count):
        if offset + 3 > len(payload):
            raise RuntimeError("realm entry header is truncated")
        realm_type, locked, flags = struct.unpack_from("<BBB", payload, offset)
        offset += 3

        def read_cstring() -> str:
            nonlocal offset
            end = payload.find(b"\0", offset)
            if end < 0:
                raise RuntimeError("realm entry string is unterminated")
            value = payload[offset:end].decode()
            offset = end + 1
            return value

        name = read_cstring()
        address = read_cstring()
        if offset + 7 > len(payload):
            raise RuntimeError("realm entry body is truncated")
        population, characters, timezone, realm_id = struct.unpack_from("<fBBB", payload, offset)
        offset += 7
        build = None
        if flags & 0x04:
            if offset + 5 > len(payload):
                raise RuntimeError("realm build tuple is truncated")
            major, minor, bugfix, build = struct.unpack_from("<BBBH", payload, offset)
            offset += 5
            build = {"build": build, "version": [major, minor, bugfix]}
        if realm_id == expected_realm:
            found = {
                "id": realm_id,
                "name": name,
                "address": address,
                "type": realm_type,
                "locked": locked,
                "flags": flags,
                "population_nonnegative": population >= 0,
                "characters": characters,
                "timezone": timezone,
                "specified_build": build,
            }
    if found is None:
        raise RuntimeError(f"realm {expected_realm} was not advertised")
    return found


def authenticate(port: int) -> tuple[dict, bytes]:
    with socket.create_connection(("127.0.0.1", port), timeout=5) as connection:
        connection.settimeout(5)
        connection.sendall(challenge_packet(15595, ACCOUNT))
        challenge = recv_exact(connection, 119)
        if challenge[:3] != b"\x00\x00\x00":
            raise RuntimeError("build 15595 challenge header differs")
        server_b = challenge[3:35]
        if challenge[35] != 1 or challenge[36] != G or challenge[37] != 32 or challenge[38:70] != N_BYTES:
            raise RuntimeError("authserver returned unexpected SRP group parameters")
        salt = challenge[70:102]
        security_flags = challenge[118]
        if security_flags:
            raise RuntimeError("synthetic account unexpectedly requires a security token")
        proof = calculate_srp(ACCOUNT, PASSWORD, server_b, salt)
        request = b"\x01" + proof["A"] + proof["M1"] + bytes(20) + b"\x00\x00"
        if len(request) != 75:
            raise AssertionError("client proof must be 75 bytes")
        connection.sendall(request)
        response = recv_exact(connection, 32)
        if response[:2] != b"\x01\x00" or response[2:22] != proof["M2"]:
            raise RuntimeError("authserver proof response did not verify M2")
        connection.sendall(struct.pack("<BI", 0x10, 0))
        header = recv_exact(connection, 3)
        if header[0] != 0x10:
            raise RuntimeError("authserver returned the wrong realm-list opcode")
        payload = recv_exact(connection, struct.unpack_from("<H", header, 1)[0])
        realm_entry = parse_realm_list(payload, REALM_ID)
    return {
        "challenge_length": len(challenge),
        "client_proof_length": len(request),
        "server_proof_length": len(response),
        "m2_verified": True,
        "realm": realm_entry,
    }, proof["K"]


def write_authserver_config(path: Path, generation: dict, manifest: dict) -> None:
    source = (REPO_ROOT / "src/server/apps/authserver/authserver.conf.dist").read_text()
    replacements = {
        "RealmServerPort": str(generation["auth_port"]),
        "BindIP": '"127.0.0.1"',
        "RealmsStateUpdateDelay": "1",
        "LoginDatabaseInfo": f'"127.0.0.1;{generation["mysql_port"]};{manifest["mysql_user"]};'
                             f'{manifest["mysql_password"]};{generation["auth_schema"]}"',
        "Updates.EnableDatabases": "0",
        "Updates.AutoSetup": "0",
        "StrictVersionCheck": "0",
        "SourceDirectory": f'"{REPO_ROOT}"',
        "PidFile": f'"{path.parent / "authserver.pid"}"',
    }
    lines = source.splitlines()
    seen = set()
    for index, line in enumerate(lines):
        for key, value in replacements.items():
            if line.startswith(f"{key} ="):
                lines[index] = f"{key} = {value}"
                seen.add(key)
    missing = set(replacements) - seen
    if missing:
        raise RuntimeError(f"authserver config keys are missing: {sorted(missing)}")
    path.write_text("\n".join(lines) + "\n")


def wait_for_mysql(manifest: dict, generation: dict) -> None:
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        logs = run_command(["docker", "logs", generation["container"]], check=False)
        result = run_command([
            "docker", "exec", generation["container"], "mysql", "--batch", "--skip-column-names", "-uroot",
            f"-p{manifest['mysql_root_password']}", "-e", "SELECT 1;",
        ], check=False)
        if b"MySQL init process done. Ready for start up." in logs.stdout + logs.stderr \
                and result.returncode == 0 and result.stdout.strip() == b"1":
            return
        time.sleep(0.25)
    raise RuntimeError("owned MySQL did not become ready within 60 seconds")


def wait_for_port(port: int, process: subprocess.Popen[bytes]) -> None:
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"authserver exited with status {process.returncode}")
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                return
        except OSError:
            time.sleep(0.1)
    raise RuntimeError("authserver did not open its loopback port within 30 seconds")


def pid_start_time(pid: int) -> str:
    return Path(f"/proc/{pid}/stat").read_text().split()[21]


def stop_authserver(generation: dict, process: subprocess.Popen[bytes] | None = None) -> None:
    record = generation.get("authserver_process")
    if not record:
        return
    pid = int(record["pid"])
    proc = Path(f"/proc/{pid}")
    if proc.exists():
        command = (proc / "cmdline").read_bytes().split(b"\0")
        if (record["start_time"] != pid_start_time(pid) or os.fsencode(record["binary"]) not in command
                or os.fsencode(record["config"]) not in command):
            raise RuntimeError("refusing to signal a PID whose identity no longer matches the manifest")
        os.kill(pid, signal.SIGTERM)
        deadline = time.monotonic() + 15
        while proc.exists() and time.monotonic() < deadline:
            if process is not None and process.poll() is not None:
                break
            time.sleep(0.1)
        if proc.exists() and (process is None or process.poll() is None):
            raise RuntimeError("owned authserver did not stop within 15 seconds")
    generation["authserver_process"] = None


def prepare(args: argparse.Namespace) -> None:
    manifest_path = args.manifest.resolve()
    require_external_manifest(manifest_path)
    migration = args.migration.resolve()
    if not migration.is_file() or migration.parent != REPO_ROOT / "data/sql/updates/pending_db_auth":
        raise RuntimeError("--migration must name the Plan 6 pending auth SQL file")
    if run_command(["docker", "image", "inspect", MYSQL_IMAGE], check=False).returncode:
        raise RuntimeError(f"required local image is absent: {MYSQL_IMAGE}")

    if manifest_path.exists():
        manifest = load_manifest(manifest_path)
        latest = active_generation(manifest)
        if sha256(migration) != manifest["migration_sha256"] or str(migration) != manifest["migration"]:
            raise RuntimeError("the recorded Plan 6 migration path or content changed")
        if latest["state"] == "allocating":
            reset(argparse.Namespace(manifest=manifest_path))
            manifest = load_manifest(manifest_path)
            latest = active_generation(manifest)
        if latest["state"] != "reset":
            if latest["state"] in ("prepared", "passed"):
                print(f"generation {latest['number']} is already {latest['state']}")
                return
            raise RuntimeError(f"cannot prepare from state {latest['state']}")
    else:
        manifest = {
            "version": 1,
            "run_id": uuid.uuid4().hex[:12],
            "repo_root": str(REPO_ROOT),
            "repo_commit": run_command(["git", "rev-parse", "HEAD"]).stdout.decode().strip(),
            "mysql_root_password": secrets.token_hex(16),
            "mysql_user": "plan6",
            "mysql_password": secrets.token_hex(16),
            "migration": str(migration),
            "migration_sha256": sha256(migration),
            "client": str(args.client.resolve()),
            "bottle": str(args.bottle.resolve()),
            "baseline": {
                "docker": docker_inventory(),
                "client_sha256": sha256(args.client) if args.client.is_file() else None,
                "client_tree": tree_metadata(args.client.parent),
                "bottle_tree": tree_metadata(args.bottle),
            },
            "generations": [],
        }

    number = len(manifest["generations"]) + 1
    prefix = f"acore-cata-plan6-{manifest['run_id']}-g{number}"
    generation = {
        "number": number,
        "state": "allocating",
        "container": f"{prefix}-mysql",
        "container_id": None,
        "volume": f"{prefix}-mysql-data",
        "mysql_port": unused_port(),
        "auth_port": unused_port(),
        "world_port": unused_port(),
        "auth_schema": "acore_auth",
        "character_schema": "acore_characters",
        "authserver_process": None,
        "evidence": None,
    }
    manifest["generations"].append(generation)
    save_manifest(manifest_path, manifest)

    labels = ["--label", "org.azerothcore.plan=6", "--label", f"org.azerothcore.run_id={manifest['run_id']}",
              "--label", f"org.azerothcore.generation={number}"]
    require_unused(generation["mysql_port"])
    run_command(["docker", "volume", "create", *labels, generation["volume"]])
    result = run_command([
        "docker", "run", "-d", "--name", generation["container"], *labels,
        "-e", f"MYSQL_ROOT_PASSWORD={manifest['mysql_root_password']}",
        "-p", f"127.0.0.1:{generation['mysql_port']}:3306",
        "-v", f"{generation['volume']}:/var/lib/mysql", MYSQL_IMAGE,
    ])
    generation["container_id"] = result.stdout.decode().strip()
    save_manifest(manifest_path, manifest)
    wait_for_mysql(manifest, generation)

    mysql(manifest, generation,
          f"CREATE DATABASE `{generation['auth_schema']}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;"
          f"CREATE DATABASE `{generation['character_schema']}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;"
          f"CREATE USER '{manifest['mysql_user']}'@'%' IDENTIFIED BY '{manifest['mysql_password']}';"
          f"GRANT ALL ON `{generation['auth_schema']}`.* TO '{manifest['mysql_user']}'@'%';"
          f"GRANT ALL ON `{generation['character_schema']}`.* TO '{manifest['mysql_user']}'@'%';")

    import_sql_directory(manifest, generation, REPO_ROOT / "data/sql/base/db_auth", generation["auth_schema"])
    auth_updates = apply_released_updates(manifest, generation, REPO_ROOT / "data/sql/updates/db_auth",
                                          generation["auth_schema"])
    import_sql_directory(manifest, generation, REPO_ROOT / "data/sql/base/db_characters",
                         generation["character_schema"])
    character_updates = apply_released_updates(manifest, generation, REPO_ROOT / "data/sql/updates/db_characters",
                                               generation["character_schema"])

    before = mysql(manifest, generation, "SELECT * FROM `realmlist` ORDER BY `id`;", generation["auth_schema"])
    mysql(manifest, generation, migration.read_bytes(), generation["auth_schema"])
    after = mysql(manifest, generation, "SELECT * FROM `realmlist` ORDER BY `id`;", generation["auth_schema"])
    if before != after:
        raise RuntimeError("Plan 6 migration changed an existing realm row")
    build = mysql(manifest, generation,
                  "SELECT `build`,`majorVersion`,`minorVersion`,`bugfixVersion`,`hotfixVersion`,`winAuthSeed`,"
                  "`win64AuthSeed`,`mac64AuthSeed`,`winChecksumSeed`,`macChecksumSeed` FROM `build_info` "
                  "WHERE `build`=15595;",
                  generation["auth_schema"])
    if build != "15595\t4\t3\t4\tNULL\tNULL\tNULL\tNULL\tNULL\tNULL":
        raise RuntimeError(f"unexpected build 15595 row: {build}")
    default = mysql(manifest, generation,
                    "SELECT `COLUMN_DEFAULT` FROM `information_schema`.`COLUMNS` WHERE `TABLE_SCHEMA`=DATABASE() "
                    "AND `TABLE_NAME`='realmlist' AND `COLUMN_NAME`='gamebuild';", generation["auth_schema"])
    if default != "15595":
        raise RuntimeError(f"realmlist.gamebuild default is {default}, expected 15595")

    salt = bytes(range(1, 33))
    verifier = srp_registration(ACCOUNT, PASSWORD, salt)
    mysql(manifest, generation,
          "DELETE FROM `realmcharacters` WHERE `acctid`=900000 OR `realmid`=42;"
          "DELETE FROM `account` WHERE `id`=900000 OR `username`='PLAN6USER';"
          "DELETE FROM `realmlist` WHERE `id`=42 OR `name`='Plan 6 Realm';"
          "INSERT INTO `account` (`id`,`username`,`salt`,`verifier`,`email`,`reg_mail`,`expansion`,`Flags`) "
          f"VALUES ({ACCOUNT_ID},'{ACCOUNT}',UNHEX('{salt.hex()}'),UNHEX('{verifier.hex()}'),'plan6@example.invalid',"
          "'plan6@example.invalid',3,0);"
          "INSERT INTO `realmlist` (`id`,`name`,`address`,`localAddress`,`localSubnetMask`,`port`,`icon`,`flag`,"
          "`timezone`,`allowedSecurityLevel`,`population`,`gamebuild`) "
          f"VALUES ({REALM_ID},'Plan 6 Realm','127.0.0.1','127.0.0.1','255.255.255.0',"
          f"{generation['world_port']},0,0,1,0,0,15595);"
          f"INSERT INTO `realmcharacters` (`realmid`,`acctid`,`numchars`) VALUES ({REALM_ID},{ACCOUNT_ID},0);",
          generation["auth_schema"])

    generation["released_updates"] = {"auth": auth_updates, "characters": character_updates}
    generation["realm_rows_before_migration_sha256"] = hashlib.sha256(before.encode()).hexdigest()
    generation["realm_rows_after_migration_sha256"] = hashlib.sha256(after.encode()).hexdigest()
    generation["state"] = "prepared"
    save_manifest(manifest_path, manifest)
    print(f"prepared generation {number}: {generation['container']} on MySQL port {generation['mysql_port']}")


def execute(args: argparse.Namespace) -> None:
    manifest_path = args.manifest.resolve()
    require_external_manifest(manifest_path)
    manifest = load_manifest(manifest_path)
    generation = active_generation(manifest)
    if generation["state"] == "passed":
        print(f"generation {generation['number']} already passed")
        return
    if generation["state"] != "prepared":
        raise RuntimeError(f"cannot run from state {generation['state']}")
    assert_owned(manifest, generation)
    authserver = args.authserver.resolve()
    unit_tests = args.unit_tests.resolve()
    if not authserver.is_file() or not os.access(authserver, os.X_OK):
        raise RuntimeError(f"authserver is not executable: {authserver}")
    if not unit_tests.is_file() or not os.access(unit_tests, os.X_OK):
        raise RuntimeError(f"unit_tests is not executable: {unit_tests}")
    require_unused(generation["auth_port"])
    require_unused(generation["world_port"])
    mysql(manifest, generation,
          f"UPDATE `account` SET `session_key`=NULL,`online`=0 WHERE `id`={ACCOUNT_ID};",
          generation["auth_schema"])

    generation_dir = manifest_path.parent / f"generation-{generation['number']}"
    generation_dir.mkdir(parents=True, exist_ok=True)
    config = generation_dir / "authserver.conf"
    log_path = generation_dir / "authserver.log"
    write_authserver_config(config, generation, manifest)
    log = log_path.open("wb")
    process = None
    try:
        process = subprocess.Popen([str(authserver), "-c", str(config)], stdout=log, stderr=subprocess.STDOUT)
        generation["authserver_process"] = {
            "pid": process.pid,
            "start_time": pid_start_time(process.pid),
            "binary": str(authserver),
            "config": str(config),
        }
        save_manifest(manifest_path, manifest)
        wait_for_port(generation["auth_port"], process)

        # Authserver deliberately marks every realm offline on startup. This row is owned by this run.
        mysql(manifest, generation, f"UPDATE `realmlist` SET `flag`=0 WHERE `id`={REALM_ID};",
              generation["auth_schema"])
        time.sleep(1.5)

        rejected = unsupported_build(generation["auth_port"])
        null_key = mysql(manifest, generation,
                         f"SELECT IF(`session_key` IS NULL,'NULL','SET') FROM `account` WHERE `id`={ACCOUNT_ID};",
                         generation["auth_schema"])
        if null_key != "NULL":
            raise RuntimeError("unsupported-build attempt changed the session key")
        peer, session_key = authenticate(generation["auth_port"])
        database_key = mysql(manifest, generation,
                             f"SELECT HEX(`session_key`) FROM `account` WHERE `id`={ACCOUNT_ID};",
                             generation["auth_schema"])
        if database_key.upper() != session_key.hex().upper():
            raise RuntimeError("authserver peer K and persisted database K differ")
        realm_build = mysql(manifest, generation,
                            f"SELECT `gamebuild`,`port` FROM `realmlist` WHERE `id`={REALM_ID};",
                            generation["auth_schema"])
        if realm_build != f"15595\t{generation['world_port']}":
            raise RuntimeError(f"test realm identity changed: {realm_build}")
        peer["realm"]["address"] = "127.0.0.1:<world-port>"
    finally:
        if process is not None:
            stop_authserver(generation, process)
        log.close()
        save_manifest(manifest_path, manifest)

    environment = os.environ.copy()
    common = (f"127.0.0.1;{generation['mysql_port']};{manifest['mysql_user']};"
              f"{manifest['mysql_password']};")
    environment.update({
        "AC_PLAN6_LOGIN_DATABASE_INFO": common + generation["auth_schema"],
        "AC_PLAN6_CHARACTER_DATABASE_INFO": common + generation["character_schema"],
        "AC_PLAN6_ACCOUNT": ACCOUNT,
        "AC_PLAN6_ACCOUNT_ID": str(ACCOUNT_ID),
        "AC_PLAN6_REALM_ID": str(REALM_ID),
        "AC_PLAN6_SESSION_KEY_HEX": session_key.hex(),
    })
    test = subprocess.run([str(unit_tests), "--gtest_filter=WorldAuthenticationHandoffTest.UsesPersistedAuthserverKey"],
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=environment, timeout=120)
    output = test.stdout.decode(errors="replace")
    (generation_dir / "world-test.log").write_text(output)
    if test.returncode:
        raise RuntimeError(
            f"world handoff fixture exited with {test.returncode}; see {generation_dir / 'world-test.log'}"
        )
    lines = [line.removeprefix("PLAN6_WORLD_TRANSCRIPT ") for line in output.splitlines()
             if line.startswith("PLAN6_WORLD_TRANSCRIPT ")]
    if len(lines) != 1:
        raise RuntimeError("world fixture did not emit exactly one PLAN6_WORLD_TRANSCRIPT line")

    safety = {
        "client_sha256": sha256(Path(manifest["client"])) if Path(manifest["client"]).is_file() else None,
        "client_tree": tree_metadata(Path(manifest["client"]).parent),
        "bottle_tree": tree_metadata(Path(manifest["bottle"])),
    }
    if safety != {key: manifest["baseline"][key] for key in safety}:
        raise RuntimeError("personal client or Bottle metadata changed during Plan 6")
    transcript = {
        "build": 15595,
        "version": [4, 3, 4],
        "unsupported": rejected,
        "auth": peer,
        "peer_k_equals_database_k": True,
        "database_k_equals_world_query_k": True,
        "realm_database": {"build": 15595, "endpoint": "127.0.0.1:<world-port>"},
        "world": lines[0],
    }
    generation["evidence"] = transcript
    generation["state"] = "passed"
    save_manifest(manifest_path, manifest)
    print(json.dumps(transcript, sort_keys=True))


def inspect_run(args: argparse.Namespace) -> None:
    manifest = load_manifest(args.manifest.resolve())
    generation = active_generation(manifest)
    if generation["state"] != "reset":
        assert_owned(manifest, generation)
        build = mysql(manifest, generation,
                      "SELECT CONCAT(`majorVersion`,'.',`minorVersion`,'.',`bugfixVersion`) "
                      "FROM `build_info` WHERE `build`=15595;",
                      generation["auth_schema"])
        if build != "4.3.4":
            raise RuntimeError("owned database no longer reports build 15595 as 4.3.4")
    if args.compare_last_two:
        passed = [item for item in manifest["generations"] if item["evidence"] is not None]
        comparable = []
        for item in passed[-2:]:
            evidence = json.loads(json.dumps(item["evidence"]))
            evidence["auth"]["realm"]["address"] = "127.0.0.1:<world-port>"
            comparable.append(evidence)
        if len(comparable) < 2 or comparable[0] != comparable[1]:
            raise RuntimeError("the last two fresh-volume logical transcripts differ")
    print(json.dumps({"generation": generation["number"], "state": generation["state"],
                      "evidence": generation["evidence"]}, indent=2, sort_keys=True))


def reset(args: argparse.Namespace) -> None:
    manifest_path = args.manifest.resolve()
    manifest = load_manifest(manifest_path)
    generation = active_generation(manifest)
    if generation["state"] == "reset":
        print(f"generation {generation['number']} is already reset")
        return
    stop_authserver(generation)
    container = run_command(["docker", "inspect", generation["container"]], check=False)
    if container.returncode == 0:
        details = assert_owned(manifest, generation)
        run_command(["docker", "rm", "-f", details["Name"].removeprefix("/")])
    volume_result = run_command(["docker", "volume", "inspect", generation["volume"]], check=False)
    if volume_result.returncode == 0:
        volume = json.loads(volume_result.stdout)[0]
        labels = volume.get("Labels") or {}
        if labels.get("org.azerothcore.plan") != "6" or labels.get("org.azerothcore.run_id") != manifest["run_id"] \
                or labels.get("org.azerothcore.generation") != str(generation["number"]):
            raise RuntimeError("refusing to remove a volume whose ownership labels changed")
        run_command(["docker", "volume", "rm", generation["volume"]])
    generation["state"] = "reset"
    save_manifest(manifest_path, manifest)
    current = docker_inventory()
    if current != manifest["baseline"]["docker"]:
        raise RuntimeError("Docker inventory does not match the pre-Plan-6 baseline after reset")
    print(f"reset generation {generation['number']}; evidence remains in {manifest_path.parent}")


def self_check() -> None:
    salt = bytes(range(1, 33))
    verifier = srp_registration(ACCOUNT, PASSWORD, salt)
    assert len(verifier) == 32
    server_b = bytes.fromhex("7f" + "00" * 31)
    proof = calculate_srp(ACCOUNT, PASSWORD, server_b, salt)
    assert {key: len(value) for key, value in proof.items()} == {"A": 32, "M1": 20, "K": 40, "M2": 20}
    assert len(challenge_packet(15595, ACCOUNT)) == 34 + len(ACCOUNT)
    assert 3 + 32 + 1 + 1 + 1 + 32 + 32 + 16 + 1 == 119
    assert 1 + 32 + 20 + 20 + 1 + 1 == 75
    assert 1 + 1 + 20 + 4 + 4 + 2 == 32
    try:
        parse_realm_list(b"\0" * 5, REALM_ID)
    except RuntimeError:
        pass
    else:
        raise AssertionError("truncated realm list was accepted")
    try:
        require_external_manifest(REPO_ROOT / "manifest.json")
    except RuntimeError:
        pass
    else:
        raise AssertionError("inside-worktree manifest was accepted")
    print("Plan 6 runner self-check passed")


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    subcommands = result.add_subparsers(dest="command", required=True)
    subcommands.add_parser("self-check")
    prepare_parser = subcommands.add_parser("prepare")
    prepare_parser.add_argument("--manifest", type=Path, required=True)
    prepare_parser.add_argument("--migration", type=Path, required=True)
    prepare_parser.add_argument("--client", type=Path, default=DEFAULT_CLIENT)
    prepare_parser.add_argument("--bottle", type=Path, default=DEFAULT_BOTTLE)
    run_parser = subcommands.add_parser("run")
    run_parser.add_argument("--manifest", type=Path, required=True)
    run_parser.add_argument("--authserver", type=Path, required=True)
    run_parser.add_argument("--unit-tests", type=Path, required=True)
    inspect_parser = subcommands.add_parser("inspect")
    inspect_parser.add_argument("--manifest", type=Path, required=True)
    inspect_parser.add_argument("--compare-last-two", action="store_true")
    reset_parser = subcommands.add_parser("reset")
    reset_parser.add_argument("--manifest", type=Path, required=True)
    return result


def main() -> int:
    args = parser().parse_args()
    actions = {"prepare": prepare, "run": execute, "inspect": inspect_run, "reset": reset}
    try:
        if args.command == "self-check":
            self_check()
        else:
            actions[args.command](args)
        return 0
    except (OSError, RuntimeError, subprocess.SubprocessError, AssertionError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
