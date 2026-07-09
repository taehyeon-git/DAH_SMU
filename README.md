# DAH 2026 UAV/UGV C2 통신 AI 공방 테스트베드

Docker 기반 폐쇄형 UAV/UGV 전술통신 환경에서 공격 에이전트와 방어 에이전트를 실행하고, 대시보드로 통신 상태와 공방 로그를 확인하는 프로토타입이다.

- Dashboard: `http://localhost:9000`
- UAV: `dah-uav` / `172.31.50.10` / command `14551`
- UGV: `dah-ugv` / `172.31.50.20` / telemetry `14660`, command listener `14661`
- Tactical Router: `dah-tactical-router` / `http://localhost:8084`
- 테스트 범위: 로컬 Docker 네트워크 한정

## 빠른 실행

```powershell
docker compose up -d --build
```

| 항목 | 주소 / 명령 |
|---|---|
| 대시보드 | `http://localhost:9000` |
| 실시간 상태 | `Invoke-RestMethod http://localhost:9000/api/live` |
| GCS API | `http://localhost:9000/gcs/` |
| Upper C2 API | `http://localhost:9000/c2/` |
| Router API | `http://localhost:9000/router/` |
| Router 직접 상태 | `Invoke-RestMethod http://localhost:8084/api/ticn/status` |
| 종료 | `docker compose down` |

기본 실행은 UAV, UGV, Companion, GCS, Tactical Router, Mission Control, Collector, Dashboard, Gateway만 기동한다. 공격/방어 에이전트는 아래 명령으로 별도 실행한다.

## 시스템 아키텍처

기본 운용 경로는 UAV와 UGV가 다르다. UAV는 `dah-uav → dah-companion → dah-gcs → dah-tactical-router → mission-control`로 흐르고, UGV는 `dah-ugv → dah-tactical-router → mission-control / dah-dashboard`로 직접 전술 라우터에 연결된다. Dashboard, 공격 에이전트, 방어 에이전트는 같은 로컬 테스트베드 안에서 상태 표시, 공격 이벤트 주입, 탐지·대응을 담당한다.

