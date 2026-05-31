"""학습부 (Phase 2 5단계 — 1차).

AI 매도 어드바이저 로그 기록/정확도 평가 + 트레이딩 일기 추출.
finance.py에만 단방향 의존 (순환 import 없음).

1차 범위 (깨끗한 함수 — _kis / Claude API / portfolio 의존 없음):
  - _load_advisor_log / _save_advisor_log
  - log_advisor_decision (매도 시 AI 의견 기록)
  - calc_advisor_accuracy (누적 정확도 계산)
  - _get_recent_journals (최근 매도 일기 추출)

2~4차 추가:
  - _load_portfolio_history / _calc_mdd_from_portfolio (2차)
  - analyze_trading_performance (3차, B3 성적표)
  - calc_weight_recommendations (4차, B4 자가학습 — swing_score_min 주입형)
  - track_advisor_outcomes (5차, fetch_price 콜백 주입 — KIS 의존 분리)

stock.py 잔류 (다음 차수 대상):
  - ai_sell_advisor / ai_trade_journal (Claude API)
"""
import os
import json
from datetime import datetime, timedelta

from finance import load_positions, _now_kst, _today_str

# AI 매도 어드바이저 v2 (B1 → 신뢰도 검증 → 자동 결정 반영)
AI_ADVISOR_LOG          = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ai_advisor_log.json")
AI_ADVISOR_MIN_SAMPLES  = 30      # 최소 누적 건수 (이 미만이면 default 동작)
AI_ADVISOR_MIN_ACCURACY = 0.6     # 자동 활성화 신뢰도 임계 (60%+)
AI_ADVISOR_OUTCOME_DAYS = 5       # AI 의견 후 N일 가격 추적 → 정확도 평가

# 자산 추이 파일 (MDD 계산용) — finance.py로 중앙화 예정
PORTFOLIO_HISTORY       = os.path.join(os.path.dirname(os.path.abspath(__file__)), "portfolio_history.json")

# B4 자가학습 임계 (4차)
B4_MIN_SAMPLES           = 30      # 자가학습 최소 표본
B4_SECTOR_MIN_TRADES     = 3       # 섹터 권장 최소 매매 건수
B4_HOUR_MIN_TRADES       = 3       # 시간대 권장 최소 매매 건수
B4_GAP_HIGH              = 20      # 점수대 승률 차이 20%p 이상 — 임계 조정 권장
B4_WEAK_WIN_RATE         = 30      # 30% 미만 승률 — 회피 권장
B4_STRONG_WIN_RATE       = 70      # 70%+ 승률 — 우대 권장


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


def _load_portfolio_history(days: int = 90) -> list:
    """최근 N일 자산 추이 로드. 차트 데이터용."""
    try:
        if not os.path.exists(PORTFOLIO_HISTORY):
            return []
        with open(PORTFOLIO_HISTORY, "r", encoding="utf-8") as f:
            data = json.load(f)
        history = data.get("history", [])
        return history[-days:] if len(history) > days else history
    except Exception:
        return []


