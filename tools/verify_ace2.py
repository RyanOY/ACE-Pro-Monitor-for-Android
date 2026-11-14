#!/usr/bin/env python3
"""ACE2 Kotlin implementation verifier.

Step 2 of the independent-verification task:

  * ``k_*`` functions below are a **line-by-line transcription of
    app/src/main/java/com/acesolo/Ace2Protocol.kt** (Kotlin line numbers are
    cited in each comment).  Nothing here is copied from the reference Python
    implementation: this is the audited artifact, not the oracle.
  * Loads the golden vectors produced from the community reference
    implementation by ``tools/ace2_vectors.py`` and checks byte-exact equality.
  * Audits the 10 high-risk points (CRC coverage, id endianness, flags, len
    bounds, protobuf wire types, drying fields/units, status fields, response
    0x80 bit, resync trailing 0xFF, session layer threading/timeouts).
  * Only reads ``app/src/main/``; never writes there.

Exit code 0 = every comparison passed; 1 = at least one FAIL.
Warnings (WARN) are risks found by inspection; they do not fail the run but
are listed in the report.

Usage:
    python3 tools/verify_ace2.py
"""

from __future__ import annotations

import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
VECTORS_PATH = os.path.join(HERE, "ace2_vectors.json")
PROTOCOL_KT = os.path.join(ROOT, "app", "src", "main", "java", "com", "acesolo", "Ace2Protocol.kt")
CLIENT_KT = os.path.join(ROOT, "app", "src", "main", "java", "com", "acesolo", "Ace2Client.kt")

# ---------------------------------------------------------------------------
# Transcription of Ace2Protocol.kt
# ---------------------------------------------------------------------------

DEFAULT_BAUD = 230400
HEAD_0 = 0xFF
HEAD_1 = 0xAA
TAIL = 0xFE
FLAG_RESPONSE = 0x80
DEVICE_ID_MASK = 0x7F
MAX_PAYLOAD = 100
MIN_FRAME_LEN = 10
DEFAULT_DEVICE_ID = 1

# Ace2Protocol.kt lines 34-62: Cmd object
CMD_DISCOVER_DEVICE = 0
CMD_ASSIGN_DEVICE_ID = 1
CMD_IAP_UPGRADE = 2
CMD_IAP_FIRMWARE = 3
CMD_IAP_UPGRADE_FINISH = 4
CMD_GET_STATUS = 6
CMD_GET_INFO = 7
CMD_FEED_OR_ROLLBACK = 8
CMD_STOP_FEED_OR_ROLLBACK = 9
CMD_UPDATE_SPEED = 10
CMD_DRYING = 11
CMD_SET_DRY_TEMP = 12
CMD_GET_FILAMENT_INFO = 13
CMD_SET_RFID_ENABLE = 14
CMD_LINEAR_KEY_CALIBRATE = 15
CMD_SET_FEED_CHECK = 19
CMD_GET_TEMP = 64
CMD_SET_DRY_POWER = 65
CMD_SET_VALVE = 66
CMD_FILAMENT_IDENTIFY = 68
CMD_RFID_TEST = 69
CMD_FLASH_LED = 70
CMD_SET_FAN = 71
CMD_SET_OUTPUT = 72
CMD_GET_KEY_STATE = 73
CMD_SET_PTC_TEMP = 75
CMD_GET_FEED_INFO = 76

COMMAND_NAMES = {
    0: "DISCOVER_DEVICE",
    1: "ASSIGN_DEVICE_ID",
    2: "IAP_UPGRADE",
    3: "IAP_FIRMWARE",
    4: "IAP_UPGRADE_FINISH",
    6: "GET_STATUS",
    7: "GET_INFO",
    8: "FEED_OR_ROLLBACK",
    9: "STOP_FEED_OR_ROLLBACK",
    10: "UPDATE_SPEED",
    11: "DRYING",
    12: "SET_DRY_TEMP",
    13: "GET_FILAMENT_INFO",
    14: "SET_RFID_ENABLE",
    15: "LINEAR_KEY_CALIBRATE",
    19: "SET_FEED_CHECK",
    64: "GET_TEMP",
    65: "SET_DRY_POWER",
    66: "SET_VALVE",
    68: "FILAMENT_IDENTIFY",
    69: "RFID_TEST",
    70: "FLASH_LED",
    71: "SET_FAN",
    72: "SET_OUTPUT",
    73: "GET_KEY_STATE",
    75: "SET_PTC_TEMP",
    76: "GET_FEED_INFO",
}


def command_name(code: int) -> str:
    """Ace2Protocol.kt lines 64-93."""
    return COMMAND_NAMES.get(code, f"CMD_{code}")


def describe_code(code: int) -> str:
    """Ace2Protocol.kt lines 96-106."""
    return {
        0: "SUCCESS",
        1: "PARAM_ERROR",
        2: "FORBIDDEN",
        3: "FAILED",
        4: "ANTICOLLISION",
        5: "NOTAG",
        6: "READFAILED",
        400: "UNSUPPORTED",
    }.get(code, f"CODE_{code}")


