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

# ════════════════════════════════════════════════
# 시간대 강제 설정 (KST)
#
# 1단계: TZ 환경변수 + tzset() — Linux/Mac에서 _now_kst() 영향
# 2단계: zoneinfo.ZoneInfo("Asia/Seoul")를 통한 명시적 KST — GitHub Actions
#         Ubuntu에서 tzset이 datetime에 적용 안 되는 케이스 백업
#
# 모든 _now_kst() 호출은 _now_kst() 헬퍼 사용 (둘 다 안 먹혀도 작동 보장).
# ════════════════════════════════════════════════
os.environ["TZ"] = "Asia/Seoul"
try:
    time.tzset()
except AttributeError:
    pass  # Windows에는 tzset 없음 — 로컬 개발 시 무시

try:
    from zoneinfo import ZoneInfo  # Python 3.9+
    _KST_TZ = ZoneInfo("Asia/Seoul")
except Exception:
    _KST_TZ = None


def _now_kst() -> datetime:
    """항상 한국시간(KST)을 timezone-naive datetime으로 반환.

    GitHub Actions UTC 러너에서 tzset이 datetime에 적용 안 되는 문제 회피.
    zoneinfo가 있으면 명시적 KST 변환 후 tzinfo 제거(naive로 통일).
    없으면 _now_kst() 폴백 (이때는 TZ 환경변수에 의존).
    """
    if _KST_TZ is not None:
        return datetime.now(_KST_TZ).replace(tzinfo=None)
    return _now_kst()

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

# Claude AI 모델 — 모델 변경 시 이 값만 수정하면 됨
CLAUDE_MODEL = os.environ.get("CLAUDE_MODEL", "claude-sonnet-4-6")

# KR 전용 모드 — True면 해외 종목 분석/추천 비활성화 (미국 매크로/마감 브리핑은 유지).
# 환경변수 KR_ONLY=false 로 설정하면 해외 종목도 다시 분석.
KR_ONLY = os.environ.get("KR_ONLY", "true").lower() in ("true", "1", "yes", "on")

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

# 대시보드 URL (GitHub Pages). 사용자가 활성화 후 자동으로 노출됨.
DASHBOARD_URL = os.environ.get("DASHBOARD_URL", "https://lee91251.github.io/stock-bot/")
DASHBOARD_HTML_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "docs", "index.html")
DASHBOARD_CACHE   = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dashboard_cache.json")
PORTFOLIO_HISTORY = os.path.join(os.path.dirname(os.path.abspath(__file__)), "portfolio_history.json")
DART_SEEN_FILE    = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dart_seen.json")
POSITIONS_FILE    = os.path.join(os.path.dirname(os.path.abspath(__file__)), "positions.json")
MARKET_SCAN_N     = 1500
KIS_BASE       = "https://openapi.koreainvestment.com:9443"
KIS_PAPER_BASE = "https://openapivts.koreainvestment.com:29443"
DART_BASE      = "https://opendart.fss.or.kr/api"

# ════════════════════════════════════════════════
# 자동매매 (스윙 모의투자) 설정
# ════════════════════════════════════════════════
# PAPER_TRADING=true 이면 모의투자 도메인 + 모의투자 키 사용.
# 미설정 시 안전 기본값으로 매매 자체가 차단됨.
PAPER_TRADING = os.environ.get("PAPER_TRADING", "").lower() in ("true", "1", "yes", "on")
AUTO_TRADE_ENABLED = os.environ.get("AUTO_TRADE_ENABLED", "true").lower() in ("true", "1", "yes", "on")

KIS_PAPER_APP_KEY    = os.environ.get("KIS_PAPER_APP_KEY",    "")
KIS_PAPER_APP_SECRET = os.environ.get("KIS_PAPER_APP_SECRET", "")
KIS_PAPER_ACCOUNT    = os.environ.get("KIS_PAPER_ACCOUNT",    "")  # 형식: 12345678-01

# 스윙 자동매매 파라미터 (장기/가치 기반인 STOP_LOSS_PCT/TARGET*_PCT와는 별개)
SWING_SCORE_MIN          = 65          # 매수 임계 스윙 점수 (5/4: 70→65, 100종목 전체 70+ 통과 0개 확인 후 완화)
SWING_TARGET1_PCT        = 0.06        # +6% 절반 매도
SWING_TARGET2_PCT        = 0.10        # +10% 전량 매도
SWING_STOP_LOSS_PCT      = 0.04        # -4% 손절
SWING_MAX_HOLD_DAYS      = 5           # 5거래일 후 강제 매도
SWING_MAX_DAILY_BUY      = 5           # 하루 최대 신규 매수 종목
SWING_MAX_DAILY_AMT      = 10_000_000  # 하루 최대 매수 금액(원)
SWING_LOSS_COOLDOWN_DAYS = 3           # 손절 후 같은 종목 재매수 금지 기간
SWING_PRE_ALERT_SEC      = 30          # 매수 직전 사전 알림 + /취소 대기 시간
SWING_DAILY_TRADE_CAP    = 20          # 일일 매매 횟수 한도 (폭주 차단)

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
        if not self.available() or _now_kst() < self._token_exp:
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
            self._token_exp = _now_kst() + timedelta(
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
        end_dt   = _now_kst().strftime("%Y%m%d")
        start_dt = (_now_kst() - timedelta(days=months * 31)).strftime("%Y%m%d")
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
# KIS 매매 클라이언트 (모의/실전 도메인 분기)
# ════════════════════════════════════════════════
class KisTradingClient:
    """모의투자(VTS) / 실전투자 도메인 분기 매매 클라이언트.

    PAPER_TRADING=true 일 때만 모의투자 도메인+키 사용. 키가 없으면 매매 자체가 차단됨.
    안전 기본값: PAPER_TRADING 미설정 → available()=False → 모든 주문 거부.
    """
    def __init__(self):
        self.paper      = PAPER_TRADING
        self.base       = KIS_PAPER_BASE if self.paper else KIS_BASE
        self.app_key    = KIS_PAPER_APP_KEY    if self.paper else KIS_APP_KEY
        self.app_secret = KIS_PAPER_APP_SECRET if self.paper else KIS_APP_SECRET
        self.account    = KIS_PAPER_ACCOUNT    if self.paper else os.environ.get("KIS_REAL_ACCOUNT", "")
        self._token: str = ""
        self._token_exp: datetime = datetime.min

    def mode_tag(self) -> str:
        return "[모의]" if self.paper else "[실전]"

    def available(self) -> bool:
        return bool(self.app_key and self.app_secret and self.account)

    def _account_split(self) -> tuple:
        s = self.account.replace("-", "").strip()
        return s[:8], s[8:10] if len(s) >= 10 else "01"

    def _ensure_token(self):
        if not self.available() or _now_kst() < self._token_exp:
            return
        try:
            r = requests.post(
                f"{self.base}/oauth2/tokenP",
                json={"grant_type": "client_credentials",
                      "appkey": self.app_key, "appsecret": self.app_secret},
                timeout=10,
            )
            d = r.json()
            self._token     = d.get("access_token", "")
            self._token_exp = _now_kst() + timedelta(
                seconds=int(d.get("expires_in", 86400)) - 600
            )
            if not self._token:
                print(f"  [매매{self.mode_tag()}] 토큰 발급 오류: {d.get('msg1','access_token 없음')}")
        except Exception as e:
            print(f"  [매매{self.mode_tag()}] 토큰 발급 실패: {e}")

    def _order(self, code: str, qty: int, side: str) -> dict:
        """side: 'buy' or 'sell'. 시장가 주문(ORD_DVSN=01)."""
        self._ensure_token()
        if not self._token:
            return {"ok": False, "msg": "토큰 없음"}
        cano, prdt = self._account_split()
        if side == "buy":
            tr_id = "VTTC0802U" if self.paper else "TTTC0802U"
        else:
            tr_id = "VTTC0801U" if self.paper else "TTTC0801U"
        try:
            r = requests.post(
                f"{self.base}/uapi/domestic-stock/v1/trading/order-cash",
                headers={
                    "content-type":  "application/json; charset=utf-8",
                    "authorization": f"Bearer {self._token}",
                    "appkey":        self.app_key,
                    "appsecret":     self.app_secret,
                    "tr_id":         tr_id,
                    "custtype":      "P",
                },
                json={
                    "CANO":         cano,
                    "ACNT_PRDT_CD": prdt,
                    "PDNO":         code,
                    "ORD_DVSN":     "01",   # 01: 시장가
                    "ORD_QTY":      str(qty),
                    "ORD_UNPR":     "0",
                },
                timeout=10,
            )
            d = r.json()
            if d.get("rt_cd") == "0":
                return {
                    "ok":       True,
                    "order_no": d.get("output", {}).get("ODNO", ""),
                    "msg":      d.get("msg1", ""),
                }
            return {"ok": False, "msg": d.get("msg1", "주문 실패")}
        except Exception as e:
            return {"ok": False, "msg": str(e)}

    def buy(self, code: str, qty: int) -> dict:
        return self._order(code, qty, "buy")

    def sell(self, code: str, qty: int) -> dict:
        return self._order(code, qty, "sell")

    def get_balance(self) -> dict:
        """잔고 조회. 정상 시 {'cash': int, 'positions': [...], 'total_eval': int} 반환."""
        self._ensure_token()
        if not self._token:
            return {}
        cano, prdt = self._account_split()
        tr_id = "VTTC8434R" if self.paper else "TTTC8434R"
        try:
            r = requests.get(
                f"{self.base}/uapi/domestic-stock/v1/trading/inquire-balance",
                headers={
                    "content-type":  "application/json; charset=utf-8",
                    "authorization": f"Bearer {self._token}",
                    "appkey":        self.app_key,
                    "appsecret":     self.app_secret,
                    "tr_id":         tr_id,
                    "custtype":      "P",
                },
                params={
                    "CANO":                  cano,
                    "ACNT_PRDT_CD":          prdt,
                    "AFHR_FLPR_YN":          "N",
                    "OFL_YN":                "",
                    "INQR_DVSN":             "02",
                    "UNPR_DVSN":             "01",
                    "FUND_STTL_ICLD_YN":     "N",
                    "FNCG_AMT_AUTO_RDPT_YN": "N",
                    "PRCS_DVSN":             "01",
                    "CTX_AREA_FK100":        "",
                    "CTX_AREA_NK100":        "",
                },
                timeout=10,
            )
            d = r.json()
            if d.get("rt_cd") != "0":
                print(f"  [매매{self.mode_tag()}] 잔고조회 오류: {d.get('msg1','')}")
                return {}
            out2 = d.get("output2", [{}])
            summary = out2[0] if isinstance(out2, list) and out2 else {}
            return {
                "cash":       int(_safe_float(summary.get("dnca_tot_amt"))),
                "total_eval": int(_safe_float(summary.get("tot_evlu_amt"))),
                "positions":  d.get("output1", []),
            }
        except Exception as e:
            print(f"  [매매{self.mode_tag()}] 잔고조회 실패: {e}")
            return {}


_kis_trading: 'KisTradingClient | None' = None


def get_trading_client() -> KisTradingClient:
    global _kis_trading
    if _kis_trading is None:
        _kis_trading = KisTradingClient()
    return _kis_trading


# ════════════════════════════════════════════════
# 자동매매 포지션 / 상태 관리 (positions.json)
# ════════════════════════════════════════════════
def load_positions() -> dict:
    """현재 보유 포지션 + 거래 이력 + 일일 카운터 + 정지 상태."""
    try:
        if os.path.exists(POSITIONS_FILE):
            with open(POSITIONS_FILE, "r", encoding="utf-8") as f:
                d = json.load(f)
                d.setdefault("positions",     {})
                d.setdefault("history",       [])
                d.setdefault("daily",         {})
                d.setdefault("loss_cooldown", {})
                d.setdefault("halted",        False)
                d.setdefault("pending_cancel", False)
                return d
    except Exception:
        pass
    return {
        "positions":      {},
        "history":        [],
        "daily":          {},
        "loss_cooldown":  {},
        "halted":         False,
        "pending_cancel": False,
    }


def save_positions(data: dict):
    try:
        with open(POSITIONS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2, default=str)
    except Exception as e:
        print(f"  [포지션] 저장 실패: {e}")


def _today_str() -> str:
    return _now_kst().strftime("%Y-%m-%d")


def _ensure_daily(pos: dict, date: str) -> dict:
    pos.setdefault("daily", {}).setdefault(
        date, {"buy_count": 0, "buy_amount": 0, "trade_count": 0}
    )
    return pos["daily"][date]


def _trading_days_between(start_iso: str, end_iso: str) -> int:
    """시작일~종료일 사이 평일(월~금) 수 (시작일 제외, 종료일 포함)."""
    try:
        s = datetime.strptime(start_iso, "%Y-%m-%d")
        e = datetime.strptime(end_iso,   "%Y-%m-%d")
    except Exception:
        return 0
    days = 0
    d = s + timedelta(days=1)
    while d <= e:
        if d.weekday() < 5:
            days += 1
        d += timedelta(days=1)
    return days


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
    year = _now_kst().year - 1
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
    today = _now_kst()
    start = (today - timedelta(days=days)).strftime("%Y%m%d")
    end   = today.strftime("%Y%m%d")
    d = _dart_req("list.json", {
        "corp_code":  corp_code,
        "bgn_de":     start,
        "end_de":     end,
        "page_count": "40",
    })
    return d.get("list", [])


def fetch_recent_disclosures_by_stock_codes(stock_codes: set, days: int = 1) -> list:
    """주식 코드 set에 매칭되는 최근 N일 공시 fetch (DART list.json 전체 → stock_code 필터).

    daily / autosell 끝에 호출되어 보유 종목 + KR_STOCKS 새 공시 알림용.
    """
    if not DART_API_KEY or not stock_codes:
        return []
    today = _now_kst()
    start = (today - timedelta(days=days)).strftime("%Y%m%d")
    end   = today.strftime("%Y%m%d")
    all_items = []
    # 최대 300건 (3페이지 × 100)
    for page in range(1, 4):
        d = _dart_req("list.json", {
            "bgn_de": start, "end_de": end,
            "page_no": str(page), "page_count": "100",
        })
        items = d.get("list", []) if d else []
        if not items:
            break
        all_items.extend(items)
        if len(items) < 100:
            break
    # stock_codes 필터
    out = []
    for item in all_items:
        sc = item.get("stock_code", "")
        if sc and sc in stock_codes:
            out.append(item)
    return out


def _load_seen_disclosures() -> set:
    """본 공시 ID 캐시 로드. 어제 이전은 자동 정리."""
    try:
        if not os.path.exists(DART_SEEN_FILE):
            return set()
        with open(DART_SEEN_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        # 최근 3일 이내만 유지 (메모리 누수 방지)
        cutoff = (_now_kst() - timedelta(days=3)).strftime("%Y%m%d")
        return set(rid for rid in data.get("seen_ids", []) if rid >= cutoff)
    except Exception:
        return set()


def _save_seen_disclosures(seen: set) -> None:
    try:
        with open(DART_SEEN_FILE, "w", encoding="utf-8") as f:
            json.dump({
                "seen_ids": sorted(seen),
                "updated": _now_kst().strftime("%Y-%m-%d %H:%M:%S"),
            }, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"  [DART] dart_seen 저장 실패: {e}")


_DART_BAD_KEYWORDS = ("유증", "유상증자", "감자", "공시정정", "정정신고",
                      "감사보고서거절", "관리종목", "거래정지", "상장폐지")
_DART_GOOD_KEYWORDS = ("수주", "공급계약", "투자결정", "자사주취득",
                       "배당", "신규시설투자", "분할합병")

def _disclosure_emoji(title: str) -> str:
    if any(k in title for k in _DART_BAD_KEYWORDS):  return "⚠️"
    if any(k in title for k in _DART_GOOD_KEYWORDS): return "✅"
    return "📰"


def collect_new_disclosures(context_label: str = "") -> list:
    """보유 종목 + KR_STOCKS 새 공시 수집 (중복 차단 캐시).

    텔레그램은 **보유 종목 + ⚠️/✅ 키워드 공시만** (중요한 것만 알림).
    전체 공시 list는 반환 → 대시보드에서 표시.
    """
    if not DART_API_KEY:
        return []

    # 1) 대상 종목
    target = set()
    held_codes = set()
    try:
        for h in (check_holdings_alerts() or []):
            c = h.get("code", "")
            if c:
                target.add(c); held_codes.add(c)
    except Exception:
        pass
    try:
        for c in load_positions().get("positions", {}).keys():
            target.add(c); held_codes.add(c)
    except Exception:
        pass
    for ticker in KR_STOCKS.keys():
        target.add(ticker.split(".")[0])
    if not target:
        return []

    # 2) Fetch + 중복 제외
    items = fetch_recent_disclosures_by_stock_codes(target, days=1)
    if not items:
        return []
    seen = _load_seen_disclosures()
    new_items = [it for it in items if it.get("rcept_no") and it["rcept_no"] not in seen]
    if not new_items:
        return []

    # 3) 보유 종목 우선 정렬
    new_items.sort(key=lambda it: (
        0 if it.get("stock_code", "") in held_codes else 1,
        it.get("rcept_dt", ""), it.get("rcept_no", "")
    ))

    # 4) 텔레그램은 핵심만 — 보유 종목 OR ⚠️/✅ 키워드
    critical = []
    for it in new_items:
        title = it.get("report_nm", "")
        code  = it.get("stock_code", "")
        em    = _disclosure_emoji(title)
        is_held = code in held_codes
        if is_held or em in ("⚠️", "✅"):
            critical.append((it, em, is_held))

    if critical:
        header = "📢 <b>DART 공시 (중요)</b>"
        if context_label:
            header += f" <i>({context_label})</i>"
        lines = [header, ""]
        for it, em, is_held in critical[:10]:
            title = it.get("report_nm", "")
            name  = it.get("corp_name", "")
            rno   = it.get("rcept_no", "")
            held_mark = " 🏠" if is_held else ""
            url = f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rno}"
            lines.append(f'{em} <b>{name}</b>{held_mark}')
            lines.append(f'   <a href="{url}">{title}</a>')
        if len(critical) > 10:
            lines.append(f"\n…외 중요 공시 {len(critical) - 10}건 더 (대시보드에서 확인)")
        lines.append(f"\n📊 전체 새 공시 {len(new_items)}건은 대시보드 '공시' 섹션에서.")
        tg_send("\n".join(lines))

    # 5) 캐시 갱신 (모든 새 공시)
    seen.update(it["rcept_no"] for it in new_items if it.get("rcept_no"))
    _save_seen_disclosures(seen)

    # 6) 대시보드용 가공 데이터 반환
    enriched = []
    for it in new_items:
        title = it.get("report_nm", "")
        code  = it.get("stock_code", "")
        rno   = it.get("rcept_no", "")
        enriched.append({
            "name": it.get("corp_name", ""),
            "code": code,
            "title": title,
            "rcept_dt": it.get("rcept_dt", ""),
            "rcept_no": rno,
            "url": f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rno}",
            "emoji": _disclosure_emoji(title),
            "is_held": code in held_codes,
        })
    return enriched


# 과거 호출 호환용 별칭 (이전 코드에서 호출 시 동작 유지 — 알림은 핵심만)
def notify_new_disclosures(context_label: str = "") -> int:
    return len(collect_new_disclosures(context_label=context_label))


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
        mood["vix"]    = round(float(vix.get("regularMarketPrice") or 20), 2)
        mood["usdkrw"] = round(float(usdkrw.get("regularMarketPrice") or 1300), 2)
        mood["wti"]    = round(float(wti.get("regularMarketPrice") or 75), 2)
        mood["gold"]   = round(float(gold.get("regularMarketPrice") or 2000), 2)

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
    # FRED는 미발표월에 "." 또는 빈 값을 반환 — 이를 걸러내고 유효한 숫자 행만 사용.
    try:
        r = requests.get(
            "https://fred.stlouisfed.org/graph/fredgraph.csv?id=CPIAUCSL",
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=12,
        )
        if r.status_code != 200:
            print(f"  [CPI] FRED 응답 오류: status={r.status_code}")
        else:
            rows = [ln.split(",") for ln in r.text.strip().split("\n")[1:] if ln]
            valid_rows = []
            for row in rows:
                if len(row) < 2:
                    continue
                try:
                    float(row[1])  # "." 또는 빈 값이면 ValueError
                    valid_rows.append(row)
                except (ValueError, TypeError):
                    continue
            print(f"  [CPI] FRED 응답 {len(rows)}행, 유효 {len(valid_rows)}행")
            if len(valid_rows) >= 13:
                v_now  = float(valid_rows[-1][1])
                v_prev = float(valid_rows[-2][1])
                v_yr   = float(valid_rows[-13][1])
                macro["cpi_yoy"]   = round((v_now - v_yr)   / v_yr   * 100, 2)
                macro["cpi_mom"]   = round((v_now - v_prev) / v_prev * 100, 2)
                macro["cpi_month"] = valid_rows[-1][0][:7]
                print(f"  [CPI] {macro['cpi_month']} YoY {macro['cpi_yoy']}% / MoM {macro['cpi_mom']}%")
            else:
                print(f"  [CPI] 유효 행 부족 (13개 필요, 실제 {len(valid_rows)}개)")
    except Exception as e:
        print(f"  [CPI] 수집 실패: {e}")

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
    # ── 조선 (4) ──
    "009540.KS": ("HD한국조선해양",    "중기", "조선"),
    "010140.KS": ("삼성중공업",        "중기", "조선"),
    "042660.KS": ("한화오션",          "중기", "조선"),
    "010620.KS": ("HD현대미포",        "중기", "조선"),
    # ── 방산 (7) ──
    "012450.KS": ("한화에어로스페이스","단기", "방산"),
    "047810.KS": ("한국항공우주",      "단기", "방산"),
    "000880.KS": ("한화",              "중기", "방산"),
    "064350.KS": ("현대로템",          "단기", "방산"),
    "079550.KS": ("LIG넥스원",         "단기", "방산"),
    "103140.KS": ("풍산",              "단기", "방산"),
    "272210.KS": ("한화시스템",        "단기", "방산"),
    # ── 원전/전력/신재생 (4) ──
    "034020.KS": ("두산에너빌리티",    "장기", "원전"),
    "267260.KS": ("HD현대일렉트릭",    "단기", "전력"),
    "298040.KS": ("효성중공업",        "중기", "전력"),
    "009830.KS": ("한화솔루션",        "장기", "신재생"),
    "015760.KS": ("한국전력",          "장기", "신재생"),
    # ── 반도체 (6) ──
    "005930.KS": ("삼성전자",          "장기", "반도체"),
    "000660.KS": ("SK하이닉스",        "장기", "반도체"),
    "042700.KS": ("한미반도체",        "단기", "반도체"),
    "000990.KS": ("DB하이텍",          "중기", "반도체"),
    "058470.KQ": ("리노공업",          "중기", "반도체"),
    "403870.KQ": ("HPSP",              "중기", "반도체"),
    # ── IT/플랫폼 (4) ──
    "035420.KS": ("NAVER",             "중기", "IT"),
    "035720.KS": ("카카오",            "중기", "IT"),
    "066570.KS": ("LG전자",            "중기", "IT"),
    "018260.KS": ("삼성에스디에스",    "중기", "IT"),
    # ── 통신 (3) ──
    "030200.KS": ("KT",                "장기", "통신"),
    "017670.KS": ("SK텔레콤",          "장기", "통신"),
    "032640.KS": ("LG유플러스",        "장기", "통신"),
    # ── 자동차/부품 (5) ──
    "005380.KS": ("현대차",            "중기", "자동차"),
    "000270.KS": ("기아",              "중기", "자동차"),
    "012330.KS": ("현대모비스",        "중기", "자동차"),
    "018880.KS": ("한온시스템",        "중기", "자동차"),
    "204320.KS": ("HL만도",            "중기", "자동차"),
    # ── 2차전지/소재 (7) ──
    "373220.KS": ("LG에너지솔루션",    "장기", "2차전지"),
    "006400.KS": ("삼성SDI",           "장기", "2차전지"),
    "003670.KS": ("포스코퓨처엠",      "장기", "2차전지"),
    "051910.KS": ("LG화학",            "장기", "2차전지"),
    "247540.KQ": ("에코프로비엠",      "장기", "2차전지"),
    "066970.KQ": ("엘앤에프",          "장기", "2차전지"),
    "096770.KS": ("SK이노베이션",      "장기", "2차전지"),
    # ── 화학/소재 (4) ──
    "011170.KS": ("롯데케미칼",        "중기", "화학"),
    "298020.KS": ("효성티앤씨",        "중기", "화학"),
    "010060.KS": ("OCI홀딩스",         "중기", "화학"),
    "014680.KS": ("한솔케미칼",        "중기", "화학"),
    # ── 금융 (8) ──
    "105560.KS": ("KB금융",            "단기", "금융"),
    "055550.KS": ("신한지주",          "단기", "금융"),
    "086790.KS": ("하나금융지주",      "단기", "금융"),
    "138930.KS": ("BNK금융지주",       "단기", "금융"),
    "316140.KS": ("우리금융지주",      "단기", "금융"),
    "323410.KS": ("카카오뱅크",        "중기", "금융"),
    "006800.KS": ("미래에셋증권",      "단기", "금융"),
    "039490.KS": ("키움증권",          "단기", "금융"),
    # ── 보험 (3) ──
    "032830.KS": ("삼성생명",          "단기", "보험"),
    "000810.KS": ("삼성화재",          "단기", "보험"),
    "138040.KS": ("메리츠금융지주",    "단기", "보험"),
    # ── 건설 (3) ──
    "006360.KS": ("GS건설",            "중기", "건설"),
    "000720.KS": ("현대건설",          "중기", "건설"),
    "375500.KS": ("DL이앤씨",          "중기", "건설"),
    # ── 소비재 (5) ──
    "033780.KS": ("KT&G",              "장기", "소비재"),
    "090430.KS": ("아모레퍼시픽",      "중기", "소비재"),
    "051900.KS": ("LG생활건강",        "중기", "소비재"),
    "008770.KS": ("호텔신라",          "중기", "소비재"),
    "139480.KS": ("이마트",            "중기", "유통"),
    # ── 유통/리테일 (3) ──
    "004170.KS": ("신세계",            "중기", "유통"),
    "282330.KS": ("BGF리테일",         "중기", "유통"),
    "007070.KS": ("GS리테일",          "중기", "유통"),
    # ── 음식료 (4) ──
    "271560.KS": ("오리온",            "중기", "음식료"),
    "097950.KS": ("CJ제일제당",        "중기", "음식료"),
    "004370.KS": ("농심",              "중기", "음식료"),
    "003230.KS": ("삼양식품",          "중기", "음식료"),
    # ── 엔터/콘텐츠 (4) ──
    "352820.KS": ("하이브",            "중기", "엔터"),
    "035900.KQ": ("JYP Ent.",          "중기", "엔터"),
    "041510.KQ": ("에스엠",            "중기", "엔터"),
    "122870.KQ": ("YG엔터테인먼트",    "중기", "엔터"),
    # ── 게임 (3) ──
    "259960.KS": ("크래프톤",          "중기", "게임"),
    "036570.KS": ("엔씨소프트",        "중기", "게임"),
    "293490.KQ": ("카카오게임즈",      "중기", "게임"),
    # ── 바이오/제약 (8) ──
    "000100.KS": ("유한양행",          "중기", "바이오"),
    "128940.KS": ("한미약품",          "중기", "바이오"),
    "068270.KS": ("셀트리온",          "중기", "바이오"),
    "207940.KS": ("삼성바이오로직스",  "장기", "바이오"),
    "326030.KS": ("SK바이오팜",        "장기", "바이오"),
    "196170.KQ": ("알테오젠",          "장기", "바이오"),
    "185750.KS": ("종근당",            "중기", "바이오"),
    "069620.KS": ("대웅제약",          "중기", "바이오"),
    # ── 헬스케어 (3) ──
    "008930.KS": ("한미사이언스",      "중기", "헬스케어"),
    "214150.KQ": ("클래시스",          "중기", "헬스케어"),
    "145020.KQ": ("휴젤",              "중기", "헬스케어"),
    # ── 로봇/AI (2) ──
    "277810.KQ": ("레인보우로보틱스",  "장기", "로봇"),
    "454910.KS": ("두산로보틱스",      "장기", "로봇"),
    # ── 종합/기타 (1) ──
    "028260.KS": ("삼성물산",          "장기", "종합"),
    # ── 해운/항공/물류 (3) ──
    "011200.KS": ("HMM",               "단기", "해운"),
    "003490.KS": ("대한항공",          "중기", "항공"),
    "086280.KS": ("현대글로비스",      "중기", "물류"),
    # ── 철강 (3) ──
    "004020.KS": ("현대제철",          "중기", "철강"),
    "005490.KS": ("POSCO홀딩스",       "중기", "철강"),
    "016380.KS": ("KG스틸",            "중기", "철강"),
    # ── 에너지 (2) ──
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
        if foreign_eok >= 50:    score += 10; reasons.append(f"외국인 순매수 +{foreign_eok:.2f}억원 — 강한 외국인 매수세")
        elif foreign_eok >= 10:  score += 5;  reasons.append(f"외국인 순매수 +{foreign_eok:.2f}억원")
        elif foreign_eok <= -50: score -= 8;  warnings.append(f"외국인 순매도 {foreign_eok:.2f}억원 — 외국인 이탈 주의")

        if inst_eok >= 50:    score += 8;  reasons.append(f"기관 순매수 +{inst_eok:.2f}억원 — 기관 집중 매수")
        elif inst_eok >= 10:  score += 4;  reasons.append(f"기관 순매수 +{inst_eok:.2f}억원")
        elif inst_eok <= -50: score -= 5;  warnings.append(f"기관 순매도 {inst_eok:.2f}억원")

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

    # ── 스윙 전용 점수 (기존 'score'는 가치투자 점수, 그대로 유지) ──
    # 스윙은 단기 모멘텀 위주: 기술적 35% / 거래량·모멘텀 25% / 수급 20% / 공시 10% / 가치 5% / 섹터 5%
    sw_score = 0
    sw_reasons = []

    # 1) 기술적 (RSI, MACD, 볼린저, 52주 위치)
    if rsi < 30:
        sw_score += 15; sw_reasons.append(f"RSI {rsi} 과매도 반등 구간")
    elif rsi < 45:
        sw_score += 12; sw_reasons.append(f"RSI {rsi} 저점 매수권")
    elif rsi > 65:
        sw_score -= 10
    if macd_cross:
        sw_score += 10; sw_reasons.append("MACD 골든크로스")
    if bb_pct < 20:
        sw_score += 8;  sw_reasons.append("볼린저밴드 하단 (반등 통계)")
    elif bb_pct > 80:
        sw_score -= 5
    if pct_from_low <= 10:
        sw_score += 8;  sw_reasons.append(f"52주 저점 +{pct_from_low}%")
    elif pct_from_low <= 20:
        sw_score += 4

    # 2) 거래량 / 모멘텀
    if vol_ratio >= 200:
        sw_score += 12; sw_reasons.append(f"거래량 {vol_ratio:.0f}% 급증")
    elif vol_ratio >= 150:
        sw_score += 8;  sw_reasons.append(f"거래량 {vol_ratio:.0f}% 활발")
    elif vol_ratio < 80:
        sw_score -= 5
    if 0 < ret_1w <= 5:
        sw_score += 8;  sw_reasons.append(f"1주 +{ret_1w}% 가벼운 상승")
    elif 5 < ret_1w <= 10:
        sw_score += 4
    elif ret_1w > 10:
        sw_score -= 3   # 너무 오른 종목은 추격 매수 위험
    elif ret_1w < -3:
        sw_score -= 5
    if -5 <= ret_1m <= 0:
        sw_score += 5;  sw_reasons.append("1달 가벼운 조정 (눌림목)")
    elif ret_1m < -15:
        sw_score -= 10
    if sr["near_support"]:
        sw_score += 8;  sw_reasons.append("지지선 근처")

    # 3) 수급 (외국인/기관)
    if foreign_eok >= 50:
        sw_score += 12; sw_reasons.append(f"외국인 +{foreign_eok:.2f}억")
    elif foreign_eok >= 10:
        sw_score += 6
    elif foreign_eok <= -50:
        sw_score -= 8
    if inst_eok >= 50:
        sw_score += 8;  sw_reasons.append(f"기관 +{inst_eok:.2f}억")
    elif inst_eok >= 10:
        sw_score += 4

    # 4) 공시 (단기 호재 / 악재)
    if dart_sigs.get("rights"):
        sw_score -= 20  # 유증은 단기 치명적
    if dart_sigs.get("buyback"):
        sw_score += 6;  sw_reasons.append("자사주 매입")
    if dart_sigs.get("order"):
        sw_score += 8;  sw_reasons.append("신규 수주")
    if dart_sigs.get("insider"):
        sw_score -= 5

    # 5) 가치 (스윙엔 비중 작음 — 너무 비싼 것만 거름)
    if per and per > 30:
        sw_score -= 5

    # 6) 섹터 (약하게만)
    if sector in ("조선", "방산", "원전", "전력", "바이오"):
        sw_score += 5

    # 가격 조작 / 모멘텀 약화는 강력 차단
    if manipulation_signal:
        sw_score -= 25
    if momentum_bad:
        sw_score -= 15

    # 스윙 매수 시그널: 점수 + 안전 조건 6개. 차단 사유 list로도 기록 (자동매수 진단용).
    swing_block_reasons = []
    if sw_score < SWING_SCORE_MIN:
        swing_block_reasons.append(f"점수<{SWING_SCORE_MIN}")
    if rsi >= 65:
        swing_block_reasons.append(f"RSI{int(rsi)}")
    if manipulation_signal:
        swing_block_reasons.append("조작감지")
    if momentum_bad:
        swing_block_reasons.append("모멘텀악화")
    if dart_sigs.get("rights"):
        swing_block_reasons.append("유증")
    if sr["near_resistance"]:
        swing_block_reasons.append("저항근처")
    if vol_ratio < 100:
        swing_block_reasons.append(f"거래량{int(vol_ratio)}%")
    if ret_1m <= -15:
        swing_block_reasons.append(f"1개월{ret_1m:.0f}%")
    swing_signal = len(swing_block_reasons) == 0

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
        "swing_score": sw_score, "swing_reasons": sw_reasons, "swing_signal": swing_signal,
        "swing_block_reasons": swing_block_reasons,
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


