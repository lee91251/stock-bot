"""백테스팅 엔진 — 스윙 자동매매 검증

과거 N개월 데이터로 stock.py의 스윙 매매 전략을 가상 시뮬레이션.
실제 매매는 영향 없음. 봇 설정(점수 임계치/매도 조건/가중치)이 효과적인지 검증.

핵심 단순화:
  - DART 공시 / 뉴스 감성: 과거 데이터 가져오기 복잡 → 점수에서 제외
  - KIS 실시간 API 대신 pykrx 일봉 데이터 사용 (외국인/기관 포함)
  - 매수/매도는 다음 영업일 시초가(open)로 시뮬레이션 (현실적인 갭 반영)
  - 수수료 0.015% + 매도 거래세 0.18% + 슬리피지 0.1% 정확 반영

사용법:
  python backtest.py                 # 기본: 최근 6개월
  python backtest.py 12              # 최근 12개월
  python backtest.py 6 --no-report   # 리포트 생략 (빠르게 테스트)
"""

import os
import sys
import json
import time
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import numpy as np

try:
    from pykrx import stock as _krx
    _PYKRX_OK = True
except ImportError:
    _PYKRX_OK = False
    _krx = None

# stock.py에서 종목 목록과 상수 가져오기 (코드 중복 방지)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from stock import (
    KR_STOCKS,
    INVEST_PER_STOCK,
    SWING_SCORE_MIN,
    SWING_TARGET1_PCT,
    SWING_TARGET2_PCT,
    SWING_STOP_LOSS_PCT,
    SWING_MAX_HOLD_DAYS,
    SWING_MAX_DAILY_BUY,
    SWING_MAX_DAILY_AMT,
    SWING_LOSS_COOLDOWN_DAYS,
    _now_kst,
    _safe_float,
    tg_send,
)

# ════════════════════════════════════════════════
# 백테스팅 비용 모델 (실전 기준)
# ════════════════════════════════════════════════
COMMISSION_RATE  = 0.00015   # 거래 수수료 (KIS 기준 0.015%)
TRANSACTION_TAX  = 0.0018    # 매도 거래세 (코스피 0.18%)
SLIPPAGE_RATE    = 0.001     # 시장가 슬리피지 가정 0.1%

# 데이터 캐시 위치
BACKTEST_CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".backtest_cache")
BACKTEST_RESULTS   = os.path.join(os.path.dirname(os.path.abspath(__file__)), "backtest_results.json")

INITIAL_CAPITAL = 100_000_000  # 모의투자 1억
RISK_FREE_RATE  = 0.035        # Sharpe 계산용 무위험 수익률 (한국 국채 3.5%)


# ════════════════════════════════════════════════
# 데이터 로드 (pykrx)
# ════════════════════════════════════════════════
def _ensure_cache_dir():
    Path(BACKTEST_CACHE_DIR).mkdir(parents=True, exist_ok=True)


def _cache_path(ticker: str, kind: str) -> str:
    return os.path.join(BACKTEST_CACHE_DIR, f"{ticker}_{kind}.csv")


def load_ohlcv(ticker: str, start: str, end: str) -> pd.DataFrame:
    """일봉 OHLCV 데이터 로드 (pykrx 캐시).

    ticker: 종목코드 6자리 (예: '012450')
    start, end: 'YYYYMMDD'
    """
    _ensure_cache_dir()
    cache = _cache_path(ticker, f"ohlcv_{start}_{end}")
    if os.path.exists(cache):
        try:
            return pd.read_csv(cache, parse_dates=["날짜"], index_col="날짜")
        except Exception:
            pass
    try:
        df = _krx.get_market_ohlcv_by_date(start, end, ticker)
        if df is None or df.empty:
            return pd.DataFrame()
        df.index.name = "날짜"
        df.to_csv(cache, encoding="utf-8")
        return df
    except Exception as e:
        print(f"  [load_ohlcv] {ticker} 실패: {e}")
        return pd.DataFrame()


