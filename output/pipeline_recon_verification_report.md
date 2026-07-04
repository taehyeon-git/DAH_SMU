# DAH_SMU Pipeline Recon Verification Report

작성일: 2026-07-04  
대상 폴더: `C:\temp_git\DAH_SMU`  
대상 시나리오: `Low-Privilege Sentinel (DAH_SMU JSON telemetry pipeline)`

## 1. 요약

기존 정찰 시나리오는 `14550/udp` MAVLink 브로드캐스트를 수신한다고 가정했지만, DAH_SMU의 실제 구조는 `dah-uav -> dah-companion -> dah-gcs -> fan-out` 형태이다.

따라서 정찰 지점을 MAVLink 원본 수신 포트가 아니라 GCS 이후 JSON telemetry fan-out 경로로 변경했다.

현재 구현은 다음 구조로 동작한다.

```text
dah-uav
  -> dah-companion
  -> dah-gcs
  -> dashboard / collector / tactical-router
  -> dah-recon:14572
```

검증 가능한 범위에서는 정찰 로직이 정상 동작했다. Synthetic telemetry 기준으로 `UAV-001`을 식별했고, confidence `1.00`, 패턴 `PATROL_TRANSIT`를 출력했다.

단, 실제 Docker 컨테이너 실행 검증은 Docker Desktop Linux engine API 장애로 완료하지 못했다.

## 2. 변경된 파일

### `gcs/app.py`

GCS telemetry fan-out 대상에 선택적 Recon Tap을 추가했다.

```python
RECON_TAP_HOST = os.getenv("RECON_TAP_HOST", "")
RECON_TAP_PORT = int(os.getenv("RECON_TAP_PORT", "14572"))
```

`fanout()`은 기존 Dashboard, Collector, Tactical Router에 더해 `RECON_TAP_HOST`가 설정된 경우 `dah-recon:14572`로 동일 telemetry를 전송한다.

### `docker-compose.yml`

`dah-gcs` 환경 변수에 Recon Tap을 추가했다.

```yaml
RECON_TAP_HOST: dah-recon
RECON_TAP_PORT: 14572
```

`dah-recon`의 수신 포트도 기존 `14550`에서 `14572`로 변경했다.

```yaml
--listen-port
"14572"
```

### `attack_agent/recon.py`

정찰 모듈을 DAH_SMU 구조에 맞게 재작성했다.

핵심 변경:

- MAVLink 브로드캐스트 전제 제거
- JSON telemetry pipeline 수신 지원
- `UAV-001`을 `SYS_ID=1`로 매핑
- `lat`, `lon`, `alt`, `speed`, `fuel`, `ticn` 필드 수집
- confidence scoring 유지
- 위치 예측 및 패턴 분류 유지
- `intel_handoff.json` 생성 유지
- raw socket, `CAP_NET_RAW`, 패킷 주입 없음

## 3. 검증 명령 및 결과

### 3.1 Python 문법 검증

명령:

```powershell
python -m py_compile gcs\app.py attack_agent\recon.py attack_agent\mavlink_parser.py
```

결과:

```text
PASS
```

### 3.2 Compose 구성 검증

명령:

```powershell
docker compose --profile cyber-lab config --services
```

결과:

```text
tactical-router
telemetry-collector
dah-gcs
dah-companion
dah-executor
mission-control
dah-dashboard
dah-gateway
dah-inducer
dah-jammer
dah-uav
dah-ugv
dah-defense
dah-recon
dah-spoofer
```

해석:

`cyber-lab` profile 기준으로 `dah-recon` 서비스가 정상 해석된다.

### 3.3 Synthetic UDP Telemetry 검증

실제 Docker engine이 불안정했기 때문에, 동일한 UDP JSON telemetry를 로컬에서 `127.0.0.1:14572`로 주입해 정찰 파이프라인 자체를 검증했다.

검증 입력:

```json
{
  "platform_id": "UAV-001",
  "platform_type": "UAV",
  "message_type": "telemetry",
  "source": "companion_computer/MAVLink",
  "status": "ACTIVE",
  "lat": 37.895,
  "lon": 126.800,
  "alt": 3500,
  "speed": 600,
  "fuel": 78,
  "ticn": {
    "loss_pct": 0,
    "link_quality": 100
  }
}
```

결과:

```text
packets: 8
json_packets: 8
parse_errors: 0
assets: 1
UAV-001: Y
msg_types: {'JSON_TELEMETRY': 8}
sources: {'companion_computer/MAVLink': 8}
confidence: 1.00
pattern: PATROL_TRANSIT
```

출력 파일:

- `output/synthetic_pipeline_recon.json`
- `output/intel_handoff.json`

## 4. 결과값 분석

### 4.1 Collection Summary

```json
{
  "packet_count": 8,
  "parse_errors": 0,
  "unknown_msgs": 0,
  "json_packets": 8,
  "pipeline_sources": {
    "companion_computer/MAVLink": 8
  },
  "asset_count": 1,
  "uav001_identified": true,
  "msg_type_counts": {
    "JSON_TELEMETRY": 8
  }
}
```

해석:

- JSON telemetry 수신 경로는 정상 동작한다.
- `UAV-001` 식별이 정상적으로 수행됐다.
- 파싱 오류는 없다.
- 수집 source가 `companion_computer/MAVLink`로 기록된다.

### 4.2 UAV-001 상태 복원

```json
{
  "platform_id": "UAV-001",
  "mav_type": "FIXED_WING",
  "system_status": "ACTIVE",
  "is_armed": true,
  "is_guided": true,
  "lat_deg": 37.8964,
  "lon_deg": 126.8007,
  "alt_m": 3500.0,
  "ground_speed_mps": 166.67,
  "heading_deg": 0.0,
  "battery_pct": 78,
  "drop_rate_comm": 0,
  "pipeline_seq": 8,
  "in_operational_area": true
}
```

해석:

- 위치, 고도, 속도, 연료, 링크 손실률을 복원한다.
- `speed=600km/h`는 `166.67m/s`로 변환된다.
- 작전구역 내부 여부도 계산된다.
- `UAV-001`은 고속 순항 중인 `FIXED_WING` 자산으로 분류된다.

### 4.3 Confidence Score

결과:

```text
confidence = 1.00
label = HIGH - usable for controlled follow-on validation
```

충족 요인:

```text
message_repetition: true
position_repetition: true
physical_consistency: true
cross_message_validation: true
frame_or_pipeline_integrity: json_pipeline
freshness: true
```

해석:

단일 telemetry가 아니라 반복 수신, 위치 반복성, 물리 일관성, freshness가 모두 충족됐다. 따라서 DAH_SMU 환경에서는 JSON telemetry fan-out만으로도 후속 검증에 사용할 수 있는 고신뢰 정찰 결과를 만들 수 있다.

### 4.4 Pattern Classification

결과:

```text
PATROL_TRANSIT
```

근거:

- 속도 `166.67m/s`
- 작전구역 내부 위치
- 반복 위치 샘플 존재

해석:

자산이 정지 또는 LOITER 상태가 아니라, 작전구역 내 고속 순항 중인 것으로 분류된다.

### 4.5 Prediction

```json
{
  "model": "constant_velocity_short_horizon",
  "horizon_s": 60,
  "lat": 37.9862329,
  "lon": 126.8007,
  "alt_m": 3500.0,
  "expected_error_m": 45.0,
  "in_operational_area": false
}
```

해석:

60초 단기 예측을 수행한다. 현재 synthetic 입력에서는 북향 속도 성분으로 처리되어 예측 위치가 작전구역 바깥으로 계산됐다. 실제 컨테이너 운용 telemetry에서는 heading/속도 성분 모델 보정 여지가 있다.

## 5. Docker 컨테이너 검증 상태

실제 Docker 컨테이너 로그와 output 파일 검증을 시도했으나 Docker Desktop Linux engine이 정상 응답하지 않았다.

확인된 상태:

```text
\\.\pipe\dockerDesktopLinuxEngine = False
\\.\pipe\docker_engine = False
com.docker.service = Stopped
WSL Ubuntu = Stopped
WSL kali-linux = Stopped
```

시도한 조치:

```powershell
Start-Service com.docker.service
Start-Process "C:\Program Files\Docker\Docker\Docker Desktop.exe"
wsl --shutdown
Docker Desktop 재시작
Docker Desktop 관리자 권한 실행
```

계속 발생한 오류:

```text
open //./pipe/dockerDesktopLinuxEngine: The system cannot find the file specified
```

또는:

```text
request returned 500 Internal Server Error for API route ...
```

판단:

현재 실패는 `dah-recon` 코드나 compose 구성 문제가 아니라 Docker Desktop Linux backend 기동 문제다.

## 6. 실제 Docker 검증 절차

Docker Desktop engine이 정상화되면 아래 순서로 검증하면 된다.

```powershell
cd C:\temp_git\DAH_SMU
docker compose --profile cyber-lab up -d --build
docker logs dah-gcs --tail 120
docker logs dah-recon --tail 120
Get-Content .\output\passive_mavlink_intel.json -Raw
Get-Content .\output\intel_handoff.json -Raw
```

정상 기대값:

```text
dah-recon 로그:
  [phase1] <gcs-ip> json platform=UAV-001 source=companion_computer/MAVLink

collection_summary:
  json_packets > 0
  asset_count >= 1
  uav001_identified = true
  parse_errors = 0

uav001:
  confidence.score >= 0.80
  state.lat_deg / lon_deg / alt_m 존재
  pattern 존재
```

## 7. 결론

2번 방향, 즉 DAH_SMU 구조에 맞춘 `JSON telemetry pipeline recon`은 로직 및 출력 검증을 통과했다.

핵심 결론:

- 기존 `14550 MAVLink broadcast` 정찰은 DAH_SMU 구조와 맞지 않았다.
- DAH_SMU에서는 GCS 이후 telemetry fan-out이 더 현실적인 정찰 지점이다.
- `dah-recon:14572` tap 방식으로 구조를 맞췄다.
- Synthetic telemetry 기준으로 결과값은 정상 출력된다.
- 실제 Docker 컨테이너 검증은 Docker Desktop Linux engine 장애로 보류됐다.

현재 상태에서 코드/compose 문제보다 우선 해결해야 할 것은 Docker Desktop backend 정상화다.
