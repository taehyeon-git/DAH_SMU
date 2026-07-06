# 정찰 기반 공격 체인

이 패키지는 `DAH_SMU` 정찰 결과를 유지보수 가능한 실험실 전용 3단계 kill chain으로 변환합니다.

```text
ReconAgent
  -> InitialAccessAgent
  -> FollowUpAttackAgent
```

## 3개 Agent 역할

| 단계 | Agent | 입력 | 출력 | 역할 |
|---|---|---|---|---|
| 1 | `ReconAgent` | passive mirror/API 정찰 실행, `output/intel_handoff.json`, `output/passive_mavlink_intel.json` | `output/stage_1_recon.json` | 모든 정찰 이벤트를 실행하고 `recon_tags`/`analysis_hints`를 표준 `IntelDocument`로 정규화 |
| 2 | `InitialAccessAgent` | `output/stage_1_recon.json` | `output/stage_2_initial_access.json`, `output/stage_2_attack_graph.json` | `recon_tags`, API surface, 자산, 통신 edge, GCS 모델을 근거로 후속공격 후보 생성 |
| 3 | `FollowUpAttackAgent` | `output/stage_2_initial_access.json` | `output/stage_3_attack_plan.json`, `output/stage_3_execution_report.json` | `AttackPlan` 생성 후 dry-run 또는 명시 실행 |

세 단계는 파일 기반으로 연결됩니다.  
즉, 1단계 산출물이 2단계 입력이고, 2단계 산출물이 3단계 입력입니다.

역할 경계는 명확합니다. `ReconAgent`는 실행 가능한 후속공격 후보를 만들지 않고, `InitialAccessAgent`가 정찰 태그와 API/GCS 모델을 근거로 `candidate_actions`를 생성합니다.

## 안전 범위

이 코드는 폐쇄형 `DAH_SMU` Docker 실험 환경에서만 사용하도록 설계되었습니다.

- 기본 모드는 `dry-run`입니다.
- 실제 실험 이벤트 실행은 `--execute`와 `ENABLE_LAB_ATTACKS=true`가 모두 있어야 가능합니다.
- 대상 host/port는 로컬 실험망 allowlist로 검증됩니다.
- 인터넷 스캔, 자격 증명 접근, 지속성, 악성코드, raw socket, RF 동작, 실제 MAVLink 공격 도구는 구현하지 않습니다.
- 저수준 tamper 기능은 parser-resilience 테스트를 위한 합성 패킷 변이만 지원합니다.

## 명령어

### 단계별 실행

1단계 ReconAgent:

```powershell
python -m attack_agent.kill_chain --stage recon
```

이 명령은 기본적으로 `dah-recon` 수집 컨테이너를 실행한 뒤 `stage_1_recon.json`까지 생성합니다. 이미 생성된 정찰 JSON만 다시 정규화하려면 아래처럼 실행합니다.

```powershell
python -m attack_agent.kill_chain --stage recon --skip-recon-collection
```

2단계 InitialAccessAgent:

```powershell
python -m attack_agent.kill_chain --stage initial-access
```

3단계 FollowUpAttackAgent dry-run:

```powershell
python -m attack_agent.kill_chain --stage follow-up --objective FAILSAFE_INDUCTION --max-steps 1
```

3단계 FollowUpAttackAgent 명시 실행:

```powershell
$env:ENABLE_LAB_ATTACKS="true"
python -m attack_agent.kill_chain --stage follow-up --objective FAILSAFE_INDUCTION --execute --max-steps 1
```

전체 체인 dry-run:

```powershell
python -m attack_agent.kill_chain --stage all --objective PROTOCOL_INTEGRITY_TEST --max-steps 1
```

## 실제 이벤트 전송 실행 방법

이 프로젝트에서 말하는 "실제 이벤트 전송"은 실제 드론, 실제 RF, 외부 네트워크, raw packet 공격을 의미하지 않습니다.  
`DAH_SMU` Docker 내부 테스트베드 안에서만 `FollowUpAttackAgent -> Adapter -> Safe Follow-up Module -> Dashboard/C2 evidence`로 시뮬레이션 이벤트를 전달하는 것을 의미합니다.

