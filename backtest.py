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
    # 5/14: 4트랙 점수 함수 (백테스트에서도 동일 공식 사용 — drift 방지)
    _calc_swing_score,
    _calc_short_term_score,
    _calc_mid_term_score,
    _calc_long_term_score,
)


# ════════════════════════════════════════════════
# 5/14 4트랙 백테스트 설정 — 트랙별 매수/매도 룰
# ════════════════════════════════════════════════
TRACK_CONFIG = {
    "swing": {
        "label": "🚀 스윙 (1~5일)",
        "score_func": _calc_swing_score,
        "target1_pct": 0.06,   # +6% 절반 익절
        "target2_pct": 0.10,   # +10% 전량 익절
        "stop_pct":    0.04,   # -4% 손절
        "max_hold":    5,      # 5거래일 (기본)
        "cooldown":    3,      # 손절 후 3일 쿨다운
        # 5/29 유연 보유 룰
        "max_hold_extended": 10,   # 💚 상승 추세 연장 한도
        "quick_exit_days":   3,    # 🔴 하락 빨리 청산
        "quick_exit_pct":   -1.0,
        "extend_min_pct":    3.0,  # 💚 연장 조건: +3% 이상
    },
    "short_term": {
        "label": "📈 단기 (1~3주)",
        "score_func": _calc_short_term_score,
        "target1_pct": 0.08,
        "target2_pct": 0.15,
        "stop_pct":    0.05,
        "max_hold":    15,
        "cooldown":    5,
        "max_hold_extended": 25,
        "quick_exit_days":   5,
        "quick_exit_pct":   -2.0,
        "extend_min_pct":    4.0,
    },
    "mid_term": {
        "label": "📊 중기 (1~3개월)",
        "score_func": _calc_mid_term_score,
        "target1_pct": 0.15,
        "target2_pct": 0.30,
        "stop_pct":    0.08,
        "max_hold":    60,
        "cooldown":    7,
        "max_hold_extended": 90,
        "quick_exit_days":  10,
        "quick_exit_pct":   -3.0,
        "extend_min_pct":    7.0,
    },
    "long_term": {
        "label": "💎 장기 (3개월+)",
        "score_func": _calc_long_term_score,
        "target1_pct": 0.40,
        "target2_pct": 1.00,
        "stop_pct":    None,   # 장기는 손절 X (헌법)
        "max_hold":    180,
        "cooldown":    0,
        "max_hold_extended": 365,  # 장기는 1년까지 연장
        "quick_exit_days":  30,
        "quick_exit_pct":  -10.0,
        "extend_min_pct":   15.0,
    },
}

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

# 백테스트 전용 점수 임계치 — stock.py의 SWING_SCORE_MIN(70)과 분리.
# 백테스트는 DART 공시(최대 ±20점) + 뉴스 감성(최대 ±10점)이 빠지므로
# 같은 종목이라도 백테스트 점수가 실전 대비 10~15점 낮게 나옴.
# 첫 실행 데이터 (평균 13.6 / 최고 64) 기준으로 55 설정.
BACKTEST_SCORE_MIN = int(os.environ.get("BACKTEST_SCORE_MIN", "55"))

# 파라미터 최적화 환경변수 (5/12 추가)
# 각 파라미터를 env로 변경 가능 — Grid Search 용
BT_RSI_BUY_MAX     = float(os.environ.get("BT_RSI_BUY_MAX", "65"))     # RSI 이 값 이상 매수 차단
BT_VOL_RATIO_MIN   = float(os.environ.get("BT_VOL_RATIO_MIN", "100"))  # 거래량 평균 대비 % 최소
BT_RET_1M_MIN      = float(os.environ.get("BT_RET_1M_MIN", "-15"))     # 1개월 수익률 이 값 이하 차단
BT_OPTIMIZE_MODE   = os.environ.get("BT_OPTIMIZE_MODE", "false").lower() == "true"  # 순차 최적화
BT_GRID_MODE       = os.environ.get("BT_GRID_MODE", "true").lower() == "true"      # Multi-env Grid (5/12 기본 ON)


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
# 5/14 — 4트랙 백테스트 features 빌더
# stock.py _calc_*_score 함수들이 기대하는 dict 형태로 OHLCV 데이터 변환.
# 동일 공식 재사용 → drift 방지 (한 곳만 고치면 봇/백테스트 모두 반영).
# ════════════════════════════════════════════════
def _compute_features(
    closes: pd.Series,
    volumes: pd.Series,
    investor_row: dict,
    fundamental_row: dict,
    sector: str = "기타",
) -> dict:
    """OHLCV + 펀더에서 4트랙 점수 함수가 기대하는 stock dict 생성."""
    if len(closes) < 30:
        return {}

    # 기술 지표
    delta = closes.diff()
    gain  = delta.clip(lower=0).rolling(14).mean()
    loss  = (-delta.clip(upper=0)).rolling(14).mean()
    rsi   = float((100 - 100 / (1 + gain / loss.replace(0, 1e-9))).iloc[-1])

    ema12 = closes.ewm(span=12).mean()
    ema26 = closes.ewm(span=26).mean()
    macd  = ema12 - ema26
    sig   = macd.ewm(span=9).mean()
    macd_cross = float(macd.iloc[-1]) > float(sig.iloc[-1])
    macd_hist  = float(macd.iloc[-1]) - float(sig.iloc[-1])

    sma20 = closes.rolling(20).mean()
    std20 = closes.rolling(20).std()
    bb_up = sma20 + 2 * std20
    bb_dn = sma20 - 2 * std20
    bb_pct = float(
        (float(closes.iloc[-1]) - float(bb_dn.iloc[-1]))
        / (float(bb_up.iloc[-1]) - float(bb_dn.iloc[-1]) + 1e-9) * 100
    )

    avg_vol = float(volumes.rolling(20).mean().iloc[-1])
    last_vol = float(volumes.iloc[-1])
    vol_ratio = (last_vol / avg_vol * 100) if avg_vol else 100

    n = len(closes)
    ret_1w = (float(closes.iloc[-1]) - float(closes.iloc[-5])) / float(closes.iloc[-5]) * 100 if n >= 5 else 0
    ret_1m = (float(closes.iloc[-1]) - float(closes.iloc[-20])) / float(closes.iloc[-20]) * 100 if n >= 20 else 0
    ret_3m = (float(closes.iloc[-1]) - float(closes.iloc[0])) / float(closes.iloc[0]) * 100 if n >= 60 else 0

    high52 = float(closes.tail(min(252, n)).max())
    low52  = float(closes.tail(min(252, n)).min())
    pct_from_low  = (float(closes.iloc[-1]) - low52)  / low52  * 100 if low52  else 0
    pct_from_high = (float(closes.iloc[-1]) - high52) / high52 * 100 if high52 else 0

    sr_window = closes.tail(min(60, n))
    support = float(sr_window.quantile(0.2))
    resistance = float(sr_window.quantile(0.8))
    near_support = abs(float(closes.iloc[-1]) - support) / float(closes.iloc[-1]) < 0.03
    near_resistance = abs(float(closes.iloc[-1]) - resistance) / float(closes.iloc[-1]) < 0.03

    momentum_bad = ret_3m < -20 and rsi < 40 and not macd_cross
    manipulation = vol_ratio > 300 and ret_1w < -10

    per = _safe_float(fundamental_row.get("PER", 0)) if fundamental_row else 0
    pbr = _safe_float(fundamental_row.get("PBR", 0)) if fundamental_row else 0
    div = _safe_float(fundamental_row.get("DIV", 0)) if fundamental_row else 0
    eps = _safe_float(fundamental_row.get("EPS", 0)) if fundamental_row else 0
    bps = _safe_float(fundamental_row.get("BPS", 0)) if fundamental_row else 0
    roe = round(eps / bps * 100, 1) if bps > 0 else 0.0

    # mktcap: pykrx daily fundamental에 없음 → 시가총액 데이터 부재 시 매우 큰 값으로 가정
    # (KR_STOCKS는 사전 큐레이션된 가치주 26개 = 대형주 위주이므로 mktcap 필터 통과 가정)
    mktcap = 10_0000_0000_0000  # 10조 (큐레이션 풀이라 안전 가정)

    return {
        "price": float(closes.iloc[-1]),
        "rsi": round(rsi, 1),
        "macd_cross": macd_cross,
        "macd_hist": round(macd_hist, 4),
        "bb_pct": round(bb_pct, 1),
        "vol_ratio": round(vol_ratio, 0),
        "ret_1w": round(ret_1w, 1),
        "ret_1m": round(ret_1m, 1),
        "ret_3m": round(ret_3m, 1),
        "pct_from_low": round(pct_from_low, 1),
        "pct_from_high": round(pct_from_high, 1),
        "near_support": near_support,
        "near_resistance": near_resistance,
        "manipulation_signal": manipulation,
        "momentum_bad": momentum_bad,
        "per": per if per > 0 else None,
        "pbr": pbr if pbr > 0 else None,
        "roe": roe,
        "div": div,
        "mktcap": mktcap,
        "sector": sector,
        "score": 50,  # 기본 점수 (단기/중기 트랙 필터에서 사용)
        "dart_financials": {},
    }


