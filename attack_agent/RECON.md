# Passive MAVLink Recon — 수동 정찰 시나리오

> **시나리오 코드**: S11-RECON  
> **분류**: 저권한 수동 정찰 (Low-Privilege Passive Reconnaissance)  
> **보안 제약**: 실제 군 장비 미연결 · raw socket 없음 · 패킷 주입 없음

---

## 1. 개요

**Passive MAVLink Recon**은 dah-net(`172.31.50.0/24`) 안에서 Companion Computer가 복제한 MAVLink mirror를 수동으로 청취하여, 후속 안전 시뮬레이션 모듈의 판단 근거를 구조화하는 인텔리전스 수집 모듈이다.

### 핵심 특성

| 항목 | 내용 |
|---|---|
| 대상 자산 | UAV-001 (SYS_ID=1, 172.31.50.10) |
| 청취 포트 | UDP 14550 (Companion mirror) |
| 권한 | 일반 UDP bind (CAP_NET_RAW 불필요) |
| GCS 흔적 | Phase 0에서 HTTP 2회 / Phase 1 이후 완전 수동 |
| 출력 | `passive_mavlink_intel.json` + `intel_handoff.json` |

---

## 2. 네트워크 토폴로지

```
  dah-net (172.31.50.0/24)
  ┌───────────────────────────────────────────────────────────┐
  │                                                           │
  │  172.31.50.10  dah-uav        ─── MAVLink UDP :14550 ───► │
  │                (송골매 UAV)                                │
  │                                      ▼                     │
  │  172.31.50.30  dah-companion  ─── mirror copy ───────────► │
  │  172.31.50.40  dah-recon       UDP :14550 수동 청취         │
  │  172.31.50.70  dah-dashboard                              │
  └───────────────────────────────────────────────────────────┘

  ops_net
  ┌──────────────────────────────────────────┐
  │  dah-dashboard :8080   /api/live        │  ◄── Phase 0 HTTP
  │  dah-dashboard :8080   /api/failsafe    │  ◄── Phase 0 HTTP
  │  dah-dashboard :14571  UDP 이벤트 수신  │  ◄── 실시간 이벤트 전송
  └──────────────────────────────────────────┘
```

**왜 Companion Computer와 충돌이 없는가?**  
정찰기는 실제 C2 경로에 끼어들지 않는다. `dah-companion`이 수신한 MAVLink 원본 바이트를 `dah-recon:14550`으로 복제할 뿐이며, 정찰 컨테이너가 꺼져 있어도 UAV → Companion → GCS 경로는 유지된다.

---

## 3. 6단계 파이프라인

```
Phase 0 ──► Phase 1 ──► Phase 2 ──► Phase 3 ──► Phase 4 ──► Phase 5
API 정찰    UDP 청취   신뢰도 채점  재검증(LOW)  후속 모듈 매핑  결과 저장
(~5s)      (기본 30s) (즉시)       (20s/생략)   (즉시)        (즉시)
```

### Phase 0 — Dashboard API 사전 정찰

```
GET http://dah-dashboard:8080/api/live      → UAV 현재 상태
GET http://dah-dashboard:8080/api/failsafe  → Fail-safe 정책값
```

수집 항목:

| 항목 | 활용 |
|---|---|
| UAV 고도/모드/연료 | 현재 운용 상태와 실행 전후 비교 기준 |
| TICN 손실률/링크 품질 | `EW_LINK_DEGRADATION_SIM` 선택 및 효과 확인 근거 |
| HB timeout / max_miss | fail-safe 정책값 확인 |
| loss critical % / latency critical ms | 링크 저하/지연 임계값 확인 |
| failsafe_action | 대시보드 fail-safe 상태 해석 기준 |

> **주의**: HTTP 요청 2회가 Dashboard → GCS 경로에 로그로 남을 수 있다.  
> `--skip-phase0` 플래그 사용 시 완전 수동 모드로 전환된다.

---

### Phase 1 — 수동 MAVLink 청취 (기본 30s)

UDP 소켓으로 `0.0.0.0:14550`에 바인딩 후 `dah-companion`이 복제한 MAVLink mirror 패킷을 수신한다.  
GCS HTTP 요청 없음 — 감사 로그 흔적 없음.

**수신 메시지 종류:**