체인 코드는 실행 위치를 자동으로 구분합니다.

| 실행 위치 | 주소 처리 방식 |
|---|---|
| PowerShell / Windows 호스트 | `dah-tactical-router` 같은 Docker 서비스명을 `localhost` 공개 포트로 자동 변환 |
| Docker 컨테이너 내부 | Docker DNS 이름인 `dah-dashboard`, `dah-tactical-router` 등을 그대로 사용 |

따라서 일반 사용 흐름은 PowerShell에서 실행해도 됩니다. 단, 라우터 이벤트 전송을 위해 `docker-compose.yml`에서 `14590/udp`와 `8084:8080` 포트가 열려 있어야 하며, 현재 compose에는 이 설정이 포함되어 있습니다.

즉, 아래 두 가지 조건이 모두 만족되어야 이벤트가 전송됩니다.

| 조건 | 의미 |
|---|---|
| `--execute` | 계획만 만들지 않고 선택된 후속 모듈을 실행 |
| `ENABLE_LAB_ATTACKS=true` | 로컬 Docker 실험망 실행을 명시적으로 허용 |

둘 중 하나라도 빠지면 안전하게 실행이 막히거나 dry-run 성격으로 동작합니다.

### 1. Docker 테스트베드 실행

먼저 `DAH_SMU` 루트에서 Docker 서비스를 올립니다.

```powershell
cd C:\Users\taehy\OneDrive\문서\UAS\DAH_SMU
docker compose up -d --build
```

대시보드는 아래 주소에서 확인합니다.

```text
http://localhost:9000
```

### 2. 정찰 결과 파일 확인

체인은 정찰 결과 파일을 입력으로 사용합니다. 기본 입력 파일은 아래입니다.

```text
output\intel_handoff.json
```

파일이 없다면 먼저 ReconAgent를 실행합니다. ReconAgent가 `dah-recon` 서비스를 실행해 수집과 정규화를 한 번에 처리합니다.

```powershell
python -m attack_agent.kill_chain --stage recon
```

`dah-recon`은 고정 IP `172.31.50.40`에서 Companion mirror 패킷을 수신합니다. 이 때문에 ReconAgent는 PowerShell/호스트 실행 시 내부적으로 `docker compose --profile recon-lab up --build --no-deps dah-recon` 방식을 사용합니다.

정찰이 끝나면 아래 파일이 생성됩니다.

```text
output\intel_handoff.json
output\passive_mavlink_intel.json
```

정찰 시간을 줄이고 싶으면 아래처럼 실행합니다.

```powershell
python -m attack_agent.kill_chain --stage recon --recon-duration-s 10 --recon-revalidate-s 5
```

### 3. 계획만 생성해서 확인

이 단계는 안전 확인용입니다. 이벤트를 전송하지 않고 `output\stage_3_attack_plan.json`만 생성합니다.

```powershell
python -m attack_agent.kill_chain --stage follow-up --objective FAILSAFE_INDUCTION --max-steps 1
```

생성되는 주요 파일은 아래와 같습니다.

| 파일 | 역할 |
|---|---|
| `output\stage_1_recon.json` | 정찰 결과를 체인 내부 표준 형식으로 변환한 파일 |
| `output\stage_3_attack_plan.json` | Planner가 생성한 실행 가능한 AttackPlan |
| `output\stage_3_execution_report.json` | 실행 전후 요약, 선택 모듈, evidence를 포함한 최종 보고서 |

### 4. dry-run으로 전체 체인 점검

아직 이벤트를 전송하지 않고, 어떤 모듈이 선택되는지와 어떤 evidence가 만들어지는지만 확인합니다.

```powershell
python -m attack_agent.kill_chain --stage all --objective FAILSAFE_INDUCTION --max-steps 1
```