def _calc_mdd_from_portfolio(window_days: int = 30) -> dict:
    """portfolio_history.json 기반 MDD (Maximum Drawdown) 계산.

    Returns: {"mdd_pct", "mdd_amount", "peak_date", "trough_date", "current_dd_pct"}
    """
    try:
        history = _load_portfolio_history(days=window_days)
        if not history or len(history) < 2:
            return {"mdd_pct": 0, "mdd_amount": 0, "peak_date": "", "trough_date": "", "current_dd_pct": 0}

        # total 키는 _load_portfolio_history 반환 형식에 따라 다름. 일반적으로 total/value/auto+value
        def get_total(h):
            return h.get("total") or h.get("value") or h.get("auto_value") or 0

        peak_amount = 0
        peak_date   = ""
        max_dd      = 0
        max_dd_amt  = 0
        trough_date = ""

        for h in history:
            amt = get_total(h)
            if amt <= 0:
                continue
            if amt > peak_amount:
                peak_amount = amt
                peak_date   = h.get("date", "")
            elif peak_amount > 0:
                dd_pct = (amt - peak_amount) / peak_amount * 100
                if dd_pct < max_dd:
                    max_dd      = dd_pct
                    max_dd_amt  = amt - peak_amount
                    trough_date = h.get("date", "")

        # 현재 시점 낙폭 (피크 대비)
        last_amt = get_total(history[-1]) if history else 0
        current_dd = ((last_amt - peak_amount) / peak_amount * 100) if peak_amount > 0 else 0

        return {
            "mdd_pct":        round(max_dd, 2),
            "mdd_amount":     round(max_dd_amt),
            "peak_date":      peak_date,
            "trough_date":    trough_date,
            "current_dd_pct": round(current_dd, 2),
        }
    except Exception as e:
        print(f"  [MDD] 계산 오류: {e}")
        return {"mdd_pct": 0, "mdd_amount": 0, "peak_date": "", "trough_date": "", "current_dd_pct": 0}


