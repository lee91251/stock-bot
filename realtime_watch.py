# -*- coding: utf-8 -*-
"""실시간 감시 알림 (10/1 신설, 시험 운영 — 매매 안 함, 알림·기록만).

목적: 15분 주기 봇보다 실시간 포착이 실제로 더 유리한 가격을 주는지 2주간 숫자로 비교.
방식: 한국투자증권 웹소켓 실시간 체결가(H0STCNT0)로 감시 종목 최대 40개를 장중 계속 지켜보다가
     '급등 시작' 조건이 되는 순간 텔레그램 알림 + realtime_log/날짜.json 기록.
     보유 종목은 -4%/+6%/+10% 도달 시각을 기록만 (봇 실제 매도 시각과 비교 → 손절 지연 측정).

접속 규격 출처: koreainvestment/open-trading-api (examples_llm/kis_auth.py, ccnl_krx.py)
  - 접속키: POST {KIS_BASE}/oauth2/Approval {grant_type, appkey, secretkey} → approval_key
  - 주소: ws://ops.koreainvestment.com:21000/tryitout  (구독 최대 40개)
  - 데이터: "0|H0STCNT0|건수|필드^필드^..." (46개 필드 × 건수), PINGPONG 은 pong 으로 응답

포착 조건 (봇의 '급등 모멘텀' 신호를 실시간으로, 거래량은 시간 보정):
  당일 +3%~+5%  AND  거래량 속도(누적거래량 ÷ (20일 평균 × 경과비율)) ≥ 2.0  AND  RSI(14, 현재가 포함) < 80
  09:05~14:30 사이, 종목당 하루 1회.
"""
import os, sys, json, time, asyncio, argparse
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import requests

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)
from finance import _now_kst  # 가벼운 모듈 (표준 라이브러리만)

KIS_BASE = "https://openapi.koreainvestment.com:9443"
WS_URL = "ws://ops.koreainvestment.com:21000/tryitout"
APP_KEY, APP_SECRET = os.environ.get("KIS_APP_KEY", ""), os.environ.get("KIS_APP_SECRET", "")
LOG_DIR = os.path.join(BASE, "realtime_log")
MAX_SUBS = 40
COLS = 46            # H0STCNT0 필드 수
F_CODE, F_TIME, F_PRICE, F_CHG, F_ACML_VOL = 0, 1, 2, 5, 13
MAX_ALERTS_PER_DAY = 8


def tg(msg: str, silent: bool = True):
    try:
        from notify import tg_send
        tg_send(msg, silent=silent)
    except Exception as e:
        print(f"  [실시간] 텔레그램 실패: {e}")


def is_trading_day(now) -> bool:
    try:
        from stock import _is_trading_day
        return _is_trading_day(now)
    except Exception:
        return now.weekday() < 5


def build_watchlist() -> tuple[list, dict]:
    """감시 종목: 보유 종목 + 내일 유망주 + 시장스캔 스윙 상위 → 최대 40개."""
    held, watch = {}, []
    try:
        pos = json.load(open(os.path.join(BASE, "positions.json"), encoding="utf-8"))
        held = {c: p for c, p in pos.get("positions", {}).items()}
    except Exception:
        pass
    names = {c: p.get("name", c) for c, p in held.items()}
    try:
        tp = json.load(open(os.path.join(BASE, "tomorrow_picks.json"), encoding="utf-8"))
        for p in sorted(tp.get("picks", []), key=lambda x: -x.get("score_bonus", 0)):
            watch.append(p["code"]); names[p["code"]] = p.get("name", p["code"])
    except Exception as e:
        print(f"  [실시간] tomorrow_picks 없음: {e}")
    try:
        sc = json.load(open(os.path.join(BASE, "market_scan_cache.json"), encoding="utf-8"))
        allst = sc.get("stocks", [])
        ss = [s for s in allst if s.get("swing_score") is not None]
        ss.sort(key=lambda s: -s.get("swing_score", 0))
        rest = sorted([s for s in allst if s.get("swing_score") is None], key=lambda s: -(s.get("score") or 0))
        for s in ss + rest:                      # 스윙 통과 우선, 남는 자리는 일반 점수 상위로 채움
            c = str(s.get("ticker", "")).split(".")[0]
            if c: watch.append(c); names.setdefault(c, s.get("name", c))
    except Exception as e:
        print(f"  [실시간] market_scan_cache 없음: {e}")
    codes, seen = [], set()
    for c in list(held.keys()) + watch:
        if c and c not in seen and len(c) == 6:
            seen.add(c); codes.append(c)
    codes = codes[:MAX_SUBS]
    return codes, {"held": held, "names": names}


