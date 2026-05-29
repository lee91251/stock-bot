"""🛡️ 검증부 (verify.py) — Phase 2 2단계 (5/29 회장 복귀 후 진행)

설계 문서: Obsidian Vault/11 - Phase 2 설계도 (14부서).md
체크리스트: Obsidian Vault/12 - Phase 2 실행 체크리스트.md
회장 메모리: feedback_numbers_verification.md + feedback_paper_as_real.md

책임: 매수/매도 실행 *직전* 게이트. 통과해야만 실행부 호출.

원칙 (회장 메모리):
- 한 부서가 다른 부서 일 직접 X (위임만)
- 거절(REJECT)도 정식 답변 — 매수부는 우회 시도 X
- 같은 사고 2번 차단 — 사고 패턴 학습

# 매수 게이트 15개 (회장 5/8~5/9 확정)
# 매도 게이트 8개 (회장 5/8~5/9 확정)
"""

import os
import json
from datetime import datetime
from finance import (
    load_positions, load_mirae_paper, _now_kst, _today_str,
    POSITIONS_FILE, MIRAE_PAPER_FILE,
)


# ════════════════════════════════════════════════
# 한국 거래소 휴장일 (stock.py와 동기화 — Phase 2 향후 시장정보부로 이동 예정)
# ════════════════════════════════════════════════
_KRX_HOLIDAYS = {
    "2026-01-01",  # 신정 (목)
    "2026-02-16", "2026-02-17", "2026-02-18",  # 설날 (월화수)
    "2026-03-02",  # 삼일절 대체 (3/1 일)
    "2026-05-01",  # 근로자의 날 (금)
    "2026-05-05",  # 어린이날 (화)
    "2026-05-25",  # 부처님오신날 대체 (5/24 일)
    "2026-06-03",  # 지방선거 (수)
    "2026-09-24", "2026-09-25",  # 추석 연휴 (목금)
    "2026-10-09",  # 한글날 (금)
    "2026-12-25",  # 크리스마스 (금)
    "2026-12-31",  # 연말 종가일 (목)
}


# ════════════════════════════════════════════════
# 한도 설정 (회장 메모리 — 5/13 Regime-Adaptive 그대로)
# ════════════════════════════════════════════════
SCORE_MIN          = 65          # 매수 점수 임계 (회장 5/13 결정)
INVEST_PER_STOCK   = 2_000_000   # 종목당 한도 200만원
MAX_DAILY_BUY      = 5           # 일일 종목 한도 5개
MAX_DAILY_AMT      = 10_000_000  # 일일 금액 한도 1,000만원
PRICE_DIFF_PCT     = 0.02        # 시세 cross-check ±2%
AMOUNT_DIFF_KRW    = 100         # 산술 일치 ±100원
CHASE_BUY_LIMIT    = 0.05        # 추격매수 차단 +5% (시장환경 따라 조정)
MARKET_RISK_LIMIT  = 75          # 시장 위험 지수 차단
SECTOR_CONCENTRATION_PCT = 0.40  # 섹터 집중도 40%

STOP_LOSS_PCT      = 0.07        # 손절선 -7%
TARGET1_PCT        = 0.10        # 1차 익절 +10%
TARGET2_PCT        = 0.20        # 2차 익절 +20%
LONG_TARGET_PCT    = 0.40        # 장기 목표 +40%


# ════════════════════════════════════════════════
# 응답 형식
# ════════════════════════════════════════════════
def _reject(gate: str, reason: str, **detail) -> dict:
    """REJECT 응답 — 게이트 명시 + 사유 + 학습부 로그용 detail."""
    return {"ok": False, "gate": gate, "reason": reason, "detail": detail}


def _ok(notes: list = None) -> dict:
    """OK 응답 — 통과한 게이트 모두 통과 / 경고 정보 가능."""
    return {"ok": True, "rejects": [], "warnings": notes or []}