| MAVLink 메시지 | MSG_ID | 추출 정보 |
|---|---|---|
| `HEARTBEAT` | 0 | mav_type, 시스템 상태, 무장 여부, 유도 여부 |
| `SYS_STATUS` | 1 | 배터리%, 패킷 손실률, 통신 오류 수 |
| `GLOBAL_POSITION_INT` | 33 | 위도·경도·고도·속도·방위각 |
| `COMMAND_LONG` | 76 | 명령 종류·대상 시스템 (GCS→UAV 명령 도청) |
| `COMMAND_ACK` | 77 | 명령 결과 (수락/거부/진행중) |
| `MISSION_CURRENT` | 42 | 현재 웨이포인트 번호 |
| `MISSION_COUNT` | 44 | 미션 항목 수 (업로드 감지) |

**MAVLink v2 프레임 구조 파싱:**

```
[STX=0xFD][LEN][INCOMPAT][COMPAT][SEQ][SYS_ID][COMP_ID][MSG_ID×3B][PAYLOAD][CRC16][SIG×13B]
```

- `INCOMPAT_FLAGS & 0x01` = 서명 있음
- CRC는 x25 알고리즘 + CRC_EXTRA 로 검증
- 서명 있어도 payload는 평문 → 기밀성 미제공

---

### Phase 2 — 신뢰도 채점 (6개 팩터)

6개 팩터를 합산하여 0.00~1.00 점수를 산출한다.

| 팩터 | 가중치 | 충족 조건 |
|---|---|---|
| `message_repetition` | 0.20 | 수신 패킷 수 ≥ 3 |
| `position_repetition` | 0.15 | 위치 샘플 수 ≥ 2 |
| `physical_consistency` | 0.25 | 계산 속도 vs 보고 속도 비율 < 4.0 |
| `cross_message_validation` | 0.15 | 무장+고도 모순 없음, ACTIVE+위치 일치 |
| `frame_integrity` | 0.15 | CRC 유효 프레임 존재 (invalid=0.00, unknown=0.08) |
| `freshness` | 0.10 | 마지막 수신 ≤ 90초 이내 |
| **합계** | **1.00** | |

**임계값:**

```
HIGH   ≥ 0.80  → 후속 모듈 후보 생성 가능
MEDIUM  0.50~0.79 → 재검증 권고
LOW    < 0.50  → 지연/스푸핑/불완전 관측 가능성
```

**physical_consistency 상세:**

```python
# 위치 히스토리 2개 이상 수집 후
calculated_speed = distance(pos[-2], pos[-1]) / delta_t
reported_speed   = GLOBAL_POSITION_INT.vx² + vy² 합 (cm/s → m/s)

# 송골매 (600km/h = ~167m/s) 기준 비율 허용치 4.0
ratio = max(calc, rep) / min(calc, rep)
ok    = ratio < 4.0
```

---

### Phase 3 — 단기 재검증 (LOW 자산만)

신뢰도 < HIGH인 자산이 있을 경우 ReconAgent의 `--recon-revalidate-s`(기본 20s)만큼  
추가 청취 후 더 나은 관측값으로 병합한다.

```
전 자산 HIGH → "재검증 생략" 메시지 출력 후 Phase 4로 진행
LOW 자산 있음 → 추가 20s 청취 → score 개선 시 덮어쓰기
```

---

### Phase 4 — DAH_SMU 후속 모듈 매핑

UAV-001 상태와 신뢰도를 기반으로 현재 구현된 안전 후속 모듈 권고를 생성한다.

#### JammerAdapter / `dah-jammer` (`EW_LINK_DEGRADATION_SIM`)
```
조건: 링크 상태 모니터링 가능
타이밍: PATROL_TRANSIT 중 실행 시 대시보드 링크저하 변화 관측 용이
파라미터: router_host=dah-tactical-router  jam_port=14590
          channels=[VHF, UHF, HF]  duration=14s
```

#### TamperAdapter / `tamper` (`PROTOCOL_FRAME_INTEGRITY_SIM`)
```
조건: Phase 0 API 정찰 성공 또는 parser 테스트 목적
타이밍: 즉시 가능
파라미터: mutation=FRAME_CRC_BREAK, protocol=MAVLink-like
```

**행동 패턴 분류 (DAH_SMU 맞춤):**