def baselines(codes: list, today: str) -> dict:
    """어제까지 일봉으로 20일 평균 거래량·전일 종가·RSI 계산용 종가 (오늘 봉 제외)."""
    import FinanceDataReader as fdr
    start = (datetime.strptime(today, "%Y-%m-%d") - timedelta(days=60)).strftime("%Y-%m-%d")
    out = {}
    for c in codes:
        try:
            d = fdr.DataReader(c, start)
            d = d[d.index < pd.Timestamp(today)]
            if len(d) < 21: continue
            out[c] = {"avg20": float(d.Volume.tail(20).mean()), "prev_close": float(d.Close.iloc[-1]),
                      "closes": [float(x) for x in d.Close.tail(30)]}
        except Exception as e:
            print(f"  [실시간] {c} 일봉 실패: {e}")
    return out


def rsi14(closes: list) -> float:
    s = pd.Series(closes, dtype=float); d = s.diff()
    g = d.clip(lower=0).rolling(14).mean(); l = (-d.clip(upper=0)).rolling(14).mean()
    v = (100 - 100 / (1 + g / l.replace(0, 1e-9))).iloc[-1]
    return float(v) if v == v else 50.0


def approval_key() -> str:
    r = requests.post(f"{KIS_BASE}/oauth2/Approval",
                      json={"grant_type": "client_credentials", "appkey": APP_KEY, "secretkey": APP_SECRET},
                      timeout=10)
    k = r.json().get("approval_key", "")
    if not k:
        raise RuntimeError(f"웹소켓 접속키 발급 실패: {r.text[:200]}")
    return k


