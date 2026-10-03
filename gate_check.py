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
  - 기준 자금 = 종목당 100만원 × 최대 10종목 = 1,000만원 (GATE_BASE_CAPITAL 로 바꿀 수 있음)
  - 코스피 수익률 = 같은 8주 동안 코스피 지수 등락률 (FinanceDataReader 'KS11')
  - 통과 = 매도 20건 이상 + 비용 뺀 손익 > 0 + 봇 수익률 > 코스피 수익률
"""
import os, json
from datetime import timedelta

BASE = os.path.dirname(os.path.abspath(__file__))
POSITIONS = os.path.join(BASE, "positions.json")
STATE = os.path.join(BASE, "gate_state.json")
WEEKS = 8
MIN_TRADES = 20
COST_RATE = 0.0023
BASE_CAPITAL = float(os.environ.get("GATE_BASE_CAPITAL", "10000000"))
LIVE = os.environ.get("PAPER_TRADING", "true").lower() == "false"


def _now():
    try:
        from finance import _now_kst  # 프로젝트 규칙: datetime.now() 직접 사용 금지
        return _now_kst()
    except Exception:
        from datetime import datetime, timezone
        return datetime.now(timezone(timedelta(hours=9)))


def _kospi_return(start: str, end: str):
    try:
        import FinanceDataReader as fdr
        df = fdr.DataReader("KS11", start, end)
        if len(df) < 2:
            return None
        return float(df["Close"].iloc[-1] / df["Close"].iloc[0] - 1)
    except Exception as e:
        print("코스피 조회 실패:", e)
        return None


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
    kospi = _kospi_return(start, end)
    if len(sells) < MIN_TRADES:
        verdict = "표본부족"
    elif kospi is None:
        verdict = "판정불가"
    else:
        verdict = "통과" if (net > 0 and bot > kospi) else "미달"
    return {"date": end, "from": start, "trades": len(sells), "profit": round(profit), "cost": round(cost), "net": round(net),
            "bot_pct": round(bot * 100, 2), "kospi_pct": None if kospi is None else round(kospi * 100, 2), "verdict": verdict}


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
            f"· 같은 기간 코스피: <b>{kp}</b>\n"
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
