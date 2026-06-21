# DAH 2026 — Defense AI Cyber Security Hackathon

> **예선 마감**: 2026.07.10 (금) 23:59 KST  
> **도메인**: UAV / UGV  
> **환경**: 위성 네트워크 기반 클라우드 가상 전장  
> **주최**: LIG D&A (구 LIG넥스원)

---

## 프로젝트 개요

위성 네트워크 기반 UAV/UGV 합동 운용 환경에서  
**AI 공격 에이전트(Attack AI)** 와 **AI 방어 에이전트(Defense AI)** 가  
실시간으로 공방전을 벌이는 시스템.

공격 AI는 MAVLink 취약점·위성 통신망·UAV/UGV 제어 체계의 취약점을 탐색하고 공격을 자율 수행한다.  
방어 AI는 이상 탐지·위협 분류·대응을 자율 실행하여 가용성(Availability)을 유지한다.

---

## 채점 구조

```
total_score = (attack_score + defense_score) × availability
availability: SLA 체크 기반 0 ~ 100
```

> 공격이 아군 가용성까지 훼손하면 역효과.  
> 정밀 타격으로 임무 방해 + 방어 AI로 가용성 유지가 핵심.

---

## 공격 표면 (Attack Surface)

### MAVLink 프로토콜 취약점

UAV ↔ GCS 간 통신 프로토콜. **기본 암호화·인증 없음** — 공격의 핵심 진입점.

| 취약점 | 원인 | 공격 성공률 |
|---|---|---|
| Command Injection | 인증 없이 패킷 수락 | **90~95%** (v1 기준) |
| UDP Flooding (DoS) | 무상태 UDP, 버퍼 오버플로우 | **100%** 통신 방해 |
| Replay Attack | 타임스탬프 검증 허술 | 검증 없는 환경에서 성공 |
| Downgrade Attack | v2 → v1 강제 다운그레이드 | 모든 서명·암호화 우회 |
| MITM | 평문 전송 + 인증 없음 | 라디오 범위 내 성공 |
| Spoofing | 메시지 서명 선택사항 | v1에서 사실상 탐지 불가 |

#### Command Injection 공격 흐름
```
1. 네트워크 스니핑 → MAVLink 채널 도청 (UDP 14550)
2. 패킷 파싱 → System ID / Component ID / Sequence Number 파악
3. 위조 COMMAND_LONG 생성
   예) MAV_CMD_NAV_LAND, MAV_CMD_DO_SET_MODE, MAV_CMD_NAV_RETURN_TO_LAUNCH
4. GCS보다 빠르게 전송 → UAV는 먼저 온 패킷 수락
5. UAV 강제 착륙 / 귀환 / 임무 중단
```

#### Downgrade + Spoof 콤보 흐름
```
1. MAVLink v2 환경 탐지
2. v1 요청 패킷으로 다운그레이드 유도
3. 서명 없는 v1 환경에서 자유 스푸핑
```

### 위성 통신망 취약점

| 공격 | 방법 |
|---|---|
| Link Jamming | 대역폭 포화 → 통신 두절 → UAV 비상모드 |
| ISR 데이터 위변조 | 정찰 데이터 위조 → C2 서버 오판 유도 |
| GPS Spoofing | 위치 데이터 덮어쓰기 → 경로 이탈 |

---

## 시나리오 우선순위

| 순위 | 시나리오 | 차별화 포인트 |
|---|---|---|
| 1 | ISR 데이터 위변조 | 정보전 개념, 심사 창의성 최고 |
| 2 | MAVLink Command Injection | 성공률 90~95% 논문 근거, 기술 완성도 |
| 3 | Satellite Link Jamming | 위성망 환경과 직결, 가용성 점수 연결 |
| 4 | GPS Spoofing | IMU 교차검증 방어 로직으로 확장 |

> 상세 시나리오 → [docs/SCENARIO.md](docs/SCENARIO.md)

---

## 시스템 아키텍처

```
┌─────────────────────────────────────────────────────┐
│                  BATTLEFIELD LAYER                  │
│   [UAV Sim]  [UGV Sim]  [Mission State]  [Monitor]  │
│      MAVLink (UDP 14550) ↕ 평문 통신                 │
└───────────────────────┬─────────────────────────────┘
                        │
┌───────────────────────┴─────────────────────────────┐
│             SATELLITE NETWORK (netem)                │
│         delay 600ms / loss 2% / bw 2Mbps            │
└───────────────────────┬─────────────────────────────┘
                        │
┌───────────────────────┴─────────────────────────────┐
│                  COMMAND LAYER                       │
│        [GCS]   [Ground Station]   [C2 Server]        │
└──────────────┬────────────────────────┬─────────────┘
               │                        │
┌──────────────┴──────┐    ┌────────────┴─────────────┐
│   ATTACK AI AGENT   │    │   DEFENSE AI AGENT        │
│  Recon → Planner    │    │  Monitor → Detector       │
│      → Executor     │    │      → Responder          │
└──────────────┬──────┘    └────────────┬─────────────┘
               └────────────┬───────────┘
                            │
                     [Ollama LLM]
               (두 에이전트 공통 추론 모듈)
```

