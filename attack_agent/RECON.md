# Passive MAVLink Recon — 수동 정찰 시나리오

> **시나리오 코드**: S11-RECON  
> **분류**: 저권한 수동 정찰 (Low-Privilege Passive Reconnaissance)  
> **보안 제약**: 실제 군 장비 미연결 · raw socket 없음 · 패킷 주입 없음

---

## 1. 개요

**Passive MAVLink Recon**은 dah-net(172.20.0.0/24) 브로드캐스트 세그먼트에서  
UAV-001(송골매)이 방출하는 평문 MAVLink 텔레메트리를 수동으로 청취하여  
후속 공격 에이전트(`dah-executor`, `dah-spoofer`, `dah-jammer`, `dah-inducer`)의  
실행 파라미터를 구조화하는 인텔리전스 수집 모듈이다.

### 핵심 특성

| 항목 | 내용 |
|---|---|
| 대상 자산 | UAV-001 (SYS_ID=1, 172.20.0.10) |
| 청취 포트 | UDP 14550 (dah-net MAVLink 브로드캐스트) |
| 권한 | 일반 UDP bind (CAP_NET_RAW 불필요) |
| GCS 흔적 | Phase 0에서 HTTP 2회 / Phase 1 이후 완전 수동 |
| 출력 | `passive_mavlink_intel.json` + `intel_handoff.json` |

---

## 2. 네트워크 토폴로지

```
  dah-net (172.20.0.0/24)
  ┌───────────────────────────────────────────────────────────┐
  │                                                           │
  │  172.20.0.10  dah-uav        ─── MAVLink broadcast ───►  │
  │               (송골매 UAV)        172.20.0.255:14550       │
  │                                         │                 │
  │                              ┌──────────┼──────────┐      │
  │                              ▼          ▼          ▼      │
  │  172.20.0.30  dah-companion  (수신)               │      │
  │  172.20.0.40  dah-recon ◄────────────────────────┘      │
  │               (정찰 에이전트)  SO_REUSEADDR + SO_REUSEPORT │
  │  172.20.0.70  dah-dashboard                              │
  └───────────────────────────────────────────────────────────┘

  ops_net
  ┌──────────────────────────────────────────┐
  │  dah-dashboard :8080   /api/live        │  ◄── Phase 0 HTTP
  │  dah-dashboard :8080   /api/failsafe    │  ◄── Phase 0 HTTP
  │  dah-dashboard :14571  UDP 이벤트 수신  │  ◄── 실시간 이벤트 전송
  └──────────────────────────────────────────┘
```

**왜 Companion Computer와 포트 충돌이 없는가?**  
UDP 브로드캐스트에서 `SO_REUSEADDR` + `SO_REUSEPORT`를 사용하면  
동일 포트에 여러 소켓이 바인딩되어 모두 같은 패킷을 수신할 수 있다.  
`dah-companion`(172.20.0.30)과 `dah-recon`(172.20.0.40)이 14550을 공유한다.

---

## 3. 6단계 파이프라인

```
Phase 0 ──► Phase 1 ──► Phase 2 ──► Phase 3 ──► Phase 4 ──► Phase 5
API 정찰    UDP 청취   신뢰도 채점  재검증(LOW)  에이전트 매핑  결과 저장
(~5s)      (120s)     (즉시)       (20s/생략)   (즉시)        (즉시)
```

### Phase 0 — Dashboard API 사전 정찰

```
GET http://dah-dashboard:8080/api/live      → UAV 현재 상태
GET http://dah-dashboard:8080/api/failsafe  → Fail-safe 정책값
```

수집 항목:

| 항목 | 활용 |
|---|---|
| UAV 고도/모드/연료 | executor 타이밍 판단 기준 |
| TICN 손실률/링크 품질 | jammer 효과 예측 |
| HB timeout / max_miss | inducer Phase 1 파라미터 |
| loss critical % / latency critical ms | inducer Phase 2·3 파라미터 |
| failsafe_action | inducer 유도 목표 확인 |

