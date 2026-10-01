# -*- coding: utf-8 -*-
"""자가학습 ③ — 봇이 스스로 '사지 마라 / 쉬어라' 규칙 후보를 찾는 주간 발굴기 (10/1).

과최적화 방벽 (반박 검증 반영):
  1) 3구간 분리: 학습 ~2022 / 검증 2023~2024 / 최종확인 2025~ — 학습에서 고르고, 검증·최종확인 둘 다 통과해야 함
  2) 실시간에도 같은 정의로 구할 수 있는 지표만 사용 (selflearn_virtual.LIVE_FEATURES)
  3) 규칙은 최대 2개 조합, 남는 매매 50% 이상 (너무 깎으면 우연)
  4) 등록돼도 '그림자'일 뿐 — 실매매 적용은 실시간 성적 + 회장 승인
  5) 동시 도전자 최대 3개 (C1 포함). 주당 신규 등록 최대 1개
"""
import os, json
from datetime import datetime
import numpy as np, pandas as pd

BASE = os.path.dirname(os.path.abspath(__file__))
REG = os.path.join(BASE, "challengers.json")
TR_END, VA_END = pd.Timestamp("2023-01-01"), pd.Timestamp("2025-01-01")
MAX_ACTIVE = 3
NAMES = {"rsi": "RSI", "bb_pct": "볼린저위치%", "ret_1w": "1주수익%", "ret_1m": "1달수익%", "ret_3m": "3달수익%",
         "pct_from_low": "52주저점대비%", "pct_from_high": "52주고점대비%", "dist_ma20": "20일선이격%",
         "atr_pct": "변동성(ATR)%", "kospi_1d": "코스피당일%", "breadth_prev": "전일상승종목비율%"}

DEFAULT_REG = {"challengers": [{
    "id": "C1", "name": "시장 나쁜 날 쉬기",
    "rules": [{"f": "kospi_1d", "op": "<", "v": -0.24}, {"f": "breadth_prev", "op": "<", "v": 39.0}],
    "status": "shadow", "source": "10/1 8년 가상매매(학습~2022→시험2023~26 4년 모두 개선)", "created": "2026-10-01"}],
    "history": []}


def load_registry() -> dict:
    try:
        return json.load(open(REG, encoding="utf-8"))
    except Exception:
        return json.loads(json.dumps(DEFAULT_REG))


def save_registry(reg: dict):
    json.dump(reg, open(REG, "w", encoding="utf-8"), ensure_ascii=False, indent=1)


def rule_text(rules: list) -> str:
    return " 또는 ".join(f"{NAMES.get(r['f'], r['f'])} {r['op']} {r['v']:g}" for r in rules)


def _mask(df: pd.DataFrame, rules: list) -> pd.Series:
    """True = 이 규칙에 걸려 '제외'. 지표가 비어 있으면 제외 안 함 (실시간과 동일)."""
    m = pd.Series(False, index=df.index)
    for r in rules:
        col = df[r["f"]]
        hit = (col < r["v"]) if r["op"] == "<" else (col > r["v"])
        m |= hit.fillna(False)
    return m


def _gain(df, rules, base):
    keep = df[~_mask(df, rules)]
    if len(keep) < 30: return None
    return {"gain": keep.net_pct.mean() - base, "keep": len(keep) / len(df),
            "big": (keep.net_pct <= -5).mean() * 100, "n": len(keep), "avg": keep.net_pct.mean(),
            "win": (keep.net_pct > 0).mean() * 100}


def discover(X: pd.DataFrame, live_features: list) -> dict:
    B = X[X.bot_pass].copy()
    B["date"] = pd.to_datetime(B.date)
    tr, va, ho = B[B.date < TR_END], B[(B.date >= TR_END) & (B.date < VA_END)], B[B.date >= VA_END]
    base = {k: d.net_pct.mean() for k, d in [("tr", tr), ("va", va), ("ho", ho)]}
    bigb = {k: (d.net_pct <= -5).mean() * 100 for k, d in [("tr", tr), ("va", va), ("ho", ho)]}
    out = {"base": {"학습": [len(tr), round(base["tr"], 3)], "검증": [len(va), round(base["va"], 3)],
                    "최종확인": [len(ho), round(base["ho"], 3)]}, "found": None, "checked": 0, "top": []}

    singles = []
    for f in live_features:
        if f not in tr or tr[f].notna().sum() < 100: continue
        for q in [.1, .2, .3, .7, .8, .9]:
            v = float(np.nanquantile(tr[f], q))
            r = [{"f": f, "op": "<" if q <= .3 else ">", "v": round(v, 3)}]
            g = _gain(tr, r, base["tr"])
            out["checked"] += 1
            if g and g["keep"] >= .5 and g["gain"] > 0:
                singles.append((g["gain"], r))
    singles.sort(key=lambda x: -x[0])
    cands = [r for _, r in singles[:12]]
    for i in range(min(8, len(singles))):          # 2개 조합 (서로 다른 지표)
        for j in range(i + 1, min(12, len(singles))):
            a, b = singles[i][1], singles[j][1]
            if a[0]["f"] != b[0]["f"]:
                cands.append(a + b)
    scored = []
    for rules in cands:
        gt, gv, gh = _gain(tr, rules, base["tr"]), _gain(va, rules, base["va"]), _gain(ho, rules, base["ho"])
        out["checked"] += 1
        if not (gt and gv and gh) or min(gt["keep"], gv["keep"]) < .5:
            continue
        ok = bool(gt["gain"] > .10 and gv["gain"] > .10 and gh["gain"] > .10 and gv["big"] <= bigb["va"] + .5)
        scored.append({"rules": rules, "ok": ok, "train": gt, "valid": gv, "hold": gh,
                       "score": gt["gain"]})    # 순위는 학습 구간만 (검증·최종확인은 합격 문턱으로만 사용)
    scored.sort(key=lambda s: (-s["ok"], -s["score"]))
    out["top"] = [{"규칙": rule_text(s["rules"]), "통과": s["ok"],
                   "학습": round(s["train"]["gain"], 3), "검증": round(s["valid"]["gain"], 3),
                   "최종확인": round(s["hold"]["gain"], 3), "남는비율": round(s["valid"]["keep"], 2)} for s in scored[:5]]
    out["found"] = None
    out["passed_list"] = [s for s in scored if s["ok"]]
    if out["passed_list"]:
        out["found"] = out["passed_list"][0]
    return out