# ════════════════════════════════════════════════
# 매수 게이트 15개 — verify_buy(req)
# ════════════════════════════════════════════════
def verify_buy(req: dict) -> dict:
    """매수 요청서 검증 — 게이트 15개.

    req = {
        "code": "005930",       # 종목 6자리
        "name": "삼성전자",
        "qty": 30,              # 매수 수량
        "price": 70000,         # 매수 단가
        "amount": 2100000,      # 매수 총액 (qty × price)
        "score": 67,            # 매수 점수
        "sector": "반도체",       # 섹터 (선택)
        "curr_price": 70200,    # 현재 시장가 (cross-check용)
        "day_change_pct": 2.5,  # 당일 변동률 (추격매수 차단용)
        "market_risk": 30,      # 시장 위험 지수 (0~100)
        "blacklist": [],        # 블랙리스트 종목 코드들
        "recent_failures": [],  # 최근 5건 사고 패턴
        "now_kst": None,        # 현재 시각 (None이면 _now_kst())
    }

    Returns:
        {"ok": True, "rejects": [], "warnings": [...]}
        또는
        {"ok": False, "rejects": [{"gate":..., "reason":..., "detail":...}], "warnings": [...]}
    """
    rejects = []
    warnings = []

    code   = req.get("code", "")
    name   = req.get("name", "")
    qty    = int(req.get("qty", 0))
    price  = float(req.get("price", 0))
    amount = float(req.get("amount", 0))
    score  = int(req.get("score", 0))
    sector = req.get("sector", "")
    curr_price = float(req.get("curr_price", price))
    day_change_pct = float(req.get("day_change_pct", 0))
    market_risk = int(req.get("market_risk", 0))
    blacklist = req.get("blacklist", [])
    recent_failures = req.get("recent_failures", [])
    now = req.get("now_kst") or _now_kst()

    # 봇 내부 상태 (재무부에서 로드)
    pos = load_positions()
    halted = pos.get("halted", False) or pos.get("halted_until_date") == _today_str()
    positions = pos.get("positions", {})
    daily = pos.get("daily", {}).get(_today_str(), {})
    today_bought_codes = {
        h.get("code") for h in pos.get("history", [])
        if h.get("date") == _today_str() and h.get("side") == "buy"
    }

    # ── 게이트 1: 종목 코드 유효 ──
    if not code or len(code) != 6 or not code.isdigit():
        rejects.append(_reject(
            "종목코드", f"코드 무효: '{code}' (6자리 숫자 필요)",
            code=code,
        ))
        return {"ok": False, "rejects": rejects, "warnings": warnings}  # 즉시 종료

    # ── 게이트 2: 장 시간 (09:00~15:30 KST) ──
    if not (9 <= now.hour < 15 or (now.hour == 15 and now.minute < 30)):
        rejects.append(_reject(
            "장시간", f"장 시간 외 {now.strftime('%H:%M')} (09:00~15:30 KST)",
            now=now.strftime("%H:%M"),
        ))

    # ── 게이트 3: 휴장일 X ──
    today_str = now.strftime("%Y-%m-%d")
    if now.weekday() >= 5 or today_str in _KRX_HOLIDAYS:
        rejects.append(_reject(
            "휴장일", f"휴장일 {today_str} ({now.strftime('%A')})",
            date=today_str,
        ))

    # ── 게이트 4: 같은 종목 1일 1회 (중복 차단) ──
    if code in today_bought_codes:
        rejects.append(_reject(
            "중복매수", f"오늘 이미 매수한 종목: {name}",
            code=code,
        ))

    # ── 게이트 5: 일일 한도 (5종목 / 1,000만원) ──
    cur_count = daily.get("buy_count", 0)
    cur_amount = daily.get("buy_amount", 0)
    if cur_count >= MAX_DAILY_BUY:
        rejects.append(_reject(
            "일일종목한도", f"일일 종목 {cur_count}/{MAX_DAILY_BUY} 도달",
            current=cur_count, limit=MAX_DAILY_BUY,
        ))
    if cur_amount + amount > MAX_DAILY_AMT:
        rejects.append(_reject(
            "일일금액한도", f"일일 금액 {(cur_amount + amount):,.0f}원 > 한도 {MAX_DAILY_AMT:,}원",
            current=cur_amount, requested=amount, limit=MAX_DAILY_AMT,
        ))

    # ── 게이트 6: 종목당 한도 (200만원) ──
    if amount > INVEST_PER_STOCK:
        rejects.append(_reject(
            "종목한도", f"매수금액 {amount:,.0f}원 > 종목당 한도 {INVEST_PER_STOCK:,}원",
            amount=amount, limit=INVEST_PER_STOCK,
        ))

    # ── 게이트 7: 점수 임계 (≥65) ──
    if score < SCORE_MIN:
        rejects.append(_reject(
            "점수임계", f"점수 {score} < {SCORE_MIN} (회장 5/13 결정)",
            score=score, min=SCORE_MIN,
        ))

    # ── 게이트 8 ⭐: 시세 cross-check (현재가 ±2%) ──
    if curr_price > 0 and price > 0:
        diff_pct = abs(curr_price - price) / price
        if diff_pct > PRICE_DIFF_PCT:
            rejects.append(_reject(
                "시세_cross_check", f"매수가 {price:,.0f}원 vs 현재가 {curr_price:,.0f}원 ({diff_pct*100:.2f}%)",
                price=price, curr_price=curr_price, diff_pct=round(diff_pct*100, 2),
            ))

    # ── 게이트 9 ⭐: 산술 일치 (평단×수량 = 매수금액 ±100원) ──
    expected_amount = qty * price
    if abs(expected_amount - amount) > AMOUNT_DIFF_KRW:
        rejects.append(_reject(
            "산술_일치", f"평단 {price:,.0f} × 수량 {qty} = {expected_amount:,.0f}원 ≠ 매수금액 {amount:,.0f}원",
            qty=qty, price=price, expected=expected_amount, actual=amount,
        ))

    # ── 게이트 10: 추격매수 차단 (당일 +5% 이상) ──
    if day_change_pct > CHASE_BUY_LIMIT * 100:
        rejects.append(_reject(
            "추격매수", f"당일 +{day_change_pct:.1f}% > +{CHASE_BUY_LIMIT*100:.0f}% — 추격매수 차단",
            day_change=day_change_pct,
        ))

    # ── 게이트 11: 비상정지 발동 시 매수 X ──
    if halted:
        rejects.append(_reject(
            "비상정지", "리스크부 비상정지 발동 중",
            halted=True,
        ))

    # ── 게이트 12: 시장 위험 지수 (75 이상 차단) ──
    if market_risk >= MARKET_RISK_LIMIT:
        rejects.append(_reject(
            "시장위험", f"시장 위험 지수 {market_risk} ≥ {MARKET_RISK_LIMIT}",
            market_risk=market_risk,
        ))

    # ── 게이트 13: 잔고 충분 ── (실제 KIS API 잔고 조회 필요 — 현재는 일일한도 체크로 대체)
    # TODO: Phase 2 5단계(실행부)에서 KIS API 잔고 조회 연결
    # 현재는 일일 금액 한도로 간접 검증 (게이트 5)

    # ── 게이트 14 ⭐: 같은 사고 검출 (최근 5건 동일 패턴) ──
    if recent_failures:
        # 같은 종목 최근 5건 매도 *전부 손실*이면 의심
        same_code_failures = [f for f in recent_failures if f.get("code") == code]
        if len(same_code_failures) >= 3 and all(f.get("profit", 0) < 0 for f in same_code_failures):
            rejects.append(_reject(
                "사고패턴", f"{name} 최근 {len(same_code_failures)}건 매수 모두 손실 — 패턴 회피",
                code=code, recent_losses=len(same_code_failures),
            ))

    # ── 게이트 15: 블랙리스트 (관리종목/거래정지) ──
    if code in blacklist:
        rejects.append(_reject(
            "블랙리스트", f"블랙리스트 종목 — 거래정지/관리종목",
            code=code,
        ))

    # 응답
    if rejects:
        return {"ok": False, "rejects": rejects, "warnings": warnings}
    return _ok(warnings)