def analyze_trading_performance(window_days: int = 30) -> dict:
    """positions.json history 기반 매매 결과 자가 분석. (Phase 2 — 자가 학습 인프라)

    매수↔매도 매칭하여 종목별 수익률/보유일/매도 사유 계산.
    daily 08:00에 호출되어 ai_personal_coach 프롬프트에 통계 전달 → AI가 학습 결과 반영.

    B3 (5/6): MDD / 섹터별 / 보유일별 / TOP 종목 추가 — 자가학습 인프라 완성.
    데이터 30건+ 쌓이면 swing_score 가중치 자동 조정으로 확장 (B4).
    """
    try:
        pos = load_positions()
        history = pos.get("history", [])
        if not history:
            return {"trades": 0, "summary": "매매 데이터 없음 (자동매매 시작 후 누적)"}

        cutoff = (_now_kst() - timedelta(days=window_days)).strftime("%Y-%m-%d")
        recent = [h for h in history if h.get("date", "") >= cutoff]

        # 매수/매도 매칭 (FIFO)
        buys = {}
        results = []
        for h in recent:
            code = h.get("code", "")
            if h.get("side") == "buy":
                buys[code] = h
            elif h.get("side") == "sell" and code in buys:
                buy = buys[code]
                bp = buy.get("price", 0)
                sp = h.get("price", 0)
                if bp > 0 and sp > 0:
                    pnl_pct = (sp - bp) / bp * 100
                    # 보유일 계산
                    try:
                        bd = datetime.strptime(buy.get("date", ""), "%Y-%m-%d")
                        sd = datetime.strptime(h.get("date", ""), "%Y-%m-%d")
                        hold_days = (sd - bd).days
                    except Exception:
                        hold_days = 0
                    # buy의 reason "swing_score N" 파싱
                    score = 0
                    try:
                        reason = buy.get("reason", "")
                        if "swing_score" in reason:
                            score = int(reason.split("swing_score")[-1].strip().split()[0])
                    except Exception:
                        pass
                    results.append({
                        "name": buy.get("name", code),
                        "code": code,
                        "sector": buy.get("sector", "") or h.get("sector", ""),
                        "swing_score": score,
                        "pnl_pct": pnl_pct,
                        "hold_days": hold_days,
                        "sell_reason": h.get("reason", ""),
                        "buy_time":  buy.get("time", ""),
                        "sell_time": h.get("time", ""),
                    })
                buys.pop(code, None)

        if not results:
            return {"trades": 0, "summary": f"최근 {window_days}일 완료 매매 없음 (보유 중인 종목은 미포함)"}

        wins   = [r for r in results if r["pnl_pct"] > 0]
        losses = [r for r in results if r["pnl_pct"] <= 0]
        avg_win  = sum(r["pnl_pct"] for r in wins) / len(wins) if wins else 0
        avg_loss = sum(r["pnl_pct"] for r in losses) / len(losses) if losses else 0
        avg_hold = sum(r["hold_days"] for r in results) / len(results)

        # 점수 구간별 승률
        bucket_70 = [r for r in results if r["swing_score"] >= 70]
        bucket_65 = [r for r in results if 65 <= r["swing_score"] < 70]
        bucket_60 = [r for r in results if 60 <= r["swing_score"] < 65]
        def _wr(b):
            if not b: return 0
            return sum(1 for r in b if r["pnl_pct"] > 0) / len(b) * 100

        summary_lines = [
            f"최근 {window_days}일 완료 매매: {len(results)}건 (승 {len(wins)}/패 {len(losses)})",
            f"승률: {len(wins)/len(results)*100:.1f}% / 평균 수익 {avg_win:+.2f}% / 평균 손실 {avg_loss:+.2f}%",
            f"평균 보유: {avg_hold:.1f}일",
        ]
        if bucket_70:
            summary_lines.append(f"점수 70+: {len(bucket_70)}건, 승률 {_wr(bucket_70):.0f}%")
        if bucket_65:
            summary_lines.append(f"점수 65-69: {len(bucket_65)}건, 승률 {_wr(bucket_65):.0f}%")
        if bucket_60:
            summary_lines.append(f"점수 60-64: {len(bucket_60)}건, 승률 {_wr(bucket_60):.0f}%")

        # B3: 섹터별 승률
        sector_stats = {}
        for r in results:
            sec = r.get("sector") or "기타"
            sector_stats.setdefault(sec, []).append(r)
        sector_perf = []
        for sec, lst in sector_stats.items():
            if len(lst) < 2:  # 1건은 통계 의미 X
                continue
            sec_wins = sum(1 for r in lst if r["pnl_pct"] > 0)
            sec_avg  = sum(r["pnl_pct"] for r in lst) / len(lst)
            sector_perf.append({
                "sector":   sec,
                "trades":   len(lst),
                "win_rate": sec_wins / len(lst) * 100,
                "avg_pnl":  sec_avg,
            })
        sector_perf.sort(key=lambda x: -x["win_rate"])

        # B3: 보유일별 승률 (1일 / 2~3일 / 4~5일)
        hold_buckets = {"1일": [], "2-3일": [], "4-5일": [], "6일+": []}
        for r in results:
            d = r["hold_days"]
            if d <= 1:   hold_buckets["1일"].append(r)
            elif d <= 3: hold_buckets["2-3일"].append(r)
            elif d <= 5: hold_buckets["4-5일"].append(r)
            else:        hold_buckets["6일+"].append(r)
        hold_perf = []
        for label, lst in hold_buckets.items():
            if not lst:
                continue
            wr = sum(1 for r in lst if r["pnl_pct"] > 0) / len(lst) * 100
            hold_perf.append({"range": label, "trades": len(lst), "win_rate": wr})

        # B3: 최고 / 최악 종목 TOP 3
        sorted_by_pnl = sorted(results, key=lambda r: -r["pnl_pct"])
        top_winners = sorted_by_pnl[:3]
        top_losers  = sorted_by_pnl[-3:][::-1]  # 역순 (가장 큰 손실 먼저)

        # B3+ 자가학습: 매수/매도 시간대별 승률 (사용자 의도 — 주가 언제 오르고 내리는지 파악)
        def _hour_bucket(time_str: str) -> str:
            if not time_str or ":" not in time_str:
                return ""
            try:
                h = int(time_str.split(":")[0])
            except Exception:
                return ""
            if 9 <= h < 11:   return "09-11시 (개장)"
            if 11 <= h < 13:  return "11-13시 (점심)"
            if 13 <= h < 15:  return "13-15시 (오후)"
            if 15 <= h < 16:  return "15시+ (마감)"
            return ""

        # 매수 시간대별 승률
        buy_hour_stats: dict = {}
        for r in results:
            bucket = _hour_bucket(r.get("buy_time", ""))
            if not bucket:
                continue
            buy_hour_stats.setdefault(bucket, []).append(r)
        buy_hour_perf = []
        for bucket, lst in buy_hour_stats.items():
            hour_wins = sum(1 for r in lst if r["pnl_pct"] > 0)
            hour_avg  = sum(r["pnl_pct"] for r in lst) / len(lst)
            buy_hour_perf.append({
                "bucket":   bucket,
                "trades":   len(lst),
                "win_rate": round(hour_wins / len(lst) * 100, 1),
                "avg_pnl":  round(hour_avg, 2),
            })
        # 시간 순 정렬
        bucket_order = ["09-11시 (개장)", "11-13시 (점심)", "13-15시 (오후)", "15시+ (마감)"]
        buy_hour_perf.sort(key=lambda x: bucket_order.index(x["bucket"]) if x["bucket"] in bucket_order else 99)

        # 매도 시간대별 승률
        sell_hour_stats: dict = {}
        for r in results:
            bucket = _hour_bucket(r.get("sell_time", ""))
            if not bucket:
                continue
            sell_hour_stats.setdefault(bucket, []).append(r)
        sell_hour_perf = []
        for bucket, lst in sell_hour_stats.items():
            hour_wins = sum(1 for r in lst if r["pnl_pct"] > 0)
            hour_avg  = sum(r["pnl_pct"] for r in lst) / len(lst)
            sell_hour_perf.append({
                "bucket":   bucket,
                "trades":   len(lst),
                "win_rate": round(hour_wins / len(lst) * 100, 1),
                "avg_pnl":  round(hour_avg, 2),
            })
        sell_hour_perf.sort(key=lambda x: bucket_order.index(x["bucket"]) if x["bucket"] in bucket_order else 99)

        # B3: MDD (portfolio_history 기반)
        mdd = _calc_mdd_from_portfolio(window_days=window_days)

        return {
            "trades": len(results),
            "wins": len(wins),
            "losses": len(losses),
            "win_rate": len(wins) / len(results) * 100,
            "avg_win": avg_win,
            "avg_loss": avg_loss,
            "avg_hold_days": avg_hold,
            "summary": "\n".join(summary_lines),
            "details": results,
            # B3 추가
            "sector_perf": sector_perf,    # [{sector, trades, win_rate, avg_pnl}, ...]
            "hold_perf":   hold_perf,      # [{range, trades, win_rate}, ...]
            "top_winners": top_winners,    # 상위 3건
            "top_losers":  top_losers,     # 하위 3건
            "mdd":         mdd,            # MDD 정보
            "score_buckets": {             # 점수대별 (이미 있던 것 정리)
                "70+":   {"trades": len(bucket_70), "win_rate": _wr(bucket_70)},
                "65-69": {"trades": len(bucket_65), "win_rate": _wr(bucket_65)},
                "60-64": {"trades": len(bucket_60), "win_rate": _wr(bucket_60)},
            },
            # B3+ 자가학습 — 시간대별 매매 성과 (주가 언제 오르고 내리는지)
            "buy_hour_perf":  buy_hour_perf,
            "sell_hour_perf": sell_hour_perf,
        }
    except Exception as e:
        return {"trades": 0, "summary": f"분석 오류: {e}"}


