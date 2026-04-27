"""
투자 비서 프로그램 v6.0 — AI 강화판
- Anthropic Claude AI 시장 판단 + 섹터/매크로 분석
- 텔레그램 인터랙티브 봇 (자연어 질의, 5분 폴링)
- 뉴스 감성 분석 (네이버 뉴스 RSS)
- 공포/탐욕 지수 (외국인·기관 수급 기반)
- 지지/저항선 + ATR 동적 손절
- 보유종목 손절/목표 알림
- 월간 성과 추적
"""

import os
import sys
import json
import time
import re
import requests
import yfinance as yf
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from xml.etree import ElementTree as ET

try:
    import anthropic as _ant_lib
    _ANTHROPIC_OK = True
except ImportError:
    _ANTHROPIC_OK = False

try:
    from pykrx import stock as _pykrx
    _PYKRX_OK = True
except ImportError:
    _PYKRX_OK = False
    _pykrx = None

# ════════════════════════════════════════════════
# 환경변수 & 기본 설정
# ════════════════════════════════════════════════
TELEGRAM_TOKEN    = os.environ.get("TELEGRAM_TOKEN",    "")
TELEGRAM_CHAT_ID  = os.environ.get("TELEGRAM_CHAT_ID",  "")
DART_API_KEY      = os.environ.get("DART_API_KEY",      "")
KIS_APP_KEY       = os.environ.get("KIS_APP_KEY",       "")
KIS_APP_SECRET    = os.environ.get("KIS_APP_SECRET",    "")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

INVEST_PER_STOCK = 2_000_000
STOP_LOSS_PCT    = 0.07
TARGET1_PCT      = 0.10
TARGET2_PCT      = 0.20
TARGET3_PCT      = 0.40

# 보유 종목: 환경변수 HOLDINGS_JSON 또는 직접 설정
# 형식: [{"code":"012450","name":"한화에어로스페이스","qty":10,"avg_price":180000}]
try:
    HOLDINGS: list = json.loads(os.environ.get("HOLDINGS_JSON", "[]"))
except Exception:
    HOLDINGS = []

PERFORMANCE_FILE  = os.path.join(os.path.dirname(os.path.abspath(__file__)), "performance.json")
MARKET_SCAN_CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "market_scan_cache.json")
MARKET_SCAN_N     = 1500
KIS_BASE  = "https://openapi.koreainvestment.com:9443"
DART_BASE = "https://opendart.fss.or.kr/api"

# ════════════════════════════════════════════════
# 유틸
# ════════════════════════════════════════════════
def _safe_float(val, default: float = 0.0) -> float:
    try:
        return float(str(val).replace(",", "")) if val not in (None, "", "-", "N/A") else default
    except (ValueError, TypeError):
        return default


def _fmt_krw(val: int) -> str:
    if val == 0:
        return "0원"
    sign = "-" if val < 0 else ""
    v = abs(val)
    if v >= 1_000_000_000_000:
        return f"{sign}{v/1_000_000_000_000:.1f}조원"
    return f"{sign}{v/100_000_000:.0f}억원"


# ════════════════════════════════════════════════
# KIS 클라이언트
# ════════════════════════════════════════════════
class KisClient:
    def __init__(self):
        self._token: str = ""
        self._token_exp: datetime = datetime.min

    def available(self) -> bool:
        return bool(KIS_APP_KEY and KIS_APP_SECRET)

    def _ensure_token(self):
        if not self.available() or datetime.now() < self._token_exp:
            return
        try:
            r = requests.post(
                f"{KIS_BASE}/oauth2/tokenP",
                json={"grant_type": "client_credentials",
                      "appkey": KIS_APP_KEY, "appsecret": KIS_APP_SECRET},
                timeout=10,
            )
            d = r.json()
            self._token     = d.get("access_token", "")
            self._token_exp = datetime.now() + timedelta(
                seconds=int(d.get("expires_in", 86400)) - 600
            )
            if not self._token:
                print(f"  [KIS] 토큰 발급 오류: access_token 없음 — {d}")
        except Exception as e:
            print(f"  [KIS] 토큰 발급 실패: {e}")

    def _get(self, path: str, tr_id: str, params: dict) -> dict:
        if not self.available():
            return {}
        self._ensure_token()
        if not self._token:
            print(f"  [KIS] {tr_id} 스킵: 유효한 토큰 없음")
            return {}
        try:
            r = requests.get(
                f"{KIS_BASE}{path}",
                headers={
                    "content-type":  "application/json; charset=utf-8",
                    "authorization": f"Bearer {self._token}",
                    "appkey":        KIS_APP_KEY,
                    "appsecret":     KIS_APP_SECRET,
                    "tr_id":         tr_id,
                    "custtype":      "P",
                },
                params=params,
                timeout=10,
            )
            d = r.json()
            if d.get("rt_cd") == "0":
                return d
            print(f"  [KIS] {tr_id} API 오류: rt_cd={d.get('rt_cd')} msg={d.get('msg1','')}")
            return {}
        except Exception as e:
            print(f"  [KIS] {tr_id} 실패: {e}")
            return {}

    def get_price(self, code: str) -> dict:
        d = self._get(
            "/uapi/domestic-stock/v1/quotations/inquire-price",
            "FHKST01010100",
            {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": code},
        )
        return d.get("output", {})

    def get_investor(self, code: str) -> dict:
        d = self._get(
            "/uapi/domestic-stock/v1/quotations/inquire-investor",
            "FHKST01010900",
            {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": code},
        )
        if not d:
            return {}
        zero_fallback = {}  # 모든 키에서 비-제로 수급을 못 찾으면 사용할 폴백
        for key in ("output", "output1", "output2"):
            out = d.get(key)
            if out is None:
                continue
            if isinstance(out, dict) and out:
                # 딕셔너리 응답: 수급 필드가 있을 때만 후보로 채택
                if out.get("frgn_ntby_tr_pbmn") is not None or out.get("orgn_ntby_tr_pbmn") is not None:
                    if (_safe_float(out.get("frgn_ntby_tr_pbmn")) != 0.0 or
                            _safe_float(out.get("orgn_ntby_tr_pbmn")) != 0.0):
                        return out
                    if not zero_fallback:
                        zero_fallback = out
                continue
            if isinstance(out, list):
                if not out:
                    continue
                # 비(非)제로 수급이 있는 가장 최신 항목을 우선 반환
                for item in out:
                    if not isinstance(item, dict):
                        continue
                    if (_safe_float(item.get("frgn_ntby_tr_pbmn")) != 0.0 or
                            _safe_float(item.get("orgn_ntby_tr_pbmn")) != 0.0):
                        return item
                # 전체 0이면 다음 키(output1/output2)도 시도하기 위해 fallback 보관 후 continue
                if not zero_fallback and isinstance(out[0], dict):
                    zero_fallback = out[0]
                continue
        if zero_fallback:
            print(f"  [KIS] {code} 수급 전체 0 "
                  f"(date={zero_fallback.get('stck_bsop_date','?')})")
            return zero_fallback
        print(f"  [KIS] {code} 투자자 데이터 파싱 실패 — 응답 키: {list(d.keys())}")
        return {}

    def get_daily_chart(self, code: str, months: int = 5) -> list:
        end_dt   = datetime.now().strftime("%Y%m%d")
        start_dt = (datetime.now() - timedelta(days=months * 31)).strftime("%Y%m%d")
        d = self._get(
            "/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice",
            "FHKST03010100",
            {
                "FID_COND_MRKT_DIV_CODE": "J",
                "FID_INPUT_ISCD":         code,
                "FID_INPUT_DATE_1":       start_dt,
                "FID_INPUT_DATE_2":       end_dt,
                "FID_PERIOD_DIV_CODE":    "D",
                "FID_ORG_ADJ_PRC":        "0",
            },
        )
        return list(reversed(d.get("output2", [])))

    def get_kospi(self) -> dict:
        d = self._get(
            "/uapi/domestic-stock/v1/quotations/inquire-index-price",
            "FHPUP02100000",
            {"FID_COND_MRKT_DIV_CODE": "U", "FID_INPUT_ISCD": "0001"},
        )
        return d.get("output", {})


_kis = KisClient()


# ════════════════════════════════════════════════
# DART API
# ════════════════════════════════════════════════
_corp_cache: dict = {}

SIGNAL_DEFS = {
    "rights":   (["유상증자결정"],
                 "유상증자",   True),
    "buyback":  (["자기주식취득결정", "자기주식취득신탁계약체결"],
                 "자사주매입", False),
    "order":    (["단일판매ㆍ공급계약체결", "수주계약", "공급계약체결"],
                 "신규수주",   False),
    "dividend": (["현금배당결정", "배당결정"],
                 "배당결정",   False),
    "insider":  (["임원ㆍ주요주주특정증권등소유상황보고서"],
                 "내부자변동", True),
}


def _dart_req(endpoint: str, params: dict) -> dict:
    if not DART_API_KEY:
        return {}
    try:
        r = requests.get(
            f"{DART_BASE}/{endpoint}",
            params={"crtfc_key": DART_API_KEY, **params},
            timeout=10,
        )
        d = r.json()
        return d if d.get("status") == "000" else {}
    except Exception:
        return {}


def dart_corp_code(stock_code: str) -> str:
    if stock_code in _corp_cache:
        return _corp_cache[stock_code]
    d = _dart_req("company.json", {"stock_code": stock_code})
    code = d.get("corp_code", "")
    if code:
        _corp_cache[stock_code] = code
    return code


def dart_financials(corp_code: str) -> dict:
    year = datetime.now().year - 1
    d = {}
    fs_used = "CFS"
    for fs_div in ("CFS", "OFS"):
        d = _dart_req("fnlttSinglAcntAll.json", {
            "corp_code":  corp_code,
            "bsns_year":  str(year),
            "reprt_code": "11011",
            "fs_div":     fs_div,
        })
        if d and "list" in d:
            fs_used = fs_div
            break

    if not d or "list" not in d:
        return {}

    REVENUE_NM    = {"매출액", "영업수익", "수익(매출액)"}
    OP_INCOME_NM  = {"영업이익", "영업이익(손실)"}
    NET_INCOME_NM = {"당기순이익", "당기순이익(손실)", "분기순이익"}

    res = {"year": year, "fs_div": fs_used}
    prev_res: dict = {}

    for item in d["list"]:
        if item.get("sj_div") not in ("IS", "CIS"):
            continue
        acct = item.get("account_nm", "").strip()
        raw  = item.get("thstrm_amount", "0").replace(",", "")
        prev = item.get("frmtrm_amount", "0").replace(",", "")
        try:
            val  = int(raw)
            pval = int(prev)
        except (ValueError, TypeError):
            continue
        if acct in REVENUE_NM and "revenue" not in res:
            res["revenue"]    = val
            prev_res["revenue"] = pval
        elif acct in OP_INCOME_NM and "op_income" not in res:
            res["op_income"]    = val
            prev_res["op_income"] = pval
        elif acct in NET_INCOME_NM and "net_income" not in res:
            res["net_income"]   = val
            prev_res["net_income"] = pval

    # YoY 성장률
    if "revenue" in res and prev_res.get("revenue", 0) > 0:
        res["revenue_yoy"] = round(
            (res["revenue"] - prev_res["revenue"]) / prev_res["revenue"] * 100, 1
        )
    if "op_income" in res and abs(prev_res.get("op_income", 0)) > 0:
        res["op_income_yoy"] = round(
            (res["op_income"] - prev_res["op_income"]) / abs(prev_res["op_income"]) * 100, 1
        )
    return res


def dart_disclosures(corp_code: str, days: int = 7) -> list:
    today = datetime.now()
    start = (today - timedelta(days=days)).strftime("%Y%m%d")
    end   = today.strftime("%Y%m%d")
    d = _dart_req("list.json", {
        "corp_code":  corp_code,
        "bgn_de":     start,
        "end_de":     end,
        "page_count": "40",
    })
    return d.get("list", [])


def detect_signals(disclosures: list) -> dict:
    result = {k: [] for k in SIGNAL_DEFS}
    for disc in disclosures:
        nm = disc.get("report_nm", "")
        for key, (keywords, _, _) in SIGNAL_DEFS.items():
            if any(kw in nm for kw in keywords):
                rno = disc.get("rcept_no", "")
                result[key].append({
                    "title": nm,
                    "date":  disc.get("rcept_dt", ""),
                    "url":   f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rno}",
                })
    return result


def get_all_dart_data(kr_stocks: dict) -> dict:
    if not DART_API_KEY:
        print("  [DART] API 키 없음 — 공시 수집 건너뜀")
        return {}
    result = {}
    for ticker in kr_stocks:
        code = ticker.split(".")[0]
        corp = dart_corp_code(code)
        if not corp:
            continue
        fin  = dart_financials(corp)
        sigs = detect_signals(dart_disclosures(corp, days=7))
        result[ticker] = {"financials": fin, "signals": sigs}
        time.sleep(0.3)
    return result


# ════════════════════════════════════════════════
# 뉴스 감성 분석 (네이버 뉴스 RSS)
# ════════════════════════════════════════════════
_POS_KW = [
    "급등", "상승", "호재", "계약", "수주", "실적", "흑자", "성장",
    "매수", "목표가상향", "신고가", "배당", "수익", "개선", "호실적",
]
_NEG_KW = [
    "하락", "급락", "손실", "악재", "적자", "소송", "리콜", "제재",
    "조사", "유상증자", "매도", "부진", "경고", "위반", "하향",
]


def get_news_sentiment(name: str) -> dict:
    """네이버 뉴스 RSS로 종목명 감성 분석 (최근 10건)"""
    try:
        url = (
            "https://search.naver.com/rss.nhn"
            f"?where=news&query={requests.utils.quote(name)}"
        )
        r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=8)
        root = ET.fromstring(r.content)
        items = root.findall(".//item")[:10]

        pos = neg = 0
        titles = []
        for it in items:
            t = (it.findtext("title") or "").strip()
            titles.append(t)
            pos += sum(1 for kw in _POS_KW if kw in t)
            neg += sum(1 for kw in _NEG_KW if kw in t)

        total = pos + neg
        score = round((pos - neg) / max(total, 1) * 100)
        if score > 20:
            sentiment = "긍정"
        elif score < -20:
            sentiment = "부정"
        else:
            sentiment = "중립"
        return {"pos": pos, "neg": neg, "score": score,
                "sentiment": sentiment, "titles": titles[:3]}
    except Exception:
        return {"pos": 0, "neg": 0, "score": 0, "sentiment": "중립", "titles": []}


# ════════════════════════════════════════════════
# 공포/탐욕 지수 (VIX + 수급 + 지수 기반 근사)
# ════════════════════════════════════════════════
def get_fear_greed(mood: dict) -> dict:
    fg = 50
    try:
        vix = mood.get("vix", 20)
        if vix >= 35:    fg -= 30
        elif vix >= 28:  fg -= 20
        elif vix >= 22:  fg -= 10
        elif vix <= 13:  fg += 20
        elif vix <= 16:  fg += 10

        chg = mood.get("kospi_chg", 0)
        if chg >= 2.0:    fg += 15
        elif chg >= 0.8:  fg += 8
        elif chg <= -2.0: fg -= 15
        elif chg <= -0.8: fg -= 8

        sp = mood.get("sp500_chg", 0)
        if sp >= 1.5:    fg += 10
        elif sp >= 0.5:  fg += 5
        elif sp <= -1.5: fg -= 10
        elif sp <= -0.5: fg -= 5

        fg = max(0, min(100, fg))
        if fg >= 75:   label = "극도 탐욕"
        elif fg >= 60: label = "탐욕"
        elif fg >= 40: label = "중립"
        elif fg >= 25: label = "공포"
        else:          label = "극도 공포"
        return {"score": fg, "label": label}
    except Exception:
        return {"score": 50, "label": "중립"}


# ════════════════════════════════════════════════
# 시장 분위기
# ════════════════════════════════════════════════
def get_market_mood() -> dict:
    mood: dict = {}
    try:
        kospi_ok = False
        if _kis.available():
            try:
                ki = _kis.get_kospi()
                if ki:
                    mood["kospi_price"] = round(_safe_float(ki.get("bstp_nmix_prpr")), 2)
                    mood["kospi_chg"]   = round(_safe_float(ki.get("prdy_ctrt")), 2)
                    kospi_ok = True
            except Exception:
                pass

        if not kospi_ok:
            kospi = yf.Ticker("^KS11").history(period="5d")
            if len(kospi) >= 2:
                mood["kospi_chg"]   = round(
                    (kospi["Close"].iloc[-1] - kospi["Close"].iloc[-2])
                    / kospi["Close"].iloc[-2] * 100, 2
                )
                mood["kospi_price"] = round(float(kospi["Close"].iloc[-1]), 2)
            else:
                mood["kospi_chg"] = mood["kospi_price"] = 0

        vix    = yf.Ticker("^VIX").info
        usdkrw = yf.Ticker("KRW=X").info
        wti    = yf.Ticker("CL=F").info
        gold   = yf.Ticker("GC=F").info
        sp500  = yf.Ticker("^GSPC").history(period="2d")

        mood["sp500_chg"] = (
            round(
                (sp500["Close"].iloc[-1] - sp500["Close"].iloc[-2])
                / sp500["Close"].iloc[-2] * 100, 2
            ) if len(sp500) >= 2 else 0
        )
        mood["vix"]    = round(float(vix.get("regularMarketPrice") or 20), 1)
        mood["usdkrw"] = round(float(usdkrw.get("regularMarketPrice") or 1300), 0)
        mood["wti"]    = round(float(wti.get("regularMarketPrice") or 75), 1)
        mood["gold"]   = round(float(gold.get("regularMarketPrice") or 2000), 0)

        if mood["vix"] > 30:
            mood["status"] = "위험"
            mood["advice"] = "⛔ 공포지수 매우 높음 — 오늘은 관망 추천. 급하게 매수 금지."
        elif mood["vix"] > 20:
            mood["status"] = "주의"
            mood["advice"] = "⚠️ 시장 불안정 — 검증된 가치주 위주로 소량만 접근."
        elif mood["kospi_chg"] < -1.5:
            mood["status"] = "하락"
            mood["advice"] = "⚠️ 코스피 하락 중 — 분할매수 전략으로 접근 권장."
        else:
            mood["status"] = "양호"
            mood["advice"] = "✅ 시장 분위기 양호 — 추천 종목 적극 검토 가능."

    except Exception:
        mood = {
            "kospi_chg": 0, "kospi_price": 0, "sp500_chg": 0,
            "vix": 20, "usdkrw": 1300, "wti": 75, "gold": 2000,
            "status": "확인불가",
            "advice": "⚠️ 시장 데이터 수집 실패 — 직접 확인 필요.",
        }
    return mood


