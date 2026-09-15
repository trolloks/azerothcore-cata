#!/usr/bin/env python3
"""One-shot, non-GUI acceptance check for issues #52/#57/#58/#59/#60.

Boots a disposable, run-owned MySQL container plus a real authserver and worldserver,
authenticates a synthetic account over the actual SRP6 + world-socket wire protocol, sends a
real CMSG_CHAR_CREATE packet for a Human Warrior, and inspects the resulting
characters/character_inventory/character_skills/character_action rows against the native
reference values already recorded in fixtures/plan22-starting-data-audit.json. This exercises
Player::Create's real loading path without needing the graphical client or wine. It also covers
three negative cases from #52's acceptance scope: duplicate name, invalid race/class combination,
and a second account attempting to delete the first account's character (ownership).

Every Docker resource this script creates is named with its own run id and removed in a
finally block; nothing here touches an existing database.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import importlib.util
import json
import os
from pathlib import Path
import shutil
import signal
import socket
import struct
import subprocess
import sys
import time
import uuid

REPO_ROOT = Path(__file__).resolve().parents[2]
MYSQL_IMAGE = "mysql:8.4"
CLIENT_BUILD = 15595
CHAR_NAME = "Auditwarr"

CMSG_AUTH_SESSION = 0x0449
SMSG_AUTH_CHALLENGE = 0x4542
SMSG_AUTH_RESPONSE = 0x5DB6
CMSG_CHAR_CREATE = 0x4A36
SMSG_CHAR_CREATE = 0x2D05
CMSG_CHAR_DELETE = 0x6425
SMSG_CHAR_DELETE = 0x003C
CHAR_CREATE_SUCCESS = 0x2F
CHAR_CREATE_ERROR = 0x30
CHAR_CREATE_NAME_IN_USE = 0x32
AUTH_OK = 0x0C

SERVER_CONNECTION_INITIALIZE = b"WORLD OF WARCRAFT CONNECTION - SERVER TO CLIENT"
CLIENT_CONNECTION_INITIALIZE = b"WORLD OF WARCRAFT CONNECTION - CLIENT TO SERVER"

DEFAULT_DATA_ROOT = Path("/mnt/f79365ff-6a68-45da-925e-b9ddc6d5da6c/Fun/TrinityCore/TrinityCore/data")
DEFAULT_DBC_ROOT = REPO_ROOT / "var/extractors/dbc-out3/dbc"


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


plan6 = load_module("plan6_authentication_handoff", Path(__file__).with_name("run_authentication_handoff.py"))
plan7 = load_module("plan7_real_client_authentication", Path(__file__).with_name("run_real_client_authentication.py"))

# plan6.authenticate() is reused as-is (SRP6 + realm-list parsing), so this script must
# authenticate as the same synthetic account/realm that module hardcodes internally.
ACCOUNT = plan6.ACCOUNT
PASSWORD = plan6.PASSWORD
ACCOUNT_ID = plan6.ACCOUNT_ID
REALM_ID = plan6.REALM_ID


def run(args: list[str], *, input_bytes: bytes | None = None, check: bool = True,
        timeout: int = 120) -> subprocess.CompletedProcess[bytes]:
    result = subprocess.run(args, input=input_bytes, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             check=False, timeout=timeout)
    if check and result.returncode:
        detail = result.stderr.decode(errors="replace").strip() or result.stdout.decode(errors="replace").strip()
        raise RuntimeError(f"{args[0]} failed with exit {result.returncode}: {detail}")
    return result


class RC4:
    def __init__(self, key: bytes) -> None:
        state = list(range(256))
        j = 0
        for i in range(256):
            j = (j + state[i] + key[i % len(key)]) % 256
            state[i], state[j] = state[j], state[i]
        self._state = state
        self._i = 0
        self._j = 0

    def crypt(self, data: bytes) -> bytes:
        state = self._state
        i, j = self._i, self._j
        out = bytearray(len(data))
        for index, byte in enumerate(data):
            i = (i + 1) % 256
            j = (j + state[i]) % 256
            state[i], state[j] = state[j], state[i]
            out[index] = byte ^ state[(state[i] + state[j]) % 256]
        self._i, self._j = i, j
        return bytes(out)


class WorldCrypt:
    """Mirrors AzerothCore's AuthCrypt (src/common/Cryptography/Authentication/AuthCrypt.cpp):
    HMAC-SHA1-keyed RC4-drop1024, one stream per direction. A client mirrors the server by using
    the ServerEncryptionKey stream to decrypt and the ServerDecryptionKey stream to encrypt."""

    SERVER_ENCRYPTION_KEY = bytes(
        [0xCC, 0x98, 0xAE, 0x04, 0xE8, 0x97, 0xEA, 0xCA, 0x12, 0xDD, 0xC0, 0x93, 0x42, 0x91, 0x53, 0x57])
    SERVER_DECRYPTION_KEY = bytes(
        [0xC2, 0xB3, 0x72, 0x3C, 0xC6, 0xAE, 0xD9, 0xB5, 0x34, 0x3C, 0x53, 0xEE, 0x2F, 0x43, 0x67, 0xCE])

    def __init__(self, session_key: bytes) -> None:
        decrypt_stream_key = hmac.new(self.SERVER_ENCRYPTION_KEY, session_key, "sha1").digest()
        encrypt_stream_key = hmac.new(self.SERVER_DECRYPTION_KEY, session_key, "sha1").digest()
        self._decrypt = RC4(decrypt_stream_key)
        self._encrypt = RC4(encrypt_stream_key)
        self._decrypt.crypt(bytes(1024))
        self._encrypt.crypt(bytes(1024))

    def decrypt_incoming_header(self, data: bytes) -> bytes:
        return self._decrypt.crypt(data)

    def encrypt_outgoing_header(self, data: bytes) -> bytes:
        return self._encrypt.crypt(data)


class BitReader:
    """Mirrors ByteBuffer::ReadBit() (src/server/shared/Packets/ByteBuffer.h): MSB-first bits,
    one fresh byte pulled from the stream every 8 reads."""

    def __init__(self, data: bytes) -> None:
        self._data = data
        self._pos = 0
        self._bitpos = 8
        self._current = 0

    def read_bit(self) -> bool:
        if self._bitpos >= 8:
            self._current = self._data[self._pos]
            self._pos += 1
            self._bitpos = 0
        self._bitpos += 1
        return bool((self._current >> (8 - self._bitpos)) & 1)


class BitPacker:
    def __init__(self) -> None:
        self._bitpos = 8
        self._current = 0
        self.out = bytearray()

    def write_bit(self, bit: int) -> None:
        self._bitpos -= 1
        if bit:
            self._current |= 1 << self._bitpos
        if self._bitpos == 0:
            self.out.append(self._current)
            self._current = 0
            self._bitpos = 8

    def write_bits(self, value: int, bits: int) -> None:
        for i in range(bits - 1, -1, -1):
            self.write_bit((value >> i) & 1)

    def flush(self) -> None:
        if self._bitpos != 8:
            self.out.append(self._current)
            self._current = 0
            self._bitpos = 8


def build_auth_session_body(account: str, digest: bytes, local_challenge: bytes, realm_id: int) -> bytes:
    # Field order mirrors WorldPackets::Auth::AuthSession::Read() in
    # src/server/game/Server/Packets/AuthenticationPackets.cpp exactly, including the scrambled
    # digest-byte interleaving. AC never validates LoginServerID/BattlegroupID/LoginServerType/
    # DosResponse/BuildType/RegionID, so those are sent as zero.
    body = struct.pack("<i", 0)  # LoginServerID
    body += struct.pack("<I", 0)  # BattlegroupID
    body += struct.pack("<b", 0)  # LoginServerType
    body += bytes([digest[10], digest[18], digest[12], digest[5]])
    body += struct.pack("<Q", 0)  # DosResponse
    body += bytes([digest[15], digest[9], digest[19], digest[4], digest[7], digest[16], digest[3]])
    body += struct.pack("<H", CLIENT_BUILD)
    body += bytes([digest[8]])
    body += struct.pack("<I", realm_id)
    body += struct.pack("<b", 0)  # BuildType
    body += bytes([digest[17], digest[6], digest[0], digest[1], digest[11]])
    body += local_challenge
    body += bytes([digest[2]])
    body += struct.pack("<I", 0)  # RegionID
    body += bytes([digest[14], digest[13]])
    body += struct.pack("<I", 0)  # addonInfoSize (no addons)
    account_bytes = account.encode()
    packer = BitPacker()
    packer.write_bit(0)  # UseIPv6
    packer.write_bits(len(account_bytes), 12)
    packer.flush()
    body += bytes(packer.out)
    body += account_bytes
    return body


def send_client_frame(sock: socket.socket, size_only_prefix: bytes) -> None:
    sock.sendall(size_only_prefix)


def _open_world_session(auth_port: int, world_port: int, sock: socket.socket):
    """Authenticates the shared synthetic account over one world-socket connection and returns
    (send_client_packet, recv_server_packet) bound to it."""
    peer, session_key = plan6.authenticate(auth_port)

    sock.settimeout(10)
    header = plan6.recv_exact(sock, 2)
    size = struct.unpack(">H", header)[0]
    greeting = plan6.recv_exact(sock, size)
    if greeting != SERVER_CONNECTION_INITIALIZE:
        raise RuntimeError(f"unexpected world greeting: {greeting!r}")
    hello = CLIENT_CONNECTION_INITIALIZE
    sock.sendall(struct.pack(">H", len(hello)) + hello)

    header = plan6.recv_exact(sock, 4)
    size, opcode = struct.unpack(">H", header[0:2])[0], struct.unpack("<H", header[2:4])[0]
    if opcode != SMSG_AUTH_CHALLENGE:
        raise RuntimeError(f"expected SMSG_AUTH_CHALLENGE, got opcode 0x{opcode:04x}")
    challenge_body = plan6.recv_exact(sock, size - 2)
    auth_seed = challenge_body[32:36]

    local_challenge = bytes(range(4))
    digest = hashlib.sha1(plan6.ACCOUNT.encode() + bytes(4) + local_challenge + auth_seed + session_key).digest()
    auth_body = build_auth_session_body(plan6.ACCOUNT, digest, local_challenge, REALM_ID)
    auth_header = struct.pack(">H", 4 + len(auth_body)) + struct.pack("<I", CMSG_AUTH_SESSION)
    sock.sendall(auth_header + auth_body)

    crypt = WorldCrypt(session_key)

    def recv_server_packet() -> tuple[int, bytes]:
        raw_header = plan6.recv_exact(sock, 4)
        raw_header = crypt.decrypt_incoming_header(raw_header)
        packet_size, packet_opcode = struct.unpack(">H", raw_header[0:2])[0], struct.unpack(
            "<H", raw_header[2:4])[0]
        packet_body = plan6.recv_exact(sock, packet_size - 2)
        return packet_opcode, packet_body

    def send_client_packet(opcode: int, body: bytes) -> None:
        header = struct.pack(">H", 4 + len(body)) + struct.pack("<I", opcode)
        header = crypt.encrypt_outgoing_header(header)
        sock.sendall(header + body)

    response_opcode, response_body = recv_server_packet()
    if response_opcode != SMSG_AUTH_RESPONSE:
        raise RuntimeError(f"expected SMSG_AUTH_RESPONSE, got opcode 0x{response_opcode:04x}")
    # Mirrors WorldPackets::Auth::AuthResponse::Write() (AuthenticationPackets.cpp): a
    # sequential bit stream (WaitInfo present, then HasFCM only if WaitInfo present, then
    # SuccessInfo present) flushed to one byte, since WorldSessionMgr::AddSession_ always
    # calls SendAuthResponse(AUTH_OK, /*shortForm=*/false, queuePos) on success.
    reader = BitReader(response_body)
    has_wait_info = reader.read_bit()
    reader.read_bit() if has_wait_info else None  # HasFCM, unused here
    has_success_info = reader.read_bit()
    offset = 1
    if has_success_info:
        offset += 15
    result = response_body[offset]
    if result != AUTH_OK:
        raise RuntimeError(f"world auth response was 0x{result:02x}, expected AUTH_OK")

    return send_client_packet, recv_server_packet


def _char_create_body(name: str, race: int, cls: int) -> bytes:
    return name.encode() + b"\x00" + bytes([race, cls, 0, 0, 0, 0, 0, 0, 0])


def _attempt_char_create(auth_port: int, world_port: int, name: str, race: int, cls: int) -> int:
    """Sends one CMSG_CHAR_CREATE over its own fresh session and returns the response code."""
    with socket.create_connection(("127.0.0.1", world_port), timeout=10) as sock:
        send_client_packet, recv_server_packet = _open_world_session(auth_port, world_port, sock)
        send_client_packet(CMSG_CHAR_CREATE, _char_create_body(name, race, cls))
        # See the matching comment in create_character(): Player::Create()'s Init*ForLevel()
        # helpers legitimately emit ordinary gameplay packets before SMSG_CHAR_CREATE.
        for _ in range(500):
            opcode, body = recv_server_packet()
            if opcode == SMSG_CHAR_CREATE:
                return body[0]
        raise RuntimeError(f"expected SMSG_CHAR_CREATE, got opcode 0x{opcode:04x}")


SECOND_ACCOUNT_ID = 900001
# Real WoW clients always uppercase the account name and password before the SRP handshake, and
# AC's login flow assumes that convention (see AUDITTWO in the log for a lowercase attempt against
# an uppercase-keyed lookup); keep both uppercase like the primary ACCOUNT/PASSWORD constants.
SECOND_ACCOUNT = "AUDITTWO"
SECOND_PASSWORD = "AUDITTWOPASS"


def _attempt_char_delete_as(auth_port: int, world_port: int, account: str, password: str,
                             account_id: int, guid: int) -> bytes | None:
    """Authenticates as a different account than the one create_character() used and sends
    CMSG_CHAR_DELETE for someone else's guid. Returns the response body if the server sent one
    (SMSG_CHAR_DELETE), or None if the connection produced no response within the timeout -
    which is what HandleCharDeleteOpcode's ownership check ("accountId != initAccountId") does:
    it returns without calling SendCharDelete at all, exactly like the earlier
    still-connected-elsewhere guard right above it."""
    saved_account, saved_password = plan6.ACCOUNT, plan6.PASSWORD
    plan6.ACCOUNT, plan6.PASSWORD = account, password
    try:
        with socket.create_connection(("127.0.0.1", world_port), timeout=10) as sock:
            send_client_packet, recv_server_packet = _open_world_session(auth_port, world_port, sock)
            send_client_packet(CMSG_CHAR_DELETE, struct.pack("<Q", guid))
            # Same tolerance as _attempt_char_create(): an authenticated session receives ordinary
            # gameplay/session packets unrelated to the request in flight. The ownership guard emits
            # nothing at all, so "no response" is proven by draining those for a few seconds without
            # ever seeing SMSG_CHAR_DELETE, not by the first packet's opcode.
            sock.settimeout(3)
            try:
                for _ in range(500):
                    opcode, body = recv_server_packet()
                    if opcode == SMSG_CHAR_DELETE:
                        return body
            except (socket.timeout, OSError):
                return None
            return None
    finally:
        plan6.ACCOUNT, plan6.PASSWORD = saved_account, saved_password


def run_negative_cases(auth_port: int, world_port: int, container: str, root_password: str,
                        characters_schema: str, auth_schema: str, created_guid: int) -> bool:
    """Covers the negative cases from #52's acceptance scope that this deterministic
    wire-protocol harness can exercise without a real client."""
    ok = True

    duplicate_result = _attempt_char_create(auth_port, world_port, CHAR_NAME, 1, 1)
    duplicate_ok = duplicate_result == CHAR_CREATE_NAME_IN_USE
    print(f"negative case, duplicate name: response=0x{duplicate_result:02x} "
          f"expected=0x{CHAR_CREATE_NAME_IN_USE:02x} match={duplicate_ok}")
    ok = ok and duplicate_ok

    # Human (race 1) cannot be Shaman (class 7). HandleCharCreateOpcode has no dedicated
    # "restricted combo" response: absent playercreateinfo for the pair fails Player::Create()
    # itself, which the handler reports as CHAR_CREATE_ERROR (see CharacterHandler.cpp's
    # "Player not create (race/class/etc problem?)" branch).
    invalid_combo_result = _attempt_char_create(auth_port, world_port, "Auditbad", 1, 7)
    invalid_combo_ok = invalid_combo_result == CHAR_CREATE_ERROR
    print(f"negative case, invalid race/class combo: response=0x{invalid_combo_result:02x} "
          f"expected=0x{CHAR_CREATE_ERROR:02x} match={invalid_combo_ok}")
    ok = ok and invalid_combo_ok

    salt = bytes(range(33, 65))
    verifier = plan6.srp_registration(SECOND_ACCOUNT, SECOND_PASSWORD, salt)
    mysql(container, root_password,
          "INSERT INTO `account` (`id`,`username`,`salt`,`verifier`,`email`,`reg_mail`,`expansion`,`Flags`) "
          f"VALUES ({SECOND_ACCOUNT_ID},'{SECOND_ACCOUNT}',UNHEX('{salt.hex()}'),UNHEX('{verifier.hex()}'),"
          "'charcreate-2@example.invalid','charcreate-2@example.invalid',3,0);"
          f"INSERT INTO `realmcharacters` (`realmid`,`acctid`,`numchars`) VALUES ({REALM_ID},{SECOND_ACCOUNT_ID},0);",
          auth_schema)

    ownership_response = _attempt_char_delete_as(auth_port, world_port, SECOND_ACCOUNT, SECOND_PASSWORD,
                                                  SECOND_ACCOUNT_ID, created_guid)
    ownership_no_response = ownership_response is None
    print(f"negative case, delete another account's character: response="
          f"{'none' if ownership_response is None else ownership_response.hex()} expected=none "
          f"match={ownership_no_response}")
    ok = ok and ownership_no_response

    survives_row = mysql(container, root_password,
                          f"SELECT `guid` FROM `characters` WHERE `guid`={created_guid};", characters_schema)
    character_survived = bool(survives_row)
    print(f"negative case, character row survives ownership-violating delete attempt: "
          f"{character_survived}")
    ok = ok and character_survived

    return ok


def create_character(auth_port: int, world_port: int) -> dict:
    with socket.create_connection(("127.0.0.1", world_port), timeout=10) as sock:
        send_client_packet, recv_server_packet = _open_world_session(auth_port, world_port, sock)

        send_client_packet(CMSG_CHAR_CREATE, _char_create_body(CHAR_NAME, 1, 1))

        # Player::Create() runs several Init*ForLevel() helpers (talents, power, proficiencies,
        # criteria) that unconditionally push their normal in-game update packets to the session
        # if one is attached, since their "player is loading" guards only suppress the resend
        # done later by SendInitialPacketsBeforeAddToMap - not character creation. This is
        # pre-existing AC behavior (present since the WotLK "First Commit"), harmless because a
        # real client on the character-creation screen ignores everything except
        # SMSG_CHAR_CREATE; skip up to a bounded number of them here for the same reason.
        for _ in range(500):
            create_opcode, create_body = recv_server_packet()
            if create_opcode == SMSG_CHAR_CREATE:
                break
        else:
            raise RuntimeError(f"expected SMSG_CHAR_CREATE, got opcode 0x{create_opcode:04x}")
        if create_body[0] != CHAR_CREATE_SUCCESS:
            raise RuntimeError(f"character creation failed with response code 0x{create_body[0]:02x}")

    return {}


def mysql(container: str, root_password: str, sql: str | bytes, schema: str | None = None,
          timeout: int = 300) -> str:
    command = ["docker", "exec", "-i", container, "mysql", "--batch", "--skip-column-names",
               "-uroot", f"-p{root_password}"]
    if schema:
        command.extend(["-D", schema])
    payload = sql.encode() if isinstance(sql, str) else sql
    return run(command, input_bytes=payload, timeout=timeout).stdout.decode().rstrip("\n")


def wait_for_mysql(container: str, root_password: str) -> None:
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        logs = run(["docker", "logs", container], check=False)
        probe = run(["docker", "exec", container, "mysql", "--batch", "--skip-column-names", "-uroot",
                     f"-p{root_password}", "-e", "SELECT 1;"], check=False)
        if b"MySQL init process done. Ready for start up." in logs.stdout + logs.stderr \
                and probe.returncode == 0 and probe.stdout.strip() == b"1":
            return
        time.sleep(0.25)
    raise RuntimeError("owned MySQL did not become ready within 60 seconds")


def wait_for_port(port: int, process: subprocess.Popen) -> None:
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"server process exited with status {process.returncode}")
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                return
        except OSError:
            time.sleep(0.2)
    raise RuntimeError(f"server did not open port {port} within 60 seconds")


def stop_process(process: subprocess.Popen | None) -> None:
    if process is None or process.poll() is not None:
        return
    process.send_signal(signal.SIGTERM)
    try:
        process.wait(timeout=20)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=10)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worldserver", type=Path,
                         default=REPO_ROOT / "var/build-plan7/src/server/apps/worldserver")
    parser.add_argument("--authserver", type=Path,
                         default=REPO_ROOT / "var/build-plan7/src/server/apps/authserver")
    parser.add_argument("--data-root", type=Path, default=DEFAULT_DATA_ROOT)
    parser.add_argument("--server-dbc-root", type=Path, default=DEFAULT_DBC_ROOT)
    parser.add_argument("--keep-workdir", action="store_true")
    args = parser.parse_args()

    for binary in (args.worldserver, args.authserver):
        if not binary.is_file() or not os.access(binary, os.X_OK):
            raise RuntimeError(f"binary is not executable: {binary}")

    run_id = uuid.uuid4().hex[:12]
    container = f"acore-cata-charcreate-{run_id}-mysql"
    workdir = Path(f"/tmp/acore-cata-charcreate-{run_id}")
    workdir.mkdir(parents=True)
    manifest_path = workdir / "manifest.json"
    manifest_path.write_text(json.dumps({"run_id": run_id, "docker_container": container,
                                          "workdir": str(workdir)}, indent=2))
    print(f"run {run_id}: workdir {workdir}, container {container}")

    root_password = uuid.uuid4().hex
    mysql_user = "charcreate"
    mysql_password = uuid.uuid4().hex
    mysql_port = plan6.unused_port()
    auth_port = plan6.unused_port()
    world_port = plan6.unused_port()

    authserver_process: subprocess.Popen | None = None
    worldserver_process: subprocess.Popen | None = None
    try:
        plan6.require_unused(mysql_port)
        result = run([
            "docker", "run", "-d", "--name", container,
            "-e", f"MYSQL_ROOT_PASSWORD={root_password}",
            "-p", f"127.0.0.1:{mysql_port}:3306",
            # Data lives only for this run's lifetime, so put it on tmpfs and turn off durability
            # fsyncs; this is what actually dominates a bulk SQL import, not process-spawn overhead.
            "--tmpfs", "/var/lib/mysql",
            MYSQL_IMAGE, "--skip-log-bin",
            "--innodb-flush-log-at-trx-commit=0", "--innodb-doublewrite=0", "--innodb-flush-method=nosync",
        ])
        container_id = result.stdout.decode().strip()
        wait_for_mysql(container, root_password)

        auth_schema, characters_schema, world_schema = "acore_auth", "acore_characters", "acore_world"
        mysql(container, root_password,
              f"CREATE DATABASE `{auth_schema}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;"
              f"CREATE DATABASE `{characters_schema}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;"
              f"CREATE DATABASE `{world_schema}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;"
              f"CREATE USER '{mysql_user}'@'%' IDENTIFIED BY '{mysql_password}';"
              f"GRANT ALL ON `{auth_schema}`.* TO '{mysql_user}'@'%';"
              f"GRANT ALL ON `{characters_schema}`.* TO '{mysql_user}'@'%';"
              f"GRANT ALL ON `{world_schema}`.* TO '{mysql_user}'@'%';")

        # Each mysql() call is a separate `docker exec`, whose process-spawn overhead dominates
        # runtime when applied per file across ~1500 base/update .sql files; batch every schema's
        # files into one mysql client invocation instead.
        def import_dir(directory: Path, schema: str) -> None:
            files = sorted(directory.glob("*.sql"))
            if not files:
                return
            payload = b"SET autocommit=0,unique_checks=0,foreign_key_checks=0;\n" \
                + b"\n".join(path.read_bytes() for path in files) + b"\nCOMMIT;\n"
            mysql(container, root_password, payload, schema, timeout=900)

        def apply_updates(directory: Path, schema: str) -> None:
            if not directory.is_dir():
                return
            applied = set(mysql(container, root_password, "SELECT `name` FROM `updates`;", schema).splitlines())
            parts = []
            for path in sorted(directory.glob("*.sql")):
                if path.name in applied:
                    continue
                file_hash = hashlib.sha1(path.read_bytes()).hexdigest().upper()
                parts.append(path.read_bytes())
                parts.append(f"\nINSERT INTO `updates` (`name`,`hash`,`state`,`speed`) "
                              f"VALUES ('{path.name}','{file_hash}','RELEASED',0);\n".encode())
            if parts:
                mysql(container, root_password, b"".join(parts), schema, timeout=900)

        for key, schema in (("auth", auth_schema), ("characters", characters_schema), ("world", world_schema)):
            import_dir(REPO_ROOT / f"data/sql/base/db_{key}", schema)
            apply_updates(REPO_ROOT / f"data/sql/updates/db_{key}", schema)
            apply_updates(REPO_ROOT / f"data/sql/updates/pending_db_{key}", schema)

        salt = bytes(range(1, 33))
        verifier = plan6.srp_registration(ACCOUNT, PASSWORD, salt)
        mysql(container, root_password,
              "INSERT INTO `account` (`id`,`username`,`salt`,`verifier`,`email`,`reg_mail`,`expansion`,`Flags`) "
              f"VALUES ({ACCOUNT_ID},'{ACCOUNT}',UNHEX('{salt.hex()}'),UNHEX('{verifier.hex()}'),"
              "'charcreate@example.invalid','charcreate@example.invalid',3,0);"
              "INSERT INTO `realmlist` (`id`,`name`,`address`,`localAddress`,`localSubnetMask`,`port`,`icon`,"
              "`flag`,`timezone`,`allowedSecurityLevel`,`population`,`gamebuild`) "
              f"VALUES ({REALM_ID},'CharCreate Realm','127.0.0.1','127.0.0.1','255.255.255.0',"
              f"{world_port},0,0,1,0,0,{CLIENT_BUILD});"
              f"INSERT INTO `realmcharacters` (`realmid`,`acctid`,`numchars`) VALUES ({REALM_ID},{ACCOUNT_ID},0);",
              auth_schema)

        data_dir = workdir / "data"
        data_dir.mkdir()
        (data_dir / "dbc").symlink_to(args.server_dbc_root, target_is_directory=True)
        (data_dir / "maps").symlink_to(args.data_root / "maps", target_is_directory=True)

        common_db = f"127.0.0.1;{mysql_port};{mysql_user};{mysql_password};"
        auth_replacements = {
            "RealmServerPort": str(auth_port),
            "BindIP": '"127.0.0.1"',
            "LogsDir": f'"{workdir / "logs"}"',
            "PidFile": f'"{workdir / "authserver.pid"}"',
            "RealmsStateUpdateDelay": "1",
            "LoginDatabaseInfo": f'"{common_db}{auth_schema}"',
            "Updates.EnableDatabases": "0",
            "Updates.AutoSetup": "0",
            "StrictVersionCheck": "0",
            "SourceDirectory": f'"{REPO_ROOT}"',
        }
        world_replacements = {
            "RealmID": str(REALM_ID),
            "WorldServerPort": str(world_port),
            "BindIP": '"127.0.0.1"',
            "LoginDatabaseInfo": f'"{common_db}{auth_schema}"',
            "WorldDatabaseInfo": f'"{common_db}{world_schema}"',
            "CharacterDatabaseInfo": f'"{common_db}{characters_schema}"',
            "DataDir": f'"{data_dir}"',
            "LogsDir": f'"{workdir / "logs"}"',
            "PidFile": f'"{workdir / "worldserver.pid"}"',
            "Console.Enable": "0",
            "Updates.EnableDatabases": "0",
            "Updates.AutoSetup": "0",
            "Expansion": "3",
            "MoveMaps.Enable": "0",
            "vmap.enableLOS": "0",
            "vmap.enableHeight": "0",
            "vmap.enableIndoorCheck": "0",
            "Warden.Enabled": "0",
            "Ra.Enable": "0",
            "SOAP.Enabled": "0",
            "Cluster.Enabled": "0",
        }
        (workdir / "logs").mkdir()
        auth_conf = workdir / "authserver.conf"
        world_conf = workdir / "worldserver.conf"
        auth_conf.write_text(plan7.replace_config(
            (REPO_ROOT / "src/server/apps/authserver/authserver.conf.dist").read_text(), auth_replacements))
        world_conf.write_text(plan7.replace_config(
            (REPO_ROOT / "src/server/apps/worldserver/worldserver.conf.dist").read_text(), world_replacements))

        authserver_log = (workdir / "authserver.log").open("wb")
        authserver_process = subprocess.Popen([str(args.authserver), "-c", str(auth_conf)],
                                               stdout=authserver_log, stderr=subprocess.STDOUT)
        wait_for_port(auth_port, authserver_process)
        time.sleep(1.5)  # authserver marks realms offline at startup before the periodic update flips them on

        worldserver_log = (workdir / "worldserver.log").open("wb")
        worldserver_process = subprocess.Popen([str(args.worldserver), "-c", str(world_conf)],
                                                stdout=worldserver_log, stderr=subprocess.STDOUT)
        wait_for_port(world_port, worldserver_process)
        time.sleep(3)  # let worldserver finish loading DBC/world state before the socket accepts real work

        create_character(auth_port, world_port)

        char_row = mysql(container, root_password,
                          "SELECT `guid`,`race`,`class`,`gender`,`map`,`position_x`,`position_y`,`position_z`,"
                          "`orientation` FROM `characters` WHERE `account`="
                          f"{ACCOUNT_ID} AND `name`='{CHAR_NAME}';",
                          characters_schema)
        if not char_row:
            raise RuntimeError("no characters row was created")
        guid, race, cls, gender, map_id, pos_x, pos_y, pos_z, orientation = char_row.split("\t")
        print(f"characters row: guid={guid} race={race} class={cls} gender={gender} map={map_id} "
              f"pos=({pos_x},{pos_y},{pos_z}) orientation={orientation}")

        expected_spawn = [1, 1, 0, 9, -8914.57, -133.909, 80.5378, 5.13806]
        actual_spawn = [int(race), int(cls), int(map_id), None, float(pos_x), float(pos_y), float(pos_z),
                        float(orientation)]
        spawn_ok = (actual_spawn[0] == expected_spawn[0] and actual_spawn[1] == expected_spawn[1]
                    and actual_spawn[2] == expected_spawn[2]
                    and all(abs(actual_spawn[i] - expected_spawn[i]) < 0.001 for i in (4, 5, 6, 7)))
        print(f"spawn matches reference (map/position/orientation, zone unverified since it is not persisted): "
              f"{spawn_ok}")

        items_row = mysql(container, root_password,
                           "SELECT `ii`.`itemEntry` FROM `character_inventory` `ci` "
                           "JOIN `item_instance` `ii` ON `ci`.`item`=`ii`.`guid` "
                           f"WHERE `ci`.`guid`={guid} ORDER BY `ii`.`itemEntry`;",
                           characters_schema)
        actual_items = sorted(int(value) for value in items_row.splitlines() if value)
        expected_items = sorted([58231, 39, 40, 49778, 6948])
        print(f"starting items: expected={expected_items} actual={actual_items} "
              f"match={actual_items == expected_items}")

        # Spell 2457 (Battle Stance) is a skill-derived grant: AC marks it PLAYERSPELL_TEMPORARY
        # and recomputes it from character_skills on every login instead of persisting a
        # character_spell row (see issue #76 for the AC-vs-TrinityCore-Cata design divergence).
        # The correct persistence check is therefore the driving skill (26, Arms), not the spell.
        skill_row = mysql(container, root_password,
                           f"SELECT `value`,`max` FROM `character_skills` WHERE `guid`={guid} AND `skill`=26;",
                           characters_schema)
        arms_skill_present = bool(skill_row)
        print(f"starting skill: Arms(26) present={arms_skill_present} "
              f"(drives recomputed initial cast spell 2457 on every login)")

        actions_row = mysql(container, root_password,
                             f"SELECT `button`,`action`,`type` FROM `character_action` WHERE `guid`={guid} "
                             "ORDER BY `button`;",
                             characters_schema)
        actual_actions = [tuple(int(value) for value in line.split("\t")) for line in actions_row.splitlines()
                           if line]
        expected_actions = sorted((row[2], row[3], row[4]) for row in [
            [1, 1, 72, 88163, 0], [1, 1, 73, 88161, 0], [1, 1, 81, 59752, 0],
            [1, 1, 84, 6603, 0], [1, 1, 96, 6603, 0], [1, 1, 108, 6603, 0],
        ])
        print(f"action bars: expected={expected_actions} actual={sorted(actual_actions)} "
              f"match={sorted(actual_actions) == expected_actions}")

        base_pass = spawn_ok and actual_items == expected_items and arms_skill_present \
            and sorted(actual_actions) == expected_actions

        negative_pass = run_negative_cases(auth_port, world_port, container, root_password,
                                            characters_schema, auth_schema, int(guid))

        all_pass = base_pass and negative_pass
        print(f"OVERALL: {'PASS' if all_pass else 'FAIL'}")
        return 0 if all_pass else 1
    finally:
        stop_process(worldserver_process)
        stop_process(authserver_process)
        run(["docker", "rm", "-f", container], check=False)
        if not args.keep_workdir:
            shutil.rmtree(workdir, ignore_errors=True)
        print(f"cleaned up docker container {container}")


if __name__ == "__main__":
    raise SystemExit(main())