현재 구현에서 행동 패턴은 "어떤 모듈을 실행할지"를 단독 결정하지 않는다.  
`score >= MEDIUM`이면 `EW_LINK_DEGRADATION_SIM` 후보가 생성되고, Phase 0 API baseline이 있으면 `PROTOCOL_FRAME_INTEGRITY_SIM` 후보도 생성된다.  
패턴은 주로 `EW_LINK_DEGRADATION_SIM`의 타이밍/설명 문구를 보강하는 근거로 사용된다.

| 패턴 | 조건 | 현재 활용 |
|---|---|---|
| `PATROL_TRANSIT` | 속도 > 80m/s + 작전구역 내 | 링크 저하 시뮬레이션 타이밍이 좋다는 근거 |
| `PATROL_TURNING` | 방위각 변화 > 30° | 비행 패턴 설명/재검증 근거 |
| `LOITER_HOLDING` | 속도 < 10m/s + 위치 샘플 있음 | 즉각 반응이 약할 수 있다는 타이밍 근거 |
| `DESCENT_OR_RTL` | 고도 변화 < -50m + 속도 < 100m/s | 이미 복귀/하강 중일 가능성 설명 |
| `OUT_OF_AREA` | 작전구역(37.85-37.96°N) 이탈 | 위치 이상/재검증 필요성 근거 |
| `MISSION_PROGRESS` | mission_seq 관측 | 임무 진행 중이라는 근거 |

---

### Phase 5 — 결과 저장

두 개의 JSON 파일을 `output/`에 생성한다. Docker 컨테이너 내부에서는 같은 볼륨이 `/app/output/`으로 보인다.

```
호스트:
output/
├── passive_mavlink_intel.json   ← 전체 상세 인텔
└── intel_handoff.json           ← 후속 체인용 경량 파일

컨테이너 내부:
/app/output/
```

**intel_handoff.json 구조:**

```jsonc
{
  "generated_at": "2026-07-01T14:30:00",
  "target": {
    "platform_id": "UAV-001",
    "sys_id": 1,
    "host": "172.31.50.10",
    "cmd_port": 14551
  },
  "confidence": { "score": 1.00, "label": "HIGH — 후속 모듈 후보 생성 가능" },
  "uav_state": {
    "armed": true,
    "alt_m": 3500,
    "lat": 37.9034,
    "lon": 126.8512,
    "pattern": "PATROL_TRANSIT",
    "in_oa": true
  },
  "link_degradation_ready": true,
  "protocol_integrity_ready": true,
  "follow_on_agents": [
    { "agent": "dah-jammer", "action": "EW_LINK_DEGRADATION_SIM", "params": {...} },
    { "agent": "tamper",     "action": "PROTOCOL_FRAME_INTEGRITY_SIM", "params": {...} }
  ]
}
```

---

## 4. 실행 방법

### 전제 조건

```bash
# 기본 스택이 실행 중이어야 한다
cd C:\Users\taehy\OneDrive\문서\UAS\DAH_SMU
docker compose up -d
```

서비스 상태 확인:
```bash
docker compose ps
# dah-uav, dah-companion, dah-gcs, dah-dashboard, tactical-router 가 Up 상태여야 함
```

---

### ReconAgent 실행 (권장)

```bash
# ReconAgent가 dah-recon 수집 컨테이너 실행부터 정규화까지 수행
python -m attack_agent.kill_chain --stage recon
```

기본 파라미터:
- `--recon-listen-port 14550` — Companion mirror 수신 포트
- `--recon-duration-s 30` — 30초 수집
- `--recon-revalidate-s 20` — LOW 자산 재검증 20초
- `--recon-prediction-horizon-s 60` — 60초 위치 예측
- `--recon-output output/stage_1_recon.json`

생성 파일:

```text
output/passive_mavlink_intel.json
output/intel_handoff.json
output/stage_1_recon.json
output/stage_1_recon_report.json
```

---

### 파라미터 커스터마이징

정찰 시간과 재검증 시간은 ReconAgent 옵션으로 조정한다.

```bash
# 30초만 수집 (빠른 테스트)
python -m attack_agent.kill_chain --stage recon --recon-duration-s 30 --recon-revalidate-s 0
```

```bash
# 기존 정찰 JSON만 다시 정규화
python -m attack_agent.kill_chain --stage recon --skip-recon-collection
```

---

### 전체 3단계 체인 실행

```bash
# 1. 정찰 수집 + 정규화
python -m attack_agent.kill_chain --stage recon
```