def _ai_system() -> str:
    """AI 시스템 프롬프트 — 매번 호출 시점의 KST 날짜를 동적으로 포함.

    Claude 학습 데이터 컷오프로 인한 잘못된 연도 표기(예: '2025') 방지.
    """
    today = _now_kst().strftime("%Y년 %m월 %d일")
    return (
        f"오늘 날짜: {today} (한국시간). 이 날짜를 기준으로 분석하세요. "
        "당신은 한국 주식 시장 전문 AI 투자 분석가입니다. "
        "데이터를 바탕으로 간결하고 핵심적인 분석을 제공합니다. "
        "섹터별 트렌드, 매크로 환경, 수급 동향을 종합적으로 고려합니다. "
        "투자 조언은 참고용임을 명심하고, 불확실성을 솔직하게 표현합니다. "
        "한국어로 답변하며, 핵심만 간결하게 작성합니다. "
        "절대로 다른 연도를 추측해서 표기하지 마세요."
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
        us_names = ", ".join(s["name"] for s in us_top[:3]) if us_top else ""
        usdkrw   = mood.get("usdkrw", 1300)
        fx_note  = "고환율(수출주 유리)" if usdkrw >= 1380 else ("저환율(내수주 유리)" if usdkrw <= 1250 else "환율 중립")
        vix_note = "방어주 중심 권장" if mood.get("vix", 20) >= 30 else ("성장주 접근 가능" if mood.get("vix", 20) <= 18 else "균형 전략")

        # KR 전용 모드: 해외 종목 라인 제외, 미국 매크로는 KR 영향 분석용으로 유지
        kr_only_note = (
            "(이 봇은 KR 종목 전용입니다. 해외 추천은 다루지 않으며, "
            "미국 시장 데이터는 KR 시장에 미치는 영향만 분석하세요.)"
            if not us_names else ""
        )
        us_line = f"- 해외 TOP3: {us_names}\n" if us_names else ""

        prompt = (
            f"오늘의 시장 데이터:\n"
            f"- 코스피: {mood['kospi_price']:,.2f} ({mood['kospi_chg']:+.2f}%)\n"
            f"- S&P500: {mood['sp500_chg']:+.2f}%\n"
            f"- VIX: {mood['vix']:.2f} → {vix_note}\n"
            f"- 달러/원: {mood['usdkrw']:,.2f} → {fx_note}\n"
            f"- WTI: ${mood['wti']:.2f} / 금: ${mood['gold']:,.2f}\n"
            f"- 공포탐욕지수: {fg['score']} ({fg['label']})\n"
            f"- 국내 TOP3: {kr_names}\n"
            f"{us_line}\n"
            f"{kr_only_note}\n"
            "다음 세 가지를 각각 한 문장으로 답해주세요:\n"
            "1. 오늘 주식을 사도 되는 시장인가? (YES/NO + 한줄 이유)\n"
            "2. 오늘 주목해야 할 한국 섹터와 그 이유\n"
            "3. 오늘 가장 중요한 리스크 요인"
        )
        resp = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=400,
            system=_ai_system(),
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
            f"- 달러/원 환율: {usdkrw:,.2f}원 (1380↑=수출유리, 1250↓=내수유리)\n"
            f"- VIX: {vix} (30↑=방어주, 18↓=성장주)\n"
            f"- WTI 유가: ${wti:.2f} (80↑=에너지, 60↓=소비재)\n"
            f"- 코스피: {mood['kospi_chg']:+.2f}%\n\n"
            "오늘 유망한 섹터 2개와 피해야 할 섹터 1개를 선택하고, 각각 이유를 1문장으로.\n"
            "형식: 유망: [섹터1] - 이유 / [섹터2] - 이유 | 주의: [섹터] - 이유"
        )
        resp = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=200,
            system=_ai_system(),
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
            f"- 달러/원 환율: {mood.get('usdkrw', 1300):,.2f}원\n\n"
            "위 지표가 오늘 한국 주식시장에 미치는 영향을 아래 3가지로 각각 1문장씩 분석:\n"
            "1. 금리·달러 환경이 수출주(조선·방산·반도체)에 미치는 영향\n"
            "2. 현재 매크로 환경에서 주목할 한국 섹터\n"
            "3. 오늘 가장 주의해야 할 매크로 리스크"
        )
        resp = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=700,  # 5/4: 350→700 (3가지 항목 답변이 한국어로 잘리는 문제)
            system=_ai_system(),
            messages=[{"role": "user", "content": prompt}],
        )
        return resp.content[0].text.strip()
    except Exception as e:
        print(f"  [AI] 매크로 분석 실패: {e}")
        return ""


def analyze_trading_performance(window_days: int = 30) -> dict:
    """positions.json history 기반 매매 결과 자가 분석. (Phase 2 — 자가 학습 인프라)

    매수↔매도 매칭하여 종목별 수익률/보유일/매도 사유 계산.
    daily 08:00에 호출되어 ai_personal_coach 프롬프트에 통계 전달 → AI가 학습 결과 반영.

    데이터 30건+ 쌓이면 swing_score 가중치 자동 조정으로 확장 (TODO).
    """
    try:
        pos = load_positions()
        history = pos.get("history", [])
        if not history:
            return {"trades": 0, "summary": "매매 데이터 없음 (자동매매 시작 후 누적)"}

        cutoff = (_now_kst() - timedelta(days=window_days)).strftime("%Y-%m-%d")
        recent = [h for h in history if h.get("date", "") >= cutoff]

        # 매수/매도 매칭 (FIFO)
        buys = {}
        results = []
        for h in recent:
            code = h.get("code", "")
            if h.get("side") == "buy":
                buys[code] = h
            elif h.get("side") == "sell" and code in buys:
                buy = buys[code]
                bp = buy.get("price", 0)
                sp = h.get("price", 0)
                if bp > 0 and sp > 0:
                    pnl_pct = (sp - bp) / bp * 100
                    # 보유일 계산
                    try:
                        bd = datetime.strptime(buy.get("date", ""), "%Y-%m-%d")
                        sd = datetime.strptime(h.get("date", ""), "%Y-%m-%d")
                        hold_days = (sd - bd).days
                    except Exception:
                        hold_days = 0
                    # buy의 reason "swing_score N" 파싱
                    score = 0
                    try:
                        reason = buy.get("reason", "")
                        if "swing_score" in reason:
                            score = int(reason.split("swing_score")[-1].strip().split()[0])
                    except Exception:
                        pass
                    results.append({
                        "name": buy.get("name", code),
                        "swing_score": score,
                        "pnl_pct": pnl_pct,
                        "hold_days": hold_days,
                        "sell_reason": h.get("reason", ""),
                    })
                buys.pop(code, None)

        if not results:
            return {"trades": 0, "summary": f"최근 {window_days}일 완료 매매 없음 (보유 중인 종목은 미포함)"}

        wins   = [r for r in results if r["pnl_pct"] > 0]
        losses = [r for r in results if r["pnl_pct"] <= 0]
        avg_win  = sum(r["pnl_pct"] for r in wins) / len(wins) if wins else 0
        avg_loss = sum(r["pnl_pct"] for r in losses) / len(losses) if losses else 0
        avg_hold = sum(r["hold_days"] for r in results) / len(results)

        # 점수 구간별 승률
        bucket_70 = [r for r in results if r["swing_score"] >= 70]
        bucket_65 = [r for r in results if 65 <= r["swing_score"] < 70]
        bucket_60 = [r for r in results if 60 <= r["swing_score"] < 65]
        def _wr(b):
            if not b: return 0
            return sum(1 for r in b if r["pnl_pct"] > 0) / len(b) * 100

        summary_lines = [
            f"최근 {window_days}일 완료 매매: {len(results)}건 (승 {len(wins)}/패 {len(losses)})",
            f"승률: {len(wins)/len(results)*100:.1f}% / 평균 수익 {avg_win:+.2f}% / 평균 손실 {avg_loss:+.2f}%",
            f"평균 보유: {avg_hold:.1f}일",
        ]
        if bucket_70:
            summary_lines.append(f"점수 70+: {len(bucket_70)}건, 승률 {_wr(bucket_70):.0f}%")
        if bucket_65:
            summary_lines.append(f"점수 65-69: {len(bucket_65)}건, 승률 {_wr(bucket_65):.0f}%")
        if bucket_60:
            summary_lines.append(f"점수 60-64: {len(bucket_60)}건, 승률 {_wr(bucket_60):.0f}%")

        return {
            "trades": len(results),
            "wins": len(wins),
            "losses": len(losses),
            "win_rate": len(wins) / len(results) * 100,
            "avg_win": avg_win,
            "avg_loss": avg_loss,
            "avg_hold_days": avg_hold,
            "summary": "\n".join(summary_lines),
            "details": results,
        }
    except Exception as e:
        return {"trades": 0, "summary": f"분석 오류: {e}"}


_MARKET_EVENTS = [
    # 2026 주요 매크로 이벤트 (한국 시간 기준 결과 발표일)
    # ── FOMC (미국 기준금리 결정, 한국시간 익일 03:00 발표) ──
    {"date": "2026-06-18", "name": "FOMC 6월 회의 결과", "type": "FOMC", "impact": "high",
     "note": "동결/인상/인하 + 점도표 변화 → 한국 시장 변동성 확대 가능"},
    {"date": "2026-07-30", "name": "FOMC 7월 회의 결과", "type": "FOMC", "impact": "high"},
    {"date": "2026-09-17", "name": "FOMC 9월 회의 결과", "type": "FOMC", "impact": "high"},
    {"date": "2026-11-05", "name": "FOMC 11월 회의 결과", "type": "FOMC", "impact": "high"},
    {"date": "2026-12-17", "name": "FOMC 12월 회의 결과", "type": "FOMC", "impact": "high"},
    # ── 미국 CPI (한국시간 21:30 발표) ──
    {"date": "2026-05-13", "name": "미국 4월 CPI 발표", "type": "CPI", "impact": "high",
     "note": "전월비·전년비. 인플레이션 둔화/심화 신호"},
    {"date": "2026-06-11", "name": "미국 5월 CPI", "type": "CPI", "impact": "high"},
    {"date": "2026-07-15", "name": "미국 6월 CPI", "type": "CPI", "impact": "high"},
    {"date": "2026-08-12", "name": "미국 7월 CPI", "type": "CPI", "impact": "high"},
    {"date": "2026-09-11", "name": "미국 8월 CPI", "type": "CPI", "impact": "high"},
    {"date": "2026-10-15", "name": "미국 9월 CPI", "type": "CPI", "impact": "high"},
    {"date": "2026-11-13", "name": "미국 10월 CPI", "type": "CPI", "impact": "high"},
    {"date": "2026-12-10", "name": "미국 11월 CPI", "type": "CPI", "impact": "high"},
    # ── 한국 GDP (분기말 익월 24일 전후) ──
    {"date": "2026-07-24", "name": "한국 2분기 GDP 발표", "type": "GDP", "impact": "medium"},
    {"date": "2026-10-24", "name": "한국 3분기 GDP 발표", "type": "GDP", "impact": "medium"},
    # ── 한국 금통위 기준금리 결정 ──
    {"date": "2026-05-29", "name": "한국 금통위 기준금리 결정", "type": "BOK", "impact": "high",
     "note": "한국 기준금리 동결/인하 결정. 코스피·환율 직접 영향"},
    {"date": "2026-07-10", "name": "한국 금통위", "type": "BOK", "impact": "high"},
    {"date": "2026-08-28", "name": "한국 금통위", "type": "BOK", "impact": "high"},
    {"date": "2026-10-23", "name": "한국 금통위", "type": "BOK", "impact": "high"},
    {"date": "2026-11-27", "name": "한국 금통위", "type": "BOK", "impact": "high"},
]


def get_upcoming_events(days_ahead: int = 7) -> list:
    """오늘 ~ N일 이내 매크로 이벤트 반환 (D-day 포함)."""
    today = _now_kst().date()
    end = today + timedelta(days=days_ahead)
    out = []
    for e in _MARKET_EVENTS:
        try:
            d = datetime.strptime(e["date"], "%Y-%m-%d").date()
            if today <= d <= end:
                days_to = (d - today).days
                out.append({**e, "days_to": days_to})
        except Exception:
            continue
    return sorted(out, key=lambda x: x.get("days_to", 99))


def notify_imminent_events() -> int:
    """D-day(오늘) / D-1(내일) 이벤트 텔레그램 알림. daily 08:00 1회 호출.

    반환: 발송한 이벤트 수.
    """
    upcoming = [e for e in get_upcoming_events(days_ahead=1) if e.get("days_to", -1) in (0, 1)]
    if not upcoming:
        return 0
    EMOJI = {"FOMC": "🇺🇸", "CPI": "📊", "GDP": "📈", "BOK": "🇰🇷"}
    lines = ["📅 <b>경제 이벤트 알림</b>", ""]
    today_evts = [e for e in upcoming if e.get("days_to") == 0]
    tmrw_evts  = [e for e in upcoming if e.get("days_to") == 1]
    if today_evts:
        lines.append("<b>🔔 오늘 (D-day)</b>")
        for e in today_evts:
            em = EMOJI.get(e.get("type"), "📌")
            lines.append(f"{em} <b>{e['name']}</b> <i>({e.get('type')})</i>")
            if e.get("note"):
                lines.append(f"   <i>{e['note']}</i>")
        lines.append("")
    if tmrw_evts:
        lines.append("<b>⏰ 내일 (D-1)</b>")
        for e in tmrw_evts:
            em = EMOJI.get(e.get("type"), "📌")
            lines.append(f"{em} <b>{e['name']}</b> <i>({e.get('type')})</i>")
            if e.get("note"):
                lines.append(f"   <i>{e['note']}</i>")
        lines.append("")
    lines.append("<i>※ 발표 직후 변동성 확대 가능. 자동매매 한도 자동 축소.</i>")
    tg_send("\n".join(lines))
    return len(upcoming)


def calculate_market_risk(mood: dict, fg: dict) -> dict:
    """시장 위험 지수 0~100 — VIX/공포탐욕/코스피 변동률 종합. (Phase 1.5)

    구성:
      - VIX (40점): 35+ 40 / 30+ 30 / 25+ 20 / 20+ 10 / <20 0
      - 공포탐욕 (30점): ≤25 30(극도공포) / ≤40 20 / ≤60 10 / ≥75 15(탐욕역설)
      - 코스피 변동률 (20점): ≤-3 20(폭락) / ≤-2 15 / ≤-1 10 / 정상 0
      - 변동성 가속 (10점): VIX 직전 대비 +20% 급등 시

    등급 → autobuy 동작:
      - 위험(70+) → qty_factor 0 (매수 정지)
      - 경계(50+) → qty_factor 0.5 (50% 축소)
      - 주의(30+) → qty_factor 0.75 (25% 축소)
      - 안전(<30) → qty_factor 1.0
    """
    score = 0
    reasons = []

    # 1) VIX (40점)
    vix = mood.get("vix", 20) if mood else 20
    if vix >= 35:
        score += 40; reasons.append(f"VIX {vix:.1f} 위험구간")
    elif vix >= 30:
        score += 30; reasons.append(f"VIX {vix:.1f} 경계")
    elif vix >= 25:
        score += 20
    elif vix >= 20:
        score += 10

    # 2) 공포·탐욕 (30점) — 극도공포 OR 극도탐욕 둘 다 위험
    fg_score = fg.get("score", 50) if fg else 50
    if fg_score <= 25:
        score += 30; reasons.append(f"공포탐욕 {fg_score} 극도공포")
    elif fg_score <= 40:
        score += 20; reasons.append(f"공포탐욕 {fg_score} 공포")
    elif fg_score >= 80:
        score += 20; reasons.append(f"공포탐욕 {fg_score} 극도탐욕(조정 임박)")
    elif fg_score >= 70:
        score += 10
    elif fg_score <= 60:
        score += 5

    # 3) 코스피 변동률 (20점)
    kospi_chg = mood.get("kospi_chg", 0) if mood else 0
    if kospi_chg <= -3:
        score += 20; reasons.append(f"코스피 {kospi_chg:.1f}% 폭락")
    elif kospi_chg <= -2:
        score += 15; reasons.append(f"코스피 {kospi_chg:.1f}% 급락")
    elif kospi_chg <= -1:
        score += 10
    elif kospi_chg <= -0.5:
        score += 5

    # 4) 변동성 가속 (10점) — VIX 직전 종가 대비 +20% 급등 (history_cache)
    try:
        history = _load_market_history(max_age_hours=2)
        vix_hist = history.get("vix", {}).get("values", [])
        if len(vix_hist) >= 2:
            prev_vix = vix_hist[-2]
            if prev_vix > 0 and (vix - prev_vix) / prev_vix >= 0.20:
                score += 10
                reasons.append(f"VIX 24h +{(vix - prev_vix) / prev_vix * 100:.0f}% 급등")
    except Exception:
        pass

    # 5) 매크로 이벤트 D-day / D-1 (Phase 1.5 확장)
    try:
        upcoming = get_upcoming_events(days_ahead=1)
        for e in upcoming:
            d = e.get("days_to", -1)
            impact = e.get("impact", "low")
            if d == 0 and impact == "high":
                score += 20
                reasons.append(f"오늘 {e.get('name', '')} 발표 (D-day)")
            elif d == 0 and impact == "medium":
                score += 10
                reasons.append(f"오늘 {e.get('name', '')}")
            elif d == 1 and impact == "high":
                score += 10
                reasons.append(f"내일 {e.get('name', '')} (D-1)")
    except Exception:
        pass

    score = min(100, max(0, score))

    if score >= 70:
        level, action, qty_factor = "위험", "자동매수 정지", 0.0
    elif score >= 50:
        level, action, qty_factor = "경계", "매수량 50% 축소", 0.5
    elif score >= 30:
        level, action, qty_factor = "주의", "매수량 25% 축소", 0.75
    else:
        level, action, qty_factor = "안전", "정상 매수", 1.0

    return {
        "score": score,
        "level": level,
        "action": action,
        "qty_factor": qty_factor,
        "reasons": reasons,
    }


def _build_user_portfolio_context() -> str:
    """이제훈님 보유 + 자금 + 섹터 분포를 텍스트로 요약 (AI 프롬프트용).

    반환 예시:
        가치주(미래에셋, 10종목, 평가액 +967만원/+44.81%):
          - 두산에너빌리티 80주 @65,364 → 127,100 (+94.8%, 원전)
          - ...
        자동매매(한국투자증권 모의, 0/5 슬롯, 잔여 1,000만원/일):
          - 보유 없음
        섹터 분포:
          - 방산 30% / 신재생 25% / 바이오 20% / 기타 25%
    """
    lines = []

    # 가치주 (HOLDINGS_JSON)
    try:
        ha = check_holdings_alerts()
    except Exception:
        ha = []
    if ha:
        v_value = sum(h.get("value", 0) for h in ha)
        v_cost  = sum(h.get("cost", 0) for h in ha)
        v_pnl   = v_value - v_cost
        v_pct   = (v_pnl / v_cost * 100) if v_cost > 0 else 0
        lines.append(
            f"가치주(미래에셋, {len(ha)}종목, 평가 {v_value:,.0f}원 / 매입 {v_cost:,.0f}원 / "
            f"손익 {v_pnl:+,.0f}원/{v_pct:+.2f}%):"
        )
        for h in ha[:15]:  # 최대 15종목
            sec = h.get("sector", "")
            sec_str = f", {sec}" if sec else ""
            lines.append(
                f"  - {h.get('name')} {h.get('qty')}주 @{h.get('avg_price', 0):,.0f} "
                f"→ {h.get('curr_price', 0):,.0f} ({h.get('pnl_pct', 0):+.1f}%{sec_str})"
            )
    else:
        lines.append("가치주: 보유 없음")

    # 자동매매 (positions.json)
    try:
        pos = load_positions()
    except Exception:
        pos = {}
    auto_positions = pos.get("positions", {})
    today = _today_str()
    daily = pos.get("daily", {}).get(today, {"buy_count": 0, "buy_amount": 0})
    remaining_slots = SWING_MAX_DAILY_BUY - daily.get("buy_count", 0)
    remaining_amt   = SWING_MAX_DAILY_AMT - daily.get("buy_amount", 0)
    if auto_positions:
        lines.append(
            f"\n자동매매(한국투자증권 모의, {len(auto_positions)}종목 보유, "
            f"오늘 잔여 {remaining_slots}슬롯/{remaining_amt:,}원):"
        )
        for code, p in list(auto_positions.items())[:10]:
            lines.append(
                f"  - {p.get('name', code)} {p.get('qty', 0)}주 @{p.get('buy_price', 0):,.0f} "
                f"({p.get('buy_date', '')} 매수, 점수 {p.get('swing_score', 0)})"
            )
    else:
        lines.append(
            f"\n자동매매: 보유 없음. 오늘 잔여 {remaining_slots}슬롯/{remaining_amt:,}원"
        )

    # 섹터 분포 (가치주만 — 자동매매는 빈번히 변하므로 가치주 위주)
    if ha:
        sec_value = {}
        for h in ha:
            sec = h.get("sector") or "기타"
            sec_value[sec] = sec_value.get(sec, 0) + h.get("value", 0)
        total = sum(sec_value.values())
        if total > 0:
            lines.append("\n가치주 섹터 분포:")
            for sec, v in sorted(sec_value.items(), key=lambda x: -x[1]):
                pct = v / total * 100
                lines.append(f"  - {sec}: {pct:.0f}%")

    return "\n".join(lines)


