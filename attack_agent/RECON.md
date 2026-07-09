# Passive MAVLink Recon

> `S11-RECON` · 저권한 수동 정찰 · DAH Docker 폐쇄 실험망 전용

`ReconAgent`는 UAV/UGV 통신망에서 MAVLink mirror와 Dashboard 상태값을 수집해 공격/방어 에이전트가 함께 쓰는 정찰 인텔리전스를 만든다. 실제 군 장비, 실제 RF, 외부 네트워크는 사용하지 않는다.

이 문서는 다음 세 가지를 설명한다.

| 항목 | 내용 |
|---|---|
| 공격 시나리오 근거 | MAVLink 무서명 트래픽, heartbeat/status, command ack, TMMR/TICN 링크 품질 |
| AI Agent 구조 | `ReconAgent -> InitialAccessAgent -> FollowUpAttackAgent` |
| 방어 연계 | `DefensePolicyAgent -> Detection -> Response -> Recovery` |

---

## 1. 핵심 통신 구조

```text
UAV-001(dah-uav)
  -> dah-companion
  -> dah-gcs
  -> tactical-router
  -> mission-control

dah-companion
  -> dah-recon:14550        # MAVLink mirror, 수동 정찰

dah-dashboard
  -> /api/live              # 현재 상태
  -> /api/failsafe          # fail-safe 정책
  -> /api/agent-event       # 공격/방어 에이전트 로그
```

주요 포트:

| 대상 | 포트 | 용도 |
|---|---:|---|
| `dah-uav` | `14550/udp` | MAVLink telemetry |
| `dah-uav` | `14551/udp` | MAVLink command |
| `dah-companion` | `14550/udp` | UAV telemetry 수신 |
| `dah-companion` | `14552/udp` | GCS direct command |
| `dah-recon` | `14550/udp` | Companion mirror 수동 청취 |
| `dah-gcs` | `14555/udp` | Companion JSON telemetry |
| `tactical-router` | `14590/udp` | TMMR/TICN jamming simulation |
| `dah-dashboard` | `8080/tcp` | API/UI, 호스트는 `localhost:9000` |

`dah-recon`은 C2 경로에 끼어들지 않는다. 정찰 컨테이너가 꺼져도 `UAV -> Companion -> GCS -> Router -> Mission Control` 흐름은 유지된다.

---

## 2. ReconAgent 역할

대상은 `UAV-001(SYS_ID=1, 172.31.50.10)`이다. `UDP:14550` MAVLink mirror를 수동 청취하고, 필요 시 Dashboard `GET /api/live`, `GET /api/failsafe`만 조회한다. 일반 UDP bind만 사용하므로 raw socket과 `CAP_NET_RAW`는 필요 없다.

정찰 파이프라인:

```text
P0 API 기준값 수집
P1 MAVLink mirror 청취
P2 신뢰도 채점
P3 LOW 신뢰도 재검증
P4 recon_tags / analysis_hints 생성
P5 JSON 저장
```

### 2.1 수집 메시지

| MAVLink 메시지 | 활용 |
|---|---|
| `HEARTBEAT` | 시스템 상태, 모드, heartbeat spoof/timeout 근거 |
| `SYS_STATUS` | 배터리, packet drop, 통신 오류 |
| `GLOBAL_POSITION_INT` | 위치, 고도, 속도, 공격 전후 상태 비교 |
| `COMMAND_LONG` | GCS/상위 C2 명령 흐름 관측 |
| `COMMAND_ACK` | 명령 수락/거부/진행 상태 추정 |
| `MISSION_CURRENT`, `MISSION_COUNT` | 임무 진행 상태와 waypoint 흐름 |

Dashboard API에서는 UAV/UGV 상태, `ticn_loss_pct`, `latency_ms`, `loss_critical_pct`, `hb_timeout_sec`, `failsafe_action`을 가져온다. 이 값은 공격 후보 생성과 방어 임계값 비교에 같이 쓰인다.

### 2.2 정찰 신호

