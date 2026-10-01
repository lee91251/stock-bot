# -*- coding: utf-8 -*-
"""자가학습 ②⑤ 주간 채점 + 텔레그램 성적표 (10/1 신설).

매주 토요일 자동 실행 (.github/workflows/selflearn.yml):
  1) candidate_log/*.json (봇이 검토한 전 종목 기록)을 읽어
  2) 기록 시점 가격에 샀다면 봇 매도 규칙으로 어떻게 됐을지 실제 시세(FinanceDataReader)로 채점
  3) 챔피언(현 규칙: 실제로 산 종목) vs 도전자 C1(그림자: 1차 후보 중 C1이 'buy') vs 전체 후보 비교
  4) selflearn_report.json 저장 + 텔레그램 주간 성적표 (무음)
매매에는 일절 관여하지 않음. 도전자 승격은 회장 승인 사항 (자동 승격 없음).

채점 규칙 (SWING_RULES.md 와 동일, 일봉 근사):
  진입=기록 시점 가격(장중) / 판단=매일 종가 / 손절 -4% / +6% 절반 / +10% 전량 /
  3일+ -1% 미만 빨리청산 / 5일 청산(+3% 이상이면 10일까지) / 체결=판단 다음날 시가.
  비용: 수수료 0.015%×2 + 세금 0.18% + 슬리피지 0.1%×2. 아직 결과가 안 난 건은 '진행중'으로 분리.
"""
import os, json, glob
from datetime import datetime, timedelta

import pandas as pd

BASE = os.path.dirname(os.path.abspath(__file__))
LOG_DIR = os.path.join(BASE, "candidate_log")
REPORT = os.path.join(BASE, "selflearn_report.json")
POSITIONS = os.path.join(BASE, "positions.json")
COMM, TAX, SLIP = 0.00015, 0.0018, 0.001
MIN_SAMPLES_FOR_VERDICT = 100      # 그룹당 이 건수 미만이면 "판정 보류"


def _load_logs() -> list:
    rows = []
    for fn in sorted(glob.glob(os.path.join(LOG_DIR, "*.json"))):
        day = os.path.basename(fn)[:10]
        try:
            d = json.load(open(fn, encoding="utf-8"))
        except Exception as e:
            print(f"  [주간학습] {fn} 읽기 실패: {e}")
            continue
        for code, r in d.get("stocks", {}).items():
            if r.get("price"):
                rows.append({"date": day, "code": code, **r})
    return rows


def _actual_buys() -> dict:
    try:
        h = json.load(open(POSITIONS, encoding="utf-8")).get("history", [])
    except Exception:
        h = []
    return {(x["date"], x["code"]): x.get("price") for x in h if x.get("side") == "buy"}


def _simulate(ohlc: pd.DataFrame, day: str, entry: float):
    """day 장중 entry 매수 → 봇 매도 규칙. 반환 (net%, 결과) / 진행중이면 (None,'진행중')."""
    d = ohlc[ohlc.index >= pd.Timestamp(day)]
    if d.empty:
        return None, "시세없음"
    bp = entry * (1 + SLIP)
    O, C = d.Open.values, d.Close.values
    parts, half = [], False
    for k in range(len(C)):
        held = k + 1
        pct = (C[k] / bp - 1) * 100
        act = None
        if pct <= -4: act = "all"
        elif pct >= 10: act = "all"
        elif pct >= 6 and not half: act = "half"
        elif held >= 3 and pct < -1: act = "all"
        elif held >= 5 and not (held < 10 and pct >= 3): act = "all"
        if not act:
            continue
        if k + 1 >= len(C):
            return None, "진행중"
        sp = O[k + 1] * (1 - SLIP) * (1 - COMM - TAX)
        if act == "half":
            parts.append((0.5, sp)); half = True; continue
        parts.append((1 - sum(w for w, _ in parts), sp))
        return sum(w * (p / (bp * (1 + COMM)) - 1) for w, p in parts) * 100, "완료"
    return None, "진행중"