```text
┌──────────────────────────── UAV / UGV Asset Layer ────────────────────────────┐
│                                                                               │
│  ┌────────────────────── UAV Simulator ──────────────────────┐  ┌───────────┐ │
│  │ dah-uav / UAV-001                                          │  │ dah-ugv   │ │
│  │ - MAVLink telemetry 송신 → dah-companion:14550             │  │ UGV-001   │ │
│  │ - COMMAND_LONG 수신 ← UDP:14551                            │  │ - JSON    │ │
│  │ - heartbeat timeout / link loss 기반 fail-safe 모사         │  │   telemetry│ │
│  └──────────────────────┬────────────────────────────────────┘  │   → Router │ │
│                         │                                       │   :14660   │ │
│                         │                                       │ - command  │ │
│                         │                                       │   listener │ │
│                         │                                       │   :14661   │ │
│                         │                                       └─────┬─────┘ │
└─────────────────────────┼─────────────────────────────────────────────┼───────┘
                          │ MAVLink telemetry                          │ UGV telemetry
                          ▼                                             ▼

┌──────────────────────────── Onboard Companion Layer ──────────────────────────┐
│                                                                               │
│  ┌────────────────────── Companion Computer ──────────────────────┐           │
│  │ dah-companion / 172.31.50.30                                    │           │
│  │ - MAVLink 수신 :14550                                           │           │
│  │ - MAVLink → JSON 변환                                           │           │
│  │ - GCS로 JSON telemetry 송신 → dah-gcs:14555                     │           │
│  │ - GCS command 수신 ← :14552, UAV command로 전달 → UAV:14551     │           │
│  │ - Passive recon mirror 송신 → dah-recon:14550                   │           │
│  └──────────────────────────────┬──────────────────────────────────┘           │
└─────────────────────────────────┼──────────────────────────────────────────────┘
                                  │ JSON telemetry :14555
                                  ▼

┌──────────────────────────── Ground Gateway / GCS Layer ───────────────────────┐
│                                                                               │
│  ┌────────────────────── Ground Control Station ───────────────────┐           │
│  │ dah-gcs                                                          │           │
│  │ - Companion telemetry 수신 :14555                                │           │
│  │ - Dashboard fan-out → dah-dashboard:14571                        │           │
│  │ - Collector fan-out → telemetry-collector:14541                  │           │
│  │ - Tactical relay → tactical-router:14560                         │           │
│  │ - Upper C2 command 수신 ← Router:14562                           │           │
│  │ - Companion command 전달 → dah-companion:14552                   │           │
│  └──────────────────────────────┬──────────────────────────────────┘           │
└─────────────────────────────────┼──────────────────────────────────────────────┘
                                  │ tactical relay :14560
                                  ▼

┌──────────────────── Virtual Tactical Router / TMMR / TICN Layer ──────────────┐
│ dah-tactical-router                                                            │
│ - GCS 전술 릴레이 수신 :14560                                                   │
│ - UGV telemetry 수신 :14660                                                     │
│ - Upper C2 command 수신 :14546 → GCS:14562로 하달                               │
│ - Router situation report → mission-control:14545                               │
│ - Dashboard fan-out → dah-dashboard:14571                                       │
│ - HTTP status/API :8084                                                         │
│ - EW/JAM lab event 수신 UDP :14590                                              │
│ - TMMR/TICN은 별도 컨테이너가 아니라 tactical_router/ticn 내부 시뮬레이션        │
└──────────────────────────────┬────────────────────────────────────────────────┘
                               │ Report / Situation Data ↓
                               │ Command / Tasking ↑
                               ▼

┌──────────────────────────── Upper C2 / BMS Layer ─────────────────────────────┐
│ mission-control                                                                │
│ - Router 경유 전술 상황 수신 ← :14545                                          │
│ - 작전 명령 하달 → tactical-router:14546                                       │
│ - UAV 직접 명령 없음: Router → GCS → Companion → UAV 경유                      │
└───────────────────────────────────────────────────────────────────────────────┘

┌──────────────────────────── Dashboard / Evidence Layer ───────────────────────┐
│ dah-dashboard + dah-gateway                                                    │
│ - 외부 진입점: http://localhost:9000                                           │
│ - GCS/Router UDP fan-out 수신 :14571                                           │
│ - 지도, 임무 상태, 링크 상태, ATK/DEF 이벤트 로그 표시                         │
│ - 운용자 직접 명령 및 GCS heartbeat → UAV:14551                                │
└───────────────────────────────────────────────────────────────────────────────┘

┌──────────────────────────── AI Attack / Defense Layer ────────────────────────┐
│  ┌──────────────────────────── AI Attack Agent ─────────────────────────────┐ │
│  │ attack_agent                                                             │ │
│  │ - Recon → InitialAccess → FollowUp                                       │ │
│  │ - MAVLink lab injection → UAV:14551                                      │ │
│  │ - EW link degradation event → Router:14590                               │ │
│  │ - ATK evidence/log → Dashboard                                           │ │
│  └──────────────────────────────────────────────────────────────────────────┘ │
│                                                                               │
│  ┌──────────────────────────── AI Defense Agents ───────────────────────────┐ │
│  │ defense_agents                                                           │ │
│  │ - Policy → Detection → Response → Recovery                               │ │
│  │ - Dashboard /api/live, Router /api/ticn/status, MAVLink UDP listener     │ │
│  │ - Router clear/hop, UAV RTL/SAFE_MODE, DEF evidence/log                  │ │
│  └──────────────────────────────────────────────────────────────────────────┘ │
└───────────────────────────────────────────────────────────────────────────────┘
```

### 핵심 통신 흐름