```bash
# 2. 초기침투 분석 + Attack Graph 생성
python -m attack_agent.kill_chain --stage initial-access
```

```bash
# 3. 후속공격 계획 생성
python -m attack_agent.kill_chain --stage follow-up --objective FAILSAFE_INDUCTION --max-steps 1
```

```bash
# 4. 안전한 로컬 Docker 테스트베드 이벤트 실행
$env:ENABLE_LAB_ATTACKS="true"
python -m attack_agent.kill_chain --stage follow-up --objective FAILSAFE_INDUCTION --execute --max-steps 1
```

---

### 결과 확인

```bash
# 실시간 로그
docker logs -f dah-recon

# 결과 파일 확인 (호스트 PowerShell)
Get-Content output\passive_mavlink_intel.json
Get-Content output\intel_handoff.json
```

---

## 5. 출력 파일 상세

### passive_mavlink_intel.json

```
meta                       — 시나리오 메타데이터 및 제약 플래그
phase0_api_baseline        — Dashboard API 사전 정찰 결과
collection_summary         — 패킷 수, 파싱 오류, CRC 통계, 메시지 분포
assets                     — 자산별 원시 관측 데이터 (position_history 포함)
uav001
  ├── confidence            — 6-팩터 점수 및 상세
  ├── state                 — 최신 UAV 상태값
  ├── pattern               — 행동 패턴 (PATROL_TRANSIT 등)
  ├── prediction            — 위치 예측 (constant_velocity 모델)
  └── timing_recs           — 후속 모듈 타이밍 권고
follow_on_agents           — Phase 4 후속 모듈 매핑 (legacy key, 파라미터 포함)
revalidation               — Phase 3 재검증 변경 이력
blue_team_mapping          — 탐지 계층별 가시성 및 권고 통제
ghost_sentinel             — 고권한 수동 정찰 위협모델 비교
```

### intel_handoff.json

후속 체인이 파싱하기 위한 경량 파일.  
현재 표준 체인에서는 `ReconAgent`가 이 파일을 `stage_1_recon.json`으로 정규화한 뒤 `InitialAccessAgent`가 후속 모듈 후보를 다시 계산한다.

---

## 6. 후속 모듈 연계 구조

```
ReconAgent
  ├── Phase 0: /api/live + /api/failsafe
  ├── Phase 1: passive MAVLink mirror 수집
  └── stage_1_recon.json
        ↓
InitialAccessAgent
  ├── API surface / asset / edge / GCS model 생성
  └── 후속 모듈 후보 생성
        ├── EW_LINK_DEGRADATION_SIM
        └── PROTOCOL_FRAME_INTEGRITY_SIM
        ↓
FollowUpAttackAgent
  ├── AttackPlan 생성
  └── 명시 실행 시 안전 시뮬레이션 이벤트 전송
```

---

## 7. Dashboard 이벤트 흐름

recon.py는 `dah-dashboard:14571`(UDP)으로 이벤트를 실시간 전송한다.  
대시보드 `/api/live` → `agent_events` 배열에서 확인 가능.

```jsonc
// 이벤트 구조
{
  "platform_type": "AGENT",
  "agent_type":    "ATK",
  "platform_id":   "ATK-RECON",
  "source":        "PASSIVE-MAVLINK-RECON",
  "message":       "UAV-001 HIGH 신뢰도 확보",
  "detail":        "score=1.00 armed=Y alt=3500m pattern=PATROL_TRANSIT",
  "level":         "warn",
  "status":        "ALERT",
  "time":          "14:30:05"
}
```

**주요 이벤트 타이밍:**

| 단계 | 이벤트 level | 메시지 |
|---|---|---|
| 파이프라인 시작 | info | "정찰 파이프라인 시작" |
| Phase 0 완료 | warn | "Phase 0 완료 — 운용 상태 및 Fail-safe 정책 수집" |
| Phase 1 완료 | warn/info | "Phase 1 완료 — N개 자산 식별" |
| HIGH 신뢰도 확보 | warn | "UAV-001 HIGH 신뢰도 확보" |
| 후속 모듈 매핑 | warn | "후속 에이전트 매핑 완료" |
| 인텔 저장 완료 | info | "인텔 저장 완료" |

---

## 8. 탐지 분석 (Blue-team)

