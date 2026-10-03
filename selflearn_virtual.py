# -*- coding: utf-8 -*-
"""자가학습 — 8년 가상 매매 생성기 (GitHub 서버 주간 실행용, 10/1).

실제 자동매수와 *같은 함수* stock.calc_live_swing() 으로 과거 매일의 매수 후보를 판정하고,
봇 매도 규칙으로 결과를 계산해 '그 순간의 상태(feature) ↔ 결과' 데이터셋을 만든다.

한계(정직): 과거 외국인·기관 수급, DART 공시, PER 은 없어 0점 처리 / 진입=신호 다음날 시가(실제는 장중) /
종목=현재 시총 상위(생존편향) → 절대 수익률보다 '어떤 조건이 더 나쁜가' 비교용.
매도: 손절 -4%·+6% 절반·+10% 전량은 장중 도달 즉시(갭이면 시가), 3일+ -1% 미만·5일(+3%↑면 10일)은 종가→다음날 시가. 매수일=0일.
"""
import os, sys, glob, time
import numpy as np, pandas as pd

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)
CACHE = os.path.join(BASE, ".selflearn_cache")
COMM, TAX, SLIP = 0.00015, 0.0018, 0.001
START_LOAD = "2017-10-01"
SIM_START = "2018-09-01"

# 실시간 판정 시점에도 같은 정의로 구할 수 있는 지표만 '발굴 가능' 으로 표시 (discover 가 사용)
LIVE_FEATURES = ["rsi", "bb_pct", "ret_1w", "ret_1m", "ret_3m", "pct_from_low", "pct_from_high",
                 "dist_ma20", "atr_pct", "kospi_1d", "breadth_prev"]


def load_universe(n: int = 500) -> list:
    import FinanceDataReader as fdr
    l = fdr.StockListing("KRX")
    l = l[l["Market"].isin(["KOSPI", "KOSDAQ", "KOSDAQ GLOBAL"])]
    l = l[l["Code"].str.endswith("0") & ~l["Name"].str.contains("스팩|우$|우B$|리츠")]
    return list(l.sort_values("Marcap", ascending=False).head(n)["Code"])


def load_ohlcv(code: str, end: str) -> pd.DataFrame:
    """캐시 + 증분: 캐시 마지막 날 이후만 새로 받음."""
    import FinanceDataReader as fdr
    os.makedirs(CACHE, exist_ok=True)
    fn = os.path.join(CACHE, f"{code}.csv")
    old = pd.DataFrame()
    if os.path.exists(fn):
        try:
            old = pd.read_csv(fn, index_col=0, parse_dates=True)
        except Exception:
            old = pd.DataFrame()
    start = START_LOAD if old.empty else (old.index[-1] - pd.Timedelta(days=7)).strftime("%Y-%m-%d")
    for i in range(3):
        try:
            new = fdr.DataReader(code, start, end)[["Open", "High", "Low", "Close", "Volume"]]
            break
        except Exception:
            time.sleep(1 + i)
    else:
        return old
    df = pd.concat([old, new]) if not old.empty else new
    df = df[~df.index.duplicated(keep="last")].sort_index()
    df = df[(df.Volume > 0) & (df.Close > 0)]
    df.to_csv(fn)
    return df