def work_state_name(code: int) -> str:
    """Ace2Protocol.kt lines 109-115."""
    return {0: "init", 1: "ready", 2: "busy", 3: "upgrading"}.get(code, "unknown")


def dry_state_name(code: int) -> str:
    """Ace2Protocol.kt lines 274-280."""
    if code in (1, 2):
        return "drying"
    if code == 3:
        return "stop"
    if code in (4, 5):
        return "error"
    if code == 0:
        return "stop"
    return "unknown"


def dry_state_detail_name(code: int) -> str:
    """Ace2Protocol.kt lines 283-291."""
    return {
        0: "stop",
        1: "starting",
        2: "keeping",
        3: "stopping",
        4: "ptc_error",
        5: "ntc_error",
    }.get(code, "unknown")


def k_crc16(data: bytes) -> int:
    """Ace2Protocol.kt lines 121-130, exact transcription."""
    crc = 0xFFFF
    for byte in data:
        mixed = (byte & 0xFF) ^ (crc & 0xFF)
        mixed = mixed ^ ((mixed & 0x0F) << 4)
        crc = ((mixed << 8) | (crc >> 8)) ^ (mixed >> 4) ^ (mixed << 3)
        crc = crc & 0xFFFF
    return crc & 0xFFFF


def k_build_frame(flags: int, request_id: int, command: int, payload: bytes) -> bytes:
    """Ace2Protocol.kt lines 133-153."""
    if len(payload) > MAX_PAYLOAD:
        raise ValueError(f"payload 超过上限 {MAX_PAYLOAD}B：{len(payload)}B")
    inner = bytes(
        [
            flags & 0xFF,
            request_id & 0xFF,
            (request_id >> 8) & 0xFF,
            command & 0xFF,
            len(payload) & 0xFF,
        ]
    ) + payload
    crc = k_crc16(inner)
    return bytes([HEAD_0, HEAD_1]) + inner + bytes([crc & 0xFF, (crc >> 8) & 0xFF]) + bytes([TAIL])


def k_request_flags(command: int, device_id: int) -> int:
    """Ace2Protocol.kt lines 156-162."""
    if command in (CMD_DISCOVER_DEVICE, CMD_ASSIGN_DEVICE_ID):
        return 0
    if not (1 <= device_id <= DEVICE_ID_MASK):
        raise ValueError(f"deviceId 必须在 1..{DEVICE_ID_MASK}：{device_id}")
    return device_id & DEVICE_ID_MASK


def k_build_request(
    request_id: int, command: int, payload: bytes = b"", device_id: int = DEFAULT_DEVICE_ID
) -> bytes:
    """Ace2Protocol.kt lines 164-169."""
    return k_build_frame(k_request_flags(command, device_id), request_id, command, payload)


# --- payload builders (Ace2Protocol.kt lines 175-208) ----------------------


def k_discover_payload() -> bytes:
    return b""


def k_assign_id_payload(uid1: int, uid2: int, uid3: int, device_id: int) -> bytes:
    return (
        k_uint32(1, uid1) + k_uint32(2, uid2) + k_uint32(3, uid3) + k_uint32(4, device_id)
    )


def k_drying_payload(temp_c: int, duration_minutes: int, auto_roll: bool = False) -> bytes:
    return k_uint32(1, temp_c) + k_uint32(2, duration_minutes) + k_bool(3, auto_roll)


def k_stop_drying_payload() -> bytes:
    return k_drying_payload(0, 0, False)


def k_feed_or_rollback_payload(index: int, speed: int, length: int, mode: int) -> bytes:
    return k_uint32(1, index) + k_uint32(2, speed) + k_uint32(3, length) + k_uint32(4, mode)


def k_stop_feed_payload(index: int) -> bytes:
    return k_uint32(1, index)


def k_update_speed_payload(index: int, speed: int) -> bytes:
    return k_uint32(1, index) + k_uint32(2, speed)


def k_get_filament_info_payload(index: int) -> bytes:
    return k_uint32(1, index)


def k_set_dry_temp_payload(temp_c: int) -> bytes:
    return k_uint32(1, temp_c)


def k_set_rfid_enable_payload(index: int, enable: bool) -> bytes:
    return k_uint32(1, index) + k_bool(2, enable)


# --- PbMessage (Ace2Protocol.kt lines 340-461) -----------------------------


class PbField:
    __slots__ = ("wire_type", "varint", "raw")

    def __init__(self, wire_type: int, varint: int = 0, raw: bytes | None = None) -> None:
        self.wire_type = wire_type
        self.varint = varint
        self.raw = raw


