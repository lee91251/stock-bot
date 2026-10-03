"""🚦 실계좌 문지기 (gate_check.py) — 2026-10-03 신설 (회장 지시: "멈춤 기준 + 다시 돌아가는 장치")

매주 토요일 자가학습 성적표 뒤에 자동 실행. 매매 규칙·주문은 전혀 건드리지 않는다 (판정·기록·텔레그램만).

기준 (회장 확정 10/3): 최근 8주 동안 봇이 코스피보다 못 벌면 실계좌로 가지 않는다 / 실계좌면 멈춤 권고.

단계 (gate_state.json 의 stage)
  ① 관찰   : 모의투자로 계속 매매하며 기록. 8주 기준 통과 → ② 후보 (텔레그램 알림)
  ② 후보   : "기준 통과, 회장 승인 필요" 상태. 실계좌 전환은 회장이 직접 정한다 (이 스크립트는 절대 안 바꿈).
             다음 주 판정에서 기준을 못 넘으면 → ① 관찰 로 돌아감
  ③ 실계좌 : PAPER_TRADING=false 로 돌고 있을 때. 8주 기준 미달이면 "멈춤 권고" 텔레그램 → 회장이 PAPER_TRADING 을 true 로
             되돌리면 ① 관찰 로 돌아가 다시 처음부터 (= 다시 돌아가는 장치)

판정 숫자
  - 봇 수익률 = 최근 8주 매도 실현손익 합 − 추정 거래비용(매도금액 × 0.23%: 거래세 0.20% + 수수료 왕복 약 0.03%) ÷ 기준 자금
  - 기준 자금 = 종목당 100만원 × 최대 10종목 = 1,000만원 (GATE_BASE_CAPITAL 로 바꿀 수 있음).
    실제 최대 동시 보유는 약 775만원(9/30)이라 봇 수익률이 조금 낮게 나옴 = 보수적(통과가 더 어려움)
  - 코스피 수익률 = 시작일 직전 종가 → 오늘까지 마지막 종가. yfinance '^KS11' 먼저, 안 되면 FinanceDataReader 'KS11'.
    ⚠️ 10/3 FDR KS11 이 9/17에서 멈춰 있던 사고 → 마지막 날짜가 오늘보다 5일 넘게 오래되면 '판정불가'
  - 통과 = 매도 20건 이상 + 비용 뺀 손익 > 0 + 봇 수익률 > 코스피 수익률
           + 1건당 순익 t값 ≥ 1.5 + 제일 많이 번 1건을 빼도 순익 > 0   (운으로 통과 방지, 10/3 카운슬)
  - 아직 안 보는 것(나중에): 들고 있는 종목의 평가손익, 코스닥·반반 지수 비교, 실계좌 첫 전환 시 금액 줄여 4주 재판정
"""
import os, json
from datetime import timedelta

BASE = os.path.dirname(os.path.abspath(__file__))
POSITIONS = os.path.join(BASE, "positions.json")
STATE = os.path.join(BASE, "gate_state.json")
WEEKS = 8
MIN_TRADES = 20
MIN_T = 1.5          # 1건당 순익 평균 ÷ 표준오차 — 운과 구별되는 최소선
STALE_DAYS = 5       # 코스피 데이터가 이보다 오래 멈춰 있으면 판정 안 함
COST_RATE = 0.0023
BASE_CAPITAL = float(os.environ.get("GATE_BASE_CAPITAL", "10000000"))


def _live_mode() -> bool:
    """실제 매매 설정은 daily.yml 의 PAPER_TRADING 값이 정한다.
    (selflearn.yml 은 안전을 위해 PAPER_TRADING="true" 로 고정돼 있어 환경변수로는 실계좌 여부를 알 수 없음 — 10/3 카운슬 지적)"""
    import re
    try:
        txt = open(os.path.join(BASE, ".github", "workflows", "daily.yml"), encoding="utf-8").read()
        m = re.search(r'PAPER_TRADING:\s*"?(\w+)"?', txt)
        return bool(m) and m.group(1).lower() == "false"
    except Exception:
        return False


LIVE = _live_mode()


def _now():
    try:
        from finance import _now_kst  # 프로젝트 규칙: datetime.now() 직접 사용 금지
        return _now_kst()
    except Exception:
        from datetime import datetime, timezone
        return datetime.now(timezone(timedelta(hours=9)))