> **주의**: HTTP 요청 2회가 Dashboard → GCS 경로에 로그로 남을 수 있다.  
> `--skip-phase0` 플래그 사용 시 완전 수동 모드로 전환된다.

---

### Phase 1 — 수동 MAVLink 청취 (기본 120s)

UDP 소켓으로 `0.0.0.0:14550`에 바인딩 후 dah-net 브로드캐스트를 수신한다.  
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
HIGH   ≥ 0.80  → 후속 에이전트 즉시 실행 가능
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

신뢰도 < HIGH인 자산이 있을 경우 `--revalidate-s`(기본 20s)만큼  
추가 청취 후 더 나은 관측값으로 병합한다.

```
전 자산 HIGH → "재검증 생략" 메시지 출력 후 Phase 4로 진행
LOW 자산 있음 → 추가 20s 청취 → score 개선 시 덮어쓰기
```

---

### Phase 4 — DAH_SMU 후속 에이전트 매핑

UAV-001 상태와 신뢰도를 기반으로 4개 후속 에이전트에 대한 권고를 생성한다.

#### dah-executor (LAND-INJECT)
```
조건: UAV 무장(is_armed=Y) + 고도 > 500m + 신뢰도 HIGH
타이밍: 고도 > 1000m 시 최적
파라미터: target_host=172.20.0.10  cmd_port=14551  sys_id=1
```

#### dah-spoofer (GPS-SPOOF)
```
조건: 위치 확보 완료 + 신뢰도 MEDIUM 이상
타이밍: PATROL_TRANSIT 또는 MISSION_PROGRESS 중 효과 최대
파라미터: gcs_host=dah-gcs  gcs_port=14555
          start_lat/lon/alt = 수집된 현재 위치
          spoof_target = 38.50N, 126.60E (북쪽 허가구역 외)
```

#### dah-jammer (TMMR-JAM)
```
조건: 항상 가능 (신뢰도 MEDIUM 이상)
타이밍: PATROL_TRANSIT 중 주입 시 즉각 LOITER 전환 유발
파라미터: router_host=dah-tactical-router  jam_port=14590
          channels=[VHF, UHF, HF]  duration=14s  interval=6s
```

#### dah-inducer (FAILSAFE-INDUCE)
```
조건: Phase 0 API 정찰 성공 + fail-safe 정책값 확보
타이밍: 4단계 순차 실행
파라미터: hb_timeout_sec, loss_critical_pct, latency_critical_ms
         failsafe_action, dashboard_host, router_host
```

**행동 패턴 분류 (DAH_SMU 맞춤):**

| 패턴 | 조건 | 권고 에이전트 |
|---|---|---|
| `PATROL_TRANSIT` | 속도 > 80m/s + 작전구역 내 | dah-executor |
| `PATROL_TURNING` | 방위각 변화 > 30° | dah-spoofer |
| `LOITER_HOLDING` | 속도 < 10m/s + 위치 샘플 있음 | dah-inducer |
| `DESCENT_OR_RTL` | 고도 변화 < -50m + 속도 < 100m/s | dah-jammer |
| `OUT_OF_AREA` | 작전구역(37.85-37.96°N) 이탈 | dah-spoofer |
| `MISSION_UPLOAD_ACTIVITY` | MISSION_COUNT 수신 | dah-inducer |

---

### Phase 5 — 결과 저장

두 개의 JSON 파일을 `/app/output/`에 생성한다.

```
/app/output/
├── passive_mavlink_intel.json   ← 전체 상세 인텔
└── intel_handoff.json           ← 후속 에이전트용 경량 파일
```

**intel_handoff.json 구조:**

