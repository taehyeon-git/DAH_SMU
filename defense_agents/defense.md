# Defense Agent Architecture

현재 DAH 로컬 구현 기준의 방어 에이전트 구조와 실행 방법이다. 보고서에서는 `공격 시나리오 대응 방어 아키텍처`와 `AI 에이전트 설계 및 구현` 근거로 사용한다.

방어 체계는 외부 LLM API 없이 동작하는 정책 기반, 점수 기반, 시나리오 기반 multi-agent 구조다. 공격 에이전트가 만드는 MAVLink 명령 위조, heartbeat/fail-safe 유도, EW 링크 저하, protocol integrity alert를 같은 Dashboard/Router/MAVLink 관측면에서 탐지하고, 사전 정의된 safe playbook으로 대응한다.

모든 대응은 폐쇄형 Docker 테스트베드(`LOCAL_DOCKER_TESTBED_ONLY`)에서만 수행된다. 실제 RF, 실제 항공기, 외부 네트워크 대상으로 확장하지 않는다.

```text
DefensePolicyAgent
  -> DefenseDetectionAgent
  -> DefenseResponseAgent
  -> DefenseRecoveryAgent

DefenseOrchestrator가 4개 Agent를 실행하고 queue/event bus를 연결한다.
```

## 1. 보고서 관점 요약

| 보고서 항목 | 본 구현에서 보여주는 내용 |
|---|---|
| 공격 시나리오 대응 방어 아키텍처 | 공격 벡터별 탐지 근거, 대응 playbook, 복구 확인 절차 |
| AI 에이전트 설계 및 구현 | Policy/Detection/Response/Recovery 4-Agent 협력 구조 |
| 구현 및 테스트 결과 | Dashboard `DEF` 로그, 컨테이너 로그, JSON incident report, 단위 테스트 |
| 재현성 | Docker Compose profile 기반 실행, 로컬 API/로그 확인 가능 |

핵심 증거는 세 가지다.

1. Dashboard LOG에 남는 `DEF` Agent 이벤트
2. `output/defense_incident_report.json`
3. `output/defense_policy_recommendations.json`

## 2. 구성 요약

| 항목 | 내용 |
|---|---|
| 컨테이너 | `dah-defense` |
| 실행 모듈 | `python -m defense_agents.orchestrator` |
| 관측 | Dashboard `/api/live`, Router `/api/ticn/status`, MAVLink UDP `14551`, agent event |
| 로그 | Dashboard LOG에 `agent_type=DEF`, `platform_id=DEF-001`로 기록 |
| 정책 파일 | `defense_agents/policies/default_policy.json` |
| 산출물 | `output/defense_incident_report.json`, `output/defense_policy_recommendations.json` |

## 3. 통신 구조

현재 `docker-compose.yml` 기준 주요 통신 경로다.

```text
UAV-001 mock UAV (172.31.50.10)
  MAVLink telemetry -> dah-companion:14550
  MAVLink command   <- UAV:14551

dah-companion (172.31.50.30)
  MAVLink -> JSON 변환
  JSON telemetry -> dah-gcs:14555
  recon mirror    -> dah-recon 172.31.50.40:14550

dah-gcs
  telemetry fan-out:
    -> dah-dashboard:14571
    -> telemetry-collector:14541
    -> tactical-router:14560
  command relay:
    tactical-router:14562 -> dah-companion:14552 -> UAV:14551

tactical-router
  HTTP API :8080, host exposed as localhost:8084
  EW jammer UDP :14590
  Upper C2/BMS -> mission-control:14545

dah-dashboard
  web/API gateway: http://localhost:9000
  live API: /api/live

dah-defense (172.31.50.60)
  polls Dashboard /api/live
  polls Router /api/ticn/status
  monitors MAVLink UDP 14551
  emits DEF events -> dah-dashboard:14571
  writes reports -> /app/output
```

`dah-defense`는 `dah-net`과 `ops_net`에 연결된다. 따라서 UAV 명령면과 GCS/Router/Dashboard 상태면을 동시에 관측할 수 있다.

## 4. Agent 역할

| Agent | 파일 | 역할 | 출력 |
|---|---|---|---|
| `DefensePolicyAgent` | `policy_agent.py` | 자산, 허용 SYS_ID, 명령 allowlist, threshold, lab-only surface 정책 로드 | policy event |
| `DefenseDetectionAgent` | `detection_agent.py` | command injection, replay, GPS spoofing, jamming, fail-safe, protocol integrity 탐지 | `Threat` |
| `DefenseResponseAgent` | `response_agent.py` | threat 시나리오에 맞는 safe playbook 선택 및 실행 | `DefenseAction` |
| `DefenseRecoveryAgent` | `recovery_agent.py` | 대응 후 Dashboard 상태 확인, incident/policy report 저장 | JSON report |
| `DefenseOrchestrator` | `orchestrator.py` | 4개 Agent 실행, heartbeat, queue와 event bus 연결 | orchestrator event |

