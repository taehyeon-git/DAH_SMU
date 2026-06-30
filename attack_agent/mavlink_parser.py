from __future__ import annotations

import json
import struct
from dataclasses import dataclass
from typing import Any


MAVLINK_V1_STX = 0xFE
MAVLINK_V2_STX = 0xFD
MAVLINK_IFLAG_SIGNED = 0x01

MESSAGE_NAMES = {
    0: "HEARTBEAT",
    1: "SYS_STATUS",
    33: "GLOBAL_POSITION_INT",
    42: "MISSION_CURRENT",
    44: "MISSION_COUNT",
    47: "MISSION_ACK",
    51: "MISSION_REQUEST_INT",
    73: "MISSION_ITEM_INT",
    76: "COMMAND_LONG",
    77: "COMMAND_ACK",
    340: "UTM_GLOBAL_POSITION",
}

CRC_EXTRA = {
    0: 50,
    1: 124,
    33: 104,
    42: 28,
    44: 221,
    47: 153,
    51: 196,
    73: 38,
    76: 152,
    77: 143,
}


@dataclass(frozen=True)
class ParsedMavlinkFrame:
    version: int
    sequence: int
    system_id: int
    component_id: int
    message_id: int
    message_name: str
    fields: dict[str, Any]
    payload_len: int = 0
    frame_len: int = 0
    signed: bool = False
    crc_valid: bool | None = None
    signature_link_id: int | None = None
    signature_timestamp: int | None = None


def parse_datagram(datagram: bytes) -> list[ParsedMavlinkFrame | dict[str, Any]]:
    stripped = datagram.strip()
    if stripped.startswith(b"{"):
        return [_parse_json_datagram(stripped)]
    frames: list[ParsedMavlinkFrame | dict[str, Any]] = []
    index = 0
    while index < len(datagram):
        marker = datagram[index]
        if marker == MAVLINK_V2_STX:
            frame, next_index = _parse_v2(datagram, index)
            frames.append(frame)
            index = next_index
        elif marker == MAVLINK_V1_STX:
            frame, next_index = _parse_v1(datagram, index)
            frames.append(frame)
            index = next_index
        else:
            index += 1
    return frames


def _parse_json_datagram(data: bytes) -> dict[str, Any]:
    value = json.loads(data.decode("utf-8"))
    if not isinstance(value, dict):
        raise ValueError("JSON telemetry datagram must be an object")
    return value


def _parse_v1(data: bytes, index: int) -> tuple[ParsedMavlinkFrame, int]:
    if index + 8 > len(data):
        raise ValueError("truncated MAVLink v1 header")
    payload_len = data[index + 1]
    frame_len = 6 + payload_len + 2
    if index + frame_len > len(data):
        raise ValueError("truncated MAVLink v1 frame")
    sequence = data[index + 2]
    system_id = data[index + 3]
    component_id = data[index + 4]
    message_id = data[index + 5]
    payload_start = index + 6
    payload_end = payload_start + payload_len
    payload = data[payload_start:payload_end]
    actual_crc = struct.unpack_from("<H", data, payload_end)[0]
    crc_valid = _crc_valid_or_none(message_id, data[index + 1:payload_end], actual_crc)
    return (
        ParsedMavlinkFrame(
            version=1,
            sequence=sequence,
            system_id=system_id,
            component_id=component_id,
            message_id=message_id,
            message_name=MESSAGE_NAMES.get(message_id, f"MSG_{message_id}"),
            fields=_parse_payload(message_id, payload),
            payload_len=payload_len,
            frame_len=frame_len,
            signed=False,
            crc_valid=crc_valid,
        ),
        index + frame_len,
    )


