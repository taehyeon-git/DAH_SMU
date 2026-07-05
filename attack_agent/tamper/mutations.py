from __future__ import annotations

from attack_agent.tamper.packet_model import LabPacket


def _bytearray(packet: LabPacket) -> bytearray:
    return bytearray(packet.raw_bytes)


def mutate_magic_or_stx(packet: LabPacket) -> LabPacket:
    data = _bytearray(packet)
    data[0] = 0x00
    return packet.clone_with(bytes(data), {"mode": "FRAME_STX_CORRUPT", "field": "stx", "expected": "0xFD", "observed": "0x00"})


def mutate_length_field(packet: LabPacket) -> LabPacket:
    data = _bytearray(packet)
    data[1] = (data[1] + 9) & 0xFF
    return packet.clone_with(bytes(data), {"mode": "FRAME_LENGTH_MISMATCH", "field": "length", "observed": data[1]})


def mutate_crc_field(packet: LabPacket) -> LabPacket:
    data = _bytearray(packet)
    data[-2] ^= 0xFF
    data[-1] ^= 0xFF
    return packet.clone_with(bytes(data), {"mode": "FRAME_CRC_BREAK", "field": "crc", "expected": packet.metadata.get("expected_crc"), "observed": f"0x{data[-1]:02x}{data[-2]:02x}"})


def mutate_sequence_field(packet: LabPacket) -> LabPacket:
    data = _bytearray(packet)
    old = data[4]
    data[4] = 1
    return packet.clone_with(bytes(data), {"mode": "FRAME_SEQUENCE_ROLLBACK", "field": "seq", "expected_min": old, "observed": data[4]})


def mutate_signature_field(packet: LabPacket) -> LabPacket:
    data = _bytearray(packet)
    data[2] |= 0x01
    data.extend(b"\x00" * 13)
    data[-1] = 0xFF
    return packet.clone_with(bytes(data), {"mode": "FRAME_SIGNATURE_INVALID", "field": "signature", "observed": "invalid_synthetic_signature"})


def mutate_old_timestamp(packet: LabPacket) -> LabPacket:
    data = _bytearray(packet)
    data[2] |= 0x01
    data.extend(b"\x01\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00")
    return packet.clone_with(bytes(data), {"mode": "FRAME_REPLAY_OLD_TIMESTAMP", "field": "signature_timestamp", "observed": 0})


def mutate_payload_bitflip(packet: LabPacket) -> LabPacket:
    data = _bytearray(packet)
    if len(data) > 10:
        data[10] ^= 0x01
    return packet.clone_with(bytes(data), {"mode": "FRAME_PAYLOAD_BITFLIP_SIM", "field": "payload[0]", "operation": "xor_0x01"})


def truncate_payload(packet: LabPacket) -> LabPacket:
    data = packet.raw_bytes[:-3]
    return packet.clone_with(data, {"mode": "FRAME_LENGTH_MISMATCH", "field": "payload", "operation": "truncate"})


def append_garbage(packet: LabPacket) -> LabPacket:
    return packet.clone_with(packet.raw_bytes + b"\xff\xee\xdd", {"mode": "FRAME_PAYLOAD_BITFLIP_SIM", "field": "tail", "operation": "append_garbage"})


MUTATIONS = {
    "FRAME_STX_CORRUPT": mutate_magic_or_stx,
    "FRAME_LENGTH_MISMATCH": mutate_length_field,
    "FRAME_CRC_BREAK": mutate_crc_field,
    "FRAME_SIGNATURE_INVALID": mutate_signature_field,
    "FRAME_SEQUENCE_ROLLBACK": mutate_sequence_field,
    "FRAME_REPLAY_OLD_TIMESTAMP": mutate_old_timestamp,
    "FRAME_PAYLOAD_BITFLIP_SIM": mutate_payload_bitflip,
}


def apply_mutation(packet: LabPacket, mode: str) -> LabPacket:
    if mode not in MUTATIONS:
        raise ValueError(f"unsupported lab mutation: {mode}")
    return MUTATIONS[mode](packet)