def _same(a: list, b: list) -> bool:
    if {r["f"] for r in a} != {r["f"] for r in b}: return False
    for r in a:
        m = next(x for x in b if x["f"] == r["f"])
        if m["op"] != r["op"] or abs(m["v"] - r["v"]) > max(abs(r["v"]) * .1, .05): return False
    return True


def register(found: dict, reg: dict, live_scores: dict | None = None) -> str:
    """새 도전자 등록 (중복·한도 관리). 반환: 결과 설명."""
    act = [c for c in reg["challengers"] if c.get("status") == "shadow"]
    if any(_same(found["rules"], c["rules"]) for c in reg["challengers"]):   # 은퇴한 규칙도 재등록 안 함
        return "DUP"
    today = datetime.now().strftime("%Y-%m-%d")
    nid = f"C{max([int(c['id'][1:]) for c in reg['challengers'] if c['id'][1:].isdigit()] + [1]) + 1}"
    new = {"id": nid, "name": "봇 자동 발굴", "rules": found["rules"], "status": "shadow",
           "source": f"{today} 주간 발굴 (학습 {found['train']['gain']:+.2f}%p / 검증 {found['valid']['gain']:+.2f}%p / "
                     f"최종확인 {found['hold']['gain']:+.2f}%p)", "created": today,
           "valid_gain": round(found["valid"]["gain"], 3)}
    msg = f"신규 도전자 {nid} 등록: {rule_text(found['rules'])}"
    if len(act) >= MAX_ACTIVE:
        disc = [c for c in act if c["id"] != "C1"]
        if not disc:
            return "도전자 한도 초과 → 등록 안 함"
        # 실시간 성적(있으면) 또는 검증 개선폭이 가장 낮은 자동발굴 도전자 은퇴
        champ = (live_scores or {}).get("champion", {})
        def strength(c):   # 같은 단위(%p 개선폭)로 비교: 실시간 30건↑면 챔피언 대비 개선폭, 아니면 검증 개선폭
            ls = (live_scores or {}).get(c["id"], {})
            if ls.get("n", 0) >= 30 and champ.get("n", 0) >= 30:
                return ls["avg"] - champ["avg"]
            return c.get("valid_gain", 0)
        weak = min(disc, key=strength)
        if strength(weak) >= new["valid_gain"]:
            return f"기존 도전자보다 약함 → 등록 안 함 ({rule_text(found['rules'])})"
        weak["status"] = "retired"; weak["retired"] = today
        reg["history"].append({"date": today, "event": f"{weak['id']} 은퇴 (더 나은 {nid}로 교체)"})
        msg += f" / {weak['id']} 은퇴"
    reg["challengers"].append(new)
    reg["history"].append({"date": today, "event": msg})
    return msg


def run(live_scores: dict | None = None) -> dict:
    import selflearn_virtual as sv
    X = sv.build()
    res = discover(X, sv.LIVE_FEATURES)
    reg = load_registry()
    res["action"] = "검증 통과 규칙 없음 → 등록 안 함"
    for cand in res.get("passed_list", []):          # 1등이 이미 있는 규칙이면 다음 합격 규칙 시도
        msg = register(cand, reg, live_scores)
        if msg != "DUP":
            res["action"] = msg
            break
        res["action"] = "합격 규칙이 모두 기존 도전자와 같음 → 등록 안 함"
    save_registry(reg)
    res.pop("found", None); res.pop("passed_list", None)
    print(json.dumps(res, ensure_ascii=False, indent=1, default=float))
    return res


if __name__ == "__main__":
    run()
