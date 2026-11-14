#!/usr/bin/env python3
"""ACE2 protocol golden vector generator.

Single source of truth: the local reference repository copies under
``/storage/emulated/0/dsh/.acesolo-ci/ref/``:

  * ``extras_ace_protocol.py::crc16_mcrf4xx``            (lines 18-31)
  * ``extras_ace_protocol_ace2.py``:
      - ``_pb_varint`` / ``_pb_uint32`` / ``_pb_bool`` / ``_pb_bytes``   (152-179)
      - ``_pb_decode_varint`` / ``_pb_decode`` / ``_pb_first``           (182-220)
      - ``_encode_request_payload``                                     (285-368)
      - ``_decode_response_payload`` (GET_STATUS / GET_INFO / DISCOVER) (370-460)
      - ``_build_frame_flags`` / ``serialize_request_frame``            (599-625)
      - ``_keep_split_header`` / ``extract_responses``                  (627-712)
  * upstream: https://github.com/ducati1198/acepro-any-klipper

This script deliberately does NOT look at the Kotlin implementation: it is the
independent oracle.  It writes ``ace2_vectors.json`` next to itself, and is
fully deterministic (run it twice, get byte-identical output).

Usage:
    python3 tools/ace2_vectors.py            # regenerate ace2_vectors.json
    python3 tools/ace2_vectors.py --check    # regenerate + verify on-disk JSON
"""

from __future__ import annotations

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
VECTORS_PATH = os.path.join(HERE, "ace2_vectors.json")

# ---------------------------------------------------------------------------
# CRC: exact port of extras_ace_protocol.py::crc16_mcrf4xx
# ---------------------------------------------------------------------------


def crc16_mcrf4xx(data: bytes) -> int:
    """CRC-16/MCRF4XX (poly 0x8408 reflected, init 0xFFFF, no final XOR)."""
    crc = 0xFFFF
    for byte in data:
        mixed = (byte ^ crc) & 0xFF
        mixed ^= (mixed & 0x0F) << 4
        crc = ((mixed << 8) | (crc >> 8)) ^ (mixed >> 4) ^ (mixed << 3)
    return crc & 0xFFFF


# ---------------------------------------------------------------------------
# Protobuf helpers: exact port of extras_ace_protocol_ace2.py
# ---------------------------------------------------------------------------


def _pb_varint(value: int) -> bytes:
    result = bytearray()
    while value > 0x7F:
        result.append((value & 0x7F) | 0x80)
        value >>= 7
    result.append(value & 0x7F)
    return bytes(result)


def _pb_uint32(field: int, value: int) -> bytes:
    return _pb_varint((field << 3) | 0) + _pb_varint(value)


def _pb_bool(field: int, value: bool) -> bytes:
    return _pb_varint((field << 3) | 0) + _pb_varint(1 if value else 0)


def _pb_bytes(field: int, value: bytes) -> bytes:
    return _pb_varint((field << 3) | 2) + _pb_varint(len(value)) + value


def _pb_string(field: int, value: str) -> bytes:
    return _pb_bytes(field, value.encode())


def _pb_decode_varint(data: bytes, pos: int) -> tuple[int, int]:
    result = 0
    shift = 0
    while pos < len(data):
        byte = data[pos]
        pos += 1
        result |= (byte & 0x7F) << shift
        if not (byte & 0x80):
            return result, pos
        shift += 7
    return result, pos


def _pb_decode(data: bytes) -> dict[int, list[tuple[int, object]]]:
    fields: dict[int, list[tuple[int, object]]] = {}
    pos = 0
    while pos < len(data):
        tag, pos = _pb_decode_varint(data, pos)
        field_num, wire_type = tag >> 3, tag & 7
        if wire_type == 0:
            value, pos = _pb_decode_varint(data, pos)
        elif wire_type == 2:
            length, pos = _pb_decode_varint(data, pos)
            value = data[pos:pos + length]
            pos += length
        else:
            break
        fields.setdefault(field_num, []).append((wire_type, value))
    return fields


def _pb_first(fields: dict[int, list[tuple[int, object]]], field: int, default=0):
    return fields.get(field, [(0, default)])[0][1]


# ---------------------------------------------------------------------------
# Command catalog / request payload encoding: subset used by this project
# ---------------------------------------------------------------------------

