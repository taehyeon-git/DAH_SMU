# DAH 2026 Attack Agent 실행 체인

`attack_agent`는 UAV/UGV 통신 테스트베드에서 정찰 결과를 기반으로 fail-safe 유도 공격을 계획하고, 폐쇄형 Docker 실험망 안에서만 실행/검증하는 공격 에이전트 체인이다.

공격 실행은 실제 RF, 외부망, 실제 항공 장비가 아니라 Docker 내부의 MAVLink/UDP lab event와 TMMR/TICN 시뮬레이션 이벤트로 제한된다.

---

## 1. 공통 전제

| 항목 | 내용 |
|---|---|
| 위협모델 | 블랙박스 기반 외부 관측. Dashboard API, MAVLink mirror, link metric만 사용 |
| 표적 | `UAV-001`, MAVLink `sys_id=1` |
| 주요 목표 | UAV fail-safe 상태 유도, link degradation 유발, 실행 전후 telemetry 검증 |
| 실행 범위 | 로컬 Docker 테스트베드 전용 |
| 기본 모드 | dry-run. 실제 lab event 전송은 `--execute` + `ENABLE_LAB_ATTACKS=true` 필요 |

---

## 2. 공격 체인 아키텍처

```text
ReconAgent
  -> InitialAccessAgent
  -> FollowUpAttackAgent
  -> Adapter
```

| 단계 | 구현 | 입력 | 출력 | 역할 |
|---|---|---|---|---|
| 1 | `attack_agent/recon.py` | Dashboard API, Failsafe API, MAVLink mirror | `output/stage_1_recon.json` | 표적/프로토콜/link/fail-safe 정찰값 정규화 |
| 2 | `attack_agent/agents/initial_access_agent.py` | `stage_1_recon.json` | `stage_2_initial_access.json`, `stage_2_attack_graph.json` | 자산, edge, GCS command path, 공격 후보 생성 |
| 3 | `attack_agent/agents/followup_attack_agent.py` | `stage_2_initial_access.json` | `stage_3_attack_plan.json`, `stage_3_execution_report.json` | 공격 계획 생성, lab 실행, 전후 상태 검증 |
| 4 | `attack_agent/adapters/` | `AttackStep` | Dashboard event, UAV/router event | 계획과 실제 이벤트 전송 로직 분리 |

각 단계는 파일 기반으로 연결된다. 1단계 산출물이 2단계 입력이 되고, 2단계 산출물이 3단계 입력이 된다.

---

## 3. 통신 경로

```text
ReconAgent
  -> dashboard /api/live
  -> dashboard /api/failsafe
  -> dah-recon:14550              # Companion MAVLink mirror 수동 청취

FollowUpAttackAgent
  -> dashboard /api/live
  -> dashboard /api/agent-event
  -> UAV-001:14551                # MAVLink lab event
  -> tactical-router:14590        # TMMR/TICN EW simulation
```

| 대상 | 용도 |
|---|---|
| `http://localhost:9000` | Dashboard 상태/API 확인 |
| `dah-dashboard:9000` | 컨테이너 내부 Dashboard API |
| `dah-recon:14550` | MAVLink mirror 수동 정찰 |
| `dah-uav:14551` | MAVLink heartbeat/status/command lab event |
| `tactical-router:14590` | TMMR/TICN 링크 손실 시뮬레이션 |
| `http://localhost:8084/api/ticn/status` | Router link metric 확인 |

---

## 4. ReconAgent 세부 설계

`ReconAgent`는 공격 후보를 직접 만들지 않는다. 정찰값을 표준 `IntelDocument`로 정리하고, `InitialAccessAgent`가 후보 선택을 담당한다.

| Phase | 내용 | 주요 근거 |
|---|---|---|
| P0 API recon | Dashboard 상태와 fail-safe 기준 수집 | `/api/live`, `/api/failsafe` |
| P1 passive collect | Companion MAVLink mirror 수동 청취 | UDP `14550` |
| P2 confidence scoring | 관측 신호별 신뢰도 계산 | frame count, heartbeat, telemetry freshness |
| P3 revalidation | LOW confidence 자산 단기 재검증 | 짧은 재수집 |
| P4 recon tags | 후속 분석용 태그 생성 | `unsigned_frames`, `hb_timeout_sec`, `sys_id`, `ticn_loss_pct` |
| P5 artifact save | JSON 산출물 저장 | `stage_1_recon.json`, `intel_handoff.json` |