def _parse_v2(data: bytes, index: int) -> tuple[ParsedMavlinkFrame, int]:
    if index + 12 > len(data):
        raise ValueError("truncated MAVLink v2 header")
    payload_len = data[index + 1]
    incompat_flags = data[index + 2]
    sequence = data[index + 4]
    system_id = data[index + 5]
    component_id = data[index + 6]
    message_id = data[index + 7] | (data[index + 8] << 8) | (data[index + 9] << 16)
    signature_len = 13 if incompat_flags & MAVLINK_IFLAG_SIGNED else 0
    frame_len = 10 + payload_len + 2 + signature_len
    if index + frame_len > len(data):
        raise ValueError("truncated MAVLink v2 frame")
    payload_start = index + 10
    payload_end = payload_start + payload_len
    payload = data[payload_start:payload_end]
    actual_crc = struct.unpack_from("<H", data, payload_end)[0]
    crc_valid = _crc_valid_or_none(message_id, data[index + 1:payload_end], actual_crc)
    signature_link_id = None
    signature_timestamp = None
    if signature_len:
        sig_start = payload_end + 2
        signature_link_id = data[sig_start]
        signature_timestamp = int.from_bytes(data[sig_start + 1:sig_start + 7], "little")
    return (
        ParsedMavlinkFrame(
            version=2,
            sequence=sequence,
            system_id=system_id,
            component_id=component_id,
            message_id=message_id,
            message_name=MESSAGE_NAMES.get(message_id, f"MSG_{message_id}"),
            fields=_parse_payload(message_id, payload),
            payload_len=payload_len,
            frame_len=frame_len,
            signed=bool(signature_len),
            crc_valid=crc_valid,
            signature_link_id=signature_link_id,
            signature_timestamp=signature_timestamp,
        ),
        index + frame_len,
    )


def _crc_valid_or_none(message_id: int, crc_input: bytes, actual_crc: int) -> bool | None:
    if message_id not in CRC_EXTRA:
        return None
    expected = x25_checksum(crc_input + bytes([CRC_EXTRA[message_id]]))
    return expected == actual_crc


def x25_checksum(data: bytes) -> int:
    crc = 0xFFFF
    for byte in data:
        tmp = byte ^ (crc & 0xFF)
        tmp = (tmp ^ (tmp << 4)) & 0xFF
        crc = ((crc >> 8) ^ (tmp << 8) ^ (tmp << 3) ^ (tmp >> 4)) & 0xFFFF
    return crc


def _parse_payload(message_id: int, payload: bytes) -> dict[str, Any]:
    if message_id == 0:
        return _parse_heartbeat(payload)
    if message_id == 1:
        return _parse_sys_status(payload)
    if message_id == 33:
        return _parse_global_position_int(payload)
    if message_id == 42:
        return _parse_mission_current(payload)
    if message_id == 44:
        return _parse_mission_count(payload)
    if message_id == 47:
        return _parse_mission_ack(payload)
    if message_id == 51:
        return _parse_mission_request_int(payload)
    if message_id == 73:
        return _parse_mission_item_int(payload)
    if message_id == 76:
        return _parse_command_long(payload)
    if message_id == 77:
        return _parse_command_ack(payload)
    if message_id == 340:
        return _parse_utm_global_position(payload)
    return {"raw_payload_len": len(payload)}


def _parse_heartbeat(payload: bytes) -> dict[str, Any]:
    if len(payload) < 9:
        raise ValueError("HEARTBEAT payload too short")
    custom_mode, mav_type, autopilot, base_mode, system_status, mavlink_version = struct.unpack_from("<IBBBBB", payload)
    return {
        "custom_mode": custom_mode,
        "type": mav_type,
        "autopilot": autopilot,
        "base_mode": base_mode,
        "system_status": system_status,
        "mavlink_version": mavlink_version,
    }


def _parse_sys_status(payload: bytes) -> dict[str, Any]:
    if len(payload) < 31:
        raise ValueError("SYS_STATUS payload too short")
    battery_remaining = struct.unpack_from("<b", payload, 30)[0]
    drop_rate_comm = struct.unpack_from("<H", payload, 18)[0]
    errors_comm = struct.unpack_from("<H", payload, 20)[0]
    return {
        "battery_remaining": battery_remaining,
        "drop_rate_comm": drop_rate_comm,
        "errors_comm": errors_comm,
    }