def _close_series(start: str, end: str):
    """코스피 종가 (날짜 오름차순). yfinance 먼저, 실패하면 FDR."""
    import pandas as pd
    s0 = (pd.Timestamp(start) - pd.Timedelta(days=10)).strftime("%Y-%m-%d")
    e1 = (pd.Timestamp(end) + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
    try:
        import yfinance as yf
        df = yf.download("^KS11", start=s0, end=e1, progress=False, auto_adjust=False)
        c = df["Close"]
        c = c.iloc[:, 0] if hasattr(c, "columns") else c
        if len(c.dropna()) >= 2:
            return c.dropna(), "yfinance"
    except Exception as e:
        print("yfinance 코스피 실패:", e)
    try:
        import FinanceDataReader as fdr
        c = fdr.DataReader("KS11", s0, end)["Close"].dropna()
        if len(c) >= 2:
            return c, "FDR"
    except Exception as e:
        print("FDR 코스피 실패:", e)
    return None, ""


def _kospi_return(start: str, end: str):
    """(수익률, 메모). 시작일 직전 종가 → 오늘까지 마지막 종가. 데이터가 멈춰 있으면 (None, 이유)."""
    import pandas as pd
    c, src = _close_series(start, end)
    if c is None:
        return None, "코스피 조회 실패"
    c.index = pd.to_datetime(c.index).tz_localize(None)
    before = c[c.index <= pd.Timestamp(start)]
    upto = c[c.index <= pd.Timestamp(end)]
    if before.empty or upto.empty:
        return None, "코스피 기간 데이터 없음"
    last_day = upto.index[-1]
    if (pd.Timestamp(end) - last_day).days > STALE_DAYS:
        return None, f"코스피 데이터가 {last_day:%m/%d}에서 멈춤({src})"
    return float(upto.iloc[-1] / before.iloc[-1] - 1), f"{before.index[-1]:%m/%d}→{last_day:%m/%d} {src}"


def judge(today=None) -> dict:
    today = today or _now()
    start = (today - timedelta(weeks=WEEKS)).strftime("%Y-%m-%d")
    end = today.strftime("%Y-%m-%d")
    hist = json.load(open(POSITIONS, encoding="utf-8")).get("history", [])
    sells = [h for h in hist if h.get("side") == "sell" and start <= h.get("date", "") <= end]
    profit = sum(float(h.get("profit", 0)) for h in sells)
    cost = sum(float(h.get("amount", 0)) for h in sells) * COST_RATE
    net = profit - cost
    bot = net / BASE_CAPITAL
    kospi, kmemo = _kospi_return(start, end)
    per = [float(h.get("profit", 0)) - float(h.get("amount", 0)) * COST_RATE for h in sells]
    n = len(per)
    mean = sum(per) / n if n else 0.0
    sd = (sum((x - mean) ** 2 for x in per) / (n - 1)) ** 0.5 if n > 1 else 0.0
    tval = mean / (sd / n ** 0.5) if sd > 0 else 0.0
    net_wo_best = net - max(per) if per else 0.0
    if n < MIN_TRADES:
        verdict = "표본부족"
    elif kospi is None:
        verdict = "판정불가"
    else:
        verdict = "통과" if (net > 0 and bot > kospi and tval >= MIN_T and net_wo_best > 0) else "미달"
    return {"date": end, "from": start, "trades": n, "profit": round(profit), "cost": round(cost), "net": round(net),
            "bot_pct": round(bot * 100, 2), "kospi_pct": None if kospi is None else round(kospi * 100, 2), "kospi_memo": kmemo,
            "t": round(tval, 2), "net_wo_best": round(net_wo_best), "verdict": verdict}


def step(state: dict, r: dict) -> tuple:
    """단계 이동. (새 단계, 알릴 말, 소리 알림 여부)"""
    stage = state.get("stage", "관찰")
    if LIVE and stage != "실계좌":
        stage = "실계좌"                                   # 회장이 실계좌로 바꿔 놓은 상태
    if not LIVE and stage == "실계좌":
        return "관찰", "모의투자로 돌아왔습니다 → ① 관찰부터 다시 시작", True   # 다시 돌아가는 장치
    if stage == "관찰":
        if r["verdict"] == "통과":
            return "후보", "8주 기준 통과! 실계좌 전환을 검토할 수 있습니다 (회장님 승인 필요, 자동 전환 안 함)", True
        return "관찰", "", False
    if stage == "후보":
        if r["verdict"] == "통과":
            return "후보", "기준 통과 유지 — 회장님 승인 대기 중", False
        return "관찰", "기준 미달로 후보에서 내려옴 → ① 관찰", True
    if stage == "실계좌":
        if r["verdict"] == "미달":
            return "실계좌", "⛔ 멈춤 권고: 실계좌가 8주 동안 코스피보다 못 벌었습니다. PAPER_TRADING 을 true 로 되돌리세요", True
        return "실계좌", "", False
    return stage, "", False


def main(send: bool = True) -> dict:
    state = json.load(open(STATE, encoding="utf-8")) if os.path.exists(STATE) else {"stage": "관찰", "history": []}
    r = judge()
    new, msg, loud = step(state, r)
    changed = new != state.get("stage", "관찰")
    state["stage"] = new
    if changed:
        state["since"] = r["date"]
    state.setdefault("history", []).append({**r, "stage": new})
    state["history"] = state["history"][-60:]
    json.dump(state, open(STATE, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    kp = "?" if r["kospi_pct"] is None else f"{r['kospi_pct']:+.2f}%"
    icon = {"관찰": "①", "후보": "②", "실계좌": "③"}[new]
    text = (f"🚦 <b>실계좌 문지기</b> · {r['date']}\n"
            f"{icon} 지금 단계: <b>{new}</b>\n\n"
            f"· 최근 8주 봇: <b>{r['bot_pct']:+.2f}%</b> (매도 {r['trades']}건, 비용 뺀 손익 {r['net']:+,}원)\n"
            f"· 같은 기간 코스피: <b>{kp}</b> ({r['kospi_memo']})\n"
            f"· 운 점검: t값 {r['t']} (1.5 이상), 제일 큰 1건 빼면 {r['net_wo_best']:+,}원\n"
            f"· 판정: <b>{r['verdict']}</b>")
    if msg:
        text += f"\n\n👉 {msg}"
    print(text.replace("<b>", "").replace("</b>", ""))
    if send:
        try:
            from notify import tg_send
            tg_send(text, silent=not loud)
        except Exception as e:
            print("텔레그램 실패:", e)
    return state


if __name__ == "__main__":
    import sys
    main(send="--no-send" not in sys.argv)