COMMANDS_BY_NAME = {
    "DISCOVER_DEVICE": 0,
    "ASSIGN_DEVICE_ID": 1,
    "GET_STATUS": 6,
    "GET_INFO": 7,
    "FEED_OR_ROLLBACK": 8,
    "STOP_FEED_OR_ROLLBACK": 9,
    "UPDATE_SPEED": 10,
    "DRYING": 11,
    "SET_DRY_TEMP": 12,
    "GET_FILAMENT_INFO": 13,
    "GET_TEMP": 64,
    "GET_FEED_INFO": 76,
}

FLAG_RESPONSE = 0x80
FLAG_DEVICE_ID_MASK = 0x7F


def encode_request_payload(command_name: str, params: dict) -> bytes:
    """Port of _encode_request_payload for the commands this project uses."""
    if command_name in {
        "DISCOVER_DEVICE",
        "GET_INFO",
        "GET_STATUS",
        "GET_TEMP",
        "GET_KEY_STATE",
        "GET_FEED_INFO",
    }:
        return b""
    if command_name == "ASSIGN_DEVICE_ID":
        return (
            _pb_uint32(1, int(params["uid1"]))
            + _pb_uint32(2, int(params["uid2"]))
            + _pb_uint32(3, int(params["uid3"]))
            + _pb_uint32(4, int(params["device_id"]))
        )
    if command_name in {"GET_FILAMENT_INFO", "STOP_FEED_OR_ROLLBACK"}:
        return _pb_uint32(1, int(params["index"]))
    if command_name == "UPDATE_SPEED":
        return _pb_uint32(1, int(params["index"])) + _pb_uint32(2, int(params["speed"]))
    if command_name == "FEED_OR_ROLLBACK":
        return (
            _pb_uint32(1, int(params["index"]))
            + _pb_uint32(2, int(params["speed"]))
            + _pb_uint32(3, int(params["length"]))
            + _pb_uint32(4, int(params["mode"]))
        )
    if command_name == "DRYING":
        return (
            _pb_uint32(1, int(params["temp"]))
            + _pb_uint32(2, int(params["duration"]))
            + _pb_bool(3, bool(params.get("auto_roll", False)))
        )
    if command_name in {"SET_DRY_TEMP", "SET_PTC_TEMP"}:
        return _pb_uint32(1, int(params["temp"]))
    raise NotImplementedError(command_name)


def frame_flags(command_name: str, target_device_id: int = 0) -> int:
    """Port of _build_frame_flags: DISCOVER/ASSIGN are broadcast (0)."""
    if command_name in {"DISCOVER_DEVICE", "ASSIGN_DEVICE_ID"}:
        return 0
    if target_device_id <= 0 or target_device_id > FLAG_DEVICE_ID_MASK:
        raise ValueError("target_device_id must be 1..127 for addressed commands")
    return target_device_id & FLAG_DEVICE_ID_MASK


def serialize_request_frame(
    command_name: str,
    params: dict | None = None,
    request_id: int = 0,
    target_device_id: int = 0,
) -> bytes:
    """Port of serialize_request_frame (lines 612-625)."""
    params = params or {}
    payload = encode_request_payload(command_name, params)
    flags = frame_flags(command_name, target_device_id)
    inner = bytearray(
        [
            flags,
            request_id & 0xFF,
            (request_id >> 8) & 0xFF,
            COMMANDS_BY_NAME[command_name],
            len(payload),
        ]
    )
    inner.extend(payload)
    crc_value = crc16_mcrf4xx(bytes(inner))
    crc_bytes = bytes([crc_value & 0xFF, (crc_value >> 8) & 0xFF])
    return b"\xFF\xAA" + bytes(inner) + crc_bytes + b"\xFE"


# ---------------------------------------------------------------------------
# Response parsing: port of _decode_response_payload + extract_responses
# ---------------------------------------------------------------------------