def ai_personal_coach(query: str = "지금 뭐 사야 할까?",
                      mood: dict = None, fg: dict = None,
                      kr_top: list = None, ai_macro: str = "",
                      max_tokens: int = 1200) -> str:
    """이제훈님 맞춤 AI 비서 — 보유종목+자금+섹터+시장+매크로 종합해서 답변.

    텔레그램 명령어 (/추천, /진단, /오늘) + 대시보드 데일리 브리핑 카드에서 사용.
    Phase 1: 자비스 진화 1단계 — 단순 점수 리스트가 아닌 개인화 코칭.
    """
    client = _get_ai_client()
    if not client:
        return ""

    try:
        # 1) 사용자 컨텍스트 수집 (보유 + 자금 + 섹터)
        user_ctx = _build_user_portfolio_context()

        # 2) 시장 데이터
        if mood is None:
            try:
                mood = get_market_mood()
            except Exception:
                mood = {}
        if fg is None:
            try:
                fg = get_fear_greed(mood) if mood else {"score": 50, "label": "중립"}
            except Exception:
                fg = {"score": 50, "label": "중립"}

        market_lines = []
        if mood:
            market_lines.append(
                f"코스피 {mood.get('kospi_chg', 0):+.2f}% / VIX {mood.get('vix', 0):.2f} / "
                f"환율 {mood.get('usdkrw', 0):,.0f}원 / WTI ${mood.get('wti', 0):.2f}"
            )
            market_lines.append(
                f"공포·탐욕 {fg.get('score', 50)} ({fg.get('label', '중립')})"
            )
        if ai_macro:
            market_lines.append(f"매크로 분석: {ai_macro[:300]}")

        # 3) 추천 후보 (가치주 TOP5)
        if not kr_top:
            try:
                kr_top = _load_value_top5() or []
            except Exception:
                kr_top = []
        cand_lines = []
        for s in (kr_top or [])[:5]:
            cand_lines.append(
                f"  - {s.get('name', '?')} ({s.get('sector', '')}) — {s.get('score', 0)}점, "
                f"{s.get('price', 0):,.0f}원, "
                f"{'매수신호 ✅' if s.get('buy_signal') else '관찰 🔍'}"
            )

        # 자가 학습 통계 (Phase 2)
        try:
            perf = analyze_trading_performance(window_days=30)
            perf_text = perf.get("summary", "")
        except Exception:
            perf_text = ""

        prompt = (
            f"이제훈님(비개발자, 한국 거주)의 맞춤 투자 비서로서 답변하세요.\n"
            f"점수 나열이 아니라 **그의 현재 포트폴리오와 자금 상황을 고려한** 조언.\n\n"
            f"⚠️ 두 계좌 분리 (시간 프레임 완전히 다름):\n"
            f"  - 가치주 = 미래에셋, **장기 보유** (몇 달~몇 년, 펀더멘털/배당 추구)\n"
            f"  - 자동매매 = 한국투자증권 모의, **5일 이내 스윙** (단기 모멘텀, +6%/+10% 익절, -4% 손절)\n"
            f"→ 두 계좌의 종목/섹터가 **겹쳐도 독립 리스크** (시간 프레임 다름). 같은 섹터 강제 분산 X.\n"
            f"→ 섹터 집중도 평가는 **가치주 내부에서만** (예: 가치주 방산 50%면 가치주 추가 매수는 비추, 자동매매 방산 매수는 무관).\n\n"
            f"[그의 현재 상태]\n{user_ctx}\n\n"
            f"[자동매매 자가 학습 통계 (최근 30일)]\n{perf_text or '데이터 부족'}\n\n"
            f"[시장 상황]\n" + ("\n".join(market_lines) if market_lines else "데이터 없음") + "\n\n"
            f"[봇이 발굴한 후보 종목]\n" + ("\n".join(cand_lines) if cand_lines else "  (마켓스캔 캐시 없음)") + "\n\n"
            f"[질문]\n{query}\n\n"
            f"답변 가이드:\n"
            f"- 한국어, 비개발자 친화 (전문 용어는 풀어서)\n"
            f"- 가치주 추천: 가치주 섹터 분포 고려 (장기 보유라 분산 중요)\n"
            f"- 자동매매 추천: 시간 프레임 다르므로 가치주 섹터와 별개. 점수/시그널/매크로 위주\n"
            f"- 자동매매 잔여 슬롯/금액 안에서만 추천\n"
            f"- '사세요/팔지 마세요' 단정 X. '이 조건이면 권장, 저 위험은 주의' 형식\n"
            f"- 너무 길지 않게 (텔레그램 한 화면). 핵심 3~5가지로 정리\n"
            f"- 마지막에 '오늘 행동 제안 1줄' 추가"
        )

        resp = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=max_tokens,
            system=_ai_system(),
            messages=[{"role": "user", "content": prompt}],
        )
        return resp.content[0].text.strip()
    except Exception as e:
        print(f"  [AI] 개인 코칭 실패: {e}")
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
            model=CLAUDE_MODEL,
            max_tokens=100,
            system=_ai_system(),
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
                f"시장상태: {mood['status']} / VIX: {mood['vix']:.2f} "
                f"/ 코스피: {mood['kospi_chg']:+.2f}%"
            )

        resp = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=400,
            system=_ai_system(),
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
    today = _now_kst().strftime("%Y-%m-%d")
    month = _now_kst().strftime("%Y-%m")
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
    last_month = (_now_kst().replace(day=1) - timedelta(days=1)).strftime("%Y-%m")
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
                "qty": qty,
                "value": curr_price * qty,
                "cost": avg_price * qty,
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
            f_str = f"{'▲' if f_eok >= 0 else '▼'} {abs(f_eok):.2f}억원"
            i_str = f"{'▲' if i_eok >= 0 else '▼'} {abs(i_eok):.2f}억원"
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


def _dashboard_css() -> str:
    """풀 대시보드 디자인 시스템 — 사이드바 + Hero 헤더 + 카드 그리드 + 다크모드."""
    return """
<style>
:root {
  --bg: #f5f6fa;
  --surface: #ffffff;
  --surface-2: #f8f9fb;
  --sidebar-bg: #0f1729;
  --sidebar-text: #cbd5e1;
  --sidebar-active: #5f6dff;
  --sidebar-hover: rgba(255,255,255,0.06);
  --text-1: #0a0e1a;
  --text-2: #475569;
  --text-3: #94a3b8;
  --border: #e5e7eb;
  --up: #e03131;
  --down: #1971c2;
  --neutral: #475569;
  --accent: #5f6dff;
  --accent-value: #5f6dff;
  --accent-auto: #14b8a6;
  --shadow-sm: 0 1px 2px rgba(15,23,41,0.04);
  --shadow: 0 1px 3px rgba(15,23,41,0.05), 0 8px 24px rgba(15,23,41,0.06);
  --shadow-lg: 0 4px 8px rgba(15,23,41,0.06), 0 24px 48px rgba(15,23,41,0.12);
  --hero-grad: linear-gradient(135deg, #5f6dff 0%, #7c3aed 50%, #ec4899 100%);
  --radius: 16px;
  --radius-sm: 10px;
  --radius-lg: 24px;
  --sidebar-w: 240px;
  --gap: 20px;
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    --bg: #0a0e1a;
    --surface: #131826;
    --surface-2: #1a1f2e;
    --sidebar-bg: #050811;
    --sidebar-text: #cbd5e1;
    --sidebar-hover: rgba(255,255,255,0.04);
    --text-1: #e9ecef;
    --text-2: #cbd5e1;
    --text-3: #94a3b8;
    --border: #2a3142;
    --up: #ff6b6b;
    --down: #4dabf7;
    --neutral: #cbd5e1;
    --accent: #748ffc;
    --accent-value: #748ffc;
    --accent-auto: #2dd4bf;
    --shadow-sm: 0 1px 2px rgba(0,0,0,0.3);
    --shadow: 0 1px 3px rgba(0,0,0,0.3), 0 8px 24px rgba(0,0,0,0.4);
    --shadow-lg: 0 4px 8px rgba(0,0,0,0.4), 0 24px 48px rgba(0,0,0,0.6);
  }
}
[data-theme="dark"] {
  --bg: #0a0e1a;
  --surface: #131826;
  --surface-2: #1a1f2e;
  --sidebar-bg: #050811;
  --sidebar-text: #cbd5e1;
  --sidebar-hover: rgba(255,255,255,0.04);
  --text-1: #e9ecef;
  --text-2: #cbd5e1;
  --text-3: #94a3b8;
  --border: #2a3142;
  --up: #ff6b6b;
  --down: #4dabf7;
  --neutral: #cbd5e1;
  --accent: #748ffc;
  --accent-value: #748ffc;
  --accent-auto: #2dd4bf;
  --shadow-sm: 0 1px 2px rgba(0,0,0,0.3);
  --shadow: 0 1px 3px rgba(0,0,0,0.3), 0 8px 24px rgba(0,0,0,0.4);
  --shadow-lg: 0 4px 8px rgba(0,0,0,0.4), 0 24px 48px rgba(0,0,0,0.6);
}

* { box-sizing: border-box; }
html, body { margin: 0; padding: 0; background: var(--bg); color: var(--text-1);
             font-family: -apple-system, BlinkMacSystemFont, "SF Pro Display", "Pretendard",
             "Apple SD Gothic Neo", "맑은 고딕", sans-serif;
             -webkit-font-smoothing: antialiased; -moz-osx-font-smoothing: grayscale; }

/* ── 앱 레이아웃 (fixed sidebar + margin main) ──────────── */
.app { min-height: 100vh; }

/* ── 사이드바 ──────────────────────────── */
.sidebar { background: var(--sidebar-bg); color: var(--sidebar-text);
           position: fixed; left: 0; top: 0; bottom: 0; width: var(--sidebar-w);
           overflow-y: auto;
           padding: 20px 14px; display: flex; flex-direction: column; z-index: 50; }
.sidebar__brand { display: flex; align-items: center; gap: 10px; padding: 4px 12px 24px;
                  font-size: 16px; font-weight: 700; color: white; letter-spacing: -0.3px; }
.sidebar__brand-icon { width: 32px; height: 32px; border-radius: 9px;
                       background: var(--hero-grad); display: flex; align-items: center;
                       justify-content: center; font-size: 16px; }
.sidebar__nav { display: flex; flex-direction: column; gap: 2px; flex: 1; }
.sidebar__link { display: flex; align-items: center; gap: 10px; padding: 10px 12px;
                 border-radius: 10px; color: var(--sidebar-text);
                 text-decoration: none; font-size: 14px; font-weight: 500;
                 transition: all 0.15s; cursor: pointer; }
.sidebar__link:hover { background: var(--sidebar-hover); color: white; }
.sidebar__link.is-active { background: var(--sidebar-active); color: white; box-shadow: 0 4px 12px rgba(95,109,255,0.4); }
.sidebar__link.is-disabled { opacity: 0.4; cursor: not-allowed; }
.sidebar__link.is-disabled:hover { background: transparent; color: var(--sidebar-text); }
.sidebar__link-icon { font-size: 16px; flex-shrink: 0; }
.sidebar__link-label { flex: 1; }
.sidebar__link-badge { padding: 1px 6px; background: rgba(255,255,255,0.08);
                       border-radius: 6px; font-size: 10px; font-weight: 600; }
.sidebar__section-title { padding: 16px 12px 6px; font-size: 11px; color: rgba(255,255,255,0.4);
                          font-weight: 600; letter-spacing: 0.5px; text-transform: uppercase; }
.sidebar__footer { padding-top: 16px; border-top: 1px solid rgba(255,255,255,0.06);
                   display: flex; align-items: center; gap: 8px; font-size: 11px;
                   color: rgba(255,255,255,0.4); }

/* ── 메인 영역 (사이드바 옆) ──────────────────────────── */
.main { margin-left: var(--sidebar-w); padding: 24px 32px 60px; max-width: 1400px; min-width: 0; }

/* ── Hero 헤더 ──────────────────────────── */
.hero { background: var(--hero-grad); border-radius: var(--radius-lg);
        padding: 28px 28px 26px; margin-bottom: 20px; color: white;
        box-shadow: 0 12px 32px rgba(95,109,255,0.25); position: relative; overflow: hidden; }
.hero::before { content: ""; position: absolute; right: -60px; top: -60px; width: 240px; height: 240px;
                background: radial-gradient(circle, rgba(255,255,255,0.15) 0%, transparent 70%); pointer-events: none; }
.hero__top { display: flex; justify-content: space-between; align-items: flex-start; flex-wrap: wrap;
             gap: 14px; margin-bottom: 18px; }
.hero__greeting { font-size: 13px; font-weight: 500; opacity: 0.85; }
.hero__title { margin: 4px 0 0; font-size: 26px; font-weight: 700; letter-spacing: -0.8px; }
.hero__date { font-size: 12px; opacity: 0.75; margin-top: 6px; }
.hero__advice { display: inline-block; padding: 8px 14px; background: rgba(255,255,255,0.18);
                border-radius: 99px; font-size: 13px; backdrop-filter: blur(8px); }
.hero__kpi-grid { display: grid; grid-template-columns: 2fr 1fr 1fr; gap: 18px; margin-top: 6px; }
.hero__kpi { background: rgba(255,255,255,0.12); border-radius: 14px; padding: 16px 18px;
             backdrop-filter: blur(12px); }
.hero__kpi-label { font-size: 11px; font-weight: 600; opacity: 0.8; letter-spacing: 0.3px;
                   text-transform: uppercase; }
.hero__kpi-value { font-size: 28px; font-weight: 700; margin-top: 6px; letter-spacing: -0.6px; line-height: 1.1; }
.hero__kpi-pnl { font-size: 13px; font-weight: 600; margin-top: 4px; }
.hero__kpi-pnl small { font-weight: 500; opacity: 0.85; margin-left: 4px; }
.hero__kpi--small .hero__kpi-value { font-size: 19px; }

/* ── 섹션 카드 (공통) ──────────────────────────── */
.section { background: var(--surface); border-radius: var(--radius);
           box-shadow: var(--shadow); margin-bottom: 18px; overflow: hidden;
           transition: box-shadow 0.2s, transform 0.2s; scroll-margin-top: 20px; }
.section:hover { box-shadow: var(--shadow-lg); }
.section__head { padding: 18px 22px 16px; border-bottom: 1px solid var(--border); }
.section__title { display: flex; align-items: center; gap: 10px; }
.section__icon { width: 32px; height: 32px; border-radius: 9px; display: flex;
                 align-items: center; justify-content: center; font-size: 16px; flex-shrink: 0; }
.section__icon--value { background: rgba(95,109,255,0.12); }
.section__icon--auto { background: rgba(20,184,166,0.12); }
.section__icon--market { background: rgba(245,158,11,0.12); }
.section__icon--ai { background: rgba(168,85,247,0.12); }
.section__icon--rec { background: rgba(34,197,94,0.12); }
.section__icon--alert { background: rgba(239,68,68,0.12); }
.section__icon--macro { background: rgba(59,130,246,0.12); }

/* ── 🦾 AI 맞춤 비서 카드 (자비스 스타일) ── */
.coach-section { background: linear-gradient(135deg, var(--surface-1) 0%, var(--surface-2) 100%);
                 border: 1px solid var(--accent); position: relative; overflow: visible; }
.coach-section::before { content: ""; position: absolute; top: 0; left: 0; right: 0; height: 3px;
                          background: linear-gradient(90deg, #7c3aed, #ec4899, #f59e0b);
                          border-radius: 14px 14px 0 0; }
.coach-head { display: flex !important; align-items: center; justify-content: space-between;
              gap: 16px; flex-wrap: wrap; padding-top: 22px !important; }
.coach-section .section__icon--ai { background: linear-gradient(135deg, #7c3aed, #ec4899);
                                    color: #fff; box-shadow: 0 2px 8px rgba(124,58,237,0.3); }
.coach-section .section__title h2 { background: linear-gradient(90deg, #7c3aed, #ec4899);
                                     -webkit-background-clip: text; -webkit-text-fill-color: transparent;
                                     background-clip: text; font-weight: 800; }
.coach-body { padding: 16px 24px 22px; font-size: 14px; line-height: 1.7; color: var(--text-1); }
.coach-h3 { font-size: 17px; font-weight: 700; color: var(--accent); margin: 18px 0 10px;
            padding-bottom: 6px; border-bottom: 2px solid var(--surface-2); display: flex;
            align-items: center; gap: 6px; }
.coach-h4 { font-size: 15px; font-weight: 700; color: var(--text-1); margin: 14px 0 8px; }
.coach-h5 { font-size: 13px; font-weight: 600; color: var(--text-2); margin: 10px 0 6px;
            text-transform: uppercase; letter-spacing: 0.5px; }
.coach-p { margin: 6px 0; color: var(--text-1); }
.coach-list { margin: 8px 0; padding-left: 22px; }
.coach-list li { margin: 4px 0; color: var(--text-1); }
.coach-quote { border-left: 3px solid #f59e0b; padding: 10px 14px;
               background: rgba(245,158,11,0.08); margin: 12px 0; border-radius: 0 8px 8px 0;
               color: var(--text-1); font-size: 13px; }
.coach-table-wrap { margin: 12px 0; overflow-x: auto; border-radius: 10px;
                    border: 1px solid var(--border); }
.coach-table { border-collapse: collapse; width: 100%; font-size: 13px; }
.coach-table th { background: var(--surface-2); font-weight: 700; padding: 8px 12px;
                  text-align: left; color: var(--text-1); border-bottom: 2px solid var(--border);
                  position: sticky; top: 0; }
.coach-table td { padding: 8px 12px; border-bottom: 1px solid var(--border); color: var(--text-1); }
.coach-table tr:last-child td { border-bottom: none; }
.coach-table tr:hover td { background: var(--surface-2); }

/* 위험 지수 게이지 */
.risk-gauge { display: flex; flex-direction: column; align-items: center;
              padding: 8px 16px; min-width: 200px; }
.risk-gauge svg { display: block; }
.risk-gauge__label { font-size: 16px; font-weight: 800; margin-top: -8px;
                     letter-spacing: 0.3px; }
.risk-gauge__action { font-size: 11px; color: var(--text-3); margin-top: 2px; }

@media (max-width: 720px) {
  .coach-head { flex-direction: column; align-items: stretch; }
  .risk-gauge { align-self: center; }
  .coach-body { padding: 14px 16px 18px; }
  .coach-table { font-size: 12px; }
  .coach-table th, .coach-table td { padding: 6px 8px; }
}
.section__title h2 { margin: 0; font-size: 16px; font-weight: 700; color: var(--text-1); letter-spacing: -0.2px; }
.section__badge { padding: 3px 10px; background: var(--surface-2); border-radius: 99px;
                  font-size: 11px; color: var(--text-3); font-weight: 600; border: 1px solid var(--border); }
.section__count { margin-left: auto; font-size: 12px; color: var(--text-3); font-weight: 500; }
.section__subtitle { display: flex; align-items: baseline; gap: 14px; margin-top: 12px; flex-wrap: wrap; }
.section__amount { font-size: 22px; font-weight: 700; color: var(--text-1); letter-spacing: -0.4px; }
.section__pnl { font-size: 14px; font-weight: 600; }
.section__pnl.up { color: var(--up); }
.section__pnl.down { color: var(--down); }
.section__pnl.flat { color: var(--neutral); }
.section__body { padding: 4px 0; }

/* 종목 행 */
.row { display: flex; justify-content: space-between; align-items: center;
       padding: 13px 22px; border-bottom: 1px solid var(--border); gap: 12px;
       transition: background 0.15s; }
.row:last-child { border-bottom: none; }
.row:hover { background: var(--surface-2); }
.row__main { flex: 1; min-width: 0; }
.row__name { font-weight: 600; font-size: 15px; color: var(--text-1); display: flex; align-items: center; gap: 6px; }
.row__sub { font-size: 12px; color: var(--text-3); margin-top: 3px; }
.row__price { text-align: right; flex-shrink: 0; }
.row__current { font-weight: 600; font-size: 15px; color: var(--text-1); }
.row__pnl { font-size: 13px; font-weight: 600; margin-top: 3px; white-space: nowrap; }
.row__pnl small { font-weight: 500; opacity: 0.85; margin-left: 4px; font-size: 12px; }
.row__pnl.up { color: var(--up); }
.row__pnl.down { color: var(--down); }
.row__pnl.flat { color: var(--neutral); }
.row__badge { display: inline-block; padding: 2px 7px; border-radius: 6px;
              font-size: 10px; font-weight: 700; vertical-align: middle; }
.row__badge--cut { background: rgba(224,49,49,0.12); color: var(--up); }
.row__badge--t1 { background: rgba(224,49,49,0.12); color: var(--up); }
.row__badge--t2 { background: rgba(224,49,49,0.18); color: var(--up); }
/* ── 📢 최근 공시 카드 ── */
.disc-list { padding: 6px 0; }
.disc-row { display: flex; align-items: flex-start; gap: 12px;
            padding: 12px 22px; border-bottom: 1px solid var(--border);
            text-decoration: none; color: inherit; transition: background 0.15s; }
.disc-row:last-child { border-bottom: none; }
.disc-row:hover { background: var(--surface-2); }
.disc-row--held { background: rgba(124,58,237,0.04); }
.disc-row--held:hover { background: rgba(124,58,237,0.08); }
.disc-row__emoji { font-size: 18px; line-height: 1.2; flex-shrink: 0; padding-top: 1px; }
.disc-row__emoji--bad { filter: drop-shadow(0 0 6px rgba(239,68,68,0.5)); }
.disc-row__emoji--good { filter: drop-shadow(0 0 6px rgba(16,185,129,0.5)); }
.disc-row__main { flex: 1; min-width: 0; }
.disc-row__head { display: flex; align-items: center; gap: 6px; margin-bottom: 2px; }
.disc-row__name { font-weight: 700; font-size: 14px; color: var(--text-1); }
.disc-row__badge { padding: 2px 7px; border-radius: 5px;
                   font-size: 10px; font-weight: 700; }
.disc-row__badge--held { background: rgba(124,58,237,0.15); color: var(--accent); }
.disc-row__title { font-size: 13px; color: var(--text-2); line-height: 1.4;
                   overflow: hidden; text-overflow: ellipsis;
                   display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; }
.disc-row__date { font-size: 11px; color: var(--text-3); flex-shrink: 0;
                  font-variant-numeric: tabular-nums; padding-top: 3px; }
.disc-more { padding: 12px 22px; font-size: 12px; color: var(--text-3); text-align: center;
             border-top: 1px dashed var(--border); }

.row__sparkline { display: flex; flex-direction: column; align-items: flex-end;
                  flex-shrink: 0; min-width: 78px; }
.row__sparkline-label { font-size: 10px; font-weight: 700; margin-top: 1px;
                        letter-spacing: 0.2px; }
@media (max-width: 600px) {
  .row__sparkline { min-width: 60px; }
  .row__sparkline svg { width: 50px !important; height: 20px !important; }
}

/* 자산 배분 카드 */
.allocation { padding: 22px; display: grid; grid-template-columns: 200px 1fr; gap: 28px;
              align-items: center; }
.allocation__chart-wrap { position: relative; width: 200px; height: 200px; }
.allocation__chart-center { position: absolute; inset: 0; display: flex; flex-direction: column;
                            justify-content: center; align-items: center; pointer-events: none; }
.allocation__chart-label { font-size: 11px; color: var(--text-3); font-weight: 600;
                           text-transform: uppercase; letter-spacing: 0.3px; }
.allocation__chart-total { font-size: 18px; font-weight: 700; color: var(--text-1); margin-top: 4px; letter-spacing: -0.3px; }
.allocation__chart-pct { font-size: 12px; color: var(--text-3); margin-top: 2px; }
.allocation__bar { height: 12px; background: var(--surface-2); border-radius: 99px;
                   overflow: hidden; display: flex; }
.allocation__seg { height: 100%; transition: width 0.4s; }
.allocation__seg--value { background: var(--accent-value); }
.allocation__seg--auto { background: var(--accent-auto); }
.allocation__legend { display: flex; flex-direction: column; gap: 12px; margin-top: 16px; }
.allocation__item { display: flex; align-items: center; justify-content: space-between;
                    padding: 10px 14px; background: var(--surface-2); border-radius: 10px; }
.allocation__item-left { display: flex; align-items: center; gap: 10px; }
.allocation__dot { width: 12px; height: 12px; border-radius: 4px; flex-shrink: 0; }
.allocation__dot--value { background: var(--accent-value); }
.allocation__dot--auto { background: var(--accent-auto); }
.allocation__name { color: var(--text-1); font-weight: 600; font-size: 14px; }
.allocation__pct { color: var(--text-3); font-weight: 500; font-size: 12px; margin-left: 4px; }
.allocation__amount { color: var(--text-1); font-weight: 700; font-size: 14px; }
@media (max-width: 700px) {
  .allocation { grid-template-columns: 1fr; }
  .allocation__chart-wrap { margin: 0 auto; }
}

/* 시장 브리핑 카드 그리드 */
.market-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 12px; padding: 16px 22px; }
.market-card { background: var(--surface-2); border-radius: var(--radius-sm);
               padding: 14px 16px; border: 1px solid var(--border);
               transition: transform 0.15s, box-shadow 0.15s; }
.market-card:hover { transform: translateY(-1px); box-shadow: var(--shadow-sm); }
.market-card__top { display: flex; justify-content: space-between; align-items: flex-start; gap: 8px; }
.market-card__main { flex: 1; min-width: 0; }
.market-card__chart { width: 80px; height: 40px; flex-shrink: 0; }
.market-card__label { font-size: 11px; color: var(--text-3); font-weight: 600;
                      text-transform: uppercase; letter-spacing: 0.3px; }
.market-card__value { font-size: 18px; font-weight: 700; margin-top: 4px; color: var(--text-1); }
.market-card__chg { font-size: 12px; font-weight: 600; margin-top: 3px; }
.market-card__chg.up { color: var(--up); }
.market-card__chg.down { color: var(--down); }
.market-card__chg.flat { color: var(--text-3); }

/* 매크로 차트 카드 */
.macro-chart-wrap { padding: 10px 22px 22px; }
.macro-chart { width: 100%; height: 240px; }
.macro-legend { display: flex; gap: 14px; padding: 0 22px 14px; flex-wrap: wrap; font-size: 12px; }
.macro-legend__item { display: flex; align-items: center; gap: 6px; color: var(--text-2); }
.macro-legend__dot { width: 10px; height: 10px; border-radius: 50%; }

/* 차트 모달 (시장 브리핑 카드 클릭 시) */
.market-card { cursor: pointer; }
.modal-overlay { position: fixed; inset: 0; background: rgba(0,0,0,0.55);
                 z-index: 300; display: none; align-items: center; justify-content: center;
                 backdrop-filter: blur(4px); animation: fadein 0.15s ease; padding: 20px; }
.modal-overlay.is-open { display: flex; }
@keyframes fadein { from { opacity: 0; } to { opacity: 1; } }
@keyframes scalein { from { transform: scale(0.95); opacity: 0; } to { transform: scale(1); opacity: 1; } }
.modal { background: var(--surface); border-radius: 20px; max-width: 820px; width: 100%;
         max-height: 90vh; overflow-y: auto; box-shadow: var(--shadow-lg);
         animation: scalein 0.2s ease; }
.modal__head { padding: 22px 24px 18px; border-bottom: 1px solid var(--border);
               display: flex; justify-content: space-between; align-items: center; gap: 12px; }
.modal__title { display: flex; align-items: center; gap: 10px; }
.modal__title h3 { margin: 0; font-size: 18px; font-weight: 700; color: var(--text-1); }
.modal__title-badge { padding: 3px 9px; background: var(--surface-2); border-radius: 99px;
                      font-size: 11px; color: var(--text-3); font-weight: 600; }
.modal__close { background: var(--surface-2); border: none; font-size: 18px; cursor: pointer;
                color: var(--text-2); width: 36px; height: 36px; border-radius: 10px;
                display: flex; align-items: center; justify-content: center; transition: all 0.15s; }
.modal__close:hover { background: var(--up); color: white; }
.modal__body { padding: 22px 24px; }
.modal__chart-wrap { height: 320px; margin-bottom: 22px; }
.modal__chart { width: 100%; height: 100%; }
.modal__stats { display: grid; grid-template-columns: repeat(4, 1fr); gap: 10px; margin-bottom: 18px; }
.modal__stat { padding: 12px 14px; background: var(--surface-2); border-radius: 12px; }
.modal__stat-label { font-size: 10px; color: var(--text-3); text-transform: uppercase;
                     font-weight: 600; letter-spacing: 0.3px; }
.modal__stat-value { font-size: 16px; font-weight: 700; margin-top: 4px; color: var(--text-1); }
.modal__stat-value.up { color: var(--up); }
.modal__stat-value.down { color: var(--down); }
.modal__external { display: flex; gap: 10px; flex-wrap: wrap; }
.modal__btn { padding: 11px 16px; border-radius: 11px; border: 1px solid var(--border);
              background: var(--surface-2); color: var(--text-1); text-align: center;
              text-decoration: none; font-weight: 600; font-size: 13px; flex: 1; min-width: 140px;
              transition: all 0.15s; display: flex; align-items: center; justify-content: center; gap: 6px; }
.modal__btn:hover { background: var(--accent); color: white; border-color: var(--accent);
                    transform: translateY(-1px); }
.modal__btn::after { content: "↗"; font-size: 12px; opacity: 0.7; }
@media (max-width: 600px) {
  .modal { border-radius: 16px; }
  .modal__stats { grid-template-columns: repeat(2, 1fr); }
  .modal__chart-wrap { height: 240px; }
}

/* 매크로 / AI / 추천 영역 — wrapper로 감쌈 */
.embed-wrap { padding: 4px 22px 18px; }
.embed-wrap > div:first-child { margin: 0; padding: 0; background: transparent !important; border: none !important; }

/* 빈 상태 */
.empty { padding: 36px 22px; text-align: center; color: var(--text-3); font-size: 13px; }
.empty__icon { font-size: 28px; margin-bottom: 10px; opacity: 0.5; }
.empty__title { font-weight: 600; color: var(--text-2); font-size: 14px; margin-bottom: 4px; }
.empty__desc { font-size: 12px; line-height: 1.6; }

/* 다크모드 토글 + 햄버거 */
.toolbar { position: fixed; top: 16px; right: 16px; display: flex; gap: 8px; z-index: 100; }
.icon-btn { width: 40px; height: 40px; border-radius: 12px;
            background: var(--surface); color: var(--text-1);
            border: 1px solid var(--border); box-shadow: var(--shadow-sm);
            cursor: pointer; font-size: 16px; display: inline-flex;
            align-items: center; justify-content: center;
            transition: transform 0.15s, box-shadow 0.15s; }
.icon-btn:hover { transform: translateY(-1px); box-shadow: var(--shadow); }
.hamburger { display: none; }

/* 푸터 */
.footer { padding: 22px; text-align: center; color: var(--text-3); font-size: 11px; line-height: 1.7; }

/* ── 모바일 반응형 ──────────────────────────── */
@media (max-width: 900px) {
  .sidebar { transform: translateX(-100%); width: 280px; z-index: 200;
             transition: transform 0.25s ease;
             box-shadow: 8px 0 32px rgba(0,0,0,0.2); }
  .sidebar.is-open { transform: translateX(0); }
  .main { margin-left: 0; }
  .sidebar-backdrop { display: none; position: fixed; inset: 0; background: rgba(0,0,0,0.4);
                      z-index: 150; backdrop-filter: blur(2px); }
  .sidebar-backdrop.is-open { display: block; }
  .hamburger { display: inline-flex; }
  .main { padding: 70px 16px 40px; }
  .hero { padding: 22px 20px; }
  .hero__title { font-size: 22px; }
  .hero__kpi-grid { grid-template-columns: 1fr; gap: 10px; }
  .hero__kpi-value { font-size: 22px; }
  .hero__kpi--small .hero__kpi-value { font-size: 17px; }
  .market-grid { grid-template-columns: repeat(2, 1fr); padding: 14px 16px; gap: 8px; }
  .section__head { padding: 16px 18px 14px; }
  .row { padding: 12px 18px; }
  .row__name { font-size: 14px; }
  .toolbar { top: 12px; right: 12px; }
  .embed-wrap { padding: 4px 16px 14px; }
}
@media (max-width: 480px) {
  .market-grid { grid-template-columns: 1fr; }
  .section__amount { font-size: 19px; }
  .hero__kpi-value { font-size: 20px; }
}
</style>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4/dist/chart.umd.min.js"></script>
<script>
(function () {
  // 다크모드 즉시 적용 (FOUC 방지)
  try {
    var saved = localStorage.getItem('dash-theme');
    if (saved === 'dark' || saved === 'light') document.documentElement.setAttribute('data-theme', saved);
  } catch (e) {}

  document.addEventListener('DOMContentLoaded', function () {
    // ── 다크모드 토글 + 햄버거 ──
    var themeBtn = document.querySelector('.theme-toggle');
    if (themeBtn) {
      function syncTheme() {
        var dark = document.documentElement.getAttribute('data-theme') === 'dark'
          || (!document.documentElement.getAttribute('data-theme') &&
              window.matchMedia('(prefers-color-scheme: dark)').matches);
        themeBtn.textContent = dark ? '☀' : '☾';
      }
      themeBtn.onclick = function () {
        var cur = document.documentElement.getAttribute('data-theme');
        var next = cur === 'dark' ? 'light' : 'dark';
        document.documentElement.setAttribute('data-theme', next);
        try { localStorage.setItem('dash-theme', next); } catch (e) {}
        syncTheme();
      };
      syncTheme();
    }

    var ham = document.querySelector('.hamburger');
    var sidebar = document.querySelector('.sidebar');
    var backdrop = document.querySelector('.sidebar-backdrop');
    function toggle() {
      if (!sidebar) return;
      sidebar.classList.toggle('is-open');
      if (backdrop) backdrop.classList.toggle('is-open');
    }
    if (ham) ham.onclick = toggle;
    if (backdrop) backdrop.onclick = toggle;

    // ── 사이드바 활성 링크 추적 ──
    var links = document.querySelectorAll('.sidebar__link[data-target]');
    var sections = [];
    links.forEach(function (l) {
      var t = l.getAttribute('data-target');
      var s = t ? document.getElementById(t) : null;
      if (s) sections.push({ link: l, section: s });
    });
    function setActive() {
      var y = window.scrollY + 100;
      var current = sections[0];
      sections.forEach(function (e) {
        if (e.section.offsetTop <= y) current = e;
      });
      links.forEach(function (l) { l.classList.remove('is-active'); });
      if (current) current.link.classList.add('is-active');
    }
    window.addEventListener('scroll', setActive, { passive: true });
    setActive();

    // 사이드바 링크 클릭 시 모바일에서 사이드바 닫기
    links.forEach(function (l) {
      l.addEventListener('click', function () {
        if (window.innerWidth <= 900 && sidebar) {
          sidebar.classList.remove('is-open');
          if (backdrop) backdrop.classList.remove('is-open');
        }
      });
    });

    // ── 자산 배분 도넛 차트 (Chart.js) ──
    function drawAllocChart() {
      var canvas = document.getElementById('alloc-chart');
      if (!canvas || typeof Chart === 'undefined') return;
      var v = parseFloat(canvas.dataset.value || 0);
      var a = parseFloat(canvas.dataset.auto || 0);
      if (v + a <= 0) return;
      var dark = document.documentElement.getAttribute('data-theme') === 'dark'
        || (!document.documentElement.getAttribute('data-theme') &&
            window.matchMedia('(prefers-color-scheme: dark)').matches);
      var colorValue = dark ? '#748ffc' : '#5f6dff';
      var colorAuto = dark ? '#2dd4bf' : '#14b8a6';
      var colorBg = dark ? '#131826' : '#ffffff';
      try {
        if (canvas._chart) canvas._chart.destroy();
        canvas._chart = new Chart(canvas.getContext('2d'), {
          type: 'doughnut',
          data: {
            labels: ['가치주', '자동매매'],
            datasets: [{
              data: [v, a],
              backgroundColor: [colorValue, colorAuto],
              borderColor: colorBg,
              borderWidth: 3,
              hoverOffset: 6,
            }]
          },
          options: {
            responsive: false,
            cutout: '72%',
            plugins: {
              legend: { display: false },
              tooltip: {
                backgroundColor: dark ? '#1a1f2e' : '#0a0e1a',
                titleColor: '#ffffff',
                bodyColor: '#ffffff',
                padding: 10,
                cornerRadius: 8,
                callbacks: {
                  label: function(c) {
                    var t = c.dataset.data.reduce(function(s,x){return s+x;}, 0);
                    var pct = t > 0 ? (c.parsed / t * 100).toFixed(1) : 0;
                    return c.label + ': ' + Math.round(c.parsed).toLocaleString() + '원 (' + pct + '%)';
                  }
                }
              }
            },
            animation: { animateRotate: true, animateScale: true, duration: 600 },
          }
        });
      } catch (e) { console.warn('alloc chart error', e); }
    }
    drawAllocChart();

    // ── 시장 브리핑 sparkline (각 카드 7일 추세) ──
    function drawSparklines() {
      if (typeof Chart === 'undefined') return;
      var data = window._chartData || {};
      var canvases = document.querySelectorAll('.spark-chart');
      canvases.forEach(function (canvas) {
        var key = canvas.dataset.key;
        var d = data[key];
        if (!d || !d.values || d.values.length < 2) return;
        var first = d.values[0], last = d.values[d.values.length - 1];
        var dark = document.documentElement.getAttribute('data-theme') === 'dark'
          || (!document.documentElement.getAttribute('data-theme') &&
              window.matchMedia('(prefers-color-scheme: dark)').matches);
        var rising = last >= first;
        // VIX는 반대 의미: 올라가면 위험 (파랑 X 빨강 O 반대로)
        var color;
        if (key === 'vix') {
          color = rising ? (dark ? '#ff6b6b' : '#e03131') : (dark ? '#4dabf7' : '#1971c2');
        } else {
          color = rising ? (dark ? '#ff6b6b' : '#e03131') : (dark ? '#4dabf7' : '#1971c2');
        }
        try {
          if (canvas._chart) canvas._chart.destroy();
          canvas._chart = new Chart(canvas.getContext('2d'), {
            type: 'line',
            data: {
              labels: d.labels,
              datasets: [{
                data: d.values,
                borderColor: color,
                backgroundColor: color + '22',
                borderWidth: 1.8,
                pointRadius: 0,
                pointHoverRadius: 3,
                tension: 0.35,
                fill: true,
              }]
            },
            options: {
              responsive: false,
              maintainAspectRatio: false,
              plugins: {
                legend: { display: false },
                tooltip: {
                  backgroundColor: dark ? '#1a1f2e' : '#0a0e1a',
                  titleColor: '#ffffff', bodyColor: '#ffffff',
                  padding: 8, cornerRadius: 6, displayColors: false,
                  callbacks: { label: function(c){ return c.parsed.y.toLocaleString(); } }
                }
              },
              scales: { x: { display: false }, y: { display: false } },
              animation: { duration: 0 },
              interaction: { intersect: false, mode: 'index' },
            }
          });
        } catch (e) { console.warn('spark error', key, e); }
      });
    }
    drawSparklines();

    // ── 매크로 라인 차트 (TNX/IRX/DXY 30일) ──
    function drawMacroChart() {
      if (typeof Chart === 'undefined') return;
      var canvas = document.getElementById('macro-chart');
      if (!canvas) return;
      var data = window._chartData || {};
      var dark = document.documentElement.getAttribute('data-theme') === 'dark'
        || (!document.documentElement.getAttribute('data-theme') &&
            window.matchMedia('(prefers-color-scheme: dark)').matches);
      var gridColor = dark ? 'rgba(255,255,255,0.08)' : 'rgba(0,0,0,0.06)';
      var textColor = dark ? '#94a3b8' : '#94a3b8';
      var datasets = [];
      function add(key, label, color, axis) {
        var d = data[key];
        if (d && d.values && d.values.length > 1) {
          datasets.push({
            label: label, data: d.values,
            borderColor: color, backgroundColor: color + '15',
            borderWidth: 2, pointRadius: 0, pointHoverRadius: 4,
            tension: 0.3, yAxisID: axis,
          });
        }
      }
      add('tnx', '10년물 금리 (TNX)', '#5f6dff', 'y');
      add('irx', '단기 금리 (IRX)', '#14b8a6', 'y');
      add('dxy', '달러인덱스 (DXY)', '#f59e0b', 'y2');
      if (datasets.length === 0) return;
      var labels = (data.tnx && data.tnx.labels) || (data.irx && data.irx.labels) || (data.dxy && data.dxy.labels) || [];
      try {
        if (canvas._chart) canvas._chart.destroy();
        canvas._chart = new Chart(canvas.getContext('2d'), {
          type: 'line',
          data: { labels: labels, datasets: datasets },
          options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
              legend: { display: false },
              tooltip: {
                backgroundColor: dark ? '#1a1f2e' : '#0a0e1a',
                titleColor: '#ffffff', bodyColor: '#ffffff',
                padding: 10, cornerRadius: 8,
              }
            },
            scales: {
              x: { ticks: { color: textColor, font: { size: 10 }, maxTicksLimit: 8 },
                   grid: { color: gridColor, drawBorder: false } },
              y: { position: 'left', ticks: { color: textColor, font: { size: 10 } },
                   grid: { color: gridColor, drawBorder: false },
                   title: { display: true, text: '금리 (%)', color: textColor, font: { size: 10 } } },
              y2: { position: 'right', ticks: { color: textColor, font: { size: 10 } },
                    grid: { drawOnChartArea: false },
                    title: { display: true, text: 'DXY', color: textColor, font: { size: 10 } } },
            },
            interaction: { intersect: false, mode: 'index' },
          }
        });
      } catch (e) { console.warn('macro chart error', e); }
    }
    drawMacroChart();

    // ── 차트 상세 모달 (시장 브리핑 카드 클릭) ──
    var marketInfo = {
      kospi:  { name:'코스피', unit:'', urls:[
        {label:'네이버 증권', url:'https://finance.naver.com/sise/sise_index.naver?code=KOSPI'},
        {label:'TradingView', url:'https://www.tradingview.com/symbols/KRX-KOSPI/'}
      ]},
      sp500:  { name:'S&P 500', unit:'', urls:[
        {label:'Yahoo Finance', url:'https://finance.yahoo.com/quote/%5EGSPC'},
        {label:'TradingView', url:'https://www.tradingview.com/symbols/SP-SPX/'}
      ]},
      vix:    { name:'VIX (변동성지수)', unit:'', urls:[
        {label:'Yahoo Finance', url:'https://finance.yahoo.com/quote/%5EVIX'},
        {label:'TradingView', url:'https://www.tradingview.com/symbols/CBOE-VIX/'}
      ]},
      usdkrw: { name:'달러/원 환율', unit:'원', urls:[
        {label:'네이버 증권', url:'https://finance.naver.com/marketindex/exchangeDetail.naver?marketindexCd=FX_USDKRW'},
        {label:'Investing.com', url:'https://kr.investing.com/currencies/usd-krw'}
      ]},
      wti:    { name:'WTI 유가', unit:'$', urls:[
        {label:'네이버 증권', url:'https://finance.naver.com/marketindex/worldOilDetail.naver?marketindexCd=OIL_CL'},
        {label:'Yahoo Finance', url:'https://finance.yahoo.com/quote/CL=F'}
      ]},
      tnx:    { name:'미국 10년물 금리 (TNX)', unit:'%', urls:[
        {label:'Yahoo Finance', url:'https://finance.yahoo.com/quote/%5ETNX'},
        {label:'TradingView', url:'https://www.tradingview.com/symbols/TVC-US10Y/'}
      ]},
      irx:    { name:'미국 단기금리 (IRX)', unit:'%', urls:[
        {label:'Yahoo Finance', url:'https://finance.yahoo.com/quote/%5EIRX'},
        {label:'TradingView', url:'https://www.tradingview.com/symbols/TVC-US03MY/'}
      ]},
      dxy:    { name:'달러인덱스 (DXY)', unit:'', urls:[
        {label:'Yahoo Finance', url:'https://finance.yahoo.com/quote/DX-Y.NYB'},
        {label:'TradingView', url:'https://www.tradingview.com/symbols/TVC-DXY/'}
      ]}
    };
    var modalChartInst = null;
    function openChartModal(key) {
      var info = marketInfo[key];
      var d = (window._chartData || {})[key];
      if (!info || !d || !d.values || d.values.length < 2) return;
      var dark = document.documentElement.getAttribute('data-theme') === 'dark'
        || (!document.documentElement.getAttribute('data-theme') &&
            window.matchMedia('(prefers-color-scheme: dark)').matches);
      var values = d.values;
      var first = values[0], last = values[values.length - 1];
      var min = Math.min.apply(null, values), max = Math.max.apply(null, values);
      var avg = values.reduce(function(s,x){return s+x;}, 0) / values.length;
      var chg = first !== 0 ? (last - first) / first * 100 : 0;
      var chgCls = chg >= 0 ? 'up' : 'down';
      var sign = chg >= 0 ? '+' : '';
      function fmt(v) {
        var n = Math.abs(v) >= 1000 ? Math.round(v).toLocaleString()
              : Math.abs(v) >= 10 ? v.toFixed(2)
              : v.toFixed(3);
        return n + info.unit;
      }
      document.getElementById('modal-title').textContent = info.name;
      document.getElementById('modal-period').textContent = d.values.length + '일 추이';
      document.getElementById('modal-stats').innerHTML =
        '<div class="modal__stat"><div class="modal__stat-label">현재</div><div class="modal__stat-value">' + fmt(last) + '</div></div>' +
        '<div class="modal__stat"><div class="modal__stat-label">기간 변동</div><div class="modal__stat-value ' + chgCls + '">' + sign + chg.toFixed(2) + '%</div></div>' +
        '<div class="modal__stat"><div class="modal__stat-label">최고</div><div class="modal__stat-value">' + fmt(max) + '</div></div>' +
        '<div class="modal__stat"><div class="modal__stat-label">최저</div><div class="modal__stat-value">' + fmt(min) + '</div></div>';
      document.getElementById('modal-external').innerHTML =
        info.urls.map(function(u){
          return '<a class="modal__btn" href="' + u.url + '" target="_blank" rel="noopener noreferrer">' + u.label + '</a>';
        }).join('') +
        '<div style="flex-basis:100%;font-size:11px;color:var(--text-3);margin-top:6px;">' +
        '봇 데이터: ' + d.values.length + '일 (1시간마다 캐시 갱신) · 실시간 차트는 위 외부 링크 클릭' +
        '</div>';
      // 차트 그리기
      var canvas = document.getElementById('modal-chart');
      try { if (modalChartInst) modalChartInst.destroy(); } catch (e) {}
      var color = chg >= 0 ? (dark ? '#ff6b6b' : '#e03131') : (dark ? '#4dabf7' : '#1971c2');
      var gridColor = dark ? 'rgba(255,255,255,0.08)' : 'rgba(0,0,0,0.06)';
      var textColor = dark ? '#94a3b8' : '#94a3b8';
      modalChartInst = new Chart(canvas.getContext('2d'), {
        type: 'line',
        data: {
          labels: d.labels,
          datasets: [{
            label: info.name,
            data: values,
            borderColor: color, backgroundColor: color + '20',
            borderWidth: 2.2, pointRadius: 0, pointHoverRadius: 5,
            tension: 0.3, fill: true,
          }]
        },
        options: {
          responsive: true, maintainAspectRatio: false,
          plugins: {
            legend: { display: false },
            tooltip: {
              backgroundColor: dark ? '#1a1f2e' : '#0a0e1a',
              titleColor: '#ffffff', bodyColor: '#ffffff',
              padding: 12, cornerRadius: 8, displayColors: false,
              callbacks: { label: function(c){ return fmt(c.parsed.y); } }
            }
          },
          scales: {
            x: { ticks: { color: textColor, font: { size: 10 }, maxTicksLimit: 8 },
                 grid: { color: gridColor, drawBorder: false } },
            y: { ticks: { color: textColor, font: { size: 10 } },
                 grid: { color: gridColor, drawBorder: false } },
          },
          interaction: { intersect: false, mode: 'index' },
        }
      });
      document.getElementById('chart-modal').classList.add('is-open');
      document.body.style.overflow = 'hidden';
    }
    function closeChartModal() {
      document.getElementById('chart-modal').classList.remove('is-open');
      document.body.style.overflow = '';
    }
    // 카드 클릭 핸들러
    document.querySelectorAll('.market-card').forEach(function(card){
      var canvas = card.querySelector('.spark-chart');
      var key = canvas && canvas.dataset.key;
      if (!key || !marketInfo[key]) {
        card.style.cursor = 'default';
        return;
      }
      card.addEventListener('click', function(){ openChartModal(key); });
    });
    document.getElementById('modal-close').addEventListener('click', closeChartModal);
    document.getElementById('chart-modal').addEventListener('click', function(e){
      if (e.target === this) closeChartModal();
    });
    document.addEventListener('keydown', function(e){
      if (e.key === 'Escape') closeChartModal();
    });

    // 다크모드 토글 시 차트 색상 갱신
    if (themeBtn) {
      var origClick = themeBtn.onclick;
      themeBtn.onclick = function() {
        origClick();
        drawAllocChart();
        drawSparklines();
        drawMacroChart();
      };
    }
  });
})();
</script>
"""


