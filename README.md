# DAH 2026 - UAV/UGV  통신 시뮬레이션

> **도메인**: UAV / UGV  
> **환경**: 위성 네트워크 기반 클라우드 가상 전장  

---

## 프로젝트 개요

DAH - **UAV/UGV 전술 무인체계 통신 구조 시뮬레이션**입니다.

LIG Defense&Aerospace의 항공전자·드론, 전자전, 무인화·미래전 분야와  
한화시스템의 C5I, TICN, 군 위성통신체계-II, 전술데이터링크 개념을 참고합니다.

현재 대시보드는 C2, Mission Control, UAV, UGV, EW UAV, TICN/SATCOM 링크 상태를 움직이는 전장 시뮬레이션 형태로 시각화합니다.

## 아키텍처

```text
┌──────────────────────── UAV / UGV Asset Layer ─────────────────────────────┐
│                                                                            │
│  ┌───────── UAV Simulator ─────────┐  ┌───────── UGV Simulator ──────────┐ │
│  │ - Autopilot / FC Logic          │  │ - Vehicle Controller Logic       │ │
│  │ - MAVLink-like Telemetry/Cmd    │  │ - ROS2/MQTT-like Telemetry       │ │
│  │ - Payload Status                │  │ - Sensor Status                  │ │
│  │ - Command Receive / Execute     │  │ - Command Receive / Execute      │ │
│  └────────────────┬────────────────┘  └────────────────┬─────────────────┘ │
└───────────────────┼────────────────────────────────────┼───────────────────┘
                    │ C2 Data Link                       │ C2 Data Link
                    │ Telemetry / Report ↓               │ Telemetry / Report ↓
                    │ Command / Tasking ↑                │ Command / Tasking ↑
                    ▼                                    ▼

┌────────────────────────────────────────────────────────────────────────────┐
│ GCS / Ground Gateway / Mission Control Server                              │
│ - UAV / UGV Telemetry 수신 및 해석                                           │
│ - 임무 상태 판단                                                             │
│ - 수동 조작 / Command 생성                                                   │
│ - Upper C2/BMS 명령 → UAV/UGV Command 변환                                  │
│ - 전술망 메시지 변환: 위치/상태/임무/표적/영상 메타                               │
└─────────────┬──────────────────────┬──────────────────────┬────────────────┘
              │                      │                      │
              ▼                      ▼                      ▼
  ┌────────────────────┐  ┌─────────────────────┐  ┌──────────────────────┐
  │ Dashboard          │  │ Telemetry           │  │ AI Defense Agent     │
  │ - 상태/지도 시각화   │  │ Collector / LogDB   │  │ - 실시간 상태 분석      │
  │ - 임무 표시         │  │ - Telemetry Log     │  │ - Command 무결성       │
  │ - 경고 표시         │  │ - Command Log       │  │ - 이상징후 탐지         │
  │ - 공격/방어 결과     │  │ - Network/Attack Log│  │ - 대응 정책 결정       │
  └────────────────────┘  └─────────────────────┘  └──────────┬───────────┘
                                                               │
                                                               ▼
                                                  Alert / Block / Quarantine
                                                  Re-route / Fallback / Review

              ▲
              │ 통제된 공격 이벤트 주입
┌─────────────┴──────────────────────────────────────────────────────────────┐
│ AI Attack Agent                                                            │
│ - Docker 가상 네트워크 내부 자동 공격 이벤트 생성                               │
│ - Telemetry 위조 / Command 변조 / GPS 이상 좌표 주입                          │
│ - 통신 지연 / 손실 / 차단 / 변조 이벤트                                        │
│ - AI Defense Agent 탐지 성능 검증                                            │
│ ※ 폐쇄형 UAV/UGV 도메인 가상 환경 내부에서만 동작                               │
└────────────────────────────────────────────────────────────────────────────┘

                    │ 전술망 연동 데이터
                    │ Report / Situation Data ↓
                    │ Command / Tasking ↑
                    ▼

┌────────────────────────────────────────────────────────────────────────────┐
│ Virtual Tactical Router / TIPS                                             │
│ - Docker Network 기반 가상 전술 라우터                                        │
│ - GCS / 전술망 간 IP 패킷 라우팅                                              │
│ - 지연 / 손실 / 차단 / 변조 이벤트 적용 지점                                    │
│ - QoS / 우선순위 처리 모사                                                    │
│ - GCS가 변환한 전술망 데이터 중계                                              │
│ ※ MAVLink / ROS2 직접 해석 없음                                             │
└────────────────────┬───────────────────────────────────────────────────────┘
                     │ Report / Situation Data ↓  /  Command / Tasking ↑
                     ▼

┌────────────────────────────────────────────────────────────────────────────┐
│ TMMR / 전투무선체계 (CNRS-series)                                            │
│ - 전술 무선 노드                                                             │
│ - 음성 / 데이터 송수신                                                        │
│ - TICN 접속 구간 / 전술 무선 링크 모사                                         │
└────────────────────┬───────────────────────────────────────────────────────┘
                     │ Report / Situation Data ↓  /  Command / Tasking ↑
                     ▼

┌────────────────────────────────────────────────────────────────────────────┐
│ TICN-like Tactical Network                                                 │
│ - 전술정보통신망 모사                                                         │
│ - 전술 데이터망 / C4ISR 지휘통제망 연동 흐름 모사                                │
│ - 현장 전술 노드와 상위 지휘체계 연결                                           │
└────────────────────┬───────────────────────────────────────────────────────┘
                     │ Report / Situation Data ↓  /  Command / Tasking ↑
                     ▼

┌────────────────────────────────────────────────────────────────────────────┐
│ Upper C2 / BMS Simulator                                                   │
│ - 작전 상황 공유 / 표적 및 좌표 공유                                           │
│ - 감시 구역 지정 / 임무 변경 지시                                              │
│ - 상급부대 명령 하달                                                          │
│ ※ UAV/UGV 직접 명령 없음 — GCS 경유하여 Command로 변환                        │
└────────────────────────────────────────────────────────────────────────────┘
```