Fail-safe 유도 목적만 점검하려면:

```powershell
python -m attack_agent.kill_chain --stage follow-up --objective FAILSAFE_INDUCTION --max-steps 1
```

합성 프로토콜 프레임 무결성 테스트만 점검하려면:

```powershell
python -m attack_agent.kill_chain --stage follow-up --objective PROTOCOL_INTEGRITY_TEST --max-steps 1
```

### 5. Docker 내부 실험 이벤트 전송

PowerShell에서 로컬 실험 실행 허용 환경 변수를 켭니다.

```powershell
$env:ENABLE_LAB_ATTACKS="true"
```

그 다음 `--execute`로 실행합니다.

```powershell
python -m attack_agent.kill_chain --stage follow-up --objective FAILSAFE_INDUCTION --execute --max-steps 1
```

Fail-safe 유도 시뮬레이션을 실제 C2 보고 경로로 보내려면:

```powershell
python -m attack_agent.kill_chain --stage follow-up --objective FAILSAFE_INDUCTION --execute --max-steps 1
```

합성 저수준 프레임 무결성 이벤트를 실제 C2 보고 경로로 보내려면:

```powershell
python -m attack_agent.kill_chain --stage follow-up --objective PROTOCOL_INTEGRITY_TEST --execute --max-steps 1
```

`--max-steps 1`은 한 번에 하나의 후속 모듈만 실행하기 위한 안전 옵션입니다.  
여러 단계를 연속 실행하려면 값을 늘릴 수 있지만, 결과를 확인하면서 단계적으로 늘리는 것을 권장합니다.

정찰을 다시 실행해야 한다면 ReconAgent를 다시 실행합니다. ReconAgent가 내부적으로 기존 `dah-recon` 컨테이너를 제거하고 수집 서비스를 다시 올립니다.

```powershell
python -m attack_agent.kill_chain --stage recon
```

### 6. 실행 결과 확인

최종 evidence는 아래 파일에서 확인합니다.

```text
output\stage_3_execution_report.json
```

이 파일에는 다음 내용이 포함됩니다.

| 항목 | 의미 |
|---|---|
| `input_initial_access_intel` | 어떤 2단계 분석 결과에서 시작했는지 |
| `plan_summary.steps` | Planner가 선택한 후속 모듈과 실행 파라미터 |
| `before_summary` | 실행 전 Dashboard `/api/live` 요약 |
| `after_summary` | 실행 후 Dashboard `/api/live` 요약 |
| `execution_results` | Adapter 실행 결과와 안전 alert evidence |
| `verification.recommendation_change` | 실행 전후 `mission_state.phase` 변화 |

대시보드/API에서도 변화를 확인할 수 있습니다.

```powershell
Invoke-RestMethod http://localhost:9000/api/live
```

`FAILSAFE_INDUCTION`을 `--execute`로 실행하면 대시보드의 로컬 fail-safe 상태머신이 활성화됩니다.
이는 실제 MAVLink 제어 명령이 아니라 안전한 표시/상태 오버레이입니다.

| 확인 항목 | 기대값 |
|---|---|
| `mission_state.phase` | `FAILSAFE_LAND`, 이후 `FAILSAFE_LANDED` |
| UAV `lat/lon` | 공격 시점 위치로 고정 |
| UAV `speed` | `0` |
| UAV `mission` | `FAILSAFE_STOPPED` |
| UAV `alt` | 점진적으로 감소, 최종 `0` |
| `failsafe_simulation.simulated_only` | `true` |

라우터 직접 상태는 아래처럼 확인합니다.

```powershell
Invoke-RestMethod http://localhost:8084/api/ticn/status
```

## 실행 모드 차이

| 실행 방식 | 이벤트 전송 | 사용 목적 |
|---|---:|---|
| `--dry-run` | X | 전체 체인을 실행하는 척하면서 evidence 구조 확인 |
| `--execute`만 사용 | X 또는 차단 | 환경 변수 미설정 시 안전 게이트로 차단 |
| `ENABLE_LAB_ATTACKS=true` + `--execute` | O | 로컬 Docker 실험망으로 안전 시뮬레이션 이벤트 전송 |