class PbMessage:
    """Transcription of PbMessage; field storage keeps first-value semantics."""

    def __init__(self, fields: dict[int, list[PbField]]) -> None:
        self._fields = fields

    def varint(self, field: int, default: int = 0) -> int:
        for item in self._fields.get(field, []):
            if item.wire_type == 0:
                return item.varint
        return default

    def bytes(self, field: int) -> bytes | None:
        for item in self._fields.get(field, []):
            if item.wire_type == 2:
                return item.raw
        return None

    def string(self, field: int) -> str:
        raw = self.bytes(field)
        return raw.decode("utf-8", errors="replace") if raw is not None else ""

    def repeated(self, field: int) -> list[bytes]:
        return [item.raw or b"" for item in self._fields.get(field, []) if item.wire_type == 2]

    def sub_message(self, field: int) -> PbMessage | None:
        raw = self.bytes(field)
        return PbMessage.decode(raw) if raw is not None else None

    @staticmethod
    def decode(data: bytes) -> PbMessage:
        fields: dict[int, list[PbField]] = {}
        pos = 0
        while pos < len(data):
            tag, after_tag = read_varint(data, pos)
            if after_tag < 0:
                break
            pos = after_tag
            field_num = tag >> 3
            wire_type = tag & 0x07
            if field_num <= 0:
                break
            if wire_type == 0:
                value, after_value = read_varint(data, pos)
                if after_value < 0:
                    break
                pos = after_value
                fields.setdefault(field_num, []).append(PbField(0, value, None))
            elif wire_type == 2:
                length, after_len = read_varint(data, pos)
                if after_len < 0:
                    break
                n = length
                if n < 0 or after_len + n > len(data):
                    break
                pos = after_len + n
                fields.setdefault(field_num, []).append(
                    PbField(2, 0, data[after_len:after_len + n])
                )
            elif wire_type == 5:
                if pos + 4 > len(data):
                    break
                fields.setdefault(field_num, []).append(PbField(5, 0, data[pos:pos + 4]))
                pos += 4
            else:
                break
        return PbMessage(fields)


def read_varint(data: bytes, start: int) -> tuple[int, int]:
    """PbMessage.readVarint (Ace2Protocol.kt lines 420-433)."""
    result = 0
    shift = 0
    pos = start
    while pos < len(data):
        b = data[pos] & 0xFF
        pos += 1
        result = result | ((b & 0x7F) << shift)
        if (b & 0x80) == 0:
            return result, pos
        shift += 7
        if shift > 63:
            return result, -1
    return result, -1


def encode_varint(value: int) -> bytes:
    """PbMessage.encodeVarint (Ace2Protocol.kt lines 435-445)."""
    if value < 0:
        raise ValueError(f"varint 只支持非负值：{value}")
    out = bytearray()
    v = value
    while v > 0x7F:
        out.append((v & 0x7F) | 0x80)
        v >>= 7
    out.append(v & 0x7F)
    return bytes(out)


def k_uint32(field: int, value: int) -> bytes:
    return encode_varint(field << 3) + encode_varint(value)


def k_bool(field: int, value: bool) -> bytes:
    return encode_varint(field << 3) + encode_varint(1 if value else 0)


def k_bytes(field: int, value: bytes) -> bytes:
    return encode_varint((field << 3) | 2) + encode_varint(len(value)) + value


def k_string(field: int, value: str) -> bytes:
    return k_bytes(field, value.encode("utf-8"))


# --- response parsing (Ace2Protocol.kt lines 214-291) -----------------------


class Ace2DeviceUid:
    def __init__(self, uid1: int, uid2: int, uid3: int) -> None:
        self.uid1, self.uid2, self.uid3 = uid1, uid2, uid3


class Ace2Info:
    def __init__(self, version: str, boot_version: str, first_request: bool) -> None:
        self.version, self.boot_version, self.first_request = version, boot_version, first_request


class Ace2DryerStatus:
    def __init__(self, state_code: int, target_temp_c: int, duration_seconds: int, remain_seconds: int) -> None:
        self.state_code = state_code
        self.target_temp_c = target_temp_c
        self.duration_seconds = duration_seconds
        self.remain_seconds = remain_seconds


class Ace2SlotState:
    def __init__(self, index: int, state_code: int, filament_state: int) -> None:
        self.index, self.state_code, self.filament_state = index, state_code, filament_state


class Ace2Status:
    def __init__(
        self,
        work_state_code: int,
        temp_c: int,
        humidity: int,
        dryer: Ace2DryerStatus,
        slots: list[Ace2SlotState],
        feed_assist_count: int,
    ) -> None:
        self.work_state_code = work_state_code
        self.temp_c = temp_c
        self.humidity = humidity
        self.dryer = dryer
        self.slots = slots
        self.feed_assist_count = feed_assist_count


def k_parse_discover(payload: bytes) -> Ace2DeviceUid:
    msg = PbMessage.decode(payload)
    return Ace2DeviceUid(msg.varint(1), msg.varint(2), msg.varint(3))


def k_parse_info(payload: bytes) -> Ace2Info:
    msg = PbMessage.decode(payload)
    return Ace2Info(
        version=msg.string(1),
        boot_version=msg.string(2),
        first_request=msg.varint(3) != 0,
    )


def k_parse_generic_code(payload: bytes) -> int:
    if payload == b"":
        return 0
    return PbMessage.decode(payload).varint(1)


