import json
import os
import select
import socket
import time

CC_LISTEN_PORT  = int(os.getenv("CC_LISTEN_PORT",  "14555"))   # CC(UAV) 텔레메트리 수신
UGV_LISTEN_PORT = int(os.getenv("UGV_LISTEN_PORT", "14660"))   # UGV 텔레메트리 수신
CMD_LISTEN_PORT = int(os.getenv("CMD_LISTEN_PORT", "14580"))   # GCS/MC → 명령 수신

MISSION_HOST   = os.getenv("MISSION_HOST",   "mission-control")
MISSION_PORT   = int(os.getenv("MISSION_PORT",   "14540"))
COLLECTOR_HOST = os.getenv("COLLECTOR_HOST", "telemetry-collector")
COLLECTOR_PORT = int(os.getenv("COLLECTOR_PORT", "14541"))
GCS_HOST       = os.getenv("GCS_HOST",       "dah-gcs")
GCS_PORT       = int(os.getenv("GCS_PORT",       "14570"))
DASHBOARD_HOST = os.getenv("DASHBOARD_HOST", "dah-dashboard")
DASHBOARD_PORT = int(os.getenv("DASHBOARD_PORT", "14571"))

CC_CMD_HOST = os.getenv("CC_CMD_HOST", "dah-companion")
CC_CMD_PORT = int(os.getenv("CC_CMD_PORT", "14552"))

FAN_OUT = [
    ("Mission Control", MISSION_HOST,   MISSION_PORT),
    ("Collector",       COLLECTOR_HOST, COLLECTOR_PORT),
    ("GCS",             GCS_HOST,       GCS_PORT),
    ("Dashboard",       DASHBOARD_HOST, DASHBOARD_PORT),
]


def bind_udp(port):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("0.0.0.0", port))
    return sock


def forward(sock, payload, target):
    sock.sendto(json.dumps(payload).encode("utf-8"), target)


def main():
    cc_sock  = bind_udp(CC_LISTEN_PORT)
    ugv_sock = bind_udp(UGV_LISTEN_PORT)
    cmd_sock = bind_udp(CMD_LISTEN_PORT)
    out_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    inputs   = [cc_sock, ugv_sock, cmd_sock]

    print("[ROUTER] TICN 전술 라우터 시작")
    print(f"[ROUTER] CC(UAV) UDP :{CC_LISTEN_PORT}  /  UGV UDP :{UGV_LISTEN_PORT}  /  CMD UDP :{CMD_LISTEN_PORT}")
    print(f"[ROUTER] Telemetry fan-out×{len(FAN_OUT)}: " +
          ", ".join(f"{n}:{p}" for n, _, p in FAN_OUT))
    print(f"[ROUTER] Command 전달 → {CC_CMD_HOST}:{CC_CMD_PORT}")

    while True:
        readable, _, _ = select.select(inputs, [], [], 1)
        for sock in readable:
            data, addr = sock.recvfrom(8192)
            try:
                payload = json.loads(data.decode("utf-8"))
            except json.JSONDecodeError:
                print("[ROUTER] invalid packet dropped")
                continue

            # ── Command 경로: GCS/MC → Router → CC
            if sock is cmd_sock:
                payload["router_forwarded_at"] = time.time()
                payload["via"] = "dah-tactical-router"
                out_sock.sendto(
                    json.dumps(payload).encode("utf-8"),
                    (CC_CMD_HOST, CC_CMD_PORT)
                )
                print(f"[ROUTER] CMD [{payload.get('command')}] "
                      f"← {addr[0]} → CC {CC_CMD_HOST}:{CC_CMD_PORT}")
                continue

            # ── Telemetry 경로: CC/UGV → Router → fan-out×4
            payload["router_received_at"] = time.time()
            payload["router"] = "dah-tactical-router"
            payload["network"] = "TICN"

            failed = []
            for name, host, port in FAN_OUT:
                try:
                    forward(out_sock, payload, (host, port))
                except Exception as e:
                    failed.append(name)

            status = f"fan-out×{len(FAN_OUT) - len(failed)}" + \
                     (f" (fail: {','.join(failed)})" if failed else "")
            print(
                f"[ROUTER] {payload.get('platform_id')} "
                f"seq={payload.get('seq')}  {status}"
            )


if __name__ == "__main__":
    main()