def features(df: pd.DataFrame) -> pd.DataFrame:
    """analyze() 와 같은 정의의 일봉 지표 (오늘 종가 기준)."""
    c, v, h, l = df.Close, df.Volume, df.High, df.Low
    f = pd.DataFrame(index=df.index)
    d = c.diff()
    g = d.clip(lower=0).rolling(14).mean(); ls = (-d.clip(upper=0)).rolling(14).mean()
    f["rsi"] = (100 - 100 / (1 + g / ls.replace(0, 1e-9))).round(1)
    macd = c.ewm(span=12).mean() - c.ewm(span=26).mean(); sig = macd.ewm(span=9).mean()
    f["macd_cross"] = macd > sig
    f["macd_hist"] = macd - sig
    s20, sd20 = c.rolling(20).mean(), c.rolling(20).std()
    f["bb_pct"] = ((c - (s20 - 2 * sd20)) / (4 * sd20 + 1e-9) * 100).round(1)
    f["vol_ratio"] = (v / v.rolling(20).mean() * 100).round(0)
    f["change"] = (c.pct_change() * 100).round(2)
    f["ret_1w"] = ((c / c.shift(4) - 1) * 100).round(1)
    f["ret_1m"] = ((c / c.shift(19) - 1) * 100).round(1)
    f["ret_3m"] = ((c / c.shift(100) - 1) * 100).round(1)
    hi52, lo52 = h.rolling(250, min_periods=60).max(), l.rolling(250, min_periods=60).min()
    f["pct_from_low"] = ((c / lo52 - 1) * 100).round(1)
    f["pct_from_high"] = ((c / hi52 - 1) * 100).round(1)
    ma60 = c.rolling(60, min_periods=20).mean()
    cv = c.values; n = len(cv); sup = np.full(n, np.nan); res = np.full(n, np.nan)
    m20v, m60v = s20.values, ma60.values
    for i in range(20, n):
        r = cv[i - 19:i + 1]; p = cv[i]
        sh = [r[j] for j in range(1, 19) if r[j] > r[j - 1] and r[j] > r[j + 1]]
        sl = [r[j] for j in range(1, 19) if r[j] < r[j - 1] and r[j] < r[j + 1]]
        below = [x for x in sl if x < p]; above = [x for x in sh if x > p]
        sup[i] = max(below + [m20v[i], m60v[i]]) if below else min(m20v[i], m60v[i])
        res[i] = min(above) if above else p * 1.10
    f["near_support"] = np.abs(cv - sup) / cv < 0.03
    f["near_resistance"] = np.abs(res - cv) / cv < 0.03
    f["dist_ma20"] = (c / s20 - 1) * 100
    f["atr_pct"] = (pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
                    .rolling(14).mean() / c * 100)
    f["momentum_bad"] = (f.ret_3m < -20) & (f.rsi < 40) & (f.macd_hist < 0)
    f["manip"] = (f.vol_ratio > 300) & (f.ret_1w < -10)
    return f


def simulate(df: pd.DataFrame, i: int):
    O, H, L, C = df.Open.values, df.High.values, df.Low.values, df.Close.values
    n = len(C)
    if i + 1 >= n: return None
    bp = O[i + 1] * (1 + SLIP); half = False; parts = []
    stop, t1, t2 = bp * 0.96, bp * 1.06, bp * 1.10

    def fin(price, why, held):
        parts.append((1 - sum(w for w, _ in parts), price * (1 - SLIP) * (1 - COMM - TAX)))
        return sum(w * (p / (bp * (1 + COMM)) - 1) for w, p in parts) * 100, held, why

    for k in range(i + 1, n):
        held = k - (i + 1); first = k == i + 1; op = O[k]
        if L[k] <= stop: return fin(stop if first else min(op, stop), "손절", held)
        if H[k] >= t2: return fin(t2 if first else max(op, t2), "전량익절", held)
        if H[k] >= t1 and not half:
            parts.append((0.5, (t1 if first else max(op, t1)) * (1 - SLIP) * (1 - COMM - TAX))); half = True
        pct = (C[k] / bp - 1) * 100
        if (held >= 3 and pct < -1) or (held >= 5 and not (held < 10 and pct >= 3)):
            if k + 1 >= n: return None
            return fin(O[k + 1], "기간/빨리청산", held)
    return None