RESPONSE_CODE_NAMES = {
    0: "SUCCESS",
    1: "PARAM_ERROR",
    2: "FORBIDDEN",
    3: "FAILED",
    4: "ANTICOLLISION",
    5: "NOTAG",
    6: "READFAILED",
    400: "UNSUPPORTED",
}
WORK_STATUS_BY_CODE = {0: "init", 1: "ready", 2: "busy", 3: "upgrading"}
DRY_STATUS_BY_CODE = {0: "stop", 1: "drying", 2: "drying", 3: "stop", 4: "error", 5: "error"}
DRY_STATE_DETAIL_BY_CODE = {
    0: "stop",
    1: "starting",
    2: "keeping",
    3: "stopping",
    4: "ptc_error",
    5: "ntc_error",
}
SLOT_STATUS_BY_CODE = {
    0: "ready",
    1: "feeding",
    2: "unwinding",
    3: "shifting",
    4: "shifting",
    5: "preload",
    6: "upgrading",
    129: "gear_err",
    130: "gear_err",
    131: "gear_err",
    132: "gear_err",
    133: "gear_err",
    134: "gear_err",
    135: "gear_err",
}
SLOT_STATUS_DETAIL_BY_CODE = {
    0: "ready",
    1: "feeding",
    2: "rollback",
    3: "assisting",
    4: "rollback_assisting",
    5: "preloading",
    6: "upgrading",
    129: "feed_error",
    130: "rollback_error",
    131: "assist_error",
    132: "preload_error",
    133: "stuck_error",
    134: "tangled_error",
    135: "motor_error",
}
FILAMENT_TO_RFID_STATE = {0: 0, 1: 1, 2: 2, 3: 3}

GENERIC_RESPONSE_COMMANDS = {
    "ASSIGN_DEVICE_ID",
    "FEED_OR_ROLLBACK",
    "STOP_FEED_OR_ROLLBACK",
    "UPDATE_SPEED",
    "DRYING",
    "SET_DRY_TEMP",
    "FILAMENT_IDENTIFY",
    "SET_RFID_ENABLE",
    "SET_FEED_CHECK",
}


def decode_response_payload(command_name: str, payload: bytes) -> dict:
    fields = _pb_decode(payload)
    if command_name == "DISCOVER_DEVICE":
        return {
            "uid1": _pb_first(fields, 1),
            "uid2": _pb_first(fields, 2),
            "uid3": _pb_first(fields, 3),
        }
    if command_name in GENERIC_RESPONSE_COMMANDS:
        code = _pb_first(fields, 1, 0)
        return {"code": code, "msg": RESPONSE_CODE_NAMES.get(code, str(code))}
    if command_name == "GET_INFO":
        return {
            "code": 0,
            "msg": RESPONSE_CODE_NAMES[0],
            "result": {
                "version": _pb_first(fields, 1, b"").decode(errors="ignore"),
                "boot_version": _pb_first(fields, 2, b"").decode(errors="ignore"),
                "first_request": bool(_pb_first(fields, 3, 0)),
            },
        }
    if command_name == "GET_STATUS":
        dry_status_payload = _pb_first(fields, 2, b"")
        dry_status_fields = _pb_decode(dry_status_payload) if dry_status_payload else {}
        work_state_code = _pb_first(fields, 1, 0)
        dry_state_code = _pb_first(dry_status_fields, 1, 0)
        slots = []
        for index, (_, slot_payload) in enumerate(fields.get(9, [])):
            slot_fields = _pb_decode(slot_payload) if slot_payload else {}
            slot_state = _pb_first(slot_fields, 1, 0)
            filament_state = _pb_first(slot_fields, 2, 0)
            normalized_slot_state = "empty" if filament_state == 0 else "ready"
            if filament_state != 0:
                normalized_slot_state = SLOT_STATUS_BY_CODE.get(
                    slot_state, "gear_err" if slot_state >= 129 else "ready"
                )
            slot_status_detail = (
                "empty"
                if filament_state == 0
                else SLOT_STATUS_DETAIL_BY_CODE.get(slot_state, "unknown")
            )
            slots.append(
                {
                    "index": index,
                    "status": normalized_slot_state,
                    "status_detail": slot_status_detail,
                    "status_code": slot_state,
                    "rfid": FILAMENT_TO_RFID_STATE.get(filament_state, 0),
                }
            )
        action = next(
            (slot["status"] for slot in slots if slot["status"] in ("feeding", "unwinding")),
            "none",
        )
        return {
            "code": 0,
            "msg": RESPONSE_CODE_NAMES[0],
            "result": {
                "status": WORK_STATUS_BY_CODE.get(work_state_code, "unknown"),
                "status_code": work_state_code,
                "action": action,
                "dryer_status": {
                    "status": DRY_STATUS_BY_CODE.get(dry_state_code, "unknown"),
                    "state_detail": DRY_STATE_DETAIL_BY_CODE.get(dry_state_code, "unknown"),
                    "state_code": dry_state_code,
                    "target_temp": _pb_first(dry_status_fields, 2, 0),
                    "duration": round(_pb_first(dry_status_fields, 3, 0) / 60),
                    "remain_time": _pb_first(dry_status_fields, 4, 0),
                },
                "temp": _pb_first(fields, 3, 0),
                "humidity": _pb_first(fields, 4, 0),
                "feed_assist_count": _pb_first(fields, 7, 0),
                "cont_assist_time": _pb_first(fields, 8, 0) / 1000.0,
                "slots": slots,
            },
        }
    return {"raw_fields": str(fields)}


