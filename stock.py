"""
투자 비서 프로그램 v5.0
- 단기/중기/장기 종목 분류
- 매수이유 상세 설명
- 200만원 기준 매수/매도/손절가 계산
- 분할매수 전략 제안
- 리스크 등급 및 주의사항
- 시장 브리핑 / 피해야 할 종목 경고
- OpenDart API 공식 재무제표 + 공시 알림
- 한국투자증권 API: 국내주식 실시간 시세 + 외국인/기관 순매수
- 텔레그램 봇 알림 (v5.0)
"""

import os
import requests
import yfinance as yf
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import time

# ================================================
# 텔레그램 설정 (환경변수)
# ================================================
TELEGRAM_TOKEN   = os.environ.get("TELEGRAM_TOKEN",   "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

# ================================================
# 투자 설정
# ================================================
INVEST_PER_STOCK = 2_000_000
STOP_LOSS_PCT    = 0.07
TARGET1_PCT      = 0.10
TARGET2_PCT      = 0.20
TARGET3_PCT      = 0.40

# ================================================
# 한국투자증권 (KIS) API
# ================================================
KIS_APP_KEY    = os.environ.get("KIS_APP_KEY", "")
KIS_APP_SECRET = os.environ.get("KIS_APP_SECRET", "")
KIS_BASE       = "https://openapi.koreainvestment.com:9443"


def _safe_float(val, default: float = 0.0) -> float:
    """문자열 포함 숫자 파싱 — 공백/대시/None 안전 처리"""
    try:
        return float(str(val).replace(",", "")) if val not in (None, "", "-", "N/A") else default
    except (ValueError, TypeError):
        return default


class KisClient:
    """한국투자증권 Open API 클라이언트 (토큰 자동 갱신)"""

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
                json={
                    "grant_type": "client_credentials",
                    "appkey":     KIS_APP_KEY,
                    "appsecret":  KIS_APP_SECRET,
                },
                timeout=10,
            )
            d = r.json()
            self._token     = d.get("access_token", "")
            expires_in      = int(d.get("expires_in", 86400))
            self._token_exp = datetime.now() + timedelta(seconds=expires_in - 600)
        except Exception as e:
            print(f"  [KIS] 토큰 발급 실패: {e}")

    def _get(self, path: str, tr_id: str, params: dict) -> dict:
        if not self.available():
            return {}
        self._ensure_token()
        if not self._token:
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
            if d.get("rt_cd") != "0":
                return {}
            return d
        except Exception as e:
            print(f"  [KIS] 요청 실패 ({tr_id}): {e}")
            return {}

    def get_price(self, stock_code: str) -> dict:
        """주식현재가 시세조회 (FHKST01010100)"""
        d = self._get(
            "/uapi/domestic-stock/v1/quotations/inquire-price",
            "FHKST01010100",
            {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": stock_code},
        )
        return d.get("output", {})

    def get_investor(self, stock_code: str) -> dict:
        """당일 외국인/기관 순매수 (FHKST01010900)"""
        d = self._get(
            "/uapi/domestic-stock/v1/quotations/inquire-investor",
            "FHKST01010900",
            {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": stock_code},
        )
        output = d.get("output", [])
        return output[0] if isinstance(output, list) and output else {}

    def get_daily_chart(self, stock_code: str, months: int = 5) -> list:
        """일별 주가 OHLCV (FHKST03010100) — 최대 100봉"""
        end_dt   = datetime.now().strftime("%Y%m%d")
        start_dt = (datetime.now() - timedelta(days=months * 31)).strftime("%Y%m%d")
        d = self._get(
            "/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice",
            "FHKST03010100",
            {
                "FID_COND_MRKT_DIV_CODE": "J",
                "FID_INPUT_ISCD":         stock_code,
                "FID_INPUT_DATE_1":       start_dt,
                "FID_INPUT_DATE_2":       end_dt,
                "FID_PERIOD_DIV_CODE":    "D",
                "FID_ORG_ADJ_PRC":        "0",
            },
        )
        rows = d.get("output2", [])
        return list(reversed(rows))  # KIS는 최신순 → 오래된순으로 뒤집기

    def get_kospi(self) -> dict:
        """코스피 지수 현재가 (FHPUP02100000)"""
        d = self._get(
            "/uapi/domestic-stock/v1/quotations/inquire-index-price",
            "FHPUP02100000",
            {"FID_COND_MRKT_DIV_CODE": "U", "FID_INPUT_ISCD": "0001"},
        )
        return d.get("output", {})


_kis = KisClient()

# ================================================
# OpenDart API
# ================================================
DART_API_KEY = os.environ.get("DART_API_KEY", "")
DART_BASE    = "https://opendart.fss.or.kr/api"
_corp_cache  = {}   # stock_code → corp_code 캐시

# 감지할 공시 키워드: {key: (keywords, label, is_risk)}
SIGNAL_DEFS = {
    "rights":   (["유상증자결정"],                                      "유상증자",   True),
    "buyback":  (["자기주식취득결정", "자기주식취득신탁계약체결"],        "자사주매입", False),
    "order":    (["단일판매ㆍ공급계약체결", "수주계약", "공급계약체결"],  "신규수주",   False),
    "dividend": (["현금배당결정", "배당결정"],                           "배당결정",   False),
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
    """전년도 연결(또는 개별) 재무제표 — 매출·영업이익·순이익 추출"""
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
    for item in d["list"]:
        if item.get("sj_div") not in ("IS", "CIS"):
            continue
        acct = item.get("account_nm", "").strip()
        raw  = item.get("thstrm_amount", "0").replace(",", "")
        try:
            val = int(raw)
        except (ValueError, TypeError):
            continue
        if acct in REVENUE_NM    and "revenue"   not in res:
            res["revenue"]   = val
        elif acct in OP_INCOME_NM  and "op_income"  not in res:
            res["op_income"]  = val
        elif acct in NET_INCOME_NM and "net_income" not in res:
            res["net_income"] = val
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
    """공시 목록 → {key: [{"title", "date", "url"}, ...]}"""
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
    """KR 종목 전체 DART 데이터 수집 → {ticker: {financials, signals}}"""
    if not DART_API_KEY:
        print("  [DART] API 키 없음 — 공시 수집 건너뜀")
        return {}
    result = {}
    for ticker in kr_stocks:
        stock_code = ticker.split(".")[0]
        corp_code  = dart_corp_code(stock_code)
        if not corp_code:
            continue
        fin   = dart_financials(corp_code)
        discs = dart_disclosures(corp_code, days=7)
        sigs  = detect_signals(discs)
        result[ticker] = {"financials": fin, "signals": sigs}
        time.sleep(0.3)
    return result


def _fmt_krw_large(val: int) -> str:
    if val == 0:
        return "0원"
    sign = "-" if val < 0 else ""
    v = abs(val)
    if v >= 1_000_000_000_000:
        return f"{sign}{v / 1_000_000_000_000:.1f}조원"
    return f"{sign}{v / 100_000_000:.0f}억원"


# ================================================
# 종목 분류 (단기/중기/장기 + 관심섹터 표시)
# ================================================
KR_STOCKS = {
    "009540.KS": ("HD한국조선해양",   "중기", "조선"),
    "010140.KS": ("삼성중공업",       "중기", "조선"),
    "042660.KS": ("한화오션",         "중기", "조선"),
    "012450.KS": ("한화에어로스페이스","단기", "방산"),
    "047810.KS": ("한국항공우주",     "단기", "방산"),
    "000880.KS": ("한화",             "중기", "방산"),
    "064350.KS": ("현대로템",         "단기", "방산"),
    "034020.KS": ("두산에너빌리티",   "장기", "원전"),
    "267260.KS": ("HD현대일렉트릭",   "단기", "전력"),
    "298040.KS": ("효성중공업",       "중기", "전력"),
    "009830.KS": ("한화솔루션",       "장기", "신재생"),
    "015760.KS": ("한국전력",         "장기", "신재생"),
    "000100.KS": ("유한양행",         "중기", "바이오"),
    "128940.KS": ("한미약품",         "중기", "바이오"),
    "068270.KS": ("셀트리온",         "중기", "바이오"),
    "207940.KS": ("삼성바이오로직스", "장기", "바이오"),
    "105560.KS": ("KB금융",           "단기", "금융"),
    "055550.KS": ("신한지주",         "단기", "금융"),
    "086790.KS": ("하나금융지주",     "단기", "금융"),
    "138930.KS": ("BNK금융지주",      "단기", "금융"),
    "011200.KS": ("HMM",              "단기", "해운"),
    "003490.KS": ("대한항공",         "중기", "항공"),
    "086280.KS": ("현대글로비스",     "중기", "물류"),
    "004020.KS": ("현대제철",         "중기", "철강"),
    "010950.KS": ("S-Oil",            "단기", "에너지"),
    "047050.KS": ("포스코인터내셔널", "중기", "에너지"),
}

US_STOCKS = {
    "RTX":   ("레이시온",          "단기", "방산"),
    "LMT":   ("록히드마틴",        "중기", "방산"),
    "NOC":   ("노스롭그루만",      "중기", "방산"),
    "GE":    ("GE에어로스페이스",  "단기", "항공"),
    "HII":   ("헌팅턴잉걸스",      "중기", "조선"),
    "NEE":   ("넥스트에라에너지",  "장기", "신재생"),
    "CEG":   ("콘스텔레이션에너지","중기", "원전"),
    "VST":   ("비스트라에너지",    "단기", "원전"),
    "JNJ":   ("존슨앤존슨",        "중기", "바이오"),
    "UNH":   ("유나이티드헬스",    "단기", "헬스케어"),
    "ABT":   ("애보트",            "중기", "바이오"),
    "JPM":   ("JP모건",            "단기", "금융"),
    "BRK-B": ("버크셔해서웨이",    "장기", "금융"),
    "XOM":   ("엑슨모빌",          "단기", "에너지"),
    "CVX":   ("쉐브론",            "단기", "에너지"),
    "KO":    ("코카콜라",          "장기", "소비재"),
    "PG":    ("P&G",               "장기", "소비재"),
    "O":     ("리얼티인컴",        "장기", "리츠"),
    "AMT":   ("아메리칸타워",      "장기", "리츠"),
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


def get_market_mood() -> dict:
    mood = {}
    try:
        # ── 코스피: KIS 우선, 실패 시 yfinance 폴백 ─────────
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
                k_chg = (kospi["Close"].iloc[-1] - kospi["Close"].iloc[-2]) / kospi["Close"].iloc[-2] * 100
                mood["kospi_chg"]   = round(k_chg, 2)
                mood["kospi_price"] = round(float(kospi["Close"].iloc[-1]), 2)
            else:
                mood["kospi_chg"] = 0
                mood["kospi_price"] = 0

        # ── 해외 지표: yfinance ──────────────────────────────
        vix    = yf.Ticker("^VIX").info
        usdkrw = yf.Ticker("KRW=X").info
        wti    = yf.Ticker("CL=F").info
        gold   = yf.Ticker("GC=F").info
        sp500  = yf.Ticker("^GSPC").history(period="2d")

        if len(sp500) >= 2:
            s_chg = (sp500["Close"].iloc[-1] - sp500["Close"].iloc[-2]) / sp500["Close"].iloc[-2] * 100
            mood["sp500_chg"] = round(s_chg, 2)
        else:
            mood["sp500_chg"] = 0

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
            "status": "확인불가", "advice": "⚠️ 시장 데이터 수집 실패 — 직접 확인 필요.",
        }
    return mood


def analyze(ticker: str, name: str, period: str, sector: str,
            dart_data: dict | None = None) -> dict | None:
    is_kr    = ".KS" in ticker or ".KQ" in ticker
    currency = "KRW" if is_kr else "USD"

    # ── 1. 데이터 수집 (KIS / yfinance 분기) ───────────────
    foreign_net = 0.0  # 외국인 순매수 금액 (원)
    inst_net    = 0.0  # 기관 순매수 금액 (원)
    revenue     = 0
    profit      = 0

    try:
        if is_kr and _kis.available():
            # ── KIS: 국내주식 ──────────────────────────────
            stock_code = ticker.split(".")[0]

            price_info = _kis.get_price(stock_code)
            if not price_info:
                return None

            price = _safe_float(price_info.get("stck_prpr"))
            if price <= 0:
                return None

            change = _safe_float(price_info.get("prdy_ctrt"))
            per    = _safe_float(price_info.get("per"))  or None
            pbr    = _safe_float(price_info.get("pbr"))  or None
            div    = _safe_float(price_info.get("dvdn_yield"))
            low52  = _safe_float(price_info.get("w52_lwpr"), price)
            high52 = _safe_float(price_info.get("w52_hgpr"), price)
            mktcap = _safe_float(price_info.get("hts_avls")) * 1e8  # 억원 → 원

            # ROE ≈ EPS / BPS × 100 (KIS 제공 수치 활용)
            eps = _safe_float(price_info.get("eps"))
            bps = _safe_float(price_info.get("bps"))
            roe = round(eps / bps * 100, 1) if bps > 0 else 0.0
            debt = 0.0  # KIS 미제공 — 부채비율 스코어링 생략

            # 일별 OHLCV (기술적 분석용)
            rows = _kis.get_daily_chart(stock_code, months=5)
            if len(rows) < 20:
                return None
            close  = pd.Series([_safe_float(r.get("stck_clpr")) for r in rows], dtype=float)
            volume = pd.Series([_safe_float(r.get("acml_vol"))   for r in rows], dtype=float)

            # 외국인/기관 당일 순매수
            inv = _kis.get_investor(stock_code)
            foreign_net = _safe_float(inv.get("frgn_ntby_tr_pbmn"))  # 원 단위
            inst_net    = _safe_float(inv.get("orgn_ntby_tr_pbmn"))   # 원 단위

        else:
            # ── yfinance: 해외주식 (또는 KIS 키 미설정 시 폴백) ──
            stock = yf.Ticker(ticker)
            info  = stock.info

            if not info or not info.get("regularMarketPrice"):
                return None

            price  = float(info.get("regularMarketPrice", 0))
            prev   = float(info.get("regularMarketPreviousClose") or price)
            change = round((price - prev) / prev * 100, 2) if prev else 0
            per    = info.get("trailingPE") or info.get("forwardPE")
            pbr    = info.get("priceToBook")
            roe    = round((info.get("returnOnEquity") or 0) * 100, 1)
            div    = round((info.get("dividendYield") or 0) * 100, 1)
            debt   = round(info.get("debtToEquity") or 0, 1)
            low52  = info.get("fiftyTwoWeekLow", price)
            high52 = info.get("fiftyTwoWeekHigh", price)
            mktcap = info.get("marketCap", 0)
            revenue = info.get("totalRevenue", 0)
            profit  = info.get("netIncomeToCommon", 0)

            hist = stock.history(period="6mo")
            if hist is None or len(hist) < 20:
                return None
            close  = hist["Close"].squeeze()
            volume = hist["Volume"].squeeze()

    except Exception as e:
        print(f"  [{ticker}] 데이터 수집 오류: {e}")
        return None

    pct_from_low  = round((price - low52)  / low52  * 100, 1) if low52  else 0
    pct_from_high = round((price - high52) / high52 * 100, 1) if high52 else 0

    # ── 2. 기술적 지표 계산 (공통) ─────────────────────────
    try:
        delta = close.diff()

        # RSI
        gain  = delta.clip(lower=0).rolling(14).mean()
        loss  = (-delta.clip(upper=0)).rolling(14).mean()
        rs    = gain / loss.replace(0, 1e-9)
        rsi   = round(float((100 - 100 / (1 + rs)).iloc[-1]), 1)

        # MACD
        ema12       = close.ewm(span=12).mean()
        ema26       = close.ewm(span=26).mean()
        macd_line   = ema12 - ema26
        signal_line = macd_line.ewm(span=9).mean()
        macd_cross  = float(macd_line.iloc[-1]) > float(signal_line.iloc[-1])

        # 볼린저밴드
        sma20  = close.rolling(20).mean()
        std20  = close.rolling(20).std()
        upper  = sma20 + 2 * std20
        lower  = sma20 - 2 * std20
        bb_pct = round((float(close.iloc[-1]) - float(lower.iloc[-1])) /
                       (float(upper.iloc[-1]) - float(lower.iloc[-1]) + 1e-9) * 100, 1)

        avg_vol   = float(volume.rolling(20).mean().iloc[-1])
        last_vol  = float(volume.iloc[-1])
        vol_ratio = round(last_vol / avg_vol * 100, 0) if avg_vol else 100

        n = len(close)
        ret_1w = round((float(close.iloc[-1]) - float(close.iloc[-5]))  / float(close.iloc[-5])  * 100, 1) if n >= 5  else 0
        ret_1m = round((float(close.iloc[-1]) - float(close.iloc[-20])) / float(close.iloc[-20]) * 100, 1) if n >= 20 else 0
        ret_3m = round((float(close.iloc[-1]) - float(close.iloc[0]))   / float(close.iloc[0])   * 100, 1)

        # 과거 패턴 승률
        win_count = total_count = 0
        for i in range(20, n - 20):
            d  = delta.iloc[:i + 1]
            g  = d.clip(lower=0).rolling(14).mean()
            ll = (-d.clip(upper=0)).rolling(14).mean()
            r  = g / ll.replace(0, 1e-9)
            rsi_past = float((100 - 100 / (1 + r)).iloc[-1])
            if rsi_past < 45:
                future_ret = (float(close.iloc[i + 20]) - float(close.iloc[i])) / float(close.iloc[i])
                total_count += 1
                if future_ret > 0:
                    win_count += 1
        win_rate = round(win_count / total_count * 100) if total_count > 0 else 50

    except Exception as e:
        print(f"  [{ticker}] 기술적 지표 오류: {e}")
        return None

    # ── 3. 점수 계산 (공통) ─────────────────────────────────
    score    = 0
    reasons  = []
    warnings = []

    if per:
        if per <= 8:
            score += 30; reasons.append(f"PER {per:.1f}배 — 업종 평균 대비 매우 저렴한 수준이에요")
        elif per <= 12:
            score += 22; reasons.append(f"PER {per:.1f}배 — 적정 수준보다 저렴해요")
        elif per <= 15:
            score += 15; reasons.append(f"PER {per:.1f}배 — 합리적인 가격 수준이에요")
        elif per <= 20:
            score += 7;  warnings.append(f"PER {per:.1f}배 — 약간 비싼 편이에요")
        else:
            score -= 5;  warnings.append(f"PER {per:.1f}배 — 현재 주가가 비싼 편이에요")
    else:
        warnings.append("PER 정보 없음 — 수익성 확인 필요")

    if pbr:
        if pbr <= 0.8:
            score += 25; reasons.append(f"PBR {pbr:.2f}배 — 회사 자산보다 싸게 살 수 있어요")
        elif pbr <= 1.2:
            score += 18; reasons.append(f"PBR {pbr:.2f}배 — 자산 대비 저렴하게 거래 중이에요")
        elif pbr <= 1.5:
            score += 10; reasons.append(f"PBR {pbr:.2f}배 — 적정 수준이에요")
        else:
            warnings.append(f"PBR {pbr:.2f}배 — 자산 대비 다소 비쌀 수 있어요")

    if roe >= 15:
        score += 15; reasons.append(f"ROE {roe}% — 돈을 매우 잘 버는 회사예요")
    elif roe >= 10:
        score += 10; reasons.append(f"ROE {roe}% — 꾸준히 수익을 내는 안정적인 회사예요")
    elif roe >= 5:
        score += 5
    elif roe > 0:
        warnings.append(f"ROE {roe}% — 수익성이 낮은 편이에요")

    if div >= 4:
        score += 10; reasons.append(f"배당수익률 {div}% — 은행 이자보다 훨씬 높은 배당을 줘요")
    elif div >= 2:
        score += 6;  reasons.append(f"배당수익률 {div}% — 안정적인 배당이 있어요")
    elif div >= 1:
        score += 3

    # debt: KIS에서 미제공 시(=0.0, is_kr) 부채 스코어링 생략
    if not (is_kr and _kis.available()):
        if debt > 200:
            score -= 10; warnings.append(f"부채비율 {debt}% — 부채가 많은 편이에요")
        elif debt > 100:
            warnings.append(f"부채비율 {debt}% — 부채 수준을 주시할 필요 있어요")
        elif debt <= 50:
            score += 5;  reasons.append(f"부채비율 {debt}% — 재무 건전성이 매우 좋아요")

    if rsi < 30:
        score += 15; reasons.append(f"RSI {rsi} — 과매도 구간이에요. 반등 가능성이 높아요")
    elif rsi < 45:
        score += 10; reasons.append(f"RSI {rsi} — 저점 매수 구간이에요")
    elif rsi > 70:
        score -= 10; warnings.append(f"RSI {rsi} — 과매수 구간이에요. 단기 조정 가능성 있어요")

    if macd_cross:
        score += 8;  reasons.append("MACD 골든크로스 — 상승 전환 신호가 포착됐어요")

    if bb_pct < 20:
        score += 10; reasons.append(f"볼린저밴드 하단 근처 ({bb_pct}%) — 통계적으로 반등 가능성이 높아요")
    elif bb_pct > 80:
        warnings.append(f"볼린저밴드 상단 근처 ({bb_pct}%) — 단기 과열 구간이에요")

    if pct_from_low <= 10:
        score += 12; reasons.append(f"52주 최저가 대비 +{pct_from_low}% — 역사적 저점 근처예요")
    elif pct_from_low <= 20:
        score += 6;  reasons.append(f"52주 최저가 대비 +{pct_from_low}% — 저점 구간에 있어요")

    if pct_from_high < -30:
        score += 5;  reasons.append(f"52주 최고가 대비 {pct_from_high}% — 고점 대비 많이 빠진 상태예요")

    if vol_ratio >= 200:
        score += 10; reasons.append(f"거래량 평균 대비 {vol_ratio:.0f}% — 강한 매수세가 들어오고 있어요")
    elif vol_ratio >= 150:
        score += 6;  reasons.append(f"거래량 평균 대비 {vol_ratio:.0f}% — 평소보다 거래가 활발해요")
    elif vol_ratio < 50:
        warnings.append("거래량이 매우 적어요 — 유동성 위험이 있어요")

    if ret_1m < -15:
        score -= 8;  warnings.append(f"최근 1달 {ret_1m}% 하락 — 하락 추세 주의")
    elif ret_1m < -5:
        score += 3;  reasons.append(f"최근 1달 {ret_1m}% 조정 — 눌림목 매수 기회일 수 있어요")

    sector_bonus = 0
    if sector in ["조선", "방산", "원전", "전력", "바이오"]:
        sector_bonus = 15; reasons.append(f"{sector} 섹터 — {SECTOR_DESC.get(sector, '')}")
    elif sector in ["신재생", "리츠", "소비재"]:
        sector_bonus = 8;  reasons.append(f"{sector} 섹터 — {SECTOR_DESC.get(sector, '')} (장기 보유 추천)")
    elif sector in ["금융", "해운", "에너지"]:
        sector_bonus = 10; reasons.append(f"{sector} 섹터 — {SECTOR_DESC.get(sector, '')}")
    score += sector_bonus

    # ── 외국인/기관 순매수 (KIS 전용) ──────────────────────
    foreign_eok = foreign_net / 1e8  # 원 → 억원
    inst_eok    = inst_net    / 1e8
    if is_kr and _kis.available():
        if foreign_eok >= 50:
            score += 10; reasons.append(f"외국인 순매수 +{foreign_eok:.0f}억원 — 강한 외국인 매수세")
        elif foreign_eok >= 10:
            score += 5;  reasons.append(f"외국인 순매수 +{foreign_eok:.0f}억원")
        elif foreign_eok <= -50:
            score -= 8;  warnings.append(f"외국인 순매도 {foreign_eok:.0f}억원 — 외국인 이탈 주의")

        if inst_eok >= 50:
            score += 8;  reasons.append(f"기관 순매수 +{inst_eok:.0f}억원 — 기관 집중 매수")
        elif inst_eok >= 10:
            score += 4;  reasons.append(f"기관 순매수 +{inst_eok:.0f}억원")
        elif inst_eok <= -50:
            score -= 5;  warnings.append(f"기관 순매도 {inst_eok:.0f}억원")

    # ── DART 신호 점수 조정 ─────────────────────────────────
    dart_fin  = {}
    dart_sigs = {}
    if dart_data:
        dart_fin  = dart_data.get("financials", {})
        dart_sigs = dart_data.get("signals", {})

        if dart_sigs.get("rights"):
            score -= 15
            warnings.append("유상증자 결정 공시 감지 — 주가 희석 우려, 단기 하락 리스크")

        if dart_sigs.get("buyback"):
            score += 10
            reasons.append("자사주 매입 결정 공시 — 주주 가치 제고 호재 신호")

        if dart_sigs.get("order"):
            bonus = 15 if sector == "조선" else 10
            score += bonus
            n_ord = len(dart_sigs["order"])
            reasons.append(
                f"신규 수주 공시 {n_ord}건 감지" +
                (" — 조선주 핵심 호재" if sector == "조선" else "")
            )

        if dart_sigs.get("dividend"):
            score += 5
            reasons.append("배당 결정 공시 — 주주환원 신호")

    # ── 리스크 등급 ─────────────────────────────────────────
    if score >= 80 and len(warnings) <= 1:
        risk = "🟢 낮음";  risk_desc = "안정적인 투자 기회예요"
    elif score >= 60:
        risk = "🟡 중간";  risk_desc = "적정 리스크 수준이에요"
    else:
        risk = "🔴 높음";  risk_desc = "신중하게 접근하세요"

    buy_price  = round(price * 0.99)
    stop_price = round(price * (1 - STOP_LOSS_PCT))
    target1    = round(price * (1 + TARGET1_PCT))
    target2    = round(price * (1 + TARGET2_PCT))
    target3    = round(price * (1 + TARGET3_PCT))

    shares        = int(INVEST_PER_STOCK / buy_price)
    invest_real   = shares * buy_price
    profit1       = shares * (target1 - buy_price)
    profit2       = shares * (target2 - buy_price)
    profit3       = shares * (target3 - buy_price)
    loss_amt      = shares * (buy_price - stop_price)
    split1_shares = int(shares * 0.5)
    split2_price  = round(price * 0.95)
    split2_shares = shares - split1_shares

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
        "buy_price": buy_price, "stop_price": stop_price,
        "target1": target1, "target2": target2, "target3": target3,
        "shares": shares, "invest_real": invest_real,
        "profit1": profit1, "profit2": profit2, "profit3": profit3,
        "loss_amt": loss_amt,
        "split1_shares": split1_shares, "split2_price": split2_price,
        "split2_shares": split2_shares, "period_strategy": period_strategy,
        "mktcap": mktcap, "revenue": revenue,
        "foreign_net": foreign_net, "inst_net": inst_net,
        "foreign_eok": foreign_eok, "inst_eok": inst_eok,
        "is_kr_kis": is_kr and _kis.available(),
        "dart_financials": dart_fin,
        "dart_signals":    dart_sigs,
    }


def fmt(price, currency="KRW"):
    if currency == "KRW":
        return f"{price:,.0f}원"
    return f"${price:,.2f}"


def card_html(rank: int, s: dict) -> str:
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
        warn_html = f"""
        <div style="margin-top:14px;">
          <div style="font-weight:600;font-size:13px;color:#e67700;margin-bottom:6px;">주의사항</div>
          <ul style="margin:0;padding-left:0;list-style:none;">{warn_items}</ul>
        </div>"""

    if cur == "KRW":
        price_str = f"{s['price']:,.0f}원";   buy_str  = f"{s['buy_price']:,.0f}원"
        stop_str  = f"{s['stop_price']:,.0f}원"; t1_str = f"{s['target1']:,.0f}원"
        t2_str    = f"{s['target2']:,.0f}원";   t3_str  = f"{s['target3']:,.0f}원"
        low_str   = f"{s['low52']:,.0f}원";     high_str= f"{s['high52']:,.0f}원"
        sp2_str   = f"{s['split2_price']:,.0f}원"
    else:
        price_str = f"${s['price']:,.2f}";      buy_str  = f"${s['buy_price']:,.2f}"
        stop_str  = f"${s['stop_price']:,.2f}"; t1_str   = f"${s['target1']:,.2f}"
        t2_str    = f"${s['target2']:,.2f}";    t3_str   = f"${s['target3']:,.2f}"
        low_str   = f"${s['low52']:,.2f}";      high_str = f"${s['high52']:,.2f}"
        sp2_str   = f"${s['split2_price']:,.2f}"

    mktcap_str = (
        f"{s['mktcap']/1e12:.1f}조원" if cur == "KRW" and s["mktcap"] else
        f"${s['mktcap']/1e9:.1f}B"   if s["mktcap"] else "정보없음"
    )

    # ── DART 공식 재무제표 블록 ────────────────────────────
    dart_fin_html = ""
    fin = s.get("dart_financials", {})
    if fin and "revenue" in fin:
        year_str  = f"{fin['year']}년 사업보고서"
        rev_str   = _fmt_krw_large(fin["revenue"])
        op_str    = _fmt_krw_large(fin["op_income"])  if "op_income"  in fin else "-"
        net_str   = _fmt_krw_large(fin["net_income"]) if "net_income" in fin else "-"
        op_margin = (
            f"{fin['op_income'] / fin['revenue'] * 100:.1f}%"
            if "op_income" in fin and fin["revenue"] else "-"
        )
        dart_fin_html = f"""
    <div style="margin-bottom:16px;padding:14px;background:#f0f8ff;border-radius:10px;
                border-left:4px solid #0066cc;">
      <div style="font-weight:700;font-size:14px;color:#0066cc;margin-bottom:10px;">
        🏛️ DART 공식 재무제표 ({year_str})
      </div>
      <table style="width:100%;font-size:13px;border-collapse:collapse;">
        <tr>
          <td style="padding:5px 0;color:#868e96;width:25%;">매출액</td>
          <td style="padding:5px 0;font-weight:600;">{rev_str}</td>
          <td style="padding:5px 0;color:#868e96;width:25%;">영업이익</td>
          <td style="padding:5px 0;font-weight:600;">{op_str}</td>
        </tr>
        <tr>
          <td style="padding:5px 0;color:#868e96;">순이익</td>
          <td style="padding:5px 0;font-weight:600;">{net_str}</td>
          <td style="padding:5px 0;color:#868e96;">영업이익률</td>
          <td style="padding:5px 0;font-weight:600;">{op_margin}</td>
        </tr>
      </table>
    </div>"""

    # ── DART 공시 알림 블록 ────────────────────────────────
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
            dt = it["date"]
            date_fmt = f"{dt[:4]}.{dt[4:6]}.{dt[6:]}" if len(dt) == 8 else dt
            sig_items_html.append(
                f'<li style="margin:5px 0;padding:8px 12px;background:{bg};'
                f'border-left:3px solid {color};border-radius:0 6px 6px 0;font-size:13px;">'
                f'{icon} <b>[{label}]</b> {it["title"]} '
                f'<span style="color:#868e96;font-size:12px;">({date_fmt})</span> '
                f'<a href="{it["url"]}" style="color:{color};font-size:12px;text-decoration:none;">'
                f'공시 보기 →</a>'
                f'</li>'
            )
    if sig_items_html:
        dart_sig_html = f"""
    <div style="margin-top:14px;padding:14px;background:#fffbf0;border-radius:10px;
                border-left:4px solid #f59f00;">
      <div style="font-weight:700;font-size:14px;color:#e67700;margin-bottom:8px;">
        📢 DART 공시 알림 (최근 7일)
      </div>
      <ul style="margin:0;padding-left:0;list-style:none;">{"".join(sig_items_html)}</ul>
    </div>"""

    # ── 외국인/기관 순매수 블록 (KIS 데이터) ───────────────
    investor_html = ""
    if s.get("is_kr_kis"):
        f_eok = s.get("foreign_eok", 0.0)
        i_eok = s.get("inst_eok",    0.0)
        f_col = "#e03131" if f_eok >= 0 else "#1971c2"
        i_col = "#e03131" if i_eok >= 0 else "#1971c2"
        f_str = f"{'▲' if f_eok >= 0 else '▼'} {abs(f_eok):.1f}억원"
        i_str = f"{'▲' if i_eok >= 0 else '▼'} {abs(i_eok):.1f}억원"
        investor_html = f"""
    <div style="margin-bottom:16px;padding:14px;background:#f0f4ff;border-radius:10px;
                border-left:4px solid #364fc7;">
      <div style="font-weight:700;font-size:14px;color:#364fc7;margin-bottom:10px;">
        👥 외국인/기관 순매수 (당일 · KIS)
      </div>
      <table style="width:100%;font-size:13px;border-collapse:collapse;">
        <tr>
          <td style="padding:5px 0;color:#868e96;width:25%;">외국인</td>
          <td style="padding:5px 0;font-weight:700;color:{f_col};">{f_str}</td>
          <td style="padding:5px 0;color:#868e96;width:25%;">기관</td>
          <td style="padding:5px 0;font-weight:700;color:{i_col};">{i_str}</td>
        </tr>
      </table>
    </div>"""

    return f"""
<div style="margin:0 0 28px;border:1px solid #dee2e6;border-radius:14px;
            overflow:hidden;font-family:Apple SD Gothic Neo,맑은 고딕,sans-serif;">

  <!-- 헤더 -->
  <div style="background:linear-gradient(135deg,#1a3a5c,#2d6a9f);
              color:white;padding:16px 20px;">
    <div style="display:flex;justify-content:space-between;align-items:flex-start;">
      <div>
        <span style="font-size:22px;font-weight:700;">{medal} {s['name']}</span>
        <span style="margin-left:10px;padding:3px 10px;border-radius:20px;
                     font-size:12px;background:{period_badge[0]};color:{period_badge[1]};">
          {period_badge[2]}
        </span>
        <span style="margin-left:6px;padding:3px 10px;border-radius:20px;
                     font-size:12px;background:rgba(255,255,255,0.2);color:white;">
          {s['sector']}
        </span>
      </div>
      <div style="text-align:right;font-size:12px;opacity:0.85;">{s['ticker']}</div>
    </div>
    <div style="margin-top:10px;font-size:26px;font-weight:700;">
      {price_str}
      <span style="font-size:15px;margin-left:10px;color:{chg_col if s['change']>=0 else '#74c0fc'};">
        {chg_arr} {abs(s['change']):.2f}%
      </span>
    </div>
    <div style="margin-top:6px;font-size:13px;opacity:0.8;">
      시가총액 {mktcap_str} &nbsp;|&nbsp; 52주 범위: {low_str} ~ {high_str}
    </div>
  </div>

  <!-- 종합점수 + 리스크 -->
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

    <!-- 추천 이유 -->
    <div style="margin-bottom:16px;">
      <div style="font-weight:700;font-size:15px;color:#1a3a5c;margin-bottom:8px;">
        이 종목을 추천하는 이유
      </div>
      <ul style="margin:0;padding-left:0;list-style:none;">{reason_html}</ul>
      {warn_html}
    </div>

    <!-- 기술적 지표 -->
    <div style="margin-bottom:16px;padding:14px;background:#f8f9fa;border-radius:10px;">
      <div style="font-weight:700;font-size:14px;color:#1a3a5c;margin-bottom:10px;">기술적 지표</div>
      <table style="width:100%;font-size:13px;border-collapse:collapse;">
        <tr>
          <td style="padding:4px 0;color:#868e96;width:30%;">RSI (과매도/과매수)</td>
          <td style="padding:4px 0;font-weight:600;color:{'#2f9e44' if s['rsi']<45 else '#e67700' if s['rsi']>70 else '#1a1a1a'};">
            {s['rsi']} {'← 매수 구간' if s['rsi']<45 else '← 과열 주의' if s['rsi']>70 else ''}
          </td>
          <td style="padding:4px 0;color:#868e96;width:30%;">MACD</td>
          <td style="padding:4px 0;font-weight:600;color:{'#2f9e44' if s['macd_cross'] else '#868e96'};">
            {'골든크로스 ✓' if s['macd_cross'] else '데드크로스'}
          </td>
        </tr>
        <tr>
          <td style="padding:4px 0;color:#868e96;">볼린저밴드 위치</td>
          <td style="padding:4px 0;font-weight:600;">{s['bb_pct']}% {'← 하단(매수)' if s['bb_pct']<20 else '← 상단(과열)' if s['bb_pct']>80 else ''}</td>
          <td style="padding:4px 0;color:#868e96;">거래량</td>
          <td style="padding:4px 0;font-weight:600;color:{'#e67700' if s['vol_ratio']>150 else '#1a1a1a'};">
            평균 대비 {s['vol_ratio']:.0f}%
          </td>
        </tr>
        <tr>
          <td style="padding:4px 0;color:#868e96;">52주 저점 대비</td>
          <td style="padding:4px 0;font-weight:600;color:{'#2f9e44' if s['pct_from_low']<15 else '#1a1a1a'};">
            +{s['pct_from_low']}%
          </td>
          <td style="padding:4px 0;color:#868e96;">52주 고점 대비</td>
          <td style="padding:4px 0;font-weight:600;">{s['pct_from_high']}%</td>
        </tr>
      </table>
    </div>

    {dart_fin_html}

    {investor_html}

    <!-- 200만원 투자 시뮬레이션 -->
    <div style="margin-bottom:16px;border:2px solid #1a3a5c;border-radius:10px;overflow:hidden;">
      <div style="background:#1a3a5c;color:white;padding:10px 16px;font-weight:700;font-size:14px;">
        💰 200만원 투자 시뮬레이션
      </div>
      <div style="padding:14px 16px;">
        <table style="width:100%;font-size:13px;border-collapse:collapse;">
          <tr style="background:#e7f5ff;">
            <td style="padding:8px;border-radius:6px;font-weight:600;">매수가 (지정가)</td>
            <td style="padding:8px;font-weight:700;color:#1971c2;font-size:15px;">{buy_str}</td>
            <td style="padding:8px;">매수 수량</td>
            <td style="padding:8px;font-weight:700;">{s['shares']}주</td>
          </tr>
          <tr>
            <td style="padding:8px;color:#2f9e44;font-weight:600;">✅ 1차 목표가 (+10%)</td>
            <td style="padding:8px;font-weight:700;color:#2f9e44;">{t1_str}</td>
            <td style="padding:8px;">예상 수익</td>
            <td style="padding:8px;font-weight:700;color:#2f9e44;">+{s['profit1']:,.0f}원</td>
          </tr>
          <tr style="background:#f8f9fa;">
            <td style="padding:8px;color:#1971c2;font-weight:600;">✅ 2차 목표가 (+20%)</td>
            <td style="padding:8px;font-weight:700;color:#1971c2;">{t2_str}</td>
            <td style="padding:8px;">예상 수익</td>
            <td style="padding:8px;font-weight:700;color:#1971c2;">+{s['profit2']:,.0f}원</td>
          </tr>
          <tr>
            <td style="padding:8px;color:#7950f2;font-weight:600;">✅ 장기 목표가 (+40%)</td>
            <td style="padding:8px;font-weight:700;color:#7950f2;">{t3_str}</td>
            <td style="padding:8px;">예상 수익</td>
            <td style="padding:8px;font-weight:700;color:#7950f2;">+{s['profit3']:,.0f}원</td>
          </tr>
          <tr style="background:#fff5f5;">
            <td style="padding:8px;color:#e03131;font-weight:600;">🛑 손절가 (-7%)</td>
            <td style="padding:8px;font-weight:700;color:#e03131;">{stop_str}</td>
            <td style="padding:8px;">최대 손실</td>
            <td style="padding:8px;font-weight:700;color:#e03131;">-{s['loss_amt']:,.0f}원</td>
          </tr>
        </table>
      </div>
    </div>

    <!-- 분할매수 전략 -->
    <div style="margin-bottom:16px;padding:14px;background:#fff9db;border-radius:10px;
                border-left:4px solid #f59f00;">
      <div style="font-weight:700;font-size:14px;color:#e67700;margin-bottom:8px;">
        📌 분할매수 전략 (리스크 분산 추천)
      </div>
      <div style="font-size:13px;line-height:1.8;color:#1a1a1a;">
        1차 매수: 지금 바로 <b>{s['split1_shares']}주</b> 매수 (100만원)<br>
        2차 매수: {sp2_str} 이하로 떨어지면 추가로 <b>{s['split2_shares']}주</b> 매수 (나머지 100만원)<br>
        <span style="color:#868e96;font-size:12px;">→ 한 번에 다 사는 것보다 리스크를 절반으로 줄일 수 있어요</span>
      </div>
    </div>

    <!-- 투자기간 전략 -->
    <div style="padding:14px;background:#f3f0ff;border-radius:10px;
                border-left:4px solid #7950f2;">
      <div style="font-weight:700;font-size:14px;color:#7950f2;margin-bottom:6px;">
        📅 {s['period']} 투자 전략
      </div>
      <div style="font-size:13px;color:#1a1a1a;">{s['period_strategy']}</div>
    </div>

    {dart_sig_html}

  </div>
</div>"""


def dart_alerts_section_html(dart_alerts: list) -> str:
    """전체 종목 DART 공시 알림 요약 섹션"""
    if not dart_alerts:
        return ""

    by_type: dict = {}
    for a in dart_alerts:
        by_type.setdefault(a["key"], []).append(a)

    rows = []
    for key in ["rights", "buyback", "order", "dividend"]:
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
                dt = it["date"]
                date_fmt = f"{dt[:4]}.{dt[4:6]}.{dt[6:]}" if len(dt) == 8 else dt
                items_html += (
                    f'<div style="padding:9px 14px;margin:5px 0;background:{bg};'
                    f'border:1px solid {border};border-radius:8px;font-size:13px;">'
                    f'<b>{a["name"]}</b>'
                    f'<span style="margin-left:6px;padding:2px 8px;border-radius:12px;'
                    f'font-size:11px;background:rgba(0,0,0,0.07);">{a["sector"]}</span>'
                    f' — {it["title"]} '
                    f'<span style="color:#868e96;">({date_fmt})</span> '
                    f'<a href="{it["url"]}" style="color:{color};font-size:12px;text-decoration:none;">'
                    f'공시 보기 →</a>'
                    f'</div>'
                )

        rows.append(f"""
      <div style="margin-bottom:16px;">
        <div style="font-weight:700;font-size:14px;color:{color};margin-bottom:8px;">
          {icon} {label}
        </div>
        {items_html}
      </div>""")

    if not rows:
        return ""

    return f"""
  <div style="padding:20px 16px;">
    <h2 style="color:#1a3a5c;font-size:20px;margin:0 0 16px;
               padding-bottom:10px;border-bottom:3px solid #f59f00;">
      📢 DART 공시 알림 (최근 7일)
    </h2>
    <div style="padding:16px 20px;background:white;border-radius:12px;
                border:1px solid #dee2e6;">
      {"".join(rows)}
      <div style="font-size:12px;color:#868e96;margin-top:8px;">
        * 금융감독원 전자공시시스템(DART) 공시 기준. 링크 클릭 시 원문 확인 가능.
      </div>
    </div>
  </div>"""


def make_report(kr_top: list, us_top: list, avoid: list,
                mood: dict, dart_alerts: list | None = None) -> str:
    today = datetime.now().strftime("%Y년 %m월 %d일 (%A)")
    now   = datetime.now().strftime("%H:%M")

    mood_color = {"양호": "#2f9e44", "주의": "#e67700", "하락": "#e67700", "위험": "#e03131"}.get(mood["status"], "#868e96")
    sp500_col  = "#e03131" if mood["sp500_chg"] >= 0 else "#1971c2"
    kos_col    = "#e03131" if mood["kospi_chg"] >= 0 else "#1971c2"

    html = f"""
<div style="font-family:Apple SD Gothic Neo,맑은 고딕,sans-serif;
            max-width:680px;margin:0 auto;color:#1a1a1a;">

  <!-- 상단 헤더 -->
  <div style="background:linear-gradient(135deg,#1a3a5c,#0d2137);
              color:white;padding:28px 24px;border-radius:16px 16px 0 0;">
    <h1 style="margin:0 0 6px;font-size:24px;">📊 오늘의 투자 비서 리포트</h1>
    <p style="margin:0;opacity:0.8;font-size:14px;">{today} &nbsp;·&nbsp; 분석완료 {now} KST</p>
    <div style="margin-top:14px;padding:12px 16px;background:rgba(255,255,255,0.1);
                border-radius:10px;font-size:13px;">
      {mood['advice']}
    </div>
  </div>

  <!-- 시장 브리핑 -->
  <div style="background:#f8f9fa;padding:18px 24px;border-bottom:1px solid #dee2e6;">
    <div style="font-weight:700;font-size:15px;color:#1a3a5c;margin-bottom:12px;">
      🌏 오늘의 시장 브리핑
    </div>
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
        <div style="font-size:12px;color:#868e96;">금 시세</div>
        <div style="font-size:16px;font-weight:700;">${mood['gold']:,.0f}</div>
      </div>
    </div>
  </div>

  <!-- 국내 추천 -->
  <div style="padding:20px 16px 8px;">
    <h2 style="color:#1a3a5c;font-size:20px;margin:0 0 16px;
               padding-bottom:10px;border-bottom:3px solid #1a3a5c;">
      🇰🇷 국내 추천 종목 TOP 3
    </h2>
    {"".join(card_html(i, s) for i, s in enumerate(kr_top))}
  </div>

  <!-- 해외 추천 -->
  <div style="padding:20px 16px 8px;">
    <h2 style="color:#1a3a5c;font-size:20px;margin:0 0 16px;
               padding-bottom:10px;border-bottom:3px solid #e67700;">
      🇺🇸 해외 추천 종목 TOP 3
    </h2>
    {"".join(card_html(i, s) for i, s in enumerate(us_top))}
  </div>
"""

    if avoid:
        avoid_html = "".join(
            f'<div style="padding:10px 14px;margin:6px 0;background:#fff5f5;'
            f'border-left:4px solid #e03131;border-radius:0 8px 8px 0;font-size:13px;">'
            f'<b>{a["name"]} ({a["ticker"]})</b> — '
            f'RSI {a["rsi"]} / 최근1달 {a["ret_1m"]}% / 거래량 평균대비 {a["vol_ratio"]:.0f}%'
            f'</div>'
            for a in avoid[:5]
        )
        html += f"""
  <div style="padding:16px;margin:0 16px 20px;background:#fff5f5;
              border-radius:12px;border:1px solid #ffc9c9;">
    <div style="font-weight:700;font-size:15px;color:#e03131;margin-bottom:10px;">
      🚫 오늘 피해야 할 종목
    </div>
    {avoid_html}
    <div style="font-size:12px;color:#868e96;margin-top:8px;">
      * RSI 과매수 / 급락 중 / 거래량 급감 종목이에요. 지금은 관망하세요.
    </div>
  </div>"""

    # DART 공시 알림 섹션
    if dart_alerts:
        html += dart_alerts_section_html(dart_alerts)

    html += """
  <!-- 하단 안내 -->
  <div style="padding:20px 24px;background:#f8f9fa;border-radius:0 0 16px 16px;
              font-size:12px;color:#868e96;line-height:2;">
    ⚠️ 본 리포트는 자동 분석된 참고 정보입니다. 최종 투자 판단은 반드시 본인이 직접 하세요.<br>
    투자 손익의 책임은 전적으로 투자자 본인에게 있으며 어떤 수익도 보장하지 않습니다.<br>
    🟢낮음=안정적 &nbsp;|&nbsp; 🟡중간=보통 &nbsp;|&nbsp; 🔴높음=신중 &nbsp;|&nbsp;
    단기=1~4주 &nbsp;|&nbsp; 중기=1~6개월 &nbsp;|&nbsp; 장기=1년+
  </div>
</div>"""

    return html


def make_telegram_message(kr_top: list, us_top: list, avoid: list,
                          mood: dict, dart_alerts: list | None = None) -> str:
    today = datetime.now().strftime("%Y년 %m월 %d일")
    now   = datetime.now().strftime("%H:%M")
    medals = ["🥇", "🥈", "🥉", "4위", "5위"]

    kos_arr = "▲" if mood["kospi_chg"] >= 0 else "▼"
    sp5_arr = "▲" if mood["sp500_chg"] >= 0 else "▼"

    lines = [
        "<b>📊 투자 비서 리포트</b>",
        f"<i>{today} · {now} KST</i>",
        "",
        "<b>🌏 시장 브리핑</b>",
        (f"코스피 {mood['kospi_price']:,.0f} {kos_arr}{abs(mood['kospi_chg']):.2f}%"
         f"  |  S&P500 {sp5_arr}{abs(mood['sp500_chg']):.2f}%"),
        f"VIX {mood['vix']} ({mood['status']})  |  달러/원 {mood['usdkrw']:,.0f}원",
        f"WTI ${mood['wti']}  |  금 ${mood['gold']:,.0f}",
        mood["advice"],
        "",
        "<b>🇰🇷 국내 추천 TOP 5</b>",
    ]
    for i, s in enumerate(kr_top):
        chg_arr = "▲" if s["change"] >= 0 else "▼"
        lines.append(f"{medals[i]} <b>{s['name']}</b> — {s['score']}점 {s['risk']} ({s['period']})")
        lines.append(f"   {s['price']:,.0f}원 {chg_arr}{abs(s['change']):.2f}%")
        if s.get("is_kr_kis"):
            f_eok = s.get("foreign_eok", 0.0)
            i_eok = s.get("inst_eok",    0.0)
            lines.append(
                f"   외국인 {'▲' if f_eok >= 0 else '▼'}{abs(f_eok):.1f}억"
                f"  기관 {'▲' if i_eok >= 0 else '▼'}{abs(i_eok):.1f}억"
            )

    lines += ["", "<b>🇺🇸 해외 추천 TOP 5</b>"]
    for i, s in enumerate(us_top):
        chg_arr = "▲" if s["change"] >= 0 else "▼"
        lines.append(f"{medals[i]} <b>{s['name']}</b> — {s['score']}점 {s['risk']} ({s['period']})")
        lines.append(f"   ${s['price']:,.2f} {chg_arr}{abs(s['change']):.2f}%")

    if avoid:
        lines += ["", "<b>🚫 오늘 피해야 할 종목</b>"]
        for a in avoid[:5]:
            lines.append(f"• {a['name']} — RSI {a['rsi']} / 1달 {a['ret_1m']:+.1f}%")

    if dart_alerts:
        lines += ["", "<b>📢 DART 공시 알림 (최근 7일)</b>"]
        for a in dart_alerts[:5]:
            icon = "⚠️" if a["is_risk"] else "✅"
            lines.append(f"{icon} <b>{a['name']}</b> — {a['label']}: {a['items'][0]['title']}")

    lines += ["", "<i>⚠️ 본 리포트는 참고용입니다. 투자 판단은 본인이 직접 하세요.</i>"]
    return "\n".join(lines)


def send_telegram(text_msg: str, html_report: str):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("  [텔레그램] TELEGRAM_TOKEN 또는 TELEGRAM_CHAT_ID 미설정 — 전송 건너뜀")
        return

    base = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"
    stamp = datetime.now().strftime("%m%d_%H%M")

    # 텍스트 요약 (4096자 초과 시 단락 단위로 분할)
    MAX = 4096
    chunks, buf = [], text_msg
    while buf:
        if len(buf) <= MAX:
            chunks.append(buf)
            break
        cut = buf.rfind("\n\n", 0, MAX)
        if cut == -1:
            cut = MAX
        chunks.append(buf[:cut])
        buf = buf[cut:].lstrip()

    for chunk in chunks:
        r = requests.post(
            f"{base}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": chunk, "parse_mode": "HTML"},
            timeout=15,
        )
        if not r.ok:
            print(f"  [텔레그램] 메시지 전송 실패: {r.text[:200]}")

    # HTML 전문 파일 첨부
    r = requests.post(
        f"{base}/sendDocument",
        data={
            "chat_id": TELEGRAM_CHAT_ID,
            "caption": f"📊 투자 비서 리포트 전문 ({datetime.now().strftime('%m/%d %H:%M')})",
        },
        files={"document": (f"report_{stamp}.html", html_report.encode("utf-8"), "text/html")},
        timeout=30,
    )
    if not r.ok:
        print(f"  [텔레그램] 파일 전송 실패: {r.text[:200]}")
    else:
        print("  텔레그램 전송 완료!")


def run():
    print("=" * 55)
    print("투자 비서 v5.0 시작")
    kis_status = "KIS API 연결됨" if _kis.available() else "KIS API 키 없음 (yfinance 폴백)"
    print(f"국내 {len(KR_STOCKS)}종목 + 해외 {len(US_STOCKS)}종목 분석 중...")
    print(f"국내주식 데이터 소스: {kis_status}")
    print("=" * 55)

    print("\n[시장 분위기 파악 중...]")
    mood = get_market_mood()
    print(f"  → 시장상태: {mood['status']} / VIX: {mood['vix']} / 코스피: {mood['kospi_chg']:+.2f}%")

    print("\n[DART 공시 데이터 수집 중...]")
    all_dart = get_all_dart_data(KR_STOCKS)
    sig_count = sum(
        1 for dd in all_dart.values()
        for items in dd["signals"].values() if items
    )
    print(f"  → {len(all_dart)}개 종목 수집 완료 / 주요 공시 {sig_count}건 감지")

    print("\n[국내 종목 분석 중...]")
    kr_results = []
    for ticker, val in KR_STOCKS.items():
        name, period, sector = val
        print(f"  분석: {name}")
        r = analyze(ticker, name, period, sector, dart_data=all_dart.get(ticker))
        if r:
            kr_results.append(r)
        time.sleep(0.8)

    print("\n[해외 종목 분석 중...]")
    us_results = []
    for ticker, val in US_STOCKS.items():
        name, period, sector = val
        print(f"  분석: {name}")
        r = analyze(ticker, name, period, sector)
        if r:
            us_results.append(r)
        time.sleep(0.8)

    kr_sorted = sorted(kr_results, key=lambda x: x["score"], reverse=True)
    us_sorted = sorted(us_results, key=lambda x: x["score"], reverse=True)
    kr_top3   = kr_sorted[:5]
    us_top3   = us_sorted[:5]

    avoid_list = [s for s in kr_results + us_results if s["rsi"] > 70 or s["ret_1m"] < -15]
    avoid_list = sorted(avoid_list, key=lambda x: x["ret_1m"])[:5]

    # DART 알림 목록 구성
    dart_alerts = []
    for ticker, dd in all_dart.items():
        name, _, sector = KR_STOCKS[ticker]
        for key, items in dd["signals"].items():
            if items:
                _, label, is_risk = SIGNAL_DEFS[key]
                dart_alerts.append({
                    "ticker":  ticker,
                    "name":    name,
                    "sector":  sector,
                    "key":     key,
                    "label":   label,
                    "is_risk": is_risk,
                    "items":   items,
                })

    print("\n[국내 TOP 5]")
    for s in kr_top3:
        print(f"  {s['name']} — {s['score']}점 / {s['risk']} / {s['period']}")

    print("\n[해외 TOP 5]")
    for s in us_top3:
        print(f"  {s['name']} — {s['score']}점 / {s['risk']} / {s['period']}")

    if dart_alerts:
        print(f"\n[DART 공시 알림 {len(dart_alerts)}건]")
        for a in dart_alerts:
            flag = "⚠️" if a["is_risk"] else "✅"
            print(f"  {flag} {a['name']} — {a['label']}: {a['items'][0]['title']}")

    print("\n[리포트 생성 및 텔레그램 전송 중...]")
    html = make_report(kr_top3, us_top3, avoid_list, mood, dart_alerts=dart_alerts)
    text = make_telegram_message(kr_top3, us_top3, avoid_list, mood, dart_alerts=dart_alerts)
    send_telegram(text, html)
    print("\n완료! 텔레그램을 확인하세요.")


if __name__ == "__main__":
    run()