def _fmt_money_kr(v: float) -> str:
    """금액 한국어 포맷. 음수도 처리."""
    try:
        return f"{int(round(v)):+,}" if v < 0 else f"{int(round(v)):,}"
    except Exception:
        return "0"


def _pnl_class(pct: float) -> str:
    """손익률 → CSS 클래스 (한국식: 빨강 상승, 파랑 하락)."""
    if pct > 0.01:
        return "up"
    if pct < -0.01:
        return "down"
    return "flat"


def _make_total_summary_section(value_holdings: list, auto_positions: list) -> str:
    """총 자산 요약 섹션 — 가치주+자동매매 합계."""
    v_value = sum(h.get("value", 0) for h in (value_holdings or []))
    v_cost  = sum(h.get("cost", 0) for h in (value_holdings or []))
    v_pnl   = v_value - v_cost
    v_pct   = (v_pnl / v_cost * 100) if v_cost > 0 else 0

    a_value, a_cost = 0.0, 0.0
    for p in (auto_positions or []):
        bp = p.get("buy_price", 0); qty = p.get("qty", 0)
        cp = p.get("curr_price", 0)
        a_value += cp * qty
        a_cost  += bp * qty
    a_pnl = a_value - a_cost
    a_pct = (a_pnl / a_cost * 100) if a_cost > 0 else 0

    total_value = v_value + a_value
    total_cost  = v_cost + a_cost
    total_pnl   = total_value - total_cost
    total_pct   = (total_pnl / total_cost * 100) if total_cost > 0 else 0

    if total_cost <= 0:
        return ""

    pnl_cls = _pnl_class(total_pct)
    pnl_sign = "+" if total_pnl >= 0 else ""

    chips = []
    if v_cost > 0:
        v_cls = _pnl_class(v_pct)
        chips.append(
            f'<div class="summary__chip"><span class="dot dot--value"></span>'
            f'가치주 {int(round(v_value)):,}원 '
            f'<small class="{v_cls}">({"+" if v_pct>=0 else ""}{v_pct:.2f}%)</small></div>'
        )
    if a_cost > 0:
        a_cls = _pnl_class(a_pct)
        chips.append(
            f'<div class="summary__chip"><span class="dot dot--auto"></span>'
            f'자동매매 {int(round(a_value)):,}원 '
            f'<small class="{a_cls}">({"+" if a_pct>=0 else ""}{a_pct:.2f}%)</small></div>'
        )
    split_html = (
        f'<div class="summary__split">{"".join(chips)}</div>'
        if len(chips) >= 2 else ""
    )

    return f"""
<section class="summary" aria-label="총 평가 자산">
  <div class="summary__label">💼 총 평가 자산</div>
  <div class="summary__total">{int(round(total_value)):,}원</div>
  <div class="summary__pnl {pnl_cls}">{pnl_sign}{int(round(total_pnl)):,}원
    <small>({pnl_sign}{total_pct:.2f}%)</small></div>
  {split_html}
</section>
"""


def _make_value_holdings_section(value_holdings: list, sparklines: dict = None) -> str:
    """가치주 보유 섹션 — 미래에셋증권 (HOLDINGS_JSON 기반).

    sparklines: {code: {values, labels, change_pct}} — 종목별 7일 종가 추세선 데이터.
    """
    if not value_holdings:
        return _empty_section("value", "💼", "section__icon--value", "가치주 보유",
                              "미래에셋증권", "등록된 가치주가 없습니다",
                              "채팅창에서 '한화에어로 10주 180000원에 샀어' 같이 등록하면 표시됩니다.")
    sparklines = sparklines or {}
    items = sorted(value_holdings, key=lambda h: h.get("profit", 0), reverse=True)
    total_value = sum(h.get("value", 0) for h in items)
    total_cost  = sum(h.get("cost", 0) for h in items)
    total_pnl   = total_value - total_cost
    total_pct   = (total_pnl / total_cost * 100) if total_cost > 0 else 0
    pnl_cls = _pnl_class(total_pct)
    pnl_sign = "+" if total_pnl >= 0 else ""

    rows = []
    for h in items:
        name = h.get("name", h.get("code", ""))
        code = h.get("code", "")
        qty = int(h.get("qty", 0))
        avg = h.get("avg_price", 0)
        curr = h.get("curr_price", 0)
        pct = h.get("pct", 0)
        profit = h.get("profit", 0)
        cls = _pnl_class(pct)
        sign = "+" if profit >= 0 else ""
        atype = h.get("type", "")
        badge = ""
        if atype == "손절":
            badge = '<span class="row__badge row__badge--cut">손절</span>'
        elif atype == "목표1":
            badge = '<span class="row__badge row__badge--t1">+10%</span>'
        elif atype == "목표2":
            badge = '<span class="row__badge row__badge--t2">+20%</span>'
        # 7일 sparkline
        spark = sparklines.get(code, {})
        spark_svg = _make_sparkline_svg(
            spark.get("values", []),
            change_pct=spark.get("change_pct", 0),
        ) if spark else ""
        spark_label = ""
        if spark.get("change_pct") is not None:
            chg = spark.get("change_pct", 0)
            chg_color = "#10b981" if chg > 0 else ("#ef4444" if chg < 0 else "#94a3b8")
            spark_label = f'<div class="row__sparkline-label" style="color:{chg_color};">7일 {chg:+.1f}%</div>'

        rows.append(f"""
    <div class="row">
      <div class="row__main">
        <div class="row__name">{name}{badge}</div>
        <div class="row__sub">{qty:,}주 · 평단 {avg:,.0f}원</div>
      </div>
      <div class="row__sparkline">
        {spark_svg}
        {spark_label}
      </div>
      <div class="row__price">
        <div class="row__current">{curr:,.0f}원</div>
        <div class="row__pnl {cls}">{sign}{int(round(profit)):,}원<small>({sign}{pct:.2f}%)</small></div>
      </div>
    </div>""")

    return f"""
<section class="section" id="value" aria-label="가치주 보유">
  <div class="section__head">
    <div class="section__title">
      <span class="section__icon section__icon--value">💼</span>
      <h2>가치주 보유</h2>
      <span class="section__badge">미래에셋증권</span>
      <span class="section__count">{len(items)}종목</span>
    </div>
    <div class="section__subtitle">
      <div class="section__amount">{int(round(total_value)):,}원</div>
      <div class="section__pnl {pnl_cls}">{pnl_sign}{int(round(total_pnl)):,}원 ({pnl_sign}{total_pct:.2f}%)</div>
    </div>
  </div>
  <div class="section__body">{"".join(rows)}
  </div>
</section>
"""