# ════════════════════════════════════════════════
# 미국 경제지표 (FRED 공개 URL + 야후 파이낸스)
# ════════════════════════════════════════════════
def get_us_macro_indicators() -> dict:
    """미국 주요 경제지표 수집 — API 키 없음 (yfinance + FRED 공개 CSV)"""
    macro: dict = {
        "tnx": None, "tnx_prev": None,
        "irx": None, "irx_prev": None,
        "dxy": None, "dxy_prev": None,
        "cpi_yoy": None, "cpi_mom": None, "cpi_month": "",
        "fed_direction": "확인불가", "fed_note": "",
        "yield_spread": None,
    }

    # ── 야후 파이낸스: 국채금리 + 달러인덱스 ──────
    for key, tkr in [("tnx", "^TNX"), ("irx", "^IRX"), ("dxy", "DX-Y.NYB")]:
        try:
            hist = yf.Ticker(tkr).history(period="3mo")
            if len(hist) >= 2:
                macro[key]              = round(float(hist["Close"].iloc[-1]), 3)
                macro[f"{key}_prev"]   = round(float(hist["Close"].iloc[0]),  3)
        except Exception:
            pass

    # ── 장단기 금리차 (10Y − 단기) ───────────────
    if macro["tnx"] is not None and macro["irx"] is not None:
        macro["yield_spread"] = round(macro["tnx"] - macro["irx"], 3)

    # ── CPI: FRED 공개 CSV (API 키 없음) ─────────
    try:
        r = requests.get(
            "https://fred.stlouisfed.org/graph/fredgraph.csv?id=CPIAUCSL",
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=12,
        )
        rows = [ln.split(",") for ln in r.text.strip().split("\n")[1:] if ln]
        if len(rows) >= 13:
            v_now  = float(rows[-1][1])
            v_prev = float(rows[-2][1])
            v_yr   = float(rows[-13][1])
            macro["cpi_yoy"]   = round((v_now - v_yr)   / v_yr   * 100, 2)
            macro["cpi_mom"]   = round((v_now - v_prev) / v_prev * 100, 2)
            macro["cpi_month"] = rows[-1][0][:7]
    except Exception:
        pass

    # ── 연준 기준금리 방향 감지 ───────────────────
    if macro["irx"] is not None and macro["irx_prev"] is not None:
        diff = macro["irx"] - macro["irx_prev"]
        if diff >= 0.15:
            macro["fed_direction"] = "인상 기조"
            macro["fed_note"]      = f"단기금리 3개월 +{diff:.2f}%p 상승 → 추가 인상 가능성"
        elif diff <= -0.15:
            macro["fed_direction"] = "인하 기조"
            macro["fed_note"]      = f"단기금리 3개월 {diff:.2f}%p 하락 → 완화 전환 신호"
        else:
            macro["fed_direction"] = "동결 기조"
            macro["fed_note"]      = f"단기금리 3개월 변화 {diff:+.2f}%p → 연준 관망 국면"

    return macro


# ════════════════════════════════════════════════
# 지지/저항 + ATR
# ════════════════════════════════════════════════
def calc_support_resistance(close: pd.Series) -> dict:
    price = float(close.iloc[-1])
    n     = len(close)

    ma20  = float(close.rolling(20).mean().iloc[-1])
    ma60  = float(close.rolling(min(60, n)).mean().iloc[-1])
    ma120 = float(close.rolling(min(120, n)).mean().iloc[-1])

    recent = close.iloc[-20:].values
    swing_highs = [
        float(recent[i])
        for i in range(1, len(recent) - 1)
        if recent[i] > recent[i - 1] and recent[i] > recent[i + 1]
    ]
    swing_lows = [
        float(recent[i])
        for i in range(1, len(recent) - 1)
        if recent[i] < recent[i - 1] and recent[i] < recent[i + 1]
    ]

    below_lows = [l for l in swing_lows if l < price]
    above_highs = [h for h in swing_highs if h > price]

    support    = max(below_lows + [ma20, ma60]) if below_lows else min(ma20, ma60)
    resistance = min(above_highs) if above_highs else price * 1.10

    return {
        "support":          round(support),
        "resistance":       round(resistance),
        "ma20":             round(ma20),
        "ma60":             round(ma60),
        "ma120":            round(ma120),
        "near_support":     abs(price - support)    / price < 0.03,
        "near_resistance":  abs(resistance - price) / price < 0.03,
    }


def calc_atr(
    close: pd.Series,
    high: pd.Series = None,
    low:  pd.Series = None,
    period: int = 14,
) -> float:
    try:
        if high is not None and low is not None:
            tr = pd.concat([
                high - low,
                (high - close.shift(1)).abs(),
                (low  - close.shift(1)).abs(),
            ], axis=1).max(axis=1)
        else:
            tr = close.diff().abs()
        return float(tr.rolling(period).mean().iloc[-1])
    except Exception:
        return float(close.iloc[-1]) * 0.02


# ════════════════════════════════════════════════
# 종목 분류
# ════════════════════════════════════════════════
KR_STOCKS = {
    "009540.KS": ("HD한국조선해양",    "중기", "조선"),
    "010140.KS": ("삼성중공업",        "중기", "조선"),
    "042660.KS": ("한화오션",          "중기", "조선"),
    "012450.KS": ("한화에어로스페이스","단기", "방산"),
    "047810.KS": ("한국항공우주",      "단기", "방산"),
    "000880.KS": ("한화",              "중기", "방산"),
    "064350.KS": ("현대로템",          "단기", "방산"),
    "034020.KS": ("두산에너빌리티",    "장기", "원전"),
    "267260.KS": ("HD현대일렉트릭",    "단기", "전력"),
    "298040.KS": ("효성중공업",        "중기", "전력"),
    "009830.KS": ("한화솔루션",        "장기", "신재생"),
    "015760.KS": ("한국전력",          "장기", "신재생"),
    "000100.KS": ("유한양행",          "중기", "바이오"),
    "128940.KS": ("한미약품",          "중기", "바이오"),
    "068270.KS": ("셀트리온",          "중기", "바이오"),
    "207940.KS": ("삼성바이오로직스",  "장기", "바이오"),
    "105560.KS": ("KB금융",            "단기", "금융"),
    "055550.KS": ("신한지주",          "단기", "금융"),
    "086790.KS": ("하나금융지주",      "단기", "금융"),
    "138930.KS": ("BNK금융지주",       "단기", "금융"),
    "011200.KS": ("HMM",               "단기", "해운"),
    "003490.KS": ("대한항공",          "중기", "항공"),
    "086280.KS": ("현대글로비스",      "중기", "물류"),
    "004020.KS": ("현대제철",          "중기", "철강"),
    "010950.KS": ("S-Oil",             "단기", "에너지"),
    "047050.KS": ("포스코인터내셔널",  "중기", "에너지"),
}

US_STOCKS = {
    "RTX":   ("레이시온",           "단기", "방산"),
    "LMT":   ("록히드마틴",         "중기", "방산"),
    "NOC":   ("노스롭그루만",       "중기", "방산"),
    "GE":    ("GE에어로스페이스",   "단기", "항공"),
    "HII":   ("헌팅턴잉걸스",       "중기", "조선"),
    "NEE":   ("넥스트에라에너지",   "장기", "신재생"),
    "CEG":   ("콘스텔레이션에너지", "중기", "원전"),
    "VST":   ("비스트라에너지",     "단기", "원전"),
    "JNJ":   ("존슨앤존슨",         "중기", "바이오"),
    "UNH":   ("유나이티드헬스",     "단기", "헬스케어"),
    "ABT":   ("애보트",             "중기", "바이오"),
    "JPM":   ("JP모건",             "단기", "금융"),
    "BRK-B": ("버크셔해서웨이",     "장기", "금융"),
    "XOM":   ("엑슨모빌",           "단기", "에너지"),
    "CVX":   ("쉐브론",             "단기", "에너지"),
    "KO":    ("코카콜라",           "장기", "소비재"),
    "PG":    ("P&G",                "장기", "소비재"),
    "O":     ("리얼티인컴",         "장기", "리츠"),
    "AMT":   ("아메리칸타워",       "장기", "리츠"),
}

SECTOR_DESC = {
    "조선":    "선박 건조 및 해양플랜트 — 전 세계 물동량 증가 수혜",
    "방산":    "무기·방위산업 — 글로벌 지정학 리스크로 수요 급증",
    "원전":    "원자력발전 — AI 전력 수요 폭증으로 재조명",
    "신재생":  "태양광·풍력 — 장기 성장성 높으나 단기 수익 어려움",
    "전력":    "전력기기·송배전 — 전력 인프라 투자 확대 수혜",
    "바이오":  "제약·바이오 — 신약 개발 성공 시 급등 가능",
    "금융":    "은행·보험 — 저평가 가치주, 배당 안정적",
    "해운":    "컨테이너·벌크선 운임 — 물동량 지수 연동",
    "항공":    "항공 여객·화물 — 여행 수요 회복 수혜",
    "물류":    "물류·유통 인프라 — 내수 경기 연동",
    "철강":    "철강·소재 — 건설·조선 경기 연동",
    "에너지":  "정유·가스 — 유가 연동, 고배당",
    "헬스케어":"의료기기·서비스 — 고령화 수혜",
    "소비재":  "필수소비재 — 경기 방어주, 안정적 배당",
    "리츠":    "부동산투자신탁 — 월배당, 인플레 헤지",
    "항공우주":"항공기·우주산업 — 방산+민수 복합 성장",
}


# ════════════════════════════════════════════════
# 종목 분석
# ════════════════════════════════════════════════
def analyze(
    ticker: str,
    name: str,
    period: str,
    sector: str,
    dart_data: dict = None,
    with_sentiment: bool = False,
) -> dict:
    is_kr    = ".KS" in ticker or ".KQ" in ticker
    currency = "KRW" if is_kr else "USD"

    foreign_net = inst_net = 0.0
    inv_ok = False
    revenue = profit = 0
    high_series = low_series = None

    # ── 데이터 수집 ──────────────────────────────
    try:
        if is_kr and _kis.available():
            code       = ticker.split(".")[0]
            price_info = _kis.get_price(code)
            if not price_info:
                return None

            price = _safe_float(price_info.get("stck_prpr"))
            if price <= 0:
                return None

            # 유동성 필터: 일 거래대금 5억 미만 제외
            trade_amt = _safe_float(price_info.get("acml_tr_pbmn"))
            if trade_amt > 0 and trade_amt < 500_000_000:
                print(f"  [{name}] 유동성 부족 ({trade_amt/1e8:.1f}억) — 제외")
                return None

            change = _safe_float(price_info.get("prdy_ctrt"))
            per    = _safe_float(price_info.get("per"))  or None
            pbr    = _safe_float(price_info.get("pbr"))  or None
            div    = _safe_float(price_info.get("dvdn_yield"))
            low52  = _safe_float(price_info.get("w52_lwpr"), price)
            high52 = _safe_float(price_info.get("w52_hgpr"), price)
            mktcap = _safe_float(price_info.get("hts_avls")) * 1e8

            eps = _safe_float(price_info.get("eps"))
            bps = _safe_float(price_info.get("bps"))
            roe = round(eps / bps * 100, 1) if bps > 0 else 0.0
            debt = 0.0

            rows = _kis.get_daily_chart(code, months=5)
            if len(rows) < 20:
                return None
            close       = pd.Series([_safe_float(r.get("stck_clpr")) for r in rows], dtype=float)
            volume      = pd.Series([_safe_float(r.get("acml_vol"))   for r in rows], dtype=float)
            high_series = pd.Series([_safe_float(r.get("stck_hgpr")) for r in rows], dtype=float)
            low_series  = pd.Series([_safe_float(r.get("stck_lwpr")) for r in rows], dtype=float)

            inv         = _kis.get_investor(code)
            foreign_net = _safe_float(inv.get("frgn_ntby_tr_pbmn"))
            inst_net    = _safe_float(inv.get("orgn_ntby_tr_pbmn"))
            inv_ok      = bool(inv)
            if inv_ok:
                print(f"  [{name}] 수급 OK — 외국인 {foreign_net/1e2:+.1f}억 "
                      f"기관 {inst_net/1e2:+.1f}억 "
                      f"({inv.get('stck_bsop_date', '날짜?')})")
            else:
                print(f"  [{name}] 수급 조회 실패")

        else:
            stock = yf.Ticker(ticker)
            info  = stock.info
            if not info or not info.get("regularMarketPrice"):
                return None

            price   = float(info.get("regularMarketPrice", 0))
            prev    = float(info.get("regularMarketPreviousClose") or price)
            change  = round((price - prev) / prev * 100, 2) if prev else 0
            per     = info.get("trailingPE") or info.get("forwardPE")
            pbr     = info.get("priceToBook")
            roe     = round((info.get("returnOnEquity") or 0) * 100, 1)
            div     = round((info.get("dividendYield") or 0) * 100, 1)
            debt    = round(info.get("debtToEquity") or 0, 1)
            low52   = info.get("fiftyTwoWeekLow",  price)
            high52  = info.get("fiftyTwoWeekHigh", price)
            mktcap  = info.get("marketCap", 0)
            revenue = info.get("totalRevenue", 0)
            profit  = info.get("netIncomeToCommon", 0)

            hist = stock.history(period="6mo")
            if hist is None or len(hist) < 20:
                return None
            close       = hist["Close"].squeeze()
            volume      = hist["Volume"].squeeze()
            high_series = hist["High"].squeeze()
            low_series  = hist["Low"].squeeze()

    except Exception as e:
        print(f"  [{ticker}] 데이터 오류: {e}")
        return None

    pct_from_low  = round((price - low52)  / low52  * 100, 1) if low52  else 0
    pct_from_high = round((price - high52) / high52 * 100, 1) if high52 else 0

    # ── 기술적 지표 ──────────────────────────────
    try:
        delta = close.diff()
        gain  = delta.clip(lower=0).rolling(14).mean()
        loss  = (-delta.clip(upper=0)).rolling(14).mean()
        rsi   = round(float(
            (100 - 100 / (1 + gain / loss.replace(0, 1e-9))).iloc[-1]
        ), 1)

        ema12       = close.ewm(span=12).mean()
        ema26       = close.ewm(span=26).mean()
        macd_line   = ema12 - ema26
        signal_line = macd_line.ewm(span=9).mean()
        macd_cross  = float(macd_line.iloc[-1]) > float(signal_line.iloc[-1])
        macd_hist   = float(macd_line.iloc[-1]) - float(signal_line.iloc[-1])

        sma20      = close.rolling(20).mean()
        std20      = close.rolling(20).std()
        bb_upper   = sma20 + 2 * std20
        bb_lower   = sma20 - 2 * std20
        bb_pct     = round(
            (float(close.iloc[-1]) - float(bb_lower.iloc[-1]))
            / (float(bb_upper.iloc[-1]) - float(bb_lower.iloc[-1]) + 1e-9) * 100, 1
        )

        avg_vol   = float(volume.rolling(20).mean().iloc[-1])
        last_vol  = float(volume.iloc[-1])
        vol_ratio = round(last_vol / avg_vol * 100, 0) if avg_vol else 100

        n      = len(close)
        ret_1w = round((float(close.iloc[-1]) - float(close.iloc[-5]))  / float(close.iloc[-5])  * 100, 1) if n >= 5  else 0
        ret_1m = round((float(close.iloc[-1]) - float(close.iloc[-20])) / float(close.iloc[-20]) * 100, 1) if n >= 20 else 0
        ret_3m = round((float(close.iloc[-1]) - float(close.iloc[0]))   / float(close.iloc[0])   * 100, 1)

        # 과거 유사 패턴 승률
        win_count = total_count = 0
        for i in range(20, n - 20):
            d_ = delta.iloc[:i + 1]
            g_ = d_.clip(lower=0).rolling(14).mean()
            l_ = (-d_.clip(upper=0)).rolling(14).mean()
            rsi_past = float((100 - 100 / (1 + g_ / l_.replace(0, 1e-9))).iloc[-1])
            if rsi_past < 45:
                future_ret = (float(close.iloc[i + 20]) - float(close.iloc[i])) / float(close.iloc[i])
                total_count += 1
                if future_ret > 0:
                    win_count += 1
        win_rate = round(win_count / total_count * 100) if total_count > 0 else 50

        # ATR 동적 손절
        atr_val      = calc_atr(close, high_series, low_series)
        dynamic_stop = round(price - 2 * atr_val)
        dynamic_stop_pct = round((price - dynamic_stop) / price * 100, 1)

        # 지지/저항
        sr = calc_support_resistance(close)

        # 모멘텀 필터
        momentum_bad = ret_3m < -20 and rsi < 40 and macd_hist < 0

        # 가격 조작 의심: 거래량 폭증 + 급락
        manipulation_signal = vol_ratio > 300 and ret_1w < -10

    except Exception as e:
        print(f"  [{ticker}] 기술적 지표 오류: {e}")
        return None

    # ── 점수 계산 ─────────────────────────────────
    score    = 0
    reasons  = []
    warnings = []

    if per:
        if per <= 8:    score += 30; reasons.append(f"PER {per:.1f}배 — 업종 평균 대비 매우 저렴한 수준이에요")
        elif per <= 12: score += 22; reasons.append(f"PER {per:.1f}배 — 적정 수준보다 저렴해요")
        elif per <= 15: score += 15; reasons.append(f"PER {per:.1f}배 — 합리적인 가격 수준이에요")
        elif per <= 20: score += 7;  warnings.append(f"PER {per:.1f}배 — 약간 비싼 편이에요")
        else:           score -= 5;  warnings.append(f"PER {per:.1f}배 — 현재 주가가 비싼 편이에요")
    else:
        warnings.append("PER 정보 없음 — 수익성 확인 필요")

    if pbr:
        if pbr <= 0.8:   score += 25; reasons.append(f"PBR {pbr:.2f}배 — 회사 자산보다 싸게 살 수 있어요")
        elif pbr <= 1.2: score += 18; reasons.append(f"PBR {pbr:.2f}배 — 자산 대비 저렴하게 거래 중이에요")
        elif pbr <= 1.5: score += 10; reasons.append(f"PBR {pbr:.2f}배 — 적정 수준이에요")
        else:            warnings.append(f"PBR {pbr:.2f}배 — 자산 대비 다소 비쌀 수 있어요")

    if roe >= 15:   score += 15; reasons.append(f"ROE {roe}% — 돈을 매우 잘 버는 회사예요")
    elif roe >= 10: score += 10; reasons.append(f"ROE {roe}% — 꾸준히 수익을 내는 안정적인 회사예요")
    elif roe >= 5:  score += 5
    elif roe > 0:   warnings.append(f"ROE {roe}% — 수익성이 낮은 편이에요")

    if div >= 4:   score += 10; reasons.append(f"배당수익률 {div}% — 은행 이자보다 훨씬 높은 배당을 줘요")
    elif div >= 2: score += 6;  reasons.append(f"배당수익률 {div}% — 안정적인 배당이 있어요")
    elif div >= 1: score += 3

    if not (is_kr and _kis.available()):
        if debt > 200:   score -= 10; warnings.append(f"부채비율 {debt}% — 부채가 많은 편이에요")
        elif debt > 100: warnings.append(f"부채비율 {debt}% — 부채 수준을 주시할 필요 있어요")
        elif debt <= 50: score += 5;  reasons.append(f"부채비율 {debt}% — 재무 건전성이 매우 좋아요")

    if rsi < 30:   score += 15; reasons.append(f"RSI {rsi} — 과매도 구간이에요. 반등 가능성이 높아요")
    elif rsi < 45: score += 10; reasons.append(f"RSI {rsi} — 저점 매수 구간이에요")
    elif rsi > 70: score -= 10; warnings.append(f"RSI {rsi} — 과매수 구간이에요. 단기 조정 가능성 있어요")

    if macd_cross: score += 8; reasons.append("MACD 골든크로스 — 상승 전환 신호가 포착됐어요")

    if bb_pct < 20:   score += 10; reasons.append(f"볼린저밴드 하단 근처 ({bb_pct}%) — 통계적으로 반등 가능성이 높아요")
    elif bb_pct > 80: warnings.append(f"볼린저밴드 상단 근처 ({bb_pct}%) — 단기 과열 구간이에요")

    if pct_from_low <= 10:   score += 12; reasons.append(f"52주 최저가 대비 +{pct_from_low}% — 역사적 저점 근처예요")
    elif pct_from_low <= 20: score += 6;  reasons.append(f"52주 최저가 대비 +{pct_from_low}% — 저점 구간에 있어요")

    if pct_from_high < -30: score += 5; reasons.append(f"52주 최고가 대비 {pct_from_high}% — 고점 대비 많이 빠진 상태예요")

    if vol_ratio >= 200:   score += 10; reasons.append(f"거래량 평균 대비 {vol_ratio:.0f}% — 강한 매수세가 들어오고 있어요")
    elif vol_ratio >= 150: score += 6;  reasons.append(f"거래량 평균 대비 {vol_ratio:.0f}% — 평소보다 거래가 활발해요")
    elif vol_ratio < 50:   warnings.append("거래량이 매우 적어요 — 유동성 위험이 있어요")

    if ret_1m < -15:  score -= 8; warnings.append(f"최근 1달 {ret_1m}% 하락 — 하락 추세 주의")
    elif ret_1m < -5: score += 3; reasons.append(f"최근 1달 {ret_1m}% 조정 — 눌림목 매수 기회일 수 있어요")

    if sr["near_support"]:
        score += 8; reasons.append(f"지지선({sr['support']:,}) 근처 — 반등 가능성 높은 매수 타이밍이에요")
    if sr["near_resistance"]:
        score -= 5; warnings.append(f"저항선({sr['resistance']:,}) 근처 — 돌파 확인 후 매수 권장")

    if momentum_bad:
        score -= 15
        warnings.append("모멘텀 약화 — 3개월 하락+RSI 침체. 추세 반전 확인 후 진입 권장")

    if manipulation_signal:
        score -= 20
        warnings.append("⚠️ 가격 조작 의심 — 거래량 급등+급락 패턴. 즉시 접근 금지")

    # 섹터 가중치
    sector_bonus = 0
    if sector in ["조선", "방산", "원전", "전력", "바이오"]:
        sector_bonus = 15; reasons.append(f"{sector} 섹터 — {SECTOR_DESC.get(sector, '')}")
    elif sector in ["신재생", "리츠", "소비재"]:
        sector_bonus = 8;  reasons.append(f"{sector} 섹터 — {SECTOR_DESC.get(sector, '')} (장기 보유 추천)")
    elif sector in ["금융", "해운", "에너지"]:
        sector_bonus = 10; reasons.append(f"{sector} 섹터 — {SECTOR_DESC.get(sector, '')}")
    score += sector_bonus

    # 외국인/기관 수급 (KIS API frgn/orgn_ntby_tr_pbmn 단위: 백만원 → ÷100 = 억원)
    foreign_eok = foreign_net / 1e2
    inst_eok    = inst_net    / 1e2
    if is_kr and _kis.available():
        if foreign_eok >= 50:    score += 10; reasons.append(f"외국인 순매수 +{foreign_eok:.0f}억원 — 강한 외국인 매수세")
        elif foreign_eok >= 10:  score += 5;  reasons.append(f"외국인 순매수 +{foreign_eok:.0f}억원")
        elif foreign_eok <= -50: score -= 8;  warnings.append(f"외국인 순매도 {foreign_eok:.0f}억원 — 외국인 이탈 주의")

        if inst_eok >= 50:    score += 8;  reasons.append(f"기관 순매수 +{inst_eok:.0f}억원 — 기관 집중 매수")
        elif inst_eok >= 10:  score += 4;  reasons.append(f"기관 순매수 +{inst_eok:.0f}억원")
        elif inst_eok <= -50: score -= 5;  warnings.append(f"기관 순매도 {inst_eok:.0f}억원")

    # DART 공시
    dart_fin  = {}
    dart_sigs = {}
    if dart_data:
        dart_fin  = dart_data.get("financials", {})
        dart_sigs = dart_data.get("signals", {})

        if dart_sigs.get("rights"):
            score -= 15; warnings.append("유상증자 결정 공시 감지 — 주가 희석 우려, 단기 하락 리스크")
        if dart_sigs.get("buyback"):
            score += 10; reasons.append("자사주 매입 결정 공시 — 주주 가치 제고 호재 신호")
        if dart_sigs.get("order"):
            bonus = 15 if sector == "조선" else 10
            score += bonus
            n_ord = len(dart_sigs["order"])
            reasons.append(
                f"신규 수주 공시 {n_ord}건 감지"
                + (" — 조선주 핵심 호재" if sector == "조선" else "")
            )
        if dart_sigs.get("dividend"):
            score += 5; reasons.append("배당 결정 공시 — 주주환원 신호")
        if dart_sigs.get("insider"):
            score -= 10; warnings.append("내부자 지분 변동 공시 — 임원 매도 가능성, 추가 확인 필요")

        if dart_fin.get("revenue_yoy", 0) >= 15:
            score += 8; reasons.append(f"매출 전년 대비 +{dart_fin['revenue_yoy']}% 성장 — 실적 개선 추세")
        if dart_fin.get("op_income_yoy", 0) >= 20:
            score += 6; reasons.append(f"영업이익 전년 대비 +{dart_fin['op_income_yoy']}% 성장")

        # 재무 악화 감지
        if dart_fin.get("revenue_yoy", 0) <= -10:
            score -= 10; warnings.append(f"매출 전년 대비 {dart_fin['revenue_yoy']}% 감소 — 재무 악화 주의")
        if dart_fin.get("net_income", 0) < 0:
            score -= 8; warnings.append("적자 기업 — 흑자 전환 확인 후 진입 권장")

    # ── 뉴스 감성 ───────────────────────────────
    news = {}
    if with_sentiment:
        news = get_news_sentiment(name)
        if news["sentiment"] == "긍정":
            score += 5; reasons.append(f"뉴스 감성 긍정 (점수 {news['score']}) — 긍정 기사 우세")
        elif news["sentiment"] == "부정":
            score -= 8; warnings.append(f"뉴스 감성 부정 (점수 {news['score']}) — 부정 기사 주의")

    # ── 리스크 등급 ──────────────────────────────
    if score >= 80 and len(warnings) <= 1:
        risk = "🟢 낮음"; risk_desc = "안정적인 투자 기회예요"
    elif score >= 60:
        risk = "🟡 중간"; risk_desc = "적정 리스크 수준이에요"
    else:
        risk = "🔴 높음"; risk_desc = "신중하게 접근하세요"

    # ── 매수 시그널 (Yes/No) ─────────────────────
    buy_signal = (
        score >= 60
        and rsi < 65
        and not manipulation_signal
        and not momentum_bad
        and not dart_sigs.get("rights")
        and not sr["near_resistance"]
    )
    if buy_signal:
        if sr["near_support"] and rsi < 45:
            buy_reason = "지지선+과매도 = 최적 진입 타이밍"
        elif macd_cross and rsi < 55:
            buy_reason = "MACD 반전+RSI 적정 = 추세 전환 신호"
        elif pct_from_low <= 15:
            buy_reason = "52주 저점 근처 = 역사적 저점 매수 기회"
        else:
            buy_reason = "종합 점수 양호"
    else:
        buy_reason = ""

    # ── 매매가 계산 ──────────────────────────────
    buy_price  = round(price * 0.99)
    # 동적 손절: ATR 기준 vs 고정 7% 중 더 보수적인 값
    stop_price = min(round(price * (1 - STOP_LOSS_PCT)), dynamic_stop)
    target1    = round(price * (1 + TARGET1_PCT))
    target2    = round(price * (1 + TARGET2_PCT))
    target3    = round(price * (1 + TARGET3_PCT))

    shares      = int(INVEST_PER_STOCK / buy_price)
    invest_real = shares * buy_price
    profit1     = shares * (target1 - buy_price)
    profit2     = shares * (target2 - buy_price)
    profit3     = shares * (target3 - buy_price)
    loss_amt    = shares * (buy_price - stop_price)
    split1      = int(shares * 0.5)
    split2_price = round(price * 0.95)
    split2      = shares - split1

    if period == "단기":
        period_strategy = f"1차 목표가({target1:,}) 도달 시 전량 매도 권장. 손절은 빠르게."
    elif period == "중기":
        period_strategy = f"1차({target1:,})에서 절반 매도, 나머지는 2차 목표({target2:,}) 대기."
    else:
        period_strategy = f"1~2차 목표에서 일부만 매도, 나머지는 장기 보유. 배당도 챙기세요."

    return {
        "ticker": ticker, "name": name, "period": period, "sector": sector,
        "price": price, "change": change, "currency": currency,
        "per": per, "pbr": pbr, "roe": roe, "div": div, "debt": debt,
        "low52": low52, "high52": high52,
        "pct_from_low": pct_from_low, "pct_from_high": pct_from_high,
        "rsi": rsi, "macd_cross": macd_cross, "bb_pct": bb_pct,
        "vol_ratio": vol_ratio, "ret_1w": ret_1w, "ret_1m": ret_1m, "ret_3m": ret_3m,
        "win_rate": win_rate, "score": score, "risk": risk, "risk_desc": risk_desc,
        "reasons": reasons, "warnings": warnings,
        "buy_signal": buy_signal, "buy_reason": buy_reason,
        "buy_price": buy_price, "stop_price": stop_price,
        "dynamic_stop": dynamic_stop, "dynamic_stop_pct": dynamic_stop_pct,
        "target1": target1, "target2": target2, "target3": target3,
        "shares": shares, "invest_real": invest_real,
        "profit1": profit1, "profit2": profit2, "profit3": profit3,
        "loss_amt": loss_amt,
        "split1_shares": split1, "split2_price": split2_price, "split2_shares": split2,
        "period_strategy": period_strategy,
        "mktcap": mktcap, "revenue": revenue,
        "foreign_net": foreign_net, "inst_net": inst_net,
        "foreign_eok": foreign_eok, "inst_eok": inst_eok,
        "is_kr_kis": is_kr and _kis.available(),
        "inv_ok": is_kr and _kis.available() and inv_ok,
        "dart_financials": dart_fin, "dart_signals": dart_sigs,
        "support": sr["support"], "resistance": sr["resistance"],
        "ma20": sr["ma20"], "ma60": sr["ma60"],
        "atr": round(atr_val),
        "news": news,
    }