def calc_weight_recommendations(swing_score_min: int) -> dict:
    """B4 자가학습 — 매매 데이터 누적 분석 → 가중치 자동 조정 권장 (#5).

    데이터 30건+ 누적 시 활성화. positions.json history 기반.
    분석 항목:
      1. 점수 임계 (70+ vs 60-64 승률 차이 → SWING_SCORE_MIN 조정)
      2. 섹터 우대/회피 (승률 70%+ → 우대 / 30% 미만 → 회피)
      3. 시간대 회피 (특정 시간대 승률 30% 미만 → 매수 회차 조정)

    swing_score_min: 현재 매수 임계 (stock.py SWING_SCORE_MIN 주입 — 순환 import 회피).

    Returns: {
        "ready": True if 30건+ else False,
        "trades": 누적 건수,
        "remaining": 활성화까지 남은 건수,
        "win_rate": 전체 승률,
        "recommendations": [
            {"type", "reason", "current", "recommended", "level"}, ...
        ],
    }
    """
    perf = analyze_trading_performance(window_days=90)
    total = perf.get("trades", 0)

    base = {
        "trades":      total,
        "remaining":   max(0, B4_MIN_SAMPLES - total),
        "win_rate":    perf.get("win_rate", 0),
        "ready":       total >= B4_MIN_SAMPLES,
        "recommendations": [],
    }

    if total < B4_MIN_SAMPLES:
        return base

    recs = []

    # 1) 점수 임계 조정 권장
    sb = perf.get("score_buckets", {})
    bucket_70 = sb.get("70+", {})
    bucket_60 = sb.get("60-64", {})
    if bucket_70.get("trades", 0) >= 5 and bucket_60.get("trades", 0) >= 5:
        wr_70 = bucket_70.get("win_rate", 0)
        wr_60 = bucket_60.get("win_rate", 0)
        gap = wr_70 - wr_60
        if gap >= B4_GAP_HIGH:
            recs.append({
                "type":        "score_threshold",
                "level":       "high",
                "title":       f"매수 점수 임계 {swing_score_min} → 70 권장",
                "reason":      f"70점+ 승률 {wr_70:.0f}% vs 60-64점 {wr_60:.0f}% (차이 +{gap:.0f}%p)",
                "current":     swing_score_min,
                "recommended": 70,
                "code_var":    "SWING_SCORE_MIN",
            })
        elif wr_60 - wr_70 >= B4_GAP_HIGH:
            # 역으로 60점대가 더 잘 나오는 이상 케이스
            recs.append({
                "type":        "score_threshold_lower",
                "level":       "medium",
                "title":       f"매수 점수 임계 완화 검토",
                "reason":      f"60-64점 승률 {wr_60:.0f}% > 70점+ {wr_70:.0f}% — 표본 더 필요",
                "current":     swing_score_min,
                "recommended": swing_score_min,
                "code_var":    "SWING_SCORE_MIN",
            })

    # 2) 섹터별 권장
    sector_perf = perf.get("sector_perf", [])
    weak_sectors = [s for s in sector_perf
                    if s.get("win_rate", 0) < B4_WEAK_WIN_RATE
                    and s.get("trades", 0) >= B4_SECTOR_MIN_TRADES]
    strong_sectors = [s for s in sector_perf
                      if s.get("win_rate", 0) >= B4_STRONG_WIN_RATE
                      and s.get("trades", 0) >= B4_SECTOR_MIN_TRADES]

    for s in weak_sectors[:3]:
        recs.append({
            "type":     "sector_avoid",
            "level":    "medium",
            "title":    f"🚫 {s['sector']} 섹터 회피 권장",
            "reason":   f"승률 {s['win_rate']:.0f}% ({s['trades']}건) — 평균 손익 {s.get('avg_pnl', 0):+.1f}%",
            "sector":   s["sector"],
        })
    for s in strong_sectors[:3]:
        recs.append({
            "type":   "sector_boost",
            "level":  "low",
            "title":  f"🎯 {s['sector']} 섹터 우대 권장",
            "reason": f"승률 {s['win_rate']:.0f}% ({s['trades']}건) — 평균 손익 {s.get('avg_pnl', 0):+.1f}%",
            "sector": s["sector"],
        })

    # 3) 시간대별 권장
    bhp = perf.get("buy_hour_perf", [])
    weak_hours = [h for h in bhp
                  if h.get("win_rate", 0) < B4_WEAK_WIN_RATE
                  and h.get("trades", 0) >= B4_HOUR_MIN_TRADES]
    for h in weak_hours[:2]:
        recs.append({
            "type":   "hour_avoid",
            "level":  "low",
            "title":  f"⏰ {h['bucket']} 매수 회차 검토",
            "reason": f"승률 {h['win_rate']:.0f}% ({h['trades']}건) — 평균 손익 {h.get('avg_pnl', 0):+.1f}%",
            "bucket": h["bucket"],
        })

    # 4) 보유일 권장 (특정 보유일이 다른 것보다 현저히 낮으면)
    hp = perf.get("hold_perf", [])
    if len(hp) >= 2:
        worst = min(hp, key=lambda x: x.get("win_rate", 0))
        best  = max(hp, key=lambda x: x.get("win_rate", 0))
        if worst != best and worst.get("trades", 0) >= 3:
            wr_gap = best.get("win_rate", 0) - worst.get("win_rate", 0)
            if wr_gap >= B4_GAP_HIGH:
                recs.append({
                    "type":   "hold_days",
                    "level":  "low",
                    "title":  f"⏱️ 보유 {best['range']} 우수, {worst['range']} 부진",
                    "reason": f"{best['range']} 승률 {best['win_rate']:.0f}% / {worst['range']} {worst['win_rate']:.0f}% (차이 {wr_gap:.0f}%p)",
                })

    base["recommendations"] = recs
    return base


