from __future__ import annotations
from typing import Any

from attack_agent.core.schemas import CandidateAction, GCSModel, IntelDocument


def _latest_api(doc: IntelDocument, service: str, path: str) -> dict:
    for obs in doc.observations:
        if obs.get("type") == "api_response" and obs.get("service") == service and obs.get("path") == path:
            return obs.get("body", {})
    return {}


def _current_vehicle_state(doc: IntelDocument) -> dict:
    live = _latest_api(doc, "dashboard", "/api/live")
    for item in live.get("platforms", []):
        if item.get("platform_id") == "UAV-001":
            return item
    for asset in doc.assets:
        if asset.asset_id == "UAV-001":
            return asset.observed_state
    return {}


def _observation_value(doc: IntelDocument, obs_type: str) -> dict:
    for obs in reversed(doc.observations):
        if obs.get("type") == obs_type and isinstance(obs.get("value"), dict):
            return obs["value"]
    return {}


def _has_recon_tag(doc: IntelDocument, tag: str) -> bool:
    tags = _observation_value(doc, "recon_tags").get("tags", [])
    return tag in tags


def _api_baseline(doc: IntelDocument) -> dict:
    baseline = _observation_value(doc, "api_baseline")
    if baseline:
        return baseline
    failsafe = _latest_api(doc, "dashboard", "/api/failsafe")
    live = _latest_api(doc, "dashboard", "/api/live")
    if failsafe or live:
        return {"api_available": bool(failsafe or live), "failsafe_action": failsafe.get("failsafe_action")}
    return {}


def reconstruct_gcs(doc: IntelDocument) -> IntelDocument:
    failsafe = _latest_api(doc, "dashboard", "/api/failsafe")
    baseline = _api_baseline(doc)
    current = _current_vehicle_state(doc)
    model = GCSModel(
        telemetry_ingress="UAV-001 -> dah-companion -> dah-gcs -> dah-dashboard",
        command_egress="dah-dashboard/mission-control -> dah-gcs/dah-companion -> UAV-001",
        dashboard_command_path="dah-dashboard /api/command -> MAVLink COMMAND_LONG -> UAV-001:14551",
        upper_c2_command_path="mission-control -> dah-tactical-router:14546 -> dah-gcs:14562 -> dah-companion:14552 -> UAV-001:14551",
        heartbeat_behavior=failsafe.get("heartbeat", {"interval_sec": 1, "timeout_sec": 5}),
        failsafe_policy=failsafe,
        trust_assumptions=[
            "GCS accepts companion JSON telemetry on UDP 14555 inside the lab.",
            "Dashboard trusts GCS /api/dashboard and direct UDP fan-out state.",
            "Router link metrics influence dashboard communication-loss presentation.",
            "Command source validation is evaluated by defense-agent, not enforced everywhere by default.",
        ],
        weak_points=[
            "TMMR/TICN link state can be degraded through dah-jammer.",
            "Protocol parser resilience can be tested through synthetic tamper module.",
        ],
        current_vehicle_state=current,
    )
    doc.gcs_model = model
    doc.candidate_actions = build_candidate_actions(model, doc)
    doc.recommended_chain = [action.action_id for action in doc.candidate_actions[:4]]
    doc.confidence = 0.8 if current else 0.6
    doc.observations.append({
        "type": "gcs_reconstruction",
        "weak_point_count": len(model.weak_points),
        "candidate_action_count": len(doc.candidate_actions),
        "selection_source": "InitialAccessAgent",
        "api_baseline_available": bool(baseline),
    })
    return doc



# ── fail-safe 유발 분석 헬퍼 (인증부재/정셋/표적 식별) ──

def _failsafe_values(model: GCSModel, doc: IntelDocument | None = None) -> dict[str, Any]:
    """failsafe 임계값 추출 — /api/failsafe(중첩) 우선, 없으면 정찰 baseline(평탄) fallback."""
    policy = model.failsafe_policy or {}
    base = _api_baseline(doc)
    return {
        "hb_timeout_sec":    policy.get("heartbeat", {}).get("timeout_sec", base.get("hb_timeout_sec")),
        "hb_interval_sec":   policy.get("heartbeat", {}).get("interval_sec", base.get("hb_interval_sec")),
        "loss_critical_pct": policy.get("packet_loss", {}).get("critical_pct", base.get("loss_critical_pct")),
        "loss_duration_sec": policy.get("packet_loss", {}).get("critical_duration_sec", base.get("loss_duration_sec")),
        "latency_critical_ms": policy.get("latency", {}).get("critical_ms", base.get("latency_critical_ms")),
        "failsafe_action":   policy.get("failsafe_action", base.get("failsafe_action")),
    }