대표 정찰 항목:

| 항목 | 의미 |
|---|---|
| `signed_frames`, `unsigned_frames` | MAVLink 서명 적용 여부 판단 |
| `sys_id` | 표적 UAV system id |
| `hb_timeout_sec`, `hb_interval_sec` | heartbeat timeout 기반 fail-safe 조건 |
| `ticn_loss_pct`, `drop_rate_comm` | link degradation 공격 후보 근거 |
| `command_acks` | 명령 수용 여부 추정 근거 |

---

## 5. InitialAccessAgent 세부 설계

`InitialAccessAgent`는 정찰 결과를 이용해 GCS 모델과 공격 후보를 만든다.

```text
discover_api_surface()
  -> map_assets()
  -> map_edges()
  -> reconstruct_gcs()
  -> build_candidate_actions()
```

| 분석 | 내용 |
|---|---|
| API surface | Dashboard, Router 등 관측 가능한 API와 상태 필드 정리 |
| Asset map | UAV, GCS, Companion, Router, Dashboard, Recon module 등 자산화 |
| Edge map | telemetry ingress, command egress, recon mirror, EW simulation edge 정리 |
| GCS reconstruction | UAV 명령 경로와 weak point 모델링 |
| Candidate actions | A-1~B-2 fail-safe 유도 후보와 tamper 후보 생성 |

현재 모델링된 핵심 command path:

| 경로 | 내용 |
|---|---|
| Telemetry ingress | `UAV-001 -> dah-companion -> dah-gcs -> dah-dashboard` |
| Dashboard command path | `dah-dashboard /api/command -> MAVLink COMMAND_LONG -> UAV-001:14551` |
| Upper C2 command path | `mission-control -> tactical-router:14546 -> dah-gcs:14562 -> dah-companion:14552 -> UAV-001:14551` |
| EW simulation path | `attack adapter -> tactical-router:14590` |

---

## 6. FollowUpAttackAgent 세부 설계

`FollowUpAttackAgent`는 `AttackPlan`을 만들고, dry-run 또는 명시 실행으로 전후 상태를 검증한다.

| 기능 | 내용 |
|---|---|
| 계획 생성 | `planner/plan_builder.py`에서 objective별 후보 정렬 |
| 실행 제어 | 기본 dry-run, `--execute` + `ENABLE_LAB_ATTACKS=true`일 때만 lab event 전송 |
| 이벤트 기록 | `/api/agent-event`로 `STEP_STARTED`, `STEP_SUCCEEDED`, `STEP_FAILED` 기록 |
| 성공 판정 | Dashboard overlay가 아니라 GCS 원본 telemetry와 실제 link metric 기준 |
| 체인 전략 | `FAILSAFE_INDUCTION`은 `fallback_until_success` |

성공 판정 기준:

| 공격 유형 | 판정 |
|---|---|
| A-1/A-2 | UAV가 공중 상태를 유지하면서 위치 이동 80m 미만이면 LOITER로 추정 |
| A-3 | 실행 전후 고도 차이가 50m 이상이면 성공 |
| B-1/B-2 | 실제 `loss_pct`가 목표 손실률 이상이면 성공 |
| Tamper | 합성 프레임 검증 실패 alert가 생성되면 성공 |

---

## 7. Adapter

| Adapter | 담당 벡터 | 대상 |
|---|---|---|
| `MavlinkInjectorAdapter` | A-1, A-2, A-3 | `dah-uav:14551` |
| `JammerAdapter` | B-1, B-2 | `tactical-router:14590` |
| `TamperAdapter` | protocol integrity test | 로컬 합성 프레임 |

Adapter는 계획 단계와 실제 이벤트 전송 단계를 분리한다. 이 구조 덕분에 같은 `AttackPlan`을 dry-run evidence로 저장하거나, lab 환경에서만 명시 실행할 수 있다.

---

## 8. Fail-safe 공격 순서

`FAILSAFE_INDUCTION`은 `attack_agent/planner/plan_builder.py`의 `FAILSAFE_CHAIN_ORDER` 순서대로 실행된다. A-3가 가장 강한 벡터지만, 체인 실행 순서는 아래 고정 순서를 따른다.