def market_table(closes: pd.DataFrame, end: str) -> pd.DataFrame:
    import FinanceDataReader as fdr
    m = pd.DataFrame(index=closes.index)
    try:
        # 10/3: FDR 'KS11'이 9/17에서 멈춤(ffill로 이후 등락률이 전부 0이 됨) → 네이버 우선, 둘 중 최신 것 사용
        srcs = []
        for sym in ("NAVER:KOSPI", "KS11"):
            try:
                s = fdr.DataReader(sym, START_LOAD, end)["Close"].dropna()
                if len(s): srcs.append(s)
            except Exception as e:
                print(f"  [가상매매] 코스피 {sym} 실패: {e}")
        k = max(srcs, key=lambda s: pd.to_datetime(s.index[-1]))
        k.index = pd.to_datetime(k.index)
        kk = k.reindex(m.index).ffill()
        kk[m.index > k.index[-1]] = np.nan   # 지수 데이터가 끝난 뒤는 '모름'(0% 아님)
        m["kospi_1d"] = kk.pct_change(fill_method=None) * 100
    except Exception as e:
        print(f"  [가상매매] 코스피 지수 실패 — 시장지표 없이 진행: {e}")
        m["kospi_1d"] = np.nan
    up = (closes.pct_change() > 0).sum(axis=1) / closes.notna().sum(axis=1) * 100
    m["breadth"] = up
    m["breadth_prev"] = up.shift(1)
    return m


def build(n_universe: int = 500, end: str | None = None) -> pd.DataFrame:
    from stock import calc_live_swing, KR_STOCKS
    end = end or pd.Timestamp.now().strftime("%Y-%m-%d")
    sect = {t.split(".")[0]: v[2] for t, v in KR_STOCKS.items()}
    codes = list(dict.fromkeys([t.split(".")[0] for t in KR_STOCKS] + load_universe(n_universe)))
    data = {}
    for j, code in enumerate(codes):
        df = load_ohlcv(code, end)
        if len(df) > 300: data[code] = df
        if j % 100 == 0: print(f"  [가상매매] 시세 {j}/{len(codes)}", flush=True)
    closes = pd.DataFrame({c: d.Close for c, d in data.items()})
    mk = market_table(closes, end)
    rows = []
    for code, df in data.items():
        f = features(df)
        sector = sect.get(code, "기타")
        idx = {t: i for i, t in enumerate(df.index)}
        ff = f[(f.index >= SIM_START)].dropna(subset=["rsi", "ret_3m", "pct_from_low", "bb_pct"])
        for t, r in ff.iterrows():
            sw = calc_live_swing({
                "rsi": r.rsi, "macd_cross": bool(r.macd_cross), "bb_pct": r.bb_pct, "pct_from_low": r.pct_from_low,
                "vol_ratio": r.vol_ratio, "change": r.change, "ret_1w": r.ret_1w, "ret_1m": r.ret_1m,
                "near_support": bool(r.near_support), "near_resistance": bool(r.near_resistance),
                "sector": sector, "manipulation_signal": bool(r.manip), "momentum_bad": bool(r.momentum_bad)})
            passed = sw["swing_signal"] or sw["momentum_signal"]
            if sw["sw_score"] < 50 and not passed:
                continue
            sim = simulate(df, idx[t])
            if sim is None: continue
            rec = {"date": t, "code": code, "sector": sector, "net_pct": sim[0], "held": sim[1], "exit": sim[2],
                   "sw_score": sw["sw_score"], "bot_pass": passed, "swing_signal": sw["swing_signal"],
                   "momentum_signal": sw["momentum_signal"]}
            for col in ["rsi", "bb_pct", "vol_ratio", "change", "ret_1w", "ret_1m", "ret_3m", "pct_from_low",
                        "pct_from_high", "dist_ma20", "atr_pct"]:
                rec[col] = float(r[col])
            rows.append(rec)
    X = pd.DataFrame(rows)
    X = X.merge(mk[["kospi_1d", "breadth", "breadth_prev"]], left_on="date", right_index=True, how="left")
    print(f"  [가상매매] 후보 {len(X):,}건 / 봇 매수조건 통과 {int(X.bot_pass.sum()):,}건")
    return X


if __name__ == "__main__":
    X = build(int(sys.argv[1]) if len(sys.argv) > 1 else 500)
    X.to_pickle(os.path.join(CACHE, "virtual_trades.pkl"))
