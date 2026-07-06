# DAH_SMU 방어 AI Agent 4분할 구조

이 문서는 `DAH_SMU`의 블루팀 방어 체계를 4개 AI-like Agent로 분리한 구조를 설명한다.

```text
Defense Policy Agent
  -> Defense Detection Agent
  -> Defense Response Agent
  -> Defense Recovery Agent
```

외부 LLM API는 사용하지 않는다. 판단은 정책 기반, 점수 기반, 시나리오 기반으로 수행하며 모든 대응은 `LOCAL_DOCKER_TESTBED_ONLY` 범위의 시뮬레이션 동작으로 제한된다.

## 1. Agent 역할

| Agent | 단계 | 역할 | 주요 출력 |
|---|---|---|---|
| `DefensePolicyAgent` | 예방/정책 | 정상 자산, SYS_ID, 허용 명령, 공격면 노출 정책 로드 | policy snapshot event |
| `DefenseDetectionAgent` | 탐지/분석 | MAVLink command, replay, GPS spoofing, jamming, fail-safe, protocol integrity alert 분석 | `Threat` object |
| `DefenseResponseAgent` | 대응/차단 | 사전 정의된 안전 playbook만 실행 | `DefenseAction` object |
| `DefenseRecoveryAgent` | 복구/개선 | 상태 정상화 확인, 사고 보고서 및 정책 개선안 저장 | JSON reports |
| `DefenseOrchestrator` | 실행 관리 | 4개 Agent 실행, queue 연결, heartbeat 전송 | orchestrator event |

## 2. 공격-방어 매핑

| 공격 흐름 | 방어 Agent | 방어 관점 |
|---|---|---|
| `ReconAgent` | `DefensePolicyAgent` | Recon mirror, Router API, attack event port의 lab-only 노출 정책 점검 |
| `InitialAccessAgent` | `DefenseDetectionAgent` | 비정상 SYS_ID, command injection, replay, API/GCS 이상 상태 탐지 |
| `FollowUpAttackAgent` | `DefenseResponseAgent` | 안전 playbook으로 RTL, SAFE_MODE, FREQ_HOP, INS fallback, HOLD 권고 수행 |
| 반복/지속 교란 | `DefenseRecoveryAgent` | loss_pct, gps_spoofed, mission_state 정상화 확인 및 정책 개선 제안 |

## 3. 탐지 시나리오

| 시나리오 | 조건 | 권고 playbook |
|---|---|---|
| `COMMAND_INJECTION` | 허용되지 않은 SYS_ID의 `COMMAND_LONG` | `BLOCK_COMMAND`, 필요 시 `FORCE_RTL` |
| `FORCED_LAND_ATTEMPT` | 비정상 출처 또는 허용 목록 밖 `MAV_CMD_NAV_LAND` | `BLOCK_COMMAND`, `FORCE_RTL` |
| `UNKNOWN_COMMAND` | 허용 명령 목록 밖 command | `BLOCK_COMMAND`, `IGNORE_AND_MONITOR` |
| `REPLAY_ATTACK` | `seq <= previous_seq` | `BLOCK_COMMAND`, `SAFE_MODE` |
| `GPS_SPOOFING` | `gps_spoofed=true` 또는 implied speed 임계값 초과 | `INS_FALLBACK`, `HOLD_POSITION` |
| `EW_LINK_DEGRADATION` | `loss_pct >= jamming_loss_warn` | `IGNORE_AND_MONITOR` |
| `JAMMING_CRITICAL` | `loss_pct >= jamming_loss_critical` | `FREQ_HOP`, `HOLD_POSITION` |
| `FAILSAFE_INDUCTION` | heartbeat gap + link degradation/fail-safe 상태 동시 관측 | `HOLD_POSITION`, `FORCE_RTL` |
| `PROTOCOL_FRAME_INTEGRITY` | Dashboard agent event에서 protocol integrity alert 관측 | `BLOCK_COMMAND`, `IGNORE_AND_MONITOR` |

## 4. Playbook

| Playbook | 구현 방식 | 안전 범위 |
|---|---|---|
| `BLOCK_COMMAND` | Dashboard-compatible DEF event로 command trust gate 차단 기록 | 이벤트 기반 |
| `FORCE_RTL` | 사전 정의된 `MAV_CMD_NAV_RETURN_TO_LAUNCH`를 lab UAV로 전송 | Docker lab UAV only |
| `SAFE_MODE` | 사전 정의된 safe mode 전환 명령을 lab UAV로 전송 | Docker lab UAV only |
| `FREQ_HOP` | Router `/api/ticn/clear`로 VHF/UHF jam 상태 초기화 | Router simulation only |
| `INS_FALLBACK` | GPS 신뢰도 저하 및 INS fallback event 기록 | 이벤트 기반 |
| `HOLD_POSITION` | 임의 명령 생성 없이 hold-position 권고 event 기록 | 이벤트 기반 |
| `IGNORE_AND_MONITOR` | 대응 없이 모니터링 지속 | 이벤트 기반 |

## 5. 실행 방법

기본 테스트베드 실행:

```powershell
docker compose up -d --build
```

방어 4-Agent 실행:

```powershell
docker compose --profile defense-lab up --build dah-defense
```

호스트에서 빠른 점검:

```powershell
python -m defense_agents.orchestrator --once
```

장시간 실행:

```powershell
python -m defense_agents.orchestrator
```

## 6. Dashboard 이벤트

모든 방어 Agent는 기존 Dashboard 호환 이벤트 형식을 유지한다.

```json
{
  "platform_type": "AGENT",
  "agent_type": "DEF",
  "platform_id": "DEF-001",
  "source": "DETECTION-AGENT",
  "message": "JAMMING_CRITICAL 탐지",
  "detail": "TICN loss_pct critical threshold 초과",
  "level": "warn",
  "status": "THREAT",
  "time": "13:00:01"
}
```

대표 source:

```text
POLICY-AGENT
DETECTION-AGENT
RESPONSE-AGENT
RECOVERY-AGENT
DEFENSE-ORCHESTRATOR
```

## 7. 산출물

Recovery Agent는 아래 파일을 생성한다.

```text
output/defense_incident_report.json
output/defense_policy_recommendations.json
```

`defense_incident_report.json`에는 incident id, 시나리오, timeline, playbook action, recovery observation이 저장된다.

`defense_policy_recommendations.json`에는 반복 탐지 유형을 근거로 한 정책 개선 제안이 저장된다.

## 8. 실제 방산 환경 구현 가능성

현재 구현은 실제 군 장비, 실제 RF, 실제 외부 네트워크와 연결하지 않는다. 실제 환경으로 확장하려면 다음이 필요하다.

| 영역 | 실제 환경 추가 요구 |
|---|---|
| 정책 | 인증된 GCS/Companion ID, mission-state 기반 command authorization |
| 탐지 | 서명 검증, 시간 동기화, radio telemetry, link-layer sensor fusion |
| 대응 | 승인 기반 playbook, 안전성 검토, 조종권 이관 절차 |
| 복구 | 사고 보고 체계, 임무 재개 승인, 포렌식 로그 보존 |

따라서 이 구현은 대회/연구용 폐쇄형 Docker 시뮬레이션에서 탐지·차단·복구 흐름을 보여주기 위한 구조다.