def _frame_security(doc: IntelDocument | None) -> tuple[int | None, int | None]:
    """수동 MAVLink 청취에서 수집된 서명 프레임 카운트 (signed, unsigned)."""
    if doc is None:
        return None, None
    for obs in doc.observations:
        if obs.get("type") == "passive_recon_summary":
            cs = obs.get("collection_summary", {}) or {}
            if "signed_frames" in cs or "unsigned_frames" in cs:
                return cs.get("signed_frames"), cs.get("unsigned_frames")
    for asset in doc.assets:
        st = asset.observed_state or {}
        if "unsigned_frames" in st or "signed_frames" in st:
            return st.get("signed_frames"), st.get("unsigned_frames")
    return None, None


def _uav_identity(model: GCSModel, doc: IntelDocument | None) -> dict[str, Any]:
    """주입 표적 식별 — sys_id / 명령포트 / 호스트."""
    ident = {"sys_id": 1, "cmd_port": 14551, "target_host": "dah-uav"}
    st = model.current_vehicle_state or {}
    ident["sys_id"] = st.get("sys_id", ident["sys_id"])
    if doc is not None:
        for asset in doc.assets:
            if asset.asset_type == "UAV" or asset.asset_id == "UAV-001":
                obs = asset.observed_state or {}
                ident["sys_id"] = obs.get("sys_id", ident["sys_id"])
                if asset.ports:
                    ident["cmd_port"] = asset.ports[0]
                break
    return ident



# ── 정찰 데이터 → 취약점 분석 → 후속공격 후보 생성 ──