def _make_auto_positions_section(auto_positions: list) -> str:
    """자동매매 보유 섹션 — 한국투자증권 모의투자 (positions.json 기반)."""
    if not auto_positions:
        return _empty_section("auto", "🤖", "section__icon--auto", "자동매매 보유",
                              "한국투자증권 (모의)", "아직 매수된 종목이 없습니다",
                              "평일 09:10 첫 자동매수 후 표시됩니다. 보유 풀 = KR_STOCKS 26종목 + 시장 스캔 상위 50종목.")
    enriched = []
    for p in auto_positions:
        bp = p.get("buy_price", 0); qty = p.get("qty", 0)
        cp = p.get("curr_price", 0)
        if cp <= 0:
            try:
                if _kis.available():
                    pi = _kis.get_price(p.get("code", ""))
                    cp = _safe_float(pi.get("stck_prpr")) if pi else 0
            except Exception:
                cp = 0
        pct = ((cp - bp) / bp * 100) if (cp and bp) else 0
        profit = (cp - bp) * qty if (cp and bp and qty) else 0
        enriched.append({**p, "curr_price": cp, "pct": pct, "profit": profit,
                         "value": cp * qty, "cost": bp * qty})
    enriched.sort(key=lambda x: x["profit"], reverse=True)
    total_value = sum(e["value"] for e in enriched)
    total_cost  = sum(e["cost"] for e in enriched)
    total_pnl   = total_value - total_cost
    total_pct   = (total_pnl / total_cost * 100) if total_cost > 0 else 0
    pnl_cls = _pnl_class(total_pct)
    pnl_sign = "+" if total_pnl >= 0 else ""

    rows = []
    for e in enriched:
        name = e.get("name", e.get("code", ""))
        qty = int(e.get("qty", 0))
        bp = e.get("buy_price", 0)
        cp = e.get("curr_price", 0)
        pct = e.get("pct", 0)
        profit = e.get("profit", 0)
        cls = _pnl_class(pct)
        sign = "+" if profit >= 0 else ""
        partial = " · 1차매도완료" if e.get("partial_sold") else ""
        rows.append(f"""
    <div class="row">
      <div class="row__main">
        <div class="row__name">{name}</div>
        <div class="row__sub">{qty:,}주 · 매수 {bp:,.0f}원{partial}</div>
      </div>
      <div class="row__price">
        <div class="row__current">{cp:,.0f}원</div>
        <div class="row__pnl {cls}">{sign}{int(round(profit)):,}원<small>({sign}{pct:.2f}%)</small></div>
      </div>
    </div>""")

    return f"""
<section class="section" id="auto" aria-label="자동매매 보유">
  <div class="section__head">
    <div class="section__title">
      <span class="section__icon section__icon--auto">🤖</span>
      <h2>자동매매 보유</h2>
      <span class="section__badge">한국투자증권 모의</span>
      <span class="section__count">{len(enriched)}종목</span>
    </div>
    <div class="section__subtitle">
      <div class="section__amount">{int(round(total_value)):,}원</div>
      <div class="section__pnl {pnl_cls}">{pnl_sign}{int(round(total_pnl)):,}원 ({pnl_sign}{total_pct:.2f}%)</div>
    </div>
  </div>
  <div class="section__body">{"".join(rows)}
  </div>
</section>
"""


def _empty_section(sid: str, icon: str, icon_cls: str, title: str, badge: str,
                   empty_title: str, empty_desc: str) -> str:
    """데이터가 없을 때 안내 메시지를 띄우는 빈 섹션."""
    return f"""
<section class="section" id="{sid}" aria-label="{title}">
  <div class="section__head">
    <div class="section__title">
      <span class="section__icon {icon_cls}">{icon}</span>
      <h2>{title}</h2>
      <span class="section__badge">{badge}</span>
    </div>
  </div>
  <div class="empty">
    <div class="empty__icon">{icon}</div>
    <div class="empty__title">{empty_title}</div>
    <div class="empty__desc">{empty_desc}</div>
  </div>
</section>
"""


def _make_hero_header(today: str, time_str: str, mood: dict, fg: dict,
                      total_value: float, total_pnl: float, total_pct: float) -> str:
    """대시보드 상단 Hero 헤더 — 그라데이션 배경 + 핵심 KPI."""
    pnl_cls = _pnl_class(total_pct)
    sign = "+" if total_pnl >= 0 else ""
    m = mood or {}
    f = fg or {"score": 50, "label": "중립"}
    advice = m.get("advice", "") or ""
    advice_html = f'<div class="hero__advice">{advice}</div>' if advice else ""

    kospi_price = m.get("kospi_price", 0)
    kospi_chg = m.get("kospi_chg", 0)
    kospi_arr = "▲" if kospi_chg >= 0 else "▼"
    fg_score = f.get("score", 50)
    fg_label = f.get("label", "중립")

    if total_value > 0:
        kpi_main = f"""
    <div class="hero__kpi">
      <div class="hero__kpi-label">💼 총 평가 자산</div>
      <div class="hero__kpi-value">{int(round(total_value)):,}원</div>
      <div class="hero__kpi-pnl">{sign}{int(round(total_pnl)):,}원<small>({sign}{total_pct:.2f}%)</small></div>
    </div>"""
    else:
        kpi_main = """
    <div class="hero__kpi">
      <div class="hero__kpi-label">💼 총 평가 자산</div>
      <div class="hero__kpi-value">데이터 준비 중</div>
      <div class="hero__kpi-pnl"><small>봇 첫 실행 후 표시</small></div>
    </div>"""

    return f"""
<header class="hero" id="overview">
  <div class="hero__top">
    <div>
      <div class="hero__greeting">투자 비서 v6.0</div>
      <h1 class="hero__title">대시보드</h1>
      <div class="hero__date">{today} · {time_str} KST</div>
    </div>
    {advice_html}
  </div>
  <div class="hero__kpi-grid">
    {kpi_main}
    <div class="hero__kpi hero__kpi--small">
      <div class="hero__kpi-label">코스피</div>
      <div class="hero__kpi-value">{kospi_price:,.2f}</div>
      <div class="hero__kpi-pnl">{kospi_arr} {abs(kospi_chg):.2f}%</div>
    </div>
    <div class="hero__kpi hero__kpi--small">
      <div class="hero__kpi-label">공포탐욕</div>
      <div class="hero__kpi-value">{fg_score}</div>
      <div class="hero__kpi-pnl"><small>{fg_label}</small></div>
    </div>
  </div>
</header>
"""


def _make_allocation_card(value_holdings: list, auto_positions: list) -> str:
    """자산 배분 카드 — 가치주 vs 자동매매 비중 (도넛 차트 + progress bar)."""
    v_value = sum(h.get("value", 0) for h in (value_holdings or []))
    a_value = sum((p.get("curr_price", 0) * p.get("qty", 0)) for p in (auto_positions or []))
    total = v_value + a_value
    if total <= 0:
        return ""
    v_pct = v_value / total * 100
    a_pct = a_value / total * 100
    total_man = total / 10000  # 만원 단위

    return f"""
<section class="section" id="allocation" aria-label="자산 배분">
  <div class="section__head">
    <div class="section__title">
      <span class="section__icon section__icon--macro">📊</span>
      <h2>자산 배분</h2>
      <span class="section__count">총 {int(round(total)):,}원</span>
    </div>
  </div>
  <div class="allocation">
    <div class="allocation__chart-wrap">
      <canvas id="alloc-chart" width="200" height="200"
        data-value="{v_value:.0f}" data-auto="{a_value:.0f}"></canvas>
      <div class="allocation__chart-center">
        <div class="allocation__chart-label">총 자산</div>
        <div class="allocation__chart-total">{total_man:,.0f}만원</div>
      </div>
    </div>
    <div class="allocation__detail">
      <div class="allocation__bar">
        <div class="allocation__seg allocation__seg--value" style="width:{v_pct:.1f}%"></div>
        <div class="allocation__seg allocation__seg--auto" style="width:{a_pct:.1f}%"></div>
      </div>
      <div class="allocation__legend">
        <div class="allocation__item">
          <div class="allocation__item-left">
            <span class="allocation__dot allocation__dot--value"></span>
            <span class="allocation__name">가치주<span class="allocation__pct">{v_pct:.1f}%</span></span>
          </div>
          <span class="allocation__amount">{int(round(v_value)):,}원</span>
        </div>
        <div class="allocation__item">
          <div class="allocation__item-left">
            <span class="allocation__dot allocation__dot--auto"></span>
            <span class="allocation__name">자동매매<span class="allocation__pct">{a_pct:.1f}%</span></span>
          </div>
          <span class="allocation__amount">{int(round(a_value)):,}원</span>
        </div>
      </div>
    </div>
  </div>
</section>
"""


_HISTORY_CACHE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "history_cache.json")


def _fetch_market_history(period_days: int = 30) -> dict:
    """시장 지표 시계열 — sparkline/라인 차트용 (yfinance batch)."""
    tickers = {
        "kospi":  "^KS11",
        "sp500":  "^GSPC",
        "vix":    "^VIX",
        "usdkrw": "USDKRW=X",
        "wti":    "CL=F",
        "tnx":    "^TNX",
        "irx":    "^IRX",
        "dxy":    "DX-Y.NYB",
    }
    history = {}
    try:
        symbols = list(tickers.values())
        data = yf.download(symbols, period=f"{period_days}d", interval="1d",
                           progress=False, group_by="ticker", threads=True)
        for name, sym in tickers.items():
            try:
                if isinstance(data.columns, pd.MultiIndex):
                    if sym in data.columns.get_level_values(0):
                        series = data[sym]["Close"].dropna()
                    else:
                        continue
                else:
                    series = data["Close"].dropna()
                if series.empty:
                    continue
                pts = series[-period_days:]
                history[name] = {
                    "labels": [d.strftime("%m/%d") for d in pts.index],
                    "values": [round(float(v), 4) for v in pts.values],
                }
            except Exception:
                continue
    except Exception as e:
        print(f"  [history] fetch 실패: {e}")
    return history


def _fetch_holdings_sparklines(holdings: list, days: int = 7) -> dict:
    """가치주 보유 종목별 N일 종가 시계열. 반환: {code: {values, labels, change_pct}}.

    각 종목 KS/KQ 자동 시도. 가치주 ~10종목이라 batch 호출보다 개별 호출이 안정적.
    daily 08:00에 1회 호출 → dashboard_cache.json에 저장 → 30분마다 호출은 캐시 재사용.
    """
    out = {}
    if not holdings:
        return out

    for h in holdings:
        code = h.get("code", "")
        if not code:
            continue
        # KS 먼저 시도, 실패 시 KQ
        for suffix in (".KS", ".KQ"):
            try:
                ticker = yf.Ticker(f"{code}{suffix}")
                hist = ticker.history(period=f"{days+5}d", interval="1d")
                if hist.empty or "Close" not in hist.columns:
                    continue
                series = hist["Close"].dropna()
                if len(series) < 2:
                    continue
                pts = series[-days:]
                values = [round(float(v), 2) for v in pts.values]
                if not values:
                    continue
                change_pct = (values[-1] - values[0]) / values[0] * 100 if values[0] > 0 else 0
                out[code] = {
                    "values": values,
                    "labels": [d.strftime("%m/%d") for d in pts.index],
                    "change_pct": round(change_pct, 2),
                }
                break  # KS 성공 시 KQ 시도 X
            except Exception:
                continue
    return out


def _make_sparkline_svg(values: list, width: int = 70, height: int = 24,
                        change_pct: float = 0) -> str:
    """SVG sparkline — 7일 종가 추세선. 양수 녹색/음수 빨강/0 회색."""
    if not values or len(values) < 2:
        return '<span style="color:var(--text-3);font-size:11px;">—</span>'
    vmin, vmax = min(values), max(values)
    if vmax == vmin:
        return '<span style="color:var(--text-3);font-size:11px;">—</span>'
    n = len(values)
    points = []
    for i, v in enumerate(values):
        x = (i / (n - 1)) * (width - 2) + 1
        y = height - 2 - ((v - vmin) / (vmax - vmin)) * (height - 4)
        points.append(f"{x:.1f},{y:.1f}")
    polyline = " ".join(points)
    # 추세 색상 (시작값 vs 끝값)
    if change_pct > 0.5:
        color, fill = "#10b981", "rgba(16,185,129,0.12)"
    elif change_pct < -0.5:
        color, fill = "#ef4444", "rgba(239,68,68,0.12)"
    else:
        color, fill = "#94a3b8", "rgba(148,163,184,0.10)"
    # 영역 채우기 (마지막 점에서 바닥까지)
    last_x = points[-1].split(",")[0]
    first_x = points[0].split(",")[0]
    fill_path = f"M {first_x},{height} L " + " L ".join(points) + f" L {last_x},{height} Z"
    return (
        f'<svg width="{width}" height="{height}" '
        f'style="display:block;margin-left:auto;" viewBox="0 0 {width} {height}">'
        f'<path d="{fill_path}" fill="{fill}"/>'
        f'<polyline points="{polyline}" fill="none" stroke="{color}" stroke-width="1.5" '
        f'stroke-linecap="round" stroke-linejoin="round"/>'
        f'</svg>'
    )


def _load_market_history(max_age_hours: int = 1) -> dict:
    """캐시 우선 — 오래되면 재 fetch + 캐시 갱신."""
    try:
        if os.path.exists(_HISTORY_CACHE_PATH):
            age_h = (time.time() - os.path.getmtime(_HISTORY_CACHE_PATH)) / 3600
            if age_h < max_age_hours:
                with open(_HISTORY_CACHE_PATH, encoding="utf-8") as f:
                    return json.load(f)
    except Exception:
        pass
    history = _fetch_market_history()
    if history:
        try:
            with open(_HISTORY_CACHE_PATH, "w", encoding="utf-8") as f:
                json.dump(history, f, ensure_ascii=False)
        except Exception:
            pass
    return history


def _make_market_briefing_card(mood: dict, fg: dict, history: dict = None) -> str:
    """시장 브리핑 카드 — 코스피/S&P500/VIX/달러/유가/공포탐욕 + 7일 sparkline."""
    mood = mood or {}
    fg = fg or {"score": 50, "label": "중립"}
    history = history or {}
    if not mood.get("kospi_price") and not mood.get("vix"):
        return _empty_section("market", "🌏", "section__icon--market", "시장 브리핑",
                              "실시간 지표", "데이터 수집 중",
                              "다음 봇 실행 시 갱신됩니다.")

    def _card(label, value, chg=None, chg_label=None, spark_key=""):
        chg_html = ""
        if chg is not None:
            cls = "up" if chg >= 0 else ("down" if chg < 0 else "flat")
            arr = "▲" if chg >= 0 else "▼"
            chg_html = f'<div class="market-card__chg {cls}">{arr} {abs(chg):.2f}%</div>'
        elif chg_label:
            chg_html = f'<div class="market-card__chg flat">{chg_label}</div>'
        spark_html = ""
        if spark_key and history.get(spark_key, {}).get("values"):
            spark_html = f'<canvas class="market-card__chart spark-chart" data-key="{spark_key}" width="80" height="40"></canvas>'
        return (
            f'<div class="market-card">'
            f'  <div class="market-card__top">'
            f'    <div class="market-card__main">'
            f'      <div class="market-card__label">{label}</div>'
            f'      <div class="market-card__value">{value}</div>'
            f'      {chg_html}'
            f'    </div>'
            f'    {spark_html}'
            f'  </div>'
            f'</div>'
        )

    cards = [
        _card("코스피", f"{mood.get('kospi_price',0):,.2f}", mood.get("kospi_chg"), spark_key="kospi"),
        _card("S&P 500", "전일 대비", mood.get("sp500_chg"), spark_key="sp500"),
        _card("VIX", f"{mood.get('vix',0):.2f}", chg_label=mood.get("status",""), spark_key="vix"),
        _card("달러/원 (시장가)", f"{mood.get('usdkrw',0):,.2f}원", spark_key="usdkrw"),
        _card("WTI 유가", f"${mood.get('wti',0):.2f}", spark_key="wti"),
        _card("공포탐욕", f"{fg.get('score',50)}", chg_label=fg.get("label","중립")),
    ]

    return f"""
<section class="section" id="market" aria-label="시장 브리핑">
  <div class="section__head">
    <div class="section__title">
      <span class="section__icon section__icon--market">🌏</span>
      <h2>시장 브리핑</h2>
      <span class="section__badge">실시간 지표 · 7일 추세</span>
    </div>
  </div>
  <div class="market-grid">{"".join(cards)}</div>
</section>
"""


def _md_table_to_html(lines: list) -> str:
    """| ... | 마크다운 표 라인들을 <table> HTML로 변환 (자비스 카드용)."""
    if not lines:
        return ""
    rows = []
    for line in lines:
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if all(re.match(r'^-+:?\s*$', c) or c == "" for c in cells):
            continue  # 구분선 |---|---| 스킵
        # 굵게 **...** 처리
        cells = [re.sub(r'\*\*([^*]+)\*\*', r'<strong>\1</strong>', c) for c in cells]
        rows.append(cells)
    if not rows:
        return ""

    # 수익률/점수 셀 색상 자동 적용
    def _colorize(cell: str) -> str:
        # +X% 또는 -X% 패턴 → 색상
        m = re.search(r'([+-]?\d+(\.\d+)?)\s*%', cell)
        if m:
            v = float(m.group(1))
            if v > 0:
                color = "#10b981"  # green
            elif v < 0:
                color = "#ef4444"  # red
            else:
                color = "#94a3b8"  # gray
            return f'<span style="color:{color};font-weight:600;">{cell}</span>'
        return cell

    html = ['<div class="coach-table-wrap"><table class="coach-table">']
    html.append("<thead><tr>" + "".join(f"<th>{c}</th>" for c in rows[0]) + "</tr></thead>")
    if len(rows) > 1:
        html.append("<tbody>")
        for row in rows[1:]:
            html.append("<tr>" + "".join(f"<td>{_colorize(c)}</td>" for c in row) + "</tr>")
        html.append("</tbody>")
    html.append("</table></div>")
    return "\n".join(html)


def _md_to_html(text: str) -> str:
    """간단 마크다운 → HTML 변환 (자비스 AI 비서 카드 전용).

    지원: ##/###/#### 헤더, **굵게**, > 인용, 표 (|...|), - 리스트.
    """
    if not text:
        return ""

    # 1) 표 먼저 처리 (블록 단위)
    lines = text.split("\n")
    out_lines = []
    table_buf = []
    for line in lines:
        if line.strip().startswith("|"):
            table_buf.append(line)
        else:
            if table_buf:
                out_lines.append(_md_table_to_html(table_buf))
                table_buf = []
            out_lines.append(line)
    if table_buf:
        out_lines.append(_md_table_to_html(table_buf))

    # 2) 라인 단위 변환
    result = []
    in_list = False
    for line in out_lines:
        # 표 HTML은 그대로 통과
        if line.startswith("<div class=\"coach-table-wrap\""):
            if in_list:
                result.append("</ul>"); in_list = False
            result.append(line)
            continue

        stripped = line.strip()
        # 헤더
        if stripped.startswith("#### "):
            if in_list: result.append("</ul>"); in_list = False
            result.append(f'<h5 class="coach-h5">{stripped[5:]}</h5>')
            continue
        if stripped.startswith("### "):
            if in_list: result.append("</ul>"); in_list = False
            result.append(f'<h4 class="coach-h4">{stripped[4:]}</h4>')
            continue
        if stripped.startswith("## "):
            if in_list: result.append("</ul>"); in_list = False
            result.append(f'<h3 class="coach-h3">{stripped[3:]}</h3>')
            continue
        # 인용
        if stripped.startswith("> "):
            if in_list: result.append("</ul>"); in_list = False
            inner = re.sub(r'\*\*([^*]+)\*\*', r'<strong>\1</strong>', stripped[2:])
            result.append(f'<blockquote class="coach-quote">{inner}</blockquote>')
            continue
        # 리스트
        if stripped.startswith("- ") or stripped.startswith("* "):
            if not in_list:
                result.append('<ul class="coach-list">')
                in_list = True
            inner = re.sub(r'\*\*([^*]+)\*\*', r'<strong>\1</strong>', stripped[2:])
            result.append(f"<li>{inner}</li>")
            continue
        # 일반 라인
        if in_list and stripped == "":
            result.append("</ul>"); in_list = False
        # 굵게 처리
        line2 = re.sub(r'\*\*([^*]+)\*\*', r'<strong>\1</strong>', line)
        # 빈 줄은 단락 구분
        if not line2.strip():
            result.append("")
        else:
            result.append(f'<p class="coach-p">{line2}</p>')

    if in_list:
        result.append("</ul>")

    return "\n".join(result)


def _make_risk_gauge_html(risk: dict) -> str:
    """시장 위험 지수 게이지 (반원형 SVG) — 자비스 카드 헤더에 표시."""
    if not risk:
        return ""
    score = max(0, min(100, risk.get("score", 0)))
    level = risk.get("level", "안전")
    action = risk.get("action", "")
    # 등급별 색상
    color_map = {"안전": "#10b981", "주의": "#eab308", "경계": "#f97316", "위험": "#ef4444"}
    color = color_map.get(level, "#94a3b8")
    # 반원 게이지: 0~100 → 0~180도
    angle = score * 1.8  # 0~180
    # SVG arc — 반원 위 (0~100)
    cx, cy, r = 100, 100, 80
    end_x = cx + r * (1 - 2 * (angle / 180.0))  # 단순화: 직선 위치
    # 더 정확한 SVG arc
    import math
    rad = math.radians(180 - angle)
    end_x = cx - r * math.cos(rad)
    end_y = cy - r * math.sin(rad)
    large_arc = 1 if angle > 180 else 0
    return f"""
<div class="risk-gauge">
  <svg viewBox="0 0 200 120" width="200" height="120">
    <!-- 배경 반원 -->
    <path d="M 20 100 A 80 80 0 0 1 180 100" stroke="var(--surface-2)" stroke-width="14" fill="none" stroke-linecap="round"/>
    <!-- 위험도 호 -->
    <path d="M 20 100 A 80 80 0 {large_arc} 1 {end_x:.2f} {end_y:.2f}" stroke="{color}" stroke-width="14" fill="none" stroke-linecap="round"/>
    <!-- 중앙 점수 -->
    <text x="100" y="92" text-anchor="middle" font-size="32" font-weight="700" fill="{color}">{score}</text>
    <text x="100" y="110" text-anchor="middle" font-size="11" fill="var(--text-2)">/ 100</text>
  </svg>
  <div class="risk-gauge__label" style="color:{color};">{level}</div>
  <div class="risk-gauge__action">{action}</div>
</div>
"""


def _make_disclosures_card(disclosures: list) -> str:
    """📢 최근 공시 카드 — 보유 종목 + KR_STOCKS 24시간 새 공시.

    disclosures: [{name, code, title, rcept_dt, url, emoji, is_held}, ...]
    """
    if not disclosures:
        return _empty_section("disclosures", "📢", "section__icon--alert", "최근 공시",
                              "DART 24시간", "새 공시가 없습니다",
                              "보유 종목 + 관심종목 100개의 새 공시를 30분마다 자동 수집합니다.")

    held_count = sum(1 for d in disclosures if d.get("is_held"))
    bad_count  = sum(1 for d in disclosures if d.get("emoji") == "⚠️")
    good_count = sum(1 for d in disclosures if d.get("emoji") == "✅")

    rows = []
    for d in disclosures[:30]:  # 최대 30건
        em = d.get("emoji", "📰")
        name = d.get("name", "")
        title = d.get("title", "")
        url = d.get("url", "#")
        is_held = d.get("is_held", False)
        rcept_dt = d.get("rcept_dt", "")
        # 시간 표시 (YYYYMMDD → MM/DD)
        time_str = ""
        if len(rcept_dt) >= 8:
            time_str = f"{rcept_dt[4:6]}/{rcept_dt[6:8]}"
        held_badge = '<span class="disc-row__badge disc-row__badge--held">보유</span>' if is_held else ""
        # 키워드별 색상
        em_class = ""
        if em == "⚠️": em_class = "disc-row__emoji--bad"
        elif em == "✅": em_class = "disc-row__emoji--good"

        rows.append(f"""
    <a href="{url}" target="_blank" rel="noopener" class="disc-row {'disc-row--held' if is_held else ''}">
      <span class="disc-row__emoji {em_class}">{em}</span>
      <div class="disc-row__main">
        <div class="disc-row__head">
          <span class="disc-row__name">{name}</span>
          {held_badge}
        </div>
        <div class="disc-row__title">{title}</div>
      </div>
      <span class="disc-row__date">{time_str}</span>
    </a>""")

    more_html = ""
    if len(disclosures) > 30:
        more_html = f'<div class="disc-more">외 {len(disclosures) - 30}건 더 — DART 사이트에서 확인</div>'

    return f"""
<section class="section" id="disclosures" aria-label="최근 공시">
  <div class="section__head">
    <div class="section__title">
      <span class="section__icon section__icon--alert">📢</span>
      <h2>최근 공시</h2>
      <span class="section__badge">DART 24h</span>
      <span class="section__count">{len(disclosures)}건</span>
    </div>
    <div style="display:flex;gap:14px;margin-top:10px;font-size:12px;color:var(--text-3);flex-wrap:wrap;">
      <span>🏠 보유 종목 <b style="color:var(--text-1);">{held_count}</b></span>
      <span>⚠️ 주의 <b style="color:#ef4444;">{bad_count}</b></span>
      <span>✅ 호재성 <b style="color:#10b981;">{good_count}</b></span>
    </div>
  </div>
  <div class="disc-list">{"".join(rows)}{more_html}</div>
</section>
"""


def _make_portfolio_history_card(history: list) -> str:
    """자산 일별 추이 차트 (Chart.js 라인 차트). 가치주+자동매매 합계 + 손익.

    history: [{date, value_total, auto_total, total, total_pnl, value_pct}, ...]
    """
    if not history or len(history) < 2:
        return _empty_section("history", "📈", "section__icon--macro", "자산 추이",
                              "일별 시계열", "데이터 누적 중",
                              "매일 08:00 daily 가동마다 1건씩 누적. 7일 이상이면 차트 표시.")

    labels = [h.get("date", "")[5:] for h in history]  # MM-DD
    total_data = [h.get("total", 0) for h in history]
    value_data = [h.get("value_total", 0) for h in history]
    auto_data  = [h.get("auto_total", 0) for h in history]
    pnl_data   = [h.get("total_pnl", 0) for h in history]

    last = history[-1]
    first = history[0]
    period_change = last.get("total", 0) - first.get("total", 0)
    period_pct = (period_change / first.get("total", 1) * 100) if first.get("total", 0) > 0 else 0
    period_color = "#10b981" if period_change > 0 else ("#ef4444" if period_change < 0 else "#94a3b8")
    period_sign = "+" if period_change >= 0 else ""

    return f"""
<section class="section" id="history" aria-label="자산 추이">
  <div class="section__head">
    <div class="section__title">
      <span class="section__icon section__icon--macro">📈</span>
      <h2>자산 추이</h2>
      <span class="section__badge">최근 {len(history)}일</span>
      <span class="section__count" style="color:{period_color};font-weight:700;">
        {period_sign}{period_change:,}원 ({period_sign}{period_pct:.2f}%)
      </span>
    </div>
  </div>
  <div style="padding:18px 22px;">
    <div style="position:relative;height:280px;">
      <canvas id="portfolio-history-chart"></canvas>
    </div>
    <div style="display:flex;gap:18px;flex-wrap:wrap;margin-top:12px;font-size:12px;color:var(--text-2);">
      <div><span style="color:#7c3aed;">●</span> 총 자산</div>
      <div><span style="color:#10b981;">●</span> 가치주 (미래에셋)</div>
      <div><span style="color:#f59e0b;">●</span> 자동매매 (모의)</div>
      <div><span style="color:#3b82f6;">●</span> 누적 손익</div>
    </div>
  </div>
  <script>
  (function() {{
    const ctx = document.getElementById('portfolio-history-chart');
    if (!ctx || !window.Chart) return;
    new Chart(ctx, {{
      type: 'line',
      data: {{
        labels: {json.dumps(labels)},
        datasets: [
          {{ label: '총 자산', data: {json.dumps(total_data)},
             borderColor: '#7c3aed', backgroundColor: 'rgba(124,58,237,0.10)',
             borderWidth: 2.5, tension: 0.3, fill: true, yAxisID: 'y' }},
          {{ label: '가치주', data: {json.dumps(value_data)},
             borderColor: '#10b981', borderWidth: 1.5, tension: 0.3, fill: false, yAxisID: 'y' }},
          {{ label: '자동매매', data: {json.dumps(auto_data)},
             borderColor: '#f59e0b', borderWidth: 1.5, tension: 0.3, fill: false, yAxisID: 'y' }},
          {{ label: '누적 손익', data: {json.dumps(pnl_data)},
             borderColor: '#3b82f6', borderWidth: 1.5, borderDash: [4, 4],
             tension: 0.3, fill: false, yAxisID: 'y1' }}
        ]
      }},
      options: {{
        responsive: true, maintainAspectRatio: false,
        interaction: {{ mode: 'index', intersect: false }},
        plugins: {{
          legend: {{ display: false }},
          tooltip: {{
            callbacks: {{
              label: (ctx) => ctx.dataset.label + ': ' + Number(ctx.parsed.y).toLocaleString() + '원'
            }}
          }}
        }},
        scales: {{
          x: {{ grid: {{ display: false }}, ticks: {{ color: 'var(--text-3)', maxTicksLimit: 8 }} }},
          y: {{ position: 'left', grid: {{ color: 'rgba(148,163,184,0.15)' }},
                ticks: {{ color: 'var(--text-3)',
                  callback: (v) => (v / 10000).toFixed(0) + '만' }} }},
          y1: {{ position: 'right', grid: {{ display: false }},
                 ticks: {{ color: 'var(--text-3)',
                   callback: (v) => (v / 10000).toFixed(0) + '만' }} }}
        }}
      }}
    }});
  }})();
  </script>
</section>
"""


