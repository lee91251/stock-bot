# -*- coding: utf-8 -*-
"""자가학습 ②⑤ 주간 채점 + 텔레그램 성적표 (10/1 신설).

매주 토요일 자동 실행 (.github/workflows/selflearn.yml):
  1) candidate_log/*.json (봇이 검토한 전 종목 기록)을 읽어
  2) 기록 시점 가격에 샀다면 봇 매도 규칙으로 어떻게 됐을지 실제 시세(FinanceDataReader)로 채점
  3) 챔피언(현 규칙: 실제로 산 종목) vs 도전자들(challengers.json, 그림자) vs 전체 후보 비교
  3-1) 8년 가상매매로 새 도전자 자동 발굴 (selflearn_discover) — 3구간 검증 통과 시 그림자 등록
  4) selflearn_report.json 저장 + 텔레그램 주간 성적표 (무음)
매매에는 일절 관여하지 않음. 도전자 승격은 회장 승인 사항 (자동 승격 없음).

채점 규칙 (SWING_RULES.md 와 동일, 일봉 근사):
  진입=기록 시점 가격(장중) / 손절 -4%·익절 +6% 절반·+10% 전량은 장중 도달 즉시 /
  3일+ -1% 미만 빨리청산·5일 청산(+3% 이상이면 10일까지)은 종가 판단 → 다음날 시가. 매수일=0일.
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
                rows.append({"date": day, "code": code, "_bp": d.get("breadth_prev"), **r})
    return rows


def _actual_buys() -> dict:
    try:
        h = json.load(open(POSITIONS, encoding="utf-8")).get("history", [])
    except Exception:
        h = []
    return {(x["date"], x["code"]): x.get("price") for x in h if x.get("side") == "buy"}


def _simulate(ohlc: pd.DataFrame, day: str, entry: float, stop: float = .04, t1: float = .06, t2: float = .10):
    """day 장중 entry 매수 → 봇 매도 규칙. 반환 (net%, 결과) / 진행중이면 (None,'진행중').

    보유일: 매수일=0일 (봇 _trading_days_between 과 동일, 매수일 제외).
    손절·익절: 봇이 장중 15분마다 점검하므로 장중 저가/고가가 닿으면 그 가격에 체결(갭이면 시가).
    매수 당일은 매수 이후 저가/고가를 알 수 없어 종가로만 판단. 빨리청산·기간청산은 종가 판단 → 다음날 시가.
    """
    d = ohlc[ohlc.index >= pd.Timestamp(day)]
    if d.empty:
        return None, "시세없음"
    bp = entry * (1 + SLIP)
    O, H, L, C = d.Open.values, d.High.values, d.Low.values, d.Close.values
    stop, t1, t2 = bp * (1 - stop), bp * (1 + t1), bp * (1 + t2)   # 기본 = 봇 현재 규칙(-4/+6/+10)
    parts, half = [], False

    def fin(price):
        sp = price * (1 - SLIP) * (1 - COMM - TAX)
        parts.append((1 - sum(w for w, _ in parts), sp))
        return sum(w * (p / (bp * (1 + COMM)) - 1) for w, p in parts) * 100, "완료"

    for k in range(len(C)):
        held = k
        lo, hi = (C[k], C[k]) if k == 0 else (L[k], H[k])
        op = C[k] if k == 0 else O[k]
        if lo <= stop:                       # 손절 우선 (보수적)
            return fin(min(op, stop))
        if hi >= t2:
            return fin(max(op, t2))
        if hi >= t1 and not half:
            parts.append((0.5, max(op, t1) * (1 - SLIP) * (1 - COMM - TAX))); half = True
        pct = (C[k] / bp - 1) * 100
        if (held >= 3 and pct < -1) or (held >= 5 and not (held < 10 and pct >= 3)):
            if k + 1 >= len(C):
                return None, "진행중"
            return fin(O[k + 1])
    return None, "진행중"


def _realtime_compare(prices: dict) -> dict:
    """실시간 감시(realtime_log) vs 15분 봇: 포착 시점 가격·이후 결과, 손절선 도달 vs 실제 매도 지연."""
    out = {"alerts": 0, "stop_events": 0}
    fns = sorted(glob.glob(os.path.join(BASE, "realtime_log", "*.json")))
    if not fns:
        return out
    try:
        hist = json.load(open(POSITIONS, encoding="utf-8")).get("history", [])
    except Exception:
        hist = []
    def hm2min(t):
        t = str(t).replace(":", "")
        return int(t[:2]) * 60 + int(t[2:4]) if len(t) >= 4 and t[:4].isdigit() else None
    res, pend, both, later, pdiff = [], 0, 0, [], []
    s_delay, s_pdiff, s_n, s_ev = [], [], 0, 0
    try:
        import FinanceDataReader as fdr
    except Exception:
        fdr = None
    for fn in fns:
        day = os.path.basename(fn)[:10]
        try:
            d = json.load(open(fn, encoding="utf-8"))
        except Exception:
            continue
        for a in d.get("alerts", []):
            out["alerts"] += 1
            ohlc = prices.get(a["code"])
            if ohlc is None and fdr is not None:
                try:
                    ohlc = fdr.DataReader(a["code"], day); prices[a["code"]] = ohlc
                except Exception:
                    ohlc = None
            if ohlc is not None:
                net, st = _simulate(ohlc, day, a["price"])
                if st == "완료": res.append(net)
                elif st == "진행중": pend += 1
            b = next((h for h in hist if h.get("side") == "buy" and h.get("date") == day and h.get("code") == a["code"]), None)
            if b:
                both += 1
                ta, tb = hm2min(a["t"]), hm2min(b.get("time", ""))
                if ta is not None and tb is not None: later.append(tb - ta)
                if b.get("price"): pdiff.append((b["price"] / a["price"] - 1) * 100)
        for e in d.get("holding_events", []):
            if e.get("type") != "손절선":
                continue
            s_ev += 1
            sell = next((h for h in hist if h.get("side") == "sell" and h.get("code") == e["code"] and h.get("date", "") >= day), None)
            if sell:
                s_n += 1
                if sell.get("date") == day:
                    te, ts = hm2min(e["t"]), hm2min(sell.get("time", ""))
                    if te is not None and ts is not None: s_delay.append(ts - te)
                if sell.get("price"): s_pdiff.append((sell["price"] / e["price"] - 1) * 100)
    import statistics as _st
    out.update({"alert_result": _summ(res), "alert_pending": pend, "both": both,
                "bot_later_min": round(_st.mean(later), 1) if later else "-",
                "bot_price_diff": round(_st.mean(pdiff), 2) if pdiff else 0.0,
                "stop_events": s_ev, "stop_matched": s_n,
                "stop_delay_min": round(_st.mean(s_delay), 1) if s_delay else "-",
                "stop_price_diff": round(_st.mean(s_pdiff), 2) if s_pdiff else 0.0})
    return out


def _exit_params(c: dict, r: dict) -> dict:
    """매도 방식 도전자: {"stop": {"atr_k":2,"lo":.03,"hi":.08}, ...} → 종목 변동성(ATR%)에 비례한 폭."""
    atr_pct = (r.get("atr") / r["price"] * 100) if r.get("atr") and r.get("price") else 3.0
    out = {}
    for k in ("stop", "t1", "t2"):
        spec = (c.get("exit") or {}).get(k)
        if isinstance(spec, dict):
            out[k] = min(spec["hi"], max(spec["lo"], spec["atr_k"] * atr_pct / 100))
        elif isinstance(spec, (int, float)):
            out[k] = float(spec)
    return out


def _size_weight(c: dict, r: dict) -> float:
    """매수량 도전자: 조건별 비중 (위에서부터 첫 번째로 맞는 조건). 지표 없으면 그 조건 무시."""
    try:
        from learning import _live_feature
    except Exception:
        return 1.0
    mkt = r.get("passed_mkt") or r.get("mkt") or {}
    for rule in c.get("size", []):
        ok = True
        for q in rule.get("when", []):
            x = _live_feature(q["f"], r, mkt, r.get("_bp"))
            if x is None or not ((x < q["v"]) if q["op"] == "<" else (x > q["v"])):
                ok = False
                break
        if ok:
            return float(rule["w"])
    return float(c.get("default", 1.0))


def _readiness(rt: dict, verdicts: dict) -> dict:
    """5~7단계와 도전자 판정에 필요한 데이터가 얼마나 쌓였나."""
    rdays = len(glob.glob(os.path.join(BASE, "realtime_log", "*.json")))
    cdays = len(glob.glob(os.path.join(LOG_DIR, "*.json")))
    items = [
        {"name": "7단계 실시간 포착→봇 매수 연결 판단", "need": 10, "have": rdays, "unit": "거래일 실시간 기록",
         "todo": "실시간 포착 vs 15분 봇 가격 비교 → 봇 매수 연결 여부 결정"},
        {"name": "6단계 손절·익절 폭 (8년 검증 완료 → E1 실시간 확인)", "need": 15, "have": rdays, "unit": "거래일 실시간 기록",
         "todo": "E1(변동성 비례 손절·익절) 실시간 성적 + 장중 손절 지연 데이터로 최종 판단"},
        {"name": "5단계 사는 양 (8년 검증 완료 → S1 실시간 확인)", "need": 20, "have": cdays, "unit": "거래일 후보 기록",
         "todo": "S1(좋은 날 1.5배·나쁜 날 쉬기) 실시간 성적으로 최종 판단"},
    ]
    out = {"items": [], "todo": []}
    for it in items:
        ok = it["have"] >= it["need"]
        out["items"].append({"name": it["name"], "ok": ok, "progress": f"{min(it['have'], it['need'])}/{it['need']} {it['unit']}"})
        if ok:
            out["todo"].append(it["todo"])
    decided = [k for k, v in (verdicts or {}).items() if not str(v).startswith("판정 보류")]
    out["items"].append({"name": "도전자 승부 판정", "ok": bool(decided),
                         "progress": ("판정 나옴: " + ", ".join(f"{k} {verdicts[k]}" for k in decided)) if decided else "표본 쌓는 중 (약 3개월)"})
    if decided:
        out["todo"].append("도전자 판정 결과 검토 → 실제 규칙 반영 여부 승인")
    return out


def _summ(vals: list) -> dict:
    if not vals:
        return {"n": 0}
    s = pd.Series(vals)
    return {"n": int(len(s)), "win": round(float((s > 0).mean() * 100), 1),
            "avg": round(float(s.mean()), 3), "big_loss": round(float((s <= -5).mean() * 100), 1)}


def run(send: bool = True, discover: bool = True, research: bool = True) -> dict:
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
    try:
        reg = json.load(open(os.path.join(BASE, "challengers.json"), encoding="utf-8"))
        allc = [c for c in reg.get("challengers", []) if c.get("status") == "shadow"]
    except Exception:
        reg, allc = {"challengers": [], "history": []}, [{"id": "C1", "name": "시장 나쁜 날 쉬기", "rules": []}]
    chs = [c for c in allc if c.get("type", "rule") == "rule"]      # 매수 여부 도전자
    ex_chs = [c for c in allc if c.get("type") == "exit"]           # 매도 방식 도전자 (6단계)
    sz_chs = [c for c in allc if c.get("type") == "size"]           # 매수량 도전자 (5단계)
    groups = {"champion": [], "passed": [], "missed": [], "all": []}
    for c in chs + ex_chs + sz_chs:
        groups[c["id"]] = []
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
        if r.get("passed"):
            tags.append("passed")
            verdicts = r.get("ch") or ({"C1": r["C1"]} if r.get("C1") else {})
            for c in chs:
                if verdicts.get(c["id"]) == "buy":   # 판정이 없는 옛 기록은 그 도전자 집계에서 제외
                    tags.append(c["id"])
        if actual_price: tags.append("champion")
        elif r.get("passed"): tags.append("missed")    # 1차 후보였는데 실제로 안 산 종목 (놓친 매수)
        for t in tags:
            if t not in groups: continue
            if status == "완료": groups[t].append(net)
            elif status == "진행중": pending[t] += 1
        if r.get("passed"):
            for c in ex_chs:   # 같은 후보를 다른 매도 규칙으로
                net2, st2 = _simulate(ohlc, r["date"], entry, **_exit_params(c, r))
                if st2 == "완료": groups[c["id"]].append(net2)
                elif st2 == "진행중": pending[c["id"]] += 1
            for c in sz_chs:   # 같은 후보를 다른 매수량으로 (후보 1건당 기대 손익 = 수익률 × 비중)
                if status == "완료": groups[c["id"]].append(net * _size_weight(c, r))
                elif status == "진행중": pending[c["id"]] += 1
    res = {k: _summ(v) for k, v in groups.items()}
    cp = res["champion"]
    verdicts = {}
    for c in chs:
        ch = res[c["id"]]
        if ch.get("n", 0) >= MIN_SAMPLES_FOR_VERDICT and cp.get("n", 0) >= 30:
            better = ch["avg"] - cp["avg"] >= 0.25 and ch["big_loss"] <= cp["big_loss"]
            verdicts[c["id"]] = "도전자 우세 — 회장 승인 검토 가능" if better else "챔피언 유지"
        else:
            verdicts[c["id"]] = f"판정 보류 (표본: 도전자 {ch.get('n', 0)} / 챔피언 {cp.get('n', 0)})"
    pb = res["passed"]
    for c in ex_chs + sz_chs:   # 매도·매수량 도전자는 '같은 1차 후보 + 현재 규칙'과 비교
        ch = res[c["id"]]
        if ch.get("n", 0) >= MIN_SAMPLES_FOR_VERDICT:
            better = ch["avg"] - pb["avg"] >= 0.25
            verdicts[c["id"]] = "도전자 우세 — 회장 승인 검토 가능" if better else "현재 규칙 유지"
        else:
            verdicts[c["id"]] = f"판정 보류 (표본 {ch.get('n', 0)}/{MIN_SAMPLES_FOR_VERDICT})"

    # ③ 주간 자동 발굴 (8년 가상매매 → 새 도전자 후보) — 실패해도 성적표는 발송
    disc = {}   # 발굴은 성적표 발송 뒤에 따로 (발굴이 시간초과돼도 성적표는 나가게)
    try:
        reg = json.load(open(os.path.join(BASE, "challengers.json"), encoding="utf-8"))
    except Exception:
        pass
    rep = {
        "updated": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "period": [start, max(r["date"] for r in rows)],
        "days": len({r["date"] for r in rows}),
        "records": len(rows),
        "groups": res, "pending": pending, "verdicts": verdicts,
        "challengers": [{"id": c["id"], "name": c.get("name"), "rules": c.get("rules"), "status": c.get("status"),
                         "source": c.get("source")} for c in reg.get("challengers", [])],
        "discovery": disc,
    }
    json.dump(rep, open(REPORT, "w", encoding="utf-8"), ensure_ascii=False, indent=1, default=float)

    def line(name, g, p):
        if not g.get("n"):
            return f"• {name}: 완료 0건 (진행중 {p})"
        return f"• {name}: {g['n']}건 승률 {g['win']}% 평균 {g['avg']:+.2f}% 큰손실 {g['big_loss']}% (진행중 {p})"
    try:
        from selflearn_discover import rule_text as _rt
    except Exception:
        _rt = lambda r: str(r)
    from html import escape as _esc   # 10/10: 규칙의 '<'를 텔레그램이 태그로 오해해 C2·E1·S1·후보 줄이 통째로 사라짐
    rule_text = lambda r: _esc(_rt(r))
    lines = [f"🧠 <b>자가학습 주간 성적표</b>",
             f"기록 {rep['period'][0]}~{rep['period'][1]} ({rep['days']}거래일, {rep['records']}건)",
             line("챔피언(실제 매수)", cp, pending["champion"])]
    for c in chs:
        lines.append(line(f"도전자 {c['id']}", res[c["id"]], pending[c["id"]]))
        lines.append(f"   └ {rule_text(c.get('rules', []))}이면 쉼 → {_esc(verdicts[c['id']])}")
    for c in ex_chs + sz_chs:
        lines.append(line(f"도전자 {c['id']}", res[c["id"]], pending[c["id"]]))
        lines.append(f"   └ {_esc(c.get('desc', c.get('name', '')))} → {_esc(verdicts[c['id']])} (비교 기준: 1차 후보 전체)")
    lines += [line("1차 후보 전체", res["passed"], pending["passed"]),
              line("놓친 후보(후보였는데 안 삼)", res["missed"], pending["missed"]),
              line("검토 종목 전체", res["all"], pending["all"])]
    rt = _realtime_compare(prices)
    rep["realtime"] = rt
    json.dump(rep, open(REPORT, "w", encoding="utf-8"), ensure_ascii=False, indent=1, default=float)
    if rt.get("alerts"):
        lines.append(f"⚡ <b>실시간 포착</b> {rt['alerts']}건: " + line("결과", rt["alert_result"], rt["alert_pending"])[2:]
                     + f" / 같은 날 봇도 산 종목 {rt['both']}건 (봇이 평균 {rt['bot_later_min']}분 늦게, 가격 {rt['bot_price_diff']:+.2f}%)")
    if rt.get("stop_events"):
        lines.append(f"⏱ <b>손절 지연</b>: 손절선 닿고 봇이 판 {rt['stop_matched']}건 평균 {rt['stop_delay_min']}분 늦음, "
                     f"가격 {rt['stop_price_diff']:+.2f}% 차이")
    # 🔔 다음 단계 준비 상태 (회장이 잊지 않게 — 준비되면 "Claude와 이어하기" 알림)
    ready = _readiness(rt, verdicts)
    rep["readiness"] = ready
    json.dump(rep, open(REPORT, "w", encoding="utf-8"), ensure_ascii=False, indent=1, default=float)
    lines.append("📋 <b>다음 단계 준비 상태</b>")
    for it in ready["items"]:
        lines.append(f"{'✅' if it['ok'] else '⏳'} {_esc(it['name'])}: {_esc(it['progress'])}")
    if ready["todo"]:
        lines.append("🔔 <b>회장님 할 일:</b> Claude 대화창을 열고 <b>\"자가학습 다음 단계 이어하자\"</b>라고 말씀해 주세요")
        for t in ready["todo"]:
            lines.append(f"   · {t}")
    lines.append("<i>그림자 운영 = 실매매 무변경. 실제 반영은 회장 승인.</i>")
    msg = chr(10).join(lines)
    print(msg)
    if send:
        try:
            from notify import tg_send
            tg_send(msg, silent=True)
        except Exception as e:
            print(f"  [주간학습] 텔레그램 실패: {e}")
    # ③ 자동 발굴 (8년 가상매매) — 성적표 발송 후 실행, 결과는 별도 메시지
    if discover:
        X = None
        try:
            import selflearn_discover as sd, selflearn_virtual as sv
            X = sv.build()
            disc = sd.run(live_scores=res, X=X)
        except Exception as e:
            disc = {"action": f"발굴 실패: {e}"}
            print(f"  [주간학습] 발굴 오류: {e}")
        rep["discovery"] = disc
        json.dump(rep, open(REPORT, "w", encoding="utf-8"), ensure_ascii=False, indent=1, default=float)
        dl = [f"🔎 <b>이번 주 자동 발굴</b>: {_esc(str(disc.get('action', '-')))}",
              f"검사한 규칙 조합 {disc.get('checked', 0)}개 (학습~2022 → 검증 2023~24 → 최종확인 2025~ 모두 통과해야 등록)"]
        for t in (disc.get("top") or [])[:3]:
            dl.append(f"· {_esc(t['규칙'])} → 학습 {t['학습']:+.2f} / 검증 {t['검증']:+.2f} / 최종 {t['최종확인']:+.2f}%p {'✅' if t['통과'] else '❌'}")
        dl.append("<i>등록돼도 그림자 운영만. 실제 반영은 회장 승인.</i>")
        print(chr(10).join(dl))
        if send:
            try:
                from notify import tg_send
                tg_send(chr(10).join(dl), silent=True)
            except Exception as e:
                print(f"  [주간학습] 텔레그램 실패: {e}")
        # ④ AI 연구원 — Claude 가설 → 3구간 검증 → 통과 시 그림자 등록 (회장 승인 10/1)
        if research and X is not None:
            try:
                import selflearn_research as sr
                rres = sr.run(X=X, live_scores=res)
                rep["research"] = rres
                json.dump(rep, open(REPORT, "w", encoding="utf-8"), ensure_ascii=False, indent=1, default=float)
                rmsg = sr.message(rres)
                print(rmsg)
                if send and not str(rres.get("action", "")).startswith("AI 연구원 건너뜀"):   # 키·크레딧 없으면 조용히
                    from notify import tg_send
                    tg_send(rmsg, silent=True)
            except Exception as e:
                print(f"  [주간학습] AI 연구원 오류: {e}")
    return rep


if __name__ == "__main__":
    import sys
    run(send="--no-send" not in sys.argv, discover="--no-discover" not in sys.argv, research="--no-research" not in sys.argv)