# ════════════════════════════════════════════════
# 매도 게이트 8개 — verify_sell(req)
# ════════════════════════════════════════════════
def verify_sell(req: dict) -> dict:
    """매도 요청서 검증 — 게이트 8개.

    req = {
        "code": "005930",
        "name": "삼성전자",
        "qty": 30,              # 매도 수량
        "price": 75000,         # 매도 단가 (호가)
        "pct": 7.14,            # 현재 수익률 %
        "sell_reason": "절반 익절",  # 매도 사유
        "ai_advisor_score": 0.7,    # AI 매도 어드바이저 점수 0~1 (선택)
        "now_kst": None,
    }
    """
    rejects = []
    warnings = []

    code = req.get("code", "")
    name = req.get("name", "")
    qty = int(req.get("qty", 0))
    pct = float(req.get("pct", 0))
    sell_reason = req.get("sell_reason", "")
    ai_score = req.get("ai_advisor_score")
    now = req.get("now_kst") or _now_kst()

    pos = load_positions()
    positions = pos.get("positions", {})

    # ── 게이트 1: 보유 중 ──
    p = positions.get(code)
    if not p:
        rejects.append(_reject(
            "미보유", f"{name} ({code}) 미보유 — 매도 불가",
            code=code,
        ))
        return {"ok": False, "rejects": rejects, "warnings": warnings}

    held_qty = p.get("qty", 0)

    # ── 게이트 2: 수량 합리 (매도 ≤ 보유) ──
    if qty <= 0:
        rejects.append(_reject(
            "수량무효", f"매도 수량 {qty}주 — 1주 이상 필요",
            qty=qty,
        ))
    if qty > held_qty:
        rejects.append(_reject(
            "수량초과", f"매도 {qty}주 > 보유 {held_qty}주",
            qty=qty, held=held_qty,
        ))

    # ── 게이트 3: 장 시간 ──
    if not (9 <= now.hour < 15 or (now.hour == 15 and now.minute < 30)):
        rejects.append(_reject(
            "장시간", f"장 시간 외 {now.strftime('%H:%M')}",
            now=now.strftime("%H:%M"),
        ))

    # ── 게이트 4: 손절선 (-7%) ── 정보 검증 (도달 시 자동 매도)
    if pct <= -STOP_LOSS_PCT * 100:
        warnings.append(f"손절선 도달 ({pct:.2f}% ≤ {-STOP_LOSS_PCT*100:.0f}%) — 즉시 매도")

    # ── 게이트 5: 1차 익절 (+10%) ──
    if pct >= TARGET1_PCT * 100 and "절반" in sell_reason:
        warnings.append(f"1차 익절 적중 (+{pct:.2f}%) — 절반 매도 권장")

    # ── 게이트 6: 2차 익절 (+20%) ──
    if pct >= TARGET2_PCT * 100:
        warnings.append(f"2차 익절 적중 (+{pct:.2f}%) — 강력 권장")

    # ── 게이트 7: 장기 목표 (+40%) ──
    if pct >= LONG_TARGET_PCT * 100:
        warnings.append(f"장기 목표 도달 (+{pct:.2f}%) — 자동 처리")

    # ── 게이트 8: AI 매도 어드바이저 참고 ──
    if ai_score is not None:
        if ai_score >= 0.7:
            warnings.append(f"AI 매도 어드바이저 강력 권장 ({ai_score:.2f})")
        elif ai_score <= 0.3 and pct < 0:
            warnings.append(f"AI 매도 비추천 ({ai_score:.2f}) — 손실 상태지만 보유 유지 고려")

    if rejects:
        return {"ok": False, "rejects": rejects, "warnings": warnings}
    return _ok(warnings)