def keep_split_header(working: bytearray) -> tuple[bytearray, int]:
    """Port of _keep_split_header: keep a trailing 0xFF (split header)."""
    if working and working[-1] == 0xFF:
        return bytearray(b"\xFF"), len(working) - 1
    return bytearray(), len(working)


def extract_responses(buffer: bytes) -> tuple[list[dict], bytes, list[str]]:
    """Port of extract_responses (lines 643-712)."""
    responses: list[dict] = []
    notices: list[str] = []
    working = bytearray(buffer)

    while True:
        if len(working) < 10:
            break

        if not (working[0] == 0xFF and working[1] == 0xAA):
            header_idx = working.find(b"\xFF\xAA")
            if header_idx == -1:
                working, dropped = keep_split_header(working)
                notices.append(f"Resync: dropped junk ({dropped} bytes)")
                break
            notices.append(f"Resync: skipping {header_idx} bytes")
            working = working[header_idx:]
            if len(working) < 10:
                break

        payload_len = working[6]
        frame_len = 2 + 1 + 2 + 1 + 1 + payload_len + 2 + 1
        if len(working) < frame_len:
            break

        terminator_idx = 2 + 1 + 2 + 1 + 1 + payload_len + 2
        if working[terminator_idx] != 0xFE:
            next_header = working.find(b"\xFF\xAA", 1)
            if next_header == -1:
                working, dropped = keep_split_header(working)
            else:
                dropped = next_header
                working = working[next_header:]
            notices.append(f"Invalid frame tail, resyncing (dropped {dropped} bytes)")
            continue

        frame = bytes(working[:frame_len])
        working = bytearray(working[frame_len:])

        flags = frame[2]
        request_id = frame[3] | (frame[4] << 8)
        command_code = frame[5]
        payload = frame[7 : 7 + payload_len]
        crc_rx = frame[7 + payload_len : 7 + payload_len + 2]
        crc_calc = bytes(
            [
                crc16_mcrf4xx(frame[2 : 7 + payload_len]) & 0xFF,
                (crc16_mcrf4xx(frame[2 : 7 + payload_len]) >> 8) & 0xFF,
            ]
        )
        if crc_rx != crc_calc:
            notices.append("Invalid CRC")
            continue

        command_name = {
            code: name for name, code in COMMANDS_BY_NAME.items()
        }.get(command_code, f"CMD_{command_code}")
        decoded = decode_response_payload(command_name, payload)
        response = {"id": request_id, "command": command_name, "flags": flags}
        device_id = flags & FLAG_DEVICE_ID_MASK
        if device_id:
            response["device_id"] = device_id
        if command_name == "DISCOVER_DEVICE":
            response["result"] = decoded
        else:
            response.update(decoded)
        responses.append(response)

    return responses, bytes(working), notices


# ---------------------------------------------------------------------------
# Vector construction
# ---------------------------------------------------------------------------


def hx(data: bytes) -> str:
    return " ".join(f"{b:02X}" for b in data)