# ════════════════════════════════════════════════
# Claude AI 분석
# ════════════════════════════════════════════════
_ai_client = None
_AI_SYSTEM = (
    "당신은 한국 주식 시장 전문 AI 투자 분석가입니다. "
    "데이터를 바탕으로 간결하고 핵심적인 분석을 제공합니다. "
    "섹터별 트렌드, 매크로 환경, 수급 동향을 종합적으로 고려합니다. "
    "투자 조언은 참고용임을 명심하고, 불확실성을 솔직하게 표현합니다. "
    "한국어로 답변하며, 핵심만 간결하게 작성합니다."
)


def _get_ai_client():
    global _ai_client
    if _ai_client is None and _ANTHROPIC_OK and ANTHROPIC_API_KEY:
        _ai_client = _ant_lib.Anthropic(api_key=ANTHROPIC_API_KEY)
    return _ai_client


def ai_market_summary(mood: dict, kr_top: list, us_top: list, fg: dict) -> str:
    """AI 시장 종합 판단 (오늘 사도 되는 시장인지)"""
    client = _get_ai_client()
    if not client:
        return ""
    try:
        kr_names = ", ".join(s["name"] for s in kr_top[:3])
        us_names = ", ".join(s["name"] for s in us_top[:3])
        usdkrw   = mood.get("usdkrw", 1300)
        fx_note  = "고환율(수출주 유리)" if usdkrw >= 1380 else ("저환율(내수주 유리)" if usdkrw <= 1250 else "환율 중립")
        vix_note = "방어주 중심 권장" if mood.get("vix", 20) >= 30 else ("성장주 접근 가능" if mood.get("vix", 20) <= 18 else "균형 전략")

        prompt = (
            f"오늘의 시장 데이터:\n"
            f"- 코스피: {mood['kospi_price']:,.0f} ({mood['kospi_chg']:+.2f}%)\n"
            f"- S&P500: {mood['sp500_chg']:+.2f}%\n"
            f"- VIX: {mood['vix']} → {vix_note}\n"
            f"- 달러/원: {mood['usdkrw']:,.0f} → {fx_note}\n"
            f"- WTI: ${mood['wti']} / 금: ${mood['gold']:,.0f}\n"
            f"- 공포탐욕지수: {fg['score']} ({fg['label']})\n"
            f"- 국내 TOP3: {kr_names}\n"
            f"- 해외 TOP3: {us_names}\n\n"
            "다음 세 가지를 각각 한 문장으로 답해주세요:\n"
            "1. 오늘 주식을 사도 되는 시장인가? (YES/NO + 한줄 이유)\n"
            "2. 오늘 주목해야 할 섹터와 그 이유\n"
            "3. 오늘 가장 중요한 리스크 요인"
        )
        resp = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=400,
            system=_AI_SYSTEM,
            messages=[{"role": "user", "content": prompt}],
        )
        return resp.content[0].text.strip()
    except Exception as e:
        print(f"  [AI] 시장 요약 실패: {e}")
        return ""


def ai_sector_rotation(mood: dict) -> str:
    """섹터 로테이션 분석"""
    client = _get_ai_client()
    if not client:
        return ""
    try:
        usdkrw = mood.get("usdkrw", 1300)
        vix    = mood.get("vix", 20)
        wti    = mood.get("wti", 75)
        prompt = (
            f"현재 매크로 환경:\n"
            f"- 달러/원 환율: {usdkrw:,.0f}원 (1380↑=수출유리, 1250↓=내수유리)\n"
            f"- VIX: {vix} (30↑=방어주, 18↓=성장주)\n"
            f"- WTI 유가: ${wti} (80↑=에너지, 60↓=소비재)\n"
            f"- 코스피: {mood['kospi_chg']:+.2f}%\n\n"
            "오늘 유망한 섹터 2개와 피해야 할 섹터 1개를 선택하고, 각각 이유를 1문장으로.\n"
            "형식: 유망: [섹터1] - 이유 / [섹터2] - 이유 | 주의: [섹터] - 이유"
        )
        resp = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=200,
            system=_AI_SYSTEM,
            messages=[{"role": "user", "content": prompt}],
        )
        return resp.content[0].text.strip()
    except Exception as e:
        print(f"  [AI] 섹터 분석 실패: {e}")
        return ""


def ai_us_macro_impact(macro: dict, mood: dict) -> str:
    """미국 경제지표 → 한국 주식 영향 AI 분석"""
    client = _get_ai_client()
    if not client:
        return ""
    try:
        tnx_str = f"{macro['tnx']:.3f}%" if macro["tnx"] else "N/A"
        irx_str = f"{macro['irx']:.3f}%" if macro["irx"] else "N/A"
        dxy_str = f"{macro['dxy']:.2f}"  if macro["dxy"] else "N/A"
        cpi_str = (
            f"전년비 {macro['cpi_yoy']:+.2f}%, 전월비 {macro['cpi_mom']:+.2f}% ({macro['cpi_month']})"
            if macro["cpi_yoy"] is not None else "N/A"
        )
        spd_str = f"{macro['yield_spread']:+.3f}%p" if macro["yield_spread"] is not None else "N/A"

        prompt = (
            f"미국 경제지표:\n"
            f"- 10년물 국채금리: {tnx_str} (3개월 전: {macro.get('tnx_prev') or 'N/A'}%)\n"
            f"- 단기금리(2년물 근사): {irx_str} (3개월 전: {macro.get('irx_prev') or 'N/A'}%)\n"
            f"- 달러인덱스(DXY): {dxy_str} (3개월 전: {macro.get('dxy_prev') or 'N/A'})\n"
            f"- CPI 소비자물가: {cpi_str}\n"
            f"- 연준 기준금리 방향: {macro['fed_direction']} — {macro['fed_note']}\n"
            f"- 장단기 금리차(10Y-단기): {spd_str}\n"
            f"- 달러/원 환율: {mood.get('usdkrw', 1300):,.0f}원\n\n"
            "위 지표가 오늘 한국 주식시장에 미치는 영향을 아래 3가지로 각각 1문장씩 분석:\n"
            "1. 금리·달러 환경이 수출주(조선·방산·반도체)에 미치는 영향\n"
            "2. 현재 매크로 환경에서 주목할 한국 섹터\n"
            "3. 오늘 가장 주의해야 할 매크로 리스크"
        )
        resp = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=350,
            system=_AI_SYSTEM,
            messages=[{"role": "user", "content": prompt}],
        )
        return resp.content[0].text.strip()
    except Exception as e:
        print(f"  [AI] 매크로 분석 실패: {e}")
        return ""


def ai_stock_insight(s: dict) -> str:
    """개별 종목 AI 한줄 인사이트"""
    client = _get_ai_client()
    if not client:
        return ""
    try:
        prompt = (
            f"{s['name']} ({s['sector']}) 핵심 분석:\n"
            f"RSI {s['rsi']} / 볼린저 {s['bb_pct']}% / 1달 {s['ret_1m']:+.1f}%\n"
            f"매수시그널: {'YES' if s['buy_signal'] else 'NO'} / 점수: {s['score']}점\n"
            f"주요신호: {'; '.join(s['reasons'][:2])}\n"
            "이 종목에 대한 핵심 인사이트를 50자 이내 한 문장으로."
        )
        resp = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=100,
            system=_AI_SYSTEM,
            messages=[{"role": "user", "content": prompt}],
        )
        return resp.content[0].text.strip()
    except Exception:
        return ""


def ai_answer_query(query: str, kr_results: list, us_results: list, mood: dict) -> str:
    """텔레그램 자연어 질의 → AI 응답"""
    client = _get_ai_client()
    if not client:
        return "AI 기능을 사용하려면 ANTHROPIC_API_KEY를 설정해주세요."
    try:
        all_stocks = kr_results + us_results
        q_clean    = re.sub(r"[어때사도될까\?？\s]", "", query)
        relevant   = [
            s for s in all_stocks
            if q_clean in s["name"].replace(" ", "")
            or q_clean.lower() in s["ticker"].lower()
        ]
        if relevant:
            s = relevant[0]
            context = (
                f"종목: {s['name']} ({s['ticker']})\n"
                f"가격: {s['price']:,} ({s['change']:+.2f}%)\n"
                f"RSI: {s['rsi']} | MACD: {'골든크로스' if s['macd_cross'] else '데드크로스'} | 볼린저: {s['bb_pct']}%\n"
                f"매수시그널: {'YES' if s['buy_signal'] else 'NO'} ({s.get('buy_reason', '')})\n"
                f"추천이유: {'; '.join(s['reasons'][:3])}\n"
                f"주의사항: {'; '.join(s['warnings'][:2]) if s['warnings'] else '없음'}\n"
                f"목표가: {s['target1']:,} / 손절가: {s['stop_price']:,}\n"
                f"점수: {s['score']}점 / 리스크: {s['risk']}"
            )
        else:
            context = (
                f"시장상태: {mood['status']} / VIX: {mood['vix']} "
                f"/ 코스피: {mood['kospi_chg']:+.2f}%"
            )

        resp = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=400,
            system=_AI_SYSTEM,
            messages=[{
                "role": "user",
                "content": (
                    f"질문: {query}\n\n데이터:\n{context}\n\n"
                    "위 데이터를 바탕으로 질문에 답변해주세요. "
                    "친근하고 이해하기 쉽게, 200자 이내로."
                ),
            }],
        )
        return resp.content[0].text.strip()
    except Exception as e:
        return f"분석 중 오류가 발생했습니다: {e}"