내부 데이터 흐름은 아래처럼 단순하게 유지한다.

```text
DetectionAgent -> threat_queue -> ResponseAgent
ResponseAgent  -> recovery_queue -> RecoveryAgent
All agents     -> DashboardEventBus -> Dashboard LOG
```

이 구조는 탐지, 대응, 복구 판단을 분리해 보고서에서 각 단계의 책임과 증거를 설명하기 쉽다.

## 5. 공격-방어 매핑

| 공격/이상 징후 | 탐지 근거 | 방어 시나리오 | 대응 |
|---|---|---|---|
| Heartbeat 상태 위조 | Dashboard event, mission phase 변화 | `FAILSAFE_INDUCTION` | `HOLD_POSITION`, `FORCE_RTL` |
| Heartbeat timeout 유도 | heartbeat gap, link loss | `FAILSAFE_INDUCTION` | `HOLD_POSITION`, `FORCE_RTL` |
| 위조 `COMMAND_LONG NAV_LAND` | 비허용 SYS_ID, 제한 명령 | `FORCED_LAND_ATTEMPT` | `BLOCK_COMMAND`, `FORCE_RTL` |
| 비허용 MAVLink command | command allowlist 위반 | `COMMAND_INJECTION`, `UNKNOWN_COMMAND` | `BLOCK_COMMAND` |
| MAVLink replay | sequence rollback | `REPLAY_ATTACK` | `BLOCK_COMMAND`, `SAFE_MODE` |
| GPS spoofing | `gps_spoofed`, 비정상 implied speed | `GPS_SPOOFING` | `INS_FALLBACK`, `HOLD_POSITION` |
| EW 재밍/링크 저하 | Router/Dashboard `loss_pct` | `EW_LINK_DEGRADATION`, `JAMMING_CRITICAL` | `FREQ_HOP`, `HOLD_POSITION` |
| protocol tamper alert | Dashboard agent event | `PROTOCOL_FRAME_INTEGRITY` | `BLOCK_COMMAND`, monitoring |

공격 에이전트가 취약점 기반 이벤트를 만들면, 방어 에이전트는 같은 이벤트를 관측해 탐지, 대응, 복구 로그를 남긴다. 보고서에는 공격 로그와 방어 로그를 같은 timeline으로 배치하면 공격-방어 연계성이 명확해진다.

## 6. 정책 기준선

기본 정책 파일은 `defense_agents/policies/default_policy.json`이다.

| 항목 | 값 |
|---|---|
| 보호 대상 | `UAV-001`, `172.31.50.10:14551`, `sys_id=1` |
| 허용 GCS SYS_ID | `255` |
| 허용 명령 | `MAV_CMD_NAV_WAYPOINT`, `MAV_CMD_NAV_TAKEOFF`, `MAV_CMD_NAV_RETURN_TO_LAUNCH` |
| 제한 명령 | `MAV_CMD_NAV_LAND`, `MAV_CMD_DO_SET_MODE` |
| 재밍 임계값 | warning `30%`, critical `50%` |
| GPS spoofing 기준 | `gps_spoofed=true` 또는 implied speed `>= 300 km/h` |
| heartbeat critical gap | `5s` |
| lab-only surface | recon mirror, router API, attack event port |

정책은 탐지와 대응의 기준선이다. 예를 들어 `MAV_CMD_NAV_LAND`는 제한 명령이므로, 허용되지 않은 source에서 LAND가 들어오면 `FORCED_LAND_ATTEMPT`로 분류한다.

## 7. 탐지 로직

| 입력 | 방식 | 주요 판단 |
|---|---|---|
| MAVLink UDP `14551` | socket monitor | source SYS_ID, command allowlist, sequence rollback |
| Dashboard `/api/live` | 3초 polling | UAV 상태, GPS spoofing, mission phase, agent event |
| Router `/api/ticn/status` | 5초 polling | TMMR/TICN 손실률, 재밍 영향 |

대표 탐지 조건:

| 조건 | 시나리오 |
|---|---|
| `src_id`가 `allowed_sys_ids` 밖 | `COMMAND_INJECTION` |
| 제한 명령 `MAV_CMD_NAV_LAND` 관측 | `FORCED_LAND_ATTEMPT` |
| sequence number rollback | `REPLAY_ATTACK` |
| `gps_spoofed=true` 또는 implied speed 임계 초과 | `GPS_SPOOFING` |
| `loss_pct >= 30` | `EW_LINK_DEGRADATION` |
| `loss_pct >= 50` | `JAMMING_CRITICAL` |
| heartbeat gap + link degradation/fail-safe phase | `FAILSAFE_INDUCTION` |
| Dashboard protocol integrity alert | `PROTOCOL_FRAME_INTEGRITY` |

탐지 결과는 `Threat(threat_id, scenario, severity, confidence, reason, evidence, recommended_playbook)` 형태로 만들어져 `threat_queue`에 들어간다.

## 8. 대응 Playbook

`DefenseResponseAgent`는 임의 대응을 만들지 않고 정책 파일에 등록된 playbook만 수행한다.

| Playbook | 동작 | 범위 |
|---|---|---|
| `BLOCK_COMMAND` | command trust gate 차단 로그 기록 | 이벤트 기반 |
| `FORCE_RTL` | lab UAV에 `MAV_CMD_NAV_RETURN_TO_LAUNCH` 전송 | Docker UAV only |
| `SAFE_MODE` | lab UAV safe mode 전환 시도 | Docker UAV only |
| `FREQ_HOP` | Router `/api/ticn/clear`로 VHF/UHF clear | Router simulation only |
| `INS_FALLBACK` | GPS 신뢰도 저하 및 INS fallback 기록 | 이벤트 기반 |
| `HOLD_POSITION` | 위치 유지 권고 로그 기록 | 이벤트 기반 |
| `IGNORE_AND_MONITOR` | 모니터링 지속 | 이벤트 기반 |

`FORCE_RTL`, `SAFE_MODE`도 실제 기체가 아니라 Docker lab UAV에만 전달된다.

## 9. 복구 및 산출물

`DefenseRecoveryAgent`는 대응 후 Dashboard 상태를 다시 조회해 복구 여부와 evidence를 저장한다.

| 대응 | 확인 기준 |
|---|---|
| `FREQ_HOP` | `loss_pct < jamming_loss_critical` |
| `INS_FALLBACK` | `gps_spoofed=false` |
| `FORCE_RTL`, `SAFE_MODE`, `HOLD_POSITION` | `mission_phase`와 UAV 상태를 evidence로 기록 |

산출물:

```text
output/defense_incident_report.json          # incident timeline, threat, action, recovery observation
output/defense_policy_recommendations.json  # 반복 탐지 유형 기반 정책 개선 제안
```

Dashboard 이벤트는 아래 source 값으로 구분된다.

```text
POLICY-AGENT
DETECTION-AGENT
RESPONSE-AGENT
RECOVERY-AGENT
DEFENSE-ORCHESTRATOR
```

## 10. 실행

기본 테스트베드와 방어 에이전트 실행:

```powershell
docker compose up -d --build
docker compose --profile defense-lab up -d --build dah-defense
docker logs -f dah-defense
```

대시보드:

```text
http://localhost:9000
```

호스트 빠른 점검:

```powershell
python -m defense_agents.orchestrator --once
```

공격 체인과 함께 검증할 때는 방어 Agent를 먼저 켠 뒤 공격 Agent를 실행한다.

```powershell
$env:ENABLE_LAB_ATTACKS="true"
python -m attack_agent.kill_chain --stage follow-up --objective FAILSAFE_INDUCTION --execute --max-steps 1
```

## 11. 검증

단위 테스트:

```powershell
python -m unittest defense_agents.tests.test_defense_agents
```

상태 API:

```powershell
Invoke-RestMethod http://localhost:9000/api/live
Invoke-RestMethod http://localhost:8084/api/ticn/status
```

확인 항목:

| 위치 | 확인 내용 |
|---|---|
| Dashboard LOG | `DEF` 이벤트가 탐지 -> 대응 -> 복구 순서로 남는지 |
| `dah-defense` 로그 | `[DEFENSE][...]` 이벤트 출력 |
| `output/defense_incident_report.json` | 사고 timeline과 대응 결과 |
| `output/defense_policy_recommendations.json` | 정책 개선안 |

## 12. 파일 구조

```text
defense_agents/
  orchestrator.py
  policy_agent.py
  detection_agent.py
  response_agent.py
  recovery_agent.py
  Dockerfile
  policies/default_policy.json
  shared/event_bus.py
  shared/models.py
  shared/policy_loader.py
  shared/utils.py
  tests/test_defense_agents.py
```

참고 파일: `attack_agent/README_CHAIN.md`, `docs/defense_multi_agent_architecture.md`
