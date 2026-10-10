# -*- coding: utf-8 -*-
"""자가학습 ④ AI 연구원 (10/1, 회장 승인) — 주 1회 Claude 가 '왜 졌나' 가설을 세우고, 봇이 데이터로 검증.

흐름: ① 8년 가상매매에서 이긴 매매 vs 크게 진 매매의 지표 분포 + 실제 봇 손절 기록 + 도전자 성적을 요약
      ② Claude(claude-opus-5-5)에 "사지 마라 규칙" 가설 최대 5개를 정해진 형식(JSON)으로 요청
      ③ 각 가설을 selflearn_discover 의 3구간 검증(학습~2022/검증 2023~24/최종확인 2025~ 모두 +0.10%p↑)에 통과시켜
         통과한 것만 그림자 도전자로 등록 (Claude 말을 그대로 믿지 않음 — 데이터가 최종 판단)
비용: 주 1회 호출 1건 (입력 수천 토큰 + 출력 수천 토큰). 키 없거나 실패하면 조용히 건너뜀.
"""
import os, json
import numpy as np, pandas as pd

BASE = os.path.dirname(os.path.abspath(__file__))
MODEL = "claude-opus-5-5"


def _feature_profile(B: pd.DataFrame, feats: list) -> dict:
    """크게 진 매매(-3% 이하) vs 이긴 매매의 지표 분위수 비교."""
    lose, win = B[B.net_pct <= -3], B[B.net_pct > 0]
    prof = {}
    for f in feats:
        if f not in B or B[f].notna().sum() < 100:
            continue
        q = lambda d: [round(float(x), 2) for x in np.nanquantile(d[f], [.25, .5, .75])] if d[f].notna().sum() >= 30 else None
        prof[f] = {"크게진매매_25/50/75%": q(lose), "이긴매매_25/50/75%": q(win), "전체_10/90%":
                   [round(float(x), 2) for x in np.nanquantile(B[f], [.1, .9])]}
    return prof


def _real_losses(n: int = 25) -> list:
    try:
        h = json.load(open(os.path.join(BASE, "positions.json"), encoding="utf-8")).get("history", [])
    except Exception:
        return []
    s = [x for x in h if x.get("side") == "sell"]
    return [{"날짜": x.get("date"), "종목": x.get("name"), "수익률%": x.get("pct"), "사유": str(x.get("reason", ""))[:30],
             "섹터": x.get("sector")} for x in s[-n:]]


def ask_claude(payload: dict, feats: list) -> dict:
    import anthropic
    if not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
        raise RuntimeError("ANTHROPIC_API_KEY 없음")
    schema = {
        "type": "object",
        "properties": {
            "note": {"type": "string", "description": "회장(비개발자)에게 보낼 한국어 연구 노트, 5줄 이내"},
            "hypotheses": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "rules": {"type": "array", "items": {
                            "type": "object",
                            "properties": {"f": {"type": "string", "enum": feats},
                                           "op": {"type": "string", "enum": ["<", ">"]},
                                           "v": {"type": "number"}},
                            "required": ["f", "op", "v"], "additionalProperties": False}},
                        "why": {"type": "string", "description": "한국어 한 줄 근거"}},
                    "required": ["rules", "why"], "additionalProperties": False}}},
        "required": ["note", "hypotheses"], "additionalProperties": False}
    system = ("당신은 한국 주식 스윙 자동매매봇의 퀀트 연구원입니다. 봇은 매수 후보 중 '이 조건이면 사지 마라/오늘은 쉬어라' "
              "규칙(제외 규칙)만 학습합니다. 규칙은 주어진 지표 이름만 쓰고, 한 가설당 규칙 1~2개(둘 중 하나라도 걸리면 제외), "
              "남는 매매가 절반 이상이 되도록 극단값 위주로 제안하세요. 이미 운영 중인 도전자와 같은 규칙은 피하고, "
              "데이터 분포에서 근거를 찾되 과최적화를 경계하세요. 가설은 최대 5개.")
    user = ("아래는 8년 가상매매(실제 매수 공식 재현) 요약과 실제 봇 기록입니다. 제외 규칙 가설과 연구 노트를 작성하세요.\n\n"
            + json.dumps(payload, ensure_ascii=False, default=float))
    client = anthropic.Anthropic(timeout=300, max_retries=1)   # 최악 10분 (워크플로우 60분 한도 보호)
    kw = dict(model=MODEL, max_tokens=16000, system=system,
              output_config={"effort": "medium",
                             "format": {"type": "json_schema", "schema": schema}},
              messages=[{"role": "user", "content": user}])
    try:   # 안전 분류기가 거절하면 서버가 대체 모델로 재시도 (fallbacks: "default")
        resp = client.beta.messages.create(betas=["server-side-fallback-2026-07-01"], fallbacks="default", **kw)
    except TypeError:   # 구버전 SDK: 파라미터 이름을 모르면 본문에 직접
        resp = client.beta.messages.create(betas=["server-side-fallback-2026-07-01"], extra_body={"fallbacks": "default"}, **kw)
    if resp.stop_reason == "refusal":
        raise RuntimeError(f"모델 거절: {getattr(resp, 'stop_details', None)}")
    text = next(b.text for b in resp.content if b.type == "text")
    out = json.loads(text)
    u = resp.usage
    out["_usage"] = {"model": resp.model, "input": u.input_tokens, "output": u.output_tokens}
    return out