# ════════════════════════════════════════════════
# 학습부 로그 (REJECT 사유 누적 → 자가학습용)
# Phase 2 4단계(학습부)에서 더 깊이 통합 예정
# ════════════════════════════════════════════════
_VERIFY_LOG_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "verify_log.json"
)


def log_verify_result(req: dict, result: dict, side: str = "buy") -> None:
    """검증 결과 로그 — 거절률/통과율 통계 + 자가학습용."""
    try:
        entries = []
        if os.path.exists(_VERIFY_LOG_FILE):
            with open(_VERIFY_LOG_FILE, "r", encoding="utf-8") as f:
                entries = json.load(f)
                if not isinstance(entries, list):
                    entries = []

        entry = {
            "time": _now_kst().isoformat(),
            "side": side,
            "code": req.get("code", ""),
            "name": req.get("name", ""),
            "ok": result.get("ok", False),
            "rejects": [r.get("gate") for r in result.get("rejects", [])],
            "warnings": result.get("warnings", []),
        }
        entries.append(entry)

        # 최근 1000건만 유지
        if len(entries) > 1000:
            entries = entries[-1000:]

        with open(_VERIFY_LOG_FILE, "w", encoding="utf-8") as f:
            json.dump(entries, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"  [검증부/log] 저장 오류: {e}")


def get_verify_stats(window_days: int = 7) -> dict:
    """검증 통계 — 최근 N일 통과율 / 거부 사유 TOP."""
    try:
        if not os.path.exists(_VERIFY_LOG_FILE):
            return {}
        with open(_VERIFY_LOG_FILE, "r", encoding="utf-8") as f:
            entries = json.load(f)

        cutoff = (_now_kst() - __import__("datetime").timedelta(days=window_days)).isoformat()
        recent = [e for e in entries if e.get("time", "") >= cutoff]

        total = len(recent)
        passed = sum(1 for e in recent if e.get("ok"))
        rejected = total - passed

        # 거부 사유 TOP
        reject_counts = {}
        for e in recent:
            for gate in e.get("rejects", []):
                reject_counts[gate] = reject_counts.get(gate, 0) + 1
        top_rejects = sorted(reject_counts.items(), key=lambda x: -x[1])[:5]

        return {
            "window_days": window_days,
            "total": total,
            "passed": passed,
            "rejected": rejected,
            "pass_rate_pct": round(passed / total * 100, 1) if total else 0,
            "top_rejects": top_rejects,
        }
    except Exception:
        return {}