def _parse_global_position_int(payload: bytes) -> dict[str, Any]:
    if len(payload) < 28:
        raise ValueError("GLOBAL_POSITION_INT payload too short")
    time_boot_ms, lat, lon, alt, relative_alt, vx, vy, vz, hdg = struct.unpack_from("<IiiiihhhH", payload)
    return {
        "time_boot_ms": time_boot_ms,
        "lat": lat,
        "lon": lon,
        "alt": alt,
        "relative_alt": relative_alt,
        "vx": vx,
        "vy": vy,
        "vz": vz,
        "hdg": hdg,
    }


def _parse_mission_current(payload: bytes) -> dict[str, Any]:
    if len(payload) < 2:
        raise ValueError("MISSION_CURRENT payload too short")
    seq = struct.unpack_from("<H", payload)[0]
    return {"seq": seq}


def _parse_mission_count(payload: bytes) -> dict[str, Any]:
    if len(payload) < 4:
        raise ValueError("MISSION_COUNT payload too short")
    count, target_system, target_component, mission_type = struct.unpack_from("<HBB", payload) + (payload[4] if len(payload) >= 5 else 0,)
    return {"count": count, "target_system": target_system, "target_component": target_component, "mission_type": mission_type}


def _parse_utm_global_position(payload: bytes) -> dict[str, Any]:
    if len(payload) < 44:
        raise ValueError("UTM_GLOBAL_POSITION payload too short")
    time_usec, lat, lon, alt, relative_alt, vx, vy, vz = struct.unpack_from("<Qiiiihhh", payload)
    return {
        "time": time_usec,
        "lat": lat,
        "lon": lon,
        "alt": alt,
        "relative_alt": relative_alt,
        "vx": vx,
        "vy": vy,
        "vz": vz,
    }


def _parse_command_ack(payload: bytes) -> dict[str, Any]:
    if len(payload) < 3:
        raise ValueError("COMMAND_ACK payload too short")
    command, result = struct.unpack_from("<HB", payload)
    return {"command": command, "result": result}


def _parse_command_long(payload: bytes) -> dict[str, Any]:
    if len(payload) < 33:
        raise ValueError("COMMAND_LONG payload too short")
    unpacked = struct.unpack_from("<fffffffHBBB", payload)
    return {
        "params": list(unpacked[:7]),
        "command": unpacked[7],
        "target_system": unpacked[8],
        "target_component": unpacked[9],
        "confirmation": unpacked[10],
    }


def _parse_mission_request_int(payload: bytes) -> dict[str, Any]:
    if len(payload) < 4:
        raise ValueError("MISSION_REQUEST_INT payload too short")
    seq, target_system, target_component = struct.unpack_from("<HBB", payload)
    mission_type = struct.unpack_from("<B", payload, 4)[0] if len(payload) >= 5 else 0
    return {"seq": seq, "target_system": target_system, "target_component": target_component, "mission_type": mission_type}


def _parse_mission_ack(payload: bytes) -> dict[str, Any]:
    if len(payload) < 3:
        raise ValueError("MISSION_ACK payload too short")
    target_system, target_component, ack_type = struct.unpack_from("<BBB", payload)
    mission_type = struct.unpack_from("<B", payload, 3)[0] if len(payload) >= 4 else 0
    return {"target_system": target_system, "target_component": target_component, "type": ack_type, "mission_type": mission_type}


def _parse_mission_item_int(payload: bytes) -> dict[str, Any]:
    if len(payload) < 38:
        raise ValueError("MISSION_ITEM_INT payload too short")
    values = struct.unpack_from("<ffffiifHHBBBBBB", payload)
    return {
        "params": list(values[:4]),
        "x": values[4],
        "y": values[5],
        "z": values[6],
        "seq": values[7],
        "command": values[8],
        "target_system": values[9],
        "target_component": values[10],
        "frame": values[11],
        "current": values[12],
        "autocontinue": values[13],
        "mission_type": values[14],
    }