```jsonc
{
  "generated_at": "2026-07-01T14:30:00",
  "target": {
    "platform_id": "UAV-001",
    "sys_id": 1,
    "host": "172.20.0.10",
    "cmd_port": 14551
  },
  "confidence": { "score": 1.00, "label": "HIGH — 후속 에이전트 실행 가능" },
  "uav_state": {
    "armed": true,
    "alt_m": 3500,
    "lat": 37.9034,
    "lon": 126.8512,
    "pattern": "PATROL_TRANSIT",
    "in_oa": true
  },
  "executor_ready": true,
  "spoofer_ready":  true,
  "jammer_ready":   true,
  "inducer_ready":  true,
  "follow_on_agents": [
    { "agent": "dah-executor", "action": "LAND-INJECT", "params": {...} },
    { "agent": "dah-spoofer",  "action": "GPS-SPOOF",   "params": {...} },
    { "agent": "dah-jammer",   "action": "TMMR-JAM",    "params": {...} },
    { "agent": "dah-inducer",  "action": "FAILSAFE-INDUCE", "params": {...} }
  ]
}
```

---

## 4. 실행 방법

### 전제 조건

```bash
# 기본 스택이 실행 중이어야 한다
cd C:\temp_git\DAH_SMU
docker compose up -d
```

서비스 상태 확인:
```bash
docker compose ps
# dah-uav, dah-companion, dah-gcs, dah-dashboard, tactical-router 가 Up 상태여야 함
```

---

### 단독 실행 (권장)

```bash
# cyber-lab 프로파일로 dah-recon만 실행
docker compose --profile cyber-lab up dah-recon
```

기본 파라미터:
- `--listen-port 14550` — dah-net MAVLink 브로드캐스트
- `--duration-s 120` — 2분 수집
- `--revalidate-s 20` — LOW 자산 재검증 20초
- `--prediction-horizon-s 60` — 60초 위치 예측
- `--output /app/output/passive_mavlink_intel.json`

---

### 파라미터 커스터마이징

`docker-compose.yml`의 `dah-recon` 서비스 `command` 섹션을 수정하거나,  
임시로 오버라이드할 수 있다.

```bash
# 30초만 수집 (빠른 테스트)
docker compose --profile cyber-lab run --rm dah-recon \
  python recon.py \
  --listen-port 14550 \
  --duration-s 30 \
  --revalidate-s 0 \
  --output /app/output/passive_mavlink_intel.json
```

```bash
# Phase 0 생략 — 완전 수동 모드 (HTTP 흔적 제로)
docker compose --profile cyber-lab run --rm dah-recon \
  python recon.py \
  --listen-port 14550 \
  --duration-s 120 \
  --skip-phase0 \
  --output /app/output/passive_mavlink_intel.json
```

---

### 전체 사이버랩 시나리오 실행

```bash
# 정찰 → 공격 순차 실행 예시
docker compose --profile cyber-lab up dah-recon        # Phase 0~5 완료 대기
docker compose --profile cyber-lab up dah-executor     # LAND 명령 주입
```

```bash
# 재밍과 스푸핑 동시 실행
docker compose --profile cyber-lab up dah-jammer dah-spoofer
```

```bash
# 전체 공격 에이전트 + 방어 에이전트 동시 실행
docker compose --profile cyber-lab up
```

---

### 결과 확인

```bash
# 실시간 로그
docker logs -f dah-recon

# 결과 파일 확인 (호스트에서)
cat C:\temp_git\DAH_SMU\output\passive_mavlink_intel.json | python -m json.tool
cat C:\temp_git\DAH_SMU\output\intel_handoff.json         | python -m json.tool
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
  └── timing_recs           — 후속 에이전트 타이밍 권고
follow_on_agents           — Phase 4 에이전트 매핑 (파라미터 포함)
revalidation               — Phase 3 재검증 변경 이력
blue_team_mapping          — 탐지 계층별 가시성 및 권고 통제
ghost_sentinel             — 고권한 수동 정찰 위협모델 비교
```

### intel_handoff.json