| 신호 | 의미 | 후속 활용 |
|---|---|---|
| `MAVLINK_UNSIGNED_TRAFFIC` | MAVLink 서명 프레임 미관측 | spoof/injection 후보 |
| `SYS_ID_DISCOVERED` | 표적 SYS_ID 식별 | 공격 대상 및 방어 allowlist |
| `HEARTBEAT_OBSERVED` | heartbeat/status 관측 | heartbeat spoof/timeout 조건 |
| `COMMAND_ACK_OBSERVED` | command ack 흐름 관측 | command injection 가능성 평가 |
| `LINK_METRICS_AVAILABLE` | 손실률, 지연, LQ 관측 | EW link degradation 조건 |
| `FAILSAFE_POLICY_OBSERVED` | fail-safe 임계값 확보 | 공격 성공 판정 및 방어 playbook |

### 2.3 신뢰도 기준

정찰 신뢰도는 메시지 반복성, 위치 샘플 반복성, 물리 일관성, 메시지 간 교차 검증, 프레임 무결성, freshness를 합산해 계산한다.

```text
HIGH   >= 0.80  재현 가능한 정찰 근거
MEDIUM >= 0.50  후보 생성은 가능하나 추가 관측 권장
LOW    <  0.50  지연, 스푸핑, 불완전 관측 가능성
```

LOW 신뢰도 자산은 짧게 재청취해 더 좋은 관측값이 있으면 병합한다.

---

## 3. 공격 체인

```text
ReconAgent -> InitialAccessAgent -> FollowUpAttackAgent
```

| 단계 | 역할 | 주요 출력 |
|---|---|---|
| `ReconAgent` | 표적, 프로토콜, 링크 품질, fail-safe 정책 정리 | `stage_1_recon.json` |
| `InitialAccessAgent` | 공격 표면과 후보 액션 생성 | `stage_2_initial_access.json`, `stage_2_attack_graph.json` |
| `FollowUpAttackAgent` | 계획 수립, dry-run/execute, 실측 성공 판정 | `stage_3_attack_plan.json`, `stage_3_execution_report.json` |

`FAILSAFE_INDUCTION`은 `fallback_until_success` 방식이다. 앞 단계가 성공하면 이후 단계는 실행하지 않는다.

| 순서 | 공격 벡터 | 계열 | 실행 대상 | 성공 판정 |
|---:|---|---|---|---|
| 1 | `MAVLINK_STATUS_SPOOF` | A-1 인증 부재 | `dah-uav:14551` | 위치 이동 80m 미만, LOITER 추정 |
| 2 | `HB_TIMEOUT_INDUCTION` | A-2 인증 부재 | `dah-uav:14551` | heartbeat 공백 후 LOITER 추정 |
| 3 | `MAVLINK_COMMAND_INJECTION` | A-3 인증 부재 | `dah-uav:14551` | 실제 고도 50m 이상 하강 |
| 4 | `EW_LINK_DEGRADATION_SIM` | B-1 링크 계층 | `tactical-router:14590` | 실제 `loss_pct` 목표치 이상 |
| 5 | `EW_STEALTH_DEGRADATION_SIM` | B-2 링크 계층 | `tactical-router:14590` | 목표 band 내 링크 저하 |

이 순서는 영향도 순서가 아니라 현재 mock UAV와 Dashboard에서 실험 성공 가능성이 높은 순서다. `MAVLINK_COMMAND_INJECTION`은 영향도는 가장 크지만 체인에서는 3번째다.

Adapter는 다음처럼 분리된다.

| Adapter | 담당 |
|---|---|
| `MavlinkInjectorAdapter` | heartbeat spoof, heartbeat timeout, command injection |
| `JammerAdapter` | TMMR/TICN link degradation |
| `TamperAdapter` | 네트워크 전송 없는 protocol integrity simulation |

---

## 4. 방어 체계

```text
DefensePolicyAgent -> DefenseDetectionAgent -> DefenseResponseAgent -> DefenseRecoveryAgent
```

