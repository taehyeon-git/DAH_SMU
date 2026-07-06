from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class LabPacket:
    raw_bytes: bytes
    protocol: str
    src_asset: str
    dst_asset: str
    dst_host: str
    dst_port: int
    message_type: str
    metadata: dict[str, Any] = field(default_factory=dict)
    annotations: list[dict[str, Any]] = field(default_factory=list)

    def clone_with(self, raw_bytes: bytes, annotation: dict[str, Any]) -> "LabPacket":
        return LabPacket(
            raw_bytes=raw_bytes,
            protocol=self.protocol,
            src_asset=self.src_asset,
            dst_asset=self.dst_asset,
            dst_host=self.dst_host,
            dst_port=self.dst_port,
            message_type=self.message_type,
            metadata=dict(self.metadata),
            annotations=[*self.annotations, annotation],
        )


def synthetic_mavlink_like_packet(dst_host: str = "localhost", dst_port: int = 14550) -> LabPacket:
    # MAVLink-like v2 frame: STX, LEN, INCOMPAT, COMPAT, SEQ, SYS, COMP, MSGID(3), PAYLOAD, CRC(2)
    raw = bytes([0xFD, 3, 0, 0, 12, 1, 1, 0, 0, 0, 0xAA, 0xBB, 0xCC, 0x34, 0x12])
    return LabPacket(
        raw_bytes=raw,
        protocol="MAVLink-like",
        src_asset="synthetic-frame-generator",
        dst_asset="local-parser",
        dst_host=dst_host,
        dst_port=dst_port,
        message_type="SYNTHETIC_FRAME",
        metadata={"simulated_only": True, "scope": "LOCAL_DOCKER_TESTBED_ONLY", "seq": 12, "expected_crc": "0x1234"},
    )