def track_advisor_outcomes(fetch_price) -> int:
    """AI 어드바이저 로그의 5일 전 의견들 결과 추적 (정확도 평가).

    daily(08:00) 또는 close_summary에서 호출 → 5일 전 매도들의 현재가 비교.

    fetch_price: code(str) → 현재가(float, 조회 실패 시 0). stock.py가 _kis/_safe_float를
                 묶어 주입 (KIS API 의존을 학습부 밖에 둠).
    Returns: 갱신된 건수
    """
    log = _load_advisor_log()
    if not log:
        return 0

    today = _now_kst()
    cutoff = (today - timedelta(days=AI_ADVISOR_OUTCOME_DAYS)).strftime("%Y-%m-%d")
    updated = 0

    for entry in log:
        if entry.get("ai_correct") is not None:
            continue  # 이미 평가됨
        entry_date = entry.get("date", "")
        if entry_date > cutoff:
            continue  # 5일 미만 — 아직 평가 X

        try:
            code = entry.get("code", "")
            sell_price = entry.get("sell_price", 0)
            if not code or sell_price <= 0:
                continue

            # 현재가 조회 (5일 후 가격) — 주입된 콜백 사용
            cur = fetch_price(code) or 0
            if cur <= 0:
                continue

            outcome_pct = (cur - sell_price) / sell_price * 100
            entry["outcome_price"] = round(cur)
            entry["outcome_date"]  = today.strftime("%Y-%m-%d")
            entry["outcome_pct"]   = round(outcome_pct, 2)

            # AI 정확도 판정
            #   "hold" (보류) → 5일 후 +1% 이상이면 정확 (안 팔길 잘했다)
            #   "sell" (매도) → 5일 후 -1% 이상 하락이면 정확 (잘 팔았다)
            cls = entry.get("opinion_class", "neutral")
            if cls == "hold":
                entry["ai_correct"] = outcome_pct >= 1.0
            elif cls == "sell":
                entry["ai_correct"] = outcome_pct <= -1.0
            else:
                entry["ai_correct"] = abs(outcome_pct) < 1.0  # neutral은 정체일 때 맞음
            updated += 1
        except Exception as e:
            print(f"  [advisor] 결과 추적 오류 ({entry.get('name', '?')}): {e}")

    if updated > 0:
        _save_advisor_log(log)
        print(f"  [advisor] {updated}건 결과 평가 완료")

    return updated