## 지원되는 후속 모듈

Planner는 Initial Access 결과의 후보 모듈을 읽고 아래 모듈들을 실행 가능한 `AttackStep`으로 변환합니다.

| 모듈 | 역할 |
|---|---|
| `EW_LINK_DEGRADATION_SIM` | 전자전 환경의 링크 품질 저하를 안전 이벤트로 모사하고 Dashboard `FAILSAFE_LAND` 오버레이 활성화 |
| `PROTOCOL_FRAME_INTEGRITY_SIM` | 합성 프로토콜 프레임 무결성 검증 실패를 모사 |

## 합성 저수준 프레임 무결성 테스트

저수준 조작은 실제 MAVLink, UDP, RF, raw packet을 만들거나 전송하지 않습니다.  
로컬 메모리 또는 로컬 JSON 기반의 합성 프레임만 변형하고, 검증 실패를 내부 alert 메시지로 변환합니다.

지원되는 frame mutation mode는 아래와 같습니다.

| 모드 | 의미 |
|---|---|
| `FRAME_STX_CORRUPT` | 시작 바이트가 깨진 상황 모사 |
| `FRAME_LENGTH_MISMATCH` | 선언 길이와 실제 payload 길이 불일치 모사 |
| `FRAME_CRC_BREAK` | CRC 검증 실패 모사 |
| `FRAME_SIGNATURE_INVALID` | 서명 검증 실패 모사 |
| `FRAME_SEQUENCE_ROLLBACK` | 시퀀스 번호가 과거로 되돌아간 상황 모사 |
| `FRAME_REPLAY_OLD_TIMESTAMP` | 오래된 timestamp replay 징후 모사 |
| `FRAME_PAYLOAD_BITFLIP_SIM` | payload 일부 bit flip 상황 모사 |

생성되는 alert 예시는 아래와 같습니다.

```json
{
  "message_type": "protocol_integrity_alert",
  "vehicle_id": "UAS-01",
  "integrity_status": "CRC_FAIL",
  "frame_mutation_mode": "FRAME_CRC_BREAK",
  "severity": "HIGH",
  "simulated_only": true,
  "scope": "LOCAL_DOCKER_TESTBED_ONLY",
  "evidence": {
    "expected_crc": "0x1234",
    "observed_crc": "0xabcd",
    "seq": 12
  }
}
```

이 alert는 실제 공격 패킷이 아니라, Dashboard/Upper C2가 탐지·추천 변경을 표시할 수 있도록 전달되는 안전한 내부 시뮬레이션 메시지입니다.
실행 모드에서는 합성 프레임 바이트를 실제 MAVLink/UDP 공격 트래픽으로 전송하지 않고, 생성된 `protocol_integrity_alert`를 Dashboard의 `/api/agent-event`로 보고합니다.
Dashboard는 이 이벤트를 `agent_events`에 저장하고 `mission_state.phase`를 `INTEGRITY_ALERT`로 변경하여 `stage_3_execution_report.json`의 recommendation change에 반영합니다.

## 출력 파일

체인은 아래 파일을 생성합니다.

```text
output/stage_1_recon.json
output/stage_2_initial_access.json
output/stage_2_attack_graph.json
output/stage_3_attack_plan.json
output/stage_3_execution_report.json
```

`stage_3_execution_report.json`에는 원본 정찰 파일, 생성된 계획, dry-run 또는 실행 결과, 대시보드 실행 전후 관측값이 포함됩니다.

기존 JSON handoff 파일도 계속 사용할 수 있습니다. 새 체인은 이를 `IntelDocument`로 정규화하여, 정찰에서 파생된 `recon_tags`와 API baseline을 `InitialAccessAgent`가 후보 선정 근거로 사용하도록 합니다.