# ════════════════════════════════════════════════
# 성과 추적
# ════════════════════════════════════════════════
def load_performance() -> dict:
    try:
        if os.path.exists(PERFORMANCE_FILE):
            with open(PERFORMANCE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return {"monthly": {}, "recommendations": []}


def save_performance(data: dict):
    try:
        with open(PERFORMANCE_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"  [성과] 저장 실패: {e}")


def record_recommendations(kr_top: list, us_top: list):
    perf  = load_performance()
    today = datetime.now().strftime("%Y-%m-%d")
    month = datetime.now().strftime("%Y-%m")
    recs  = [r for r in perf.get("recommendations", []) if r.get("date") != today]
    for s in (kr_top + us_top)[:10]:
        recs.append({
            "date":       today,
            "month":      month,
            "ticker":     s["ticker"],
            "name":       s["name"],
            "price":      s["price"],
            "score":      s["score"],
            "buy_signal": s.get("buy_signal", False),
            "result":     None,
        })
    perf["recommendations"] = recs[-300:]
    perf.setdefault("monthly", {}).setdefault(month, {"total": 0, "wins": 0})
    save_performance(perf)


def make_monthly_report() -> str:
    perf       = load_performance()
    last_month = (datetime.now().replace(day=1) - timedelta(days=1)).strftime("%Y-%m")
    recs       = [r for r in perf.get("recommendations", []) if r.get("month") == last_month]
    if not recs:
        return ""
    total = len(recs)
    wins  = sum(1 for r in recs if r.get("result") and r["result"] > 0)
    rate  = round(wins / total * 100) if total else 0
    lines = [
        f"<b>📊 {last_month} 월간 성과 리포트</b>",
        f"추천 종목 수: {total}개 / 수익 달성: {wins}개 ({rate}%)",
        "",
        "<b>추천 종목 목록:</b>",
    ]
    for r in recs[:10]:
        result_str = (
            f"+{r['result']:.1f}%" if r.get("result") and r["result"] > 0
            else (f"{r['result']:.1f}%" if r.get("result") else "미집계")
        )
        lines.append(f"• {r['name']} — {result_str}")
    return "\n".join(lines)


# ════════════════════════════════════════════════
# 보유종목 알림
# ════════════════════════════════════════════════
def check_holdings_alerts() -> list:
    alerts = []
    if not HOLDINGS:
        return alerts
    for h in HOLDINGS:
        code      = h.get("code", "")
        name      = h.get("name", code)
        qty       = h.get("qty", 0)
        avg_price = h.get("avg_price", 0)
        if not (code and qty and avg_price):
            continue
        try:
            if _kis.available():
                pi         = _kis.get_price(code)
                curr_price = _safe_float(pi.get("stck_prpr")) if pi else 0
            else:
                curr_price = float(
                    yf.Ticker(f"{code}.KS").info.get("regularMarketPrice", 0)
                )
            if curr_price <= 0:
                continue
            pct    = (curr_price - avg_price) / avg_price * 100
            profit = qty * (curr_price - avg_price)
            entry  = {
                "name": name, "code": code, "type": "정상",
                "pct": pct, "curr_price": curr_price,
                "avg_price": avg_price, "profit": profit,
            }
            if pct <= -7:
                entry["type"] = "손절"
                entry["msg"]  = f"🚨 {name} 손절 경고! {pct:.1f}% ({avg_price:,}→{curr_price:,})"
            elif pct >= 20:
                entry["type"] = "목표2"
                entry["msg"]  = f"🎯🎯 {name} 2차 목표 달성! +{pct:.1f}%"
            elif pct >= 10:
                entry["type"] = "목표1"
                entry["msg"]  = f"🎯 {name} 1차 목표 달성! +{pct:.1f}%"
            alerts.append(entry)
        except Exception as e:
            print(f"  [보유종목] {name} 조회 오류: {e}")
    return alerts


# ════════════════════════════════════════════════
# HTML 카드 생성
# ════════════════════════════════════════════════
def card_html(rank: int, s: dict, ai_insight: str = "") -> str:
    medals  = ["🥇", "🥈", "🥉"]
    medal   = medals[rank] if rank < 3 else f"{rank + 1}위"
    cur     = s["currency"]
    chg_col = "#e03131" if s["change"] >= 0 else "#1971c2"
    chg_arr = "▲" if s["change"] >= 0 else "▼"

    period_badge = {
        "단기": ("#fff0f6", "#c2255c", "단기 1~4주"),
        "중기": ("#e8f4fd", "#1971c2", "중기 1~6개월"),
        "장기": ("#ebfbee", "#2f9e44", "장기 1년+"),
    }.get(s["period"], ("#f8f9fa", "#495057", s["period"]))

    # 매수 시그널 배지
    if s.get("buy_signal"):
        sig_html = (
            f'<div style="margin:10px 0;padding:10px 16px;background:#d3f9d8;'
            f'border-radius:8px;border-left:4px solid #2f9e44;font-size:14px;font-weight:700;color:#2f9e44;">'
            f'✅ 매수 YES — {s.get("buy_reason","")}</div>'
        )
    else:
        sig_html = (
            f'<div style="margin:10px 0;padding:10px 16px;background:#fff5f5;'
            f'border-radius:8px;border-left:4px solid #e03131;font-size:13px;color:#e03131;">'
            f'❌ 매수 NO — 지금은 관망 권장</div>'
        )

    # AI 인사이트
    ai_html = ""
    if ai_insight:
        ai_html = (
            f'<div style="margin:10px 0;padding:10px 14px;background:#f3f0ff;'
            f'border-radius:8px;border-left:4px solid #7950f2;font-size:13px;color:#5f3dc4;">'
            f'🤖 AI: {ai_insight}</div>'
        )

    reason_html = "".join(
        f'<li style="margin:5px 0;padding:6px 10px;background:#f8fffe;'
        f'border-left:3px solid #20c997;border-radius:0 6px 6px 0;font-size:13px;">'
        f'✅ {r}</li>'
        for r in s["reasons"]
    )
    warn_html = ""
    if s["warnings"]:
        warn_items = "".join(
            f'<li style="margin:5px 0;padding:6px 10px;background:#fff9f0;'
            f'border-left:3px solid #f76707;border-radius:0 6px 6px 0;font-size:13px;">'
            f'⚠️ {w}</li>'
            for w in s["warnings"]
        )
        warn_html = (
            f'<div style="margin-top:14px;">'
            f'<div style="font-weight:600;font-size:13px;color:#e67700;margin-bottom:6px;">주의사항</div>'
            f'<ul style="margin:0;padding-left:0;list-style:none;">{warn_items}</ul>'
            f'</div>'
        )

    if cur == "KRW":
        price_str = f"{s['price']:,.0f}원";   buy_str   = f"{s['buy_price']:,.0f}원"
        stop_str  = f"{s['stop_price']:,.0f}원"; t1_str  = f"{s['target1']:,.0f}원"
        t2_str    = f"{s['target2']:,.0f}원";   t3_str   = f"{s['target3']:,.0f}원"
        low_str   = f"{s['low52']:,.0f}원";     high_str = f"{s['high52']:,.0f}원"
        sp2_str   = f"{s['split2_price']:,.0f}원"
        sup_str   = f"{s.get('support', 0):,.0f}원"
        res_str   = f"{s.get('resistance', 0):,.0f}원"
    else:
        price_str = f"${s['price']:,.2f}";     buy_str   = f"${s['buy_price']:,.2f}"
        stop_str  = f"${s['stop_price']:,.2f}"; t1_str   = f"${s['target1']:,.2f}"
        t2_str    = f"${s['target2']:,.2f}";    t3_str   = f"${s['target3']:,.2f}"
        low_str   = f"${s['low52']:,.2f}";      high_str = f"${s['high52']:,.2f}"
        sp2_str   = f"${s['split2_price']:,.2f}"
        sup_str   = f"${s.get('support', 0):,.2f}"
        res_str   = f"${s.get('resistance', 0):,.2f}"

    mktcap_str = (
        f"{s['mktcap']/1e12:.1f}조원" if cur == "KRW" and s["mktcap"] else
        f"${s['mktcap']/1e9:.1f}B"   if s["mktcap"] else "정보없음"
    )

    # DART 재무제표
    dart_fin_html = ""
    fin = s.get("dart_financials", {})
    if fin and "revenue" in fin:
        year_str  = f"{fin['year']}년 사업보고서"
        rev_str   = _fmt_krw(fin["revenue"])
        op_str    = _fmt_krw(fin["op_income"])  if "op_income"  in fin else "-"
        net_str   = _fmt_krw(fin["net_income"]) if "net_income" in fin else "-"
        op_margin = (
            f"{fin['op_income'] / fin['revenue'] * 100:.1f}%"
            if "op_income" in fin and fin["revenue"] else "-"
        )
        yoy_str = ""
        if "revenue_yoy" in fin:
            yoy_col = "#2f9e44" if fin["revenue_yoy"] >= 0 else "#e03131"
            yoy_str = f' <span style="color:{yoy_col};font-size:12px;">({fin["revenue_yoy"]:+.1f}% YoY)</span>'
        dart_fin_html = (
            f'<div style="margin-bottom:16px;padding:14px;background:#f0f8ff;border-radius:10px;'
            f'border-left:4px solid #0066cc;">'
            f'<div style="font-weight:700;font-size:14px;color:#0066cc;margin-bottom:10px;">'
            f'🏛️ DART 공식 재무제표 ({year_str})</div>'
            f'<table style="width:100%;font-size:13px;border-collapse:collapse;">'
            f'<tr><td style="padding:5px 0;color:#868e96;width:25%;">매출액</td>'
            f'<td style="padding:5px 0;font-weight:600;">{rev_str}{yoy_str}</td>'
            f'<td style="padding:5px 0;color:#868e96;width:25%;">영업이익</td>'
            f'<td style="padding:5px 0;font-weight:600;">{op_str}</td></tr>'
            f'<tr><td style="padding:5px 0;color:#868e96;">순이익</td>'
            f'<td style="padding:5px 0;font-weight:600;">{net_str}</td>'
            f'<td style="padding:5px 0;color:#868e96;">영업이익률</td>'
            f'<td style="padding:5px 0;font-weight:600;">{op_margin}</td></tr>'
            f'</table></div>'
        )

    # DART 공시 알림
    dart_sig_html = ""
    sigs = s.get("dart_signals", {})
    sig_items_html = []
    for key, (_, label, is_risk) in SIGNAL_DEFS.items():
        items = sigs.get(key, [])
        if not items:
            continue
        color = "#e03131" if is_risk else "#2f9e44"
        bg    = "#fff5f5" if is_risk else "#f0fff4"
        icon  = "⚠️" if is_risk else "✅"
        for it in items[:2]:
            dt       = it["date"]
            date_fmt = f"{dt[:4]}.{dt[4:6]}.{dt[6:]}" if len(dt) == 8 else dt
            sig_items_html.append(
                f'<li style="margin:5px 0;padding:8px 12px;background:{bg};'
                f'border-left:3px solid {color};border-radius:0 6px 6px 0;font-size:13px;">'
                f'{icon} <b>[{label}]</b> {it["title"]} '
                f'<span style="color:#868e96;font-size:12px;">({date_fmt})</span> '
                f'<a href="{it["url"]}" style="color:{color};font-size:12px;">공시 보기 →</a>'
                f'</li>'
            )
    if sig_items_html:
        dart_sig_html = (
            f'<div style="margin-top:14px;padding:14px;background:#fffbf0;border-radius:10px;'
            f'border-left:4px solid #f59f00;">'
            f'<div style="font-weight:700;font-size:14px;color:#e67700;margin-bottom:8px;">'
            f'📢 DART 공시 알림 (최근 7일)</div>'
            f'<ul style="margin:0;padding-left:0;list-style:none;">{"".join(sig_items_html)}</ul>'
            f'</div>'
        )

    # 외국인/기관 수급
    investor_html = ""
    if s.get("is_kr_kis"):
        inv_ok = s.get("inv_ok", False)
        f_eok  = s.get("foreign_eok", 0.0)
        i_eok  = s.get("inst_eok",    0.0)
        if inv_ok:
            f_col = "#e03131" if f_eok >= 0 else "#1971c2"
            i_col = "#e03131" if i_eok >= 0 else "#1971c2"
            f_str = f"{'▲' if f_eok >= 0 else '▼'} {abs(f_eok):.1f}억원"
            i_str = f"{'▲' if i_eok >= 0 else '▼'} {abs(i_eok):.1f}억원"
        else:
            f_col = i_col = "#868e96"
            f_str = i_str = "조회불가"
        investor_html = (
            f'<div style="margin-bottom:16px;padding:14px;background:#f0f4ff;border-radius:10px;'
            f'border-left:4px solid #364fc7;">'
            f'<div style="font-weight:700;font-size:14px;color:#364fc7;margin-bottom:10px;">'
            f'👥 외국인/기관 순매수 (당일 · KIS)</div>'
            f'<table style="width:100%;font-size:13px;border-collapse:collapse;">'
            f'<tr><td style="padding:5px 0;color:#868e96;width:25%;">외국인</td>'
            f'<td style="padding:5px 0;font-weight:700;color:{f_col};">{f_str}</td>'
            f'<td style="padding:5px 0;color:#868e96;width:25%;">기관</td>'
            f'<td style="padding:5px 0;font-weight:700;color:{i_col};">{i_str}</td>'
            f'</tr></table></div>'
        )

    # 뉴스 감성
    news_html = ""
    news = s.get("news", {})
    if news and news.get("titles"):
        sent_col = "#2f9e44" if news["sentiment"] == "긍정" else ("#e03131" if news["sentiment"] == "부정" else "#868e96")
        news_html = (
            f'<div style="margin-bottom:16px;padding:12px 14px;background:#f8f9fa;border-radius:10px;'
            f'border-left:4px solid {sent_col};">'
            f'<div style="font-weight:700;font-size:13px;color:{sent_col};margin-bottom:6px;">'
            f'📰 뉴스 감성: {news["sentiment"]} (점수 {news["score"]})</div>'
            + "".join(
                f'<div style="font-size:12px;color:#495057;margin:3px 0;">• {t}</div>'
                for t in news["titles"][:2]
            )
            + '</div>'
        )

    return f"""
<div style="margin:0 0 28px;border:1px solid #dee2e6;border-radius:14px;
            overflow:hidden;font-family:Apple SD Gothic Neo,맑은 고딕,sans-serif;">
  <div style="background:linear-gradient(135deg,#1a3a5c,#2d6a9f);color:white;padding:16px 20px;">
    <div style="display:flex;justify-content:space-between;align-items:flex-start;">
      <div>
        <span style="font-size:22px;font-weight:700;">{medal} {s['name']}</span>
        <span style="margin-left:10px;padding:3px 10px;border-radius:20px;font-size:12px;
                     background:{period_badge[0]};color:{period_badge[1]};">{period_badge[2]}</span>
        <span style="margin-left:6px;padding:3px 10px;border-radius:20px;font-size:12px;
                     background:rgba(255,255,255,0.2);color:white;">{s['sector']}</span>
      </div>
      <div style="text-align:right;font-size:12px;opacity:0.85;">{s['ticker']}</div>
    </div>
    <div style="margin-top:10px;font-size:26px;font-weight:700;">
      {price_str}
      <span style="font-size:15px;margin-left:10px;color:{'#74c0fc' if s['change']<0 else '#ffd700'};">
        {chg_arr} {abs(s['change']):.2f}%
      </span>
    </div>
    <div style="margin-top:6px;font-size:13px;opacity:0.8;">
      시가총액 {mktcap_str} &nbsp;|&nbsp; 52주 범위: {low_str} ~ {high_str}
    </div>
  </div>

  <div style="padding:12px 20px;background:#f0fff4;border-bottom:1px solid #dee2e6;">
    {sig_html}
    {ai_html}
  </div>

  <div style="display:flex;background:#f8f9fa;border-bottom:1px solid #dee2e6;">
    <div style="flex:1;padding:14px 20px;text-align:center;border-right:1px solid #dee2e6;">
      <div style="font-size:12px;color:#868e96;">종합점수</div>
      <div style="font-size:28px;font-weight:700;color:#1a3a5c;">{s['score']}점</div>
      <div style="font-size:12px;color:#868e96;">과거 유사패턴 승률 {s['win_rate']}%</div>
    </div>
    <div style="flex:1;padding:14px 20px;text-align:center;border-right:1px solid #dee2e6;">
      <div style="font-size:12px;color:#868e96;">리스크 등급</div>
      <div style="font-size:18px;font-weight:700;margin:4px 0;">{s['risk']}</div>
      <div style="font-size:12px;color:#868e96;">{s['risk_desc']}</div>
    </div>
    <div style="flex:1;padding:14px 20px;text-align:center;">
      <div style="font-size:12px;color:#868e96;">최근 수익률</div>
      <div style="font-size:13px;margin-top:4px;">
        1주: <b style="color:{'#e03131' if s['ret_1w']>=0 else '#1971c2'}">{'+' if s['ret_1w']>=0 else ''}{s['ret_1w']}%</b><br>
        1달: <b style="color:{'#e03131' if s['ret_1m']>=0 else '#1971c2'}">{'+' if s['ret_1m']>=0 else ''}{s['ret_1m']}%</b><br>
        3달: <b style="color:{'#e03131' if s['ret_3m']>=0 else '#1971c2'}">{'+' if s['ret_3m']>=0 else ''}{s['ret_3m']}%</b>
      </div>
    </div>
  </div>

  <div style="padding:18px 20px;">
    <div style="margin-bottom:16px;">
      <div style="font-weight:700;font-size:15px;color:#1a3a5c;margin-bottom:8px;">이 종목을 추천하는 이유</div>
      <ul style="margin:0;padding-left:0;list-style:none;">{reason_html}</ul>
      {warn_html}
    </div>

    <div style="margin-bottom:16px;padding:14px;background:#f8f9fa;border-radius:10px;">
      <div style="font-weight:700;font-size:14px;color:#1a3a5c;margin-bottom:10px;">기술적 지표</div>
      <table style="width:100%;font-size:13px;border-collapse:collapse;">
        <tr>
          <td style="padding:4px 0;color:#868e96;width:30%;">RSI</td>
          <td style="padding:4px 0;font-weight:600;color:{'#2f9e44' if s['rsi']<45 else '#e67700' if s['rsi']>70 else '#1a1a1a'};">
            {s['rsi']} {'← 매수구간' if s['rsi']<45 else '← 과열주의' if s['rsi']>70 else ''}</td>
          <td style="padding:4px 0;color:#868e96;width:30%;">MACD</td>
          <td style="padding:4px 0;font-weight:600;color:{'#2f9e44' if s['macd_cross'] else '#868e96'};">
            {'골든크로스 ✓' if s['macd_cross'] else '데드크로스'}</td>
        </tr>
        <tr>
          <td style="padding:4px 0;color:#868e96;">볼린저밴드</td>
          <td style="padding:4px 0;font-weight:600;">{s['bb_pct']}% {'← 하단' if s['bb_pct']<20 else '← 상단' if s['bb_pct']>80 else ''}</td>
          <td style="padding:4px 0;color:#868e96;">거래량</td>
          <td style="padding:4px 0;font-weight:600;">평균 대비 {s['vol_ratio']:.0f}%</td>
        </tr>
        <tr>
          <td style="padding:4px 0;color:#868e96;">지지선</td>
          <td style="padding:4px 0;font-weight:600;color:#2f9e44;">{sup_str}</td>
          <td style="padding:4px 0;color:#868e96;">저항선</td>
          <td style="padding:4px 0;font-weight:600;color:#e03131;">{res_str}</td>
        </tr>
        <tr>
          <td style="padding:4px 0;color:#868e96;">52주 저점 대비</td>
          <td style="padding:4px 0;font-weight:600;color:{'#2f9e44' if s['pct_from_low']<15 else '#1a1a1a'};">+{s['pct_from_low']}%</td>
          <td style="padding:4px 0;color:#868e96;">ATR 손절</td>
          <td style="padding:4px 0;font-weight:600;color:#e03131;">{s.get('dynamic_stop_pct',7):.1f}%</td>
        </tr>
      </table>
    </div>

    {dart_fin_html}
    {news_html}
    {investor_html}

    <div style="margin-bottom:16px;border:2px solid #1a3a5c;border-radius:10px;overflow:hidden;">
      <div style="background:#1a3a5c;color:white;padding:10px 16px;font-weight:700;font-size:14px;">
        💰 200만원 투자 시뮬레이션
      </div>
      <div style="padding:14px 16px;">
        <table style="width:100%;font-size:13px;border-collapse:collapse;">
          <tr style="background:#e7f5ff;">
            <td style="padding:8px;font-weight:600;">매수가 (지정가)</td>
            <td style="padding:8px;font-weight:700;color:#1971c2;font-size:15px;">{buy_str}</td>
            <td style="padding:8px;">매수 수량</td>
            <td style="padding:8px;font-weight:700;">{s['shares']}주</td>
          </tr>
          <tr>
            <td style="padding:8px;color:#2f9e44;font-weight:600;">✅ 1차 목표 (+10%)</td>
            <td style="padding:8px;font-weight:700;color:#2f9e44;">{t1_str}</td>
            <td style="padding:8px;">예상 수익</td>
            <td style="padding:8px;font-weight:700;color:#2f9e44;">+{s['profit1']:,.0f}원</td>
          </tr>
          <tr style="background:#f8f9fa;">
            <td style="padding:8px;color:#1971c2;font-weight:600;">✅ 2차 목표 (+20%)</td>
            <td style="padding:8px;font-weight:700;color:#1971c2;">{t2_str}</td>
            <td style="padding:8px;">예상 수익</td>
            <td style="padding:8px;font-weight:700;color:#1971c2;">+{s['profit2']:,.0f}원</td>
          </tr>
          <tr>
            <td style="padding:8px;color:#7950f2;font-weight:600;">✅ 장기 목표 (+40%)</td>
            <td style="padding:8px;font-weight:700;color:#7950f2;">{t3_str}</td>
            <td style="padding:8px;">예상 수익</td>
            <td style="padding:8px;font-weight:700;color:#7950f2;">+{s['profit3']:,.0f}원</td>
          </tr>
          <tr style="background:#fff5f5;">
            <td style="padding:8px;color:#e03131;font-weight:600;">🛑 손절가</td>
            <td style="padding:8px;font-weight:700;color:#e03131;">{stop_str}</td>
            <td style="padding:8px;">최대 손실</td>
            <td style="padding:8px;font-weight:700;color:#e03131;">-{s['loss_amt']:,.0f}원</td>
          </tr>
        </table>
      </div>
    </div>

    <div style="margin-bottom:16px;padding:14px;background:#fff9db;border-radius:10px;border-left:4px solid #f59f00;">
      <div style="font-weight:700;font-size:14px;color:#e67700;margin-bottom:8px;">📌 분할매수 전략</div>
      <div style="font-size:13px;line-height:1.8;">
        1차 매수: 지금 바로 <b>{s['split1_shares']}주</b> 매수<br>
        2차 매수: {sp2_str} 이하 추가 <b>{s['split2_shares']}주</b>
      </div>
    </div>

    <div style="padding:14px;background:#f3f0ff;border-radius:10px;border-left:4px solid #7950f2;">
      <div style="font-weight:700;font-size:14px;color:#7950f2;margin-bottom:6px;">📅 {s['period']} 투자 전략</div>
      <div style="font-size:13px;">{s['period_strategy']}</div>
    </div>

    {dart_sig_html}
  </div>
</div>"""


def dart_alerts_section_html(dart_alerts: list) -> str:
    if not dart_alerts:
        return ""
    by_type: dict = {}
    for a in dart_alerts:
        by_type.setdefault(a["key"], []).append(a)
    rows = []
    for key in ["rights", "buyback", "order", "dividend", "insider"]:
        entries = by_type.get(key, [])
        if not entries:
            continue
        _, label, is_risk = SIGNAL_DEFS[key]
        color  = "#e03131" if is_risk else "#2f9e44"
        bg     = "#fff5f5" if is_risk else "#f0fff4"
        border = "#ffc9c9" if is_risk else "#b2f2bb"
        icon   = "⚠️" if is_risk else "✅"
        items_html = ""
        for a in entries:
            for it in a["items"]:
                dt       = it["date"]
                date_fmt = f"{dt[:4]}.{dt[4:6]}.{dt[6:]}" if len(dt) == 8 else dt
                items_html += (
                    f'<div style="padding:9px 14px;margin:5px 0;background:{bg};'
                    f'border:1px solid {border};border-radius:8px;font-size:13px;">'
                    f'<b>{a["name"]}</b>'
                    f'<span style="margin-left:6px;padding:2px 8px;border-radius:12px;'
                    f'font-size:11px;background:rgba(0,0,0,0.07);">{a["sector"]}</span>'
                    f' — {it["title"]} <span style="color:#868e96;">({date_fmt})</span> '
                    f'<a href="{it["url"]}" style="color:{color};font-size:12px;">공시 보기 →</a>'
                    f'</div>'
                )
        rows.append(
            f'<div style="margin-bottom:16px;">'
            f'<div style="font-weight:700;font-size:14px;color:{color};margin-bottom:8px;">'
            f'{icon} {label}</div>{items_html}</div>'
        )
    if not rows:
        return ""
    return (
        f'<div style="padding:20px 16px;">'
        f'<h2 style="color:#1a3a5c;font-size:20px;margin:0 0 16px;'
        f'padding-bottom:10px;border-bottom:3px solid #f59f00;">📢 DART 공시 알림 (최근 7일)</h2>'
        f'<div style="padding:16px 20px;background:white;border-radius:12px;border:1px solid #dee2e6;">'
        f'{"".join(rows)}'
        f'<div style="font-size:12px;color:#868e96;margin-top:8px;">'
        f'* 금융감독원 전자공시시스템(DART) 공시 기준.</div></div></div>'
    )


# ════════════════════════════════════════════════
# HTML 전체 리포트
# ════════════════════════════════════════════════
def _make_macro_html(macro: dict, ai_macro: str) -> str:
    """미국 경제지표 HTML 섹션"""
    if not macro:
        return ""

    def _fmt(val, unit="", fmt=".3f"):
        return f"{val:{fmt}}{unit}" if val is not None else "N/A"

    tnx  = _fmt(macro.get("tnx"), "%")
    irx  = _fmt(macro.get("irx"), "%")
    dxy  = _fmt(macro.get("dxy"), "", ".2f")
    spd  = _fmt(macro.get("yield_spread"), "%p")
    cpi_yoy = f"{macro['cpi_yoy']:+.2f}%" if macro.get("cpi_yoy") is not None else "N/A"
    cpi_mom = f"{macro['cpi_mom']:+.2f}%" if macro.get("cpi_mom") is not None else "N/A"
    cpi_month = macro.get("cpi_month", "")
    fed = macro.get("fed_direction", "확인불가")
    fed_note = macro.get("fed_note", "")

    fed_color = (
        "#e03131" if "인상" in fed else
        "#2f9e44" if "인하" in fed else
        "#e67700"
    )
    spd_val = macro.get("yield_spread")
    spd_color = "#e03131" if (spd_val is not None and spd_val < 0) else "#2f9e44"

    ai_html = ""
    if ai_macro:
        ai_html = (
            f'<div style="margin-top:14px;padding:12px 16px;background:#f3f0ff;'
            f'border-radius:8px;border-left:4px solid #7950f2;font-size:13px;'
            f'color:#5f3dc4;line-height:1.8;white-space:pre-line;">'
            f'🤖 AI 매크로 분석: {ai_macro}</div>'
        )

    return f"""
  <div style="background:#e8f4fd;padding:18px 24px;border-bottom:1px solid #dee2e6;">
    <div style="font-weight:700;font-size:15px;color:#1a3a5c;margin-bottom:12px;">🇺🇸 미국 경제지표 브리핑</div>
    <div style="display:grid;grid-template-columns:repeat(3,1fr);gap:10px;">
      <div style="background:white;padding:12px;border-radius:10px;border:1px solid #dee2e6;text-align:center;">
        <div style="font-size:12px;color:#868e96;">10년물 국채금리</div>
        <div style="font-size:18px;font-weight:700;">{tnx}</div>
        <div style="font-size:11px;color:#868e96;">3개월 전: {_fmt(macro.get('tnx_prev'), '%')}</div>
      </div>
      <div style="background:white;padding:12px;border-radius:10px;border:1px solid #dee2e6;text-align:center;">
        <div style="font-size:12px;color:#868e96;">단기금리(2년물 근사)</div>
        <div style="font-size:18px;font-weight:700;">{irx}</div>
        <div style="font-size:11px;color:#868e96;">3개월 전: {_fmt(macro.get('irx_prev'), '%')}</div>
      </div>
      <div style="background:white;padding:12px;border-radius:10px;border:1px solid #dee2e6;text-align:center;">
        <div style="font-size:12px;color:#868e96;">달러인덱스(DXY)</div>
        <div style="font-size:18px;font-weight:700;">{dxy}</div>
        <div style="font-size:11px;color:#868e96;">3개월 전: {_fmt(macro.get('dxy_prev'), '', '.2f')}</div>
      </div>
      <div style="background:white;padding:12px;border-radius:10px;border:1px solid #dee2e6;text-align:center;">
        <div style="font-size:12px;color:#868e96;">CPI 소비자물가</div>
        <div style="font-size:16px;font-weight:700;">전년비 {cpi_yoy}</div>
        <div style="font-size:11px;color:#868e96;">전월비 {cpi_mom} ({cpi_month})</div>
      </div>
      <div style="background:white;padding:12px;border-radius:10px;border:1px solid #dee2e6;text-align:center;">
        <div style="font-size:12px;color:#868e96;">연준 기준금리 방향</div>
        <div style="font-size:15px;font-weight:700;color:{fed_color};">{fed}</div>
        <div style="font-size:11px;color:#868e96;">{fed_note[:30] if fed_note else ''}</div>
      </div>
      <div style="background:white;padding:12px;border-radius:10px;border:1px solid #dee2e6;text-align:center;">
        <div style="font-size:12px;color:#868e96;">장단기 금리차(10Y-단기)</div>
        <div style="font-size:18px;font-weight:700;color:{spd_color};">{spd}</div>
        <div style="font-size:11px;color:#868e96;">{'역전=경기침체 신호' if (spd_val is not None and spd_val < 0) else '정상 커브'}</div>
      </div>
    </div>
    {ai_html}
  </div>"""


def make_report(
    kr_top: list,
    us_top: list,
    avoid: list,
    mood: dict,
    dart_alerts: list = None,
    ai_summary: str = "",
    ai_sector: str = "",
    fg: dict = None,
    ai_insights: dict = None,
    macro: dict = None,
    ai_macro: str = "",
) -> str:
    today     = datetime.now().strftime("%Y년 %m월 %d일 (%A)")
    now       = datetime.now().strftime("%H:%M")
    fg        = fg or {"score": 50, "label": "중립"}
    ai_insights = ai_insights or {}

    mood_color = {"양호": "#2f9e44", "주의": "#e67700", "하락": "#e67700", "위험": "#e03131"}.get(mood["status"], "#868e96")
    sp500_col  = "#e03131" if mood["sp500_chg"] >= 0 else "#1971c2"
    kos_col    = "#e03131" if mood["kospi_chg"] >= 0 else "#1971c2"

    fg_color = "#e03131" if fg["score"] >= 70 else ("#2f9e44" if fg["score"] <= 30 else "#e67700")

    # AI 요약 섹션
    ai_section = ""
    if ai_summary:
        ai_section = f"""
  <div style="padding:18px 24px;background:#f3f0ff;border-bottom:1px solid #dee2e6;">
    <div style="font-weight:700;font-size:15px;color:#5f3dc4;margin-bottom:10px;">🤖 AI 시장 판단</div>
    <div style="font-size:14px;line-height:1.8;white-space:pre-line;color:#1a1a1a;">{ai_summary}</div>
    {"" if not ai_sector else f'<div style="margin-top:10px;padding:10px 14px;background:white;border-radius:8px;font-size:13px;color:#364fc7;"><b>섹터 로테이션:</b> {ai_sector}</div>'}
  </div>"""

    html = f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>투자 비서 리포트 v6.0 — {today}</title>
</head>
<body>
<div style="font-family:Apple SD Gothic Neo,맑은 고딕,sans-serif;max-width:680px;margin:0 auto;color:#1a1a1a;">

  <div style="background:linear-gradient(135deg,#1a3a5c,#0d2137);color:white;padding:28px 24px;border-radius:16px 16px 0 0;">
    <h1 style="margin:0 0 6px;font-size:24px;">📊 투자 비서 리포트 v6.0</h1>
    <p style="margin:0;opacity:0.8;font-size:14px;">{today} &nbsp;·&nbsp; 분석완료 {now} KST</p>
    <div style="margin-top:14px;padding:12px 16px;background:rgba(255,255,255,0.1);border-radius:10px;font-size:13px;">
      {mood['advice']}
    </div>
  </div>

  {ai_section}

  {_make_macro_html(macro, ai_macro)}

  <div style="background:#f8f9fa;padding:18px 24px;border-bottom:1px solid #dee2e6;">
    <div style="font-weight:700;font-size:15px;color:#1a3a5c;margin-bottom:12px;">🌏 오늘의 시장 브리핑</div>
    <div style="display:grid;grid-template-columns:repeat(3,1fr);gap:10px;">
      <div style="background:white;padding:12px;border-radius:10px;border:1px solid #dee2e6;text-align:center;">
        <div style="font-size:12px;color:#868e96;">코스피</div>
        <div style="font-size:16px;font-weight:700;">{mood['kospi_price']:,.0f}</div>
        <div style="font-size:13px;color:{kos_col};">{'▲' if mood['kospi_chg']>=0 else '▼'} {abs(mood['kospi_chg']):.2f}%</div>
      </div>
      <div style="background:white;padding:12px;border-radius:10px;border:1px solid #dee2e6;text-align:center;">
        <div style="font-size:12px;color:#868e96;">미국 S&P500</div>
        <div style="font-size:16px;font-weight:700;">{'▲' if mood['sp500_chg']>=0 else '▼'} {abs(mood['sp500_chg']):.2f}%</div>
        <div style="font-size:13px;color:{sp500_col};">전일 대비</div>
      </div>
      <div style="background:white;padding:12px;border-radius:10px;border:1px solid #dee2e6;text-align:center;">
        <div style="font-size:12px;color:#868e96;">공포지수 VIX</div>
        <div style="font-size:16px;font-weight:700;">{mood['vix']}</div>
        <div style="font-size:13px;color:{mood_color};">{mood['status']}</div>
      </div>
      <div style="background:white;padding:12px;border-radius:10px;border:1px solid #dee2e6;text-align:center;">
        <div style="font-size:12px;color:#868e96;">달러/원 환율</div>
        <div style="font-size:16px;font-weight:700;">{mood['usdkrw']:,.0f}원</div>
      </div>
      <div style="background:white;padding:12px;border-radius:10px;border:1px solid #dee2e6;text-align:center;">
        <div style="font-size:12px;color:#868e96;">WTI 유가</div>
        <div style="font-size:16px;font-weight:700;">${mood['wti']}</div>
      </div>
      <div style="background:white;padding:12px;border-radius:10px;border:1px solid #dee2e6;text-align:center;">
        <div style="font-size:12px;color:#868e96;">공포탐욕지수</div>
        <div style="font-size:16px;font-weight:700;color:{fg_color};">{fg['score']}</div>
        <div style="font-size:12px;color:{fg_color};">{fg['label']}</div>
      </div>
    </div>
  </div>

  <div style="padding:20px 16px 8px;">
    <h2 style="color:#1a3a5c;font-size:20px;margin:0 0 16px;padding-bottom:10px;border-bottom:3px solid #1a3a5c;">
      🇰🇷 국내 추천 종목 TOP 5
    </h2>
    {"".join(card_html(i, s, ai_insights.get(s['ticker'], "")) for i, s in enumerate(kr_top))}
  </div>

  <div style="padding:20px 16px 8px;">
    <h2 style="color:#1a3a5c;font-size:20px;margin:0 0 16px;padding-bottom:10px;border-bottom:3px solid #e67700;">
      🇺🇸 해외 추천 종목 TOP 5
    </h2>
    {"".join(card_html(i, s, ai_insights.get(s['ticker'], "")) for i, s in enumerate(us_top))}
  </div>
"""

    if avoid:
        avoid_html = "".join(
            f'<div style="padding:10px 14px;margin:6px 0;background:#fff5f5;'
            f'border-left:4px solid #e03131;border-radius:0 8px 8px 0;font-size:13px;">'
            f'<b>{a["name"]} ({a["ticker"]})</b> — '
            f'RSI {a["rsi"]} / 최근1달 {a["ret_1m"]}% / 거래량 {a["vol_ratio"]:.0f}%'
            f'</div>'
            for a in avoid[:5]
        )
        html += f"""
  <div style="padding:16px;margin:0 16px 20px;background:#fff5f5;border-radius:12px;border:1px solid #ffc9c9;">
    <div style="font-weight:700;font-size:15px;color:#e03131;margin-bottom:10px;">🚫 오늘 피해야 할 종목</div>
    {avoid_html}
    <div style="font-size:12px;color:#868e96;margin-top:8px;">* RSI 과매수 / 급락 중 / 거래량 급감 종목. 지금은 관망하세요.</div>
  </div>"""

    if dart_alerts:
        html += dart_alerts_section_html(dart_alerts)

    html += """
  <div style="padding:20px 24px;background:#f8f9fa;border-radius:0 0 16px 16px;font-size:12px;color:#868e96;line-height:2;">
    ⚠️ 본 리포트는 자동 분석된 참고 정보입니다. 최종 투자 판단은 반드시 본인이 직접 하세요.<br>
    투자 손익의 책임은 전적으로 투자자 본인에게 있으며 어떤 수익도 보장하지 않습니다.<br>
    🟢낮음=안정적 | 🟡중간=보통 | 🔴높음=신중 | 단기=1~4주 | 중기=1~6개월 | 장기=1년+
  </div>
</div>
</body>
</html>"""
    return html


# ════════════════════════════════════════════════
# 텔레그램 메시지
# ════════════════════════════════════════════════
def make_telegram_message(
    kr_top: list,
    us_top: list,
    avoid: list,
    mood: dict,
    dart_alerts: list = None,
    ai_summary: str = "",
    fg: dict = None,
    macro: dict = None,
    ai_macro: str = "",
) -> str:
    today  = datetime.now().strftime("%Y년 %m월 %d일")
    now    = datetime.now().strftime("%H:%M")
    medals = ["🥇", "🥈", "🥉", "4위", "5위"]
    fg     = fg or {"score": 50, "label": "중립"}

    kos_arr = "▲" if mood["kospi_chg"] >= 0 else "▼"
    sp5_arr = "▲" if mood["sp500_chg"] >= 0 else "▼"

    lines = [
        "<b>📊 투자 비서 리포트 v6.0</b>",
        f"<i>{today} · {now} KST</i>",
        "",
    ]

    if ai_summary:
        lines += ["<b>🤖 AI 시장 판단</b>", ai_summary, ""]

    # 미국 경제지표 브리핑
    if macro:
        tnx_str = f"{macro['tnx']:.3f}%" if macro.get("tnx") else "N/A"
        irx_str = f"{macro['irx']:.3f}%" if macro.get("irx") else "N/A"
        dxy_str = f"{macro['dxy']:.2f}"  if macro.get("dxy") else "N/A"
        cpi_str = (
            f"전년비 {macro['cpi_yoy']:+.2f}% / 전월비 {macro['cpi_mom']:+.2f}%"
            if macro.get("cpi_yoy") is not None else "N/A"
        )
        fed_str = macro.get("fed_direction", "확인불가")
        lines += [
            "<b>🇺🇸 미국 경제지표</b>",
            f"10년물금리 {tnx_str}  |  단기금리 {irx_str}",
            f"달러인덱스(DXY) {dxy_str}",
            f"CPI: {cpi_str}",
            f"연준 방향: {fed_str}",
        ]
        if ai_macro:
            lines += [f"🤖 {ai_macro}", ""]
        else:
            lines.append("")

    lines += [
        "<b>🌏 시장 브리핑</b>",
        f"코스피 {mood['kospi_price']:,.0f} {kos_arr}{abs(mood['kospi_chg']):.2f}%  |  S&P500 {sp5_arr}{abs(mood['sp500_chg']):.2f}%",
        f"VIX {mood['vix']} ({mood['status']})  |  달러/원 {mood['usdkrw']:,.0f}원",
        f"WTI ${mood['wti']}  |  공포탐욕 {fg['score']} ({fg['label']})",
        mood["advice"],
        "",
        "<b>🇰🇷 국내 추천 TOP 5</b>",
    ]
    for i, s in enumerate(kr_top):
        chg_arr = "▲" if s["change"] >= 0 else "▼"
        buy_tag = " ✅매수YES" if s.get("buy_signal") else " ❌관망"
        lines.append(f"{medals[i]} <b>{s['name']}</b> — {s['score']}점 {s['risk']} ({s['period']}){buy_tag}")
        lines.append(f"   {s['price']:,.0f}원 {chg_arr}{abs(s['change']):.2f}%")
        if s.get("is_kr_kis"):
            if s.get("inv_ok"):
                f_eok = s.get("foreign_eok", 0.0)
                i_eok = s.get("inst_eok",    0.0)
                lines.append(
                    f"   외국인 {'▲' if f_eok>=0 else '▼'}{abs(f_eok):.1f}억"
                    f"  기관 {'▲' if i_eok>=0 else '▼'}{abs(i_eok):.1f}억"
                )
            else:
                lines.append("   외국인/기관 수급 조회불가")

    lines += ["", "<b>🇺🇸 해외 추천 TOP 5</b>"]
    for i, s in enumerate(us_top):
        chg_arr = "▲" if s["change"] >= 0 else "▼"
        buy_tag = " ✅매수YES" if s.get("buy_signal") else " ❌관망"
        lines.append(f"{medals[i]} <b>{s['name']}</b> — {s['score']}점 {s['risk']} ({s['period']}){buy_tag}")
        lines.append(f"   ${s['price']:,.2f} {chg_arr}{abs(s['change']):.2f}%")

    if avoid:
        lines += ["", "<b>🚫 오늘 피해야 할 종목</b>"]
        for a in avoid[:5]:
            lines.append(f"• {a['name']} — RSI {a['rsi']} / 1달 {a['ret_1m']:+.1f}%")

    if dart_alerts:
        lines += ["", "<b>📢 DART 공시 알림</b>"]
        for a in dart_alerts[:5]:
            icon = "⚠️" if a["is_risk"] else "✅"
            lines.append(f"{icon} <b>{a['name']}</b> — {a['label']}: {a['items'][0]['title']}")

    lines += ["", "<i>⚠️ 본 리포트는 참고용입니다. 투자 판단은 본인이 직접 하세요.</i>"]
    return "\n".join(lines)


# ════════════════════════════════════════════════
# 텔레그램 전송
# ════════════════════════════════════════════════
def _tg_base() -> str:
    return f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"


def tg_send(text: str, chat_id: str = ""):
    cid = chat_id or TELEGRAM_CHAT_ID
    if not TELEGRAM_TOKEN or not cid:
        return
    MAX = 4096
    buf = text
    while buf:
        if len(buf) <= MAX:
            chunk, buf = buf, ""
        else:
            cut = buf.rfind("\n\n", 0, MAX)
            if cut == -1:
                cut = MAX
            chunk, buf = buf[:cut], buf[cut:].lstrip()
        try:
            requests.post(
                f"{_tg_base()}/sendMessage",
                json={"chat_id": cid, "text": chunk, "parse_mode": "HTML"},
                timeout=15,
            )
        except Exception:
            pass


def tg_send_document(html: str, caption: str = ""):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return
    stamp = datetime.now().strftime("%m%d_%H%M")
    try:
        r = requests.post(
            f"{_tg_base()}/sendDocument",
            data={
                "chat_id": TELEGRAM_CHAT_ID,
                "caption": caption or f"📊 투자 비서 리포트 ({datetime.now().strftime('%m/%d %H:%M')})",
            },
            files={"document": (f"report_{stamp}.html", html.encode("utf-8"), "text/html")},
            timeout=30,
        )
        if r.ok:
            print("  텔레그램 전송 완료!")
        else:
            print(f"  [텔레그램] 파일 전송 실패: {r.text[:200]}")
    except Exception as e:
        print(f"  [텔레그램] 전송 오류: {e}")


def tg_get_updates(offset: int = 0) -> list:
    try:
        r = requests.get(
            f"{_tg_base()}/getUpdates",
            params={"offset": offset, "timeout": 25, "allowed_updates": ["message"]},
            timeout=35,
        )
        return r.json().get("result", [])
    except Exception:
        return []


# ════════════════════════════════════════════════
# 텔레그램 봇 (인터랙티브 폴링)
# ════════════════════════════════════════════════
def run_bot(kr_results: list, us_results: list, mood: dict,
            kr_top: list, us_top: list, avoid: list,
            dart_alerts: list, ai_summary: str, fg: dict,
            duration_sec: int = 300):
    if not TELEGRAM_TOKEN:
        print("  [봇] TELEGRAM_TOKEN 없음 — 건너뜀")
        return

    print(f"  [봇] 텔레그램 봇 폴링 시작 ({duration_sec}초)")
    offset   = 0
    deadline = time.time() + duration_sec
    all_stocks = kr_results + us_results

    while time.time() < deadline:
        updates = tg_get_updates(offset)
        for upd in updates:
            offset   = upd["update_id"] + 1
            msg      = upd.get("message", {})
            text     = (msg.get("text") or "").strip()
            chat_id  = str(msg.get("chat", {}).get("id", ""))
            if not text:
                continue

            print(f"  [봇] 수신: {text[:60]}")

            if text in ("/start", "/리포트"):
                summary = make_telegram_message(
                    kr_top, us_top, avoid, mood,
                    dart_alerts=dart_alerts, ai_summary=ai_summary, fg=fg
                )
                tg_send(summary, chat_id)

            elif text in ("/도움말", "/help"):
                tg_send(
                    "<b>📌 투자 비서 명령어</b>\n\n"
                    "/리포트 — 오늘 전체 리포트\n"
                    "/보유 — 보유종목 현황\n"
                    "/도움말 — 이 메시지\n\n"
                    "<b>자연어 질의 예시:</b>\n"
                    "현대로템 어때?\n"
                    "삼성중공업 사도 될까?\n"
                    "오늘 방산주 전망은?\n"
                    "지금 시장 어때?",
                    chat_id,
                )

            elif text == "/보유":
                ha = check_holdings_alerts()
                if not ha:
                    tg_send("보유종목이 없거나 HOLDINGS_JSON이 설정되지 않았습니다.", chat_id)
                else:
                    lines = ["<b>📦 보유종목 현황</b>", ""]
                    for a in ha:
                        emoji = "🔴" if a["type"] == "손절" else ("🟢" if "목표" in a["type"] else "⚪")
                        lines.append(
                            f"{emoji} <b>{a['name']}</b>: {a['pct']:+.1f}%"
                            f" ({a['curr_price']:,}원) / 수익 {a['profit']:+,.0f}원"
                        )
                    tg_send("\n".join(lines), chat_id)

            else:
                answer = ai_answer_query(text, kr_results, us_results, mood)
                if not answer:
                    q_clean = re.sub(r"[어때사도될까\?？\s]", "", text)
                    found   = [s for s in all_stocks if q_clean in s["name"].replace(" ", "")]
                    if found:
                        s = found[0]
                        answer = (
                            f"<b>{s['name']}</b> ({s['ticker']})\n"
                            f"현재가: {s['price']:,} ({s['change']:+.2f}%)\n"
                            f"점수: {s['score']}점 / {s['risk']}\n"
                            f"매수시그널: {'✅ YES' if s['buy_signal'] else '❌ NO'}"
                            + (f" — {s['buy_reason']}" if s.get('buy_reason') else "") + "\n"
                            f"목표가: {s['target1']:,} / 손절: {s['stop_price']:,}"
                        )
                    else:
                        answer = "해당 종목을 찾을 수 없습니다. 정확한 종목명을 입력해주세요.\n예: 현대로템, 삼성중공업"
                tg_send(answer, chat_id)

    print("  [봇] 폴링 종료")


# ════════════════════════════════════════════════
# 실시간 모니터링 신호 감지
# ════════════════════════════════════════════════
_sent_alerts: set = set()   # 중복 알림 방지 (세션 내)


def _alert_key(*args) -> str:
    return "|".join(str(a) for a in args)


def _check_monitor_signals(prev_scores: dict) -> list:
    """KR_STOCKS 전체 스캔 → 감지된 신호 목록 반환"""
    signals = []
    now_str = datetime.now().strftime("%Y%m%d%H")

    for ticker, (name, period, sector) in KR_STOCKS.items():
        if not _kis.available():
            break
        code = ticker.split(".")[0]
        try:
            pi = _kis.get_price(code)
            if not pi:
                continue
            price    = _safe_float(pi.get("stck_prpr"))
            change   = _safe_float(pi.get("prdy_ctrt"))
            # 전일 대비 거래량 비율(%): 100=동일, 300=3배 — KIS inquire-price 응답 필드
            vol_rate = _safe_float(pi.get("prdy_vrss_vol_rate"))

            inv      = _kis.get_investor(code)
            f_net    = _safe_float(inv.get("frgn_ntby_tr_pbmn"))   # 외국인 순매수 (백만원)
            i_net    = _safe_float(inv.get("orgn_ntby_tr_pbmn"))    # 기관 순매수 (백만원)
            f_eok    = f_net / 1e2   # 백만원 → 억원
            i_eok    = i_net / 1e2   # 백만원 → 억원

            prev = prev_scores.get(ticker, {})
            prev_score = prev.get("score", 0)

            # 1. 급등 감지: 전일 대비 거래량 300%↑ + 주가 3%↑
            key1 = _alert_key("surge", ticker, now_str)
            if change >= 3.0 and vol_rate >= 300 and key1 not in _sent_alerts:
                _sent_alerts.add(key1)
                signals.append({
                    "type": "surge",
                    "msg": (
                        f"🚀 <b>[급등 감지]</b> {name}\n"
                        f"주가 +{change:.1f}% / 전일 대비 거래량 {vol_rate:.0f}%\n"
                        f"현재가: {price:,.0f}원\n"
                        f"💡 단기 모멘텀 급상승 — 추격 매수 시 손절 철저히"
                    ),
                })

            # 2. 외국인+기관 동시 순매수
            key2 = _alert_key("dual_buy", ticker, now_str)
            if f_eok >= 20 and i_eok >= 20 and key2 not in _sent_alerts:
                _sent_alerts.add(key2)
                signals.append({
                    "type": "dual_buy",
                    "msg": (
                        f"✅ <b>[외국인+기관 동시 매수]</b> {name}\n"
                        f"외국인 +{f_eok:.1f}억 / 기관 +{i_eok:.1f}억\n"
                        f"현재가: {price:,.0f}원 ({change:+.1f}%)\n"
                        f"💡 기관·외국인 동시 매수 = 강한 상승 신호"
                    ),
                })

            # 3. 수급 급변 — 외국인 대량 순매도
            key3 = _alert_key("frgn_sell", ticker, now_str)
            if f_eok <= -100 and key3 not in _sent_alerts:
                _sent_alerts.add(key3)
                signals.append({
                    "type": "frgn_sell",
                    "msg": (
                        f"⚠️ <b>[외국인 대량 매도 경고]</b> {name}\n"
                        f"외국인 순매도 {f_eok:.1f}억원\n"
                        f"현재가: {price:,.0f}원 ({change:+.1f}%)\n"
                        f"🔴 보유 중이라면 손절선 재확인 필요"
                    ),
                })

            # 4. 눌림목 감지 — 고점수 종목 -3% 이상 하락
            key4 = _alert_key("pullback", ticker, now_str)
            if prev_score >= 70 and change <= -3.0 and key4 not in _sent_alerts:
                _sent_alerts.add(key4)
                signals.append({
                    "type": "pullback",
                    "msg": (
                        f"💎 <b>[눌림목 매수 기회]</b> {name} ({sector})\n"
                        f"종합점수 {prev_score}점 우량주 / 오늘 {change:.1f}% 하락\n"
                        f"현재가: {price:,.0f}원\n"
                        f"💡 고점수 우량주 일시 조정 — 분할매수 검토"
                    ),
                })

        except Exception as e:
            print(f"  [모니터] {name} 조회 오류: {e}")
        time.sleep(0.3)

    # 5. 보유종목 손절/목표 감지
    for h in HOLDINGS:
        code      = h.get("code", "")
        name      = h.get("name", code)
        qty       = h.get("qty", 0)
        avg_price = h.get("avg_price", 0)
        if not (code and qty and avg_price):
            continue
        try:
            pi         = _kis.get_price(code) if _kis.available() else {}
            curr_price = _safe_float(pi.get("stck_prpr")) if pi else float(
                yf.Ticker(f"{code}.KS").info.get("regularMarketPrice", 0)
            )
            if curr_price <= 0:
                continue
            pct        = (curr_price - avg_price) / avg_price * 100
            stop_price = avg_price * (1 - STOP_LOSS_PCT)
            target1    = avg_price * (1 + TARGET1_PCT)
            target2    = avg_price * (1 + TARGET2_PCT)

            # 손절가 1% 이내 근접
            key_s = _alert_key("near_stop", code, now_str)
            if curr_price <= stop_price * 1.01 and key_s not in _sent_alerts:
                _sent_alerts.add(key_s)
                signals.append({
                    "type": "near_stop",
                    "msg": (
                        f"🚨 <b>[손절 경고]</b> {name}\n"
                        f"현재가 {curr_price:,.0f}원 / 손절가 {stop_price:,.0f}원\n"
                        f"손실률: {pct:.1f}%\n"
                        f"🔴 <b>지금 매도하세요!</b> 손절가 {stop_price:,.0f}원 도달 임박"
                    ),
                })

            # 1차 목표 도달
            key_t1 = _alert_key("target1", code, now_str[:8])   # 하루에 한 번만
            if curr_price >= target1 and key_t1 not in _sent_alerts:
                _sent_alerts.add(key_t1)
                signals.append({
                    "type": "target1",
                    "msg": (
                        f"🎯 <b>[1차 목표 달성]</b> {name}\n"
                        f"현재가 {curr_price:,.0f}원 (+{pct:.1f}%)\n"
                        f"1차 목표가: {target1:,.0f}원\n"
                        f"💚 <b>지금 매도하세요!</b> — 절반 매도 or 트레일링 스탑 설정 권장"
                    ),
                })

            # 2차 목표 도달
            key_t2 = _alert_key("target2", code, now_str[:8])
            if curr_price >= target2 and key_t2 not in _sent_alerts:
                _sent_alerts.add(key_t2)
                signals.append({
                    "type": "target2",
                    "msg": (
                        f"🎯🎯 <b>[2차 목표 달성]</b> {name}\n"
                        f"현재가 {curr_price:,.0f}원 (+{pct:.1f}%)\n"
                        f"2차 목표가: {target2:,.0f}원\n"
                        f"💚 <b>지금 매도하세요!</b> — 수익 실현 강력 권장"
                    ),
                })

        except Exception as e:
            print(f"  [모니터] 보유종목 {name} 오류: {e}")
        time.sleep(0.2)

    return signals


def run_monitor(duration_hours: float = 7.0, interval_sec: int = 300):
    """장중 실시간 모니터링 루프 (기본: 7시간, 5분 간격)"""
    print(f"[모니터] 실시간 모니터링 시작 — {duration_hours}시간, {interval_sec}초 간격")
    tg_send("📡 <b>실시간 모니터링 시작</b>\n장중 신호 감지 시 즉시 알림을 보내드립니다.")

    deadline    = time.time() + duration_hours * 3600
    prev_scores = {}   # {ticker: {score, price}}

    # 초기 점수 캐시 로드 (일일 리포트 결과가 있으면 활용)
    perf  = load_performance()
    today = datetime.now().strftime("%Y-%m-%d")
    for rec in perf.get("recommendations", []):
        if rec.get("date") == today:
            prev_scores[rec["ticker"]] = {"score": rec.get("score", 0)}

    cycle = 0
    while time.time() < deadline:
        cycle += 1
        now = datetime.now()
        # 장 시간(09:00~15:30) 이외에는 대기
        if not (9 <= now.hour < 15 or (now.hour == 15 and now.minute <= 35)):
            print(f"  [모니터] 장 시간 외 대기 중 ({now.strftime('%H:%M')})")
            time.sleep(interval_sec)
            continue

        print(f"  [모니터] 사이클 {cycle} ({now.strftime('%H:%M')})")
        signals = _check_monitor_signals(prev_scores)

        for sig in signals:
            print(f"  [모니터] 신호: {sig['type']}")
            tg_send(sig["msg"])

        time.sleep(interval_sec)

    tg_send("📡 실시간 모니터링 종료")
    print("[모니터] 종료")


# ════════════════════════════════════════════════
# 브리핑 함수 (스케줄별)
# ════════════════════════════════════════════════
def run_us_briefing():
    """새벽 6시 — 미국 시장 마감 브리핑"""
    print("[브리핑] 미국 시장 마감 브리핑")
    try:
        sp500  = yf.Ticker("^GSPC").history(period="2d")
        nasdaq = yf.Ticker("^IXIC").history(period="2d")
        dow    = yf.Ticker("^DJI").history(period="2d")
        vix    = yf.Ticker("^VIX").info
        usdkrw = yf.Ticker("KRW=X").info
        gold   = yf.Ticker("GC=F").info
        wti    = yf.Ticker("CL=F").info

        def chg(hist):
            if len(hist) >= 2:
                return round((hist["Close"].iloc[-1] - hist["Close"].iloc[-2]) / hist["Close"].iloc[-2] * 100, 2)
            return 0.0

        sp_chg  = chg(sp500)
        nq_chg  = chg(nasdaq)
        dj_chg  = chg(dow)
        vix_val = round(float(vix.get("regularMarketPrice") or 20), 1)
        fx_val  = round(float(usdkrw.get("regularMarketPrice") or 1300), 0)
        gold_v  = round(float(gold.get("regularMarketPrice") or 2000), 0)
        wti_v   = round(float(wti.get("regularMarketPrice") or 75), 1)

        def arr(v): return "▲" if v >= 0 else "▼"

        mood_txt = ""
        if vix_val > 28:
            mood_txt = "⛔ VIX 급등 — 오늘 코스피 하락 가능성 높음. 방어적 접근 권장."
        elif sp_chg <= -1.5:
            mood_txt = "⚠️ 미국 급락 — 오늘 코스피 동조 하락 주의. 신규 매수 자제."
        elif sp_chg >= 1.5:
            mood_txt = "✅ 미국 강세 — 오늘 코스피 상승 출발 예상. 관심 종목 체크."
        else:
            mood_txt = "📊 미국 혼조 — 코스피 횡보 가능성. 개별 종목 대응 집중."

        lines = [
            f"<b>🌙 미국 시장 마감 브리핑</b>",
            f"<i>{datetime.now().strftime('%Y-%m-%d')} 새벽 브리핑</i>",
            "",
            f"S&P500  {arr(sp_chg)}{abs(sp_chg):.2f}%",
            f"나스닥   {arr(nq_chg)}{abs(nq_chg):.2f}%",
            f"다우    {arr(dj_chg)}{abs(dj_chg):.2f}%",
            f"VIX    {vix_val}",
            f"달러/원 {fx_val:,.0f}원",
            f"금      ${gold_v:,.0f}  /  WTI ${wti_v}",
            "",
            mood_txt,
        ]

        # AI 분석 추가
        client = _get_ai_client()
        if client:
            try:
                prompt = (
                    f"미국 증시 마감 데이터: S&P500 {sp_chg:+.2f}%, 나스닥 {nq_chg:+.2f}%, "
                    f"VIX {vix_val}, 달러/원 {fx_val:,.0f}원\n"
                    "오늘 한국 증시 예상과 주목할 섹터를 2문장으로 요약해줘."
                )
                resp = client.messages.create(
                    model="claude-sonnet-4-6",
                    max_tokens=200,
                    system=_AI_SYSTEM,
                    messages=[{"role": "user", "content": prompt}],
                )
                lines += ["", f"🤖 {resp.content[0].text.strip()}"]
            except Exception:
                pass

        tg_send("\n".join(lines))
    except Exception as e:
        print(f"  [브리핑] 미국 브리핑 오류: {e}")
        tg_send(f"⚠️ 미국 시장 브리핑 수집 실패: {e}")


def run_premarket_briefing():
    """8시 50분 — 장 시작 전 10분 브리핑"""
    print("[브리핑] 장 시작 전 브리핑")
    try:
        mood = get_market_mood()
        fg   = get_fear_greed(mood)

        lines = [
            "<b>🔔 장 시작 전 브리핑 (8:50)</b>",
            f"<i>9시 개장 10분 전</i>",
            "",
            f"코스피 야간선물: {mood['kospi_price']:,.0f} ({mood['kospi_chg']:+.2f}%)",
            f"달러/원: {mood['usdkrw']:,.0f}원",
            f"VIX: {mood['vix']} ({mood['status']})",
            f"공포탐욕: {fg['score']} ({fg['label']})",
            "",
            mood["advice"],
            "",
        ]

        # 오늘 주목 종목 (보유종목 + 고점수 종목)
        watch_list = []
        for h in HOLDINGS:
            watch_list.append(f"📦 보유: {h.get('name', h.get('code', ''))}")
        if watch_list:
            lines += ["<b>📋 오늘 체크할 보유종목</b>"] + watch_list[:5]

        client = _get_ai_client()
        if client:
            try:
                prompt = (
                    f"코스피 {mood['kospi_chg']:+.2f}%, VIX {mood['vix']}, "
                    f"달러/원 {mood['usdkrw']:,.0f}원 환경에서 "
                    "오늘 장 초반 전략을 1~2문장으로 알려줘."
                )
                resp = client.messages.create(
                    model="claude-sonnet-4-6",
                    max_tokens=150,
                    system=_AI_SYSTEM,
                    messages=[{"role": "user", "content": prompt}],
                )
                lines += ["", f"🤖 AI: {resp.content[0].text.strip()}"]
            except Exception:
                pass

        tg_send("\n".join(lines))
    except Exception as e:
        print(f"  [브리핑] 장전 브리핑 오류: {e}")
        tg_send(f"⚠️ 장전 브리핑 수집 실패: {e}")


def run_close_summary():
    """3시 35분 — 장 마감 결산"""
    print("[브리핑] 장 마감 결산")
    try:
        mood = get_market_mood()
        fg   = get_fear_greed(mood)

        kos_arr = "▲" if mood["kospi_chg"] >= 0 else "▼"
        lines = [
            "<b>📉 장 마감 결산 (15:35)</b>",
            f"<i>{datetime.now().strftime('%Y-%m-%d')} 오늘의 결산</i>",
            "",
            f"코스피  {mood['kospi_price']:,.0f}  {kos_arr}{abs(mood['kospi_chg']):.2f}%",
            f"달러/원  {mood['usdkrw']:,.0f}원",
            f"VIX     {mood['vix']}  ({mood['status']})",
            f"공포탐욕  {fg['score']} ({fg['label']})",
            "",
        ]

        # 보유종목 오늘 성과
        ha = check_holdings_alerts()
        if ha:
            lines.append("<b>📦 보유종목 오늘 성과</b>")
            for a in ha:
                emoji = "🔴" if a["type"] == "손절" else ("🟢" if "목표" in a["type"] else "⚪")
                lines.append(
                    f"{emoji} {a['name']}: {a['pct']:+.1f}%"
                    f" / 수익 {a['profit']:+,.0f}원"
                )
            lines.append("")

        client = _get_ai_client()
        if client:
            try:
                prompt = (
                    f"오늘 코스피 {mood['kospi_chg']:+.2f}%, VIX {mood['vix']} 마감.\n"
                    "내일 장 전망과 주목할 포인트를 2문장으로 요약해줘."
                )
                resp = client.messages.create(
                    model="claude-sonnet-4-6",
                    max_tokens=200,
                    system=_AI_SYSTEM,
                    messages=[{"role": "user", "content": prompt}],
                )
                lines += [f"🤖 내일 전망: {resp.content[0].text.strip()}"]
            except Exception:
                pass

        tg_send("\n".join(lines))
    except Exception as e:
        print(f"  [브리핑] 마감 결산 오류: {e}")
        tg_send(f"⚠️ 마감 결산 수집 실패: {e}")


# ════════════════════════════════════════════════
# 종목 비교 / 투자 시뮬레이션 (텔레그램 명령)
# ════════════════════════════════════════════════
def compare_stocks(name_a: str, name_b: str, all_stocks: list) -> str:
    """두 종목 비교 분석"""
    def find(q):
        q = q.strip().replace(" ", "")
        return next((s for s in all_stocks if q in s["name"].replace(" ", "") or q.lower() == s["ticker"].lower()), None)

    a = find(name_a)
    b = find(name_b)

    if not a or not b:
        missing = name_a if not a else name_b
        return f"'{missing}' 종목을 찾을 수 없습니다."

    def row(label, va, vb, higher_better=True):
        if isinstance(va, float) and isinstance(vb, float):
            winner = "◀" if (va > vb) == higher_better else ("▶" if va != vb else "")
            return f"{label}: <b>{va}</b> {winner}  |  <b>{vb}</b>"
        return f"{label}: {va}  |  {vb}"

    lines = [
        f"<b>⚖️ 종목 비교</b>",
        f"<b>{a['name']}</b>  vs  <b>{b['name']}</b>",
        "",
        row("종합점수",  float(a['score']),   float(b['score'])),
        row("RSI",      float(a['rsi']),      float(b['rsi']),     False),
        row("1달 수익률", float(a['ret_1m']),  float(b['ret_1m'])),
        row("3달 수익률", float(a['ret_3m']),  float(b['ret_3m'])),
        row("볼린저(%)",  float(a['bb_pct']),  float(b['bb_pct']),  False),
        row("배당(%)",   float(a['div']),     float(b['div'])),
        "",
        f"{a['name']} 매수시그널: {'✅ YES' if a.get('buy_signal') else '❌ NO'}",
        f"{b['name']} 매수시그널: {'✅ YES' if b.get('buy_signal') else '❌ NO'}",
        "",
    ]

    winner = a if a["score"] > b["score"] else b
    loser  = b if a["score"] > b["score"] else a
    lines.append(f"🏆 <b>종합 우위: {winner['name']}</b> ({winner['score']}점 vs {loser['score']}점)")
    if winner.get("buy_signal"):
        lines.append(f"💡 <b>지금 매수하세요!</b> — {winner['buy_reason']}")

    client = _get_ai_client()
    if client:
        try:
            prompt = (
                f"{a['name']}(점수:{a['score']}, RSI:{a['rsi']}, 1달:{a['ret_1m']:+.1f}%)와 "
                f"{b['name']}(점수:{b['score']}, RSI:{b['rsi']}, 1달:{b['ret_1m']:+.1f}%) 비교.\n"
                "어느 종목이 지금 더 유리한지 이유와 함께 1~2문장으로."
            )
            resp = client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=200,
                system=_AI_SYSTEM,
                messages=[{"role": "user", "content": prompt}],
            )
            lines += ["", f"🤖 AI: {resp.content[0].text.strip()}"]
        except Exception:
            pass

    return "\n".join(lines)


def simulate_investment(name: str, amount: int, all_stocks: list) -> str:
    """투자 금액 시뮬레이션"""
    q = name.strip().replace(" ", "")
    s = next((st for st in all_stocks if q in st["name"].replace(" ", "") or q.lower() == st["ticker"].lower()), None)
    if not s:
        return f"'{name}' 종목을 찾을 수 없습니다."

    price    = s["price"]
    currency = s["currency"]
    shares   = int(amount / price)
    if shares == 0:
        return f"{name}의 현재가({price:,.0f})가 투자금액({amount:,}원)보다 높아 1주도 살 수 없습니다."

    invest_real = shares * price
    fmt = (lambda v: f"{v:,.0f}원") if currency == "KRW" else (lambda v: f"${v:,.2f}")

    t1 = shares * price * TARGET1_PCT
    t2 = shares * price * TARGET2_PCT
    t3 = shares * price * TARGET3_PCT
    sl = shares * price * STOP_LOSS_PCT

    buy_tag = ""
    if s.get("buy_signal"):
        buy_tag = f"\n💚 <b>지금 매수하세요!</b> — {s.get('buy_reason', '')}"
    else:
        buy_tag = "\n⚪ 현재 매수 신호 없음 — 관망 권장"

    return "\n".join([
        f"<b>💰 투자 시뮬레이션 — {s['name']}</b>",
        f"투자금액: {amount:,}원  /  매수가: {fmt(price)}  /  수량: {shares}주",
        f"실제 투자금: {invest_real:,}원",
        buy_tag,
        "",
        f"📈 단기 수익 (+{TARGET1_PCT*100:.0f}%): <b>+{t1:,.0f}원</b>",
        f"📈 중기 수익 (+{TARGET2_PCT*100:.0f}%): <b>+{t2:,.0f}원</b>",
        f"📈 장기 수익 (+{TARGET3_PCT*100:.0f}%): <b>+{t3:,.0f}원</b>",
        f"🛑 손절 손실 (-{STOP_LOSS_PCT*100:.0f}%): <b>-{sl:,.0f}원</b>",
        "",
        f"분할매수: 1차 {shares//2}주 / 2차 {shares-shares//2}주 ({fmt(price*0.95)} 이하)",
        f"리스크 등급: {s['risk']}",
    ])


# ════════════════════════════════════════════════
# 텔레그램 봇 (인터랙티브 폴링) — 명령어 확장
# ════════════════════════════════════════════════
def run_bot_extended(kr_results: list, us_results: list, mood: dict,
                     kr_top: list, us_top: list, avoid: list,
                     dart_alerts: list, ai_summary: str, fg: dict,
                     duration_sec: int = 300):
    if not TELEGRAM_TOKEN:
        print("  [봇] TELEGRAM_TOKEN 없음 — 건너뜀")
        return

    print(f"  [봇] 텔레그램 봇 폴링 ({duration_sec}초)")
    offset     = 0
    deadline   = time.time() + duration_sec
    all_stocks = kr_results + us_results

    while time.time() < deadline:
        updates = tg_get_updates(offset)
        for upd in updates:
            offset  = upd["update_id"] + 1
            msg     = upd.get("message", {})
            text    = (msg.get("text") or "").strip()
            chat_id = str(msg.get("chat", {}).get("id", ""))
            if not text:
                continue

            print(f"  [봇] 수신: {text[:60]}")

            # ── 기본 명령어 ─────────────────────
            if text in ("/start", "/리포트"):
                summary = make_telegram_message(
                    kr_top, us_top, avoid, mood,
                    dart_alerts=dart_alerts, ai_summary=ai_summary, fg=fg,
                )
                tg_send(summary, chat_id)
                continue

            if text in ("/도움말", "/help"):
                tg_send(
                    "<b>📌 투자 비서 v6.0 명령어</b>\n\n"
                    "<b>기본</b>\n"
                    "/리포트 — 오늘 전체 리포트\n"
                    "/보유 — 보유종목 현황\n"
                    "/도움말 — 이 메시지\n\n"
                    "<b>종목 분석</b>\n"
                    "현대로템 어때? — AI 종목 분석\n"
                    "현대로템 vs 한화에어로스페이스 — 두 종목 비교\n\n"
                    "<b>투자 시뮬레이션</b>\n"
                    "현대로템 300만원 — 투자 시뮬레이션\n"
                    "삼성중공업 500 — 500만원 시뮬레이션\n\n"
                    "<b>시장</b>\n"
                    "지금 시장 어때? — 시장 현황 분석\n"
                    "오늘 뭐 사? — 오늘의 추천 종목",
                    chat_id,
                )
                continue

            if text == "/보유":
                ha = check_holdings_alerts()
                if not ha:
                    tg_send("보유종목이 없거나 HOLDINGS_JSON이 설정되지 않았습니다.", chat_id)
                else:
                    lines = ["<b>📦 보유종목 현황</b>", ""]
                    for a in ha:
                        emoji = "🔴" if a["type"] == "손절" else ("🟢" if "목표" in a["type"] else "⚪")
                        alert = ""
                        if a["type"] == "손절":
                            alert = "\n   🔴 <b>지금 매도하세요!</b>"
                        elif "목표" in a["type"]:
                            alert = f"\n   💚 <b>지금 매도하세요!</b> ({a['type']} 달성)"
                        lines.append(
                            f"{emoji} <b>{a['name']}</b>: {a['pct']:+.1f}%"
                            f" ({a['curr_price']:,}원) / 수익 {a['profit']:+,.0f}원{alert}"
                        )
                    tg_send("\n".join(lines), chat_id)
                continue

            # ── 종목 비교: A vs B ────────────────
            vs_match = re.match(r"^(.+?)\s+vs\s+(.+)$", text, re.IGNORECASE)
            if vs_match:
                result = compare_stocks(vs_match.group(1), vs_match.group(2), all_stocks)
                tg_send(result, chat_id)
                continue

            # ── 투자 시뮬레이션: 종목명 + 금액 ──
            sim_match = re.match(r"^(.+?)\s+(\d+)(?:만원?|만)?$", text)
            if sim_match:
                stock_name = sim_match.group(1).strip()
                amount_man = int(sim_match.group(2))
                amount     = amount_man * 10_000
                result = simulate_investment(stock_name, amount, all_stocks)
                tg_send(result, chat_id)
                continue

            # ── 오늘 추천 ────────────────────────
            if any(kw in text for kw in ["뭐 사", "뭐사", "추천", "오늘 추천"]):
                kr_buy = [s for s in kr_top if s.get("buy_signal")]
                us_buy = [s for s in us_top if s.get("buy_signal")]

                if "국내" in text:
                    pool   = kr_buy[:5]
                    header = "🇰🇷 국내 매수 신호 종목"
                    flag   = "kr_only"
                elif "해외" in text:
                    pool   = us_buy[:5]
                    header = "🇺🇸 해외 매수 신호 종목"
                    flag   = "us_only"
                else:
                    pool   = kr_buy[:3] + us_buy[:3]
                    header = "💚 오늘 매수 신호 종목 (국내 3 + 해외 3)"
                    flag   = "both"

                if pool:
                    reply = [f"<b>{header}</b>", ""]
                    for s in pool:
                        cur     = s["currency"]
                        p_str   = f"{s['price']:,.0f}원" if cur == "KRW" else f"${s['price']:,.2f}"
                        t1_str  = f"{s['target1']:,.0f}원" if cur == "KRW" else f"${s['target1']:,.2f}"
                        sl_str  = f"{s['stop_price']:,.0f}원" if cur == "KRW" else f"${s['stop_price']:,.2f}"
                        reply.append(
                            f"✅ <b>{s['name']}</b> ({s['score']}점/{s['period']}) — {s.get('buy_reason', '')}\n"
                            f"   현재가: {p_str} / 목표: {t1_str} / 손절: {sl_str}"
                        )
                    tg_send("\n".join(reply), chat_id)
                else:
                    scope = "국내" if flag == "kr_only" else ("해외" if flag == "us_only" else "")
                    tg_send(f"오늘은 {scope} 명확한 매수 신호 종목이 없습니다. 관망을 권장합니다.", chat_id)
                continue

            # ── 자연어 질의 → AI 응답 ────────────
            answer = ai_answer_query(text, kr_results, us_results, mood)
            if not answer:
                q_clean = re.sub(r"[어때사도될까\?？\s]", "", text)
                found   = [s for s in all_stocks if q_clean in s["name"].replace(" ", "")]
                if found:
                    s = found[0]
                    buy_line = (
                        f"💚 <b>지금 매수하세요!</b> — {s['buy_reason']}"
                        if s.get("buy_signal")
                        else "⚪ 현재 매수 신호 없음 — 관망 권장"
                    )
                    answer = (
                        f"<b>{s['name']}</b> ({s['ticker']})\n"
                        f"현재가: {s['price']:,} ({s['change']:+.2f}%)\n"
                        f"점수: {s['score']}점 / {s['risk']}\n"
                        f"{buy_line}\n"
                        f"목표: {s['target1']:,} / 손절: {s['stop_price']:,}"
                    )
                else:
                    answer = (
                        "해당 종목을 찾을 수 없습니다.\n"
                        "예: 현대로템 어때? / 현대로템 vs 삼성중공업 / 현대로템 300만원"
                    )
            tg_send(answer, chat_id)

    print("  [봇] 폴링 종료")


# ════════════════════════════════════════════════
# pykrx 시장 전체 스캔
# ════════════════════════════════════════════════
def fetch_top_market_stocks(n: int = MARKET_SCAN_N) -> list:
    """pykrx로 코스피+코스닥 시가총액 상위 n개 종목 리스트 반환 [(code, name, mkt), ...]"""
    if not _PYKRX_OK:
        print("  [pykrx] 미설치 — 시장 스캔 불가")
        return []

    date_obj = datetime.now() - timedelta(days=1)
    date_str = ""
    for _ in range(7):
        date_str = date_obj.strftime("%Y%m%d")
        try:
            df_test = _pykrx.get_market_cap_by_ticker(date_str, market="KOSPI")
            if df_test is not None and not df_test.empty:
                break
        except Exception:
            pass
        date_obj -= timedelta(days=1)
    else:
        print("  [pykrx] 시가총액 데이터 없음")
        return []

    try:
        df_kospi  = _pykrx.get_market_cap_by_ticker(date_str, market="KOSPI")
        df_kosdaq = _pykrx.get_market_cap_by_ticker(date_str, market="KOSDAQ")
    except Exception as e:
        print(f"  [pykrx] 시가총액 조회 오류: {e}")
        return []

    kospi_set = set(df_kospi.index.tolist()) if df_kospi is not None else set()
    frames = [f for f in [df_kospi, df_kosdaq] if f is not None and not f.empty]
    if not frames:
        return []
    df_all = pd.concat(frames, axis=0)
    df_all = df_all[df_all["시가총액"] > 0].sort_values("시가총액", ascending=False).head(n)

    result = []
    for code in df_all.index:
        try:
            name = _pykrx.get_market_ticker_name(code)
            mkt  = "KOSPI" if code in kospi_set else "KOSDAQ"
            result.append((code, name, mkt))
        except Exception:
            continue

    print(f"  [pykrx] 시가총액 상위 {len(result)}종목 로드 (기준일: {date_str})")
    return result


def analyze_market_stock(code: str, name: str, mkt: str) -> dict:
    """pykrx OHLCV + 펀더멘털로 종목 분석 (KIS/DART/감성 없는 빠른 버전)"""
    try:
        end_obj   = datetime.now()
        start_obj = end_obj - timedelta(days=190)
        start_str = start_obj.strftime("%Y%m%d")
        end_str   = end_obj.strftime("%Y%m%d")

        df = _pykrx.get_market_ohlcv_by_date(start_str, end_str, code)
        if df is None or len(df) < 20:
            return None

        close  = df["종가"].astype(float)
        volume = df["거래량"].astype(float)
        high_s = df["고가"].astype(float)
        low_s  = df["저가"].astype(float)

        price = float(close.iloc[-1])
        if price <= 0:
            return None

        # 유동성 필터: 거래대금 5억 미만 제외
        if "거래대금" in df.columns:
            last_amt = float(df["거래대금"].iloc[-1])
            if 0 < last_amt < 500_000_000:
                return None

        prev   = float(close.iloc[-2]) if len(close) >= 2 else price
        change = round((price - prev) / prev * 100, 2) if prev else 0

        low52  = float(close.min())
        high52 = float(close.max())
        pct_from_low  = round((price - low52)  / low52  * 100, 1) if low52  else 0
        pct_from_high = round((price - high52) / high52 * 100, 1) if high52 else 0

        # 기술적 지표
        delta = close.diff()
        gain  = delta.clip(lower=0).rolling(14).mean()
        loss  = (-delta.clip(upper=0)).rolling(14).mean()
        rsi   = round(float((100 - 100 / (1 + gain / loss.replace(0, 1e-9))).iloc[-1]), 1)

        ema12       = close.ewm(span=12).mean()
        ema26       = close.ewm(span=26).mean()
        macd_line   = ema12 - ema26
        signal_line = macd_line.ewm(span=9).mean()
        macd_cross  = float(macd_line.iloc[-1]) > float(signal_line.iloc[-1])
        macd_hist   = float(macd_line.iloc[-1]) - float(signal_line.iloc[-1])

        sma20    = close.rolling(20).mean()
        std20    = close.rolling(20).std()
        bb_upper = sma20 + 2 * std20
        bb_lower = sma20 - 2 * std20
        bb_pct   = round(
            (float(close.iloc[-1]) - float(bb_lower.iloc[-1]))
            / (float(bb_upper.iloc[-1]) - float(bb_lower.iloc[-1]) + 1e-9) * 100, 1
        )

        avg_vol   = float(volume.rolling(20).mean().iloc[-1])
        last_vol  = float(volume.iloc[-1])
        vol_ratio = round(last_vol / avg_vol * 100, 0) if avg_vol else 100

        n_rows = len(close)
        ret_1w = round((float(close.iloc[-1]) - float(close.iloc[-5]))  / float(close.iloc[-5])  * 100, 1) if n_rows >= 5  else 0
        ret_1m = round((float(close.iloc[-1]) - float(close.iloc[-20])) / float(close.iloc[-20]) * 100, 1) if n_rows >= 20 else 0
        ret_3m = round((float(close.iloc[-1]) - float(close.iloc[0]))   / float(close.iloc[0])   * 100, 1)

        atr_val = calc_atr(close, high_s, low_s)
        sr      = calc_support_resistance(close)

        momentum_bad        = ret_3m < -20 and rsi < 40 and macd_hist < 0
        manipulation_signal = vol_ratio > 300 and ret_1w < -10

        # 펀더멘털 (pykrx)
        per = pbr = None
        div = roe = mktcap = 0.0
        try:
            fund_df = _pykrx.get_market_fundamental_by_ticker(end_str, market=mkt)
            if fund_df is not None and code in fund_df.index:
                row = fund_df.loc[code]
                per = float(row.get("PER", 0) or 0) or None
                pbr = float(row.get("PBR", 0) or 0) or None
                div = float(row.get("DIV", 0) or 0)
                eps = float(row.get("EPS", 0) or 0)
                bps = float(row.get("BPS", 0) or 0)
                roe = round(eps / bps * 100, 1) if bps > 0 else 0.0
        except Exception:
            pass

        try:
            cap_df = _pykrx.get_market_cap_by_ticker(end_str, market=mkt)
            if cap_df is not None and code in cap_df.index:
                mktcap = float(cap_df.loc[code, "시가총액"])
        except Exception:
            pass

        # 점수 계산
        score    = 0
        reasons  = []
        warnings = []

        if per:
            if per <= 8:    score += 30; reasons.append(f"PER {per:.1f}배 — 매우 저렴")
            elif per <= 12: score += 22; reasons.append(f"PER {per:.1f}배 — 저렴")
            elif per <= 15: score += 15; reasons.append(f"PER {per:.1f}배 — 적정")
            elif per <= 20: score += 7;  warnings.append(f"PER {per:.1f}배 — 약간 비쌈")
            else:           score -= 5;  warnings.append(f"PER {per:.1f}배 — 비쌈")
        else:
            warnings.append("PER 없음")

        if pbr:
            if pbr <= 0.8:   score += 25; reasons.append(f"PBR {pbr:.2f}배 — 자산 대비 저렴")
            elif pbr <= 1.2: score += 18; reasons.append(f"PBR {pbr:.2f}배 — 자산 대비 적정")
            elif pbr <= 1.5: score += 10; reasons.append(f"PBR {pbr:.2f}배 — 적정")
            else:            warnings.append(f"PBR {pbr:.2f}배 — 자산 대비 비쌈")

        if roe >= 15:   score += 15; reasons.append(f"ROE {roe}% — 수익성 우수")
        elif roe >= 10: score += 10; reasons.append(f"ROE {roe}% — 수익성 양호")
        elif roe >= 5:  score += 5
        elif roe > 0:   warnings.append(f"ROE {roe}% — 수익성 낮음")

        if div >= 4:   score += 10; reasons.append(f"배당수익률 {div}% — 고배당")
        elif div >= 2: score += 6;  reasons.append(f"배당수익률 {div}% — 안정 배당")
        elif div >= 1: score += 3

        if rsi < 30:   score += 15; reasons.append(f"RSI {rsi} — 과매도 반등 가능")
        elif rsi < 45: score += 10; reasons.append(f"RSI {rsi} — 저점 매수 구간")
        elif rsi > 70: score -= 10; warnings.append(f"RSI {rsi} — 과매수 주의")

        if macd_cross: score += 8; reasons.append("MACD 골든크로스")

        if bb_pct < 20:   score += 10; reasons.append(f"볼린저밴드 하단 근처 ({bb_pct}%)")
        elif bb_pct > 80: warnings.append(f"볼린저밴드 상단 근처 ({bb_pct}%)")

        if pct_from_low <= 10:   score += 12; reasons.append(f"52주 최저가 근처 (+{pct_from_low}%)")
        elif pct_from_low <= 20: score += 6;  reasons.append(f"52주 저점 구간 (+{pct_from_low}%)")

        if pct_from_high < -30: score += 5; reasons.append(f"52주 고점 대비 {pct_from_high}%")

        if vol_ratio >= 200:   score += 10; reasons.append(f"거래량 {vol_ratio:.0f}% — 강한 매수세")
        elif vol_ratio >= 150: score += 6;  reasons.append(f"거래량 {vol_ratio:.0f}% — 활발한 거래")
        elif vol_ratio < 50:   warnings.append("거래량 매우 적음")

        if ret_1m < -15:  score -= 8; warnings.append(f"1달 {ret_1m}% 하락")
        elif ret_1m < -5: score += 3; reasons.append(f"1달 {ret_1m}% 조정 — 눌림목")

        if sr["near_support"]:    score += 8; reasons.append("지지선 근처 — 반등 가능")
        if sr["near_resistance"]: score -= 5; warnings.append("저항선 근처 — 돌파 확인 필요")

        if momentum_bad:        score -= 15; warnings.append("모멘텀 약화 — 추세 반전 확인 필요")
        if manipulation_signal: score -= 20; warnings.append("가격 조작 의심 — 접근 금지")

        buy_signal = (
            score >= 60
            and rsi < 65
            and not manipulation_signal
            and not momentum_bad
            and not sr["near_resistance"]
        )
        buy_reason = ""
        if buy_signal:
            if sr["near_support"] and rsi < 45:
                buy_reason = "지지선+과매도 = 최적 진입"
            elif macd_cross and rsi < 55:
                buy_reason = "MACD 반전+RSI 적정"
            elif pct_from_low <= 15:
                buy_reason = "52주 저점 근처"
            else:
                buy_reason = "종합 점수 양호"

        dynamic_stop     = round(price - 2 * atr_val)
        dynamic_stop_pct = round((price - dynamic_stop) / price * 100, 1)
        buy_price  = round(price * 0.99)
        stop_price = min(round(price * (1 - STOP_LOSS_PCT)), dynamic_stop)
        target1    = round(price * (1 + TARGET1_PCT))
        target2    = round(price * (1 + TARGET2_PCT))
        target3    = round(price * (1 + TARGET3_PCT))
        shares     = int(INVEST_PER_STOCK / buy_price) if buy_price > 0 else 0

        if score >= 80 and len(warnings) <= 1:
            risk = "🟢 낮음"; risk_desc = "안정적인 투자 기회"
        elif score >= 60:
            risk = "🟡 중간"; risk_desc = "적정 리스크"
        else:
            risk = "🔴 높음"; risk_desc = "신중하게 접근"

        ticker = code + (".KS" if mkt == "KOSPI" else ".KQ")

        return {
            "ticker": ticker, "name": name, "period": "중기", "sector": "기타",
            "price": price, "change": change, "currency": "KRW",
            "per": per, "pbr": pbr, "roe": roe, "div": div, "debt": 0.0,
            "low52": low52, "high52": high52,
            "pct_from_low": pct_from_low, "pct_from_high": pct_from_high,
            "rsi": rsi, "macd_cross": macd_cross, "bb_pct": bb_pct,
            "vol_ratio": vol_ratio, "ret_1w": ret_1w, "ret_1m": ret_1m, "ret_3m": ret_3m,
            "win_rate": 50, "score": score, "risk": risk, "risk_desc": risk_desc,
            "reasons": reasons, "warnings": warnings,
            "buy_signal": buy_signal, "buy_reason": buy_reason,
            "buy_price": buy_price, "stop_price": stop_price,
            "dynamic_stop": dynamic_stop, "dynamic_stop_pct": dynamic_stop_pct,
            "target1": target1, "target2": target2, "target3": target3,
            "shares": shares, "invest_real": shares * buy_price,
            "profit1": shares * (target1 - buy_price),
            "profit2": shares * (target2 - buy_price),
            "profit3": shares * (target3 - buy_price),
            "loss_amt": shares * (buy_price - stop_price),
            "split1_shares": int(shares * 0.5),
            "split2_price": round(price * 0.95),
            "split2_shares": shares - int(shares * 0.5),
            "period_strategy": f"1차 목표가({target1:,}) 도달 시 절반 매도",
            "mktcap": mktcap, "revenue": 0,
            "foreign_net": 0.0, "inst_net": 0.0,
            "foreign_eok": 0.0, "inst_eok": 0.0,
            "is_kr_kis": False, "inv_ok": False,
            "dart_financials": {}, "dart_signals": {},
            "support": sr["support"], "resistance": sr["resistance"],
            "ma20": sr["ma20"], "ma60": sr["ma60"],
            "atr": round(atr_val),
            "news": {},
            "from_market_scan": True,
        }

    except Exception as e:
        print(f"  [{code}/{name}] 스캔 오류: {e}")
        return None


def run_market_scan(n: int = MARKET_SCAN_N):
    """코스피/코스닥 시가총액 상위 n종목 분석 → 상위 50개를 market_scan_cache.json에 저장"""
    print("=" * 60)
    print(f"시장 전체 스캔 시작 -- 코스피/코스닥 상위 {n}종목")
    print("=" * 60)

    if not _PYKRX_OK:
        print("[오류] pykrx 미설치 -- pip install pykrx")
        return

    stocks = fetch_top_market_stocks(n)
    if not stocks:
        print("[오류] 종목 목록 로드 실패")
        return

    kr_codes = {t.split(".")[0] for t in KR_STOCKS}

    results = []
    total   = len(stocks)
    for i, (code, name, mkt) in enumerate(stocks):
        if code in kr_codes:
            continue
        if (i + 1) % 100 == 0 or i == 0:
            print(f"  진행: {i+1}/{total}")
        r = analyze_market_stock(code, name, mkt)
        if r:
            results.append(r)
        time.sleep(0.2)  # KIS API rate limit: 초당 5건 이하 유지

    results_sorted = sorted(results, key=lambda x: x["score"], reverse=True)
    top50 = results_sorted[:50]

    print(f"\n[스캔 완료] {len(results)}종목 분석 완료 / 상위 50개 캐시 저장")
    for s in top50[:10]:
        print(f"  {s['name']} ({s['ticker']}) -- {s['score']}점  buy={s['buy_signal']}")

    cache = {
        "updated": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "count":   len(top50),
        "stocks":  top50,
    }
    with open(MARKET_SCAN_CACHE, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)

    print(f"캐시 저장 완료: {MARKET_SCAN_CACHE}")


# ════════════════════════════════════════════════
# 메인 실행
# ════════════════════════════════════════════════
def run():
    print("=" * 60)
    print("투자 비서 v6.0 시작")
    print(f"국내 {len(KR_STOCKS)}종목 + 해외 {len(US_STOCKS)}종목 분석 중...")
    print(f"KIS API: {'연결됨' if _kis.available() else 'yfinance 폴백'}")
    print(f"AI 분석: {'Claude 활성화' if (_ANTHROPIC_OK and ANTHROPIC_API_KEY) else '비활성화 (ANTHROPIC_API_KEY 없음)'}")
    print("=" * 60)

    # ── 월간 리포트 (매월 1일) ──────────────────
    if datetime.now().day == 1:
        monthly = make_monthly_report()
        if monthly:
            print("\n[월간 성과 리포트 전송]")
            tg_send(monthly)

    print("\n[1/7] 시장 분위기 파악 중...")
    mood = get_market_mood()
    fg   = get_fear_greed(mood)
    print(f"  → 시장: {mood['status']} / VIX: {mood['vix']} / 코스피: {mood['kospi_chg']:+.2f}%")
    print(f"  → 공포탐욕지수: {fg['score']} ({fg['label']})")

    print("\n[2/7] 미국 경제지표 수집 중...")
    macro = get_us_macro_indicators()
    print(f"  → 10년물: {macro['tnx']}% / 단기금리: {macro['irx']}% / DXY: {macro['dxy']}")
    print(f"  → CPI 전년비: {macro['cpi_yoy']}% / 연준: {macro['fed_direction']}")

    print("\n[3/7] DART 공시 데이터 수집 중...")
    all_dart  = get_all_dart_data(KR_STOCKS)
    sig_count = sum(1 for dd in all_dart.values() for items in dd["signals"].values() if items)
    print(f"  → {len(all_dart)}개 수집 / 주요 공시 {sig_count}건")

    print("\n[4/7] 국내 종목 분석 중...")
    kr_results = []
    for ticker, val in KR_STOCKS.items():
        name, period, sector = val
        print(f"  분석: {name}")
        r = analyze(ticker, name, period, sector,
                    dart_data=all_dart.get(ticker),
                    with_sentiment=True)
        if r:
            kr_results.append(r)
        time.sleep(0.8)

    print("\n[5/7] 해외 종목 분석 중...")
    us_results = []
    for ticker, val in US_STOCKS.items():
        name, period, sector = val
        print(f"  분석: {name}")
        r = analyze(ticker, name, period, sector)
        if r:
            us_results.append(r)
        time.sleep(0.8)

    # 시장 스캔 캐시 병합 (새벽 2시 run_market_scan이 저장한 상위 50개)
    scan_top = []
    if os.path.exists(MARKET_SCAN_CACHE):
        try:
            with open(MARKET_SCAN_CACHE, "r", encoding="utf-8") as _f:
                _scan = json.load(_f)
            scan_top = _scan.get("stocks", [])
            print(f"  → 시장 스캔 캐시 로드: {len(scan_top)}종목 (갱신: {_scan.get('updated','?')})")
        except Exception as _e:
            print(f"  → 시장 스캔 캐시 로드 실패: {_e}")

    # KR_STOCKS 결과 우선, 캐시에서 중복 아닌 것만 추가
    kr_tickers = {r["ticker"] for r in kr_results}
    merged_kr  = kr_results + [s for s in scan_top if s["ticker"] not in kr_tickers]

    kr_sorted  = sorted(merged_kr,  key=lambda x: x["score"], reverse=True)
    us_sorted  = sorted(us_results, key=lambda x: x["score"], reverse=True)
    kr_top5    = kr_sorted[:5]
    us_top5    = us_sorted[:5]
    avoid_list = sorted(
        [s for s in merged_kr + us_results if s["rsi"] > 70 or s["ret_1m"] < -15],
        key=lambda x: x["ret_1m"],
    )[:5]

    dart_alerts = []
    for ticker, dd in all_dart.items():
        name, _, sector = KR_STOCKS[ticker]
        for key, items in dd["signals"].items():
            if items:
                _, label, is_risk = SIGNAL_DEFS[key]
                dart_alerts.append({
                    "ticker": ticker, "name": name, "sector": sector,
                    "key": key, "label": label, "is_risk": is_risk, "items": items,
                })

    print("\n[6/7] AI 분석 중...")
    ai_summary = ai_market_summary(mood, kr_top5, us_top5, fg)
    ai_sector  = ai_sector_rotation(mood)
    ai_macro   = ai_us_macro_impact(macro, mood)

    ai_insights: dict = {}
    for s in kr_top5:
        insight = ai_stock_insight(s)
        if insight:
            ai_insights[s["ticker"]] = insight
        time.sleep(0.3)

    print("\n[7/7] 리포트 생성 및 전송 중...")
    html = make_report(
        kr_top5, us_top5, avoid_list, mood,
        dart_alerts=dart_alerts,
        ai_summary=ai_summary,
        ai_sector=ai_sector,
        fg=fg,
        ai_insights=ai_insights,
        macro=macro,
        ai_macro=ai_macro,
    )
    text = make_telegram_message(
        kr_top5, us_top5, avoid_list, mood,
        dart_alerts=dart_alerts,
        ai_summary=ai_summary,
        fg=fg,
        macro=macro,
        ai_macro=ai_macro,
    )
    tg_send(text)
    tg_send_document(html)

    ha = check_holdings_alerts()
    alert_msgs = [a["msg"] for a in ha if "msg" in a]
    if alert_msgs:
        print(f"\n[보유종목 알림 {len(alert_msgs)}건]")
        tg_send("\n".join(["<b>📦 보유종목 알림</b>", ""] + alert_msgs))

    record_recommendations(kr_top5, us_top5)

    print("\n[결과 요약]")
    for s in kr_top5:
        print(f"  {s['name']} — {s['score']}점 / {'✅' if s.get('buy_signal') else '❌'}")
    for s in us_top5:
        print(f"  {s['name']} — {s['score']}점 / {'✅' if s.get('buy_signal') else '❌'}")

    # 텔레그램 봇 폴링 (5분)
    run_bot_extended(
        kr_results, us_results, mood,
        kr_top5, us_top5, avoid_list,
        dart_alerts, ai_summary, fg,
        duration_sec=300,
    )
    print("\n완료! 텔레그램을 확인하세요.")


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else ""

    if mode == "--monitor":
        run_monitor(duration_hours=7.0, interval_sec=300)
    elif mode == "--usclose":
        run_us_briefing()
    elif mode == "--premarket":
        run_premarket_briefing()
    elif mode == "--close":
        run_close_summary()
    elif mode == "--marketscan":
        run_market_scan()
    else:
        run()