---

## 레포지토리 구조

```
aidah2026/
├── attack_agent/
│   ├── recon.py           # 공격 표면 정찰 (MAVLink 스니핑 포함)
│   ├── planner.py         # LLM 기반 공격 전략 선택
│   ├── executor.py        # 공격 함수 실행 (injection, jamming 등)
│   └── Dockerfile
│
├── defense_agent/
│   ├── monitor.py         # MAVLink / 네트워크 / ISR 로그 수집
│   ├── detector.py        # 이상 탐지 (시퀀스 번호, 출처 IP, 패턴)
│   ├── responder.py       # 차단 / 격리 / 복구
│   └── Dockerfile
│
├── llm/
│   ├── ollama_client.py   # 로컬 LLM API 호출
│   └── prompts/
│       ├── attack.txt     # 공격 에이전트 프롬프트
│       └── defense.txt    # 방어 에이전트 프롬프트
│
├── sim/
│   ├── uav/               # PX4 SITL
│   └── ugv/               # Gazebo / ROS2
│
├── satnet/
│   └── netem_config.sh    # tc/netem 위성 네트워크 모사
│
├── docs/
│   ├── SCENARIO.md        # 시나리오 상세 (공격/방어 흐름)
│   └── architecture.png
│
├── docker-compose.yml
├── .env.example
└── requirements.txt
```

---

## 환경 구성

### Docker 컨테이너

| 컨테이너 | 역할 |
|---|---|
| `dah-uav` | UAV 상태·센서 데이터 생성 (PX4 SITL) |
| `dah-ugv` | UGV 위치·상태 데이터 생성 (Gazebo/ROS2) |
| `dah-satnet` | 위성 통신 지연·손실 모사 (tc/netem) |
| `dah-gcs` | MAVLink 수신·명령 처리 (QGroundControl) |
| `dah-command` | 작전 명령 생성·전달 (C2 Server) |
| `dah-attack-ai` | 공격 에이전트 실행 |
| `dah-defense-ai` | 방어 에이전트 실행 |
| `dah-monitor` | 로그·트래픽·점수 수집 |
| `dah-llm` | Ollama 로컬 LLM API 서버 |

### 위성 네트워크 모사

```bash
tc qdisc add dev eth0 root netem delay 600ms loss 2% rate 2mbit
```

### 네트워크 구성

```
dah-uav       172.20.0.10
dah-ugv       172.20.0.11
dah-gcs       172.20.0.20
dah-satnet    172.20.0.30   ← 패킷이 반드시 통과하는 게이트웨이
dah-attack-ai 172.20.0.40
dah-defense-ai 172.20.0.50
dah-monitor   172.20.0.60
dah-llm       172.20.0.70
```

---

## AI Agent 입출력 포맷

### LLM 입력 (공통)

```json
{
  "role": "attack_agent | defense_agent",
  "system_state": {
    "uav": { "status": "normal", "gps": [37.1, 127.0], "battery": 82 },
    "ugv": { "status": "moving", "position": [10, 20] },
    "network": { "latency_ms": 580, "loss_pct": 1.8 }
  },
  "available_actions": ["inject_mavlink_command", "spoof_gps", "jam_link", "falsify_isr"],
  "recent_logs": ["..."],
  "objective": "임무 가용성 저하 | 가용성 유지"
}
```

### LLM 출력

```json
{
  "selected_action": "inject_mavlink_command",
  "reason": "배터리 82%, 임무 핵심 구간 — LAND 주입 시 임무 실패 가능성 높음",
  "target": "uav_mavlink_stream",
  "expected_effect": "UAV 강제 착륙",
  "confidence": 0.92
}
```

---

## 역할 분담

| 역할 | 담당 | 주요 작업 |
|---|---|---|
| Defense / Detection | 1명 | MAVLink 로그 수집, 이상 탐지 룰, 사고 대응 함수 |
| Attack / Recon | 1명 | 공격 표면 분석, MAVLink 패킷 조작, 공격 함수 |
| AI Agent + Infra | 1명 | Observer→Analyzer→Planner→Executor, LLM 연동, Docker |

---

## 핵심 레퍼런스

| 자료 | 용도 |
|---|---|
| Empirical Analysis of MAVLink Vulnerability (IEEE, 2021) | 공격 성공률 90~95% 수치 근거 |
| MAVSec: Securing MAVLink for ArduPilot/PX4 (arXiv) | 방어 아키텍처 설계 근거 |
| MUVIDS: False MAVLink Injection Detection (NDSS) | 방어 AI 탐지 로직 근거 |
| Counter-UAS: State of the Art | GPS Spoofing / Jamming 대응 근거 |
| UAV Command and Control, Navigation and Surveillance | 위성 통신 기반 UAS 구조 근거 |

---

## 빠른 시작

```bash
git clone https://github.com/YOUR_TEAM/aidah2026.git
cd aidah2026
cp .env.example .env
docker compose up --build
```

---

## 제출 파일명

- 보고서: `DAH2026_예선보고서_[팀명].pdf`
- 소스코드: `DAH2026_소스코드_[팀명].zip`
