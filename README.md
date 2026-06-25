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
┌────────────────────── 무인체계 (UAV / UGV) ──────────────────────┐
│                                                                  │
│  ┌────────────── UAV ──────────────┐   ┌────────────── UGV ──────────────┐
│  │ Autopilot / Flight Controller   │   │ Vehicle Controller              │
│  │          ↓                      │   │          ↓                      │
│  │ Companion Computer              │   │ Onboard Computer / Mission Comp │
│  │ - MAVLink 수집                  │   │ - ROS2 / MQTT 상태 수집         │
│  │ - Payload 상태 수집             │   │ - Sensor 상태 수집              │
│  │ - Command 전달                  │   │ - Command 전달                  │
│  └──────────────┬──────────────────┘   └──────────────┬──────────────────┘
│                 │ Telemetry                            │ Telemetry
└─────────────────┼──────────────────────────────────────┼────────────────────┘
                  ▼                                      ▼
            ┌────────────────────────────────────────────────┐
            │                Tactical Router                 │
            │                전술 통신 라우터                  │
            └──────────────────────┬─────────────────────────┘
                                   │
        ┌───────────────┬──────────┴───────────┬────────────────┐
        │               │                      │                │
        ▼               ▼                      ▼                ▼
┌───────────────┐ ┌──────────────────────┐ ┌───────────────┐ ┌──────────────────────────┐
│ GCS           │ │ Mission Control / C2 │ │ Dashboard     │ │ Telemetry Collector      │
│ Ground Control│ │ Server               │ │ 상태 시각화    │ │ Raw 로그 수집 / 저장      │
│ Station       │ │ - 상태 수신          │ └───────────────┘ └────────────┬─────────────┘
│ - 상태 확인    │ │ - 임무 판단          │                                ▼
│ - 수동 조작    │ │ - Command 생성       │                             Log DB
│ - 명령 입력    │ └──────────────┬───────┘
└────────┬──────┘                │
         │                       │
         └───────────┬───────────┘
                     │ Command
                     ▼
              Tactical Router
                     │
      ┌──────────────┴──────────────┐
      ▼                             ▼
Companion Computer            Onboard Computer
      ▼                             ▼
Autopilot / Flight Controller Vehicle Controller
      ▼                             ▼
     UAV                           UGV
```

Recon, Executor, Defense는 별도의 직접 공격/방어 실습 레이어로 유지합니다.

## 실행

\`\`\`powershell
docker compose up -d --build dah-dashboard
\`\`\`

\`\`\`text
Dashboard: http://localhost:8081
Mission Control API: http://localhost:8082/api/dashboard
\`\`\`