| 흐름 | 경로 | 설명 |
|---|---|---|
| UAV telemetry | `dah-uav → dah-companion → dah-gcs` | MAVLink 수신 후 JSON 변환 |
| Dashboard 표시 | `dah-gcs / tactical-router → dah-dashboard` | 지도, 임무, 링크, 로그 표시 |
| 상위 C2 연동 | `dah-gcs → tactical-router → mission-control` | TMMR/TICN 링크 품질 반영 |
| 명령 하달 | `mission-control → router → gcs → companion → uav` | Upper C2/BMS 명령 경로 |
| Dashboard 직접 명령 | `dah-dashboard → dah-uav:14551` | 운용자 명령 및 GCS heartbeat |
| UGV 전술망 | `dah-ugv → tactical-router → dah-dashboard/mission-control` | UGV 상태와 TICN 품질 반영 |
| 정찰 mirror | `dah-companion → dah-recon` | 운용 경로를 바꾸지 않는 passive 수집 |

## 컨테이너

| 컨테이너 | 역할 |
|---|---|
| `dah-uav` | MAVLink 기반 UAV 시뮬레이터 |
| `dah-ugv` | UGV 상태/임무 시뮬레이터 |
| `dah-companion` | MAVLink 수신, JSON 변환, Recon mirror |
| `dah-gcs` | Telemetry 수신 및 Dashboard/Collector/Router fan-out |
| `dah-tactical-router` | TMMR/TICN 링크 품질, 손실률, 재밍 시뮬레이션 |
| `dah-mission-control` | Upper C2/BMS 작전 상태 및 명령 |
| `dah-dashboard` | Flask + Leaflet 기반 실시간 대시보드 |
| `dah-recon` | Passive MAVLink 정찰 컨테이너 |
| `dah-defense` | 4-Agent 방어 체계 |
| `dah-gateway` | `localhost:9000` 단일 진입점 |

## 공격 에이전트

`attack_agent`는 정찰, 초기 침투 분석, 후속 공격 실행을 JSON 산출물로 연결한다.

```text
ReconAgent → InitialAccessAgent → FollowUpAttackAgent
```

### 실행

```powershell
python -m attack_agent.kill_chain --stage recon
python -m attack_agent.kill_chain --stage initial-access
python -m attack_agent.kill_chain --stage follow-up --objective FAILSAFE_INDUCTION --max-steps 1
```

실제 lab 이벤트 전송은 기본 비활성화다. 아래처럼 명시해야 폐쇄망 테스트베드 안에서만 실행된다.

```powershell
$env:ENABLE_LAB_ATTACKS="true"
python -m attack_agent.kill_chain --stage follow-up --objective FAILSAFE_INDUCTION --execute --max-steps 1
```

### Fail-safe 유도 순서

`FAILSAFE_INDUCTION`은 `attack_agent/planner/plan_builder.py`의 `FAILSAFE_CHAIN_ORDER` 순서대로 시도하고, 성공하면 중단한다.

| 순서 | 공격 벡터 | 동작 |
|---|---|---|
| 1 | `MAVLINK_STATUS_SPOOF` | 위조 heartbeat 상태 주입 |
| 2 | `HB_TIMEOUT_INDUCTION` | heartbeat 두절 유도 |
| 3 | `MAVLINK_COMMAND_INJECTION` | 위조 `COMMAND_LONG` 주입 |
| 4 | `EW_LINK_DEGRADATION_SIM` | TICN 손실률 상승 이벤트 |
| 5 | `EW_STEALTH_DEGRADATION_SIM` | 탐지 임계값 회피를 목표로 한 링크 열화 이벤트 |

## 방어 에이전트

`defense_agents`는 정책, 탐지, 대응, 복구를 분리한 4-Agent 구조다.

```text
DefensePolicyAgent → DefenseDetectionAgent → DefenseResponseAgent → DefenseRecoveryAgent
```

방어 에이전트를 먼저 실행하면 Policy/Response 단계가 Dashboard, Router, UAV에 예방 게이트를 적용한다. 이후 공격 체인이 lab event를 전송해도 Dashboard는 fail-safe overlay 전환을 막고, Router는 EW/JAM 이벤트가 TICN 손실률 변경으로 이어지는 것을 막으며, UAV는 비허용 명령과 위조 heartbeat를 차단한다. 차단된 시도는 `DEF` 이벤트와 JSON evidence로 남는다.