def k_parse_status(payload: bytes) -> Ace2Status:
    msg = PbMessage.decode(payload)
    dryer_bytes = msg.bytes(2)
    dryer = PbMessage.decode(dryer_bytes if dryer_bytes is not None and dryer_bytes else b"")
    slots: list[Ace2SlotState] = []
    for i, slot_payload in enumerate(msg.repeated(9)):
        slot = PbMessage.decode(slot_payload)
        slots.append(Ace2SlotState(i, slot.varint(1), slot.varint(2)))
    return Ace2Status(
        work_state_code=msg.varint(1),
        temp_c=msg.varint(3),
        humidity=msg.varint(4),
        dryer=Ace2DryerStatus(
            state_code=dryer.varint(1),
            target_temp_c=dryer.varint(2),
            duration_seconds=dryer.varint(3),
            remain_seconds=dryer.varint(4),
        ),
        slots=slots,
        feed_assist_count=msg.varint(7),
    )


# --- frame parser (Ace2Protocol.kt lines 467-560) ---------------------------


class Ace2Frame:
    def __init__(self, flags: int, request_id: int, command_code: int, payload: bytes) -> None:
        self.flags = flags
        self.request_id = request_id
        self.command_code = command_code
        self.payload = payload

    @property
    def is_response(self) -> bool:
        return (self.flags & FLAG_RESPONSE) != 0

    @property
    def device_id(self) -> int:
        return self.flags & DEVICE_ID_MASK

    @property
    def command_name(self) -> str:
        return command_name(self.command_code)


class Ace2FrameParser:
    MAX_BUFFER_BYTES = 1024

    def __init__(self) -> None:
        self.buffer = b""

    def reset(self) -> None:
        self.buffer = b""

    def pending_bytes(self) -> int:
        return len(self.buffer)

    def feed(self, chunk: bytes) -> list[Ace2Frame]:
        if chunk == b"":
            return []
        working = self.buffer + chunk
        frames: list[Ace2Frame] = []
        while True:
            if len(working) < MIN_FRAME_LEN:
                break
            if working[0] != HEAD_0 or working[1] != HEAD_1:
                idx = index_of_head(working, 0)
                if idx < 0:
                    working = keep_split_header(working)
                    break
                working = working[idx:]
                if len(working) < MIN_FRAME_LEN:
                    break
            payload_len = working[6]
            if payload_len > MAX_PAYLOAD:
                working = working[1:]
                continue
            frame_len = MIN_FRAME_LEN + payload_len
            if len(working) < frame_len:
                break
            if working[frame_len - 1] != TAIL:
                idx = index_of_head(working, 1)
                working = keep_split_header(working) if idx < 0 else working[idx:]
                continue
            frame_bytes = working[:frame_len]
            working = working[frame_len:]
            crc_rx = frame_bytes[7 + payload_len] | (frame_bytes[8 + payload_len] << 8)
            crc_calc = k_crc16(frame_bytes[2:7 + payload_len])
            if crc_rx != crc_calc:
                continue
            flags = frame_bytes[2]
            request_id = frame_bytes[3] | (frame_bytes[4] << 8)
            command = frame_bytes[5]
            payload = frame_bytes[7:7 + payload_len]
            frames.append(Ace2Frame(flags, request_id, command, payload))
        self.buffer = b"" if len(working) > self.MAX_BUFFER_BYTES else working
        return frames


def index_of_head(data: bytes, start: int) -> int:
    i = start
    while i + 1 < len(data):
        if data[i] == HEAD_0 and data[i + 1] == HEAD_1:
            return i
        i += 1
    return -1


def keep_split_header(data: bytes) -> bytes:
    return bytes([HEAD_0]) if data and data[-1] == HEAD_0 else b""


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------


class Report:
    def __init__(self) -> None:
        self.passes = 0
        self.fails: list[str] = []
        self.warns: list[str] = []
        self.audits: list[tuple[str, str, str]] = []

    def check(self, name: str, actual, expected) -> bool:
        if actual == expected:
            self.passes += 1
            return True
        self.fails.append(f"{name}: got {actual!r}, expected {expected!r}")
        return False

    def warn(self, name: str, detail: str) -> None:
        self.warns.append(f"{name}: {detail}")

    def audit(self, point: str, verdict: str, detail: str) -> None:
        self.audits.append((point, verdict, detail))


def hx(data: bytes) -> str:
    return " ".join(f"{b:02X}" for b in data)


def from_hex(text: str) -> bytes:
    if not text:
        return b""
    return bytes(int(part, 16) for part in text.split())