## 구현 범위

본 프로젝트는 실제 TICN/SATCOM 또는 실제 MAVLink/ROS2 네트워크를 완전 구현한 것이 아니라,
UAV/UGV 전술 통신 구조를 학습하고 시연하기 위한 Docker 기반 시뮬레이션이다.

Telemetry는 MAVLink/ROS2/MQTT 메시지 구조를 모사한 JSON 기반 데이터로 생성되며,
GCS가 CC(Companion Computer)로부터 직접 수신하여 Dashboard, Telemetry Collector, Tactical Router로 fan-out한다.

Command는 Upper C2/BMS 또는 GCS 운용자가 생성하며, Upper C2 명령은 Router → GCS → CC → UAV/UGV 경로로 전달된다.
GCS 운용자 직접 명령은 CC로 즉시 전달되며, 수신된 명령은 이후 Telemetry 상태 변화로 반영된다.

## 시스템 구성 요소

- UAV / UGV는 상태 정보를 생성한다.
- Companion / Onboard Computer는 MAVLink를 수신하여 JSON으로 변환하고 GCS로 전달한다.
- GCS / Ground Gateway는 텔레메트리를 직수신하고 전술망 하위 노드에 fan-out하는 중앙 허브이다.
- Tactical Router / TIPS는 GCS 전술 데이터를 TMMR/TICN 시뮬레이션을 통해 Upper C2로 중계한다.
- Upper C2 / BMS는 전술 상황을 판단하고 작전 명령을 Router 경유 하달하는 최상위 지휘 계층이다.
- Dashboard는 상태, 링크, 로그, Agent 판단 결과를 시각화한다.
- Telemetry Collector는 Raw 로그를 수집한다.
- AI Agent는 로그와 상태 정보를 기반으로 공격/방어 판단 흐름을 생성한다.

## 실행

```powershell
docker compose up -d --build dah-dashboard
```

```text
Dashboard: http://localhost:8081
Mission Control API: http://localhost:8082/api/dashboard
```