### 실행

```powershell
docker compose --profile defense-lab up --build dah-defense
```

### 탐지와 대응

| 탐지 시나리오 | 대응 |
|---|---|
| `COMMAND_INJECTION`, `UNKNOWN_COMMAND` | UAV/Dashboard 명령 신뢰 게이트 차단 |
| `FORCED_LAND_ATTEMPT` | LAND/SET_MODE 차단 후 RTL 명령 |
| `REPLAY_ATTACK` | SAFE_MODE |
| `GPS_SPOOFING` | INS fallback |
| `EW_LINK_DEGRADATION` | Router EW/JAM 게이트 유지, 위치 유지 |
| `JAMMING_CRITICAL` | Router EW/JAM 게이트 유지, TICN 채널 clear/hop |
| `FAILSAFE_INDUCTION` | Dashboard fail-safe overlay 차단, 위치 유지 후 필요 시 RTL |
| `PROTOCOL_FRAME_INTEGRITY` | 변조 프레임 이벤트 격리 및 차단 |

## 통신 구현

- MAVLink: `pymavlink` 기반 MAVLink 2.0
- UAV telemetry: `HEARTBEAT`, `SYS_STATUS`, `GLOBAL_POSITION_INT`, `MISSION_ITEM_REACHED`
- Dashboard 직접 UAV command: `COMMAND_LONG` 기반 `HOLD`, `PAUSE`, `RESUME`, `MONITOR`, `RTB`
- GCS/Upper C2 경유 UAV command: JSON `LAND`/`RTB`를 Companion이 MAVLink `COMMAND_LONG`으로 변환
- GCS heartbeat: `dashboard/app.py`가 1Hz로 전송, `mock_uav.py`는 5초 이상 미수신 시 LOITER fail-safe
- TMMR/TICN: `tactical_router/ticn/`에서 RSSI, Link Quality, loss_pct, delay, jam, channel hop 시뮬레이션
- 공격 이벤트: 실제 RF 재밍이 아니라 Router/Dashboard/UAV 시뮬레이터로 보내는 로컬 lab 이벤트

## 산출물

| 파일 | 내용 |
|---|---|
| `output/stage_1_recon.json` | 정찰 정규화 결과 |
| `output/stage_2_initial_access.json` | 자산, 엣지, GCS 모델, 공격 후보 |
| `output/stage_2_attack_graph.json` | 공격 그래프 |
| `output/stage_3_attack_plan.json` | 후속 공격 계획 |
| `output/stage_3_execution_report.json` | 실행 및 검증 결과 |
| `output/defense_incident_report.json` | 방어 사고 타임라인 |
| `output/defense_policy_recommendations.json` | 정책 개선 권고 |

## 안전 범위

- 모든 동작은 로컬 Docker 테스트베드 안에서만 수행한다.
- 실제 군 장비, 실제 항공기, 실제 RF 장비를 사용하지 않는다.
- MAVLink 패킷 주입 대상은 `dah-uav` 시뮬레이터로 제한한다.
- 재밍은 실제 전파 방해가 아니라 `loss_pct` 상승 이벤트로 표현한다.
- 기본 공격 실행은 dry-run이며, `ENABLE_LAB_ATTACKS=true`와 `--execute`가 모두 필요하다.

## 디렉터리

```text
uav/                  UAV MAVLink 시뮬레이터
ugv/                  UGV 시뮬레이터
companion_computer/   MAVLink to JSON, Recon mirror
gcs/                  Telemetry fan-out
tactical_router/      TMMR/TICN 시뮬레이션
c2/mission_control/   Upper C2/BMS
dashboard/            실시간 대시보드
attack_agent/         공격 에이전트
defense_agents/       방어 에이전트
docs/                 보조 문서
output/               실행 산출물
```

## 관련 문서

- [attack_agent/README_CHAIN.md](attack_agent/README_CHAIN.md)
- [attack_agent/RECON.md](attack_agent/RECON.md)
- [docs/defense_multi_agent_architecture.md](docs/defense_multi_agent_architecture.md)