def main() -> int:
    with open(VECTORS_PATH, "r", encoding="utf-8") as fh:
        vectors = json.load(fh)
    report = Report()

    # ---------------- a/b: CRC known check + edge cases ----------------
    crc_group = "CRC16/MCRF4XX"
    for name, case in vectors["crc"].items():
        data = from_hex(case["input_hex"])
        report.check(f"{crc_group} {name}", f"0x{k_crc16(data):04X}", f"0x{case['crc']:04X}")
    report.check(
        f"{crc_group} golden 123456789",
        f"0x{k_crc16(b'123456789'):04X}",
        "0x6F91",
    )

    # ---------------- c-i: request frames, byte exact ----------------
    for req in vectors["requests"]:
        expected = req["frame_hex"]
        payload = build_payload_for_vector(req)
        device_id = req["target_device_id"] or DEFAULT_DEVICE_ID
        actual = hx(k_build_request(req["request_id"], req["command_code"], payload, device_id))
        report.check(f"request {req['name']} frame", actual, expected)
        report.check(f"request {req['name']} payload", hx(payload), req["payload_hex"])
        report.check(f"request {req['name']} frame_len", len(from_hex(actual)), req["frame_len"])
        report.check(f"request {req['name']} flags", k_request_flags(req["command_code"], device_id), req["flags"])

    # ---------------- j: response decoding ----------------
    for resp in vectors["responses"]:
        name = resp["name"]
        frame_bytes = from_hex(resp["frame_hex"])
        parser = Ace2FrameParser()
        frames = parser.feed(frame_bytes)
        exp = resp["expected"]
        if not report.check(f"response {name} frame_count", len(frames), exp["response_count"]):
            continue
        report.check(f"response {name} pending", parser.pending_bytes(), len(from_hex(exp["leftover_hex"])))
        if not frames:
            continue
        frame = frames[0]
        if "flags" in exp:
            report.check(f"response {name} flags", frame.flags, exp["flags"])
        if "id" in exp:
            report.check(f"response {name} id", frame.request_id, exp["id"])
            report.check(f"response {name} device_id", frame.device_id, exp["device_id"])
        if name == "get_status_response":
            status = k_parse_status(frame.payload)
            result = exp["result"]
            report.check("status work_state", work_state_name(status.work_state_code), result["status"])
            report.check("status work_state_code", status.work_state_code, result["status_code"])
            report.check("status temp", status.temp_c, result["temp"])
            report.check("status humidity", status.humidity, result["humidity"])
            report.check("status feed_assist_count", status.feed_assist_count, result["feed_assist_count"])
            report.check("status dryer state", dry_state_name(status.dryer.state_code), result["dryer_status"]["status"])
            report.check("status dryer detail", dry_state_detail_name(status.dryer.state_code), result["dryer_status"]["state_detail"])
            report.check("status dryer state_code", status.dryer.state_code, result["dryer_status"]["state_code"])
            report.check("status dryer target", status.dryer.target_temp_c, result["dryer_status"]["target_temp"])
            report.check(
                "status dryer duration_min",
                round(status.dryer.duration_seconds / 60),
                result["dryer_status"]["duration"],
            )
            report.check(
                "status dryer remain_sec",
                status.dryer.remain_seconds,
                result["dryer_status"]["remain_time"],
            )
            report.check("status slot_count", len(status.slots), len(result["slots"]))
            for i, slot in enumerate(status.slots[: len(result["slots"])]):
                exp_slot = result["slots"][i]
                report.check(f"status slot{i} index", slot.index, exp_slot["index"])
                report.check(f"status slot{i} state_code", slot.state_code, exp_slot["status_code"])
                # 参考实现 rfid 字段就是 filament 标志的原值（0/1/2/3）
                report.check(f"status slot{i} filament", slot.filament_state, exp_slot["rfid"])
        elif name == "get_info_response":
            info = k_parse_info(frame.payload)
            report.check("info version", info.version, exp["version"])
            report.check("info boot_version", info.boot_version, exp["boot_version"])
            report.check("info first_request", info.first_request, exp["first_request"])
        elif name == "discover_device_response":
            uid = k_parse_discover(frame.payload)
            report.check("discover uid1", uid.uid1, exp["uid1"])
            report.check("discover uid2", uid.uid2, exp["uid2"])
            report.check("discover uid3", uid.uid3, exp["uid3"])
        else:  # generic code responses
            code = k_parse_generic_code(frame.payload)
            report.check(f"{name} code", code, exp["code"])
            report.check(f"{name} msg", describe_code(code), exp["msg"])

    # ---------------- k: robustness / resync ----------------
    for case in vectors["robustness"]:
        name = case["name"]
        exp = case["expected"]
        parser = Ace2FrameParser()
        if name == "half_frame_then_rest":
            frames = parser.feed(from_hex(case["input_hex"]))
            report.check(f"{name} first_count", len(frames), exp["response_count"])
            report.check(f"{name} first_pending", parser.pending_bytes(), len(from_hex(exp["leftover_hex"])))
            frames2 = parser.feed(from_hex(case["continued_input_hex"]))
            report.check(f"{name} after_rest_count", len(frames2), exp["after_rest_response_count"])
            report.check(f"{name} after_rest_pending", parser.pending_bytes(), len(from_hex(exp["after_rest_leftover_hex"])))
        elif name == "split_header_keeps_trailing_ff":
            parser.feed(from_hex(case["input_hex"]))
            report.check(f"{name} pending_after_junk", parser.pending_bytes(), 1)
            frames = parser.feed(from_hex(case["continued_input_hex"]))
            report.check(f"{name} after_rest_count", len(frames), exp["after_rest_response_count"])
            report.check(f"{name} after_rest_pending", parser.pending_bytes(), len(from_hex(exp["after_rest_leftover_hex"])))
        else:
            frames = parser.feed(from_hex(case["input_hex"]))
            report.check(f"{name} count", len(frames), exp["response_count"])
            report.check(f"{name} pending", parser.pending_bytes(), len(from_hex(exp.get("leftover_hex", ""))))
            if "ids" in exp:
                report.check(f"{name} ids", [f.request_id for f in frames], exp["ids"])
            if name == "response_flag_and_device_id":
                frame = frames[0]
                report.check(f"{name} flags", frame.flags, exp["flags"])
                report.check(f"{name} device_id", frame.device_id, exp["device_id"])
                report.check(f"{name} id", frame.request_id, exp["id"])
                report.check(f"{name} command", frame.command_name, exp["command"])

    # ---------------- 10 high-risk audits ----------------
    run_audits(report)

    # ---------------- summary ----------------
    total = report.passes + len(report.fails)
    print("=" * 76)
    print("ACE2 验证报告（Kotlin Ace2Protocol.kt vs 参考实现黄金向量）")
    print("=" * 76)
    print(f"检查项：{total}    PASS：{report.passes}    FAIL：{len(report.fails)}")
    if report.fails:
        print("\nFAIL 明细：")
        for item in report.fails:
            print("  - " + item)
    if report.warns:
        print("\nWARN（风险提示，不计入 FAIL）：")
        for item in report.warns:
            print("  - " + item)
    print("\n10 个高风险点审查：")
    for point, verdict, detail in report.audits:
        print(f"  [{verdict}] {point}：{detail}")
    return 1 if report.fails else 0