class Watcher:
    def __init__(self, codes, meta, base, today, stop_hm):
        self.codes, self.meta, self.base, self.today = codes, meta, base, today
        self.stop_hm = stop_hm
        self.log = {"date": today, "watch": codes, "alerts": [], "holding_events": [], "stats":
                    {"msgs": 0, "ticks": 0, "reconnects": 0, "first_tick": None, "last_tick": None, "sample": None}}
        self.alerted, self.hold_flags = set(), {}
        self.last = {}                     # code → 최신 (time, price, chg, acml_vol)
        self.last_save = time.time()
        os.makedirs(LOG_DIR, exist_ok=True)
        self.fn = os.path.join(LOG_DIR, f"{today}.json")
        if os.path.exists(self.fn):                      # 같은 날 재실행 → 기존 기록 이어 쓰기 (덮어쓰기 방지)
            try:
                old = json.load(open(self.fn, encoding="utf-8"))
                self.log["alerts"] = old.get("alerts", [])
                self.log["holding_events"] = old.get("holding_events", [])
                self.log["stats"]["reconnects"] = old.get("stats", {}).get("reconnects", 0)
                self.alerted = {a["code"] for a in self.log["alerts"]}
                self.hold_flags = {(e["code"], e["type"]): True for e in self.log["holding_events"]}
            except Exception as e:
                print(f"  [실시간] 기존 기록 읽기 실패(새로 시작): {e}")

    def save(self):
        self.log["last_prices"] = {c: v for c, v in self.last.items()}
        tmp = self.fn + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.log, f, ensure_ascii=False, separators=(",", ":"))
        os.replace(tmp, self.fn)
        self.last_save = time.time()

    def on_tick(self, f: list):
        if len(f) <= F_ACML_VOL:
            return
        code = f[F_CODE]
        try:
            hhmmss, price, chg, acml = f[F_TIME], float(f[F_PRICE]), float(f[F_CHG]), float(f[F_ACML_VOL])
        except (ValueError, IndexError):
            return
        if len(code) != 6 or len(hhmmss) != 6 or not hhmmss.isdigit():
            return
        st = self.log["stats"]; st["ticks"] += 1
        st["first_tick"] = st["first_tick"] or hhmmss; st["last_tick"] = hhmmss
        if st["sample"] is None: st["sample"] = "^".join(f[:16])     # 필드 순서 실검증용
        self.last[code] = [hhmmss, price, chg, acml]
        hm = int(hhmmss[:4])
        name = self.meta["names"].get(code, code)

        # 보유 종목: -4/+6/+10 도달 시각 기록만 (봇 실제 매도 시각과 비교용)
        held = self.meta["held"].get(code)
        if held and held.get("buy_price"):
            pct = (price / held["buy_price"] - 1) * 100
            for lvl, tag in [(-4, "손절선"), (6, "1차익절"), (10, "2차익절")]:
                hit = pct <= lvl if lvl < 0 else pct >= lvl
                if hit and (code, tag) not in self.hold_flags:
                    self.hold_flags[(code, tag)] = True
                    self.log["holding_events"].append({"code": code, "name": name, "t": hhmmss, "price": price,
                                                       "pct": round(pct, 2), "type": tag})
                    self.save()
            return

        # 감시 종목: 급등 시작 포착
        if code in self.alerted or not (905 <= hm <= 1430) or len(self.log["alerts"]) >= MAX_ALERTS_PER_DAY:
            return
        b = self.base.get(code)
        if not b: return
        elapsed = max(5, (int(hhmmss[:2]) - 9) * 60 + int(hhmmss[2:4]))
        pace = acml / (b["avg20"] * min(1.0, elapsed / 390)) if b["avg20"] else 0
        if not (3.0 <= chg <= 5.0 and pace >= 2.0):
            return
        r = rsi14(b["closes"] + [price])
        if r >= 80:
            return
        self.alerted.add(code)
        a = {"code": code, "name": name, "t": hhmmss, "price": price, "chg": chg, "pace": round(pace, 2),
             "vol_ratio_raw": round(acml / b["avg20"] * 100, 1), "rsi": round(r, 1)}
        self.log["alerts"].append(a); self.save()
        tg(f"⚡ <b>실시간 포착 (시험)</b> {name} ({code}) {hhmmss[:2]}:{hhmmss[2:4]}\n"
           f"현재가 {price:,.0f}원 ({chg:+.1f}%) · 거래량 속도 평소 {pace:.1f}배 · RSI {r:.0f}\n"
           f"참고: 손절 {price*0.96:,.0f} / 1차 {price*1.06:,.0f} / 2차 {price*1.10:,.0f}\n"
           f"<i>검증 전 시험 기록 — 매수 권장 아님. 봇 매수와 무관.</i>")

    async def run(self):
        import websockets
        key = approval_key()
        backoff = 3
        while True:
            now = _now_kst()
            if now.strftime("%H:%M") >= self.stop_hm:
                break
            try:
                async with websockets.connect(WS_URL, ping_interval=None, open_timeout=15) as ws:
                    for c in self.codes:
                        await ws.send(json.dumps({
                            "header": {"approval_key": key, "custtype": "P", "tr_type": "1", "content-type": "utf-8"},
                            "body": {"input": {"tr_id": "H0STCNT0", "tr_key": c}}}))
                        await asyncio.sleep(0.05)
                    print(f"[실시간] 구독 {len(self.codes)}종목 완료 {_now_kst().strftime('%H:%M:%S')}")
                    backoff = 3
                    while True:
                        if _now_kst().strftime("%H:%M") >= self.stop_hm:
                            return
                        try:
                            raw = await asyncio.wait_for(ws.recv(), timeout=60)
                        except asyncio.TimeoutError:
                            if time.time() - self.last_save > 300: self.save()
                            continue
                        self.log["stats"]["msgs"] += 1
                        if raw and raw[0] in "01":
                            parts = raw.split("|")
                            if len(parts) >= 4 and parts[1] == "H0STCNT0" and parts[0] == "0":
                                fields = parts[3].split("^")
                                try:
                                    cnt = max(1, int(parts[2]))
                                except ValueError:
                                    cnt = 1
                                width = len(fields) // cnt          # 10/1 실측: 건수로 나눠야 정확 (필드 수 고정 가정 X)
                                for i in range(cnt):
                                    try:
                                        self.on_tick(fields[i * width:(i + 1) * width])
                                    except Exception as e:      # 한 건 오류가 연결 전체를 끊지 않게
                                        self.log["stats"]["bad_ticks"] = self.log["stats"].get("bad_ticks", 0) + 1
                                        if self.log["stats"]["bad_ticks"] <= 3:
                                            print(f"[실시간] 체결 1건 파싱 오류(무시): {e} / {fields[i*width:i*width+6]}")
                        else:
                            try:
                                j = json.loads(raw)
                                tid = j.get("header", {}).get("tr_id")
                                if tid == "PINGPONG":
                                    await ws.pong(raw)
                                elif j.get("body", {}).get("rt_cd") not in (None, "0"):
                                    print(f"[실시간] 구독 응답: {j.get('header',{}).get('tr_key')} {j['body'].get('msg1')}")
                            except Exception:
                                pass
                        if time.time() - self.last_save > 300:
                            self.save()
            except Exception as e:
                self.log["stats"]["reconnects"] += 1
                print(f"[실시간] 연결 끊김/오류 → {backoff}초 후 재접속: {e}")
                self.save()
                if self.log["stats"]["reconnects"] > 30:
                    tg("⚠️ 실시간 감시: 재접속 30회 초과로 오늘 감시 중단")
                    break
                await asyncio.sleep(backoff); backoff = min(60, backoff * 2)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stop", default="14:30", help="감시 종료 시각 HH:MM (KST)")
    ap.add_argument("--no-wait", action="store_true", help="09:00 전이라도 바로 시작(테스트)")
    a = ap.parse_args()
    import re
    if not re.fullmatch(r"\d{2}:\d{2}", a.stop):
        print(f"[실시간] 종료 시각 형식 오류 '{a.stop}' (HH:MM) — 종료"); return
    now = _now_kst(); today = now.strftime("%Y-%m-%d")
    if now.strftime("%H:%M") >= a.stop:
        print(f"[실시간] 이미 종료 시각({a.stop}) 지남 — 종료"); return
    if not is_trading_day(now):
        print("[실시간] 휴장일 — 종료"); return
    if not (APP_KEY and APP_SECRET):
        print("[실시간] KIS 키 없음 — 종료"); return
    codes, meta = build_watchlist()
    base = baselines([c for c in codes if c not in meta["held"]], today)
    print(f"[실시간] 감시 {len(codes)}종목 (보유 {len(meta['held'])}) / 기준데이터 {len(base)}종목 / 종료 {a.stop}")
    if not a.no_wait:
        while _now_kst().strftime("%H:%M") < "09:00":
            time.sleep(20)
    w = Watcher(codes, meta, base, today, a.stop)
    try:
        asyncio.run(w.run())
    finally:
        w.save()
        st = w.log["stats"]
        print(f"[실시간] 종료 — 메시지 {st['msgs']} / 체결 {st['ticks']} / 재접속 {st['reconnects']} / 포착 {len(w.log['alerts'])} / 샘플 {st['sample']}")
        if st["ticks"] == 0 and st["msgs"] >= 0 and _now_kst().strftime("%H:%M") >= "09:10":
            tg("⚠️ 실시간 감시: 체결 데이터를 하나도 받지 못함 — 접속 규격 점검 필요")


if __name__ == "__main__":
    main()