def _summ(vals: list) -> dict:
    if not vals:
        return {"n": 0}
    s = pd.Series(vals)
    return {"n": int(len(s)), "win": round(float((s > 0).mean() * 100), 1),
            "avg": round(float(s.mean()), 3), "big_loss": round(float((s <= -5).mean() * 100), 1)}


def run(send: bool = True) -> dict:
    import FinanceDataReader as fdr
    rows = _load_logs()
    buys = _actual_buys()
    if not rows:
        print("[주간학습] 후보 기록 없음 — 종료")
        return {}
    start = min(r["date"] for r in rows)
    prices = {}
    for code in sorted({r["code"] for r in rows}):
        try:
            df = fdr.DataReader(code, start)
            if df is not None and not df.empty:
                prices[code] = df
        except Exception as e:
            print(f"  [주간학습] {code} 시세 실패: {e}")
    groups = {"champion": [], "challenger": [], "passed": [], "all": []}
    pending = {k: 0 for k in groups}
    for r in rows:
        key = (r["date"], r["code"])
        ohlc = prices.get(r["code"])
        if ohlc is None:
            continue
        actual_price = buys.get(key)
        entry = actual_price or r["price"]
        net, status = _simulate(ohlc, r["date"], entry)
        tags = ["all"]
        if r.get("passed"): tags.append("passed")
        if r.get("passed") and r.get("C1") == "buy": tags.append("challenger")
        if actual_price: tags.append("champion")
        for t in tags:
            if status == "완료": groups[t].append(net)
            elif status == "진행중": pending[t] += 1
    res = {k: _summ(v) for k, v in groups.items()}
    rep = {
        "updated": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "period": [start, max(r["date"] for r in rows)],
        "days": len({r["date"] for r in rows}),
        "records": len(rows),
        "groups": res, "pending": pending,
        "challenger": "C1 (코스피 하락일·전일 상승비율 39% 미만 쉬기 + 볼린저 63.6 미만 제외)",
    }
    ch, cp = res["challenger"], res["champion"]
    if ch.get("n", 0) >= MIN_SAMPLES_FOR_VERDICT and cp.get("n", 0) >= 30:
        better = ch["avg"] - cp["avg"] >= 0.25 and ch["big_loss"] <= cp["big_loss"]
        rep["verdict"] = "도전자 우세 — 회장 승인 검토 가능" if better else "챔피언 유지"
    else:
        rep["verdict"] = f"판정 보류 (표본 부족: 도전자 {ch.get('n', 0)}건 / 챔피언 {cp.get('n', 0)}건)"
    json.dump(rep, open(REPORT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    def line(name, g, p):
        if not g.get("n"):
            return f"• {name}: 완료 0건 (진행중 {p})"
        return f"• {name}: {g['n']}건 승률 {g['win']}% 평균 {g['avg']:+.2f}% 큰손실 {g['big_loss']}% (진행중 {p})"
    msg = (f"🧠 <b>자가학습 주간 성적표</b>\n"
           f"기록 {rep['period'][0]}~{rep['period'][1]} ({rep['days']}거래일, {rep['records']}건)\n"
           + line("챔피언(실제 매수)", cp, pending["champion"]) + "\n"
           + line("도전자 C1(그림자)", ch, pending["challenger"]) + "\n"
           + line("1차 후보 전체", res["passed"], pending["passed"]) + "\n"
           + line("검토 종목 전체", res["all"], pending["all"]) + "\n"
           f"<b>판정:</b> {rep['verdict']}\n"
           f"<i>그림자 운영 = 실매매 무변경. 승격은 회장 승인.</i>")
    print(msg)
    if send:
        try:
            from notify import tg_send
            tg_send(msg, silent=True)
        except Exception as e:
            print(f"  [주간학습] 텔레그램 실패: {e}")
    return rep


if __name__ == "__main__":
    import sys
    run(send="--no-send" not in sys.argv)