def calc_track_score_at(track: str, features: dict) -> tuple:
    """주어진 features dict에서 트랙별 점수 계산. Returns (score, signal, reasons)."""
    cfg = TRACK_CONFIG.get(track)
    if not cfg or not features:
        return 0, False, []

    result = cfg["score_func"](features)
    if result is None:
        # 트랙 필터 미통과
        return 0, False, []
    score, reasons = result
    # 트랙별 최소 매수 점수 — 우선 50 공통, 추후 백테스트 결과 보고 튜닝
    signal = score >= 50
    return score, signal, reasons


# ════════════════════════════════════════════════
# 점수 계산 (백테스팅 전용 단순화 — DART/뉴스 제외)
# ════════════════════════════════════════════════
def calc_swing_score_at(
    closes: pd.Series,
    volumes: pd.Series,
    investor_row: dict,
    fundamental_row: dict,
    sector: str,
    investor_3day_rows: list = None,
    new_rules: bool = False,
) -> tuple:
    """주어진 시점 데이터로 스윙 점수와 매수시그널 계산.

    Args:
        investor_3day_rows: 최근 3거래일 외국인/기관 데이터 (new_rules=True 시 필수)
        new_rules: True면 한국 스윙 확률 룰 추가 적용
                   - 외국인+기관 3일 연속 순매수 가산점 (둘 다 X면 차단)
                   - 거래량 +500% 초과 차단 (과열 회피)

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

    # ── 한국 스윙 확률 룰 (new_rules=True 시) ──
    # B안: 외국인 OR 기관 3일 합산 순매수 > 0 (둘 다 음수면 차단)
    # *3일 연속 양수*는 너무 엄격 (6개월 매매 0건 검증), *합산*으로 완화
    foreign_3day_sum = 0.0
    inst_3day_sum = 0.0
    foreign_3day_buy = False  # 3일 연속 양수 (가산점용)
    inst_3day_buy = False
    if new_rules and investor_3day_rows and len(investor_3day_rows) >= 3:
        try:
            foreign_vals = [float(r.get("외국인합계", 0)) for r in investor_3day_rows[-3:]]
            inst_vals = [float(r.get("기관합계", 0)) for r in investor_3day_rows[-3:]]
            foreign_3day_sum = sum(foreign_vals)
            inst_3day_sum = sum(inst_vals)
            foreign_3day_buy = all(v > 0 for v in foreign_vals)
            inst_3day_buy = all(v > 0 for v in inst_vals)
        except Exception:
            pass

    if new_rules:
        # 가산점 — 3일 합산 매수 + 3일 연속이면 추가 가산
        if foreign_3day_sum > 0:
            sw += 5
            if foreign_3day_buy:
                sw += 5  # 3일 연속이면 추가 +5
        if inst_3day_sum > 0:
            sw += 3
            if inst_3day_buy:
                sw += 2

    # 매수 시그널 — 백테스트 전용 임계치 사용 (DART/뉴스 미반영 보정)
    # 파라미터 최적화 환경변수 사용 (Grid Search)
    signal = (
        sw >= BACKTEST_SCORE_MIN
        and rsi < BT_RSI_BUY_MAX
        and not manipulation
        and not momentum_bad
        and not near_resistance
        and vol_ratio >= BT_VOL_RATIO_MIN
        and ret_1m > BT_RET_1M_MIN
    )

    # ── 새 룰 추가 가드 (B안: 합산 OR 조건) ──
    if new_rules and signal:
        # 외국인 OR 기관 *둘 중 하나라도* 3일 합산 매수 > 0 필수
        # (둘 다 3일 합산 음수면 = 외인+기관 동반 매도세 → 차단)
        if foreign_3day_sum <= 0 and inst_3day_sum <= 0:
            signal = False
        # 거래량 +500% 초과 → 과열 차단 (한국 스윙 정석)
        if vol_ratio > 500:
            signal = False

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
        "manipulation": manipulation,
        "momentum_bad": momentum_bad,
        "near_resistance": near_resistance,
        "foreign_3day_buy": foreign_3day_buy,
        "inst_3day_buy": inst_3day_buy,
        "foreign_3day_sum": round(foreign_3day_sum, 1),
        "inst_3day_sum": round(inst_3day_sum, 1),
    }


# ════════════════════════════════════════════════
# 시뮬레이션 엔진
# ════════════════════════════════════════════════
def simulate_track(track: str, months: int = 1) -> dict:
    """5/14 4트랙 백테스트 — 트랙별 점수 + 매수/매도 룰 분리.

    Args:
        track: "swing" / "short_term" / "mid_term" / "long_term"
        months: 백테스트 기간 (개월)

    Returns: metrics dict (수익률 / 승률 / MDD / 매매 기록)
    """
    if not _PYKRX_OK:
        return {"error": "pykrx 미설치"}

    cfg = TRACK_CONFIG.get(track)
    if not cfg:
        return {"error": f"알 수 없는 트랙: {track}"}

    # 기간 설정
    base_end = _now_kst().replace(tzinfo=None, hour=0, minute=0, second=0, microsecond=0) - timedelta(days=1)
    start_dt = base_end - timedelta(days=months * 31 + 60)
    sim_start = base_end - timedelta(days=months * 31)

    print(f"\n{'='*60}")
    print(f"백테스트 — {cfg['label']} / 최근 {months}개월")
    print(f"기간: {sim_start.date()} ~ {base_end.date()}")
    print(f"룰: target +{cfg['target1_pct']*100:.0f}%/+{cfg['target2_pct']*100:.0f}% / "
          f"stop {'-'+str(int(cfg['stop_pct']*100))+'%' if cfg['stop_pct'] else 'X(헌법)'} / "
          f"max_hold {cfg['max_hold']}일")
    print(f"{'='*60}\n")

    s_str = start_dt.strftime("%Y%m%d")
    e_str = base_end.strftime("%Y%m%d")

    # 데이터 로드 (KR_STOCKS 26개)
    print(f"[1/3] {len(KR_STOCKS)}종목 데이터 로드...")
    data = {}
    for i, (ticker, val) in enumerate(KR_STOCKS.items(), 1):
        name, period, sector = val
        code = ticker.split(".")[0]
        ohlcv = load_ohlcv(code, s_str, e_str)
        fund = load_fundamental(code, s_str, e_str)
        if ohlcv.empty:
            continue
        data[code] = {"name": name, "sector": sector, "ohlcv": ohlcv, "fund": fund}
        time.sleep(0.1)
    print(f"  → {len(data)}/{len(KR_STOCKS)}종목 로드 완료\n")

    all_dates = sorted(set().union(*[set(d["ohlcv"].index) for d in data.values()]))
    sim_dates = [d for d in all_dates if d >= pd.Timestamp(sim_start)]
    print(f"[2/3] 시뮬레이션 거래일: {len(sim_dates)}일\n")

    # 시뮬레이션 상태
    cash = INITIAL_CAPITAL
    positions = {}
    closed_trades = []
    cooldown = {}
    daily_capital = []
    filter_pass_count = 0  # 트랙 필터 통과 횟수
    score_buckets = {"<50": 0, "50-59": 0, "60-69": 0, "70-79": 0, "80+": 0}

    print(f"[3/3] 시뮬레이션 실행 ({cfg['label']})...")
    for di, today in enumerate(sim_dates):
        if di % 20 == 0 and di > 0:
            print(f"  진행: {di}/{len(sim_dates)} ({today.date()}) — "
                  f"현금 {cash/1e6:.1f}M / 보유 {len(positions)} / 청산 {len(closed_trades)}")

        today_str = today.strftime("%Y-%m-%d")

        # 매도 점검
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
            buy_date = pd.Timestamp(p["buy_date"])
            held_days = len([d for d in sim_dates if buy_date < d <= today])

            sell_qty = 0
            reason = ""
            is_loss = False
            # 5/29 유연 보유 룰 적용 (stock.py와 동일 로직 — drift 방지)
            quick_exit_days = cfg.get("quick_exit_days", 3)
            quick_exit_pct  = cfg.get("quick_exit_pct", -1.0)
            extend_min_pct  = cfg.get("extend_min_pct", 3.0)
            extended_max    = cfg.get("max_hold_extended", cfg["max_hold"] * 2)
            if cfg["stop_pct"] and pct <= -cfg["stop_pct"] * 100:
                sell_qty = held_qty
                reason = f"손절 ({pct:.1f}%)"
                is_loss = True
            elif pct >= cfg["target2_pct"] * 100:
                sell_qty = held_qty
                reason = f"+{pct:.1f}% 전량 익절"
            elif pct >= cfg["target1_pct"] * 100 and not partial:
                sell_qty = max(1, held_qty // 2)
                reason = f"+{pct:.1f}% 절반 익절"
            # 🔴 NEW: 하락 빨리 청산 (3일+ -1% 미만)
            elif held_days >= quick_exit_days and pct < quick_exit_pct:
                sell_qty = held_qty
                reason = f"하락 빨리 청산 ({held_days}일/{pct:+.1f}%)"
            elif held_days >= cfg["max_hold"]:
                # 💚 NEW: 상승 추세 보유 연장 (+3% 이상 + 연장 한도 미만)
                if held_days < extended_max and pct >= extend_min_pct:
                    continue  # 매도 X, 보유 연장
                # 🟡 정체: 강제 청산
                sell_qty = held_qty
                reason = f"{held_days}일 강제 청산 ({pct:+.1f}%)"

            if sell_qty <= 0:
                continue

            next_idx = di + 1
            if next_idx >= len(sim_dates):
                continue
            next_day = sim_dates[next_idx]
            next_row = data[code]["ohlcv"].loc[data[code]["ohlcv"].index == next_day]
            if next_row.empty:
                continue
            sell_price_raw = float(next_row["시가"].iloc[0])
            sell_price = sell_price_raw * (1 - SLIPPAGE_RATE)
            sell_amount = sell_price * sell_qty
            commission = sell_amount * COMMISSION_RATE
            tax = sell_amount * TRANSACTION_TAX
            net_sell = sell_amount - commission - tax
            cash += net_sell

            buy_amount = buy_price * sell_qty
            net_pnl = net_sell - buy_amount
            net_pct = net_pnl / buy_amount * 100

            closed_trades.append({
                "code": code, "name": p["name"],
                "buy_date": p["buy_date"], "sell_date": next_day.strftime("%Y-%m-%d"),
                "buy_price": buy_price, "sell_price": sell_price_raw,
                "qty": sell_qty, "raw_pct": round(pct, 2), "net_pct": round(net_pct, 2),
                "net_pnl": round(net_pnl, 0), "held_days": held_days,
                "reason": reason, "score": p.get("score", 0), "sector": p.get("sector", ""),
            })

            if sell_qty == held_qty:
                del positions[code]
                if is_loss and cfg["cooldown"] > 0:
                    cd_until = (pd.Timestamp(next_day) + pd.Timedelta(days=cfg["cooldown"])).strftime("%Y-%m-%d")
                    cooldown[code] = cd_until
            else:
                positions[code]["qty"] = held_qty - sell_qty
                positions[code]["partial_sold"] = True

        # 매수 점검
        candidates = []
        for code, d in data.items():
            ohlcv = d["ohlcv"]
            today_idx = ohlcv.index <= today
            ohlcv_slice = ohlcv[today_idx]
            if len(ohlcv_slice) < 30:
                continue

            fund_slice = d["fund"][d["fund"].index <= today] if not d["fund"].empty else pd.DataFrame()
            fund_row = fund_slice.iloc[-1].to_dict() if not fund_slice.empty else {}

            features = _compute_features(
                ohlcv_slice["종가"], ohlcv_slice["거래량"], {}, fund_row, d["sector"]
            )
            if not features:
                continue

            score, signal, reasons = calc_track_score_at(track, features)
            if score > 0:
                filter_pass_count += 1
                bucket = "<50" if score < 50 else "50-59" if score < 60 else "60-69" if score < 70 else "70-79" if score < 80 else "80+"
                score_buckets[bucket] += 1

            if signal and code not in positions and code not in cooldown:
                candidates.append({
                    "code": code, "name": d["name"], "sector": d["sector"],
                    "score": score, "reasons": reasons,
                    "price": features["price"],
                })

        # 점수 높은 순 매수 (일일 한도: 트랙별 차등, 기본 5종목)
        candidates.sort(key=lambda c: -c["score"])
        daily_buy_count = 0
        max_daily = 5 if track == "swing" else 3 if track == "short_term" else 2
        for c in candidates:
            if daily_buy_count >= max_daily:
                break
            # 다음 영업일 시초가로 매수
            next_idx = di + 1
            if next_idx >= len(sim_dates):
                break
            next_day = sim_dates[next_idx]
            next_row = data[c["code"]]["ohlcv"].loc[data[c["code"]]["ohlcv"].index == next_day]
            if next_row.empty:
                continue
            buy_price_raw = float(next_row["시가"].iloc[0])
            buy_price = buy_price_raw * (1 + SLIPPAGE_RATE)
            qty = max(1, int(INVEST_PER_STOCK / buy_price))
            cost = buy_price * qty * (1 + COMMISSION_RATE)
            if cost > cash:
                continue
            cash -= cost
            positions[c["code"]] = {
                "name": c["name"], "qty": qty,
                "buy_price": buy_price, "buy_date": next_day.strftime("%Y-%m-%d"),
                "score": c["score"], "sector": c["sector"],
            }
            daily_buy_count += 1

        # 일별 자산
        holdings_value = sum(
            float(data[code]["ohlcv"].loc[data[code]["ohlcv"].index == today, "종가"].iloc[0]) * p["qty"]
            for code, p in positions.items()
            if not data[code]["ohlcv"].loc[data[code]["ohlcv"].index == today].empty
        )
        daily_capital.append({"date": today_str, "total": cash + holdings_value})  # 5/29 fix: 키 통일

    # 메트릭 계산
    metrics = compute_metrics(daily_capital, closed_trades, INITIAL_CAPITAL)
    metrics["track"] = track
    metrics["track_label"] = cfg["label"]
    metrics["months"] = months
    metrics["start_date"] = sim_start.strftime("%Y-%m-%d")
    metrics["end_date"] = base_end.strftime("%Y-%m-%d")
    metrics["filter_pass_count"] = filter_pass_count
    metrics["score_buckets"] = score_buckets
    metrics["open_positions"] = len(positions)
    metrics["trades"] = closed_trades

    print(f"\n[{cfg['label']}] 완료 — 수익률 {metrics.get('total_return', 0):+.2f}% / "
          f"매매 {metrics.get('total_trades', 0)}건 / 승률 {metrics.get('win_rate', 0):.1f}% / "
          f"필터 통과 {filter_pass_count}회")
    return metrics


def run_4track_backtest(months: int = 1) -> dict:
    """4트랙 백테스트 일괄 실행 + 통합 결과 저장.

    Args:
        months: 백테스트 기간 (기본 1개월, 30일)

    Returns: {track_name: metrics, ...}
    """
    print(f"\n{'='*70}")
    print(f"🔬 4트랙 백테스트 — 최근 {months}개월")
    print(f"{'='*70}\n")

    results = {}
    for track in ["swing", "short_term", "mid_term", "long_term"]:
        try:
            results[track] = simulate_track(track, months)
        except Exception as e:
            print(f"❌ {track} 백테스트 실패: {e}")
            results[track] = {"error": str(e)}

    # 종합 요약
    print(f"\n{'='*70}")
    print(f"📊 4트랙 백테스트 종합")
    print(f"{'='*70}")
    print(f"{'트랙':<25} {'수익률':>10} {'매매':>6} {'승률':>8} {'MDD':>8}")
    print("-" * 70)
    for track, m in results.items():
        if "error" in m:
            print(f"{TRACK_CONFIG[track]['label']:<25} {'ERROR':>10}")
            continue
        ret = m.get("total_return", 0)
        trd = m.get("total_trades", 0)
        win = m.get("win_rate", 0)
        mdd = m.get("mdd", 0)
        print(f"{TRACK_CONFIG[track]['label']:<25} {ret:>+9.2f}% {trd:>6} {win:>7.1f}% {mdd:>7.2f}%")
    print("=" * 70)

    return results


def simulate(months: int = 6, new_rules: bool = False, end_offset_days: int = 0) -> dict:
    """메인 시뮬레이션 — months 개월 과거 데이터로 가상 매매.

    Args:
        new_rules: True면 한국 스윙 확률 룰 적용
                   (외국인+기관 3일 연속 매수 필수 + 거래량 +500% 차단)
        end_offset_days: 현재로부터 N일 이전을 *끝점*으로 (out-of-sample 검증용)
                         0 = 어제까지 / 365 = 1년 전까지 (그 이전 months개월 시뮬)
    """
    if not _PYKRX_OK:
        return {"error": "pykrx 미설치"}

    # 분석 기간 설정 (백테스트 + 분석에 필요한 30거래일 여유)
    # pykrx 인덱스는 tznaive 이므로 비교 시 tzinfo 제거 (TypeError 방지)
    base_end = _now_kst().replace(tzinfo=None, hour=0, minute=0, second=0, microsecond=0) - timedelta(days=1)
    end_dt   = base_end - timedelta(days=end_offset_days)
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

    # 진단 카운터 (왜 매수 신호가 안 나오는지 추적)
    diag = {
        "evals": 0,           # 총 점수 평가 횟수
        "score_max": 0,
        "score_sum": 0.0,
        "score_buckets": {"<30": 0, "30-49": 0, "50-59": 0, "60-69": 0, "70-79": 0, "80+": 0},
        "top_records": [],    # 최고 점수 5개 (code, name, date, score)
        # signal=False일 때 어디서 막혔는지
        "rej_score":       0,
        "rej_rsi":         0,
        "rej_manipulation":0,
        "rej_momentum_bad":0,
        "rej_near_resist": 0,
        "rej_vol_ratio":   0,
        "rej_ret_1m":      0,
        "signals_passed":  0,
        # 데이터 품질 확인
        "foreign_nonzero":  0,
        "inst_nonzero":     0,
        "foreign_eok_sum":  0.0,
        "inst_eok_sum":     0.0,
    }

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

            # 외국인/기관: 최근 3거래일 데이터 (new_rules 검증용)
            inv_3day = []
            if new_rules and not d["invest"].empty:
                past_3 = d["invest"][d["invest"].index <= today].tail(3)
                if not past_3.empty:
                    inv_3day = past_3.to_dict('records')

            # PER: 당일 데이터
            fund_row = {}
            if not d["fund"].empty:
                fv = d["fund"][d["fund"].index == today]
                if not fv.empty:
                    fund_row = fv.iloc[0].to_dict()

            sw, signal, det = calc_swing_score_at(
                closes, volumes, inv_row, fund_row, d["sector"],
                investor_3day_rows=inv_3day,
                new_rules=new_rules,
            )

            # 진단 카운터 (데이터 부족은 제외)
            if not det.get("error"):
                diag["evals"] += 1
                diag["score_sum"] += sw
                if sw > diag["score_max"]:
                    diag["score_max"] = sw
                if sw < 30:    diag["score_buckets"]["<30"]   += 1
                elif sw < 50:  diag["score_buckets"]["30-49"] += 1
                elif sw < 60:  diag["score_buckets"]["50-59"] += 1
                elif sw < 70:  diag["score_buckets"]["60-69"] += 1
                elif sw < 80:  diag["score_buckets"]["70-79"] += 1
                else:          diag["score_buckets"]["80+"]   += 1

                # 최고 점수 5개 추적 (sw 기준 정렬)
                top_records = diag["top_records"]
                top_records.append((sw, code, d["name"], today.strftime("%Y-%m-%d"), det["rsi"], det["foreign_eok"]))
                top_records.sort(key=lambda x: -x[0])
                diag["top_records"] = top_records[:5]

                # 외국인/기관 데이터 품질
                fe = det.get("foreign_eok", 0)
                ie = det.get("inst_eok", 0)
                if fe != 0:
                    diag["foreign_nonzero"] += 1
                    diag["foreign_eok_sum"] += abs(fe)
                if ie != 0:
                    diag["inst_nonzero"] += 1
                    diag["inst_eok_sum"] += abs(ie)

                if signal:
                    diag["signals_passed"] += 1
                else:
                    # 어디서 막혔는지 (첫 실패 기준)
                    if sw < BACKTEST_SCORE_MIN:        diag["rej_score"] += 1
                    elif det["rsi"] >= 65:              diag["rej_rsi"] += 1
                    elif det["manipulation"]:           diag["rej_manipulation"] += 1
                    elif det["momentum_bad"]:           diag["rej_momentum_bad"] += 1
                    elif det["near_resistance"]:        diag["rej_near_resist"] += 1
                    elif det["vol_ratio"] < 100:        diag["rej_vol_ratio"] += 1
                    elif det["ret_1m"] <= -15:          diag["rej_ret_1m"] += 1

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

    # 진단 요약 (왜 매수 신호가 적었는지)
    if diag["evals"] > 0:
        avg_score = diag["score_sum"] / diag["evals"]
        avg_foreign = diag["foreign_eok_sum"] / diag["foreign_nonzero"] if diag["foreign_nonzero"] else 0
        avg_inst    = diag["inst_eok_sum"]    / diag["inst_nonzero"]    if diag["inst_nonzero"]    else 0
        print(f"━━━━━━━━━━ 진단 (점수 분포 + 차단 원인) ━━━━━━━━━━")
        print(f"총 평가: {diag['evals']:,}회 (종목×거래일) / 임계치: {BACKTEST_SCORE_MIN}점")
        print(f"점수 — 평균 {avg_score:.1f} / 최고 {diag['score_max']}")
        print(f"점수 분포:")
        for k, v in diag["score_buckets"].items():
            pct = v / diag["evals"] * 100
            print(f"  {k:>6}점: {v:>6,}회 ({pct:5.1f}%)")
        print(f"매수 신호 통과: {diag['signals_passed']}회")
        print(f"차단 사유 (첫 실패 기준):")
        print(f"  점수 < {BACKTEST_SCORE_MIN}:     {diag['rej_score']:,}")
        print(f"  RSI ≥ 65:        {diag['rej_rsi']:,}")
        print(f"  조작 의심:       {diag['rej_manipulation']:,}")
        print(f"  모멘텀 약화:     {diag['rej_momentum_bad']:,}")
        print(f"  저항선 근처:     {diag['rej_near_resist']:,}")
        print(f"  거래량 < 100%:   {diag['rej_vol_ratio']:,}")
        print(f"  1개월 < -15%:    {diag['rej_ret_1m']:,}")
        print(f"데이터 품질:")
        print(f"  외국인 데이터 있는 일: {diag['foreign_nonzero']:,} / {diag['evals']:,} (평균 |{avg_foreign:.1f}|억)")
        print(f"  기관   데이터 있는 일: {diag['inst_nonzero']:,} / {diag['evals']:,} (평균 |{avg_inst:.1f}|억)")
        print(f"최고 점수 5개:")
        for sw, code, name, dt, rsi, fe in diag["top_records"]:
            print(f"  {sw}점 — {name}({code}) {dt} (RSI {rsi}, 외국인 {fe}억)")
        print(f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n")

    # 메트릭 계산
    metrics = compute_metrics(daily_capital, closed_trades, INITIAL_CAPITAL)
    metrics["diagnostics"] = {
        "threshold":       BACKTEST_SCORE_MIN,
        "evals":           diag["evals"],
        "score_avg":       round(diag["score_sum"] / diag["evals"], 1) if diag["evals"] else 0,
        "score_max":       diag["score_max"],
        "score_buckets":   diag["score_buckets"],
        "signals_passed":  diag["signals_passed"],
        "top_records":     diag["top_records"],
        "rejections":      {
            "score":         diag["rej_score"],
            "rsi":           diag["rej_rsi"],
            "manipulation":  diag["rej_manipulation"],
            "momentum_bad":  diag["rej_momentum_bad"],
            "near_resist":   diag["rej_near_resist"],
            "vol_ratio":     diag["rej_vol_ratio"],
            "ret_1m":        diag["rej_ret_1m"],
        },
        "data_quality":    {
            "foreign_nonzero": diag["foreign_nonzero"],
            "inst_nonzero":    diag["inst_nonzero"],
            "avg_foreign_eok": round(diag["foreign_eok_sum"] / diag["foreign_nonzero"], 1) if diag["foreign_nonzero"] else 0,
            "avg_inst_eok":    round(diag["inst_eok_sum"] / diag["inst_nonzero"], 1) if diag["inst_nonzero"] else 0,
        },
    }
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
def optimize_parameters(months: int = 6) -> dict:
    """파라미터 순차 최적화 — Grid Search.

    각 파라미터별 여러 값 테스트 → 최적값 발견 → 다음 파라미터 (최적값 고정)
    환경: 최근 6개월 1개만 (시간 절약, 시뮬 12번 × 5분 = 60분)

    Returns: {
        'score_min': [...], 'rsi_max': [...], 'vol_min': [...],
        'best': {'score_min': X, 'rsi_max': Y, 'vol_min': Z},
        'final_metrics': {...}
    }
    """
    global BACKTEST_SCORE_MIN, BT_RSI_BUY_MAX, BT_VOL_RATIO_MIN

    results = {"score_min": [], "rsi_max": [], "vol_min": [], "best": {}}

    print("\n" + "="*70)
    print("🔬 파라미터 순차 최적화 (Grid Search)")
    print("="*70)

    # ─── 파라미터 1: 매수 점수 임계 ───
    print(f"\n[1/3] 매수 점수 임계 (BACKTEST_SCORE_MIN)")
    print("-"*70)
    score_candidates = [40, 45, 50, 55]
    score_results = []
    for v in score_candidates:
        BACKTEST_SCORE_MIN = v
        print(f"  → 임계 {v} 시뮬 중...")
        m = simulate(months=months, new_rules=False)
        m["param_value"] = v
        score_results.append(m)
        ret = m.get("cumulative_return_pct", 0) or 0
        trades = m.get("total_trades", 0) or 0
        win = m.get("win_rate_pct", 0) or 0
        print(f"    수익 {ret:+.2f}% / 매매 {trades}건 / 승률 {win:.1f}%")
        results["score_min"].append({"value": v, "return": ret, "trades": trades, "win_rate": win})

    # 최적: 수익률 최고 (단 매매 0건은 제외)
    valid = [r for r in score_results if (r.get("total_trades", 0) or 0) > 0]
    best_score = max(valid, key=lambda x: x.get("cumulative_return_pct", -999)) if valid else score_results[0]
    BACKTEST_SCORE_MIN = best_score["param_value"]
    results["best"]["score_min"] = BACKTEST_SCORE_MIN
    print(f"  ⭐ 최적: 임계 {BACKTEST_SCORE_MIN} (수익 {best_score.get('cumulative_return_pct', 0):+.2f}%)")

    # ─── 파라미터 2: RSI 임계 ───
    print(f"\n[2/3] RSI 매수 차단 임계 (BT_RSI_BUY_MAX) — 점수 임계 {BACKTEST_SCORE_MIN} 고정")
    print("-"*70)
    rsi_candidates = [60, 65, 70, 80]  # 80 = 거의 차단 X
    rsi_results = []
    for v in rsi_candidates:
        BT_RSI_BUY_MAX = v
        print(f"  → RSI < {v} 시뮬 중...")
        m = simulate(months=months, new_rules=False)
        m["param_value"] = v
        rsi_results.append(m)
        ret = m.get("cumulative_return_pct", 0) or 0
        trades = m.get("total_trades", 0) or 0
        win = m.get("win_rate_pct", 0) or 0
        print(f"    수익 {ret:+.2f}% / 매매 {trades}건 / 승률 {win:.1f}%")
        results["rsi_max"].append({"value": v, "return": ret, "trades": trades, "win_rate": win})

    valid = [r for r in rsi_results if (r.get("total_trades", 0) or 0) > 0]
    best_rsi = max(valid, key=lambda x: x.get("cumulative_return_pct", -999)) if valid else rsi_results[0]
    BT_RSI_BUY_MAX = best_rsi["param_value"]
    results["best"]["rsi_max"] = BT_RSI_BUY_MAX
    print(f"  ⭐ 최적: RSI < {BT_RSI_BUY_MAX} (수익 {best_rsi.get('cumulative_return_pct', 0):+.2f}%)")

    # ─── 파라미터 3: 거래량 비율 ───
    print(f"\n[3/3] 거래량 비율 최소 (BT_VOL_RATIO_MIN) — 점수 {BACKTEST_SCORE_MIN} / RSI<{BT_RSI_BUY_MAX} 고정")
    print("-"*70)
    vol_candidates = [50, 100, 150, 200]
    vol_results = []
    for v in vol_candidates:
        BT_VOL_RATIO_MIN = v
        print(f"  → 거래량 ≥ {v}% 시뮬 중...")
        m = simulate(months=months, new_rules=False)
        m["param_value"] = v
        vol_results.append(m)
        ret = m.get("cumulative_return_pct", 0) or 0
        trades = m.get("total_trades", 0) or 0
        win = m.get("win_rate_pct", 0) or 0
        print(f"    수익 {ret:+.2f}% / 매매 {trades}건 / 승률 {win:.1f}%")
        results["vol_min"].append({"value": v, "return": ret, "trades": trades, "win_rate": win})

    valid = [r for r in vol_results if (r.get("total_trades", 0) or 0) > 0]
    best_vol = max(valid, key=lambda x: x.get("cumulative_return_pct", -999)) if valid else vol_results[0]
    BT_VOL_RATIO_MIN = best_vol["param_value"]
    results["best"]["vol_min"] = BT_VOL_RATIO_MIN
    print(f"  ⭐ 최적: 거래량 ≥ {BT_VOL_RATIO_MIN}% (수익 {best_vol.get('cumulative_return_pct', 0):+.2f}%)")

    # ─── 최종 결합 시뮬레이션 ───
    print(f"\n" + "="*70)
    print(f"🏆 최종 조합: 점수≥{BACKTEST_SCORE_MIN} / RSI<{BT_RSI_BUY_MAX} / 거래량≥{BT_VOL_RATIO_MIN}%")
    print("="*70)
    final_metrics = simulate(months=months, new_rules=False)
    results["final_metrics"] = final_metrics
    ret = final_metrics.get("cumulative_return_pct", 0) or 0
    trades = final_metrics.get("total_trades", 0) or 0
    win = final_metrics.get("win_rate_pct", 0) or 0
    print(f"최종 결과: 수익 {ret:+.2f}% / 매매 {trades}건 / 승률 {win:.1f}%")
    print("="*70)

    return results


def multi_env_grid_search(months: int = 6) -> dict:
    """Multi-environment Grid Search — 모든 조합 × 다중 환경 탐색.

    학계 표준 방법. Local Optimum 함정 차단 + 과적합 차단.

    그리드 (3×3×3 = 27조합, 회장 부재 일정 고려 절충):
        매수 점수 임계: 45, 50, 55
        RSI 차단 임계:  60, 65, 70
        거래량 최소:    100, 150, 200

    환경 (2개, out-of-sample 검증):
        E1: 최근 6개월 (in-sample)
        E2: 1년 전 6개월 (out-of-sample)

    총: 27 × 2 = 54번 시뮬 ≈ 4~5시간

    Returns: {
        'all_results': [...],
        'best_overall': {...},
        'env_consistent': [...],  # 양 환경 모두 좋은 조합 TOP 5
    }
    """
    global BACKTEST_SCORE_MIN, BT_RSI_BUY_MAX, BT_VOL_RATIO_MIN

    print("\n" + "="*75)
    print("🌐 Multi-environment Grid Search")
    print("="*75)
    print("그리드: 점수[45,50,55] × RSI[60,65,70] × 거래량[100,150,200] = 27조합")
    print("환경: 최근 6개월 + 1년 전 6개월 (out-of-sample 검증)")
    print("총 54번 시뮬 — 예상 4~5시간\n")

    score_vals = [45, 50, 55]
    rsi_vals = [60, 65, 70]
    vol_vals = [100, 150, 200]

    envs = [
        ("최근 6개월", 0),
        ("1년 전 6개월", 365),
    ]

    all_results = []
    sim_count = 0
    total = len(score_vals) * len(rsi_vals) * len(vol_vals) * len(envs)

    for env_name, offset in envs:
        print(f"\n──── 환경: {env_name} ────")
        for sc in score_vals:
            for rsi in rsi_vals:
                for vol in vol_vals:
                    sim_count += 1
                    BACKTEST_SCORE_MIN = sc
                    BT_RSI_BUY_MAX = rsi
                    BT_VOL_RATIO_MIN = vol

                    label = f"점수≥{sc}/RSI<{rsi}/거래량≥{vol}%"
                    print(f"  [{sim_count}/{total}] {env_name} | {label}")
                    m = simulate(months=months, new_rules=False, end_offset_days=offset)
                    ret = m.get("cumulative_return_pct", 0) or 0
                    trades = m.get("total_trades", 0) or 0
                    win = m.get("win_rate_pct", 0) or 0

                    all_results.append({
                        "env": env_name,
                        "score_min": sc,
                        "rsi_max": rsi,
                        "vol_min": vol,
                        "return": ret,
                        "trades": trades,
                        "win_rate": win,
                        "mdd": m.get("max_drawdown_pct", 0) or 0,
                        "sharpe": m.get("sharpe_ratio", 0) or 0,
                    })
                    print(f"    → 수익 {ret:+.2f}% / 매매 {trades}건 / 승률 {win:.1f}%")

    # 환경 무관 일관 좋은 조합 찾기 (Robust Selection)
    # 각 조합별로 양 환경 결과 합치기
    combo_results = {}
    for r in all_results:
        key = (r["score_min"], r["rsi_max"], r["vol_min"])
        if key not in combo_results:
            combo_results[key] = {"e1": None, "e2": None}
        if r["env"] == "최근 6개월":
            combo_results[key]["e1"] = r
        else:
            combo_results[key]["e2"] = r

    # 양 환경 모두 매매 >= 3건 + 양 환경 평균 수익률
    robust_combos = []
    for key, envs_data in combo_results.items():
        e1 = envs_data["e1"]
        e2 = envs_data["e2"]
        if e1 and e2 and e1["trades"] >= 3 and e2["trades"] >= 3:
            avg_return = (e1["return"] + e2["return"]) / 2
            min_return = min(e1["return"], e2["return"])
            robust_combos.append({
                "score_min": key[0],
                "rsi_max": key[1],
                "vol_min": key[2],
                "e1_return": e1["return"],
                "e2_return": e2["return"],
                "avg_return": avg_return,
                "min_return": min_return,
                "e1_trades": e1["trades"],
                "e2_trades": e2["trades"],
                "avg_win": (e1["win_rate"] + e2["win_rate"]) / 2,
            })

    # min_return 기준 정렬 (최악 환경에서도 좋은 조합 우선)
    robust_combos.sort(key=lambda x: -x["min_return"])
    top5 = robust_combos[:5]

    # 단순 최고 수익률 (참고)
    best_overall = max(all_results, key=lambda x: x["return"]) if all_results else None

    # 결과 출력
    print("\n" + "="*75)
    print("🏆 Multi-environment Grid Search 결과")
    print("="*75)

    if best_overall:
        print(f"\n📈 단일 환경 최고 수익률 (참고):")
        print(f"   {best_overall['env']} | 점수≥{best_overall['score_min']}/RSI<{best_overall['rsi_max']}/거래량≥{best_overall['vol_min']}%")
        print(f"   수익 {best_overall['return']:+.2f}% / 매매 {best_overall['trades']}건 / 승률 {best_overall['win_rate']:.1f}%")
        print(f"   ⚠️ 단일 환경 최고는 과적합 위험")

    if top5:
        print(f"\n⭐ Robust Top 5 (양 환경 모두 좋은 조합):")
        print(f"   기준: 양 환경 매매 ≥3건, 최악 환경 수익률 우선")
        for i, c in enumerate(top5, 1):
            print(f"\n   #{i}. 점수≥{c['score_min']} / RSI<{c['rsi_max']} / 거래량≥{c['vol_min']}%")
            print(f"      최근 6개월:   수익 {c['e1_return']:+.2f}% / {c['e1_trades']}건")
            print(f"      1년 전 6개월: 수익 {c['e2_return']:+.2f}% / {c['e2_trades']}건")
            print(f"      평균 수익: {c['avg_return']:+.2f}% / 평균 승률: {c['avg_win']:.1f}%")

    print("\n" + "="*75)

    return {
        "all_results": all_results,
        "best_overall": best_overall,
        "robust_top5": top5,
    }


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

    # 매매 0건이면 진단 요약을 노출 (왜 신호가 안 나왔는지)
    diag = metrics.get("diagnostics", {})
    if metrics.get("total_trades", 0) == 0 and diag.get("evals", 0) > 0:
        threshold = diag.get("threshold", BACKTEST_SCORE_MIN)
        lines.extend([
            "",
            f"🔍 <b>진단 (매매 0건 사유)</b>",
            f"평가 {diag['evals']:,}회 / 임계치 {threshold}점",
            f"평균 {diag['score_avg']:.1f} / 최고 {diag['score_max']} / 신호 통과 {diag['signals_passed']}회",
        ])
        rej = diag.get("rejections", {})
        rej_sorted = sorted(rej.items(), key=lambda x: -x[1])[:3]
        if any(v > 0 for _, v in rej_sorted):
            lines.append("주요 차단:")
            for k, v in rej_sorted:
                if v > 0:
                    label = {
                        "score":        f"점수 미달",
                        "rsi":          "RSI 65 이상",
                        "manipulation": "조작 의심",
                        "momentum_bad": "모멘텀 약화",
                        "near_resist":  "저항선 근처",
                        "vol_ratio":    "거래량 부족",
                        "ret_1m":       "1개월 -15% 이하",
                    }.get(k, k)
                    lines.append(f"  • {label}: {v:,}회")
        # 데이터 품질
        dq = diag.get("data_quality", {})
        if dq:
            lines.extend([
                "",
                f"데이터 품질:",
                f"  외국인 수급 인식: {dq['foreign_nonzero']:,}/{diag['evals']:,}일 (평균 |{dq['avg_foreign_eok']}|억)",
                f"  기관 수급 인식:   {dq['inst_nonzero']:,}/{diag['evals']:,}일 (평균 |{dq['avg_inst_eok']}|억)",
            ])
        # 최고 점수 종목 (어디까지 갔는지)
        tops = diag.get("top_records", [])
        if tops:
            lines.append("최고 점수 (TOP 3):")
            for rec in tops[:3]:
                sw, code, name, dt, rsi, fe = rec
                lines.append(f"  {sw}점 - {name} {dt}")

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
    four_track_mode = False
    for arg in sys.argv[1:]:
        if arg == "--no-report":
            no_report = True
        elif arg == "--4track":
            four_track_mode = True
            months = 1  # 4트랙 기본 1개월 (30일)
        else:
            try:
                months = int(arg)
            except ValueError:
                pass

    print(f"백테스팅 v4.1 — 4트랙 통합")
    print(f"기본 기간: 최근 {months}개월\n")

    if not _PYKRX_OK:
        print("❌ pykrx 미설치 — pip install pykrx 필요")
        sys.exit(1)

    # ─── 5/14 4트랙 백테스트 모드 (--4track) ───
    if four_track_mode:
        results = run_4track_backtest(months=months)

        combined = {
            "four_track_mode": True,
            "months": months,
            "generated_at": _now_kst().isoformat(),
            "tracks": {
                t: {k: v for k, v in m.items() if k not in ("trades", "daily_capital")}
                for t, m in results.items()
            },
        }
        try:
            with open(BACKTEST_RESULTS, "w", encoding="utf-8") as f:
                json.dump(combined, f, ensure_ascii=False, indent=2, default=str)
            print(f"\n📁 결과 저장: {BACKTEST_RESULTS}")
        except Exception as e:
            print(f"⚠️ 저장 실패: {e}")

        if not no_report:
            try:
                tg_lines = [
                    f"🔬 <b>4트랙 백테스트 결과</b> (최근 {months}개월)",
                    "",
                ]
                for track, m in results.items():
                    if "error" in m:
                        tg_lines.append(f"❌ {TRACK_CONFIG[track]['label']}: {m['error']}")
                        continue
                    ret = m.get("total_return", 0)
                    trd = m.get("total_trades", 0)
                    win = m.get("win_rate", 0)
                    mdd = m.get("mdd", 0)
                    fpc = m.get("filter_pass_count", 0)
                    tg_lines.extend([
                        f"<b>{TRACK_CONFIG[track]['label']}</b>",
                        f"  수익률 {ret:+.2f}% / 매매 {trd}건 / 승률 {win:.1f}% / MDD {mdd:.1f}%",
                        f"  필터 통과 {fpc}회",
                        "",
                    ])
                tg_lines.append("<i>※ 30일 단기 검증 — 통계적 의미는 추후 6개월 확대</i>")
                tg_send("\n".join(tg_lines))
            except Exception as e:
                print(f"⚠️ 텔레그램 전송 실패: {e}")
        sys.exit(0)

    # ─── Multi-environment Grid Search 모드 (5/12 기본 ON) ───
    if BT_GRID_MODE:
        grid_results = multi_env_grid_search(months=months)

        # 결과 저장
        combined = {
            "grid_mode": True,
            "months": months,
            "generated_at": _now_kst().isoformat(),
            "grid_search": grid_results,
        }
        try:
            with open(BACKTEST_RESULTS, "w", encoding="utf-8") as f:
                json.dump(combined, f, ensure_ascii=False, indent=2, default=str)
            print(f"\n📁 결과 저장: {BACKTEST_RESULTS}")
        except Exception as e:
            print(f"⚠️ 저장 실패: {e}")

        # 텔레그램 발송
        if not no_report:
            try:
                top5 = grid_results.get("robust_top5", [])
                tg_lines = [
                    f"🌐 <b>Multi-env Grid Search 결과</b> ({months}개월 × 2환경)",
                    f"총 54조합 검증 — 양 환경 모두 좋은 조합 TOP 5:",
                    "",
                ]
                if top5:
                    for i, c in enumerate(top5, 1):
                        tg_lines.extend([
                            f"<b>#{i}. 점수≥{c['score_min']} / RSI&lt;{c['rsi_max']} / 거래량≥{c['vol_min']}%</b>",
                            f"  최근 6개월:   {c['e1_return']:+.2f}% / {c['e1_trades']}건",
                            f"  1년 전 6개월: {c['e2_return']:+.2f}% / {c['e2_trades']}건",
                            f"  평균 수익: {c['avg_return']:+.2f}% / 평균 승률: {c['avg_win']:.1f}%",
                            "",
                        ])
                else:
                    tg_lines.extend([
                        "⚠️ 양 환경 모두 매매 ≥3건 조건 충족 조합 없음",
                        "→ 그리드 더 완화 필요 또는 알고리즘 재설계",
                        "",
                    ])

                best = grid_results.get("best_overall")
                if best:
                    tg_lines.extend([
                        f"<b>📈 단일 환경 최고 (참고, 과적합 위험)</b>",
                        f"{best['env']}: {best['return']:+.2f}% / {best['trades']}건",
                        f"점수≥{best['score_min']} / RSI&lt;{best['rsi_max']} / 거래량≥{best['vol_min']}%",
                    ])

                tg_send("\n".join(tg_lines))
            except Exception as e:
                print(f"⚠️ 텔레그램 전송 실패: {e}")
        sys.exit(0)

    # ─── 파라미터 순차 최적화 모드 ───
    if BT_OPTIMIZE_MODE:
        opt_results = optimize_parameters(months=months)

        # 결과 저장
        combined = {
            "optimize_mode": True,
            "months": months,
            "generated_at": _now_kst().isoformat(),
            "optimization": opt_results,
        }
        try:
            with open(BACKTEST_RESULTS, "w", encoding="utf-8") as f:
                json.dump(combined, f, ensure_ascii=False, indent=2, default=str)
            print(f"\n📁 결과 저장: {BACKTEST_RESULTS}")
        except Exception as e:
            print(f"⚠️ 저장 실패: {e}")

        # 텔레그램 발송
        if not no_report:
            try:
                tg_lines = [
                    f"🔬 <b>파라미터 최적화 결과</b> (최근 {months}개월)",
                    "",
                    "<b>━━ 매수 점수 임계 ━━</b>",
                ]
                for r in opt_results["score_min"]:
                    tg_lines.append(f"  ≥{r['value']:>3}: 수익 {r['return']:+.2f}% / {r['trades']}건 / 승률 {r['win_rate']:.1f}%")
                tg_lines.append(f"  ⭐ 최적: <b>≥{opt_results['best']['score_min']}</b>")

                tg_lines.extend(["", "<b>━━ RSI 차단 임계 ━━</b>"])
                for r in opt_results["rsi_max"]:
                    tg_lines.append(f"  &lt;{r['value']:>3}: 수익 {r['return']:+.2f}% / {r['trades']}건 / 승률 {r['win_rate']:.1f}%")
                tg_lines.append(f"  ⭐ 최적: <b>RSI&lt;{opt_results['best']['rsi_max']}</b>")

                tg_lines.extend(["", "<b>━━ 거래량 최소 ━━</b>"])
                for r in opt_results["vol_min"]:
                    tg_lines.append(f"  ≥{r['value']:>3}%: 수익 {r['return']:+.2f}% / {r['trades']}건 / 승률 {r['win_rate']:.1f}%")
                tg_lines.append(f"  ⭐ 최적: <b>≥{opt_results['best']['vol_min']}%</b>")

                final = opt_results["final_metrics"]
                tg_lines.extend([
                    "",
                    "<b>━━ 🏆 최종 조합 ━━</b>",
                    f"점수≥{opt_results['best']['score_min']} / RSI&lt;{opt_results['best']['rsi_max']} / 거래량≥{opt_results['best']['vol_min']}%",
                    f"수익률: <b>{final.get('cumulative_return_pct', 0) or 0:+.2f}%</b>",
                    f"매매: {final.get('total_trades', 0) or 0}건 / 승률 {final.get('win_rate_pct', 0) or 0:.1f}%",
                    f"MDD: {final.get('max_drawdown_pct', 0) or 0:.2f}% / Sharpe: {final.get('sharpe_ratio', 0) or 0:.2f}",
                ])
                tg_send("\n".join(tg_lines))
            except Exception as e:
                print(f"⚠️ 텔레그램 전송 실패: {e}")
        sys.exit(0)

    # ── 3기간 × 2알고리즘 = 6회 시뮬 ──
    # 환경 1: 최근 6개월 (in-sample)
    # 환경 2: 최근 12개월 (extended)
    # 환경 3: 1년 전 6개월 (out-of-sample, 과적합 검증)
    test_envs = [
        ("최근 6개월", 6, 0),
        ("최근 12개월", 12, 0),
        ("1년 전 6개월 (out-of-sample)", 6, 365),
    ]

    all_results = []
    for env_name, env_months, env_offset in test_envs:
        # 현재 알고리즘
        print("\n" + "="*70)
        print(f"환경: {env_name} — 현재 알고리즘")
        print("="*70)
        m_cur = simulate(months=env_months, new_rules=False, end_offset_days=env_offset)
        m_cur["env"] = env_name
        m_cur["algorithm"] = "current"

        # 새 알고리즘
        print("\n" + "="*70)
        print(f"환경: {env_name} — 새 알고리즘 (한국 스윙 확률 룰)")
        print("="*70)
        m_new = simulate(months=env_months, new_rules=True, end_offset_days=env_offset)
        m_new["env"] = env_name
        m_new["algorithm"] = "new_rules"

        all_results.append({"env": env_name, "current": m_cur, "new": m_new})

    # 호환성: 메인 결과는 첫 환경 (최근 6개월)
    metrics_current = all_results[0]["current"]
    metrics_new = all_results[0]["new"]

    # ── 결과 비교 출력 ──
    print("\n" + "="*70)
    print("📊 비교 결과 — 현재 vs 새 룰")
    print("="*70)
    def _fmt(metrics, key, fmt="{:.2f}"):
        v = metrics.get(key)
        if v is None: return "N/A"
        try: return fmt.format(v)
        except: return str(v)

    print(f"{'항목':<25} {'현재':>15} {'새 룰':>15}")
    print("-"*70)
    for key, label, fmt in [
        ("total_return", "수익률 (%)", "{:+.2f}"),
        ("annual_return", "연환산 (%)", "{:+.2f}"),
        ("mdd", "MDD (%)", "{:.2f}"),
        ("sharpe", "Sharpe", "{:.2f}"),
        ("total_trades", "총 매매 (건)", "{}"),
        ("win_rate", "승률 (%)", "{:.1f}"),
        ("avg_pct", "평균 수익률 (%)", "{:+.2f}"),
        ("avg_hold_days", "평균 보유 (일)", "{:.1f}"),
    ]:
        print(f"{label:<25} {_fmt(metrics_current, key, fmt):>15} {_fmt(metrics_new, key, fmt):>15}")
    print("="*70)

    # ── 다중 환경 종합 결과 ──
    print("\n" + "="*70)
    print("📊 다중 환경 종합 비교")
    print("="*70)
    print(f"{'환경':<35} {'현재 수익률':>12} {'새 룰 수익률':>12} {'우위':>6}")
    print("-"*70)
    for r in all_results:
        cur_ret = r["current"].get("total_return") or r["current"].get("cumulative_return_pct", 0)
        new_ret = r["new"].get("total_return") or r["new"].get("cumulative_return_pct", 0)
        try:
            cur_f = float(cur_ret) if cur_ret else 0
            new_f = float(new_ret) if new_ret else 0
            winner = "새 룰 ✅" if new_f > cur_f else ("현재 ❌" if cur_f > new_f else "동일")
            print(f"{r['env']:<35} {cur_f:>+11.2f}% {new_f:>+11.2f}% {winner:>6}")
        except Exception:
            print(f"{r['env']:<35} {'?':>12} {'?':>12} {'?':>6}")
    print("="*70)

    # 결과 저장 (다중 환경)
    combined = {
        "comparison_mode": True,
        "multi_env": True,
        "months": months,
        "generated_at": _now_kst().isoformat(),
        "environments": all_results,
        "current": metrics_current,
        "new_rules": metrics_new,
    }
    try:
        with open(BACKTEST_RESULTS, "w", encoding="utf-8") as f:
            json.dump(combined, f, ensure_ascii=False, indent=2, default=str)
        print(f"\n📁 결과 저장: {BACKTEST_RESULTS}")
    except Exception as e:
        print(f"⚠️ 저장 실패: {e}")

    if not no_report:
        try:
            # 다중 환경 비교 결과를 텔레그램으로 발송
            tg_lines = [
                f"🔬 <b>백테스트 다중 환경 검증</b>",
                "",
                "<b>━━ 환경별 수익률 비교 ━━</b>",
            ]
            for r in all_results:
                cur_ret = r["current"].get("total_return") or r["current"].get("cumulative_return_pct", 0)
                new_ret = r["new"].get("total_return") or r["new"].get("cumulative_return_pct", 0)
                cur_trd = r["current"].get("total_trades", 0)
                new_trd = r["new"].get("total_trades", 0)
                cur_win = r["current"].get("win_rate") or r["current"].get("win_rate_pct", 0)
                new_win = r["new"].get("win_rate") or r["new"].get("win_rate_pct", 0)
                try:
                    cur_f = float(cur_ret) if cur_ret else 0
                    new_f = float(new_ret) if new_ret else 0
                    winner = "✅ 새 룰 우위" if new_f > cur_f else ("❌ 현재 우위" if cur_f > new_f else "동일")
                    tg_lines.extend([
                        "",
                        f"<b>📅 {r['env']}</b> — {winner}",
                        f"  현재: 수익 {cur_f:+.2f}% / 매매 {cur_trd}건 / 승률 {cur_win:.1f}%",
                        f"  새 룰: 수익 {new_f:+.2f}% / 매매 {new_trd}건 / 승률 {new_win:.1f}%",
                    ])
                except Exception:
                    tg_lines.append(f"<b>{r['env']}</b>: 비교 불가")

            tg_lines.extend([
                "",
                "<i>새 룰: 외국인+기관 3일 연속 매수 + 거래량 +500% 차단</i>",
                "<i>3환경 모두 새 룰 우위 → 진짜 좋은 룰 / 환경별 다름 → 과적합 의심</i>",
            ])
            tg_send("\n".join(tg_lines))
        except Exception as e:
            print(f"⚠️ 텔레그램 전송 실패: {e}")

    print("\n✅ 백테스팅 완료")