| 탐지 계층 | 가시성 | 이유 | 권고 통제 |
|---|---|---|---|
| GCS 감사로그 | Phase 0: 중간 / Phase 1: 낮음 | HTTP 요청 2회 (Phase 0만) | Dashboard→GCS API 로그 + 비정상 출처 검출 |
| dah-net IDS | 중간 | UDP 14550 다중 수신자 식별 어려움 | 비인가 UDP bind 이벤트 경보 |
| Docker 이벤트 | 높음 | recon-lab 컨테이너 시작 로그 | `docker events` 모니터링 |
| 호스트 EDR/eBPF | 중간-높음 | 14550 SO_REUSEPORT bind 추적 가능 | 소켓 수명·프로세스 계보 감사 |
| MAVLink 서명 | 노출=높음 | mirror 구간에서 프레임 메타데이터 관측 가능 | MAVLink v2 서명 강제 + mirror 권한 통제 |

**Ghost Sentinel 비교 (미구현):**  
AF_PACKET/CAP_NET_RAW를 사용하면 dah-net 내 unicast 패킷까지 수신 가능하다.  
현재 Low-Privilege Sentinel은 Companion mirror 포트만 수신 — UDP bind 테이블에 노출된다.  
Ghost Sentinel은 bind 테이블에 없으나 CAP_NET_RAW 정책 이벤트로 탐지 가능하다.

---

## 9. 보안 제약

다음 제약은 DAH 2026 대회 규정에 따라 절대 준수한다.

- ✅ Docker dah-net 내 Companion mirror UDP 수신만 허용
- ✅ Dashboard HTTP API 호출만 허용 (읽기 전용 엔드포인트)
- ❌ raw socket / AF_PACKET / CAP_NET_RAW 사용 금지
- ❌ 실제 MAVLink 패킷 주입 금지
- ❌ 실제 군 장비(TICN, TMMR, 군 C2/BMS) 연결 금지
- ❌ 외부 IP 대상 트래픽 생성 금지
- ❌ 실제 드론 actuator 제어 금지

---

## 10. 파일 구조

```
attack_agent/
├── recon.py              ← 6단계 정찰 파이프라인 (이 시나리오)
├── mavlink_parser.py     ← MAVLink v1/v2 커스텀 파서 (stdlib only)
├── kill_chain.py         ← 3단계 체인 컨트롤러
├── agents/               ← Recon / Initial Access / Follow-up Agent
├── adapters/             ← 안전 후속 모듈 실행 어댑터
├── tamper/               ← 합성 프레임 무결성 테스트
├── Dockerfile            ← 공격 에이전트 이미지
└── RECON.md              ← 이 문서

output/                   ← 볼륨 마운트 (./output:/app/output)
├── passive_mavlink_intel.json   ← 전체 인텔
└── intel_handoff.json           ← 후속 체인용 경량 파일
```

---

## 11. mavlink_parser.py 원리

`recon.py`는 pymavlink 없이 자체 파서를 사용한다.  
외부 의존 없이 stdlib(`struct`, `json`, `dataclasses`)만으로 MAVLink를 해석한다.

**CRC 검증 (x25):**

```python
def x25_checksum(data: bytes) -> int:
    crc = 0xFFFF
    for byte in data:
        tmp = byte ^ (crc & 0xFF)
        tmp = (tmp ^ (tmp << 4)) & 0xFF
        crc = ((crc >> 8) ^ (tmp << 8) ^ (tmp << 3) ^ (tmp >> 4)) & 0xFFFF
    return crc

# 검증: x25(payload + CRC_EXTRA[msg_id]) == frame의 CRC 2바이트
```

**CRC_EXTRA 값 (DAH_SMU 주요 메시지):**

| 메시지 | MSG_ID | CRC_EXTRA |
|---|---|---|
| HEARTBEAT | 0 | 50 |
| SYS_STATUS | 1 | 124 |
| GLOBAL_POSITION_INT | 33 | 104 |
| COMMAND_LONG | 76 | 152 |
| COMMAND_ACK | 77 | 143 |

**MAVLink 서명 탐지:**

```
v2 프레임에서 incompat_flags & 0x01 == 1
→ 프레임 끝 13바이트가 서명 (link_id 1B + timestamp 6B + signature 6B)
→ 서명이 있어도 payload는 평문 → 내용 도청 가능
→ signed_frames 카운터 증가 (보안 수준 지표)
```