def load_investor(ticker: str, start: str, end: str) -> pd.DataFrame:
    """외국인/기관 일별 순매수 데이터 (백만원 단위)."""
    _ensure_cache_dir()
    cache = _cache_path(ticker, f"investor_{start}_{end}")
    if os.path.exists(cache):
        try:
            return pd.read_csv(cache, parse_dates=["날짜"], index_col="날짜")
        except Exception:
            pass
    try:
        df = _krx.get_market_trading_value_by_date(start, end, ticker)
        if df is None or df.empty:
            return pd.DataFrame()
        df.index.name = "날짜"
        df.to_csv(cache, encoding="utf-8")
        return df
    except Exception as e:
        print(f"  [load_investor] {ticker} 실패: {e}")
        return pd.DataFrame()


def load_fundamental(ticker: str, start: str, end: str) -> pd.DataFrame:
    """일별 PER/PBR/배당수익률."""
    _ensure_cache_dir()
    cache = _cache_path(ticker, f"fund_{start}_{end}")
    if os.path.exists(cache):
        try:
            return pd.read_csv(cache, parse_dates=["날짜"], index_col="날짜")
        except Exception:
            pass
    try:
        df = _krx.get_market_fundamental_by_date(start, end, ticker)
        if df is None or df.empty:
            return pd.DataFrame()
        df.index.name = "날짜"
        df.to_csv(cache, encoding="utf-8")
        return df
    except Exception as e:
        print(f"  [load_fundamental] {ticker} 실패: {e}")
        return pd.DataFrame()


# ════════════════════════════════════════════════
# 점수 계산 (백테스팅 전용 단순화 — DART/뉴스 제외)
# ════════════════════════════════════════════════
def calc_swing_score_at(
    closes: pd.Series,
    volumes: pd.Series,
    investor_row: dict,
    fundamental_row: dict,
    sector: str,
) -> tuple:
    """주어진 시점 데이터로 스윙 점수와 매수시그널 계산.

    Returns: (score, signal, details_dict)
    """
    if len(closes) < 30:
        return 0, False, {"error": "데이터 부족"}

    # 기술적 지표
    delta = closes.diff()
    gain  = delta.clip(lower=0).rolling(14).mean()
    loss  = (-delta.clip(upper=0)).rolling(14).mean()
    rsi_series = 100 - 100 / (1 + gain / loss.replace(0, 1e-9))
    rsi   = float(rsi_series.iloc[-1])

    ema12 = closes.ewm(span=12).mean()
    ema26 = closes.ewm(span=26).mean()
    macd  = ema12 - ema26
    sig   = macd.ewm(span=9).mean()
    macd_cross = float(macd.iloc[-1]) > float(sig.iloc[-1])

    sma20 = closes.rolling(20).mean()
    std20 = closes.rolling(20).std()
    bb_up = sma20 + 2 * std20
    bb_dn = sma20 - 2 * std20
    bb_pct = float(
        (float(closes.iloc[-1]) - float(bb_dn.iloc[-1]))
        / (float(bb_up.iloc[-1]) - float(bb_dn.iloc[-1]) + 1e-9) * 100
    )

    # 거래량
    avg_vol = float(volumes.rolling(20).mean().iloc[-1])
    last_vol = float(volumes.iloc[-1])
    vol_ratio = (last_vol / avg_vol * 100) if avg_vol else 100

    # 수익률
    n = len(closes)
    ret_1w = (float(closes.iloc[-1]) - float(closes.iloc[-5])) / float(closes.iloc[-5]) * 100 if n >= 5 else 0
    ret_1m = (float(closes.iloc[-1]) - float(closes.iloc[-20])) / float(closes.iloc[-20]) * 100 if n >= 20 else 0
    ret_3m = (float(closes.iloc[-1]) - float(closes.iloc[0])) / float(closes.iloc[0]) * 100 if n >= 60 else 0

    # 52주 위치
    high52 = float(closes.tail(min(252, n)).max())
    low52  = float(closes.tail(min(252, n)).min())
    pct_from_low = (float(closes.iloc[-1]) - low52) / low52 * 100 if low52 else 0

    # 지지/저항
    sr_window = closes.tail(min(60, n))
    support = float(sr_window.quantile(0.2))
    resistance = float(sr_window.quantile(0.8))
    near_support = abs(float(closes.iloc[-1]) - support) / float(closes.iloc[-1]) < 0.03
    near_resistance = abs(float(closes.iloc[-1]) - resistance) / float(closes.iloc[-1]) < 0.03

    # 모멘텀 / 조작 시그널
    momentum_bad = ret_3m < -20 and rsi < 40 and not macd_cross
    manipulation = vol_ratio > 300 and ret_1w < -10

    # 외국인/기관 (단위: 원 → 억원으로 변환)
    foreign_eok = float(investor_row.get("외국인합계", 0)) / 1e8
    inst_eok    = float(investor_row.get("기관합계",   0)) / 1e8

    # PER (가치 비중 작음)
    per = float(fundamental_row.get("PER", 0))

    # ── 스윙 점수 계산 (stock.py와 동일 로직, 공시/뉴스만 제외) ──
    sw = 0
    if rsi < 30: sw += 15
    elif rsi < 45: sw += 12
    elif rsi > 65: sw -= 10
    if macd_cross: sw += 10
    if bb_pct < 20: sw += 8
    elif bb_pct > 80: sw -= 5
    if pct_from_low <= 10: sw += 8
    elif pct_from_low <= 20: sw += 4

    if vol_ratio >= 200: sw += 12
    elif vol_ratio >= 150: sw += 8
    elif vol_ratio < 80: sw -= 5
    if 0 < ret_1w <= 5: sw += 8
    elif 5 < ret_1w <= 10: sw += 4
    elif ret_1w > 10: sw -= 3
    elif ret_1w < -3: sw -= 5
    if -5 <= ret_1m <= 0: sw += 5
    elif ret_1m < -15: sw -= 10
    if near_support: sw += 8

    if foreign_eok >= 50: sw += 12
    elif foreign_eok >= 10: sw += 6
    elif foreign_eok <= -50: sw -= 8
    if inst_eok >= 50: sw += 8
    elif inst_eok >= 10: sw += 4

    if per and per > 30: sw -= 5

    if sector in ("조선", "방산", "원전", "전력", "바이오"):
        sw += 5

    if manipulation: sw -= 25
    if momentum_bad: sw -= 15

    # 매수 시그널 (stock.py와 동일 로직)
    signal = (
        sw >= SWING_SCORE_MIN
        and rsi < 65
        and not manipulation
        and not momentum_bad
        and not near_resistance
        and vol_ratio >= 100
        and ret_1m > -15
    )

    return sw, signal, {
        "rsi": round(rsi, 1),
        "macd_cross": macd_cross,
        "bb_pct": round(bb_pct, 1),
        "vol_ratio": round(vol_ratio, 0),
        "ret_1w": round(ret_1w, 1),
        "ret_1m": round(ret_1m, 1),
        "pct_from_low": round(pct_from_low, 1),
        "foreign_eok": round(foreign_eok, 1),
        "inst_eok": round(inst_eok, 1),
        "per": per,
    }