후속 에이전트가 파싱하기 위한 경량 파일.  
`executor_ready`, `spoofer_ready`, `jammer_ready`, `inducer_ready` 플래그와  
각 에이전트의 `params` 딕셔너리를 포함한다.

---

## 6. 후속 에이전트 연계 구조

```
dah-recon
  ├── Phase 0: /api/live + /api/failsafe  ─────────────────────────┐
  │                                                                 ▼
  │   intel_handoff.json ──────────────────────────────── [활용 가능]
  │                                                                 │
  ├── dah-executor  ←── armed=Y + alt>500m + score≥0.80            │
  │   LAND 명령 주입 → UAV FC (172.20.0.10:14551)                   │
  │                                                                 │
  ├── dah-spoofer   ←── lat/lon 확보 + score≥0.50                  │
  │   GPS 좌표 위조 → GCS (dah-gcs:14555)                          │
  │                                                                 │
  ├── dah-jammer    ←── 항상 가능 (dah-net 공유)                   │
  │   TMMR 재밍 → Router (dah-tactical-router:14590)               │
  │                                                                 │
  └── dah-inducer   ←── API 정찰 성공 시 ─────────────────────────┘
      4단계 Fail-safe 유도 (HB누락→손실률→지연→간헐적)
```

> **현재 연계 방식**: `intel_handoff.json`은 생성되지만,  
> `executor.py` · `spoofer.py`는 아직 이 파일을 자동으로 읽지 않는다.  
> 각 에이전트는 도커 환경변수나 하드코딩 값으로 동작한다.  
> 파일 기반 자동 연계는 별도 구현 확장 항목이다.

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
| 후속 에이전트 매핑 | warn | "후속 에이전트 매핑 완료" |
| 인텔 저장 완료 | info | "인텔 저장 완료" |

---

## 8. 탐지 분석 (Blue-team)

| 탐지 계층 | 가시성 | 이유 | 권고 통제 |
|---|---|---|---|
| GCS 감사로그 | Phase 0: 중간 / Phase 1: 낮음 | HTTP 요청 2회 (Phase 0만) | Dashboard→GCS API 로그 + 비정상 출처 검출 |
| dah-net IDS | 중간 | UDP 14550 다중 수신자 식별 어려움 | 비인가 UDP bind 이벤트 경보 |
| Docker 이벤트 | 높음 | cyber-lab 컨테이너 시작 로그 | `docker events` 모니터링 |
| 호스트 EDR/eBPF | 중간-높음 | 14550 SO_REUSEPORT bind 추적 가능 | 소켓 수명·프로세스 계보 감사 |
| MAVLink 서명 | 노출=높음 | 평문 브로드캐스트 전체 노출 | MAVLink v2 서명 강제 + unicast 전환 |

**Ghost Sentinel 비교 (미구현):**  
AF_PACKET/CAP_NET_RAW를 사용하면 dah-net 내 unicast 패킷까지 수신 가능하다.  
현재 Low-Privilege Sentinel은 브로드캐스트만 수신 — UDP bind 테이블에 노출된다.  
Ghost Sentinel은 bind 테이블에 없으나 CAP_NET_RAW 정책 이벤트로 탐지 가능하다.

---

## 9. 보안 제약

다음 제약은 DAH 2026 대회 규정에 따라 절대 준수한다.

- ✅ Docker dah-net 내 UDP 브로드캐스트 수신만 허용
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
├── executor.py           ← LAND 명령 주입
├── jammer.py             ← TMMR 전파 재밍
├── spoofer.py            ← GPS 좌표 위조
├── failsafe_inducer.py   ← Fail-safe 4단계 유도
├── Dockerfile            ← 공격 에이전트 이미지
└── RECON.md              ← 이 문서

output/                   ← 볼륨 마운트 (./output:/app/output)
├── passive_mavlink_intel.json   ← 전체 인텔
└── intel_handoff.json           ← 후속 에이전트용 경량 파일
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