def _make_personal_coach_card(personal_brief: str, risk: dict = None) -> str:
    """🦾 AI 맞춤 비서 카드 — 마크다운→HTML 변환 + 위험 지수 게이지."""
    if not personal_brief:
        return _empty_section("coach", "🦾", "section__icon--ai", "AI 맞춤 비서",
                              "이제훈님 전용", "맞춤 코칭이 없습니다",
                              "08:00 daily 갱신 또는 텔레그램 /추천 명령으로 즉시 생성.")

    body_html = _md_to_html(personal_brief)
    gauge_html = _make_risk_gauge_html(risk) if risk else ""

    return f"""
<section class="section coach-section" id="coach" aria-label="AI 맞춤 비서">
  <div class="section__head coach-head">
    <div class="section__title">
      <span class="section__icon section__icon--ai">🦾</span>
      <h2>AI 맞춤 비서</h2>
      <span class="section__badge">이제훈님 전용</span>
    </div>
    {gauge_html}
  </div>
  <div class="coach-body">
    {body_html}
  </div>
</section>
"""


def _make_ai_card(ai_summary: str, ai_sector: str) -> str:
    """AI 시장 판단 카드 — 마크다운→HTML 변환 적용 (5/5 가독성 개선)."""
    if not ai_summary:
        return _empty_section("ai", "🤖", "section__icon--ai", "AI 시장 판단",
                              "Claude 분석", "AI 분석이 없습니다",
                              "08:50 장 시작 전 또는 02:00 시장 스캔 시 갱신됩니다.")
    summary_html = _md_to_html(ai_summary)
    sector_html = ""
    if ai_sector:
        sector_inner_html = _md_to_html(ai_sector)
        sector_html = (
            '<div style="margin:14px 22px 22px;padding:14px 18px;background:var(--surface-2);'
            'border-radius:10px;color:var(--text-1);border-left:3px solid var(--accent);">'
            '<div style="font-weight:700;color:var(--accent);margin-bottom:8px;font-size:13px;'
            'text-transform:uppercase;letter-spacing:0.5px;">섹터 로테이션</div>'
            f'{sector_inner_html}</div>'
        )
    return f"""
<section class="section" id="ai" aria-label="AI 시장 판단">
  <div class="section__head">
    <div class="section__title">
      <span class="section__icon section__icon--ai">🤖</span>
      <h2>AI 시장 판단</h2>
      <span class="section__badge">Claude</span>
    </div>
  </div>
  <div class="coach-body">{summary_html}</div>
  {sector_html}
</section>
"""


def _make_macro_card(macro: dict, ai_macro: str, history: dict = None) -> str:
    """매크로 지표 카드 — 기존 _make_macro_html + 30일 라인 차트 (TNX/IRX/DXY)."""
    if not macro:
        return _empty_section("macro", "📈", "section__icon--macro", "미국 경제지표",
                              "TNX/CPI/DXY", "매크로 데이터 수집 중",
                              "06:00 미국 마감 또는 08:50 장 시작 전 갱신됩니다.")
    inner = _make_macro_html(macro, ai_macro)
    history = history or {}
    chart_html = ""
    has_chart = any(history.get(k, {}).get("values") for k in ("tnx", "irx", "dxy"))
    if has_chart:
        chart_html = (
            '<div class="macro-legend">'
            '  <span class="macro-legend__item"><span class="macro-legend__dot" style="background:#5f6dff"></span>10년물 금리 (TNX)</span>'
            '  <span class="macro-legend__item"><span class="macro-legend__dot" style="background:#14b8a6"></span>단기 금리 (IRX)</span>'
            '  <span class="macro-legend__item"><span class="macro-legend__dot" style="background:#f59e0b"></span>달러인덱스 (DXY)</span>'
            '</div>'
            '<div class="macro-chart-wrap">'
            '  <canvas id="macro-chart" class="macro-chart"></canvas>'
            '</div>'
        )
    return f"""
<section class="section" id="macro" aria-label="미국 경제지표">
  <div class="section__head">
    <div class="section__title">
      <span class="section__icon section__icon--macro">📈</span>
      <h2>미국 경제지표</h2>
      <span class="section__badge">FRED · Yahoo · 30일 추이</span>
    </div>
  </div>
  <div class="embed-wrap">{inner}</div>
  {chart_html}
</section>
"""


def _make_recommend_card(kr_top: list, ai_insights: dict) -> str:
    """가치주 추천 TOP 5 카드 — 기존 card_html 활용."""
    if not kr_top:
        return _empty_section("recommend", "🇰🇷", "section__icon--rec", "가치주 추천 TOP 5",
                              "오늘의 매수 후보", "추천 데이터 준비 중",
                              "평일 02:00 시장 스캔 + 08:50 장 시작 전 분석 후 표시됩니다.")
    cards = "".join(card_html(i, s, (ai_insights or {}).get(s.get('ticker',''), "")) for i, s in enumerate(kr_top))
    return f"""
<section class="section" id="recommend" aria-label="가치주 추천 TOP 5">
  <div class="section__head">
    <div class="section__title">
      <span class="section__icon section__icon--rec">🇰🇷</span>
      <h2>가치주 추천 TOP 5</h2>
      <span class="section__badge">오늘의 매수 후보</span>
    </div>
  </div>
  <div class="embed-wrap">{cards}</div>
</section>
"""


def _make_avoid_card(avoid: list) -> str:
    """오늘 피해야 할 종목 카드."""
    if not avoid:
        return ""
    items = "".join(
        f'<div class="row"><div class="row__main">'
        f'<div class="row__name">{a["name"]} <span style="color:var(--text-3);font-weight:400;font-size:12px;">({a.get("ticker","")})</span></div>'
        f'<div class="row__sub">RSI {a.get("rsi","-")} · 1달 {a.get("ret_1m",0):+.1f}% · 거래량 {a.get("vol_ratio",0):.0f}%</div>'
        f'</div></div>'
        for a in avoid[:5]
    )
    return f"""
<section class="section" id="avoid" aria-label="피해야 할 종목">
  <div class="section__head">
    <div class="section__title">
      <span class="section__icon section__icon--alert">🚫</span>
      <h2>오늘 피해야 할 종목</h2>
      <span class="section__badge">관망 권장</span>
      <span class="section__count">{len(avoid[:5])}종목</span>
    </div>
  </div>
  <div class="section__body">{items}</div>
</section>
"""


def _make_dart_card(dart_alerts: list) -> str:
    """DART 공시 알림 카드 — 기존 dart_alerts_section_html 활용."""
    if not dart_alerts:
        return ""
    inner = dart_alerts_section_html(dart_alerts)
    return f"""
<section class="section" id="dart" aria-label="DART 공시 알림">
  <div class="section__head">
    <div class="section__title">
      <span class="section__icon section__icon--alert">📢</span>
      <h2>DART 공시 알림</h2>
      <span class="section__badge">금융감독원</span>
      <span class="section__count">{len(dart_alerts)}건</span>
    </div>
  </div>
  <div class="embed-wrap">{inner}</div>
</section>
"""


def _make_sidebar(sections_status: dict, last_update: str) -> str:
    """좌측 사이드바 — 섹션 네비게이션 (활성/비활성 표시)."""
    items = [
        ("overview", "🏠", "개요", True),
        ("coach", "🦾", "AI 비서", sections_status.get("coach", False)),
        ("history", "📈", "자산 추이", sections_status.get("history", False)),
        ("value", "💼", "가치주", sections_status.get("value", False)),
        ("auto", "🤖", "자동매매", sections_status.get("auto", False)),
        ("allocation", "📊", "자산 배분", sections_status.get("allocation", False)),
        ("market", "🌏", "시장", sections_status.get("market", False)),
        ("macro", "📈", "매크로", sections_status.get("macro", False)),
        ("ai", "🤖", "AI 분석", sections_status.get("ai", False)),
        ("recommend", "🇰🇷", "추천", sections_status.get("recommend", False)),
        ("avoid", "🚫", "회피", sections_status.get("avoid", False)),
        ("disclosures", "📢", "공시", sections_status.get("disclosures", False)),
        ("dart", "🚨", "보유 공시", sections_status.get("dart", False)),
    ]
    badge_html = '<span class="sidebar__link-badge">대기</span>'
    links = []
    for sid, icon, label, active in items:
        cls = "sidebar__link"
        attr = f'data-target="{sid}" href="#{sid}"'
        if not active:
            cls += " is-disabled"
            attr = ''  # 클릭 비활성
        suffix = "" if active else badge_html
        links.append(
            f'<a class="{cls}" {attr}>'
            f'<span class="sidebar__link-icon">{icon}</span>'
            f'<span class="sidebar__link-label">{label}</span>'
            f'{suffix}'
            f'</a>'
        )

    return f"""
<aside class="sidebar">
  <div class="sidebar__brand">
    <span class="sidebar__brand-icon">📊</span>
    <span>투자 비서</span>
  </div>
  <nav class="sidebar__nav">
    <div class="sidebar__section-title">대시보드</div>
    {"".join(links)}
  </nav>
  <div class="sidebar__footer">
    <span>마지막 갱신</span>
    <span style="color:rgba(255,255,255,0.7);font-weight:600;">{last_update}</span>
  </div>
</aside>
<div class="sidebar-backdrop"></div>
"""


def _record_portfolio_value(value_total: float, value_cost: float,
                             auto_total: float, auto_cost: float) -> None:
    """오늘 자산 스냅샷을 portfolio_history.json에 누적 저장 (daily 08:00 1회).

    같은 날짜 호출은 덮어쓰기. 자산 일별 추세 차트의 데이터 원천.
    """
    try:
        today = _today_str()
        history = []
        if os.path.exists(PORTFOLIO_HISTORY):
            with open(PORTFOLIO_HISTORY, "r", encoding="utf-8") as f:
                history = json.load(f).get("history", [])
        # 같은 날짜 기존 항목 제거
        history = [h for h in history if h.get("date") != today]
        v_pnl = value_total - value_cost
        a_pnl = auto_total - auto_cost
        history.append({
            "date": today,
            "value_total": round(value_total),
            "value_cost":  round(value_cost),
            "value_pnl":   round(v_pnl),
            "value_pct":   round((v_pnl / value_cost * 100) if value_cost > 0 else 0, 2),
            "auto_total":  round(auto_total),
            "auto_cost":   round(auto_cost),
            "auto_pnl":    round(a_pnl),
            "total":       round(value_total + auto_total),
            "total_pnl":   round(v_pnl + a_pnl),
        })
        # 날짜순 정렬 + 최근 730일(2년)만 유지
        history.sort(key=lambda h: h.get("date", ""))
        if len(history) > 730:
            history = history[-730:]
        with open(PORTFOLIO_HISTORY, "w", encoding="utf-8") as f:
            json.dump({"history": history, "updated": _now_kst().strftime("%Y-%m-%d %H:%M:%S")},
                      f, ensure_ascii=False, indent=2)
        print(f"  [portfolio_history] {today} 스냅샷 저장 (누적 {len(history)}건)")
    except Exception as e:
        print(f"  [portfolio_history] 저장 실패: {e}")


def _load_portfolio_history(days: int = 90) -> list:
    """최근 N일 자산 추이 로드. 차트 데이터용."""
    try:
        if not os.path.exists(PORTFOLIO_HISTORY):
            return []
        with open(PORTFOLIO_HISTORY, "r", encoding="utf-8") as f:
            data = json.load(f)
        history = data.get("history", [])
        return history[-days:] if len(history) > days else history
    except Exception:
        return []


def _save_dashboard_cache(payload: dict) -> None:
    """daily(08:00) / marketscan(16:00) 후 풀 데이터(macro/AI/추천)를 캐시 파일로 저장.

    이후 호출(autobuy/autosell/premarket/close 등)에서 빈 인자로 build_and_save_dashboard()를
    호출해도 _load_dashboard_cache()가 None인 인자만 채워서 대시보드가 풀로 유지됨.
    """
    try:
        payload = dict(payload)
        payload["updated"] = _now_kst().strftime("%Y-%m-%d %H:%M:%S")
        with open(DASHBOARD_CACHE, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2, default=str)
    except Exception as e:
        print(f"  [dashboard_cache] 저장 실패: {e}")


def _load_dashboard_cache() -> dict:
    """dashboard_cache.json 로드 (3영업일 신선도 체크). 없으면 빈 dict."""
    try:
        if not os.path.exists(DASHBOARD_CACHE):
            return {}
        with open(DASHBOARD_CACHE, "r", encoding="utf-8") as f:
            cache = json.load(f)
        # 신선도: 3영업일 이내 데이터만 유효 (휴장/주말 고려)
        updated_str = cache.get("updated", "")
        try:
            updated_dt = datetime.strptime(updated_str.split()[0], "%Y-%m-%d").replace(
                tzinfo=ZoneInfo("Asia/Seoul")
            )
            age_days = (_now_kst() - updated_dt).days
            if age_days > 3:
                print(f"  [dashboard_cache] 오래됨({age_days}일) — 무시")
                return {}
        except Exception:
            pass
        return cache
    except Exception as e:
        print(f"  [dashboard_cache] 로드 실패: {e}")
        return {}


def build_and_save_dashboard(
    mood: dict = None,
    fg: dict = None,
    kr_top: list = None,
    us_top: list = None,
    avoid: list = None,
    dart_alerts: list = None,
    ai_summary: str = "",
    ai_sector: str = "",
    ai_insights: dict = None,
    macro: dict = None,
    ai_macro: str = "",
    holdings_alerts: list = None,
    personal_brief: str = "",
    risk: dict = None,
    holdings_sparklines: dict = None,
    portfolio_history: list = None,
    disclosures: list = None,
) -> str:
    """대시보드 HTML 생성 + docs/index.html 저장.

    풀 대시보드 디자인: 좌측 사이드바 (섹션 네비) + Hero 헤더 (KPI) + 카드 그리드.
    부분 데이터로 호출 가능 (누락된 건 dashboard_cache.json 또는 market_scan_cache에서 보충).
    GitHub Pages가 docs/index.html을 자동 노출 — staticrypt로 비밀번호 보호.
    """
    try:
        # ── dashboard_cache 보충 (autobuy 14번 빈 호출 등이 풀 dashboard 덮어쓰지 않게) ─
        _dc = _load_dashboard_cache()
        if macro is None:       macro       = _dc.get("macro")
        if not ai_summary:      ai_summary  = _dc.get("ai_summary", "") or ""
        if not ai_sector:       ai_sector   = _dc.get("ai_sector", "") or ""
        if not ai_macro:        ai_macro    = _dc.get("ai_macro", "") or ""
        if not personal_brief:  personal_brief = _dc.get("personal_brief", "") or ""
        if risk is None:        risk        = _dc.get("risk")
        if holdings_sparklines is None: holdings_sparklines = _dc.get("holdings_sparklines")
        if disclosures is None:         disclosures = _dc.get("disclosures") or []
        if portfolio_history is None:
            try:
                portfolio_history = _load_portfolio_history(days=90)
            except Exception:
                portfolio_history = []
        if ai_insights is None: ai_insights = _dc.get("ai_insights")
        if avoid is None:       avoid       = _dc.get("avoid")
        if dart_alerts is None: dart_alerts = _dc.get("dart_alerts")
        if not us_top:          us_top      = _dc.get("us_top") or []
        if not kr_top:          kr_top      = _dc.get("kr_top") or []

        # ── 데이터 누락 보충 (kr_top은 캐시에 없으면 market_scan_cache에서 직접 로드) ─
        if not kr_top:
            kr_top = _load_value_top5() or []
        if mood is None:
            try:
                mood = get_market_mood()
            except Exception:
                mood = {}
        if fg is None:
            try:
                fg = get_fear_greed(mood) if mood else {"score": 50, "label": "중립"}
            except Exception:
                fg = {"score": 50, "label": "중립"}
        if holdings_alerts is None:
            try:
                holdings_alerts = check_holdings_alerts()
            except Exception:
                holdings_alerts = []
        auto_positions = []
        try:
            pos = load_positions()
            for code, p in pos.get("positions", {}).items():
                auto_positions.append({
                    "code": code, "name": p.get("name", code),
                    "qty": p.get("qty", 0), "buy_price": p.get("buy_price", 0),
                    "buy_date": p.get("buy_date", ""), "score": p.get("score", 0),
                    "sector": p.get("sector", ""),
                    "partial_sold": p.get("partial_sold", False),
                })
        except Exception:
            pass

        # ── 합계 계산 (Hero 헤더용) ─────────────
        v_value = sum(h.get("value", 0) for h in (holdings_alerts or []))
        v_cost  = sum(h.get("cost", 0) for h in (holdings_alerts or []))
        a_value, a_cost = 0.0, 0.0
        for p in auto_positions:
            bp = p.get("buy_price", 0); qty = p.get("qty", 0)
            cp = p.get("curr_price", 0)
            if cp <= 0:
                try:
                    if _kis.available():
                        pi = _kis.get_price(p.get("code", ""))
                        cp = _safe_float(pi.get("stck_prpr")) if pi else 0
                except Exception:
                    cp = 0
                p["curr_price"] = cp  # 캐시
            a_value += cp * qty
            a_cost  += bp * qty
        total_value = v_value + a_value
        total_cost  = v_cost + a_cost
        total_pnl   = total_value - total_cost
        total_pct   = (total_pnl / total_cost * 100) if total_cost > 0 else 0

        # ── 섹션별 데이터 존재 여부 (사이드바 활성 표시) ─────
        sections_status = {
            "value": bool(holdings_alerts),
            "auto": bool(auto_positions),
            "allocation": (v_value > 0 or a_value > 0),
            "market": bool(mood and (mood.get("kospi_price") or mood.get("vix"))),
            "macro": bool(macro),
            "ai": bool(ai_summary),
            "coach": bool(personal_brief),
            "history": bool(portfolio_history and len(portfolio_history) >= 2),
            "disclosures": bool(disclosures),
            "recommend": bool(kr_top),
            "avoid": bool(avoid),
            "dart": bool(dart_alerts),
        }

        # ── 날짜/시간 ─────
        today = _now_kst().strftime("%Y년 %m월 %d일 (%a)")
        time_str = _now_kst().strftime("%H:%M")
        last_update = _now_kst().strftime("%m/%d %H:%M")

        # ── 시계열 데이터 (캐시 우선) ─────
        try:
            history = _load_market_history(max_age_hours=1)
        except Exception:
            history = {}

        # ── 섹션 HTML 생성 ─────
        hero_html = _make_hero_header(today, time_str, mood, fg, total_value, total_pnl, total_pct)
        value_html = _make_value_holdings_section(holdings_alerts or [], holdings_sparklines or {})
        auto_html = _make_auto_positions_section(auto_positions)
        allocation_html = _make_allocation_card(holdings_alerts or [], auto_positions)
        market_html = _make_market_briefing_card(mood, fg, history)
        macro_html = _make_macro_card(macro, ai_macro, history)
        ai_html = _make_ai_card(ai_summary, ai_sector)
        coach_html = _make_personal_coach_card(personal_brief, risk)
        history_html = _make_portfolio_history_card(portfolio_history or [])
        disclosures_html = _make_disclosures_card(disclosures or [])
        recommend_html = _make_recommend_card(kr_top, ai_insights)
        avoid_html = _make_avoid_card(avoid or [])
        dart_html = _make_dart_card(dart_alerts or [])
        sidebar_html = _make_sidebar(sections_status, last_update)

        # ── 차트 데이터 JSON inject ─────
        try:
            history_json = json.dumps(history, ensure_ascii=False)
        except Exception:
            history_json = "{}"

        # ── 풀 HTML 조립 ─────
        html = f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="refresh" content="300">
<meta name="color-scheme" content="light dark">
<meta name="theme-color" content="#5f6dff">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="apple-mobile-web-app-title" content="투자 비서">
<meta name="robots" content="noindex, nofollow">
<link rel="manifest" href="manifest.json">
<link rel="icon" type="image/svg+xml" href="icon.svg">
<link rel="apple-touch-icon" href="icon-192.png">
<title>투자 비서 — 대시보드</title>
<script>window._chartData = {history_json};</script>
{_dashboard_css()}
</head>
<body>
<div class="toolbar">
  <button class="icon-btn hamburger" aria-label="메뉴">☰</button>
  <button class="icon-btn theme-toggle" aria-label="테마 전환">☾</button>
</div>
<div class="app">
{sidebar_html}
<main class="main">
{hero_html}
{coach_html}
{history_html}
{allocation_html}
{value_html}
{auto_html}
{market_html}
{macro_html}
{ai_html}
{recommend_html}
{avoid_html}
{disclosures_html}
{dart_html}
<div class="footer">
  ⚠️ 본 대시보드는 자동 분석된 참고 정보입니다. 최종 투자 판단은 본인이 직접 하세요.<br>
  투자 손익의 책임은 전적으로 투자자 본인에게 있으며 어떤 수익도 보장하지 않습니다.<br>
  5분마다 자동 새로고침
</div>
</main>
</div>

<!-- 차트 상세 모달 -->
<div class="modal-overlay" id="chart-modal" role="dialog" aria-modal="true" aria-labelledby="modal-title">
  <div class="modal">
    <div class="modal__head">
      <div class="modal__title">
        <h3 id="modal-title">차트</h3>
        <span class="modal__title-badge" id="modal-period">30일 추이</span>
      </div>
      <button class="modal__close" id="modal-close" aria-label="닫기">×</button>
    </div>
    <div class="modal__body">
      <div class="modal__chart-wrap">
        <canvas id="modal-chart" class="modal__chart"></canvas>
      </div>
      <div class="modal__stats" id="modal-stats"></div>
      <div class="modal__external" id="modal-external"></div>
    </div>
  </div>
</div>

</body>
</html>"""

        # 저장
        os.makedirs(os.path.dirname(DASHBOARD_HTML_PATH), exist_ok=True)
        with open(DASHBOARD_HTML_PATH, "w", encoding="utf-8") as f:
            f.write(html)
        print(f"  [대시보드] 갱신 완료 → {DASHBOARD_HTML_PATH}")
        return html
    except Exception as e:
        print(f"  [대시보드] 생성 오류: {e}")
        import traceback
        traceback.print_exc()
        return ""


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
    today     = _now_kst().strftime("%Y년 %m월 %d일 (%A)")
    now       = _now_kst().strftime("%H:%M")
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

    # 해외 추천 섹션 — KR 전용 모드(또는 us_top 비어있음)면 출력 안 함
    us_section = ""
    if us_top:
        us_section = f"""
  <div style="padding:20px 16px 8px;">
    <h2 style="color:#1a3a5c;font-size:20px;margin:0 0 16px;padding-bottom:10px;border-bottom:3px solid #e67700;">
      🇺🇸 해외 추천 종목 TOP 5
    </h2>
    {"".join(card_html(i, s, ai_insights.get(s['ticker'], "")) for i, s in enumerate(us_top))}
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
        <div style="font-size:16px;font-weight:700;">{mood['kospi_price']:,.2f}</div>
        <div style="font-size:13px;color:{kos_col};">{'▲' if mood['kospi_chg']>=0 else '▼'} {abs(mood['kospi_chg']):.2f}%</div>
      </div>
      <div style="background:white;padding:12px;border-radius:10px;border:1px solid #dee2e6;text-align:center;">
        <div style="font-size:12px;color:#868e96;">미국 S&P500</div>
        <div style="font-size:16px;font-weight:700;">{'▲' if mood['sp500_chg']>=0 else '▼'} {abs(mood['sp500_chg']):.2f}%</div>
        <div style="font-size:13px;color:{sp500_col};">전일 대비</div>
      </div>
      <div style="background:white;padding:12px;border-radius:10px;border:1px solid #dee2e6;text-align:center;">
        <div style="font-size:12px;color:#868e96;">공포지수 VIX</div>
        <div style="font-size:16px;font-weight:700;">{mood['vix']:.2f}</div>
        <div style="font-size:13px;color:{mood_color};">{mood['status']}</div>
      </div>
      <div style="background:white;padding:12px;border-radius:10px;border:1px solid #dee2e6;text-align:center;">
        <div style="font-size:12px;color:#868e96;">달러/원 환율</div>
        <div style="font-size:16px;font-weight:700;">{mood['usdkrw']:,.2f}원</div>
      </div>
      <div style="background:white;padding:12px;border-radius:10px;border:1px solid #dee2e6;text-align:center;">
        <div style="font-size:12px;color:#868e96;">WTI 유가</div>
        <div style="font-size:16px;font-weight:700;">${mood['wti']:.2f}</div>
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
  {us_section}
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
    today  = _now_kst().strftime("%Y년 %m월 %d일")
    now    = _now_kst().strftime("%H:%M")
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
        f"코스피 {mood['kospi_price']:,.2f} {kos_arr}{abs(mood['kospi_chg']):.2f}%  |  S&P500 {sp5_arr}{abs(mood['sp500_chg']):.2f}%",
        f"VIX {mood['vix']:.2f} ({mood['status']})  |  달러/원 {mood['usdkrw']:,.2f}원",
        f"WTI ${mood['wti']:.2f}  |  공포탐욕 {fg['score']} ({fg['label']})",
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
                    f"   외국인 {'▲' if f_eok>=0 else '▼'}{abs(f_eok):.2f}억"
                    f"  기관 {'▲' if i_eok>=0 else '▼'}{abs(i_eok):.2f}억"
                )
            else:
                lines.append("   외국인/기관 수급 조회불가")

    if us_top:
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


_HTML_TAG_RE = re.compile(r"<(/?)([a-zA-Z][a-zA-Z0-9]*)([^>]*)>")


def _balance_html_tags(chunk: str) -> str:
    """미닫힌/미열린 HTML 태그를 자동 보정해 텔레그램 400 오류 방지."""
    open_stack = []
    for m in _HTML_TAG_RE.finditer(chunk):
        is_close, tag = m.group(1), m.group(2).lower()
        if tag in ("br", "hr", "img"):
            continue
        if is_close:
            if open_stack and open_stack[-1] == tag:
                open_stack.pop()
        else:
            open_stack.append(tag)
    # 미닫힌 태그를 chunk 끝에 닫아줌 (역순)
    for tag in reversed(open_stack):
        chunk += f"</{tag}>"
    return chunk


def tg_send(text: str, chat_id: str = ""):
    cid = chat_id or TELEGRAM_CHAT_ID
    if not TELEGRAM_TOKEN or not cid:
        return
    MAX = 3800  # HTML 태그 보정 여유분 확보 (4096 한도 미만)
    buf = text
    while buf:
        if len(buf) <= MAX:
            chunk, buf = buf, ""
        else:
            # 분할 우선순위: 빈 줄 > 줄바꿈 > 띄어쓰기 > 강제 컷
            cut = buf.rfind("\n\n", 0, MAX)
            if cut == -1:
                cut = buf.rfind("\n", 0, MAX)
            if cut == -1:
                cut = buf.rfind(" ", 0, MAX)
            if cut == -1:
                cut = MAX
            chunk, buf = buf[:cut], buf[cut:].lstrip()
        # HTML 태그 균형 보정 (분할 지점에서 깨진 태그 자동 닫기)
        chunk = _balance_html_tags(chunk)
        try:
            r = requests.post(
                f"{_tg_base()}/sendMessage",
                json={"chat_id": cid, "text": chunk, "parse_mode": "HTML"},
                timeout=15,
            )
            if not r.ok:
                # HTML 파싱 실패 시 plain text로 재시도 (잘림 방지)
                print(f"  [텔레그램] HTML 전송 실패 ({r.status_code}) — plain text 재시도")
                plain = re.sub(r"<[^>]+>", "", chunk)
                requests.post(
                    f"{_tg_base()}/sendMessage",
                    json={"chat_id": cid, "text": plain},
                    timeout=15,
                )
        except Exception as e:
            print(f"  [텔레그램] 전송 오류: {e}")