# ════════════════════════════════════════════════
# 시뮬레이션 엔진
# ════════════════════════════════════════════════
def simulate(months: int = 6) -> dict:
    """메인 시뮬레이션 — months 개월 과거 데이터로 가상 매매."""
    if not _PYKRX_OK:
        return {"error": "pykrx 미설치"}

    # 분석 기간 설정 (백테스트 + 분석에 필요한 30거래일 여유)
    end_dt   = _now_kst().replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=1)
    start_dt = end_dt - timedelta(days=months * 31 + 60)  # 분석용 lookback 60일 추가
    sim_start = end_dt - timedelta(days=months * 31)       # 실제 시뮬레이션 시작

    print(f"\n{'='*60}")
    print(f"백테스팅 시작 — 최근 {months}개월")
    print(f"데이터 로드 기간: {start_dt.date()} ~ {end_dt.date()}")
    print(f"실제 시뮬레이션:  {sim_start.date()} ~ {end_dt.date()}")
    print(f"{'='*60}\n")

    s_str = start_dt.strftime("%Y%m%d")
    e_str = end_dt.strftime("%Y%m%d")

    # 종목별 데이터 로드 (1회만)
    print(f"[1/3] {len(KR_STOCKS)}종목 데이터 로드 중 (pykrx)...")
    data = {}
    for i, (ticker, val) in enumerate(KR_STOCKS.items(), 1):
        name, period, sector = val
        code = ticker.split(".")[0]
        print(f"  ({i}/{len(KR_STOCKS)}) {name}...", end=" ", flush=True)
        ohlcv = load_ohlcv(code, s_str, e_str)
        invest = load_investor(code, s_str, e_str)
        fund = load_fundamental(code, s_str, e_str)
        if ohlcv.empty:
            print("❌ 데이터 없음 (스킵)")
            continue
        data[code] = {
            "name":   name,
            "sector": sector,
            "ohlcv":  ohlcv,
            "invest": invest,
            "fund":   fund,
        }
        print(f"OK ({len(ohlcv)}일)")
        time.sleep(0.1)  # pykrx rate limit 보호

    print(f"\n  → {len(data)}/{len(KR_STOCKS)}종목 로드 완료\n")

    # 거래일 목록 (시뮬레이션 시작일 이후)
    all_dates = sorted(set().union(*[set(d["ohlcv"].index) for d in data.values()]))
    sim_dates = [d for d in all_dates if d >= pd.Timestamp(sim_start)]
    print(f"[2/3] 시뮬레이션 거래일: {len(sim_dates)}일\n")

    # 시뮬레이션 상태
    cash = INITIAL_CAPITAL
    positions = {}     # {code: {name, qty, buy_price, buy_date, partial_sold, score}}
    closed_trades = [] # 매도 완료된 거래 기록
    loss_cooldown = {} # {code: cooldown_until_date}
    daily_capital = [] # 일별 자산 추이 (MDD 계산용)

    print(f"[3/3] 시뮬레이션 실행...")
    for di, today in enumerate(sim_dates):
        if di % 20 == 0 and di > 0:
            print(f"  진행: {di}/{len(sim_dates)} ({today.date()}) — "
                  f"현금 {cash/1e6:.1f}M / 보유 {len(positions)}종목 / 매도완료 {len(closed_trades)}건")

        today_str = today.strftime("%Y-%m-%d")

        # ── 1) 매도 점검 (보유 종목) ──
        for code in list(positions.keys()):
            p = positions[code]
            row = data[code]["ohlcv"].loc[data[code]["ohlcv"].index == today]
            if row.empty:
                continue
            close_price = float(row["종가"].iloc[0])

            buy_price = p["buy_price"]
            pct = (close_price - buy_price) / buy_price * 100
            held_qty = p["qty"]
            partial = p.get("partial_sold", False)

            # 보유 거래일 수
            buy_date = pd.Timestamp(p["buy_date"])
            held_dates = [d for d in sim_dates if buy_date < d <= today]
            held_days = len(held_dates)

            sell_qty = 0
            reason = ""
            is_loss = False
            if pct <= -SWING_STOP_LOSS_PCT * 100:
                sell_qty = held_qty
                reason = f"손절 ({pct:.1f}%)"
                is_loss = True
            elif pct >= SWING_TARGET2_PCT * 100:
                sell_qty = held_qty
                reason = f"+{pct:.1f}% 전량 익절"
            elif pct >= SWING_TARGET1_PCT * 100 and not partial:
                sell_qty = max(1, held_qty // 2)
                reason = f"+{pct:.1f}% 절반 익절"
            elif held_days >= SWING_MAX_HOLD_DAYS:
                sell_qty = held_qty
                reason = f"{held_days}거래일 강제 매도 ({pct:+.1f}%)"

            if sell_qty <= 0:
                continue

            # 다음 영업일 시초가로 매도 (현실적 갭 반영)
            next_idx = di + 1
            if next_idx >= len(sim_dates):
                continue  # 마지막 날은 매도 X (다음 날 없음)
            next_day = sim_dates[next_idx]
            next_row = data[code]["ohlcv"].loc[data[code]["ohlcv"].index == next_day]
            if next_row.empty:
                continue
            sell_price_raw = float(next_row["시가"].iloc[0])
            # 슬리피지 + 수수료 + 거래세 반영
            sell_price = sell_price_raw * (1 - SLIPPAGE_RATE)
            sell_amount = sell_price * sell_qty
            commission = sell_amount * COMMISSION_RATE
            tax = sell_amount * TRANSACTION_TAX
            net_sell = sell_amount - commission - tax

            cash += net_sell

            # 거래 기록
            buy_amount = buy_price * sell_qty
            net_pnl = net_sell - buy_amount
            net_pct = net_pnl / buy_amount * 100

            closed_trades.append({
                "code":       code,
                "name":       p["name"],
                "buy_date":   p["buy_date"],
                "sell_date":  next_day.strftime("%Y-%m-%d"),
                "buy_price":  buy_price,
                "sell_price": sell_price_raw,
                "qty":        sell_qty,
                "raw_pct":    round(pct, 2),
                "net_pct":    round(net_pct, 2),
                "net_pnl":    round(net_pnl, 0),
                "held_days":  held_days,
                "reason":     reason,
                "score":      p.get("score", 0),
                "sector":     p.get("sector", ""),
            })

            # 포지션 갱신
            if sell_qty == held_qty:
                del positions[code]
                if is_loss:
                    cd_until = (pd.Timestamp(next_day) +
                                pd.Timedelta(days=SWING_LOSS_COOLDOWN_DAYS)).strftime("%Y-%m-%d")
                    loss_cooldown[code] = cd_until
            else:
                positions[code]["qty"] = held_qty - sell_qty
                positions[code]["partial_sold"] = True

        # ── 2) 매수 점검 ──
        candidates = []
        for code, d in data.items():
            ohlcv = d["ohlcv"]
            today_idx = ohlcv.index <= today
            ohlcv_slice = ohlcv[today_idx]
            if len(ohlcv_slice) < 30:
                continue

            closes = ohlcv_slice["종가"]
            volumes = ohlcv_slice["거래량"]

            # 외국인/기관: 당일 데이터
            inv_row = {}
            if not d["invest"].empty:
                iv = d["invest"][d["invest"].index == today]
                if not iv.empty:
                    inv_row = iv.iloc[0].to_dict()

            # PER: 당일 데이터
            fund_row = {}
            if not d["fund"].empty:
                fv = d["fund"][d["fund"].index == today]
                if not fv.empty:
                    fund_row = fv.iloc[0].to_dict()

            sw, signal, _ = calc_swing_score_at(
                closes, volumes, inv_row, fund_row, d["sector"]
            )
            if signal:
                candidates.append((code, d["name"], d["sector"], sw))

        # 상위 SWING_MAX_DAILY_BUY 종목, 보유/쿨다운 제외
        candidates.sort(key=lambda x: x[3], reverse=True)
        daily_buy_count = 0
        daily_buy_amount = 0
        for code, name, sector, sw in candidates:
            if code in positions:
                continue
            if code in loss_cooldown and today_str < loss_cooldown[code]:
                continue
            if daily_buy_count >= SWING_MAX_DAILY_BUY:
                break
            if daily_buy_amount + INVEST_PER_STOCK > SWING_MAX_DAILY_AMT:
                break
            if cash < INVEST_PER_STOCK:
                break

            # 다음 영업일 시초가로 매수
            next_idx = di + 1
            if next_idx >= len(sim_dates):
                break
            next_day = sim_dates[next_idx]
            next_row = data[code]["ohlcv"].loc[data[code]["ohlcv"].index == next_day]
            if next_row.empty:
                continue
            buy_price_raw = float(next_row["시가"].iloc[0])
            buy_price = buy_price_raw * (1 + SLIPPAGE_RATE)
            qty = int(INVEST_PER_STOCK / buy_price)
            if qty < 1:
                continue
            buy_amount = buy_price * qty
            commission = buy_amount * COMMISSION_RATE
            total_cost = buy_amount + commission

            if total_cost > cash:
                continue

            cash -= total_cost
            positions[code] = {
                "name":        name,
                "qty":         qty,
                "buy_price":   buy_price,
                "buy_date":    next_day.strftime("%Y-%m-%d"),
                "partial_sold": False,
                "score":       sw,
                "sector":      sector,
            }
            daily_buy_count  += 1
            daily_buy_amount += buy_amount

        # 일별 자산 평가 (MDD 계산용)
        positions_value = 0
        for code, p in positions.items():
            row = data[code]["ohlcv"].loc[data[code]["ohlcv"].index == today]
            if not row.empty:
                positions_value += float(row["종가"].iloc[0]) * p["qty"]
        daily_capital.append({
            "date": today_str,
            "cash": round(cash, 0),
            "equity": round(positions_value, 0),
            "total": round(cash + positions_value, 0),
        })

    print(f"\n  진행: {len(sim_dates)}/{len(sim_dates)} (완료)")
    print(f"  최종 — 현금 {cash/1e6:.1f}M / 보유 {len(positions)}종목 / 매도완료 {len(closed_trades)}건\n")

    # 메트릭 계산
    metrics = compute_metrics(daily_capital, closed_trades, INITIAL_CAPITAL)
    metrics["months"]          = months
    metrics["start_date"]      = sim_start.strftime("%Y-%m-%d")
    metrics["end_date"]        = end_dt.strftime("%Y-%m-%d")
    metrics["initial_capital"] = INITIAL_CAPITAL
    metrics["final_cash"]      = round(cash, 0)
    metrics["open_positions"]  = len(positions)
    metrics["trades"]          = closed_trades
    metrics["daily_capital"]   = daily_capital

    return metrics


# ════════════════════════════════════════════════
# 메트릭 계산 (승률 / MDD / Sharpe)
# ════════════════════════════════════════════════
def compute_metrics(daily_capital: list, trades: list, initial: int) -> dict:
    if not daily_capital:
        return {"error": "데이터 없음"}

    totals = [d["total"] for d in daily_capital]
    final  = totals[-1]
    cum_return = (final - initial) / initial * 100

    # MDD (최대 낙폭)
    peak = totals[0]
    mdd = 0
    for t in totals:
        peak = max(peak, t)
        dd = (t - peak) / peak * 100
        mdd = min(mdd, dd)

    # 일별 수익률 (Sharpe 계산용)
    daily_returns = []
    for i in range(1, len(totals)):
        if totals[i-1] > 0:
            daily_returns.append((totals[i] - totals[i-1]) / totals[i-1])
    if daily_returns:
        avg_daily = np.mean(daily_returns)
        std_daily = np.std(daily_returns)
        # 연간화: 252 거래일
        ann_return = avg_daily * 252
        ann_vol    = std_daily * np.sqrt(252)
        sharpe = (ann_return - RISK_FREE_RATE) / ann_vol if ann_vol > 0 else 0
    else:
        sharpe = 0
        ann_return = 0
        ann_vol    = 0

    # 거래 통계
    total_trades = len(trades)
    if total_trades:
        wins   = [t for t in trades if t["net_pct"] > 0]
        losses = [t for t in trades if t["net_pct"] <= 0]
        win_rate    = len(wins) / total_trades * 100
        avg_win     = np.mean([t["net_pct"] for t in wins])   if wins   else 0
        avg_loss    = np.mean([t["net_pct"] for t in losses]) if losses else 0
        avg_held    = np.mean([t["held_days"] for t in trades])
        best_trade  = max(trades, key=lambda t: t["net_pct"])
        worst_trade = min(trades, key=lambda t: t["net_pct"])
    else:
        win_rate = avg_win = avg_loss = avg_held = 0
        best_trade = worst_trade = None

    # 점수 구간별 승률
    score_buckets = {
        "70-74": [t for t in trades if 70 <= t["score"] < 75],
        "75-79": [t for t in trades if 75 <= t["score"] < 80],
        "80-89": [t for t in trades if 80 <= t["score"] < 90],
        "90+":   [t for t in trades if t["score"] >= 90],
    }
    score_stats = {}
    for k, ts in score_buckets.items():
        if ts:
            score_stats[k] = {
                "count":     len(ts),
                "win_rate":  round(sum(1 for t in ts if t["net_pct"] > 0) / len(ts) * 100, 1),
                "avg_pct":   round(np.mean([t["net_pct"] for t in ts]), 2),
            }
        else:
            score_stats[k] = {"count": 0, "win_rate": 0, "avg_pct": 0}

    # 섹터별
    sectors = {}
    for t in trades:
        s = t.get("sector", "기타")
        sectors.setdefault(s, []).append(t)
    sector_stats = {}
    for s, ts in sectors.items():
        sector_stats[s] = {
            "count":    len(ts),
            "win_rate": round(sum(1 for t in ts if t["net_pct"] > 0) / len(ts) * 100, 1),
            "avg_pct":  round(np.mean([t["net_pct"] for t in ts]), 2),
        }

    return {
        "cumulative_return_pct": round(cum_return, 2),
        "annualized_return_pct": round(ann_return * 100, 2),
        "annualized_vol_pct":    round(ann_vol * 100, 2),
        "max_drawdown_pct":      round(mdd, 2),
        "sharpe_ratio":          round(sharpe, 2),
        "total_trades":          total_trades,
        "win_rate_pct":          round(win_rate, 1),
        "avg_win_pct":           round(avg_win, 2),
        "avg_loss_pct":          round(avg_loss, 2),
        "avg_holding_days":      round(avg_held, 1),
        "best_trade":            best_trade,
        "worst_trade":           worst_trade,
        "score_buckets":         score_stats,
        "sector_stats":          sector_stats,
    }


# ════════════════════════════════════════════════
# 결과 저장 + 텔레그램 요약
# ════════════════════════════════════════════════
def save_results(metrics: dict):
    try:
        with open(BACKTEST_RESULTS, "w", encoding="utf-8") as f:
            json.dump(metrics, f, ensure_ascii=False, indent=2, default=str)
        print(f"\n✅ 결과 저장: {BACKTEST_RESULTS}")
    except Exception as e:
        print(f"\n❌ 저장 실패: {e}")


def telegram_summary(metrics: dict):
    """백테스팅 결과 요약을 텔레그램으로 전송."""
    if "error" in metrics:
        tg_send(f"🚨 백테스팅 실패: {metrics['error']}")
        return

    lines = [
        f"📊 <b>백테스팅 결과 ({metrics['months']}개월)</b>",
        f"기간: {metrics['start_date']} ~ {metrics['end_date']}",
        "",
        f"<b>💰 수익률</b>",
        f"누적: <b>{metrics['cumulative_return_pct']:+.2f}%</b>",
        f"연환산: {metrics['annualized_return_pct']:+.2f}% / 변동성 {metrics['annualized_vol_pct']:.1f}%",
        f"최대낙폭(MDD): {metrics['max_drawdown_pct']:.2f}%",
        f"샤프비율: {metrics['sharpe_ratio']:.2f}",
        "",
        f"<b>📈 매매 통계</b>",
        f"총 매도: {metrics['total_trades']}건",
        f"승률: <b>{metrics['win_rate_pct']:.1f}%</b>",
        f"평균 수익(이긴 거래): +{metrics['avg_win_pct']:.2f}%",
        f"평균 손실(진 거래): {metrics['avg_loss_pct']:.2f}%",
        f"평균 보유: {metrics['avg_holding_days']:.1f}일",
    ]

    bt = metrics.get("best_trade")
    wt = metrics.get("worst_trade")
    if bt:
        lines.append(f"🥇 최고: {bt['name']} {bt['net_pct']:+.1f}% ({bt['held_days']}일)")
    if wt:
        lines.append(f"🥉 최악: {wt['name']} {wt['net_pct']:+.1f}% ({wt['held_days']}일)")

    lines.extend(["", "<b>🎯 점수 구간별 승률</b>"])
    for k, s in metrics.get("score_buckets", {}).items():
        if s["count"] > 0:
            lines.append(f"{k}점: {s['count']}건 / 승률 {s['win_rate']}% / 평균 {s['avg_pct']:+.2f}%")

    sec_stats = metrics.get("sector_stats", {})
    if sec_stats:
        top_sectors = sorted(sec_stats.items(),
                             key=lambda x: (-x[1]["count"], -x[1]["win_rate"]))[:5]
        lines.append("")
        lines.append("<b>🏭 섹터별 (상위 5)</b>")
        for s, st in top_sectors:
            lines.append(f"{s}: {st['count']}건 / 승률 {st['win_rate']}% / 평균 {st['avg_pct']:+.2f}%")

    lines.extend([
        "",
        "<i>※ 수수료(0.015%) + 매도세(0.18%) + 슬리피지(0.1%) 반영 후 순수익 기준",
        "※ DART 공시 / 뉴스 감성은 백테스팅에서 제외됨 (실전과 차이 가능)</i>",
    ])

    tg_send("\n".join(lines))


# ════════════════════════════════════════════════
# 메인
# ════════════════════════════════════════════════
if __name__ == "__main__":
    months = 6
    no_report = False
    for arg in sys.argv[1:]:
        if arg == "--no-report":
            no_report = True
        else:
            try:
                months = int(arg)
            except ValueError:
                pass

    print(f"백테스팅 v1.0 — 스윙 자동매매 검증")
    print(f"분석 기간: 최근 {months}개월\n")

    if not _PYKRX_OK:
        print("❌ pykrx 미설치 — pip install pykrx 필요")
        sys.exit(1)

    metrics = simulate(months=months)
    save_results(metrics)

    if not no_report:
        try:
            telegram_summary(metrics)
        except Exception as e:
            print(f"⚠️ 텔레그램 전송 실패: {e}")

    print("\n✅ 백테스팅 완료")