def build_status_response_payload() -> bytes:
    """StatusResponse: field1=1, field2={1:2,2:55,3:14400,4:1320},
    field3=52, field4=38, field9 = [{1:0,2:3}, {1:1,2:3}]."""
    dry = (
        _pb_uint32(1, 2)
        + _pb_uint32(2, 55)
        + _pb_uint32(3, 14400)
        + _pb_uint32(4, 1320)
    )
    slot0 = _pb_uint32(1, 0) + _pb_uint32(2, 3)
    slot1 = _pb_uint32(1, 1) + _pb_uint32(2, 3)
    return (
        _pb_uint32(1, 1)
        + _pb_bytes(2, dry)
        + _pb_uint32(3, 52)
        + _pb_uint32(4, 38)
        + _pb_bytes(9, slot0)
        + _pb_bytes(9, slot1)
    )


def build_response_frame(payload: bytes, flags: int, request_id: int, command_code: int) -> bytes:
    inner = bytearray([flags, request_id & 0xFF, (request_id >> 8) & 0xFF, command_code, len(payload)])
    inner.extend(payload)
    c = crc16_mcrf4xx(bytes(inner))
    return b"\xFF\xAA" + bytes(inner) + bytes([c & 0xFF, c >> 8]) + b"\xFE"


def build_vectors() -> dict:
    vectors: dict = {
        "meta": {
            "generator": "tools/ace2_vectors.py",
            "oracle": "local reference repo: .acesolo-ci/ref/extras_ace_protocol*.py",
            "upstream": "https://github.com/ducati1198/acepro-any-klipper",
            "notes": [
                "All byte strings are uppercase hex pairs separated by single spaces.",
                "Request frames use target_device_id=1 except DISCOVER/ASSIGN (flags=0).",
                "The reference encoder writes default (zero) values explicitly, so a "
                "DRYING stop payload is 08 00 10 00 18 00, not empty.",
            ],
        },
        "crc": {},
        "requests": [],
        "responses": [],
        "robustness": [],
    }

    # (a) known check value + (b) edge cases
    edge_inputs = {
        "known_check_123456789": b"123456789",
        "empty": b"",
        "one_zero_byte": b"\x00",
        "one_ff_byte": b"\xff",
        "ff_aa": b"\xff\xaa",
    }
    for name, data in edge_inputs.items():
        vectors["crc"][name] = {
            "input_hex": hx(data),
            "crc": crc16_mcrf4xx(data),
        }

    # (c)-(i) request frames
    request_cases = [
        ("discover_device", "DISCOVER_DEVICE", {}, 1, 0),
        (
            "assign_device_id",
            "ASSIGN_DEVICE_ID",
            {"uid1": 0x11223344, "uid2": 0x55667788, "uid3": 0x99AABBCC, "device_id": 1},
            2,
            0,
        ),
        ("get_status", "GET_STATUS", {}, 3, 1),
        ("get_info", "GET_INFO", {}, 4, 1),
        (
            "start_drying",
            "DRYING",
            {"temp": 55, "duration": 240, "auto_roll": False},
            5,
            1,
        ),
        (
            "stop_drying",
            "DRYING",
            {"temp": 0, "duration": 0, "auto_roll": False},
            6,
            1,
        ),
        (
            "start_drying_min_boundary",
            "DRYING",
            {"temp": 30, "duration": 10, "auto_roll": False},
            7,
            1,
        ),
        (
            "start_drying_max_boundary",
            "DRYING",
            {"temp": 65, "duration": 1440, "auto_roll": False},
            8,
            1,
        ),
        (
            "start_drying_auto_roll_true",
            "DRYING",
            {"temp": 50, "duration": 480, "auto_roll": True},
            9,
            1,
        ),
        (
            "feed_or_rollback_mode0",
            "FEED_OR_ROLLBACK",
            {"index": 2, "speed": 50, "length": 200, "mode": 0},
            10,
            1,
        ),
        ("stop_feed", "STOP_FEED_OR_ROLLBACK", {"index": 2}, 11, 1),
        (
            "set_dry_temp",
            "SET_DRY_TEMP",
            {"temp": 60},
            12,
            1,
        ),
    ]
    for name, command, params, request_id, target in request_cases:
        frame = serialize_request_frame(command, params, request_id, target)
        payload = encode_request_payload(command, params)
        vectors["requests"].append(
            {
                "name": name,
                "command": command,
                "command_code": COMMANDS_BY_NAME[command],
                "params": params,
                "request_id": request_id,
                "target_device_id": target,
                "flags": frame_flags(command, target),
                "payload_hex": hx(payload),
                "frame_hex": hx(frame),
                "frame_len": len(frame),
            }
        )

    # (j) GET_STATUS response decode
    status_payload = build_status_response_payload()
    status_frame = build_response_frame(status_payload, 0x81, 3, COMMANDS_BY_NAME["GET_STATUS"])
    parsed, leftover, notices = extract_responses(status_frame)
    vectors["responses"].append(
        {
            "name": "get_status_response",
            "payload_hex": hx(status_payload),
            "frame_hex": hx(status_frame),
            "expected": {
                "response_count": len(parsed),
                "leftover_hex": hx(leftover),
                "notices": notices,
                "id": parsed[0]["id"],
                "command": parsed[0]["command"],
                "flags": parsed[0]["flags"],
                "device_id": parsed[0].get("device_id"),
                "code": parsed[0]["code"],
                "result": parsed[0]["result"],
            },
        }
    )

    # GET_INFO response decode (string fields, wire type 2)
    info_payload = _pb_string(1, "V1.1.31") + _pb_string(2, "V1.0.0") + _pb_uint32(3, 1)
    info_frame = build_response_frame(info_payload, 0x81, 4, COMMANDS_BY_NAME["GET_INFO"])
    info_parsed, info_leftover, info_notices = extract_responses(info_frame)
    vectors["responses"].append(
        {
            "name": "get_info_response",
            "payload_hex": hx(info_payload),
            "frame_hex": hx(info_frame),
            "expected": {
                "response_count": len(info_parsed),
                "leftover_hex": hx(info_leftover),
                "notices": info_notices,
                "version": info_parsed[0]["result"]["version"],
                "boot_version": info_parsed[0]["result"]["boot_version"],
                "first_request": info_parsed[0]["result"]["first_request"],
            },
        }
    )

    # DISCOVER_DEVICE response decode (UID triplet)
    discover_payload = _pb_uint32(1, 0x11223344) + _pb_uint32(2, 0x55667788) + _pb_uint32(3, 0x99AABBCC)
    discover_frame = build_response_frame(
        discover_payload, 0x80, 1, COMMANDS_BY_NAME["DISCOVER_DEVICE"]
    )
    discover_parsed, discover_leftover, discover_notices = extract_responses(discover_frame)
    vectors["responses"].append(
        {
            "name": "discover_device_response",
            "payload_hex": hx(discover_payload),
            "frame_hex": hx(discover_frame),
            "expected": {
                "response_count": len(discover_parsed),
                "leftover_hex": hx(discover_leftover),
                "notices": discover_notices,
                "id": discover_parsed[0]["id"],
                "flags": discover_parsed[0]["flags"],
                "device_id": discover_parsed[0].get("device_id", 0),
                "uid1": discover_parsed[0]["result"]["uid1"],
                "uid2": discover_parsed[0]["result"]["uid2"],
                "uid3": discover_parsed[0]["result"]["uid3"],
            },
        }
    )

    # GenericResponse decode (code=0 success, code=2 forbidden)
    for code in (0, 2):
        gen_payload = _pb_uint32(1, code)
        gen_frame = build_response_frame(gen_payload, 0x81, 5, COMMANDS_BY_NAME["DRYING"])
        gen_parsed, _, gen_notices = extract_responses(gen_frame)
        vectors["responses"].append(
            {
                "name": f"drying_generic_code_{code}",
                "payload_hex": hx(gen_payload),
                "frame_hex": hx(gen_frame),
                "expected": {
                    "response_count": len(gen_parsed),
                    "leftover_hex": "",
                    "notices": gen_notices,
                    "code": gen_parsed[0]["code"],
                    "msg": gen_parsed[0]["msg"],
                },
            }
        )

    # (k) robustness cases
    status_frame_small = status_frame
    good = status_frame_small
    # k1 normal
    k1, k1left, k1notices = extract_responses(good)
    vectors["robustness"].append(
        {
            "name": "normal_frame",
            "input_hex": hx(good),
            "expected": {
                "response_count": len(k1),
                "leftover_hex": hx(k1left),
                "notices": k1notices,
            },
        }
    )
    # k2 junk prefix
    junk = b"\x00\x12\x34\xFE"
    k2, k2left, k2notices = extract_responses(junk + good)
    vectors["robustness"].append(
        {
            "name": "junk_prefix",
            "input_hex": hx(junk + good),
            "expected": {
                "response_count": len(k2),
                "leftover_hex": hx(k2left),
                "notices": k2notices,
            },
        }
    )
    # k3 half frame -> no response, buffer retained; then rest arrives
    half = good[:9]
    rest = good[9:]
    k3a, k3aleft, k3anotices = extract_responses(half)
    k3b, k3bleft, k3bnotices = extract_responses(bytes(k3aleft) + rest)
    vectors["robustness"].append(
        {
            "name": "half_frame_then_rest",
            "input_hex": hx(half),
            "expected": {
                "response_count": len(k3a),
                "leftover_hex": hx(k3aleft),
                "notices": k3anotices,
                "after_rest_response_count": len(k3b),
                "after_rest_leftover_hex": hx(k3bleft),
            },
            "continued_input_hex": hx(rest),
        }
    )
    # k4 CRC error frame followed by valid frame
    bad = bytearray(good)
    bad[-2] ^= 0xFF  # corrupt CRC low byte
    k4, k4left, k4notices = extract_responses(bytes(bad) + good)
    vectors["robustness"].append(
        {
            "name": "crc_error_then_valid",
            "input_hex": hx(bytes(bad) + good),
            "expected": {
                "response_count": len(k4),
                "leftover_hex": hx(k4left),
                "notices": k4notices,
            },
        }
    )
    # k5 split header across reads: junk (>=10 bytes) + trailing 0xFF, then the
    # rest of the frame arriving with its leading 0xFF already consumed.
    split = b"\x00\x11\x22\x33\x44\x55\x66\x77\x88\x99\xFF"
    body = good[1:]  # header resume: 0xAA + flags + ...
    k5a, k5aleft, k5anotices = extract_responses(split)
    k5b, k5bleft, k5bnotices = extract_responses(bytes(k5aleft) + body)
    vectors["robustness"].append(
        {
            "name": "split_header_keeps_trailing_ff",
            "input_hex": hx(split),
            "expected": {
                "first_buffered_hex": hx(k5aleft),
                "response_count": len(k5a),
                "first_notices": k5anotices,
                "after_rest_response_count": len(k5b),
                "after_rest_leftover_hex": hx(k5bleft),
            },
            "continued_input_hex": hx(body),
        }
    )
    # k6 two frames in one read
    two = good + good
    k6, k6left, k6notices = extract_responses(two)
    vectors["robustness"].append(
        {
            "name": "two_frames_one_read",
            "input_hex": hx(two),
            "expected": {
                "response_count": len(k6),
                "leftover_hex": hx(k6left),
                "notices": k6notices,
                "ids": [r["id"] for r in k6],
            },
        }
    )
    # k7 response for addressed device 1 has 0x80 flag; device_id extracted
    vectors["robustness"].append(
        {
            "name": "response_flag_and_device_id",
            "input_hex": hx(good),
            "expected": {
                "response_count": 1,
                "flags": k1[0]["flags"],
                "device_id": k1[0].get("device_id"),
                "id": k1[0]["id"],
                "command": k1[0]["command"],
            },
        }
    )

    return vectors


def main(argv: list[str]) -> int:
    vectors = build_vectors()
    encoded = json.dumps(vectors, indent=2, ensure_ascii=False, sort_keys=False) + "\n"
    if "--check" in argv:
        with open(VECTORS_PATH, "r", encoding="utf-8") as fh:
            on_disk = fh.read()
        if on_disk != encoded:
            print("FAIL: ace2_vectors.json is stale; rerun tools/ace2_vectors.py")
            return 1
        print("OK: ace2_vectors.json matches the generator")
        return 0
    with open(VECTORS_PATH, "w", encoding="utf-8") as fh:
        fh.write(encoded)
    crc_known = vectors["crc"]["known_check_123456789"]["crc"]
    print(f"wrote {VECTORS_PATH}")
    print(f"CRC16/MCRF4XX(b'123456789') = 0x{crc_known:04X}")
    if crc_known != 0x6F91:
        print("FAIL: CRC check value must be 0x6F91", file=sys.stderr)
        return 1
    print(f"requests={len(vectors['requests'])} responses={len(vectors['responses'])} "
          f"robustness={len(vectors['robustness'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
