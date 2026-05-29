"""학습부 (Phase 2 5단계 — 1차).

AI 매도 어드바이저 로그 기록/정확도 평가 + 트레이딩 일기 추출.
finance.py에만 단방향 의존 (순환 import 없음).

1차 범위 (깨끗한 함수 — _kis / Claude API / portfolio 의존 없음):
  - _load_advisor_log / _save_advisor_log
  - log_advisor_decision (매도 시 AI 의견 기록)
  - calc_advisor_accuracy (누적 정확도 계산)
  - _get_recent_journals (최근 매도 일기 추출)

stock.py 잔류 (다음 차수 대상):
  - track_advisor_outcomes (_kis 의존)
  - ai_sell_advisor / ai_trade_journal (Claude API)
  - analyze_trading_performance / calc_weight_recommendations (portfolio 의존)
"""
import os
import json

from finance import load_positions, _now_kst, _today_str

# AI 매도 어드바이저 v2 (B1 → 신뢰도 검증 → 자동 결정 반영)
AI_ADVISOR_LOG          = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ai_advisor_log.json")
AI_ADVISOR_MIN_SAMPLES  = 30      # 최소 누적 건수 (이 미만이면 default 동작)
AI_ADVISOR_MIN_ACCURACY = 0.6     # 자동 활성화 신뢰도 임계 (60%+)


def _load_advisor_log() -> list:
    """ai_advisor_log.json 로드. AI 매도 의견 + 실제 결과 추적용."""
    try:
        if os.path.exists(AI_ADVISOR_LOG):
            with open(AI_ADVISOR_LOG, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, list) else []
    except Exception as e:
        print(f"  [advisor_log] 로드 오류: {e}")
    return []


def _save_advisor_log(log: list) -> None:
    try:
        # 최근 200건만 보관 (안전 캡)
        if len(log) > 200:
            log = log[-200:]
        with open(AI_ADVISOR_LOG, "w", encoding="utf-8") as f:
            json.dump(log, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"  [advisor_log] 저장 오류: {e}")


def log_advisor_decision(stock_info: dict, ai_opinion: str,
                          sell_executed: bool, sell_pct: float) -> None:
    """매도 시점 AI 의견 + 실제 매도 정보 기록 (B1 → v2 진화 데이터).

    이후 5일 후 가격 추적 → AI 의견 정확도 평가:
    - "보류 검토" 후 가격 ↑ → AI 정확
    - "매도 적절" 후 가격 ↓ → AI 정확
    """
    if not ai_opinion:
        return  # AI 호출 실패 시 기록 X

    # 의견 분류: "매도" 키워드 vs "보류" 키워드
    op_lower = ai_opinion.lower()
    if "보류" in ai_opinion:
        opinion_class = "hold"
    elif "매도" in ai_opinion:
        opinion_class = "sell"
    else:
        opinion_class = "neutral"

    log = _load_advisor_log()
    log.append({
        "date":         _today_str(),
        "time":         _now_kst().strftime("%H:%M"),
        "code":         stock_info.get("code", ""),
        "name":         stock_info.get("name", ""),
        "sell_price":   stock_info.get("curr_price", 0),
        "sell_pct":     round(sell_pct, 2),
        "ai_opinion":   ai_opinion[:200],
        "opinion_class": opinion_class,
        "sell_executed": sell_executed,
        # 5일 후 결과 (track_advisor_outcomes에서 채움)
        "outcome_price":    None,
        "outcome_date":     None,
        "outcome_pct":      None,
        "ai_correct":       None,  # True if AI 정확
    })
    _save_advisor_log(log)
    print(f"  [advisor_log] {opinion_class} 의견 기록 (누적 {len(log)}건)")


def calc_advisor_accuracy() -> dict:
    """AI 어드바이저 누적 정확도 계산.

    Returns: {
        "total":       전체 평가 완료 건수,
        "correct":     AI 정확 건수,
        "accuracy":    정확도 %,
        "hold_total":  보류 권장 건수,
        "hold_correct": 보류 권장 정확,
        "sell_total":  매도 권장 건수,
        "sell_correct": 매도 권장 정확,
        "ready_to_activate": 30건+ AND 정확도 60%+ 인지,
    }
    """
    log = _load_advisor_log()
    evaluated = [e for e in log if e.get("ai_correct") is not None]
    total = len(evaluated)
    correct = sum(1 for e in evaluated if e.get("ai_correct"))

    hold = [e for e in evaluated if e.get("opinion_class") == "hold"]
    sell = [e for e in evaluated if e.get("opinion_class") == "sell"]

    accuracy = (correct / total * 100) if total > 0 else 0
    ready = total >= AI_ADVISOR_MIN_SAMPLES and accuracy >= AI_ADVISOR_MIN_ACCURACY * 100

    return {
        "total":          total,
        "correct":        correct,
        "accuracy":       round(accuracy, 1),
        "hold_total":     len(hold),
        "hold_correct":   sum(1 for e in hold if e.get("ai_correct")),
        "sell_total":     len(sell),
        "sell_correct":   sum(1 for e in sell if e.get("ai_correct")),
        "ready_to_activate": ready,
        "pending":        len(log) - total,  # 5일 안 된 평가 대기
    }


def _get_recent_journals(limit: int = 5) -> list:
    """positions.json history에서 최근 매도 N건의 journal 추출 (AI 학습용)."""
    try:
        pos = load_positions()
        history = pos.get("history", [])
        sells = [h for h in history if h.get("side") == "sell" and h.get("journal")]
        sells.sort(key=lambda h: (h.get("date", ""), h.get("time", "")), reverse=True)
        return [
            {
                "name":    s.get("name", ""),
                "pct":     s.get("pct", 0),
                "journal": s.get("journal", ""),
            }
            for s in sells[:limit]
        ]
    except Exception:
        return []