| Agent | 역할 |
|---|---|
| `DefensePolicyAgent` | 정상 자산, 허용 SYS_ID, 명령 allowlist, 노출 포트 정책 로드 |
| `DefenseDetectionAgent` | command injection, replay, GPS spoofing, jamming, fail-safe 유도 탐지 |
| `DefenseResponseAgent` | `BLOCK_COMMAND`, `FORCE_RTL`, `FREQ_HOP`, `HOLD_POSITION` 등 playbook 실행 |
| `DefenseRecoveryAgent` | 정상화 확인, 사고 보고서, 정책 개선안 저장 |

공격-방어 매핑:

| 공격/이상징후 | 방어 판단 |
|---|---|
| Heartbeat spoof | 비정상 SYS_ID/status 변화 |
| Heartbeat timeout | heartbeat gap + mission mode 상관 |
| Command injection | 허용되지 않은 출처의 `COMMAND_LONG` |
| EW link degradation | `loss_pct`, RSSI, LQ 임계값 초과 |
| Stealth degradation | UAV 임계값과 방어 임계값 사이 gap 악용 |

방어 이벤트는 Dashboard 로그의 `Defense Agent` 필터에서 확인한다.

---

## 5. 실행

```powershell
# 기본 테스트베드
docker compose up -d --build

# ReconAgent
docker compose --profile recon-lab up --build dah-recon

# 공격 체인 dry-run
python -m attack_agent.kill_chain --objective FAILSAFE_INDUCTION --recon-duration-s 30

# Docker lab 내부 명시 실행
$env:ENABLE_LAB_ATTACKS="true"
python -m attack_agent.kill_chain --objective FAILSAFE_INDUCTION --recon-duration-s 30 --execute

# DefenseAgent
docker compose --profile defense-lab up --build dah-defense
```

Dashboard는 `http://localhost:9000`에서 확인한다.

---

## 6. 산출물과 검증 포인트

| 파일 | 의미 |
|---|---|
| `output/passive_mavlink_intel.json` | 수동 정찰 원본 요약 |
| `output/intel_handoff.json` | 공격 체인 인계용 정찰 문서 |
| `output/stage_1_recon.json` | ReconAgent 표준 출력 |
| `output/stage_2_initial_access.json` | 공격 표면 분석 결과 |
| `output/stage_2_attack_graph.json` | 자산/통신 edge/공격 후보 그래프 |
| `output/stage_3_attack_plan.json` | FollowUpAttackAgent 실행 계획 |
| `output/stage_3_execution_report.json` | 실행 결과와 성공 판정 |
| `output/defense_incident_report.json` | 방어 사고 보고서 |
| `output/defense_policy_recommendations.json` | 방어 정책 개선안 |

Dashboard 로그 필터:

| 필터 | 확인 내용 |
|---|---|
| `Attack Agent` | 공격 단계, 벡터명, 실행 결과, fail-safe 유도 이벤트 |
| `Defense Agent` | 탐지, 대응, 복구 이벤트 |
| `UAV` | 고도, 속도, mode, battery, command 결과 |
| `TEL` | 손실률, RSSI, LQ, TMMR/TICN 상태 |

보고서에 넣을 때는 다음 흐름으로 정리하면 된다.

```text
정찰 근거 -> 후보 공격 생성 -> 단계별 fail-safe 유도 -> Dashboard/산출물 검증 -> 방어 탐지/대응
```

---

## 7. 안전 범위

- DAH Docker 폐쇄 실험망 전용이다.
- 실제 항공기, 실제 RF, 외부 네트워크, 실장비 C2 링크를 대상으로 하지 않는다.
- 기본은 dry-run이다. 실제 실험 이벤트는 `ENABLE_LAB_ATTACKS=true`와 `--execute`가 모두 필요하다.
- EW 재밍은 실제 전파 방해가 아니라 Router의 TMMR/TICN 상태를 바꾸는 JSON/UDP 시뮬레이션이다.
- `MAVLINK_STATUS_SPOOF`, `HB_TIMEOUT_INDUCTION`은 mock UAV의 mode 노출 한계 때문에 위치 이동 휴리스틱을 함께 사용한다.