def run(X: pd.DataFrame | None = None, live_scores: dict | None = None) -> dict:
    import selflearn_virtual as sv, selflearn_discover as sd
    if X is None:
        X = sv.build()
    B = X[X.bot_pass].copy()
    B["date"] = pd.to_datetime(B.date)
    reg = sd.load_registry()
    payload = {
        "지표설명": {"rsi": "RSI(14)", "bb_pct": "볼린저밴드 내 위치%", "ret_1w": "1주 수익%", "ret_1m": "1달 수익%",
                   "ret_3m": "3달 수익%", "pct_from_low": "52주 저점 대비%", "pct_from_high": "52주 고점 대비%",
                   "dist_ma20": "20일선 이격%", "atr_pct": "14일 평균 진폭%(변동성)", "kospi_1d": "코스피 당일 등락%",
                   "breadth_prev": "전일 상승종목 비율%"},
        "가상매매_건수": int(len(B)), "평균수익%": round(float(B.net_pct.mean()), 3),
        "승률%": round(float((B.net_pct > 0).mean() * 100), 1),
        "지표분포_크게진매매vs이긴매매": _feature_profile(B, sv.LIVE_FEATURES),
        "운영중_도전자": [{"id": c["id"], "규칙": sd.rule_text(c["rules"]), "출처": c.get("source")}
                     for c in reg["challengers"] if c.get("status") == "shadow"],
        "도전자_실시간성적": live_scores or {},
        "실제봇_최근매도": _real_losses(),
    }
    res = {"note": "", "tested": [], "action": "등록 없음"}
    try:
        ans = ask_claude(payload, sv.LIVE_FEATURES)
    except Exception as e:
        res["action"] = f"AI 연구원 건너뜀: {e}"
        print(f"  [AI연구원] {res['action']}")
        return res
    res["note"] = ans.get("note", "")
    res["usage"] = ans.get("_usage")
    tr, va, ho = B[B.date < sd.TR_END], B[(B.date >= sd.TR_END) & (B.date < sd.VA_END)], B[B.date >= sd.VA_END]
    base = {k: d.net_pct.mean() for k, d in [("tr", tr), ("va", va), ("ho", ho)]}
    bigva = (va.net_pct <= -5).mean() * 100
    passed = []
    import math
    for h in ans.get("hypotheses", [])[:5]:
        rules = []
        for r in (h.get("rules") or [])[:2]:
            try:
                v = float(r.get("v"))
            except (TypeError, ValueError):
                continue
            if r.get("f") in sv.LIVE_FEATURES and r.get("op") in ("<", ">") and math.isfinite(v):
                rules.append({"f": r["f"], "op": r["op"], "v": round(v, 3)})
        if not rules:
            continue
        try:
            gt, gv, gh = sd._gain(tr, rules, base["tr"]), sd._gain(va, rules, base["va"]), sd._gain(ho, rules, base["ho"])
        except Exception as e:
            print(f"  [AI연구원] 가설 검증 오류(건너뜀): {rules} {e}")
            continue
        ok = bool(gt and gv and gh and min(gt["keep"], gv["keep"]) >= .5 and gt["gain"] > .10 and gv["gain"] > .10
                  and gh["gain"] > .10 and gv["big"] <= bigva + .5)
        t = {"규칙": sd.rule_text(rules), "근거": h.get("why", ""), "통과": ok,
             "학습": round(gt["gain"], 3) if gt else None, "검증": round(gv["gain"], 3) if gv else None,
             "최종확인": round(gh["gain"], 3) if gh else None}
        res["tested"].append(t)
        if ok:
            passed.append({"rules": rules, "train": gt, "valid": gv, "hold": gh})
    passed.sort(key=lambda p: -p["train"]["gain"])
    for p in passed:
        msg = sd.register(p, reg, live_scores)
        if msg != "DUP":
            res["action"] = "AI 가설 " + msg
            for c in reg["challengers"]:
                if c.get("status") == "shadow" and c["rules"] == p["rules"]:
                    c["name"] = "AI 연구원 가설"
            break
    sd.save_registry(reg)
    print(json.dumps(res, ensure_ascii=False, indent=1, default=float))
    return res


def message(res: dict) -> str:
    from html import escape as _esc   # 10/10: 규칙의 '<'가 텔레그램 태그로 오해돼 내용이 사라지는 것 방지
    lines = ["🧑‍🔬 <b>AI 연구원 주간 노트</b>"]
    if res.get("note"):
        lines.append(_esc(str(res["note"])))
    for t in res.get("tested", []):
        g = (f"학습 {t['학습']:+.2f} / 검증 {t['검증']:+.2f} / 최종 {t['최종확인']:+.2f}%p"
             if None not in (t["학습"], t["검증"], t["최종확인"]) else "검증 불가(남는 매매 부족)")
        lines.append(f"{'✅' if t['통과'] else '❌'} {_esc(str(t['규칙']))} → {g}")
        if t.get("근거"):
            lines.append(f"   └ {_esc(str(t['근거']))}")
    lines.append(f"<b>결과:</b> {_esc(str(res.get('action', '-')))}")
    lines.append("<i>가설은 8년 데이터 3구간 검증을 통과해야만 그림자 등록. 실매매 반영은 회장 승인.</i>")
    return chr(10).join(lines)


if __name__ == "__main__":
    import sys
    r = run()
    print(message(r))
