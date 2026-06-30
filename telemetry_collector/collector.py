import json
import os
import socket
import time

LISTEN_PORT = int(os.getenv("LISTEN_PORT", "14541"))
LOG_PATH = os.getenv("LOG_PATH", "/tmp/tactical_telemetry.jsonl")


def main():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("0.0.0.0", LISTEN_PORT))
    print(f"[COLLECTOR] telemetry collector UDP {LISTEN_PORT} 수신 시작")
    print(f"[COLLECTOR] log path: {LOG_PATH}")

    while True:
        data, _ = sock.recvfrom(8192)
        try:
            payload = json.loads(data.decode("utf-8"))
        except json.JSONDecodeError:
            continue

        record = {
            "stored_at": time.time(),
            **payload,
        }
        with open(LOG_PATH, "a", encoding="utf-8") as log_file:
            log_file.write(json.dumps(record, ensure_ascii=False) + "\n")

        print(
            f"[COLLECTOR] saved {record.get('platform_id')} "
            f"seq={record.get('seq')} type={record.get('message_type')}"
        )


if __name__ == "__main__":
    main()