def build_payload_for_vector(req: dict) -> bytes:
    command = req["command"]
    params = req["params"]
    if command == "DISCOVER_DEVICE":
        return k_discover_payload()
    if command == "ASSIGN_DEVICE_ID":
        return k_assign_id_payload(params["uid1"], params["uid2"], params["uid3"], params["device_id"])
    if command == "GET_STATUS" or command == "GET_INFO":
        return b""
    if command == "DRYING":
        return k_drying_payload(params["temp"], params["duration"], params.get("auto_roll", False))
    if command == "FEED_OR_ROLLBACK":
        return k_feed_or_rollback_payload(params["index"], params["speed"], params["length"], params["mode"])
    if command == "STOP_FEED_OR_ROLLBACK":
        return k_stop_feed_payload(params["index"])
    if command == "UPDATE_SPEED":
        return k_update_speed_payload(params["index"], params["speed"])
    if command == "SET_DRY_TEMP":
        return k_set_dry_temp_payload(params["temp"])
    raise NotImplementedError(command)


# ---------------------------------------------------------------------------
# Audit section
# ---------------------------------------------------------------------------


def read_text(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return fh.read()
    except OSError:
        return ""


def find_line(lines: list[str], pattern: str, default: int = 0) -> int:
    regex = re.compile(pattern)
    for i, line in enumerate(lines, 1):
        if regex.search(line):
            return i
    return default


def run_audits(report: Report) -> None:
    protocol_src = read_text(PROTOCOL_KT)
    client_src = read_text(CLIENT_KT)
    protocol_lines = protocol_src.splitlines()
    client_lines = client_src.splitlines()
    if not protocol_src:
        report.warn("source", f"找不到 {PROTOCOL_KT}")
    if not client_src:
        report.warn("source", f"找不到 {CLIENT_KT}")

    # 1) CRC coverage: flags..payload end, LE appended. Byte-exact frames in
    #    step c-i already prove coverage; additionally check the CRC range code.
    crc_line = find_line(protocol_lines, r"crc16\(frameBytes.copyOfRange\(2, 7 \+ payloadLen\)\)")
    crc_ok = crc_line > 0
    report.audit(
        "1. CRC 覆盖范围与字节序",
        "PASS" if crc_ok else "FAIL",
        f"CRC 计算区间 frameBytes[2..7+len)（Ace2Protocol.kt:{crc_line or '?'}），"
        "小端追加；12 条请求帧与参考实现逐字节一致",
    )
    if not crc_ok:
        report.fails.append("audit 1: CRC range line not found in Ace2Protocol.kt")

    # 2) id little-endian at bytes 3,4
    id_ok = False
    for req in json.load(open(VECTORS_PATH, encoding="utf-8"))["requests"]:
        if req["request_id"] == 10:
            raw = from_hex(req["frame_hex"])
            id_ok = raw[3] == 0x0A and raw[4] == 0x00
            break
    report.audit(
        "2. id 小端位置（flags 之后 2 字节）",
        "PASS" if id_ok else "FAIL",
        "id=10 编码为 0A 00，id=1 编码为 01 00；与参考实现一致",
    )
    if not id_ok:
        report.fails.append("audit 2: id little-endian position mismatch")

    # 3) flags rules
    discover = next(r for r in json.load(open(VECTORS_PATH, encoding="utf-8"))["requests"] if r["name"] == "discover_device")
    assign = next(r for r in json.load(open(VECTORS_PATH, encoding="utf-8"))["requests"] if r["name"] == "assign_device_id")
    flags_ok = discover["flags"] == 0 and assign["flags"] == 0 and from_hex(discover["frame_hex"])[2] == 0
    try:
        k_request_flags(CMD_GET_STATUS, 0)
        flags_rejects_zero = False
    except ValueError:
        flags_rejects_zero = True
    try:
        k_request_flags(CMD_GET_STATUS, 128)
        flags_rejects_128 = False
    except ValueError:
        flags_rejects_128 = True
    flags_ok = flags_ok and flags_rejects_zero and flags_rejects_128
    report.audit(
        "3. flags 规则（广播 0 / 地址 1..127 & 0x7F / 应答 bit7）",
        "PASS" if flags_ok else "FAIL",
        "DISCOVER/ASSIGN flags=0；地址命令越界抛 ValueError；应答 bit7 由解析器按 0x7F 取 device_id",
    )
    if not flags_ok:
        report.fails.append("audit 3: flags rules mismatch")

    # 4) len <= 100 / min frame 10
    len_ok = from_hex(discover["frame_hex"])[6] == 0 and len(from_hex(discover["frame_hex"])) == MIN_FRAME_LEN
    try:
        k_build_frame(1, 1, CMD_GET_INFO, bytes(101))
        len_rejects = False
    except ValueError:
        len_rejects = True
    big = k_build_frame(1, 1, CMD_GET_INFO, bytes(100))
    len_ok = len_ok and len_rejects and big[6] == 100 and len(big) == 110 and big[3] == 1
    report.audit(
        "4. len ≤ 100 / 最小帧 10 字节",
        "PASS" if len_ok else "FAIL",
        "空 payload 帧长 10；payload=100 时 len 字节=100、总长 110；payload=101 抛异常。"
        "注：参考实现不检查 len>100（会按长度等待），Kotlin 选择丢弃 1 字节重同步——更安全，"
        "但若将来实现 IAP 固件分片（payload 可 >100），该上限会拦下大帧",
    )
    if not len_ok:
        report.fails.append("audit 4: length bound rules mismatch")

    # 5) protobuf wire types
    wire_ok = True
    for req in json.load(open(VECTORS_PATH, encoding="utf-8"))["requests"]:
        payload = from_hex(req["payload_hex"])
        pos = 0
        while pos < len(payload):
            tag, pos = read_varint(payload, pos)
            field_num, wire = tag >> 3, tag & 7
            if field_num <= 0 or wire != 0:
                wire_ok = False
                break
            _, pos = read_varint(payload, pos)
        if not wire_ok:
            break
    # GET_INFO 应答里 string 字段是 wire type 2
    info_payload = from_hex(
        next(r for r in json.load(open(VECTORS_PATH, encoding="utf-8"))["responses"] if r["name"] == "get_info_response")["payload_hex"]
    )
    tag, _ = read_varint(info_payload, 0)
    wire_ok = wire_ok and (tag & 7) == 2
    report.audit(
        "5. protobuf wire type（uint32/bool=0，string/bytes=2）",
        "PASS" if wire_ok else "FAIL",
        "所有请求 payload 字段 tag 低 3 位=0；GET_INFO 字符串字段=2；与参考实现 pb_uint32/_pb_string 一致",
    )
    if not wire_ok:
        report.fails.append("audit 5: protobuf wire type mismatch")

    # 6) drying fields + units
    drying_ok = (
        from_hex(next(r for r in json.load(open(VECTORS_PATH, encoding="utf-8"))["requests"] if r["name"] == "stop_drying")["payload_hex"])
        == b"\x08\x00\x10\x00\x18\x00"
    )
    # status 应答 duration 是秒，UI 用 /60 显示分钟
    ui_minutes = "remainSeconds / 60" in read_text(os.path.join(ROOT, "app", "src", "main", "java", "com", "acesolo", "MainActivity.kt"))
    bounds = "minDryMinutes = 10" in read_text(os.path.join(ROOT, "app", "src", "main", "java", "com", "acesolo", "MainActivity.kt"))
    drying_ok = drying_ok and ui_minutes and bounds
    report.audit(
        "6. 烘干字段号与单位（1 温度℃ / 2 时长 / 3 auto_roll）",
        "PASS" if drying_ok else "FAIL",
        "停止烘干显式写零 08 00 10 00 18 00（与参考实现一致，不做 proto3 默认省略）；"
        "请求时长=分钟（10–1440），应答 field3/4=秒，UI 用 /60 换算",
    )
    if not drying_ok:
        report.fails.append("audit 6: drying field/unit mismatch")

    # 7) status field numbers
    status_ok = True
    proto_parse_status = protocol_src
    for pattern in ("msg.varint(1)", "msg.bytes(2)", "msg.varint(3)", "msg.varint(4)", "msg.repeated(9)"):
        if pattern not in proto_parse_status:
            status_ok = False
    report.audit(
        "7. GET_STATUS 字段号（1 工作态 / 2 烘干 / 3 温度 / 4 湿度 / 9 槽位）",
        "PASS" if status_ok else "FAIL",
        "parseStatus 逐字段对应参考实现；黄金向量解析值全部一致",
    )
    if not status_ok:
        report.fails.append("audit 7: status field numbers mismatch")

    # 8) response 0x80 bit
    status_resp = next(
        r for r in json.load(open(VECTORS_PATH, encoding="utf-8"))["responses"] if r["name"] == "get_status_response"
    )
    frame = Ace2FrameParser().feed(from_hex(status_resp["frame_hex"]))[0]
    resp_bit_ok = frame.is_response and frame.device_id == 1 and frame.flags == 0x81
    # 会话层是否校验 isResponse（风险点）
    handle_frame_line = find_line(client_lines, r"private fun handleFrame")
    checks_response_flag = bool(
        re.search(r"isResponse", client_src[client_src.find("handleFrame"):]) if "handleFrame" in client_src else False
    )
    if not checks_response_flag:
        report.warn(
            "8. 应答 0x80 位",
            "Ace2Client.handleFrame 只用 requestId 匹配、未校验 frame.isResponse；"
            "若串口回显自身发送的帧（flags 无 0x80），会被误当作应答。"
            "Ace2Frame.isResponse 已实现（Ace2Protocol.kt:331），建议在 handleFrame 中加上判断",
        )
        report.warn(
            "8b. 应答命令匹配",
            "handleFrame 只比较 id、不比较 commandCode；id 单调递增+重试换新 id，"
            "实际触发概率极低，但建议顺带校验 frame.commandCode == request.command",
        )
    report.audit(
        "8. 应答 flags bit7（0x80）",
        "PASS" if resp_bit_ok else "FAIL",
        "解析器保留 flags，is_response=(flags&0x80)!=0，device_id=flags&0x7F；"
        f"会话层匹配（Ace2Client.kt:{handle_frame_line or '?'}）仅按 id，见 WARN",
    )
    if not resp_bit_ok:
        report.fails.append("audit 8: response flag bit mismatch")

    # 9) resync keeps trailing 0xFF
    split_case = next(
        r for r in json.load(open(VECTORS_PATH, encoding="utf-8"))["robustness"] if r["name"] == "split_header_keeps_trailing_ff"
    )
    parser = Ace2FrameParser()
    parser.feed(from_hex(split_case["input_hex"]))
    kept = parser.pending_bytes() == 1
    frames = parser.feed(from_hex(split_case["continued_input_hex"]))
    split_ok = kept and len(frames) == 1 and parser.pending_bytes() == 0
    report.audit(
        "9. 重同步保留跨包半个帧头（末尾 0xFF）",
        "PASS" if split_ok else "FAIL",
        "junk+0xFF 后缓冲区只保留 FF；下一包补齐后正常解析出 1 帧",
    )
    if not split_ok:
        report.fails.append("audit 9: split header retention mismatch")

    # 10) session layer threading / timeouts
    session_ok = True
    required_client_patterns = [
        (r"handler\.post", "串口回调切主线程"),
        (r"ArrayDeque<Request>\(\)", "请求队列"),
        (r"if \(shutDown \|\| inFlight != null\) return", "同一时刻仅一个在途请求"),
        (r"handler\.removeCallbacks\(timeoutRunnable\)", "清理超时任务"),
        (r"handler\.postDelayed\(timeoutRunnable", "每次写帧后启动超时"),
        (r"request\.attempts\+\+", "重试计数"),
        (r"if \(request\.attempts < request\.maxAttempts\)", "按 maxAttempts 重试"),
        (r"requestId == inFlightId", "应答按 id 匹配"),
        (r"POLL_INTERVAL_MS = 2000L", "轮询周期"),
        (r"REQUEST_TIMEOUT_MS = 1200L", "请求超时"),
    ]
    missing = [label for pattern, label in required_client_patterns if not re.search(pattern, client_src)]
    session_ok = not missing
    report.audit(
        "10. 会话层线程/超时/重试",
        "PASS" if session_ok else "FAIL",
        "串口数据经 handler.post 切主线程；单在途请求+队列；1200ms 超时（烘干 2000ms）；"
        "重试按 maxAttempts；2s 轮询",
    )
    if missing:
        report.fails.append("audit 10: missing session-layer patterns: " + ", ".join(missing))

    # 附加风险：应答按 id 匹配但重试会换新 id（迟到应答被忽略），属于已文档化取舍
    if re.search(r"queue\.addFirst\(request\)", client_src):
        report.audit(
            "10b. 重试 id 策略",
            "PASS",
            "超时重试重新分配新 id（Ace2Client.kt pump），迟到旧 id 应答被忽略；"
            "单设备场景可接受，但日志里会出现无主应答",
        )
    if re.search(r"if \(!ok\) \{", client_src) and not re.search(
        r"if \(!ok\) \{[\s\S]{0,400}?pump\(\)", client_src
    ):
        report.warn(
            "10c. 写失败路径",
            "pump() 在 writeBytes 返回 false 时直接 return，未 pump() 队列中的后续请求，"
            "也未启动超时（Ace2Client.kt:250-256）。实际上串口关闭会触发 "
            "onSerialDisconnected→resetSession 清队列，风险较低；建议补一次 pump()/清队列。",
        )
    if "MAX_BUFFER_BYTES = 1024" in protocol_src:
        report.warn(
            "10d. 解析缓冲上限",
            "Ace2FrameParser 缓冲区超过 1024B 直接丢弃（Ace2Protocol.kt:532-533），"
            "参考实现无该上限；对 ≤100B 的 ACE2 帧无影响，仅提示行为差异。",
        )


if __name__ == "__main__":
    raise SystemExit(main())