def tg_send_document(html: str, caption: str = ""):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return
    stamp = _now_kst().strftime("%m%d_%H%M")
    try:
        r = requests.post(
            f"{_tg_base()}/sendDocument",
            data={
                "chat_id": TELEGRAM_CHAT_ID,
                "caption": caption or f"📊 투자 비서 리포트 ({_now_kst().strftime('%m/%d %H:%M')})",
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
                    "/보유 — 보유종목 현황 (수동 등록)\n"
                    "/잔고 — 모의 계좌 잔고/손익 (자동매매)\n"
                    "/정지 — 자동매매 즉시 OFF\n"
                    "/재개 — 자동매매 다시 ON\n"
                    "/취소 — 매수 사전 알림 30초 동안만 유효\n"
                    "/도움말 — 이 메시지\n\n"
                    "<b>자연어 질의 예시:</b>\n"
                    "현대로템 어때?\n"
                    "삼성중공업 사도 될까?\n"
                    "오늘 방산주 전망은?\n"
                    "지금 시장 어때?",
                    chat_id,
                )

            elif text == "/잔고":
                tg_send(run_balance_report(), chat_id)

            elif text in ("/정지", "/halt", "/stop"):
                pos = load_positions()
                pos["halted"] = True
                save_positions(pos)
                tg_send("🛑 <b>자동매매 정지됨.</b>\n신규 매수/매도 모두 차단됩니다.\n해제: /재개", chat_id)

            elif text in ("/재개", "/resume"):
                pos = load_positions()
                pos["halted"] = False
                save_positions(pos)
                tg_send("▶️ <b>자동매매 재개됨.</b>\n다음 트리거부터 정상 동작합니다.", chat_id)

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
    """KR_STOCKS 전체 스캔 → 감지된 신호 목록 반환.

    가치주 트랙 다이어트 (2026-05-01) 이후:
      - 단기 스윙 신호(급등/외국인+기관 매수/외국인 대량 매도) 삭제
      - 보유 종목 단기 손절/익절 신호 삭제 (스윙 룰이라 가치주 모순)
      - 자동매매 스윙 보유 종목은 run_auto_sell()이 처리
      - 가치주 적합한 알림은 §C에서 별도 재설계 예정
    유지: 4번 눌림목 (고점수 우량주 -3% 하락 = 가치주 추매 기회).
    """
    signals = []
    now_str = _now_kst().strftime("%Y%m%d%H")

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

            prev = prev_scores.get(ticker, {})
            prev_score = prev.get("score", 0)

            # 4. 눌림목 감지 — 고점수 종목 -3% 이상 하락 (가치주 추매 기회)
            key4 = _alert_key("pullback", ticker, now_str)
            if prev_score >= 70 and change <= -3.0 and key4 not in _sent_alerts:
                _sent_alerts.add(key4)
                signals.append({
                    "type": "pullback",
                    "msg": (
                        f"💎 <b>[눌림목 매수 기회]</b> {name} ({sector})\n"
                        f"종합점수 {prev_score}점 우량주 / 오늘 {change:.2f}% 하락\n"
                        f"현재가: {price:,.0f}원\n"
                        f"💡 고점수 우량주 일시 조정 — 분할매수 검토"
                    ),
                })

        except Exception as e:
            print(f"  [모니터] {name} 조회 오류: {e}")
        time.sleep(0.3)

    return signals


def _is_market_open(now: datetime) -> bool:
    """한국 주식시장 정규 개장 시간(09:00~15:30 KST) 여부."""
    hm = now.hour * 100 + now.minute
    return 900 <= hm <= 1530


def _is_after_market_close(now: datetime) -> bool:
    """장 마감(15:35 이후) 여부 — 모니터 조기 종료 판정용."""
    return now.hour > 15 or (now.hour == 15 and now.minute >= 35)


# 한국 거래소(KRX) 휴장일. 주말 외 추가 휴장일만 등록.
# ⚠️ 매년 12월 KRX 발표분으로 갱신 필요 (krx.co.kr 휴장일 안내).
# 6/6 현충일(토), 8/15 광복절(토), 10/3 개천절(토)은 자연 토요일 휴장이라 등록 X.
_KRX_HOLIDAYS = {
    # 2026 (제10회 지방선거 6/3 포함)
    "2026-01-01",  # 신정 (목)
    "2026-02-16", "2026-02-17", "2026-02-18",  # 설날 연휴 (월화수)
    "2026-03-02",  # 삼일절 대체휴일 (3/1 일요일)
    "2026-05-01",  # 근로자의 날 (금)
    "2026-05-05",  # 어린이날 (화)
    "2026-05-25",  # 부처님오신날 대체 (5/24 일요일)
    "2026-06-03",  # 제10회 전국동시지방선거 (수)
    "2026-09-24", "2026-09-25",  # 추석 연휴 (목금) — 9/26 토, 9/27 일
    "2026-10-09",  # 한글날 (금)
    "2026-12-25",  # 크리스마스 (금)
    "2026-12-31",  # 연말 종가일 휴장 (목)
}


def _is_trading_day(d: datetime) -> bool:
    """한국 주식시장 영업일 여부 (주말 + KRX 휴장일 제외)."""
    if d.weekday() >= 5:  # 토(5), 일(6)
        return False
    return d.strftime("%Y-%m-%d") not in _KRX_HOLIDAYS


def _skip_if_holiday(mode_label: str) -> bool:
    """한국 휴장일이면 콘솔 로그만 남기고 True 반환.

    호출자는 True가 반환되면 즉시 return 해야 함 (분석/리포트 스킵).
    텔레그램 알림은 보내지 않음 — 사용자가 휴장일 메시지를 원치 않음.
    매매(autobuy/autosell)는 별도로 텔레그램 알림 (안전 확인용).
    """
    now = _now_kst()
    if _is_trading_day(now):
        return False
    print(f"[{mode_label}] 한국 휴장일 — 스킵 ({now.strftime('%Y-%m-%d %a')})")
    return True


def run_monitor(duration_hours: float = 7.0, interval_sec: int = 300):
    """장중 실시간 모니터링 루프 (기본: 7시간, 5분 간격)"""
    if _skip_if_holiday("모니터링"):
        return
    now = _now_kst()
    # 장 마감 후 시작은 무의미 — 즉시 스킵
    if _is_after_market_close(now):
        msg = (
            f"⏰ <b>[모니터링 스킵]</b>\n"
            f"현재 {now.strftime('%H:%M')} KST — 한국 장 마감 후라 모니터링 의미 없음.\n"
            f"<i>※ GitHub Actions cron 지연으로 추정. 다음 영업일 09:05 정상 실행 예정.</i>"
        )
        try:
            tg_send(msg)
        except Exception:
            pass
        print(f"[모니터] 장 마감 후 실행 — 스킵 ({now.strftime('%H:%M')})")
        return

    print(f"[모니터] 실시간 모니터링 시작 — {duration_hours}시간, {interval_sec}초 간격")
    # 텔레그램 다이어트: 시작 알림 제거. 신호 감지 시에만 알림.

    deadline    = time.time() + duration_hours * 3600
    prev_scores = {}   # {ticker: {score, price}}

    # 초기 점수 캐시 로드 (일일 리포트 결과가 있으면 활용)
    perf  = load_performance()
    today = _now_kst().strftime("%Y-%m-%d")
    for rec in perf.get("recommendations", []):
        if rec.get("date") == today:
            prev_scores[rec["ticker"]] = {"score": rec.get("score", 0)}

    cycle = 0
    while time.time() < deadline:
        cycle += 1
        now = _now_kst()
        # 장 마감 도달 시 즉시 종료 (의미 없는 대기 방지)
        if _is_after_market_close(now):
            print(f"  [모니터] 장 마감 — 조기 종료 ({now.strftime('%H:%M')})")
            break
        # 장 시작 전 대기
        if not _is_market_open(now):
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
        vix_val = round(float(vix.get("regularMarketPrice") or 20), 2)
        fx_val  = round(float(usdkrw.get("regularMarketPrice") or 1300), 2)
        gold_v  = round(float(gold.get("regularMarketPrice") or 2000), 2)
        wti_v   = round(float(wti.get("regularMarketPrice") or 75), 2)

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

        # 텔레그램 다이어트: 핵심 지표 3줄만. AI/상세는 대시보드.
        lines = [
            f"<b>🌙 미국 마감</b> {_now_kst().strftime('%m/%d')}",
            f"S&P {arr(sp_chg)}{abs(sp_chg):.2f}% / 나스닥 {arr(nq_chg)}{abs(nq_chg):.2f}% / "
            f"VIX {vix_val:.2f} / 환율 {fx_val:,.2f}원",
            mood_txt,
            f"📊 {DASHBOARD_URL}",
        ]
        tg_send("\n".join(lines))

        # 대시보드 갱신 (미국 데이터 반영)
        try:
            build_and_save_dashboard()
        except Exception as e:
            print(f"  [브리핑] 대시보드 갱신 오류: {e}")
    except Exception as e:
        print(f"  [브리핑] 미국 브리핑 오류: {e}")
        tg_send(f"⚠️ 미국 시장 브리핑 수집 실패: {e}")


def _load_value_top5() -> list:
    """market_scan_cache에서 가치 점수 상위 5종목 로드 (다이어트 텔레그램 메시지용)."""
    try:
        if not os.path.exists(MARKET_SCAN_CACHE):
            return []
        with open(MARKET_SCAN_CACHE, "r", encoding="utf-8") as f:
            cache = json.load(f)
        stocks = cache.get("stocks", [])
        # score 기준 내림차순, buy_signal 통과한 종목 우선
        sorted_stocks = sorted(stocks, key=lambda s: (-int(s.get("buy_signal", False)), -s.get("score", 0)))
        return sorted_stocks[:5]
    except Exception as e:
        print(f"  [premarket] market_scan_cache 로드 실패: {e}")
        return []


def run_premarket_briefing():
    """8시 50분 — 장 시작 전 통합 브리핑 (텔레그램 다이어트).

    포함:
      - 시장 지표 핵심 (코스피 / VIX / 환율 / 공포탐욕)
      - 가치주 TOP 5 (market_scan_cache 기반)
      - AI 한 줄 코멘트
      - 대시보드 링크
    """
    if _skip_if_holiday("장 시작 전 브리핑"):
        return
    print("[브리핑] 장 시작 전 브리핑")
    try:
        mood = get_market_mood()
        fg   = get_fear_greed(mood)

        kos_arr = "▲" if mood["kospi_chg"] >= 0 else "▼"
        lines = [
            "<b>🔔 장 시작 전 (08:50)</b>",
            "",
            f"📊 코스피 {mood['kospi_price']:,.2f} {kos_arr}{abs(mood['kospi_chg']):.2f}% / "
            f"VIX {mood['vix']:.2f} / 환율 {mood['usdkrw']:,.2f}원 / "
            f"공포탐욕 {fg['score']}({fg['label']})",
            "",
        ]

        # 가치주 TOP 5 (market_scan_cache에서)
        top5 = _load_value_top5()
        if top5:
            lines.append("<b>🎯 오늘 가치주 TOP 5</b>")
            for i, s in enumerate(top5, 1):
                name  = s.get("name", "?")
                score = s.get("score", 0)
                price = s.get("price", 0)
                sector = s.get("sector", "")
                buy_ok = "✅" if s.get("buy_signal") else "🔍"
                lines.append(f"{i}. {buy_ok} {name} ({sector}) — {score}점, {price:,.0f}원")
            lines.append("")
        else:
            lines.append("<i>⚠️ 가치주 캐시 없음 — 새벽 시장스캔 확인 필요</i>")
            lines.append("")

        # AI 한 줄
        client = _get_ai_client()
        if client:
            try:
                prompt = (
                    f"코스피 {mood['kospi_chg']:+.2f}%, VIX {mood['vix']:.2f}, "
                    f"환율 {mood['usdkrw']:,.2f}원. "
                    "오늘 장 초반 전략을 1문장으로."
                )
                resp = client.messages.create(
                    model=CLAUDE_MODEL,
                    max_tokens=120,
                    system=_ai_system(),
                    messages=[{"role": "user", "content": prompt}],
                )
                lines.append(f"🤖 {resp.content[0].text.strip()}")
                lines.append("")
            except Exception:
                pass

        # 대시보드 링크
        lines.append(f"📊 대시보드: {DASHBOARD_URL}")

        tg_send("\n".join(lines))

        # 대시보드 갱신
        try:
            build_and_save_dashboard(mood=mood, fg=fg, kr_top=top5)
        except Exception as e:
            print(f"  [브리핑] 대시보드 갱신 오류: {e}")
    except Exception as e:
        print(f"  [브리핑] 장전 브리핑 오류: {e}")
        tg_send(f"⚠️ 장전 브리핑 수집 실패: {e}")


def run_close_summary():
    """3시 35분 — 장 마감 결산.

    텔레그램 다이어트 후: 텔레그램 발송 안 함. 데이터는 대시보드 갱신용.
    """
    if _skip_if_holiday("장 마감 결산"):
        return
    print("[브리핑] 장 마감 결산 (대시보드 갱신용)")
    try:
        mood = get_market_mood()
        fg   = get_fear_greed(mood)
        # 보유종목 알림 데이터 수집 (대시보드용)
        ha = check_holdings_alerts()

        # 대시보드 갱신
        try:
            build_and_save_dashboard(mood=mood, fg=fg, holdings_alerts=ha)
        except Exception as e:
            print(f"  [브리핑] 대시보드 갱신 오류: {e}")

        # 텔레그램 발송 X — 대시보드에서 확인
        print(f"  [브리핑] 코스피 {mood['kospi_chg']:+.2f}% 마감. 대시보드 갱신 완료.")
    except Exception as e:
        print(f"  [브리핑] 마감 결산 오류: {e}")


# ════════════════════════════════════════════════
# 자동매매 — 텔레그램 제어 / 사전알림 / 매수 / 매도
# ════════════════════════════════════════════════
def _poll_cancel_during_sleep(seconds: int) -> bool:
    """사전알림 동안 텔레그램 폴링 — '/취소' 메시지 수신 시 True 반환.

    매수 직전 SWING_PRE_ALERT_SEC 동안 사용자가 취소할 기회를 줌.
    텔레그램 토큰 없으면 그냥 sleep만.
    """
    if seconds <= 0:
        return False
    if not TELEGRAM_TOKEN:
        time.sleep(seconds)
        return False
    deadline = time.time() + seconds
    offset   = 0
    cancelled = False
    # 첫 폴링: 기존 미처리 업데이트 offset 갱신
    try:
        existing = tg_get_updates(0)
        for upd in existing:
            offset = max(offset, upd["update_id"] + 1)
    except Exception:
        pass
    while time.time() < deadline:
        try:
            updates = tg_get_updates(offset)
        except Exception:
            updates = []
        for upd in updates:
            offset = upd["update_id"] + 1
            text   = (upd.get("message", {}).get("text") or "").strip().lower()
            if text in ("/취소", "/cancel"):
                cancelled = True
                break
        if cancelled:
            break
        time.sleep(2)
    return cancelled


def _load_auto_buy_pool() -> dict:
    """자동매수 후보 종목 풀 — KR_STOCKS(고정 26개) + market_scan_cache(매일 02:00 갱신)."""
    pool: dict = {}

    # 1) KR_STOCKS — 핵심 관심 종목 (항상 포함)
    for ticker, val in KR_STOCKS.items():
        name, period, sector = val
        pool[ticker] = (name, period, sector)

    # 2) market_scan_cache — 시장 전체 스캔 결과 상위 50개 (가능하면 추가)
    try:
        if not os.path.exists(MARKET_SCAN_CACHE):
            print(f"  [auto_buy] market_scan_cache 없음 — KR_STOCKS({len(pool)}개)만 사용")
            return pool
        with open(MARKET_SCAN_CACHE, "r", encoding="utf-8") as f:
            cache = json.load(f)
        # 캐시 신선도 체크 — 3영업일 이내만 유효 (휴장/주말 고려)
        updated_str = cache.get("updated", "")
        try:
            updated_dt = datetime.strptime(updated_str.split()[0], "%Y-%m-%d").replace(
                tzinfo=ZoneInfo("Asia/Seoul")
            )
            age_days = (_now_kst() - updated_dt).days
            if age_days > 3:
                print(f"  [auto_buy] market_scan_cache 오래됨({age_days}일) — KR_STOCKS만 사용")
                return pool
        except Exception:
            pass

        added = 0
        for s in cache.get("stocks", []):
            t = s.get("ticker")
            if not t or t in pool:
                continue
            pool[t] = (s.get("name", t), "중기", s.get("sector", "기타"))
            added += 1
        print(f"  [auto_buy] 종목 풀 = KR_STOCKS {len(KR_STOCKS)}개 + 시장스캔 {added}개 = {len(pool)}개")
    except Exception as e:
        print(f"  [auto_buy] market_scan_cache 로드 실패: {e} — KR_STOCKS만 사용")

    return pool


def run_auto_buy():
    """장 시작 후 자동 매수 (스윙). 09:30~15:30 30분 간격으로 호출.

    스윙 점수 ≥ SWING_SCORE_MIN + swing_signal 통과 종목을 시장가 매수.
    보유 중 / 손절 쿨다운 종목 제외, 일일 한도(positions.json daily) 누적 추적.
    텔레그램 다이어트: 차단/안내 알림은 당일 첫 호출(09:00~09:40)에서만 텔레그램,
    이후 호출은 콘솔 로그만 남김 (매수 발생 시 알림은 정상).
    """
    now = _now_kst()
    # 첫 호출 판단 — 09:00~09:40 사이만 텔레그램 차단/안내. 그 외 호출은 콘솔만.
    is_first_call = now.hour == 9 and now.minute < 40

    def _alert(msg: str) -> None:
        if is_first_call:
            tg_send(msg)
        else:
            print(f"[autobuy] {msg}")

    if not _is_trading_day(now):
        _alert(
            f"📅 <b>[자동매수 스킵]</b> {now.strftime('%m/%d (%a)')} 휴장일 — "
            f"매수 시도 안 함."
        )
        return

    client = get_trading_client()
    mode_tag = client.mode_tag()

    if not AUTO_TRADE_ENABLED:
        _alert(f"⏸ <b>{mode_tag} 자동매매</b> AUTO_TRADE_ENABLED=false — 매수 스킵")
        return

    if not client.available():
        _alert(
            f"🚨 <b>{mode_tag} 자동매매 차단</b>\n"
            f"KIS 매매 키 미설정. PAPER_TRADING={PAPER_TRADING}, "
            f"키 등록 여부: AppKey={'OK' if client.app_key else 'NO'} / "
            f"Account={'OK' if client.account else 'NO'}"
        )
        return

    pos = load_positions()
    if pos.get("halted"):
        _alert(f"⏸ <b>{mode_tag} 자동매매 정지 중</b> — /재개 명령으로 해제")
        return

    today = _today_str()
    daily = _ensure_daily(pos, today)

    # 일일 한도 사전 체크 — 이미 도달했으면 분석 자체를 건너뜀 (불필요한 API 호출 방지)
    if daily["buy_count"] >= SWING_MAX_DAILY_BUY:
        print(f"[autobuy] 일일 종목 한도({SWING_MAX_DAILY_BUY}개) 이미 도달 — 분석 스킵")
        return
    if daily["buy_amount"] >= SWING_MAX_DAILY_AMT:
        print(f"[autobuy] 일일 금액 한도({SWING_MAX_DAILY_AMT//10000}만원) 이미 도달 — 분석 스킵")
        return

    # ── 시장 위험 지수 통합 평가 (Phase 1.5) ──
    # VIX/공포탐욕/코스피 변동률/변동성 가속을 0~100 점수로 종합.
    # 70+ 정지 / 50+ 50% 축소 / 30+ 25% 축소 / <30 정상.
    mood = get_market_mood()
    fg = get_fear_greed(mood) if mood else {"score": 50, "label": "중립"}
    risk = calculate_market_risk(mood, fg)
    print(f"[자동매수] 시장 위험 지수: {risk['score']}/100 ({risk['level']}) → {risk['action']}")
    if risk['reasons']:
        print(f"  → 위험 요인: {', '.join(risk['reasons'])}")

    # Phase 3: 위험 등급 변화 즉시 알림 (먼저 말 거는 비서) ─────────────
    # 등급 변화 시에만 텔레그램 (state 추적으로 30분마다 같은 알림 X).
    LEVEL_RANK = {"안전": 0, "주의": 1, "경계": 2, "위험": 3}
    last_risk_level = pos.get("last_risk_level", "안전")
    new_rank = LEVEL_RANK.get(risk['level'], 0)
    old_rank = LEVEL_RANK.get(last_risk_level, 0)
    if new_rank > old_rank:
        # 등급 악화 — is_first_call 무관 즉시 알림 (중요)
        reasons_html = "\n".join(f"• {r}" for r in risk['reasons']) if risk['reasons'] else ""
        tg_send(
            f"🚨 <b>시장 위험 등급 상승</b>: {last_risk_level} → <b>{risk['level']}</b> ({risk['score']}/100)\n"
            f"<b>대응:</b> {risk['action']}\n\n"
            + (f"<b>주요 위험 요인:</b>\n{reasons_html}" if reasons_html else "")
        )
    elif new_rank < old_rank:
        tg_send(
            f"✅ <b>시장 위험 등급 하락</b>: {last_risk_level} → <b>{risk['level']}</b> "
            f"({risk['score']}/100) — 정상화 추세"
        )
    pos["last_risk_level"] = risk['level']
    save_positions(pos)

    if risk['qty_factor'] == 0.0:
        # 위험 등급(70+) → 자동매수 정지
        reasons_html = "\n".join(f"• {r}" for r in risk['reasons']) if risk['reasons'] else "• (상세 데이터 부족)"
        _alert(
            f"🚨 <b>{mode_tag} 시장 위험도 {risk['score']}/100 ({risk['level']}) — 자동매수 정지</b>\n"
            f"{risk['action']}\n\n"
            f"<b>위험 요인:</b>\n{reasons_html}\n\n"
            f"<i>안정 시 다음 호출에서 자동 재개</i>"
        )
        return

    # ── 종목 풀 구성 (KR_STOCKS + market_scan_cache) ──
    pool = _load_auto_buy_pool()

    print(f"[자동매수] 시작 — 풀 {len(pool)}개 / 무드 {mood.get('status', '중립')} / "
          f"공포탐욕 {fg.get('score', 50)}({fg.get('label', '중립')}) / first_call={is_first_call}")

    # 종목 분석
    print(f"\n[자동매수] {len(pool)}종목 분석 중...")
    # DART 데이터는 KR_STOCKS만 (market_scan 종목은 코드 정보 부족하여 스킵)
    all_dart = get_all_dart_data(KR_STOCKS)
    candidates = []
    diag_buckets  = {"65+": 0, "50-64": 0, "<50": 0}
    diag_blocks   = {}                  # 차단 사유 종류별 카운트
    diag_top_score: list = []           # (-score, name, score, blocks) — 최고점 TOP5 추적
    for ticker, (name, period, sector) in pool.items():
        r = analyze(ticker, name, period, sector,
                    dart_data=all_dart.get(ticker), with_sentiment=False)
        if r:
            sc = r.get("swing_score", 0)
            blocks = r.get("swing_block_reasons", [])
            # 점수 분포
            if sc >= 65:
                diag_buckets["65+"] += 1
            elif sc >= 50:
                diag_buckets["50-64"] += 1
            else:
                diag_buckets["<50"] += 1
            # 차단 사유 카운트 — 동적 값(점수<65, RSI72 등)을 type만 남기고 그룹화
            for b in blocks:
                key = re.split(r"[<\d]", b, maxsplit=1)[0] or b
                diag_blocks[key] = diag_blocks.get(key, 0) + 1
            diag_top_score.append((-sc, name, sc, blocks))
            if r.get("swing_signal") and sc >= SWING_SCORE_MIN:
                candidates.append(r)
        time.sleep(0.4)

    # 진단 로그 — swing_signal 통과 0인 경우 어느 조건이 막는지 즉시 파악 가능
    diag_top_score.sort()
    print(f"\n[진단] 점수 분포: 65+ {diag_buckets['65+']} / 50-64 {diag_buckets['50-64']} / <50 {diag_buckets['<50']}")
    print(f"[진단] 차단 사유 카운트: {diag_blocks}")
    print(f"[진단] 최고점 TOP5:")
    for _, name_, sc_, blocks_ in diag_top_score[:5]:
        print(f"  • {name_}: {sc_}점 / 차단={blocks_ or 'OK(통과)'}")

    # 보유 / 쿨다운 / 일일 한도 적용
    held = set(pos.get("positions", {}).keys())
    cooldown = pos.get("loss_cooldown", {})
    today_iso = today

    candidates.sort(key=lambda x: x.get("swing_score", 0), reverse=True)

    # qty_factor: 시장 위험 지수 기반 자동 조정 (Phase 1.5)
    qty_factor = risk['qty_factor']
    if qty_factor < 1.0:
        reasons_short = " / ".join(risk['reasons'][:3]) if risk['reasons'] else ""
        _alert(
            f"⚠️ 시장 위험 {risk['score']}/100 ({risk['level']}) — {risk['action']}"
            + (f"\n사유: {reasons_short}" if reasons_short else "")
        )

    # 일일 잔여 슬롯만큼만 selected (나머지는 다음 호출 회차에서 처리)
    remaining_slots = SWING_MAX_DAILY_BUY - daily["buy_count"]
    selected = []
    for s in candidates:
        code = s["ticker"].split(".")[0]
        if code in held:
            continue
        if cooldown.get(code) and today_iso < cooldown[code]:
            continue
        selected.append(s)
        if len(selected) >= remaining_slots:
            break

    if not selected:
        # 다이어트: 매수 후보 0개는 텔레그램 X (콘솔만). 30분마다 13번 호출되므로 시끄러움 방지.
        print(f"[자동매수] 매수할 종목 없음 — 시그널 통과 후보 {len(candidates)}개, "
              f"보유/쿨다운 제외 후 0개")
        return

    prev_buy_count = daily["buy_count"]  # 매수 요약 메시지용 (이번 회차 매수 건수 계산)

    # 사전 알림 (30초 동안 /취소 가능)
    # 가치주(미래에셋, 장기) ↔ 자동매매(한국투자증권 모의, 5일 스윙)는 시간 프레임이 달라
    # 섹터 겹쳐도 독립적 리스크 → 섹터 겹침 경고/축소 로직 제거 (5/4 사용자 결정).
    preview_lines = [f"⏰ <b>{mode_tag} {SWING_PRE_ALERT_SEC}초 후 자동 매수</b>", "취소: /취소", ""]
    for s in selected:
        price = s["price"]
        sec   = s.get("sector", "")
        qty   = max(1, int(INVEST_PER_STOCK * qty_factor / price))
        amt   = price * qty
        preview_lines.append(f"• <b>{s['name']}</b> ({s['swing_score']}점, {sec}) — {qty}주 약 {amt:,}원")
    tg_send("\n".join(preview_lines))

    if _poll_cancel_during_sleep(SWING_PRE_ALERT_SEC):
        tg_send(f"🛑 {mode_tag} 사용자 /취소 — 자동 매수 중단")
        # 취소 플래그 정리 (이번 회차 한정)
        return

    # 매수 실행
    for s in selected:
        # 정지 명령 중간 체크 (사용자가 /정지 보냈을 수도)
        pos = load_positions()
        if pos.get("halted"):
            tg_send(f"🛑 {mode_tag} /정지 감지 — 잔여 매수 중단")
            break
        daily = _ensure_daily(pos, today)

        code  = s["ticker"].split(".")[0]
        price = s["price"]
        qty   = max(1, int(INVEST_PER_STOCK * qty_factor / price))
        amt   = price * qty

        if daily["buy_amount"] + amt > SWING_MAX_DAILY_AMT:
            tg_send(f"🛑 일일 매수 한도 도달 ({SWING_MAX_DAILY_AMT//10000}만원) — 추가 매수 중단")
            break
        if daily["buy_count"] >= SWING_MAX_DAILY_BUY:
            tg_send(f"🛑 일일 종목 한도 도달 ({SWING_MAX_DAILY_BUY}개) — 추가 매수 중단")
            break
        if daily["trade_count"] >= SWING_DAILY_TRADE_CAP:
            tg_send(f"🛑 일일 매매 횟수 한도 도달 ({SWING_DAILY_TRADE_CAP}건) — 비정상 폭주 차단")
            break

        result = client.buy(code, qty)
        if result.get("ok"):
            pos["positions"][code] = {
                "name":          s["name"],
                "qty":           qty,
                "buy_price":     price,
                "buy_date":      today,
                "buy_amount":    amt,
                "partial_sold":  False,
                "score":         s.get("score", 0),
                "swing_score":   s.get("swing_score", 0),
                "order_no":      result.get("order_no", ""),
            }
            pos["history"].append({
                "date": today, "side": "buy", "code": code, "name": s["name"],
                "qty": qty, "price": price, "amount": amt,
                "reason": f"swing_score {s.get('swing_score',0)}",
            })
            daily["buy_count"]   += 1
            daily["buy_amount"]  += amt
            daily["trade_count"] += 1
            tg_send(
                f"✅ {mode_tag} 매수 체결: <b>{s['name']}</b> {qty}주 @ 약 {price:,}원 "
                f"(총 {amt:,}원 / 스윙점수 {s.get('swing_score',0)})"
            )
        else:
            tg_send(f"❌ {mode_tag} 매수 실패: {s['name']} — {result.get('msg','')}")
        save_positions(pos)
        time.sleep(1)

    # 대시보드 갱신 (보유 종목 변경 반영)
    try:
        build_and_save_dashboard()
    except Exception as e:
        print(f"  [자동매수] 대시보드 갱신 오류: {e}")

    # 종합 요약 — 이번 회차에 실제 매수 발생한 경우에만 전송 (다이어트)
    daily = pos["daily"][today]
    new_buys = daily["buy_count"] - prev_buy_count
    if new_buys > 0:
        tg_send(
            f"📊 <b>{mode_tag} 매수 요약</b>\n"
            f"이번 회차 신규: {new_buys}종목\n"
            f"오늘 누적: {daily['buy_count']}종목 / 총 {daily['buy_amount']:,}원\n"
            f"현재 보유: {len(pos.get('positions', {}))}종목"
        )
    else:
        print(f"[자동매수] 이번 회차 매수 0건 — 요약 메시지 생략 (누적: {daily['buy_count']}종목)")


def run_auto_sell():
    """장중 30분 주기 매도 점검.

    조건:
      • +SWING_TARGET1_PCT 도달 (절반 미매도) → 절반 매도, partial_sold=True
      • +SWING_TARGET2_PCT 도달 → 잔여 전량 매도 (수익 확정)
      • -SWING_STOP_LOSS_PCT 도달 → 잔여 전량 매도 (손절) + 쿨다운 등록
      • 보유 SWING_MAX_HOLD_DAYS 거래일 경과 → 잔여 전량 매도 (시간 정리)
    """
    client = get_trading_client()
    mode_tag = client.mode_tag()

    if not AUTO_TRADE_ENABLED:
        print(f"  [자동매도] AUTO_TRADE_ENABLED=false — 스킵")
        return
    if not client.available():
        print(f"  [자동매도] KIS 매매 키 미설정 — 스킵")
        return

    # 장중에만 매도 (장 시작 전/마감 후 스킵)
    now = _now_kst()
    if not _is_trading_day(now):
        print(f"  [자동매도] 휴장일 — 스킵 ({now.strftime('%Y-%m-%d %a')})")
        return
    if not _is_market_open(now):
        print(f"  [자동매도] 장 시간 아님 — 스킵")
        return

    pos = load_positions()
    if pos.get("halted"):
        print(f"  [자동매도] /정지 상태 — 스킵")
        return

    if not pos.get("positions"):
        print(f"  [자동매도] 보유 종목 없음 — 스킵")
        return

    today = _today_str()
    daily = _ensure_daily(pos, today)
    sold_msgs = []

    for code in list(pos["positions"].keys()):
        p = pos["positions"][code]
        # 시세 조회 (실전 KIS API — 모의/실전 모두 동일 시세)
        info = _kis.get_price(code) if _kis.available() else {}
        cur_price = _safe_float(info.get("stck_prpr")) if info else 0
        if cur_price <= 0:
            print(f"  [자동매도] {p.get('name','?')}({code}) 시세 조회 실패")
            continue

        buy_price = p["buy_price"]
        pct = (cur_price - buy_price) / buy_price * 100
        held_qty = p["qty"]
        partial = p.get("partial_sold", False)
        days = _trading_days_between(p["buy_date"], today)

        sell_qty = 0
        sell_reason = ""
        is_loss = False
        is_force = False

        if pct <= -SWING_STOP_LOSS_PCT * 100:
            sell_qty = held_qty
            sell_reason = f"손절 ({pct:.1f}%)"
            is_loss = True
        elif pct >= SWING_TARGET2_PCT * 100:
            sell_qty = held_qty
            sell_reason = f"+{pct:.1f}% 전량 익절"
        elif pct >= SWING_TARGET1_PCT * 100 and not partial:
            half = max(1, held_qty // 2)
            sell_qty = half
            sell_reason = f"+{pct:.1f}% 절반 익절"
        elif days >= SWING_MAX_HOLD_DAYS:
            sell_qty = held_qty
            sell_reason = f"{days}거래일 경과 강제 매도 ({pct:+.1f}%)"
            is_force = True

        if sell_qty <= 0:
            continue
        if daily["trade_count"] >= SWING_DAILY_TRADE_CAP:
            print(f"  [자동매도] 일일 매매 한도 도달")
            break

        result = client.sell(code, sell_qty)
        if not result.get("ok"):
            tg_send(f"❌ {mode_tag} 매도 실패: {p.get('name','?')} — {result.get('msg','')}")
            continue

        amt = cur_price * sell_qty
        pos["history"].append({
            "date": today, "side": "sell", "code": code, "name": p["name"],
            "qty": sell_qty, "price": cur_price, "amount": amt,
            "reason": sell_reason, "pct": round(pct, 2),
        })
        daily["trade_count"] += 1

        if sell_qty == held_qty:
            # 전량 매도 → 포지션 제거
            del pos["positions"][code]
            if is_loss:
                cooldown_until = (datetime.strptime(today, "%Y-%m-%d")
                                  + timedelta(days=SWING_LOSS_COOLDOWN_DAYS)).strftime("%Y-%m-%d")
                pos.setdefault("loss_cooldown", {})[code] = cooldown_until
        else:
            # 절반 매도
            pos["positions"][code]["qty"] = held_qty - sell_qty
            pos["positions"][code]["partial_sold"] = True

        emoji = "🔴" if is_loss else ("⏱️" if is_force else "🟢")
        sold_msgs.append(
            f"{emoji} <b>{p['name']}</b> {sell_qty}주 @ {cur_price:,}원 "
            f"({pct:+.1f}%) — {sell_reason}"
        )
        save_positions(pos)
        time.sleep(1)

    if sold_msgs:
        header = f"🤖 <b>{mode_tag} 자동 매도</b> ({_now_kst().strftime('%H:%M')})"
        tg_send("\n".join([header, ""] + sold_msgs))
        # 대시보드 갱신 (보유 변경 반영)
        try:
            build_and_save_dashboard()
        except Exception as e:
            print(f"  [자동매도] 대시보드 갱신 오류: {e}")
    else:
        print(f"  [자동매도] 매도 조건 충족 종목 없음")

    # DART 공시 새 수집 — 30분마다 폴링, 텔레그램은 핵심만, 대시보드 캐시 누적
    try:
        new_disc = collect_new_disclosures(context_label=f"{_now_kst().strftime('%H:%M')} 자동매도")
        if new_disc:
            # dashboard_cache의 disclosures에 prepend (최신순) — 최대 100건 유지
            try:
                _dc = _load_dashboard_cache()
                existing = _dc.get("disclosures") or []
                existing_ids = {d.get("rcept_no") for d in existing}
                merged = [d for d in new_disc if d.get("rcept_no") not in existing_ids] + existing
                merged = merged[:100]
                _dc["disclosures"] = merged
                _save_dashboard_cache(_dc)
                print(f"  [자동매도] 새 공시 {len(new_disc)}건 → 대시보드 누적 {len(merged)}건")
            except Exception as e:
                print(f"  [자동매도] 공시 캐시 갱신 오류: {e}")
    except Exception as e:
        print(f"  [자동매도] 공시 수집 오류: {e}")

    # ── Phase 3: 매도 임박 알림 (먼저 말 거는 비서) ─────────────────────
    # 익절(+5~+5.9%) / 손절(-3~-3.9%) 임박 종목 — 등급 변화 시에만 1회 알림.
    pos = load_positions()
    imminent_alerts = []
    state_changed = False
    for code, p in pos.get("positions", {}).items():
        try:
            if not _kis.available():
                break
            pi = _kis.get_price(code)
            if not pi:
                continue
            curr      = _safe_float(pi.get("stck_prpr"))
            buy_price = p.get("buy_price", 0)
            if buy_price <= 0 or curr <= 0:
                continue
            pnl_pct = (curr - buy_price) / buy_price * 100

            # 등급 판정
            new_state = ""
            if 5.0 <= pnl_pct < SWING_TARGET1_PCT * 100:
                new_state = "near_target1"
            elif pnl_pct >= SWING_TARGET1_PCT * 100 and pnl_pct < SWING_TARGET2_PCT * 100:
                new_state = "near_target2"
            elif -SWING_STOP_LOSS_PCT * 100 < pnl_pct <= -3.0:
                new_state = "near_stop"

            old_state = p.get("imminent_state", "")
            if new_state != old_state:
                p["imminent_state"] = new_state
                state_changed = True
                if new_state == "near_target1":
                    imminent_alerts.append(
                        f"🟢 <b>{p.get('name', code)}</b> {pnl_pct:+.2f}% — "
                        f"1차 익절 임박 (목표 +{SWING_TARGET1_PCT*100:.0f}%, 절반 매도)"
                    )
                elif new_state == "near_target2":
                    imminent_alerts.append(
                        f"💎 <b>{p.get('name', code)}</b> {pnl_pct:+.2f}% — "
                        f"2차 익절 임박 (목표 +{SWING_TARGET2_PCT*100:.0f}%, 전량 매도)"
                    )
                elif new_state == "near_stop":
                    imminent_alerts.append(
                        f"🔴 <b>{p.get('name', code)}</b> {pnl_pct:+.2f}% — "
                        f"손절 임박 (기준 -{SWING_STOP_LOSS_PCT*100:.0f}%, 자동 매도)"
                    )
        except Exception:
            continue

    if imminent_alerts:
        tg_send(
            f"⚠️ <b>매매 임박 알림</b> ({_now_kst().strftime('%H:%M')})\n\n"
            + "\n".join(imminent_alerts)
        )
    if state_changed:
        save_positions(pos)


def run_balance_report() -> str:
    """텔레그램 /잔고 응답용 — 모의 계좌 현황 요약."""
    client = get_trading_client()
    if not client.available():
        return "🚨 KIS 매매 키 미설정"
    bal = client.get_balance()
    if not bal:
        return "잔고 조회 실패"
    pos = load_positions()
    held = pos.get("positions", {})
    lines = [
        f"<b>📊 {client.mode_tag()} 모의 계좌 잔고</b>",
        f"현금: {bal.get('cash', 0):,}원",
        f"평가액: {bal.get('total_eval', 0):,}원",
        f"보유: {len(held)}종목",
        "",
    ]
    if held:
        # 현재가 기반 손익 표시
        for code, p in held.items():
            info = _kis.get_price(code) if _kis.available() else {}
            cur = _safe_float(info.get("stck_prpr")) if info else 0
            if cur > 0:
                pct = (cur - p["buy_price"]) / p["buy_price"] * 100
                profit = (cur - p["buy_price"]) * p["qty"]
                emoji = "🟢" if pct >= 0 else "🔴"
                lines.append(
                    f"{emoji} {p['name']}: {p['qty']}주 / {pct:+.1f}% / "
                    f"{profit:+,.0f}원 (매수 {p['buy_date']})"
                )
            else:
                lines.append(f"⚪ {p['name']}: {p['qty']}주 (시세조회실패)")
    return "\n".join(lines)


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
                model=CLAUDE_MODEL,
                max_tokens=200,
                system=_ai_system(),
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
                    "<b>🤖 AI 맞춤 코칭 (NEW)</b>\n"
                    "/추천 — 지금 뭐 사야 할지 종합 답변 (보유+자금+섹터 고려)\n"
                    "/진단 — 포트폴리오 리스크 진단\n"
                    "/오늘 — 보유 종목별 오늘 액션 (사/팔/홀드)\n"
                    "X 사도 돼? — 보유와 비교 후 답변\n\n"
                    "<b>기본</b>\n"
                    "/리포트 — 오늘 전체 리포트\n"
                    "/보유 — 보유종목 현황 (수동 등록)\n"
                    "/도움말 — 이 메시지\n\n"
                    "<b>자동매매 (스윙)</b>\n"
                    "/잔고 — 모의 계좌 잔고/손익\n"
                    "/정지 — 자동매매 즉시 OFF\n"
                    "/재개 — 자동매매 다시 ON\n"
                    "/취소 — 매수 사전 알림 30초 동안만 유효\n\n"
                    "<b>종목 분석</b>\n"
                    "현대로템 어때? — AI 종목 분석\n"
                    "현대로템 vs 한화에어로스페이스 — 두 종목 비교\n\n"
                    "<b>투자 시뮬레이션</b>\n"
                    "현대로템 300만원 — 투자 시뮬레이션\n"
                    "삼성중공업 500 — 500만원 시뮬레이션\n\n"
                    "<b>시장</b>\n"
                    "지금 시장 어때? — 시장 현황 분석",
                    chat_id,
                )
                continue

            # ── AI 맞춤 코칭 명령어 (Phase 1) ─────────────────
            if text in ("/추천", "/coach"):
                tg_send("🤖 분석 중... (10~20초)", chat_id)
                ans = ai_personal_coach(
                    "지금 어떤 종목을 사야 할까? 내 보유와 자금 상황 고려해서 종합 추천.",
                    mood=mood, fg=fg, kr_top=kr_top,
                )
                tg_send(ans or "AI 응답 실패", chat_id)
                continue

            if text == "/진단":
                tg_send("🩺 포트폴리오 진단 중... (10~20초)", chat_id)
                ans = ai_personal_coach(
                    "내 포트폴리오를 진단해줘. 섹터 집중도, 종목 간 상관관계 위험, "
                    "분산 부족, 큰 손실 시나리오를 짚어주고 개선안 추천.",
                    mood=mood, fg=fg, kr_top=kr_top,
                )
                tg_send(ans or "AI 응답 실패", chat_id)
                continue

            if text == "/오늘":
                tg_send("📅 오늘 액션 분석 중... (10~20초)", chat_id)
                ans = ai_personal_coach(
                    "오늘 내가 보유한 가치주 종목별로 어떻게 대응해야 할지 (홀드/익절/추매/손절) "
                    "오늘 시장 상황과 매크로 고려해서 1줄씩 알려줘. 자동매매도 매수 추천 있으면 함께.",
                    mood=mood, fg=fg, kr_top=kr_top,
                )
                tg_send(ans or "AI 응답 실패", chat_id)
                continue

            # "X 사도 돼?" / "X 사도될까?" 자연어 패턴 → 코칭으로
            if re.search(r"사도\s*(돼|될까|될까요|되나|되니)\??", text):
                tg_send("🤔 분석 중... (10~20초)", chat_id)
                ans = ai_personal_coach(
                    f"질문: '{text}'\n"
                    "이 종목을 사도 될지 내 보유 종목과 섹터 분포, 자금 상황 고려해서 답해줘. "
                    "겹치는 섹터/유사 종목 있으면 명시.",
                    mood=mood, fg=fg, kr_top=kr_top,
                )
                tg_send(ans or "AI 응답 실패", chat_id)
                continue

            if text == "/잔고":
                tg_send(run_balance_report(), chat_id)
                continue

            if text in ("/정지", "/halt", "/stop"):
                pos_h = load_positions()
                pos_h["halted"] = True
                save_positions(pos_h)
                tg_send("🛑 <b>자동매매 정지됨.</b>\n신규 매수/매도 모두 차단됩니다.\n해제: /재개", chat_id)
                continue

            if text in ("/재개", "/resume"):
                pos_h = load_positions()
                pos_h["halted"] = False
                save_positions(pos_h)
                tg_send("▶️ <b>자동매매 재개됨.</b>\n다음 트리거부터 정상 동작합니다.", chat_id)
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

                # KR 전용 모드일 때 해외 요청 → 안내 후 종료
                if KR_ONLY and "해외" in text:
                    tg_send(
                        "🌐 <b>현재 국내 전용 봇으로 운영 중입니다.</b>\n"
                        "해외 종목 추천은 비활성화 상태이며, "
                        "미국 매크로(금리/달러/CPI)는 KR 시장 영향 분석에 반영됩니다.\n\n"
                        "<i>국내 종목을 보시려면: 「국내 뭐 사?」 또는 「뭐 사?」</i>",
                        chat_id,
                    )
                    continue

                if "국내" in text:
                    pool   = kr_buy[:5]
                    header = "🇰🇷 국내 매수 신호 종목"
                    flag   = "kr_only"
                elif "해외" in text:
                    pool   = us_buy[:5]
                    header = "🇺🇸 해외 매수 신호 종목"
                    flag   = "us_only"
                elif KR_ONLY:
                    pool   = kr_buy[:5]
                    header = "💚 오늘 매수 신호 종목 (국내 TOP 5)"
                    flag   = "kr_only"
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

    date_obj = _now_kst() - timedelta(days=1)
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
        end_obj   = _now_kst()
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
    if _skip_if_holiday("시장 스캔"):
        return
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
        "updated": _now_kst().strftime("%Y-%m-%d %H:%M"),
        "count":   len(top50),
        "stocks":  top50,
    }
    with open(MARKET_SCAN_CACHE, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)

    print(f"캐시 저장 완료: {MARKET_SCAN_CACHE}")

    # 대시보드 갱신 (스캔 결과 반영)
    try:
        build_and_save_dashboard()
    except Exception as e:
        print(f"  [스캔] 대시보드 갱신 오류: {e}")