| 순서 | 벡터 | 공격 방식 | 유발/관측 |
|---:|---|---|---|
| 1 | A-1 `MAVLINK_STATUS_SPOOF` | 위조 heartbeat로 `system_status=CRITICAL` 주입 | LOITER 추정, `GCS_STATUS_SPOOFED` event |
| 2 | A-2 `HB_TIMEOUT_INDUCTION` | heartbeat timeout 조건 유도 | LOITER 추정, `HB_SUPPRESSED` event |
| 3 | A-3 `MAVLINK_COMMAND_INJECTION` | 위조 `COMMAND_LONG(NAV_LAND)` 주입 | 실제 고도 하강, `COMMAND_INJECTED` event |
| 4 | B-1 `EW_LINK_DEGRADATION_SIM` | VHF/UHF/HF link loss event | `loss_pct` 상승, `EW_LINK_DEGRADED` event |
| 5 | B-2 `EW_STEALTH_DEGRADATION_SIM` | 탐지 회피형 손실률 band 조준 | `loss_pct` 목표치 도달 |

전략은 `fallback_until_success`다. 앞 단계가 실패하면 다음 단계로 넘어가고, 한 단계가 성공하면 남은 공격은 실행하지 않는다.

---

## 9. 실행

테스트베드 실행:

```powershell
docker compose up -d --build
```

단계별 실행:

```powershell
python -m attack_agent.kill_chain --stage recon
python -m attack_agent.kill_chain --stage initial-access
python -m attack_agent.kill_chain --stage follow-up --objective FAILSAFE_INDUCTION --max-steps 5
```

전체 체인 dry-run:

```powershell
python -m attack_agent.kill_chain --stage all --objective FAILSAFE_INDUCTION --max-steps 5
```

전체 체인 lab 실행:

```powershell
$env:ENABLE_LAB_ATTACKS="true"
python -m attack_agent.kill_chain --stage all --objective FAILSAFE_INDUCTION --execute --max-steps 5
```

단일 벡터만 확인하려면 `--max-steps 1`을 사용한다.

---

## 10. 확인

API:

```powershell
Invoke-RestMethod http://localhost:9000/api/live
Invoke-RestMethod http://localhost:8084/api/ticn/status
```

주요 산출물:

| 파일 | 의미 |
|---|---|
| `output/stage_1_recon.json` | 정찰 정규화 결과 |
| `output/stage_2_initial_access.json` | 자산, edge, GCS 모델, 공격 후보 |
| `output/stage_2_attack_graph.json` | 공격 그래프 |
| `output/stage_3_attack_plan.json` | 실행 계획 |
| `output/stage_3_execution_report.json` | 실행 전후 관측 및 성공/실패 판정 |

대시보드 확인 항목:

- LOG 탭: `Attack Agent`, `STEP_STARTED`, `STEP_SUCCEEDED`, `STEP_FAILED`
- Mission State: `LOITER`, `FAILSAFE_*`, `LANDING` 등 단계 변화
- Link Status: TMMR waveform, RSSI, LQ, packet loss 변화
- UAV 상태: 고도, 속도, 좌표, 링크 품질 변화

---

## 11. 안전 범위

- 기본 실행은 dry-run이다.
- 실제 lab event 전송은 `--execute`와 `ENABLE_LAB_ATTACKS=true`가 모두 필요하다.
- 대상 host/port는 `attack_agent/core/safety.py` allowlist로 제한된다.
- 실제 RF 재밍, 외부망 스캔, credential 접근, 악성코드, raw socket은 구현하지 않는다.
- EW 재밍은 `tactical-router:14590`에 전달되는 JSON 시뮬레이션 이벤트다.
- `dah-attack`은 현재 compose 핵심 서비스가 아니다. 공격 실행은 `python -m attack_agent.kill_chain` CLI를 사용한다.

---

## 12. 코드 위치

| 영역 | 경로 |
|---|---|
| 공격 CLI | `attack_agent/kill_chain.py` |
| 정찰 | `attack_agent/recon.py` |
| 초기 접근 분석 | `attack_agent/agents/initial_access_agent.py` |
| GCS 모델/후보 생성 | `attack_agent/initial_access/gcs_reconstructor.py` |
| 공격 planner | `attack_agent/planner/plan_builder.py` |
| 후속 공격 실행 | `attack_agent/agents/followup_attack_agent.py` |
| MAVLink/EW/tamper adapter | `attack_agent/adapters/` |