def build_candidate_actions(model: GCSModel, doc: IntelDocument | None = None) -> list[CandidateAction]:
    """정찰 산출물을 근거로 fail-safe 유발 후보(A-1~B-2 + tamper)를 도출한다."""
    fs = _failsafe_values(model, doc)
    signed, unsigned = _frame_security(doc)
    ident = _uav_identity(model, doc)
    unsigned_confirmed = signed == 0 and (unsigned or 0) > 0
    auth_absent = unsigned_confirmed or signed is None  # 양성 서명 증거 없으면 위장 가능
    actions: list[CandidateAction] = []

    # A-3. 위조 COMMAND_LONG 직접 주입 — 최고 심각도(강제 착륙)
    if auth_absent:
        actions.append(CandidateAction(
            action_id="ACT-CMDINJECT-001",
            agent="dah-mavlink-injector",
            action_type="MAVLINK_COMMAND_INJECTION",
            reason=(
                f"MAVLink 무서명(signed={signed}/unsigned={unsigned}) 관측 — "
                "COMMAND_LONG 출처검증 부재로 위조 명령 직접 실행 가능."
            ),
            required_params=["target_host", "cmd_port", "command", "spoof_src_sys"],
            params={
                "target_host": ident["target_host"],
                "cmd_port": ident["cmd_port"],
                "command": "NAV_LAND",
                "spoof_src_sys": 99,
                "target_sys": ident["sys_id"],
            },
            preconditions=["MAVLink 무서명(signed_frames=0)"],
            expected_effect="NAV_LAND 주입 → UAV LANDING(고도 하강→착륙/추락). RTL/LOITER/PAUSE도 가능.",
            risk="HIGH",
            confidence=0.92 if unsigned_confirmed else 0.78,
        ))

    # A-1. Heartbeat 상태 위조 — CRITICAL→LOITER / EMERGENCY→RTL
    if auth_absent:
        actions.append(CandidateAction(
            action_id="ACT-HBSPOOF-001",
            agent="dah-mavlink-injector",
            action_type="MAVLINK_STATUS_SPOOF",
            reason=(
                "GCS(SYS_ID 255) heartbeat가 서명검증 없이 수용되고 system_status를 "
                "그대로 반영 → 위조 CRITICAL/EMERGENCY로 fail-safe 간접 유도."
            ),
            required_params=["target_host", "cmd_port", "spoof_src_sys", "system_status"],
            params={
                "target_host": ident["target_host"],
                "cmd_port": ident["cmd_port"],
                "spoof_src_sys": 255,
                "system_status": "CRITICAL",
            },
            preconditions=["MAVLink 무서명(signed_frames=0)"],
            expected_effect="위조 heartbeat CRITICAL → LOITER / EMERGENCY → RTL.",
            risk="MEDIUM",
            confidence=0.85 if unsigned_confirmed else 0.72,
        ))

    # B-1. 전술링크 손실률 임계 (EW 재밍) — 3채널 blackout으로 LOITER
    if fs.get("loss_critical_pct") is not None:
        actions.append(CandidateAction(
            action_id="ACT-JAMMER-001",
            agent="dah-jammer",
            action_type="EW_LINK_DEGRADATION_SIM",
            reason=(
                f"loss_critical_pct={fs['loss_critical_pct']}% 노출 — 링크 손실률을 "
                "임계 위로 밀면 link_monitor가 LOITER 전환. TMMR 자동 홉 때문에 3채널 동시 필요."
            ),
            required_params=["router_host", "jam_port", "channels"],
            params={
                "router_host": "dah-tactical-router",
                "jam_port": 14590,
                "channels": ["VHF", "UHF", "HF"],
                "duration_sec": 14,
                "target_loss_pct": fs["loss_critical_pct"],
            },
            preconditions=["VHF+UHF+HF 3채널 동시 재밍(blackout)"],
            expected_effect="3채널 blackout → loss_pct=100 → UAV LOITER.",
            risk="MEDIUM",
            confidence=0.86,
        ))

    # B-2. 임계값 격차 악용 (탐지 회피 재밍) — B-1 은신 변형
    if fs.get("loss_critical_pct") is not None:
        actions.append(CandidateAction(
            action_id="ACT-JAMMER-STEALTH-001",
            agent="dah-jammer",
            action_type="EW_STEALTH_DEGRADATION_SIM",
            reason=(
                f"UAV LOITER 임계({fs['loss_critical_pct']}%)와 방어 탐지 임계의 격차 — "
                "손실률을 임계~방어탐지 사이 band에 유지하면 유발+미탐지."
            ),
            required_params=["router_host", "jam_port", "channels", "observe_url"],
            params={
                "router_host": "dah-tactical-router",
                "jam_port": 14590,
                "channels": ["VHF", "UHF", "HF"],
                "duration_sec": 14,
                "observe_url": "/api/live",
                "target_loss_min": fs["loss_critical_pct"],
                "target_loss_max": 49,
            },
            preconditions=["/api/live 폐루프 관측으로 방어 반응 상한 실측"],
            expected_effect="loss 15~49% 유지 → UAV LOITER + 방어 FREQ-HOP 미발동.",
            risk="MEDIUM",
            confidence=0.70,
        ))

    # A-2. Heartbeat Timeout 두절 유도 — 5초 무수신 시 LOITER
    if fs.get("hb_timeout_sec") is not None:
        silence = int(fs["hb_timeout_sec"]) + 2
        actions.append(CandidateAction(
            action_id="ACT-HBTIMEOUT-001",
            agent="dah-mavlink-injector",
            action_type="HB_TIMEOUT_INDUCTION",
            reason=(
                f"hb_timeout_sec={fs['hb_timeout_sec']}s 노출 — GCS heartbeat 감시가 "
                "유무에만 의존, 침묵으로 통신두절 fail-safe 유도."
            ),
            required_params=["target_host", "cmd_port", "silence_sec"],
            params={
                "target_host": ident["target_host"],
                "cmd_port": ident["cmd_port"],
                "hb_timeout_sec": fs["hb_timeout_sec"],
                "silence_sec": silence,
            },
            preconditions=["UAV mode==MISSION 상태에서 적용"],
            expected_effect=f"heartbeat {silence}s 차단 → 두절 판정 → LOITER.",
            risk="LOW",
            confidence=0.75,
        ))

    # (참고) 프로토콜 프레임 무결성 — fail-safe 유발 아님, 파서 저항성 테스트용
    actions.append(CandidateAction(
        action_id="ACT-TAMPER-001",
        agent="tamper",
        action_type="PROTOCOL_FRAME_INTEGRITY_SIM",
        reason="MAVLink CRC/STX/sequence/signature 메타 관측 — 합성 변이로 파서 저항성 테스트.",
        required_params=["dst_host", "dst_port", "mutation"],
        params={"dst_asset": "local-parser", "dst_host": "localhost", "dst_port": 14550, "mutation": "FRAME_CRC_BREAK", "protocol": "MAVLink-like"},
        expected_effect="합성 파서/무결성 리포트에 CRC/STX/signature 검증 실패 기록.",
        risk="LOW",
        confidence=0.9,
    ))

    return actions