# ════════════════════════════════════════════════
# 메인 실행
# ════════════════════════════════════════════════
def run():
    # 한국 휴장일이면 일일 리포트 스킵 (휴장일 데이터로 분석하면 노이즈)
    if _skip_if_holiday("일일 리포트"):
        return
    print("=" * 60)
    print("투자 비서 v6.0 시작")
    if KR_ONLY:
        print(f"🇰🇷 KR 전용 모드 — 국내 {len(KR_STOCKS)}종목 분석 (해외 분석 스킵)")
    else:
        print(f"국내 {len(KR_STOCKS)}종목 + 해외 {len(US_STOCKS)}종목 분석 중...")
    print(f"KIS API: {'연결됨' if _kis.available() else 'yfinance 폴백'}")
    print(f"AI 분석: {'Claude 활성화' if (_ANTHROPIC_OK and ANTHROPIC_API_KEY) else '비활성화 (ANTHROPIC_API_KEY 없음)'}")
    print("=" * 60)

    # ── 월간 리포트 (매월 1일) ──────────────────
    if _now_kst().day == 1:
        monthly = make_monthly_report()
        if monthly:
            print("\n[월간 성과 리포트 전송]")
            tg_send(monthly)

    print("\n[1/7] 시장 분위기 파악 중...")
    mood = get_market_mood()
    fg   = get_fear_greed(mood)
    print(f"  → 시장: {mood['status']} / VIX: {mood['vix']:.2f} / 코스피: {mood['kospi_chg']:+.2f}%")
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

    us_results = []
    if KR_ONLY:
        print("\n[5/7] 해외 종목 분석 — 스킵 (KR_ONLY 모드)")
    else:
        print("\n[5/7] 해외 종목 분석 중...")
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

    print("\n[7/7] 리포트 생성 (대시보드만 갱신)...")
    # 텔레그램 다이어트: 일일 리포트 텔레그램 발송 제거. 대시보드에서 확인.
    # 단, 데이터(kr_top5, dart_alerts, ai_summary 등)는 모두 수집해서 대시보드에 반영.

    ha = check_holdings_alerts()
    record_recommendations(kr_top5, us_top5)

    # 매크로 이벤트 D-day / D-1 알림 (Phase 4)
    try:
        evt_count = notify_imminent_events()
        if evt_count:
            print(f"  [이벤트] D-day/D-1 {evt_count}건 알림 발송")
    except Exception as e:
        print(f"  [이벤트] 알림 오류: {e}")

    # DART 공시 새 수집 — 보유 종목 + KR_STOCKS, 캐시 중복 차단
    # 텔레그램은 보유 종목 + ⚠️/✅ 키워드만 (다이어트), 전체 공시는 대시보드 카드로
    try:
        disclosures_data = collect_new_disclosures(context_label="08:00 일일 점검")
        if disclosures_data:
            print(f"  [DART] 새 공시 {len(disclosures_data)}건 수집 → 대시보드 표시")
    except Exception as e:
        print(f"  [DART] 공시 수집 오류: {e}")
        disclosures_data = []

    # 자산 일별 스냅샷 저장 — portfolio_history.json 누적 (자산 추이 차트용)
    try:
        v_value = sum(h.get("value", 0) for h in (ha or []))
        v_cost  = sum(h.get("cost", 0)  for h in (ha or []))
        a_value = a_cost = 0.0
        for code, p in load_positions().get("positions", {}).items():
            bp = p.get("buy_price", 0); qty = p.get("qty", 0)
            if _kis.available():
                pi = _kis.get_price(code)
                cp = _safe_float(pi.get("stck_prpr")) if pi else 0
            else:
                cp = bp  # 폴백
            a_value += cp * qty
            a_cost  += bp * qty
        _record_portfolio_value(v_value, v_cost, a_value, a_cost)
    except Exception as e:
        print(f"  [run] portfolio_history 기록 오류: {e}")

    # 가치주 보유 종목별 7일 sparkline 데이터 수집 (대시보드 row 옆 추세선용)
    try:
        print("\n[7.3/7] 가치주 보유 종목 7일 sparkline 수집...")
        holdings_sparklines = _fetch_holdings_sparklines(ha or [], days=7)
        if holdings_sparklines:
            print(f"  → {len(holdings_sparklines)}개 종목 sparkline 생성")
    except Exception as e:
        print(f"  [run] sparkline 수집 오류: {e}")
        holdings_sparklines = {}

    # 시장 위험 지수 계산 (Phase 1.5) — daily 시점 기준 dashboard 게이지용
    try:
        risk = calculate_market_risk(mood, fg)
        print(f"\n[7.4/7] 시장 위험 지수: {risk['score']}/100 ({risk['level']}) → {risk['action']}")
        if risk.get('reasons'):
            print(f"  → 위험 요인: {', '.join(risk['reasons'])}")
    except Exception as e:
        print(f"  [run] 위험 지수 계산 오류: {e}")
        risk = None

    # AI 맞춤 비서 — 이제훈님 보유+자금+섹터 종합 코칭 (Phase 1)
    print("\n[7.5/7] AI 맞춤 비서 (개인 코칭) 생성 중...")
    try:
        personal_brief = ai_personal_coach(
            "오늘 내 포트폴리오 종합 진단 + 추천 종목 + 보유별 액션 + 주의사항을 "
            "한 화면에 정리. 각 섹션 헤더 (📊 시장 / 💼 보유 진단 / 🎯 추천 / ⚠️ 주의 / 🎬 오늘 행동)로 구분.",
            mood=mood, fg=fg, kr_top=kr_top5, ai_macro=ai_macro,
            max_tokens=1500,
        )
        if personal_brief:
            print(f"  → 개인 코칭 생성됨 ({len(personal_brief)}자)")
    except Exception as e:
        print(f"  [run] AI 맞춤 비서 오류: {e}")
        personal_brief = ""

    # 풀 데이터 캐시 저장 — 이후 호출(autobuy 14번, premarket, close 등)에서 보충용
    try:
        _save_dashboard_cache({
            "macro": macro, "ai_summary": ai_summary, "ai_sector": ai_sector,
            "ai_macro": ai_macro, "ai_insights": ai_insights,
            "kr_top": kr_top5, "us_top": us_top5,
            "avoid": avoid_list, "dart_alerts": dart_alerts,
            "personal_brief": personal_brief,
            "risk": risk,
            "holdings_sparklines": holdings_sparklines,
            "disclosures": disclosures_data,
        })
    except Exception as e:
        print(f"  [run] dashboard_cache 저장 오류: {e}")

    # 대시보드 갱신
    try:
        build_and_save_dashboard(
            mood=mood, fg=fg,
            kr_top=kr_top5, us_top=us_top5, avoid=avoid_list,
            dart_alerts=dart_alerts,
            ai_summary=ai_summary, ai_sector=ai_sector,
            ai_insights=ai_insights,
            macro=macro, ai_macro=ai_macro,
            holdings_alerts=ha,
            personal_brief=personal_brief,
            risk=risk,
            holdings_sparklines=holdings_sparklines,
            disclosures=disclosures_data,
        )
    except Exception as e:
        print(f"  [run] 대시보드 갱신 오류: {e}")

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


def _notify_fatal(mode: str, exc: BaseException):
    """봇 실행 중 처리되지 않은 예외 발생 시 텔레그램으로 즉시 알림."""
    import traceback
    tb_full = traceback.format_exc()
    # 텔레그램 가독성을 위해 마지막 트레이스백 8줄만 표시
    tb_tail = "\n".join(tb_full.strip().splitlines()[-8:])
    err_msg = (
        f"🚨 <b>[봇 실행 실패]</b>\n"
        f"모드: <code>{mode or 'run (기본)'}</code>\n"
        f"시간: {_now_kst().strftime('%Y-%m-%d %H:%M:%S')}\n"
        f"오류: <code>{type(exc).__name__}: {str(exc)[:300]}</code>\n\n"
        f"<b>트레이스백:</b>\n<pre>{tb_tail[:1500]}</pre>\n\n"
        f"<i>GitHub Actions 로그에서 전체 내용 확인 가능</i>"
    )
    try:
        tg_send(err_msg)
    except Exception as send_err:
        print(f"[FATAL] 실패 알림 전송도 실패: {send_err}", file=sys.stderr)
    print(f"\n[FATAL] {tb_full}", file=sys.stderr)


# 모드별 예상 KST 실행 시각 (HH:MM) — 실제 실행이 이 시각 +허용 오차를 벗어나면 경고
_MODE_SCHEDULE = {
    "":             ("08:00", 60),   # run (기본 일일 리포트)
    "--usclose":    ("06:00", 60),   # 미국 시장 마감 브리핑
    "--premarket":  ("08:50", 30),   # 장 시작 전 브리핑
    "--monitor":    ("09:05", 30),   # 장중 모니터링 시작
    "--close":      ("15:35", 30),   # 장 마감 결산
    "--marketscan": ("02:00", 90),   # 새벽 전체 스캔
    # --autobuy: 09:00~15:30 30분 주기로 변경 (5/4) → 단일 시각 비교 의미 없음, 드리프트 검사 생략
    # --autosell: 30분 주기라 단일 시각이 없음 → 드리프트 검사 생략
}


def _check_schedule_drift(mode: str) -> None:
    """현재 KST 시각이 예상 시각에서 크게 벗어나면 텔레그램으로 경고."""
    info = _MODE_SCHEDULE.get(mode)
    if not info:
        return
    expected_hm, tolerance_min = info
    eh, em = map(int, expected_hm.split(":"))
    now = _now_kst()
    expected = now.replace(hour=eh, minute=em, second=0, microsecond=0)
    diff_min = (now - expected).total_seconds() / 60
    if abs(diff_min) <= tolerance_min:
        return  # 정상 범위
    direction = "지연" if diff_min > 0 else "조기"
    msg = (
        f"⏰ <b>[스케줄 어긋남 감지]</b>\n"
        f"모드: <code>{mode or 'run (기본)'}</code>\n"
        f"예상: {expected_hm} KST / 실제: {now.strftime('%H:%M')} KST\n"
        f"차이: {abs(diff_min):.0f}분 {direction}\n\n"
        f"<i>※ GitHub Actions 무료 cron은 수시간 지연 가능. "
        f"정확한 시간이 중요하면 Railway 등 전용 서버 권장.</i>"
    )
    try:
        tg_send(msg)
    except Exception:
        pass


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else ""

    try:
        # 실행 시점이 예상 시각과 너무 다르면 사용자에게 알림
        _check_schedule_drift(mode)

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
        elif mode == "--autobuy":
            run_auto_buy()
        elif mode == "--autosell":
            run_auto_sell()
        else:
            run()
    except Exception as e:
        _notify_fatal(mode, e)
        sys.exit(1)
