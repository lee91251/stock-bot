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
import signal
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests
import yfinance as yf
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from xml.etree import ElementTree as ET

# 5/29 Phase 2 1단계: 재무부 (데이터 read/write 통합)
from finance import (
    load_positions, save_positions,
    load_mirae_paper, save_mirae_paper,
    load_alerts, save_alerts,
    _load_alerts, _save_alerts,           # 옛 이름 별칭 (점진 마이그레이션)
    load_tomorrow_picks, save_tomorrow_picks,
    _load_tomorrow_picks, _save_tomorrow_picks,  # 옛 이름 별칭
)

# 5/29 Phase 2 3단계: 알림부 (텔레그램 OUT + 알림 센터)
from notify import (
    tg_send, tg_send_document,
    _tg_base, _balance_html_tags, _HTML_TAG_RE,  # 헬퍼 (drift 방지)
    log_alert,
    notify_fatal, _notify_fatal,  # 옛 이름 별칭
)

# 5/29 Phase 2 4단계: 대시보드부 (1차 — 4트랙 카드 + avoid + 헬퍼)
from dashboard import (
    _empty_section,
    _make_short_term_card,
    _make_mid_term_card,
    _make_long_term_card,
    _make_avoid_card,
    card_html,
    _dashboard_css,
    _make_recommend_card,
    _make_value_holdings_section,
    _make_auto_positions_section,
    _make_paper_mirae_section,
    _make_b4_learning_card,
    _make_advisor_stats_card,
    _make_compare_card,
    _make_performance_card,
    _make_trade_history_card,
    _make_portfolio_history_card,
    _make_macro_html,
    _make_personal_coach_card,
    _make_ai_card,
    _make_macro_card,
    dart_alerts_section_html,
    _make_market_briefing_card,
    _make_risk_gauge_html,
    _make_disclosures_card,
    _make_dart_card,
    _make_hero_header,
    _make_sidebar,
    _make_total_summary_section,
    _make_alerts_section,
    _make_tomorrow_picks_section,
    _make_allocation_card,
)

# 5/29 Phase 2 5단계: 학습부 (1차 — AI 어드바이저 로그/정확도 + 트레이딩 일기)
from learning import (
    _load_advisor_log, _save_advisor_log,
    log_advisor_decision,
    calc_advisor_accuracy,
    _get_recent_journals,
    AI_ADVISOR_LOG, AI_ADVISOR_MIN_SAMPLES, AI_ADVISOR_MIN_ACCURACY,
)


# 5/29 영구 차단: yfinance .info 무한 대기 방지 (Linux SIGALRM)
# 사고 26620221277 (5/29 marketscan 1시간 22분 무한 대기) 진단 결과
def _yf_info_with_timeout(ticker: str, timeout_sec: int = 3) -> dict:
    """yfinance .info를 timeout 보호로 호출 — Linux/GitHub Actions만 작동.

    Args:
        ticker: '005930.KS' 등 yfinance 형식
        timeout_sec: 최대 대기 초 (3초 권장)

    Returns: .info dict 또는 None (timeout/오류 시)
    """
    if sys.platform == "win32":
        # Windows 로컬 — SIGALRM 없음. 그대로 호출 (개발 환경 디버깅용).
        try:
            return yf.Ticker(ticker).info
        except Exception:
            return None

    # 5/29 병렬화: SIGALRM은 메인 스레드 전용 → 워커 스레드에선 daemon 스레드+join(timeout)
    # 사고 재발 방지: 멈춘 yfinance 호출은 daemon이라 프로세스 종료를 막지 않음.
    if threading.current_thread() is not threading.main_thread():
        result = [None]
        def _call():
            try:
                result[0] = yf.Ticker(ticker).info
            except Exception:
                result[0] = None
        t = threading.Thread(target=_call, daemon=True)
        t.start()
        t.join(timeout_sec)
        return result[0]

    def _handler(signum, frame):
        raise TimeoutError(f"yfinance timeout {timeout_sec}s")

    old_handler = signal.signal(signal.SIGALRM, _handler)
    signal.alarm(timeout_sec)
    try:
        return yf.Ticker(ticker).info
    except (TimeoutError, Exception):
        return None
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, old_handler)

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

# 5/15: KRX 전체 시장 endpoint 사고(2026-05-09~) 대응 — FinanceDataReader 우회 데이터 소스
try:
    import FinanceDataReader as _fdr
    _FDR_OK = True
except ImportError:
    _FDR_OK = False
    _fdr = None

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
MARKET_SCAN_CACHE   = os.path.join(os.path.dirname(os.path.abspath(__file__)), "market_scan_cache.json")
TOMORROW_PICKS_CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tomorrow_picks.json")
MIRAE_PAPER_FILE    = os.path.join(os.path.dirname(os.path.abspath(__file__)), "mirae_paper.json")
ALERTS_FILE         = os.path.join(os.path.dirname(os.path.abspath(__file__)), "alerts.json")
# AI 매도 어드바이저 v2 (B1 → 신뢰도 검증 → 자동 결정 반영)
# AI_ADVISOR_LOG / MIN_SAMPLES / MIN_ACCURACY → learning.py (5단계 학습부)
AI_ADVISOR_OUTCOME_DAYS  = 5       # AI 의견 후 N일 가격 추적 → 정확도 평가 (track_advisor_outcomes 잔류용)

# B4 자동 가중치 튜닝 (자가학습 — 30건+ 누적 시 권장 알림)
B4_MIN_SAMPLES           = 30      # 자가학습 최소 표본
B4_SECTOR_MIN_TRADES     = 3       # 섹터 권장 최소 매매 건수
B4_HOUR_MIN_TRADES       = 3       # 시간대 권장 최소 매매 건수
B4_GAP_HIGH              = 20      # 점수대 승률 차이 20%p 이상 — 임계 조정 권장
B4_WEAK_WIN_RATE         = 30      # 30% 미만 승률 — 회피 권장
B4_STRONG_WIN_RATE       = 70      # 70%+ 승률 — 우대 권장

# 미래에셋 모의 (추천 검증용 가치주) — 가치주 룰 적용
PAPER_MIRAE_STOP_LOSS_PCT  = 0.07   # -7% 손절
PAPER_MIRAE_TARGET1_PCT    = 0.10   # +10% 1차 (절반)
PAPER_MIRAE_TARGET2_PCT    = 0.20   # +20% 2차 (전량)
PAPER_MIRAE_TARGET3_PCT    = 0.40   # +40% 장기 목표 (참고)

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
SWING_MAX_HOLD_DAYS      = 5           # 5거래일 후 강제 매도 (기본)
# 5/29: 유연 보유 룰 (회장 통찰 — 좋은 종목 더 보유 / 나쁜 종목 빨리 청산)
SWING_MAX_HOLD_EXTENDED  = 10          # 💚 상승 추세 보유 연장 최대 한도
SWING_QUICK_EXIT_DAYS    = 3           # 🔴 하락 종목 빨리 청산 (3거래일)
SWING_EXTEND_MIN_PCT     = 3.0         # 💚 보유 연장 조건: +3% 이상 수익
SWING_QUICK_EXIT_PCT     = -1.0        # 🔴 빨리 청산 조건: -1% 미만
SWING_MAX_DAILY_BUY      = 5           # 하루 최대 신규 매수 종목
SWING_MAX_DAILY_AMT      = 10_000_000  # 하루 최대 매수 금액(원)
SWING_LOSS_COOLDOWN_DAYS = 3           # 손절 후 같은 종목 재매수 금지 기간
SWING_PRE_ALERT_SEC      = 30          # 매수 직전 사전 알림 + /취소 대기 시간
SWING_DAILY_TRADE_CAP    = 20          # 일일 매매 횟수 한도 (폭주 차단)

# 한국 스윙 확률 룰 (5/12 추가, 백테스트 검증 후 활성화 예정)
# 활성화 시 매수 직전 추가 가드:
#   1. 외국인+기관 *둘 다* 최근 3거래일 합산 순매도 → 매수 차단
#   2. 거래량 +500% 초과 (과열) → 매수 차단
# 환경변수 NEW_RULES_ENABLED=true 로 활성화 (기본 false — 검증 전 안전)
NEW_RULES_ENABLED = os.environ.get("NEW_RULES_ENABLED", "false").lower() == "true"
NEW_RULES_VOL_OVERHEAT_PCT = 500.0  # 거래량 평균 대비 N% 초과 시 과열로 차단

# 비상정지 임계 (5/13 추가, 회장 부재 안전망)
# 회장 결정 (4단계 검증부 체크리스트): 일일 -3% / MDD -15%
EMERGENCY_DAILY_LOSS_PCT = -3.0   # 일일 누적 손익이 -3% 도달 시 자동매수 정지
EMERGENCY_MDD_PCT        = -15.0  # 30일 MDD가 -15% 도달 시 자동매수 정지

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
# 5/29 Phase 2 1단계: 재무부(finance.py)로 이동
# load_positions / save_positions → from finance import (아래 import 블록)


def _today_str() -> str:
    return _now_kst().strftime("%Y-%m-%d")


def _ensure_daily(pos: dict, date: str) -> dict:
    pos.setdefault("daily", {}).setdefault(
        date, {"buy_count": 0, "buy_amount": 0, "trade_count": 0}
    )
    return pos["daily"][date]


def _trading_days_between(start_iso: str, end_iso: str) -> int:
    """시작일~종료일 사이 실제 거래일 수 (시작일 제외, 종료일 포함).

    5/29 fix: weekday() < 5 단순 체크 → _is_trading_day로 변경
    - KRX 휴장일 제외 (부처님오신날 대체 5/25 등) — 회장 통찰: "주말 포함 5일 적용된 듯"
    - 정확한 거래일 카운트 → 시간 강제 매도 정확화
    """
    try:
        s = datetime.strptime(start_iso, "%Y-%m-%d")
        e = datetime.strptime(end_iso,   "%Y-%m-%d")
    except Exception:
        return 0
    days = 0
    d = s + timedelta(days=1)
    while d <= e:
        if _is_trading_day(d):  # 평일 + 휴장일 체크
            days += 1
        d += timedelta(days=1)
    return days


# ════════════════════════════════════════════════
# DART API
# ════════════════════════════════════════════════
_corp_cache: dict = {}
_DART_CORP_MAP_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dart_corp_map.json")
_corp_map_loaded: bool = False  # corpCode.xml 매핑 로드 여부


def dart_load_corp_code_map() -> int:
    """5/29: DART corpCode.xml 일괄 다운로드 → 전체 상장사 corp_code 매핑 캐시.

    1398종목 marketscan 시 API 호출 1,398회 → *1회*로 감소.
    파일 캐시(dart_corp_map.json) 사용 — 한 번 다운로드 후 재사용.

    Returns: 로드된 매핑 수
    """
    global _corp_cache, _corp_map_loaded
    if _corp_map_loaded:
        return len(_corp_cache)

    # 파일 캐시 확인
    if os.path.exists(_DART_CORP_MAP_FILE):
        try:
            with open(_DART_CORP_MAP_FILE, "r", encoding="utf-8") as f:
                cached = json.load(f)
            _corp_cache.update(cached)
            _corp_map_loaded = True
            print(f"  [DART] corp_code 매핑 캐시 로드: {len(_corp_cache)}종목")
            return len(_corp_cache)
        except Exception as e:
            print(f"  [DART] 캐시 로드 실패: {e}")

    # API에서 다운로드
    if not DART_API_KEY:
        return 0
    try:
        import zipfile, io
        from xml.etree import ElementTree as ET
        r = requests.get(f"{DART_BASE}/corpCode.xml",
                        params={"crtfc_key": DART_API_KEY}, timeout=30)
        z = zipfile.ZipFile(io.BytesIO(r.content))
        xml_data = z.read(z.namelist()[0]).decode("utf-8")
        root = ET.fromstring(xml_data)
        loaded = 0
        for entry in root.findall("list"):
            stock_code = (entry.findtext("stock_code") or "").strip()
            corp_code  = (entry.findtext("corp_code")  or "").strip()
            if stock_code and len(stock_code) == 6 and corp_code:
                _corp_cache[stock_code] = corp_code
                loaded += 1
        # 파일로 저장 (재사용)
        with open(_DART_CORP_MAP_FILE, "w", encoding="utf-8") as f:
            json.dump(_corp_cache, f, ensure_ascii=False, indent=0)
        _corp_map_loaded = True
        print(f"  [DART] corpCode.xml 다운로드 완료: {loaded}종목 매핑")
        return loaded
    except Exception as e:
        print(f"  [DART] corpCode.xml 다운로드 실패: {e}")
        return 0

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
    # 5/29: PBR 계산용 자기자본 — DART BS(재무상태표)에서 추출
    EQUITY_NM     = {"자본총계", "지배기업의 소유주에게 귀속되는 자본", "지배기업소유주지분"}

    res = {"year": year, "fs_div": fs_used}
    prev_res: dict = {}

    for item in d["list"]:
        sj_div = item.get("sj_div")
        acct = item.get("account_nm", "").strip()
        raw  = item.get("thstrm_amount", "0").replace(",", "")
        prev = item.get("frmtrm_amount", "0").replace(",", "")
        try:
            val  = int(raw)
            pval = int(prev)
        except (ValueError, TypeError):
            continue
        # 손익계산서 (IS/CIS) 항목
        if sj_div in ("IS", "CIS"):
            if acct in REVENUE_NM and "revenue" not in res:
                res["revenue"]    = val
                prev_res["revenue"] = pval
            elif acct in OP_INCOME_NM and "op_income" not in res:
                res["op_income"]    = val
                prev_res["op_income"] = pval
            elif acct in NET_INCOME_NM and "net_income" not in res:
                res["net_income"]   = val
                prev_res["net_income"] = pval
        # 5/29: 재무상태표 (BS) 자기자본 — PBR 계산용
        elif sj_div == "BS":
            if acct in EQUITY_NM and "equity" not in res:
                res["equity"]      = val
                prev_res["equity"] = pval

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


# 5/29: DART 기반 PER/PBR/ROE 직접 계산 — KRX 펀더 endpoint 사고 우회
# 6/1 작업 예약 → 5/29 복귀 즉시 진행 (회장 결정)
#
# 공식:
#   PER = 시가총액 / 당기순이익 (시가총액·EPS·발행주식수 분자분모 약분)
#   PBR = 시가총액 / 자기자본
#   ROE = 당기순이익 / 자기자본 × 100
#
# 발행주식수 몰라도 시가총액만 있으면 PER/PBR 계산 가능 (핵심 통찰).
_DART_FUND_CACHE: dict = {}  # corp_code → financials dict (marketscan 1회 1번만 호출)


def dart_calc_per_pbr_roe(stock_code: str, mktcap: float) -> dict:
    """DART 분기 보고서로 PER/PBR/ROE 직접 계산.

    Args:
        stock_code: 6자리 종목코드
        mktcap: 시가총액 (원)

    Returns: {"per": float, "pbr": float, "roe": float} 또는 빈 dict
    """
    if not DART_API_KEY or mktcap <= 0:
        return {}

    corp_code = dart_corp_code(stock_code)
    if not corp_code:
        return {}

    # marketscan 1회 내 동일 corp_code 1번만 DART 호출
    if corp_code in _DART_FUND_CACHE:
        fin = _DART_FUND_CACHE[corp_code]
    else:
        fin = dart_financials(corp_code)
        _DART_FUND_CACHE[corp_code] = fin

    if not fin:
        return {}

    result = {}
    net_income = fin.get("net_income", 0)
    equity     = fin.get("equity", 0)

    if net_income > 0:
        per = round(mktcap / net_income, 2)
        if 0 < per < 1000:  # 비정상 값 차단
            result["per"] = per

    if equity > 0:
        pbr = round(mktcap / equity, 2)
        if 0 < pbr < 50:
            result["pbr"] = pbr
        # ROE = 순이익 / 자기자본 × 100
        roe = round(net_income / equity * 100, 1)
        if -100 < roe < 200:
            result["roe"] = roe

    return result


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


def fetch_today_disclosures_for_dashboard(days: int = 1) -> list:
    """대시보드용 — 보유 종목 + KR_STOCKS의 최근 N일 공시 전체 (seen 무관).

    텔레그램용 collect_new_disclosures와 분리:
    - collect_new_disclosures = seen 차단 (한 번만 알림)
    - fetch_today_disclosures_for_dashboard = seen 무관 (대시보드는 항상 최신 list 표시)
    """
    if not DART_API_KEY:
        return []

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

    items = fetch_recent_disclosures_by_stock_codes(target, days=days)
    if not items:
        return []

    # 보유 우선 + 최신순
    items.sort(key=lambda it: (
        0 if it.get("stock_code", "") in held_codes else 1,
        -int(it.get("rcept_dt", "0") or "0"),
        -int(it.get("rcept_no", "0") or "0"),
    ))

    enriched = []
    for it in items[:100]:  # 최대 100건
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
        # 텔레그램 X — 대시보드 알림 센터로 (정보성 다이어트)
        for it, em, is_held in critical:
            title = it.get("report_nm", "")
            name  = it.get("corp_name", "")
            held_mark = " 🏠" if is_held else ""
            # ⚠️ 위험 키워드는 warning, 보유 종목은 info, ✅ 호재는 info
            if em == "⚠️":
                lvl = "warning"
            else:
                lvl = "info"
            log_alert(
                "disclosure", lvl,
                f"DART {em} {name}{held_mark}",
                title,
                em or "📰",
            )

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

    # 2) 거래량 / 모멘텀 (5/6: 가중치 후하게 — 급등주 캐치 가능하도록)
    if vol_ratio >= 250:
        sw_score += 25; sw_reasons.append(f"거래량 {vol_ratio:.0f}% 매우 폭증")
    elif vol_ratio >= 200:
        sw_score += 18; sw_reasons.append(f"거래량 {vol_ratio:.0f}% 급증")
    elif vol_ratio >= 150:
        sw_score += 12; sw_reasons.append(f"거래량 {vol_ratio:.0f}% 활발")
    elif vol_ratio >= 100:
        sw_score += 6;  sw_reasons.append(f"거래량 {vol_ratio:.0f}% 평균↑")
    elif vol_ratio < 70:
        sw_score -= 5
    # 급등 모멘텀 보너스 (거래량 폭증 + 가격 급등 동시) — SKC 같은 30% 종목 캐치
    if vol_ratio >= 200 and change >= 3.0:
        sw_score += 15
        sw_reasons.append(f"급등 모멘텀 (vol +{vol_ratio:.0f}%, 가격 +{change:.1f}%)")
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

    # 스윙 매수 시그널: 점수 + 안전 조건 (5/6 완화 — RSI 65→70, vol 100→70, ret_1m -15→-20)
    swing_block_reasons = []
    if sw_score < SWING_SCORE_MIN:
        swing_block_reasons.append(f"점수<{SWING_SCORE_MIN}")
    if rsi >= 70:
        swing_block_reasons.append(f"RSI{int(rsi)}")
    if manipulation_signal:
        swing_block_reasons.append("조작감지")
    if momentum_bad:
        swing_block_reasons.append("모멘텀악화")
    if dart_sigs.get("rights"):
        swing_block_reasons.append("유증")
    if sr["near_resistance"]:
        swing_block_reasons.append("저항근처")
    if vol_ratio < 70:
        swing_block_reasons.append(f"거래량{int(vol_ratio)}%")
    if ret_1m <= -20:
        swing_block_reasons.append(f"1개월{ret_1m:.0f}%")
    swing_signal = len(swing_block_reasons) == 0

    # 급등 모멘텀 매수 시그널 (5/6 추가 — SKC 같은 30% 종목 캐치)
    # 거래량 폭증 + 가격 급등 → swing_signal 통과 못해도 강력 매수 후보
    # 5/6 변경: 가격 상승률 +3~+5% 안전대만 매수 (추격매수 차단)
    #   배경: 미래에셋 5/6 매수가 +14.5%에 잡혀 평균 +1%만 남음 — 너무 늦은 진입.
    momentum_signal = (
        vol_ratio >= 200          # 거래량 평균 2배 이상
        and 3.0 <= change <= 5.0  # 당일 +3~+5% 안전대 (이전: +3% 이상 무상한)
        and rsi < 80              # 너무 과열은 X
        and not manipulation_signal
        and not momentum_bad
        and not dart_sigs.get("rights")
        and ret_1m > -20
        and sw_score >= 50        # swing 임계(65)보다 느슨
    )
    momentum_block_reasons = []
    if momentum_signal:
        sw_reasons.append(f"🚀 급등 모멘텀 매수 시그널 (점수 {sw_score}, vol {vol_ratio:.0f}%, +{change:.1f}%)")

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
        "momentum_signal": momentum_signal,
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


_AI_SYSTEM_STATIC = """당신은 한국 주식 시장 전문 AI 투자 분석가입니다.

## 분석 원칙
- 데이터 기반의 간결하고 핵심적인 분석을 제공합니다
- 섹터별 트렌드, 매크로 환경, 수급 동향을 종합적으로 고려합니다
- 투자 조언은 참고용임을 명심하고, 불확실성을 솔직하게 표현합니다
- 한국어로 답변하며, 핵심만 간결하게 작성합니다
- 절대로 다른 연도를 추측해서 표기하지 마세요 (학습 컷오프 주의)
- 데이터 부족 시 "데이터 부족"이라고 명시
- 종목 코드보다 종목명 우선 사용
- 숫자는 천 단위 콤마, 변동률은 %로

## 한국 시장 구조
- 거래시간: 09:00 ~ 15:30 KST (점심 휴장 X, 동시호가 제외)
- 코스피: 대형주, 외국인 비중 ↑, 주요 산업재
- 코스닥: 중소형/성장주, 변동성 ↑, 바이오/IT 비중 ↑
- 휴장일 (2026): 어린이날 5/5, 부처님오신날 5/25 (대체), 지방선거 6/3, 추석 9/24~9/25, 한글날 10/9, 크리스마스 12/25, 종가일 12/31

## 주요 섹터 특성
- **반도체** (삼성전자, SK하이닉스): 미국 SOXX/엔비디아 동조, 환율 영향 큼
- **조선** (HD한국조선해양, 삼성중공업): 수주 사이클, 친환경 선박 트렌드, 후행 지표
- **방산** (한화에어로, LIG넥스원): 수주 잔고 안정적, 지정학 리스크 시 강세
- **원전** (두산에너빌리티, HD현대): SMR 트렌드, AI 데이터센터 전력 수요 호재
- **바이오** (셀트리온, 삼성바이오로직스, 보령): 변동성 큼, 임상/FDA 승인 영향
- **화학/2차전지** (LG화학, 포스코퓨처엠, 한화솔루션): 전기차 수요/원자재 가격 영향
- **금융** (KB금융, 신한지주, 미래에셋증권): 금리 사이클, 부동산 PF 노출
- **자동차** (현대차, 기아): 환율 약세 시 수출 유리
- **재생에너지** (한화솔루션, OCI): 정책 / 미국 IRA 영향

## 매크로 영향 가이드
- USD/KRW 1380↑: 수출주 유리 (반도체/조선/자동차)
- USD/KRW 1250↓: 내수·소비주 유리
- VIX 30↑: 방어주 (필수소비재/배당주) 선호
- VIX 18↓: 성장주/IT 선호 가능
- 미 10년물 금리 4.5↑: 성장주 부담, 가치·금융 유리
- 미 10년물 금리 3.5↓: 성장주/리츠 유리
- WTI 80↑: 정유/에너지 강세, 항공·소비재 부담
- WTI 60↓: 항공/소비재 유리
- 공포탐욕지수 25↓ (극도공포): 역발상 매수 기회
- 공포탐욕지수 75↑ (극도탐욕): 차익실현 권장
- 코스피 -2%↓ (당일): 자동매수 중단 권장
- 외국인 순매수 +50억↑ (개별종목): 강한 매수세 신호
- 외국인 순매도 -50억↓: 이탈 주의

## 사용자 자동매매 룰 (참고)
- 매수 임계 스윙 점수: 65 (5/4 변경)
- 급등 모멘텀 매수: +3~+5% 안전대만 (5/6 추가, 추격매수 차단)
- 매도: +6% 절반 / +10% 전량 익절 / -4% 손절 / 5거래일 강제
- 종목당 200만원 / 일일 5종목·1,000만원 / 같은 종목 하루 1회

## 자주 보는 지표 해석
- PER: 8↓ 매우 저렴 / 12↓ 저렴 / 15↓ 적정 / 20↑ 비싼 편
- PBR: 0.8↓ 자산보다 싸게 / 1.2↓ 저렴 / 1.5↓ 적정
- ROE: 15%↑ 수익성 매우 좋음 / 10%↑ 안정 / 5%↑ 평균
- RSI: 30↓ 과매도 (반등 가능) / 70↑ 과매수 (조정 가능)
- 부채비율: 50%↓ 매우 양호 / 200%↑ 부담
- 거래량 비율: 200%↑ 강한 매수세 / 150%↑ 활발 / 100% 평소
"""


def _ai_system_dynamic() -> str:
    """AI 시스템 프롬프트 — 동적 부분 (오늘 날짜)."""
    today = _now_kst().strftime("%Y년 %m월 %d일")
    return f"오늘 날짜: {today} (한국시간). 이 날짜를 기준으로 분석하세요."


def _ai_system_messages() -> list:
    """캐싱 적용된 system 메시지 (Anthropic SDK 형식).

    정적 부분(약 1500토큰)에 ephemeral cache_control 적용. 5분 TTL.
    같은 mode/회차 내 여러 AI 호출 시 캐시 히트 → 비용 ~90% 절감.
    """
    return [
        {
            "type": "text",
            "text": _AI_SYSTEM_STATIC,
            "cache_control": {"type": "ephemeral"},
        },
        {
            "type": "text",
            "text": _ai_system_dynamic(),
        },
    ]


def _ai_system() -> str:
    """기존 호환성 — 캐싱 X 단순 string. 신규 코드는 _ai_system_messages() 사용."""
    return _ai_system_dynamic() + "\n\n" + _AI_SYSTEM_STATIC


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
            system=_ai_system_messages(),
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
            system=_ai_system_messages(),
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
            system=_ai_system_messages(),
            messages=[{"role": "user", "content": prompt}],
        )
        return resp.content[0].text.strip()
    except Exception as e:
        print(f"  [AI] 매크로 분석 실패: {e}")
        return ""


def ai_sell_advisor(stock_info: dict, mood: dict, sell_reason: str, pct: float, days: int) -> str:
    """매도 트리거 직전 AI 의견 조회 (참고용 — 자동 매도 룰은 그대로 진행).

    텔레그램 매도 알림에 1~2 문장 의견 추가. 1~2주 운영 후 신뢰도 검증되면
    AI 의견을 자동 매도 결정에 반영하는 단계로 진화 가능.

    Args:
        stock_info: {"name", "code", "sector", "buy_price", "curr_price"}
        mood: 시장 데이터 (kospi_chg, vix, fg_score)
        sell_reason: "+6.5% 절반 익절", "-4.2% 손절" 등
        pct: 손익률 %
        days: 보유 거래일

    Returns:
        1~2 문장 의견 (빈 문자열이면 호출 실패).
    """
    client = _get_ai_client()
    if not client:
        return ""
    try:
        prompt = (
            f"보유 종목: {stock_info.get('name', '?')} ({stock_info.get('sector', '')})\n"
            f"매수가 {stock_info.get('buy_price', 0):,.0f}원 → 현재가 {stock_info.get('curr_price', 0):,.0f}원 ({pct:+.1f}%)\n"
            f"보유: {days}거래일 / 매도 트리거: {sell_reason}\n\n"
            f"시장 상황:\n"
            f"- 코스피: {mood.get('kospi_chg', 0):+.2f}%\n"
            f"- VIX: {mood.get('vix', 20):.1f}\n"
            f"- 공포탐욕: {mood.get('fg_score', 50)}\n\n"
            f"위 매도 트리거에 따라 자동 매도 예정. 시장 상황·손익·보유 기간 종합해서 "
            f"매도가 적절한지 1~2문장으로 의견.\n"
            f"형식: \"매도 적절 — 짧은 이유\" 또는 \"보류 검토 — 짧은 이유\""
        )
        resp = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=150,
            system=_ai_system_messages(),
            messages=[{"role": "user", "content": prompt}],
        )
        return resp.content[0].text.strip()
    except Exception as e:
        print(f"  [AI 매도] 호출 오류: {e}")
        return ""


def ai_trade_journal(stock_info: dict, hold_days: int, pct: float,
                      sell_reason: str, mood: dict,
                      past_journals: list = None) -> str:
    """매도 후 AI 자동 회고 — '왜 이겼나/졌나' 1줄 일기.

    핵심: 과거 일기를 함께 전달 → AI가 패턴 학습 → 다음 매매 인사이트 누적.
    거래 이력에 누적되어 사용자 + AI 둘 다 학습 자료로 활용.

    Args:
        stock_info: {"name", "sector", "buy_price", "curr_price", "buy_time", "sell_time"}
        hold_days: 보유 거래일
        pct: 수익률 %
        sell_reason: 매도 사유 (예: "+6.0% 절반 익절")
        mood: 시장 데이터 (kospi_chg, vix, fg_score)
        past_journals: 최근 5건 일기 (AI 학습 컨텍스트용, [{name, pct, journal}, ...])

    Returns:
        1~2 문장 회고. 빈 문자열이면 호출 실패/AI 비활성.
    """
    client = _get_ai_client()
    if not client:
        return ""
    try:
        # 과거 일기 컨텍스트 (학습 효과 — AI가 비슷한 패턴 떠올리며 통찰)
        past_text = ""
        if past_journals:
            past_lines = []
            for j in past_journals[:5]:
                past_lines.append(
                    f"  - {j.get('name', '')} {j.get('pct', 0):+.1f}% "
                    f"({j.get('journal', '')[:60]})"
                )
            if past_lines:
                past_text = "\n\n[최근 5건 매매 일기 — 패턴 참고]\n" + "\n".join(past_lines)

        # 결과 분류
        if pct >= 6.0:
            outcome = "익절 성공"
        elif pct <= -3.5:
            outcome = "손절"
        elif pct > 0:
            outcome = "소폭 수익"
        else:
            outcome = "소폭 손실"

        prompt = (
            f"[방금 매도한 매매 데이터]\n"
            f"종목: {stock_info.get('name', '?')} ({stock_info.get('sector', '')})\n"
            f"매수가 {stock_info.get('buy_price', 0):,.0f}원 ({stock_info.get('buy_time', '')}) → "
            f"매도가 {stock_info.get('curr_price', 0):,.0f}원 ({stock_info.get('sell_time', '')})\n"
            f"수익률: {pct:+.2f}% ({outcome}) / 보유 {hold_days}거래일 / {sell_reason}\n\n"
            f"[당시 시장]\n"
            f"코스피 {mood.get('kospi_chg', 0):+.2f}% / VIX {mood.get('vix', 20):.1f} / "
            f"공포탐욕 {mood.get('fg_score', 50)}\n"
            f"{past_text}\n\n"
            f"위 매매를 1~2문장으로 회고하세요. 핵심: '무엇이 잘 됐고/잘못됐나'를 짚고, "
            f"가능하면 과거 일기와 연결해 패턴 발견.\n"
            f"형식: '✅ 잘된 점 — 핵심' 또는 '⚠️ 아쉬운 점 — 핵심'"
        )
        resp = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=200,
            system=_ai_system_messages(),
            messages=[{"role": "user", "content": prompt}],
        )
        return resp.content[0].text.strip()
    except Exception as e:
        print(f"  [AI 일기] 호출 오류: {e}")
        return ""


def track_advisor_outcomes() -> int:
    """AI 어드바이저 로그의 5일 전 의견들 결과 추적 (정확도 평가).

    daily(08:00) 또는 close_summary에서 호출 → 5일 전 매도들의 현재가 비교.
    Returns: 갱신된 건수
    """
    log = _load_advisor_log()
    if not log:
        return 0

    today = _now_kst()
    cutoff = (today - timedelta(days=AI_ADVISOR_OUTCOME_DAYS)).strftime("%Y-%m-%d")
    updated = 0

    for entry in log:
        if entry.get("ai_correct") is not None:
            continue  # 이미 평가됨
        entry_date = entry.get("date", "")
        if entry_date > cutoff:
            continue  # 5일 미만 — 아직 평가 X

        try:
            code = entry.get("code", "")
            sell_price = entry.get("sell_price", 0)
            if not code or sell_price <= 0:
                continue

            # 현재가 조회 (5일 후 가격)
            info = _kis.get_price(code) if _kis.available() else {}
            cur = _safe_float(info.get("stck_prpr")) if info else 0
            if cur <= 0:
                continue

            outcome_pct = (cur - sell_price) / sell_price * 100
            entry["outcome_price"] = round(cur)
            entry["outcome_date"]  = today.strftime("%Y-%m-%d")
            entry["outcome_pct"]   = round(outcome_pct, 2)

            # AI 정확도 판정
            #   "hold" (보류) → 5일 후 +1% 이상이면 정확 (안 팔길 잘했다)
            #   "sell" (매도) → 5일 후 -1% 이상 하락이면 정확 (잘 팔았다)
            cls = entry.get("opinion_class", "neutral")
            if cls == "hold":
                entry["ai_correct"] = outcome_pct >= 1.0
            elif cls == "sell":
                entry["ai_correct"] = outcome_pct <= -1.0
            else:
                entry["ai_correct"] = abs(outcome_pct) < 1.0  # neutral은 정체일 때 맞음
            updated += 1
        except Exception as e:
            print(f"  [advisor] 결과 추적 오류 ({entry.get('name', '?')}): {e}")

    if updated > 0:
        _save_advisor_log(log)
        print(f"  [advisor] {updated}건 결과 평가 완료")

    return updated


def _calc_mdd_from_portfolio(window_days: int = 30) -> dict:
    """portfolio_history.json 기반 MDD (Maximum Drawdown) 계산.

    Returns: {"mdd_pct", "mdd_amount", "peak_date", "trough_date", "current_dd_pct"}
    """
    try:
        history = _load_portfolio_history(days=window_days)
        if not history or len(history) < 2:
            return {"mdd_pct": 0, "mdd_amount": 0, "peak_date": "", "trough_date": "", "current_dd_pct": 0}

        # total 키는 _load_portfolio_history 반환 형식에 따라 다름. 일반적으로 total/value/auto+value
        def get_total(h):
            return h.get("total") or h.get("value") or h.get("auto_value") or 0

        peak_amount = 0
        peak_date   = ""
        max_dd      = 0
        max_dd_amt  = 0
        trough_date = ""

        for h in history:
            amt = get_total(h)
            if amt <= 0:
                continue
            if amt > peak_amount:
                peak_amount = amt
                peak_date   = h.get("date", "")
            elif peak_amount > 0:
                dd_pct = (amt - peak_amount) / peak_amount * 100
                if dd_pct < max_dd:
                    max_dd      = dd_pct
                    max_dd_amt  = amt - peak_amount
                    trough_date = h.get("date", "")

        # 현재 시점 낙폭 (피크 대비)
        last_amt = get_total(history[-1]) if history else 0
        current_dd = ((last_amt - peak_amount) / peak_amount * 100) if peak_amount > 0 else 0

        return {
            "mdd_pct":        round(max_dd, 2),
            "mdd_amount":     round(max_dd_amt),
            "peak_date":      peak_date,
            "trough_date":    trough_date,
            "current_dd_pct": round(current_dd, 2),
        }
    except Exception as e:
        print(f"  [MDD] 계산 오류: {e}")
        return {"mdd_pct": 0, "mdd_amount": 0, "peak_date": "", "trough_date": "", "current_dd_pct": 0}


# ════════════════════════════════════════════════
# 미래에셋 모의 (추천 검증용 가치주) — 2번 계좌
# ════════════════════════════════════════════════
# 5/29 Phase 2 1단계: 재무부로 이동
# load_mirae_paper / save_mirae_paper → from finance import


def mirae_paper_buy(code: str, name: str, qty: int, price: float,
                     buy_amount: float = None, source: str = "manual",
                     rec_score: int = 0) -> dict:
    """미래에셋 모의 매수 등록. 같은 종목 추가 매수 시 평단 가중평균 재계산.

    Args:
        code: 종목 코드 (6자리)
        name: 종목명
        qty: 수량
        price: 단가
        buy_amount: 실제 매수금액 (체결가 × 수량과 다를 수 있음 — 사용자 입력 우선)
        source: 매수 출처 ("manual" / "추천 Daily Top5" 등)
        rec_score: 추천 시점 점수 (검증용)
    """
    if buy_amount is None:
        buy_amount = qty * price

    data = load_mirae_paper()
    today = _today_str()

    if code in data["positions"]:
        # 추가 매수 — 평단 가중평균
        old = data["positions"][code]
        old_qty = old.get("qty", 0)
        old_amt = old.get("buy_amount", 0)
        new_qty = old_qty + qty
        new_amt = old_amt + buy_amount
        new_avg = new_amt / new_qty
        data["positions"][code] = {
            **old,
            "qty": new_qty,
            "buy_price": round(new_avg, 2),
            "buy_amount": round(new_amt),
        }
    else:
        data["positions"][code] = {
            "name":         name,
            "qty":          qty,
            "buy_price":    round(price, 2),
            "buy_date":     today,
            "buy_time":     _now_kst().strftime("%H:%M"),
            "buy_amount":   round(buy_amount),
            "partial_sold": False,
            "rec_date":     today if source != "manual" else "",
            "rec_score":    rec_score,
            "peak_pct":     0.0,  # 트레일링 스톱용 (최고 수익률 추적)
        }

    data["history"].append({
        "date":   today,
        "time":   _now_kst().strftime("%H:%M"),
        "side":   "buy",
        "code":   code,
        "name":   name,
        "qty":    qty,
        "price":  round(price, 2),
        "amount": round(buy_amount),
        "source": source,
    })

    save_mirae_paper(data)
    return data["positions"][code]


def mirae_paper_sell(code: str, qty: int, price: float, reason: str = "수동 매도") -> dict:
    """미래에셋 모의 매도 등록.

    Args:
        code: 종목 코드
        qty: 매도 수량 (보유 ≤이면 부분, =이면 전량 → 포지션 제거)
        price: 매도 단가
        reason: 매도 사유 (예: "+10% 1차 익절", "사용자 결정")
    """
    data = load_mirae_paper()
    if code not in data["positions"]:
        return {"ok": False, "msg": "보유 종목 아님"}

    p = data["positions"][code]
    held = p.get("qty", 0)
    if qty > held:
        qty = held  # 안전 — 보유보다 많이 매도 X

    bp     = p.get("buy_price", 0)
    profit = round((price - bp) * qty)
    pct    = round((price - bp) / bp * 100, 2) if bp else 0
    today  = _today_str()

    data["history"].append({
        "date":      today,
        "time":      _now_kst().strftime("%H:%M"),
        "side":      "sell",
        "code":      code,
        "name":      p.get("name", code),
        "qty":       qty,
        "price":     round(price, 2),
        "amount":    round(price * qty),
        "buy_price": bp,
        "profit":    profit,
        "pct":       pct,
        "reason":    reason,
    })

    if qty == held:
        # 전량 매도 → 포지션 제거
        del data["positions"][code]
    else:
        data["positions"][code] = {
            **p,
            "qty":          held - qty,
            "partial_sold": True,
        }

    save_mirae_paper(data)
    return {"ok": True, "profit": profit, "pct": pct}


def check_mirae_paper_alerts(send_telegram: bool = True) -> list:
    """미래에셋 모의 매도시점 도달 종목 추출 + 알림.

    가치주 룰 적용: -7% 손절 / +10% 1차 / +20% 2차 / +40% 장기.
    각 종목별로 도달 즉시 1번만 알림 (peak_pct 추적으로 중복 방지).
    """
    data = load_mirae_paper()
    if not data.get("positions"):
        return []

    alerts = []
    for code, p in list(data["positions"].items()):
        # 시세 조회
        try:
            info = _kis.get_price(code) if _kis.available() else {}
            cur_price = _safe_float(info.get("stck_prpr")) if info else 0
        except Exception:
            cur_price = 0
        if cur_price <= 0:
            continue

        bp  = p.get("buy_price", 0)
        if not bp:
            continue
        pct      = (cur_price - bp) / bp * 100
        peak_pct = p.get("peak_pct", 0)
        partial  = p.get("partial_sold", False)
        name     = p.get("name", code)

        # peak_pct 갱신 (최고 수익률 추적)
        if pct > peak_pct:
            data["positions"][code]["peak_pct"] = round(pct, 2)
            peak_pct = pct

        alert = None
        if pct <= -PAPER_MIRAE_STOP_LOSS_PCT * 100:
            alert = {"type": "🔴 손절", "name": name, "code": code, "pct": pct,
                     "cur_price": cur_price, "buy_price": bp,
                     "msg": f"-7% 도달 — 즉시 매도 권장"}
        elif pct >= PAPER_MIRAE_TARGET3_PCT * 100:
            alert = {"type": "🏆 장기 목표", "name": name, "code": code, "pct": pct,
                     "cur_price": cur_price, "buy_price": bp,
                     "msg": f"+40% 장기 목표 도달 — 분할 매도 검토"}
        elif pct >= PAPER_MIRAE_TARGET2_PCT * 100:
            alert = {"type": "🟢 2차 목표", "name": name, "code": code, "pct": pct,
                     "cur_price": cur_price, "buy_price": bp,
                     "msg": f"+20% 도달 — 잔여 전량 매도 권장"}
        elif pct >= PAPER_MIRAE_TARGET1_PCT * 100 and not partial:
            alert = {"type": "🟢 1차 목표", "name": name, "code": code, "pct": pct,
                     "cur_price": cur_price, "buy_price": bp,
                     "msg": f"+10% 도달 — 절반 매도 권장 (잔여는 +20% 까지 보유)"}

        if alert:
            alerts.append(alert)

    # peak_pct 갱신 저장
    save_mirae_paper(data)

    # 텔레그램 발송
    if send_telegram and alerts:
        lines = [f"<b>📊 [미래에셋 모의 — 추천 검증]</b>", ""]
        for a in alerts:
            lines.append(
                f"{a['type']} <b>{a['name']}</b> ({a['pct']:+.2f}%)\n"
                f"  매수가 {a['buy_price']:,.0f}원 → 현재 {a['cur_price']:,.0f}원\n"
                f"  💡 {a['msg']}"
            )
        tg_send("\n".join(lines))

    return alerts


def analyze_trading_performance(window_days: int = 30) -> dict:
    """positions.json history 기반 매매 결과 자가 분석. (Phase 2 — 자가 학습 인프라)

    매수↔매도 매칭하여 종목별 수익률/보유일/매도 사유 계산.
    daily 08:00에 호출되어 ai_personal_coach 프롬프트에 통계 전달 → AI가 학습 결과 반영.

    B3 (5/6): MDD / 섹터별 / 보유일별 / TOP 종목 추가 — 자가학습 인프라 완성.
    데이터 30건+ 쌓이면 swing_score 가중치 자동 조정으로 확장 (B4).
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
                        "code": code,
                        "sector": buy.get("sector", "") or h.get("sector", ""),
                        "swing_score": score,
                        "pnl_pct": pnl_pct,
                        "hold_days": hold_days,
                        "sell_reason": h.get("reason", ""),
                        "buy_time":  buy.get("time", ""),
                        "sell_time": h.get("time", ""),
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

        # B3: 섹터별 승률
        sector_stats = {}
        for r in results:
            sec = r.get("sector") or "기타"
            sector_stats.setdefault(sec, []).append(r)
        sector_perf = []
        for sec, lst in sector_stats.items():
            if len(lst) < 2:  # 1건은 통계 의미 X
                continue
            sec_wins = sum(1 for r in lst if r["pnl_pct"] > 0)
            sec_avg  = sum(r["pnl_pct"] for r in lst) / len(lst)
            sector_perf.append({
                "sector":   sec,
                "trades":   len(lst),
                "win_rate": sec_wins / len(lst) * 100,
                "avg_pnl":  sec_avg,
            })
        sector_perf.sort(key=lambda x: -x["win_rate"])

        # B3: 보유일별 승률 (1일 / 2~3일 / 4~5일)
        hold_buckets = {"1일": [], "2-3일": [], "4-5일": [], "6일+": []}
        for r in results:
            d = r["hold_days"]
            if d <= 1:   hold_buckets["1일"].append(r)
            elif d <= 3: hold_buckets["2-3일"].append(r)
            elif d <= 5: hold_buckets["4-5일"].append(r)
            else:        hold_buckets["6일+"].append(r)
        hold_perf = []
        for label, lst in hold_buckets.items():
            if not lst:
                continue
            wr = sum(1 for r in lst if r["pnl_pct"] > 0) / len(lst) * 100
            hold_perf.append({"range": label, "trades": len(lst), "win_rate": wr})

        # B3: 최고 / 최악 종목 TOP 3
        sorted_by_pnl = sorted(results, key=lambda r: -r["pnl_pct"])
        top_winners = sorted_by_pnl[:3]
        top_losers  = sorted_by_pnl[-3:][::-1]  # 역순 (가장 큰 손실 먼저)

        # B3+ 자가학습: 매수/매도 시간대별 승률 (사용자 의도 — 주가 언제 오르고 내리는지 파악)
        def _hour_bucket(time_str: str) -> str:
            if not time_str or ":" not in time_str:
                return ""
            try:
                h = int(time_str.split(":")[0])
            except Exception:
                return ""
            if 9 <= h < 11:   return "09-11시 (개장)"
            if 11 <= h < 13:  return "11-13시 (점심)"
            if 13 <= h < 15:  return "13-15시 (오후)"
            if 15 <= h < 16:  return "15시+ (마감)"
            return ""

        # 매수 시간대별 승률
        buy_hour_stats: dict = {}
        for r in results:
            bucket = _hour_bucket(r.get("buy_time", ""))
            if not bucket:
                continue
            buy_hour_stats.setdefault(bucket, []).append(r)
        buy_hour_perf = []
        for bucket, lst in buy_hour_stats.items():
            hour_wins = sum(1 for r in lst if r["pnl_pct"] > 0)
            hour_avg  = sum(r["pnl_pct"] for r in lst) / len(lst)
            buy_hour_perf.append({
                "bucket":   bucket,
                "trades":   len(lst),
                "win_rate": round(hour_wins / len(lst) * 100, 1),
                "avg_pnl":  round(hour_avg, 2),
            })
        # 시간 순 정렬
        bucket_order = ["09-11시 (개장)", "11-13시 (점심)", "13-15시 (오후)", "15시+ (마감)"]
        buy_hour_perf.sort(key=lambda x: bucket_order.index(x["bucket"]) if x["bucket"] in bucket_order else 99)

        # 매도 시간대별 승률
        sell_hour_stats: dict = {}
        for r in results:
            bucket = _hour_bucket(r.get("sell_time", ""))
            if not bucket:
                continue
            sell_hour_stats.setdefault(bucket, []).append(r)
        sell_hour_perf = []
        for bucket, lst in sell_hour_stats.items():
            hour_wins = sum(1 for r in lst if r["pnl_pct"] > 0)
            hour_avg  = sum(r["pnl_pct"] for r in lst) / len(lst)
            sell_hour_perf.append({
                "bucket":   bucket,
                "trades":   len(lst),
                "win_rate": round(hour_wins / len(lst) * 100, 1),
                "avg_pnl":  round(hour_avg, 2),
            })
        sell_hour_perf.sort(key=lambda x: bucket_order.index(x["bucket"]) if x["bucket"] in bucket_order else 99)

        # B3: MDD (portfolio_history 기반)
        mdd = _calc_mdd_from_portfolio(window_days=window_days)

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
            # B3 추가
            "sector_perf": sector_perf,    # [{sector, trades, win_rate, avg_pnl}, ...]
            "hold_perf":   hold_perf,      # [{range, trades, win_rate}, ...]
            "top_winners": top_winners,    # 상위 3건
            "top_losers":  top_losers,     # 하위 3건
            "mdd":         mdd,            # MDD 정보
            "score_buckets": {             # 점수대별 (이미 있던 것 정리)
                "70+":   {"trades": len(bucket_70), "win_rate": _wr(bucket_70)},
                "65-69": {"trades": len(bucket_65), "win_rate": _wr(bucket_65)},
                "60-64": {"trades": len(bucket_60), "win_rate": _wr(bucket_60)},
            },
            # B3+ 자가학습 — 시간대별 매매 성과 (주가 언제 오르고 내리는지)
            "buy_hour_perf":  buy_hour_perf,
            "sell_hour_perf": sell_hour_perf,
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


def _parse_coach_response(text: str) -> tuple:
    """ai_personal_coach 응답을 (holdings_diagnosis_dict, personal_brief_md) 로 분리.

    응답에 JSON 블록 (```json ... ```)이 있으면 종목 진단으로 추출,
    JSON 블록 외 부분만 personal_brief로 반환.
    """
    if not text:
        return {}, ""
    diagnosis = {}
    cleaned = text
    # ```json ... ``` 블록 찾기 (중괄호 또는 일반 텍스트 모두 가능)
    m = re.search(r"```json\s*(\{.*?\})\s*```", text, re.DOTALL)
    if m:
        try:
            diagnosis = json.loads(m.group(1))
        except Exception:
            diagnosis = {}
        # JSON 블록 + 그 앞 헤더(예: "**1. 종목별 진단 JSON**" 등) 제거
        cleaned = re.sub(r"\*\*1\.[^\n]*\n+```json\s*\{.*?\}\s*```\s*", "", text, flags=re.DOTALL)
        cleaned = re.sub(r"```json\s*\{.*?\}\s*```\s*", "", cleaned, flags=re.DOTALL)
        # "**2. 시장 코멘트 + 추천**" 헤더 제거 (자연스럽게)
        cleaned = re.sub(r"\*\*2\.[^\n]*\*\*\s*\n*", "", cleaned)
    return diagnosis, cleaned.strip()


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
            f"답변 형식 (반드시 두 부분):\n\n"
            f"**1. 종목별 진단 JSON** — 가치주 보유 종목별 한 줄 진단 (28자 이내, 핵심만).\n"
            f"형식: ```json\\n{{ \"종목명\": \"진단 텍스트\" }}\\n```\n"
            f"예: {{ \"두산에너빌리티\": \"원전 테마 강세. 홀드.\", \"보령\": \"본전 근처. 모니터링.\" }}\n\n"
            f"**2. 시장 코멘트 + 추천** — 보유 진단은 위 JSON에서 끝났으니 여기서 반복 X.\n"
            f"📊 시장 / 🎯 추천 종목 / ⚠️ 주의 / 🎬 오늘 행동 1줄. 마크다운 헤더와 표 활용.\n\n"
            f"가이드:\n"
            f"- 한국어, 비개발자 친화 (전문 용어는 풀어서)\n"
            f"- 가치주 추천: 섹터 분포 고려\n"
            f"- 자동매매 추천: 점수/시그널/매크로 위주, 가치주 섹터와 별개\n"
            f"- '사세요/팔지 마세요' 단정 X\n"
            f"- 너무 길지 않게 (한 화면). 핵심 3~5가지\n"
            f"- 마지막에 '오늘 행동 제안 1줄' 추가"
        )

        resp = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=max_tokens,
            system=_ai_system_messages(),
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
            system=_ai_system_messages(),
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
            system=_ai_system_messages(),
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
# 5/29 Phase 2 4단계 2차: card_html → dashboard.py로 이동
# from dashboard import card_html


# 5/29 Phase 2 4단계 4차-C: dart_alerts_section_html -> dashboard.py


# ════════════════════════════════════════════════
# HTML 전체 리포트
# ════════════════════════════════════════════════
# 5/29 Phase 2 4단계 4차-B: _make_macro_html -> dashboard.py


# 5/29 Phase 2 4단계 2차: _dashboard_css → dashboard.py로 이동
# from dashboard import _dashboard_css


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


# 5/29 Phase 2 4단계 6차: _make_total_summary_section -> dashboard.py


# Phase 2 4단계 3차 (5/29): _make_value_holdings_section / _make_auto_positions_section
# → dashboard.py 로 이동. import 블록(상단 from dashboard import ...) 참조.


# Phase 2 4단계 4차-A (5/29): _make_b4_learning_card / _make_advisor_stats_card / _make_compare_card -> dashboard.py


# 5/29 Phase 2 4단계 6차: _make_alerts_section -> dashboard.py


# Phase 2 4단계 3차 (5/29): _make_paper_mirae_section → dashboard.py 로 이동.


# Phase 2 4단계 4차-A (5/29): _make_trade_history_card / _make_performance_card -> dashboard.py


# 5/29 Phase 2 4단계 6차: _make_tomorrow_picks_section -> dashboard.py


# 5/29 Phase 2 4단계: 대시보드부로 이동
# _empty_section → from dashboard import (아래 import 블록)


# 5/29 Phase 2 4단계 5차: _make_hero_header -> dashboard.py


# 5/29 Phase 2 4단계 6차: _make_allocation_card -> dashboard.py


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


# 5/29 Phase 2 4단계 4차-C: _make_market_briefing_card -> dashboard.py


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


# 5/29 Phase 2 4단계 4차-C: _make_risk_gauge_html -> dashboard.py


# 5/29 Phase 2 4단계 4차-C: _make_disclosures_card -> dashboard.py


# Phase 2 4단계 4차-A (5/29): _make_portfolio_history_card -> dashboard.py


# 5/29 Phase 2 4단계 4차-B: _make_personal_coach_card -> dashboard.py


# 5/29 Phase 2 4단계 4차-B: _make_ai_card -> dashboard.py


# 5/29 Phase 2 4단계 4차-B: _make_macro_card -> dashboard.py


# 5/29 Phase 2 4단계 2차: _make_recommend_card → dashboard.py로 이동
# from dashboard import _make_recommend_card


# 5/29 Phase 2 4단계: 대시보드부로 이동
# _make_short_term_card → from dashboard import


# 5/29 Phase 2 4단계: 대시보드부로 이동
# _make_mid_term_card → from dashboard import


# 5/29 Phase 2 4단계: 대시보드부로 이동
# _make_long_term_card / _make_avoid_card → from dashboard import


# 5/29 Phase 2 4단계 4차-C: _make_dart_card -> dashboard.py


# 5/29 Phase 2 4단계 5차: _make_sidebar -> dashboard.py


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


def calc_weight_recommendations() -> dict:
    """B4 자가학습 — 매매 데이터 누적 분석 → 가중치 자동 조정 권장 (#5).

    데이터 30건+ 누적 시 활성화. positions.json history 기반.
    분석 항목:
      1. 점수 임계 (70+ vs 60-64 승률 차이 → SWING_SCORE_MIN 조정)
      2. 섹터 우대/회피 (승률 70%+ → 우대 / 30% 미만 → 회피)
      3. 시간대 회피 (특정 시간대 승률 30% 미만 → 매수 회차 조정)

    Returns: {
        "ready": True if 30건+ else False,
        "trades": 누적 건수,
        "remaining": 활성화까지 남은 건수,
        "win_rate": 전체 승률,
        "recommendations": [
            {"type", "reason", "current", "recommended", "level"}, ...
        ],
    }
    """
    perf = analyze_trading_performance(window_days=90)
    total = perf.get("trades", 0)

    base = {
        "trades":      total,
        "remaining":   max(0, B4_MIN_SAMPLES - total),
        "win_rate":    perf.get("win_rate", 0),
        "ready":       total >= B4_MIN_SAMPLES,
        "recommendations": [],
    }

    if total < B4_MIN_SAMPLES:
        return base

    recs = []

    # 1) 점수 임계 조정 권장
    sb = perf.get("score_buckets", {})
    bucket_70 = sb.get("70+", {})
    bucket_60 = sb.get("60-64", {})
    if bucket_70.get("trades", 0) >= 5 and bucket_60.get("trades", 0) >= 5:
        wr_70 = bucket_70.get("win_rate", 0)
        wr_60 = bucket_60.get("win_rate", 0)
        gap = wr_70 - wr_60
        if gap >= B4_GAP_HIGH:
            recs.append({
                "type":        "score_threshold",
                "level":       "high",
                "title":       f"매수 점수 임계 {SWING_SCORE_MIN} → 70 권장",
                "reason":      f"70점+ 승률 {wr_70:.0f}% vs 60-64점 {wr_60:.0f}% (차이 +{gap:.0f}%p)",
                "current":     SWING_SCORE_MIN,
                "recommended": 70,
                "code_var":    "SWING_SCORE_MIN",
            })
        elif wr_60 - wr_70 >= B4_GAP_HIGH:
            # 역으로 60점대가 더 잘 나오는 이상 케이스
            recs.append({
                "type":        "score_threshold_lower",
                "level":       "medium",
                "title":       f"매수 점수 임계 완화 검토",
                "reason":      f"60-64점 승률 {wr_60:.0f}% > 70점+ {wr_70:.0f}% — 표본 더 필요",
                "current":     SWING_SCORE_MIN,
                "recommended": SWING_SCORE_MIN,
                "code_var":    "SWING_SCORE_MIN",
            })

    # 2) 섹터별 권장
    sector_perf = perf.get("sector_perf", [])
    weak_sectors = [s for s in sector_perf
                    if s.get("win_rate", 0) < B4_WEAK_WIN_RATE
                    and s.get("trades", 0) >= B4_SECTOR_MIN_TRADES]
    strong_sectors = [s for s in sector_perf
                      if s.get("win_rate", 0) >= B4_STRONG_WIN_RATE
                      and s.get("trades", 0) >= B4_SECTOR_MIN_TRADES]

    for s in weak_sectors[:3]:
        recs.append({
            "type":     "sector_avoid",
            "level":    "medium",
            "title":    f"🚫 {s['sector']} 섹터 회피 권장",
            "reason":   f"승률 {s['win_rate']:.0f}% ({s['trades']}건) — 평균 손익 {s.get('avg_pnl', 0):+.1f}%",
            "sector":   s["sector"],
        })
    for s in strong_sectors[:3]:
        recs.append({
            "type":   "sector_boost",
            "level":  "low",
            "title":  f"🎯 {s['sector']} 섹터 우대 권장",
            "reason": f"승률 {s['win_rate']:.0f}% ({s['trades']}건) — 평균 손익 {s.get('avg_pnl', 0):+.1f}%",
            "sector": s["sector"],
        })

    # 3) 시간대별 권장
    bhp = perf.get("buy_hour_perf", [])
    weak_hours = [h for h in bhp
                  if h.get("win_rate", 0) < B4_WEAK_WIN_RATE
                  and h.get("trades", 0) >= B4_HOUR_MIN_TRADES]
    for h in weak_hours[:2]:
        recs.append({
            "type":   "hour_avoid",
            "level":  "low",
            "title":  f"⏰ {h['bucket']} 매수 회차 검토",
            "reason": f"승률 {h['win_rate']:.0f}% ({h['trades']}건) — 평균 손익 {h.get('avg_pnl', 0):+.1f}%",
            "bucket": h["bucket"],
        })

    # 4) 보유일 권장 (특정 보유일이 다른 것보다 현저히 낮으면)
    hp = perf.get("hold_perf", [])
    if len(hp) >= 2:
        worst = min(hp, key=lambda x: x.get("win_rate", 0))
        best  = max(hp, key=lambda x: x.get("win_rate", 0))
        if worst != best and worst.get("trades", 0) >= 3:
            wr_gap = best.get("win_rate", 0) - worst.get("win_rate", 0)
            if wr_gap >= B4_GAP_HIGH:
                recs.append({
                    "type":   "hold_days",
                    "level":  "low",
                    "title":  f"⏱️ 보유 {best['range']} 우수, {worst['range']} 부진",
                    "reason": f"{best['range']} 승률 {best['win_rate']:.0f}% / {worst['range']} {worst['win_rate']:.0f}% (차이 {wr_gap:.0f}%p)",
                })

    base["recommendations"] = recs
    return base


def _calc_bot_kospi_compare(days: int = 30) -> dict:
    """봇 자동매매 누적 수익률 vs 코스피 누적 변동률 (#3).

    portfolio_history.json (auto_total/auto_cost) → 봇 일별 수익률
    yfinance ^KS11 → 코스피 일별 종가 → 시작일 기준 변동률
    두 라인 차트로 비교 → 봇이 코스피보다 잘 하는지 시각화.

    Returns: {
        "labels":   ["5/6", "5/7", ...],
        "bot_pct":  [0, 0.5, 1.2, ...],  # 봇 누적 수익률 %
        "kospi_pct":[0, -0.2, 0.8, ...], # 코스피 누적 변동률 %
        "bot_last": 마지막 봇 수익률,
        "kospi_last": 마지막 코스피 변동률,
        "alpha":    초과수익 (bot - kospi) %p,
        "days":     데이터 일수,
    }
    """
    try:
        history = _load_portfolio_history(days=days)
        if not history or len(history) < 2:
            return {}

        # 자동매매가 첫 시작된 시점부터 (auto_cost > 0)
        start_idx = 0
        for i, h in enumerate(history):
            if h.get("auto_cost", 0) > 0:
                start_idx = i
                break
        history = history[start_idx:]
        if len(history) < 2:
            return {}

        # 봇 일별 누적 수익률 (auto_pnl / auto_cost)
        labels = []
        bot_pct = []
        for h in history:
            date = h.get("date", "")
            labels.append(date[5:].replace("-", "/"))
            cost = h.get("auto_cost", 0)
            pnl  = h.get("auto_pnl", 0)
            pct  = (pnl / cost * 100) if cost > 0 else 0
            bot_pct.append(round(pct, 2))

        # 코스피 데이터 (yfinance) — 같은 기간
        kospi_pct = []
        try:
            n = len(history)
            kos = yf.Ticker("^KS11").history(period=f"{n + 10}d")
            if not kos.empty:
                closes = kos["Close"].tolist()
                # 봇 데이터 일수만큼만 추출 (마지막 N개)
                closes = closes[-n:]
                if len(closes) >= 2 and closes[0] > 0:
                    start_kospi = closes[0]
                    kospi_pct = [round((c - start_kospi) / start_kospi * 100, 2) for c in closes]
        except Exception as e:
            print(f"  [compare] kospi 조회 오류: {e}")

        # kospi_pct 길이가 봇 길이와 다르면 패딩
        if len(kospi_pct) < len(bot_pct):
            kospi_pct = [0] * (len(bot_pct) - len(kospi_pct)) + kospi_pct
        elif len(kospi_pct) > len(bot_pct):
            kospi_pct = kospi_pct[-len(bot_pct):]

        bot_last   = bot_pct[-1] if bot_pct else 0
        kospi_last = kospi_pct[-1] if kospi_pct else 0

        return {
            "labels":     labels,
            "bot_pct":    bot_pct,
            "kospi_pct":  kospi_pct,
            "bot_last":   bot_last,
            "kospi_last": kospi_last,
            "alpha":      round(bot_last - kospi_last, 2),
            "days":       len(history),
        }
    except Exception as e:
        print(f"  [compare] 계산 오류: {e}")
        return {}


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
    holdings_diagnosis: dict = None,
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
        if holdings_diagnosis is None:  holdings_diagnosis = _dc.get("holdings_diagnosis") or {}
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
        value_html = _make_value_holdings_section(
            holdings_alerts or [], holdings_sparklines or {}, holdings_diagnosis or {}
        )
        auto_html = _make_auto_positions_section(auto_positions)
        # 미래에셋 모의 (2번 계좌 — 추천 검증용)
        paper_mirae_html = _make_paper_mirae_section()
        # 사전 매수 후보 (tomorrow_picks)
        try:
            tp_data = _load_tomorrow_picks()
        except Exception:
            tp_data = {}
        tomorrow_html = _make_tomorrow_picks_section(tp_data)
        # 봇 성적표 (B3)
        try:
            perf_data = analyze_trading_performance(window_days=30)
        except Exception:
            perf_data = {}
        performance_html = _make_performance_card(perf_data)
        # 거래 이력 (사용자 메모 대체용 — 매수/매도 자동 누적)
        trades_html = _make_trade_history_card(limit=30)
        # 알림 센터 (텔레그램 다이어트 후 정보성 알림 모음)
        alerts_html = _make_alerts_section()
        # 봇 vs 코스피 비교 (#3)
        try:
            compare_data = _calc_bot_kospi_compare(days=30)
        except Exception:
            compare_data = {}
        compare_html = _make_compare_card(compare_data)
        # AI 매도 어드바이저 신뢰도 (#4 — B1 진화)
        advisor_html = _make_advisor_stats_card()
        # 자가학습 가중치 권장 (#5 — B4)
        learning_html = _make_b4_learning_card()
        allocation_html = _make_allocation_card(holdings_alerts or [], auto_positions)
        market_html = _make_market_briefing_card(mood, fg, history)
        macro_html = _make_macro_card(macro, ai_macro, history)
        ai_html = _make_ai_card(ai_summary, ai_sector)
        coach_html = _make_personal_coach_card(personal_brief, risk)
        history_html = _make_portfolio_history_card(portfolio_history or [])
        disclosures_html = _make_disclosures_card(disclosures or [])
        # 5/14: 4트랙 진짜 알고리즘 — 트랙별 독립 점수
        swing_top = _load_swing_top3()
        short_top = _load_short_term_top3()
        mid_top   = _load_mid_term_top3()
        long_top  = _load_long_term_top3()
        # 스윙 카드 — 빈 경우 kr_top 폴백 (4트랙 점수 미산정 시 호환)
        recommend_html = _make_recommend_card(swing_top or kr_top, ai_insights)
        short_term_html = _make_short_term_card(short_top)
        mid_term_html = _make_mid_term_card(mid_top)
        long_term_html = _make_long_term_card(long_top)
        avoid_html = _make_avoid_card(avoid or [])
        dart_html = _make_dart_card(dart_alerts or [])
        sidebar_html = _make_sidebar(sections_status, last_update)

        # ── 차트 데이터 JSON inject ─────
        try:
            history_json = json.dumps(history, ensure_ascii=False)
        except Exception:
            history_json = "{}"
        try:
            compare_json = json.dumps(compare_data, ensure_ascii=False)
        except Exception:
            compare_json = "{}"

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
<script>window._chartData = {history_json}; window._compareData = {compare_json};</script>
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
<!-- 5/29 대시보드 재정리 — 정보 계층 명확 + 중복 제거 -->
<!-- ① 매일 첫 확인: 자산 + 손익 + 코치 한 줄 -->
{hero_html}
{coach_html}

<!-- ② 오늘 매수 후보 (4트랙 추천) — 회장 결정 우선 -->
{recommend_html}
{short_term_html}
{mid_term_html}
{long_term_html}
{tomorrow_html}

<!-- ③ 보유 종목 현황 -->
{value_html}
{paper_mirae_html}
{auto_html}
{allocation_html}

<!-- ④ 봇 성적표 + 시장 환경 -->
{performance_html}
{compare_html}
{history_html}
{market_html}
{macro_html}

<!-- ⑤ AI 분석 + 공시 통합 (중복 제거: dart_html은 alerts_html에 흡수, disclosures_html이 메인 공시) -->
{ai_html}
{disclosures_html}
{alerts_html}

<!-- ⑥ 부수 정보 + 경고 -->
{avoid_html}
{advisor_html}
{learning_html}
{trades_html}
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
# 5/29 Phase 2 3단계: 알림부(notify.py)로 이동
# _tg_base / _balance_html_tags / tg_send → from notify import (아래 import 블록)


# ════════════════════════════════════════════════
# 대시보드 알림 센터 (alerts.json) — 정보성 알림 누적
# ════════════════════════════════════════════════
# 5/29 Phase 2 1단계: 재무부로 이동
# _load_alerts / _save_alerts → from finance import (별칭으로 옛 이름 유지)


# 5/29 Phase 2 3단계: 알림부로 이동
# log_alert → from notify import


# 5/29 Phase 2 3단계: 알림부로 이동
# tg_send_document → from notify import


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


# ════════════════════════════════════════════════════════════
# 5/29 영구 차단 — 텔레그램 수동 매도 통보 자동 처리
# ════════════════════════════════════════════════════════════
# 사고: 카카오게임즈 5/19 회장 손매도 → 봇 시스템 갱신 안 됨 → 10일간 손절 알림 반복
# 원인: 봇 텔레그램 polling이 모니터링 시간만 → 평시 메시지 처리 안 됨
# fix: 자동매수/매도 시작 시 최근 메시지 처리 (30분 내 자동 갱신)
#
# 인식 패턴 (자연어):
#   "카카오게임즈 5월19일 104주 -11.71% 손절했어"
#   "삼성전자 100주 매도 +3.5%"
#   "/매도 카카오게임즈 5/19 104 -11.71"
_TG_OFFSET_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".tg_offset.json")


def _load_tg_offset() -> int:
    try:
        if os.path.exists(_TG_OFFSET_FILE):
            with open(_TG_OFFSET_FILE, "r", encoding="utf-8") as f:
                return int(json.load(f).get("offset", 0))
    except Exception:
        pass
    return 0


def _save_tg_offset(offset: int) -> None:
    try:
        with open(_TG_OFFSET_FILE, "w", encoding="utf-8") as f:
            json.dump({"offset": offset}, f)
    except Exception:
        pass


def _parse_sell_message(text: str) -> dict:
    """자연어 매도 메시지 파싱.

    Returns: {"name", "qty", "pct", "date", "is_loss"} 또는 빈 dict
    필요한 필드 누락 시 빈 dict 반환.
    """
    # 종목명 추출 — 메시지에서 한글 단어 (영문/숫자 종목코드 포함)
    # 단순화: KR_STOCKS / mirae_paper / positions 중 *메시지에 포함된* 첫 종목명
    name_found = None

    # 1) mirae_paper의 보유 종목 우선 (가치주 모의)
    try:
        with open(MIRAE_PAPER_FILE, encoding="utf-8") as f:
            mp = json.load(f)
        for code, p in mp.get("positions", {}).items():
            n = p.get("name", "")
            if n and n in text:
                name_found = ("mirae_paper", code, n)
                break
    except Exception:
        pass

    # 2) positions (자동매매 한투 모의)
    if not name_found:
        try:
            with open(POSITIONS_FILE, encoding="utf-8") as f:
                pos = json.load(f)
            for code, p in pos.get("positions", {}).items():
                n = p.get("name", "")
                if n and n in text:
                    name_found = ("positions", code, n)
                    break
        except Exception:
            pass

    # 3) holdings_local.json (가치주 실 사본)
    if not name_found:
        holdings_local_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "holdings_local.json"
        )
        try:
            if os.path.exists(holdings_local_path):
                with open(holdings_local_path, encoding="utf-8") as f:
                    hl = json.load(f)
                for h in hl.get("holdings", []):
                    n = h.get("name", "")
                    if n and n in text:
                        name_found = ("holdings_local", h.get("ticker", ""), n)
                        break
        except Exception:
            pass

    if not name_found:
        return {}

    # 수량 (\d+주)
    qty_m = re.search(r"(\d{1,5})\s*주", text)
    qty = int(qty_m.group(1)) if qty_m else None

    # 수익률 [+-]?\d+\.?\d*% — 음수 강조 (-11.71%, -5%, +3.5%)
    pct_m = re.search(r"([+-]?\d+\.?\d*)\s*%", text)
    pct = float(pct_m.group(1)) if pct_m else None

    # 매도일 — M월D일 또는 M/D 또는 YYYY-MM-DD
    date_iso = None
    now_kst = _now_kst()
    md1 = re.search(r"(\d{1,2})\s*월\s*(\d{1,2})\s*일", text)
    md2 = re.search(r"(\d{1,2})/(\d{1,2})(?!\d)", text)
    ymd = re.search(r"(202\d)-(\d{1,2})-(\d{1,2})", text)
    if ymd:
        date_iso = f"{ymd.group(1)}-{int(ymd.group(2)):02d}-{int(ymd.group(3)):02d}"
    elif md1:
        date_iso = f"{now_kst.year}-{int(md1.group(1)):02d}-{int(md1.group(2)):02d}"
    elif md2:
        date_iso = f"{now_kst.year}-{int(md2.group(1)):02d}-{int(md2.group(2)):02d}"
    else:
        date_iso = now_kst.strftime("%Y-%m-%d")

    # 손절/익절 자동 판단 (pct 부호로)
    is_loss = pct is not None and pct < 0

    # 액션 키워드 확인 — 매도/손절/익절/팔았 중 하나 필수
    has_sell_kw = any(kw in text for kw in ("매도", "손절", "익절", "팔았", "매각", "정리"))
    if not has_sell_kw:
        return {}

    # 5/29 영구 차단: 전량 매도 키워드 인식 (회장 5/29 사고 — 부분 매도 잘못 입력)
    # "전량/전부/다/모두/싹" 키워드 → full_sell=True → qty 무시하고 보유 전량 매도
    full_sell = any(kw in text for kw in ("전량", "전부", "모두", "싹", "다 팔", "전체", "모조리"))

    return {
        "account":   name_found[0],   # mirae_paper / positions / holdings_local
        "code":      name_found[1],
        "name":      name_found[2],
        "qty":       qty,
        "pct":       pct,
        "date":      date_iso,
        "is_loss":   is_loss,
        "full_sell": full_sell,
    }


def _apply_sell_to_account(parsed: dict) -> str:
    """파싱된 매도 정보를 해당 계좌 파일에 적용.

    Returns: 처리 결과 메시지 (텔레그램 답신용)
    """
    account = parsed.get("account")
    code    = parsed.get("code")
    name    = parsed.get("name")
    qty     = parsed.get("qty")
    pct     = parsed.get("pct")
    date    = parsed.get("date")

    if account == "mirae_paper":
        try:
            with open(MIRAE_PAPER_FILE, encoding="utf-8") as f:
                d = json.load(f)
            p = d.get("positions", {}).get(code)
            if not p:
                return f"❌ mirae_paper에 {name} 없음 (이미 매도?)"

            buy_price = p["buy_price"]
            held_qty  = p["qty"]
            full_sell = parsed.get("full_sell", False)

            # 5/29 영구 차단: 전량 키워드 OR 수량 미입력 → 무조건 전량 매도
            if full_sell or qty is None:
                sell_qty = held_qty
                full_sell = True  # 통일 처리
            else:
                sell_qty = min(qty, held_qty)
                # 입력 수량 > 보유 수량 → 경고
                if qty > held_qty:
                    return (
                        f"⚠️ <b>수량 불일치 경고</b>\n"
                        f"  {name} 입력 {qty}주 vs 보유 {held_qty}주\n"
                        f"  전량 매도가 맞다면 '{name} 전량 매도 {pct:+.2f}%' 입력"
                    )

            # 매도가 계산 (pct가 있으면 pct로, 없으면 buy_price와 동일 가정)
            if pct is not None:
                sell_price = round(buy_price * (1 + pct / 100), 0)
            else:
                sell_price = buy_price
                pct = 0.0

            profit = round(sell_qty * (sell_price - buy_price), 0)

            # history 기록
            d.setdefault("history", []).append({
                "date": date,
                "side": "sell",
                "code": code,
                "name": name,
                "qty": sell_qty,
                "buy_price": buy_price,
                "sell_price": sell_price,
                "pct": round(pct, 2),
                "profit": profit,
                "reason": "회장 수동 매도 (텔레그램 자동 등록)",
                "manual": True,
            })

            # 잔여 처리
            remaining = held_qty - sell_qty
            if remaining > 0:
                p["qty"] = remaining
                p["partial_sold"] = True
            else:
                del d["positions"][code]

            with open(MIRAE_PAPER_FILE, "w", encoding="utf-8") as f:
                json.dump(d, f, ensure_ascii=False, indent=2)

            # 5/29 답신 강조: cross-check 정보 명시 (회장이 잔여 보고 이상 감지 가능)
            emoji = "🔴" if profit < 0 else "🟢"
            cross_check = (
                f"\n💰 cross-check:\n"
                f"  매수 {held_qty}주 @ {buy_price:,.0f}원 = {held_qty * buy_price:,.0f}원\n"
                f"  매도 {sell_qty}주 @ {sell_price:,.0f}원 = {sell_qty * sell_price:,.0f}원\n"
                f"  잔여 {remaining}주"
            )
            warning = ""
            if remaining > 0:
                warning = (
                    f"\n\n⚠️ <b>부분 매도로 등록됨</b>\n"
                    f"  전량 매도였다면 '{name} 전량 매도 {pct:+.2f}%' 추가 입력"
                )
            return (
                f"{emoji} <b>미래에셋 모의 매도 등록</b>\n"
                f"  {name} ({pct:+.2f}%)\n"
                f"  손익: {profit:+,.0f}원"
                + cross_check + warning
            )
        except Exception as e:
            return f"❌ mirae_paper 갱신 오류: {e}"

    elif account == "positions":
        # 자동매매는 봇이 자동 처리 — 회장 수동 매도는 드물지만 가능
        try:
            with open(POSITIONS_FILE, encoding="utf-8") as f:
                d = json.load(f)
            p = d.get("positions", {}).get(code)
            if not p:
                return f"❌ positions에 {name} 없음"
            buy_price = p["buy_price"]
            held_qty  = p["qty"]
            sell_qty  = qty or held_qty
            sell_qty  = min(sell_qty, held_qty)
            sell_price = round(buy_price * (1 + (pct or 0) / 100), 0)
            profit = round(sell_qty * (sell_price - buy_price), 0)

            d.setdefault("history", []).append({
                "date": date, "side": "sell", "code": code, "name": name,
                "qty": sell_qty, "buy_price": buy_price, "sell_price": sell_price,
                "pct": round(pct or 0, 2), "profit": profit,
                "reason": "회장 수동 매도 (텔레그램 자동 등록)", "manual": True,
            })
            remaining = held_qty - sell_qty
            if remaining > 0:
                p["qty"] = remaining
                p["partial_sold"] = True
            else:
                del d["positions"][code]
            with open(POSITIONS_FILE, "w", encoding="utf-8") as f:
                json.dump(d, f, ensure_ascii=False, indent=2)
            return f"🟢 자동매매(한투 모의) 매도 등록: {name} {sell_qty}주 / {profit:+,.0f}원"
        except Exception as e:
            return f"❌ positions 갱신 오류: {e}"

    elif account == "holdings_local":
        # 가치주 실 사본 — bot-holdings 스킬과 동기화 필요
        return (
            f"⚠️ {name}은 가치주 실 계좌(holdings_local).\n"
            f"  현재 자동 등록 미지원 — bot-holdings 스킬로 수동 갱신 필요"
        )

    return f"❌ 알 수 없는 계좌: {account}"


def handle_pending_telegram_messages() -> int:
    """대기 중인 텔레그램 메시지 처리 (자동매수/매도 시작 시 호출).

    매도 명령(자연어/슬래시) 자동 인식 → 해당 계좌 파일 갱신 + 답신.
    /정지, /재개 등 기본 명령도 처리.

    Returns: 처리된 메시지 수
    """
    if not TELEGRAM_TOKEN:
        return 0

    offset = _load_tg_offset()
    try:
        updates = tg_get_updates(offset)
    except Exception:
        return 0

    if not updates:
        return 0

    handled = 0
    for upd in updates:
        new_offset = upd["update_id"] + 1
        offset = max(offset, new_offset)
        msg = upd.get("message", {})
        text = (msg.get("text") or "").strip()
        chat_id = str(msg.get("chat", {}).get("id", ""))
        if not text:
            continue

        # 기본 슬래시 명령
        if text in ("/정지", "/halt", "/stop"):
            pos = load_positions()
            pos["halted"] = True
            save_positions(pos)
            tg_send("🛑 <b>자동매매 정지됨.</b> 해제: /재개", chat_id)
            handled += 1
            continue
        elif text in ("/재개", "/resume"):
            pos = load_positions()
            pos["halted"] = False
            save_positions(pos)
            tg_send("▶️ <b>자동매매 재개됨.</b>", chat_id)
            handled += 1
            continue

        # 매도 키워드 포함 시 자연어 파싱 시도
        if any(kw in text for kw in ("매도", "손절", "익절", "팔았", "매각", "정리")):
            parsed = _parse_sell_message(text)
            if parsed:
                result = _apply_sell_to_account(parsed)
                tg_send(result, chat_id)
                handled += 1
                continue

    _save_tg_offset(offset)
    return handled


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


def _next_trading_day(d: datetime) -> datetime:
    """다음 거래일 반환 (주말+휴장일 건너뜀)."""
    nxt = d + timedelta(days=1)
    while not _is_trading_day(nxt):
        nxt += timedelta(days=1)
    return nxt


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

    tg_send("📡 실시간 모니터링 종료", silent=True)
    print("[모니터] 종료")


# ════════════════════════════════════════════════
# 브리핑 함수 (스케줄별)
# ════════════════════════════════════════════════

# 미국 섹터 ETF → 한국 관련 섹터 매핑 (Phase 3)
# 미국 섹터 강세/약세가 다음 거래일 한국 동조 섹터에 영향.
US_SECTOR_TO_KR = {
    "SOXX": ["반도체", "IT"],            # 필라델피아 반도체 — 한국 반도체 강한 동조
    "XLK":  ["IT", "통신", "AI"],         # 미국 기술
    "XLF":  ["금융", "은행", "보험"],     # 미국 금융
    "XLE":  ["에너지", "정유"],           # 미국 에너지
    "XLV":  ["바이오", "제약", "헬스"],   # 미국 헬스케어
    "XLI":  ["조선", "방산", "기계"],     # 미국 산업재
    "XLB":  ["화학", "소재", "철강"],     # 미국 소재
    "XLY":  ["자동차", "유통"],           # 미국 임의소비재
}


def _fetch_us_sector_changes() -> dict:
    """미국 주요 섹터 ETF 변동률 수집 (전일 대비, %)."""
    results: dict = {}
    for etf in US_SECTOR_TO_KR.keys():
        try:
            hist = yf.Ticker(etf).history(period="2d")
            if len(hist) >= 2:
                chg = (hist["Close"].iloc[-1] - hist["Close"].iloc[-2]) / hist["Close"].iloc[-2] * 100
                results[etf] = round(chg, 2)
        except Exception as e:
            print(f"  [usclose] {etf} 수집 실패: {e}")
    return results


def _calc_sector_weights(etf_changes: dict) -> dict:
    """미국 섹터 ETF 변동률 → 한국 섹터 가중치 (-5 ~ +5).

    +2.5%↑ → +5 / +1.5~2.5 → +3 / +0.5~1.5 → +1
    -2.5%↓ → -5 / -1.5~-2.5 → -3 / -0.5~-1.5 → -1
    """
    kr_weights: dict = {}
    for etf, chg in etf_changes.items():
        if   chg >= 2.5:  w = 5
        elif chg >= 1.5:  w = 3
        elif chg >= 0.5:  w = 1
        elif chg <= -2.5: w = -5
        elif chg <= -1.5: w = -3
        elif chg <= -0.5: w = -1
        else:             w = 0
        if w == 0:
            continue
        for sector in US_SECTOR_TO_KR.get(etf, []):
            existing = kr_weights.get(sector, 0)
            # 동부호 → 더 강한 값 / 이부호 → 합산 (상쇄)
            if (existing >= 0 and w >= 0) or (existing <= 0 and w <= 0):
                kr_weights[sector] = max(existing, w) if w > 0 else min(existing, w)
            else:
                kr_weights[sector] = existing + w
    return kr_weights


def _update_tomorrow_picks_sectors(sector_weights: dict, etf_changes: dict):
    """tomorrow_picks.json의 sector_weights 갱신 (오늘 = picks.date일 때만)."""
    try:
        if not os.path.exists(TOMORROW_PICKS_CACHE):
            print(f"  [usclose] tomorrow_picks.json 없음 — 섹터 가중치 스킵")
            return
        with open(TOMORROW_PICKS_CACHE, "r", encoding="utf-8") as f:
            data = json.load(f)
        # 신선도: 오늘 = picks.date여야 (어제 close에서 오늘 위해 만든 picks)
        today = _today_str()
        if data.get("date") != today:
            print(f"  [usclose] tomorrow_picks 날짜 불일치 ({data.get('date')} ≠ {today}) — 갱신 스킵")
            return
        data["sector_weights"]    = sector_weights
        data["us_etf_changes"]    = etf_changes
        data["sector_updated_at"] = _now_kst().isoformat()
        with open(TOMORROW_PICKS_CACHE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        if sector_weights:
            top = sorted(sector_weights.items(), key=lambda x: -abs(x[1]))[:5]
            print(f"  [usclose] sector_weights 갱신 — TOP: {top}")
        else:
            print(f"  [usclose] sector_weights 비어있음 (모든 섹터 변동 미미)")
    except Exception as e:
        print(f"  [usclose] sector_weights 갱신 오류: {e}")


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
        # 텔레그램 X — 대시보드 알림 센터에만 (정보성 다이어트)
        # 위험 등급 격상은 별도 risk 알림이 텔레그램 발송 (안전장치)
        detail = f"S&P {sp_chg:+.2f}% / 나스닥 {nq_chg:+.2f}% / VIX {vix_val:.2f} / 환율 {fx_val:,.0f}원"
        # 단순 mood_txt에 따라 level 결정
        if "⛔" in mood_txt or "급등" in mood_txt:
            lvl = "danger"
        elif "⚠️" in mood_txt or "급락" in mood_txt:
            lvl = "warning"
        else:
            lvl = "info"
        log_alert("briefing", lvl, "미국 마감", detail + " · " + re.sub(r"<[^>]+>", "", mood_txt), "🌙")

        # tomorrow_picks 섹터 가중치 갱신 (Phase 3) — 미국 섹터 영향 → 한국 섹터 가중치
        try:
            etf_changes = _fetch_us_sector_changes()
            if etf_changes:
                sector_weights = _calc_sector_weights(etf_changes)
                _update_tomorrow_picks_sectors(sector_weights, etf_changes)
        except Exception as e:
            print(f"  [브리핑] 섹터 가중치 처리 오류: {e}")

    except Exception as e:
        print(f"  [브리핑] 미국 브리핑 오류: {e}")
        tg_send(f"⚠️ 미국 시장 브리핑 수집 실패: {e}")

    # 대시보드 갱신 — 미국 데이터 수집 실패해도 항상 시도 (try 밖으로 분리)
    # 이래야 yfinance/외부 API 일시 장애에도 dashboard build 보장
    try:
        build_and_save_dashboard()
    except Exception as e:
        print(f"  [브리핑] 대시보드 갱신 오류: {e}")


# ════════════════════════════════════════════════════════════
# 5/14 4트랙 진짜 알고리즘 — 트랙별 독립 점수 함수
#   설계 문서: Obsidian Vault/16 - 4트랙 알고리즘 설계.md
#   각 함수: stock dict 입력 → (score, reasons) 반환. 필터 미통과 시 None.
# ════════════════════════════════════════════════════════════

def _calc_swing_score(s: dict) -> tuple | None:
    """🚀 스윙 1~5일 — 모멘텀 추격 (max 100). 필터 미통과 시 None.

    가중치: 모멘텀 60 / 기술 30 / 가치 10 (차단만)
    """
    vol_ratio   = s.get("vol_ratio", 0)
    ret_1w      = s.get("ret_1w", 0)
    macd_hist   = s.get("macd_hist", 0)
    macd_cross  = s.get("macd_cross", False)
    mktcap      = s.get("mktcap", 0)
    rsi         = s.get("rsi", 50)
    pct_from_hi = s.get("pct_from_high", 0)
    bb_pct      = s.get("bb_pct", 50)
    near_sup    = s.get("near_support", False)
    per         = s.get("per") or 0
    manipulation = s.get("manipulation_signal", False)

    # 필터 — 통과 못 하면 즉시 탈락
    # 5/29 과매수 상한 (백테스트: RSI 70+ 승률 35% / 거래량 200%+ 승률 36% — 가장 잘 지는 구간)
    if not (150 <= vol_ratio < 200): return None
    if rsi >= 70: return None
    if ret_1w < -3: return None
    if macd_hist <= 0: return None
    if mktcap < 3_000_0000_0000: return None  # 시가총액 3,000억
    if manipulation: return None

    momentum = 0
    reasons  = []

    # 모멘텀 60
    # 거래량은 필터로 150~200%로 제한됨 (200%+ 과열은 매수 제외) → 통과 시 일괄 가점
    momentum += 18; reasons.append(f"거래량 {vol_ratio:.0f}% 강세")

    if macd_hist > 0 and macd_cross: momentum += 15; reasons.append("MACD 골든+양수")
    elif macd_cross:                  momentum += 10; reasons.append("MACD 골든크로스")

    if 3 <= ret_1w <= 8:    momentum += 15; reasons.append(f"1주 +{ret_1w}% 안전 상승")
    elif 0 <= ret_1w < 3:   momentum += 10
    elif 8 < ret_1w <= 15:  momentum += 8

    if 55 <= rsi <= 70:     momentum += 5

    # 기술 30
    tech = 0
    if -10 <= pct_from_hi <= 0:        tech += 12; reasons.append("52주 고점 근접 — 돌파 후보")
    elif -20 <= pct_from_hi < -10:     tech += 6

    if bb_pct >= 80:        tech += 10; reasons.append(f"BB 상단 ({bb_pct}%)")
    elif bb_pct >= 70:      tech += 6

    if near_sup:            tech += 8

    # 가치 10 (차단만)
    value = 0 if (per and per > 100) else 10

    total = min(100, momentum + tech + value)
    return (total, reasons)


def _calc_short_term_score(s: dict) -> tuple | None:
    """📈 단기 1~3주 — 모멘텀+안정성 (max 100). 필터 미통과 시 None.

    가중치: 모멘텀 40 / 안정성 40 / 가치 20
    """
    score_base   = s.get("score", 0)
    rsi          = s.get("rsi", 50)
    vol_ratio    = s.get("vol_ratio", 0)
    macd_cross   = s.get("macd_cross", False)
    macd_hist    = s.get("macd_hist", 0)
    ret_1m       = s.get("ret_1m", 0)
    mktcap       = s.get("mktcap", 0)
    per          = s.get("per") or 0
    pbr          = s.get("pbr") or 0
    roe          = s.get("roe", 0)
    near_sup     = s.get("near_support", False)
    pct_from_lo  = s.get("pct_from_low", 0)
    manipulation = s.get("manipulation_signal", False)
    momentum_bad = s.get("momentum_bad", False)

    # 필터
    if score_base < 50: return None
    if not (40 <= rsi <= 65): return None
    if ret_1m < -10: return None
    if not macd_cross: return None
    if mktcap < 3_000_0000_0000: return None
    if manipulation or momentum_bad: return None

    reasons = []

    # 모멘텀 40
    momentum = 0
    if macd_cross and macd_hist > 0: momentum += 15; reasons.append("MACD 골든크로스 + 양수")
    elif macd_cross:                  momentum += 10
    if 45 <= rsi <= 60:                momentum += 15; reasons.append(f"RSI {rsi} 적정")
    elif 40 <= rsi < 45 or 60 < rsi <= 65: momentum += 8
    if vol_ratio >= 120:               momentum += 10
    elif vol_ratio >= 100:             momentum += 6

    # 안정성 40
    stability = 0
    if -5 <= ret_1m <= 10:             stability += 15; reasons.append(f"1개월 {ret_1m}% 안정")
    elif -10 <= ret_1m < -5 or 10 < ret_1m <= 15: stability += 8
    if near_sup:                       stability += 10; reasons.append("지지선 근처")
    if pct_from_lo <= 20:              stability += 5

    # 가치 20
    value = 0
    if per and per <= 20:              value += 10  # 시장×1.5 대략
    elif per and per <= 30:            value += 5
    if roe >= 8:                       value += 5
    if pbr and pbr <= 2:               value += 5

    total = min(100, momentum + stability + value)
    return (total, reasons)


def _calc_mid_term_score(s: dict) -> tuple | None:
    """📊 중기 1~3개월 — 펀더멘털+모멘텀 (max 100). 필터 미통과 시 None.

    가중치: 펀더 50 / 모멘텀 30 / 안전 20
    """
    score_base   = s.get("score", 0)
    per          = s.get("per") or 0
    pbr          = s.get("pbr") or 0
    roe          = s.get("roe", 0)
    div          = s.get("div", 0)
    mktcap       = s.get("mktcap", 0)
    ret_1m       = s.get("ret_1m", 0)
    ret_3m       = s.get("ret_3m", 0)
    macd_cross   = s.get("macd_cross", False)
    rsi          = s.get("rsi", 50)
    manipulation = s.get("manipulation_signal", False)
    momentum_bad = s.get("momentum_bad", False)

    # 필터
    if score_base < 55: return None
    if not (per and per <= 18):  # 시장평균 ~15 × 1.2
        return None
    if roe < 8: return None
    if mktcap < 5_000_0000_0000: return None  # 5,000억
    if ret_1m < -15 or ret_3m < -25: return None
    if manipulation or momentum_bad: return None

    reasons = []

    # 펀더 50
    fund = 0
    if per <= 10:                fund += 20; reasons.append(f"PER {per:.1f} 매우 저평가")
    elif per <= 15:              fund += 15; reasons.append(f"PER {per:.1f} 저평가")
    elif per <= 18:              fund += 8
    if pbr and pbr <= 1.0:       fund += 12; reasons.append(f"PBR {pbr:.2f} 자산 대비 저렴")
    elif pbr and pbr <= 1.5:     fund += 8
    elif pbr and pbr <= 2.0:     fund += 3
    if roe >= 15:                fund += 12; reasons.append(f"ROE {roe}% 수익성 우수")
    elif roe >= 10:              fund += 8
    elif roe >= 8:               fund += 4
    if div >= 3:                 fund += 6
    elif div >= 2:               fund += 4
    elif div >= 1:               fund += 2

    # 모멘텀 30
    momentum = 0
    if 0 <= ret_3m <= 20:        momentum += 15; reasons.append(f"3개월 +{ret_3m}% 안정 상승")
    elif -5 <= ret_3m < 0 or 20 < ret_3m <= 30: momentum += 8
    if macd_cross:               momentum += 10
    if 40 <= rsi <= 60:          momentum += 5

    # 안전 20
    safety = 0
    if mktcap >= 1_0000_0000_0000:  safety += 10; reasons.append("대형주 (시총 1조+)")
    elif mktcap >= 5_000_0000_0000: safety += 6
    # 거래대금/모멘텀 결함은 이미 필터에서 차단
    safety += 5  # 모멘텀 결함 X (필터 통과)

    total = min(100, fund + momentum + safety)
    return (total, reasons)


def _calc_long_term_score(s: dict) -> tuple | None:
    """💎 장기 3개월+ — 가치투자 헌법 (max 100). 필터 미통과 시 None.

    가중치: 가치 70 / 안전 30 / 모멘텀 0
    헌법 5단계 1차 필터 + 2차 함정 차단 그대로.
    """
    per          = s.get("per") or 0
    pbr          = s.get("pbr") or 0
    roe          = s.get("roe", 0)
    div          = s.get("div", 0)
    mktcap       = s.get("mktcap", 0)
    manipulation = s.get("manipulation_signal", False)

    # 헌법 1차 필수 통과 (전부)
    if not (0 < per <= 12): return None
    if not (0 < pbr <= 1.2): return None
    if roe < 10: return None
    if mktcap < 1_0000_0000_0000: return None  # 시가총액 1조+
    if manipulation: return None
    # 영업현금흐름 양수 — DART 데이터 있을 때만 검증 (현재 없으면 통과)
    dart = s.get("dart_financials") or {}
    if dart and dart.get("operating_cashflow") is not None:
        if dart["operating_cashflow"] <= 0:
            return None

    reasons = []

    # 가치 70
    value = 0
    if per <= 8:     value += 25; reasons.append(f"PER {per:.1f} 매우 저렴")
    elif per <= 12:  value += 18; reasons.append(f"PER {per:.1f} 저렴")
    if pbr <= 0.8:   value += 20; reasons.append(f"PBR {pbr:.2f} 자산 대비 매우 저렴")
    elif pbr <= 1.2: value += 14; reasons.append(f"PBR {pbr:.2f} 적정")
    if roe >= 15:    value += 15; reasons.append(f"ROE {roe}% 우수")
    elif roe >= 10:  value += 10
    if div >= 4:     value += 10; reasons.append(f"배당 {div}% 고배당")
    elif div >= 2:   value += 6
    elif div >= 1:   value += 2

    # 안전 30
    safety = 0
    if mktcap >= 1_0000_0000_0000:  safety += 15; reasons.append("대형주 (시총 1조+)")
    # 영업현금흐름/부채/자본잠식은 DART 데이터 있을 때 추가 (현재 없으면 기본 통과)
    safety += 15  # 기본 안전 (필터 통과 = 함정 차단 OK)

    total = min(100, value + safety)
    return (total, reasons)


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


# ─────────────────────────────────────────────────────────────
# 5/14 4트랙 로더 — market_scan_cache의 트랙별 점수 기준 TOP 3
# 기존 score 기반이 아닌 swing_score / short_score / mid_score / long_score 사용.
# 트랙 필터를 통과한 종목만 점수 보유 (그 외는 None).
# ─────────────────────────────────────────────────────────────

def _load_track_top3(track_key: str, label: str) -> list:
    """4트랙 공통 로더. track_key = 'swing_score' / 'short_score' / 'mid_score' / 'long_score'"""
    try:
        if not os.path.exists(MARKET_SCAN_CACHE):
            return []
        with open(MARKET_SCAN_CACHE, "r", encoding="utf-8") as f:
            cache = json.load(f)
        stocks = cache.get("stocks", [])

        # track_key가 None이 아닌 종목만 (= 필터 통과 종목만)
        candidates = [s for s in stocks if s.get(track_key) is not None]
        candidates.sort(key=lambda s: -s.get(track_key, 0))
        return candidates[:3]
    except Exception as e:
        print(f"  [premarket] {label} 추천 로드 오류: {e}")
        return []


def _load_swing_top3() -> list:
    """🚀 스윙 1~5일 TOP 3 (모멘텀 추격, 자동매매 대상)."""
    return _load_track_top3("swing_score", "스윙")


def _load_short_term_top3() -> list:
    """📈 단기 1~3주 TOP 3 (모멘텀+안정성)."""
    return _load_track_top3("short_score", "단기")


def _load_mid_term_top3() -> list:
    """📊 중기 1~3개월 TOP 3 (펀더멘털+모멘텀)."""
    return _load_track_top3("mid_score", "중기")


def _load_long_term_top3() -> list:
    """💎 장기 3개월+ TOP 3 (가치투자 헌법, 추천만 / 자동매매 X)."""
    return _load_track_top3("long_score", "장기")


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

        # 5/14: 4트랙 진짜 알고리즘 — 트랙별 독립 점수
        # 🚀 스윙 (1~5일, 자동매매) / 📈 단기 (1~3주, 수동)
        # 📊 중기 (1~3개월, 수동) / 💎 장기 (3개월+, 수동 / 자동매매 X)
        top3_swing = _load_swing_top3()
        top3_short = _load_short_term_top3()
        top3_mid   = _load_mid_term_top3()
        top3_long  = _load_long_term_top3()

        if top3_swing:
            lines.append("<b>🚀 스윙 TOP 3</b> <i>(1~5일, 자동매매)</i>")
            for i, s in enumerate(top3_swing, 1):
                name  = s.get("name", "?")
                sc    = s.get("swing_score", 0)
                price = s.get("price", 0)
                vol   = s.get("vol_ratio", 0)
                lines.append(f"{i}. {name} — {sc}점 / {price:,.0f}원 / 거래량 {vol:.0f}%")
            lines.append("")

        if top3_short:
            lines.append("<b>📈 단기 TOP 3</b> <i>(1~3주, 수동 매수)</i>")
            for i, s in enumerate(top3_short, 1):
                name  = s.get("name", "?")
                sc    = s.get("short_score", 0)
                price = s.get("price", 0)
                rsi   = s.get("rsi", 0)
                vol   = s.get("vol_ratio", 0)
                lines.append(f"{i}. {name} — {sc}점 / {price:,.0f}원 / RSI {rsi:.0f} / 거래량 {vol:.0f}%")
            lines.append("")

        if top3_mid:
            lines.append("<b>📊 중기 TOP 3</b> <i>(1~3개월, 수동 매수)</i>")
            for i, s in enumerate(top3_mid, 1):
                name  = s.get("name", "?")
                sc    = s.get("mid_score", 0)
                price = s.get("price", 0)
                per   = s.get("per", 0) or 0
                roe   = s.get("roe", 0)
                lines.append(f"{i}. {name} — {sc}점 / {price:,.0f}원 / PER {per:.1f} / ROE {roe:.1f}%")
            lines.append("")

        if top3_long:
            lines.append("<b>💎 장기 TOP 3</b> <i>(3개월+, 헌법 / 수동 매수만)</i>")
            for i, s in enumerate(top3_long, 1):
                name  = s.get("name", "?")
                sc    = s.get("long_score", 0)
                price = s.get("price", 0)
                per   = s.get("per", 0) or 0
                pbr   = s.get("pbr", 0) or 0
                div   = s.get("div", 0)
                lines.append(f"{i}. {name} — {sc}점 / {price:,.0f}원 / PER {per:.1f} / PBR {pbr:.2f} / 배당 {div:.1f}%")
            lines.append("")

        if not (top3_swing or top3_short or top3_mid or top3_long):
            lines.append("<i>⚠️ 추천 캐시 없음 — 16:00 시장스캔 확인 필요</i>")
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
                    system=_ai_system_messages(),
                    messages=[{"role": "user", "content": prompt}],
                )
                lines.append(f"🤖 {resp.content[0].text.strip()}")
                lines.append("")
            except Exception:
                pass

        # 대시보드 링크
        lines.append(f"📊 대시보드: {DASHBOARD_URL}")

        # 텔레그램 X — 대시보드 알림 센터에만 (정보성 다이어트)
        kos_chg = mood.get("kospi_chg", 0)
        fg_score = fg.get("score", 50)
        fg_label = fg.get("label", "중립")
        detail = f"코스피 {kos_chg:+.2f}% / VIX {mood.get('vix', 0):.1f} / 공포탐욕 {fg_score}({fg_label})"
        log_alert("briefing", "info", "장 시작 전", detail, "🔔")

        # 미래에셋 모의 매도시점 알림 (2번 계좌 — 장 시작 전 점검)
        try:
            check_mirae_paper_alerts(send_telegram=True)
        except Exception as e:
            print(f"  [브리핑] mirae_paper 알림 오류: {e}")
    except Exception as e:
        print(f"  [브리핑] 장전 브리핑 오류: {e}")
        tg_send(f"⚠️ 장전 브리핑 수집 실패: {e}")

    # 대시보드 갱신 — 데이터 수집 실패해도 항상 시도 (try 밖으로 분리)
    try:
        build_and_save_dashboard()
    except Exception as e:
        print(f"  [브리핑] 대시보드 갱신 오류: {e}")


def _pick_tomorrow_candidates() -> dict:
    """장 마감 시점 — 다음 거래일 매수 우선순위 후보 추출 + tomorrow_picks.json 저장.

    조건 (오늘 강세 + 추세 지속 시그널):
      - 오늘 +3% 이상 상승
      - 거래량 평균 대비 +150% 이상
      - 스윙 점수 50 이상
      - RSI 70 미만 (과매수 X)
      - 모멘텀 약화 X

    저장된 picks를 다음날 autobuy가 우선 분석 + score_bonus 적용.
    """
    pool = _load_auto_buy_pool()
    print(f"[tomorrow_picks] 풀 {len(pool)}종목 강세 분석 시작...")

    candidates = []
    for ticker, (name, period, sector) in pool.items():
        try:
            r = analyze(ticker, name, period, sector, with_sentiment=False)
            if not r:
                continue
            change   = r.get("change", 0) or 0
            vol_r    = r.get("vol_ratio", 0) or 0
            sw_score = r.get("swing_score", 0) or 0
            rsi      = r.get("rsi", 50) or 50

            if (change >= 3.0
                and vol_r >= 150
                and sw_score >= 50
                and rsi < 70):
                # 보너스: +3%면 +3, +10% 이상이면 상한 +10
                bonus = min(10, max(3, int(round(change))))
                candidates.append({
                    "code":          ticker.split(".")[0],
                    "name":          name,
                    "sector":        sector,
                    "score_bonus":   bonus,
                    "today_change":  round(change, 2),
                    "today_vol_ratio": round(vol_r, 0),
                    "today_score":   sw_score,
                    "reasons": [
                        f"오늘 +{change:.1f}% 마감",
                        f"거래량 평균 +{vol_r:.0f}%",
                        f"스윙점수 {sw_score}",
                    ],
                    "source": "close_summary",
                })
            time.sleep(0.4)
        except Exception as e:
            print(f"  [tomorrow_picks] {name}({ticker}) 오류: {e}")

    # 보너스 → 점수 → 거래량 순 TOP 20만
    candidates.sort(key=lambda x: (-x["score_bonus"], -x["today_score"], -x["today_vol_ratio"]))
    candidates = candidates[:20]

    next_day = _next_trading_day(_now_kst())
    data = {
        "date":           next_day.strftime("%Y-%m-%d"),
        "generated_at":   _now_kst().isoformat(),
        "source_phase":   "close_summary",
        "picks":          candidates,
        "sector_weights": {},  # usclose Phase 3에서 채움 (해외 영향 가중치)
    }

    try:
        with open(TOMORROW_PICKS_CACHE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        print(f"[tomorrow_picks] {next_day.strftime('%m/%d (%a)')} 후보 {len(candidates)}종목 저장")
        for c in candidates[:5]:
            print(f"  • {c['name']} (+{c['today_change']}%, vol {c['today_vol_ratio']:.0f}%, 점수 {c['today_score']}, 보너스 +{c['score_bonus']})")
    except Exception as e:
        print(f"[tomorrow_picks] 저장 실패: {e}")

    return data


# 5/29 Phase 2 1단계: 재무부로 이동
# _load_tomorrow_picks → from finance import (별칭으로 옛 이름 유지)


def run_close_summary():
    """3시 35분 — 장 마감 결산.

    텔레그램 다이어트 후: 텔레그램 발송 안 함. 데이터는 대시보드 갱신용.
    + 다음 거래일 매수 우선순위 후보 추출 (tomorrow_picks.json).
    """
    if _skip_if_holiday("장 마감 결산"):
        return
    print("[브리핑] 장 마감 결산 (대시보드 갱신용)")
    try:
        mood = get_market_mood()
        fg   = get_fear_greed(mood)
        # 보유종목 알림 데이터 수집 (대시보드용)
        ha = check_holdings_alerts()

        # 다음 거래일 매수 우선순위 후보 추출
        try:
            _pick_tomorrow_candidates()
        except Exception as e:
            print(f"  [브리핑] tomorrow_picks 추출 오류: {e}")

        # 미래에셋 모의 매도시점 알림 (2번 계좌)
        try:
            check_mirae_paper_alerts(send_telegram=True)
        except Exception as e:
            print(f"  [브리핑] mirae_paper 알림 오류: {e}")

        # AI 어드바이저 v2 — 5일 전 의견들의 결과 추적 (정확도 평가)
        try:
            updated = track_advisor_outcomes()
            if updated > 0:
                print(f"  [브리핑] AI 어드바이저 {updated}건 결과 평가 완료")
        except Exception as e:
            print(f"  [브리핑] advisor outcome 추적 오류: {e}")

        # B4 자가학습 — 30건 도달 시 첫 번째 알림 (1회만)
        try:
            b4 = calc_weight_recommendations()
            if b4.get("ready") and b4.get("recommendations"):
                # alerts.json에 1회만 (오늘 아직 안 보냈으면)
                today_alerts = _load_alerts()
                already_sent = any(
                    a.get("category") == "system" and "B4 자가학습" in a.get("title", "")
                    and a.get("time", "").startswith(_today_str())
                    for a in today_alerts
                )
                if not already_sent:
                    n = len(b4["recommendations"])
                    log_alert(
                        "system", "warning",
                        "🧠 B4 자가학습 권장 발견",
                        f"매매 {b4['trades']}건 누적 → 가중치 조정 권장 {n}건. 자가학습 카드에서 확인.",
                        "🧠",
                    )
                    # 텔레그램 (긴급은 아니지만 의미 있는 변화 — 1회 알림)
                    tg_send(
                        f"🧠 <b>봇 자가학습 권장</b>\n"
                        f"매매 {b4['trades']}건 누적 → 가중치 조정 권장 <b>{n}건</b>.\n"
                        f"📊 대시보드 '자가학습' 카드에서 확인 후 결정해주세요.",
                        silent=True,
                    )
        except Exception as e:
            print(f"  [브리핑] B4 학습 알림 오류: {e}")

        # 텔레그램 발송 X — 대시보드에서 확인
        print(f"  [브리핑] 코스피 {mood['kospi_chg']:+.2f}% 마감.")
    except Exception as e:
        print(f"  [브리핑] 마감 결산 오류: {e}")

    # 대시보드 갱신 — 데이터 수집 실패해도 항상 시도 (try 밖으로 분리)
    try:
        build_and_save_dashboard()
    except Exception as e:
        print(f"  [브리핑] 대시보드 갱신 오류: {e}")


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


def _check_emergency_stop(pos: dict) -> tuple:
    """비상정지 검증 — 일일 누적 손실만 (5/15 영구 차단 fix).

    회장 부재 (5/17~5/31) 안전망. 발동 시 *그날만* 자동매수 정지.
    다음 거래일 09:00 자동 재개 (회장 결정 5/13).
    매도는 항상 정상 작동 (손절/익절 자동).

    5/15 영구 fix — MDD 30일 윈도우 제거:
    - 사고: 5/13~5/15 3일 연속 비상정지 발동 (옛 손절이 30일 윈도우 안에 살아 있어 매일 재발동)
    - 원인: 매수 호출 시점에 MDD 30일 체크 → 옛 손실로 매일 재발동 → "그날만 정지" 의도와 모순
    - fix: MDD 체크 제거. 매수 호출 시점엔 *오늘 발생한 손실만* (일일 누적 -3%) 체크.
    - MDD 안전망 대체: 시장 위험 등급(70+ 자동 정지)이 시장 전체 약세 커버.
      한 종목 큰 손실은 손절 -4% + 일일 누적 -3%로 충분.

    Returns: (should_halt: bool, reason: str)
    """
    # 일일 누적 손실 -3% 체크 (오늘 매도 손익 합산)
    # 자정 지나면 자연스럽게 0부터 시작 → "그날만 정지" 의도와 정확 일치
    try:
        today = _today_str()
        today_sells = [
            h for h in pos.get("history", [])
            if h.get("side") == "sell" and h.get("date") == today
        ]
        if today_sells:
            today_loss_amt = sum(
                h.get("profit", 0) for h in today_sells if h.get("profit", 0) < 0
            )
            # 시드머니 = 일일 한도 SWING_MAX_DAILY_AMT (1,000만원)
            seed = SWING_MAX_DAILY_AMT
            today_loss_pct = today_loss_amt / seed * 100
            if today_loss_pct <= EMERGENCY_DAILY_LOSS_PCT:
                return True, (
                    f"일일 누적 손실 {today_loss_pct:.2f}% ≤ {EMERGENCY_DAILY_LOSS_PCT}% "
                    f"({today_loss_amt:,}원)"
                )
    except Exception as e:
        print(f"  [emergency] 일일 손익 계산 오류: {e}")

    return False, ""


def _get_dynamic_thresholds(risk_level: str) -> dict:
    """시장 위험 등급에 따라 동적 매수 임계 반환 (Regime-Adaptive, 5/13).

    회장 통찰: 시장 환경에 따라 룰 변경. 강세=공격 / 약세=보수.
    백테스트 검증 결과 (B 옵션, 27조합 × 2환경):
      - 강세장: 점수≥45 / 거래량≥100% — 최근 6개월 +1.80%
      - 중립:   점수≥50 / 거래량≥150% — 균형
      - 약세장: 점수≥55 / 거래량≥200% — 양 환경 +수익 (Robust)
      - 위험:   자동매매 정지 (기존 시스템)

    Args:
        risk_level: '안전' / '주의' / '경계' / '위험'

    Returns:
        dict {score_min, rsi_max, vol_min, label} 또는 None (위험 등급 = 정지)
    """
    if risk_level == "안전":  # 0~30: 강세장
        return {
            "score_min": 45, "rsi_max": 65, "vol_min": 100,
            "daily_buy_limit": 5, "daily_amt_limit": 10_000_000,
            "chase_max_pct": 5.0,  # 당일 +5%까지 추격 매수 OK
            "allow_momentum": True,  # momentum_signal로도 매수 가능
            "label": "🟢 강세장 (적극)", "regime": "강세"
        }
    elif risk_level == "주의":  # 30~50: 중립
        return {
            "score_min": 50, "rsi_max": 65, "vol_min": 150,
            "daily_buy_limit": 4, "daily_amt_limit": 8_000_000,
            "chase_max_pct": 3.0,
            "allow_momentum": True,
            "label": "🟡 중립 (중간)", "regime": "중립"
        }
    elif risk_level == "경계":  # 50~70: 약세장
        return {
            "score_min": 55, "rsi_max": 60, "vol_min": 200,
            "daily_buy_limit": 3, "daily_amt_limit": 6_000_000,
            "chase_max_pct": 2.0,
            "allow_momentum": False,  # 약세장: momentum_signal 차단 (swing_signal만)
            "label": "🟠 약세장 (보수)", "regime": "약세"
        }
    else:  # 위험 70+: 이미 정지 (기존 시스템)
        return None


def _check_foreign_inst_3day(code: str) -> tuple:
    """최근 3거래일 외국인/기관 순매수 합산 조회 (pykrx).

    NEW_RULES_ENABLED=true 시 매수 직전 호출. 둘 다 합산 매도세면 차단.

    Returns:
        (foreign_sum_eok, inst_sum_eok, ok)
        - foreign_sum_eok: 외국인 3일 합산 (억원, +매수 / -매도)
        - inst_sum_eok: 기관 3일 합산 (억원)
        - ok: 외국인 OR 기관 둘 중 하나라도 합산 > 0 (둘 다 음수면 False)

    데이터 부족 / API 오류 시 ok=True (기본 허용 — 매매 차단 방지)
    """
    try:
        from pykrx import stock as _krx
        end = _now_kst().strftime("%Y%m%d")
        start = (_now_kst() - timedelta(days=10)).strftime("%Y%m%d")  # 여유 10일
        df = _krx.get_market_trading_value_by_date(start, end, code)
        if df is None or df.empty or len(df) < 3:
            return 0.0, 0.0, True  # 데이터 부족 → 통과 (기본 허용)
        last3 = df.tail(3)
        # pykrx 컬럼명: "외국인합계", "기관합계" (또는 영문)
        f_col = "외국인합계" if "외국인합계" in last3.columns else last3.columns[-2]
        i_col = "기관합계" if "기관합계" in last3.columns else last3.columns[-3]
        foreign_sum = float(last3[f_col].sum()) / 1e8  # 원 → 억원
        inst_sum = float(last3[i_col].sum()) / 1e8
        ok = (foreign_sum > 0) or (inst_sum > 0)
        return round(foreign_sum, 1), round(inst_sum, 1), ok
    except Exception as e:
        print(f"  [3day check] {code} 오류: {e} — 기본 허용")
        return 0.0, 0.0, True  # 오류 시 통과 (기본 허용 / 안전)


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

        # 5/14: 4트랙 스윙 점수 우선 정렬 — _calc_swing_score 통과 종목 먼저 분석
        # 일일 한도 도달 전에 가장 유망한 스윙 후보 우선 처리.
        cache_stocks = cache.get("stocks", [])
        swing_pass = [s for s in cache_stocks if s.get("swing_score") is not None]
        swing_fail = [s for s in cache_stocks if s.get("swing_score") is None]
        swing_pass.sort(key=lambda s: -s.get("swing_score", 0))
        ordered_stocks = swing_pass + swing_fail

        added = 0
        added_swing = 0
        for s in ordered_stocks:
            t = s.get("ticker")
            if not t or t in pool:
                continue
            pool[t] = (s.get("name", t), "중기", s.get("sector", "기타"))
            added += 1
            if s.get("swing_score") is not None:
                added_swing += 1
        print(f"  [auto_buy] 종목 풀 = KR_STOCKS {len(KR_STOCKS)}개 + 시장스캔 {added}개 "
              f"(스윙 필터 통과 {added_swing}개 우선) = {len(pool)}개")
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
    # 5/29: 대기 중인 텔레그램 메시지 처리 (회장 수동 매도 통보 등)
    try:
        n = handle_pending_telegram_messages()
        if n > 0:
            print(f"  [autobuy] 대기 메시지 {n}건 처리 완료")
    except Exception as e:
        print(f"  [autobuy] 텔레그램 메시지 처리 오류: {e}")

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

    # 장 마감 후 자동매수 차단 (5/7 fix — 장 마감 후 매수 시도 → 한투 카톡 알림 무더기 방지)
    # cron-job.org가 15:45 호출하거나 GitHub Actions 지연으로 16시 후 도달 시 매수 시도 X
    if not _is_market_open(now):
        # 장 시간 외 — 매수 시도 X (KIS 카톡 알림/매수실패 메시지 방지)
        print(f"[autobuy] 장 시간 외 ({now.strftime('%H:%M')}) — 매수 스킵")
        return

    # 14:30 후 신규 매수 차단 (5/12 fix — 한국 스윙 매매 정석)
    # 사유: 장 마감 직전 매수 → 다음날 갭다운 시 익절 기회 0 + 즉시 손절 위험
    # 5/6~5/12 손절 4건 중 3건이 *14:30 후 또는 전일 늦은 시간 매수* 패턴
    # 회장 결정 (5/12): 14:30 이후 신규 매수 X / 기존 보유 모니터링은 계속
    if now.hour > 14 or (now.hour == 14 and now.minute >= 30):
        print(f"[autobuy] 14:30 후 신규 매수 차단 ({now.strftime('%H:%M')}) — 다음 거래일 09:00 재개")
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
    today = _today_str()

    # 영구 정지 (사용자 /정지 명령 등) — 회장 /재개 명령까지
    if pos.get("halted"):
        _alert(f"⏸ <b>{mode_tag} 자동매매 정지 중</b> — /재개 명령으로 해제")
        return

    # 비상정지 — 그날만 정지 (5/13 회장 결정, 다음날 자동 재개)
    # halted_until_date == 오늘 → 매수 X / 자정 지나면 자동 재개
    if pos.get("halted_until_date") == today:
        _alert(
            f"🟡 <b>{mode_tag} 비상정지 (오늘만)</b>\n"
            f"오늘 매수 X. 내일 09:00 자동 재개.\n"
            f"자동매도는 정상 작동."
        )
        return

    # 비상정지 검증 (5/13 추가, 회장 부재 안전망)
    # 일일 누적 -3% 또는 MDD -15% 도달 시 *오늘만* 정지
    should_halt, halt_reason = _check_emergency_stop(pos)
    if should_halt:
        pos["halted_until_date"] = today  # 그날만 정지 (자정 지나면 자동 해제)
        save_positions(pos)
        tg_send(
            f"🟡 <b>비상정지 (오늘만)</b> ({mode_tag})\n"
            f"{halt_reason}\n\n"
            f"오늘 자동매수 X. 내일 09:00 자동 재개.\n"
            f"자동매도는 정상 작동 (손절/익절)."
        )
        log_alert(
            "emergency", "warning",
            "비상정지 (오늘만)",
            f"{halt_reason} — 내일 자동 재개",
            "🟡",
        )
        return

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
        # 등급 악화 — 텔레그램 + 대시보드 둘 다 (긴급, 안전장치)
        reasons_html = "\n".join(f"• {r}" for r in risk['reasons']) if risk['reasons'] else ""
        tg_send(
            f"🚨 <b>시장 위험 등급 상승</b>: {last_risk_level} → <b>{risk['level']}</b> ({risk['score']}/100)\n"
            f"<b>대응:</b> {risk['action']}\n\n"
            + (f"<b>주요 위험 요인:</b>\n{reasons_html}" if reasons_html else "")
        )
        # 대시보드 알림 센터에도 기록
        reasons_text = " · ".join(risk['reasons'][:3]) if risk['reasons'] else ""
        log_alert(
            "risk", "danger",
            f"위험 등급 상승: {last_risk_level} → {risk['level']}",
            f"{risk['score']}/100 · {risk['action']}" + (f" · {reasons_text}" if reasons_text else ""),
            "🚨",
        )
    elif new_rank < old_rank:
        # 하락은 대시보드만 (정보성)
        log_alert(
            "risk", "info",
            f"위험 등급 하락: {last_risk_level} → {risk['level']}",
            f"{risk['score']}/100 — 정상화 추세",
            "✅",
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

    # tomorrow_picks 로드 (어제 장마감/미장마감 분석으로 추출된 강세 후보)
    # 풀에서 picks 종목을 앞으로 빼서 먼저 분석 (cron 지연 시에도 우선 캐치)
    tp_data = _load_tomorrow_picks()
    tp_picks = tp_data.get("picks", []) if tp_data else []
    tp_bonus_map = {p["code"]: p for p in tp_picks}  # code → pick 정보
    tp_sector_weights = tp_data.get("sector_weights", {}) if tp_data else {}  # Phase 3
    if tp_bonus_map:
        # 풀 정렬: tomorrow_picks 종목 우선, 그 다음 KR_STOCKS, 마지막 market_scan
        ordered_pool = {}
        # 1) tomorrow_picks 종목 (보너스 점수 높은 순)
        sorted_picks = sorted(tp_picks, key=lambda x: -x["score_bonus"])
        for p in sorted_picks:
            for ticker in pool:
                if ticker.split(".")[0] == p["code"]:
                    ordered_pool[ticker] = pool[ticker]
                    break
        # 2) 나머지
        for ticker, val in pool.items():
            if ticker not in ordered_pool:
                ordered_pool[ticker] = val
        pool = ordered_pool
        print(f"[자동매수] 🎯 tomorrow_picks 우선순위 적용 — {len(tp_picks)}종목 우선 분석")

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
            # tomorrow_picks 보너스 적용 (어제 강세 후보 → 점수 가산)
            code_only = ticker.split(".")[0]
            if code_only in tp_bonus_map:
                pick = tp_bonus_map[code_only]
                bonus = pick.get("score_bonus", 0)
                r["swing_score"] = r.get("swing_score", 0) + bonus
                r["from_tomorrow_picks"] = True
                r["tomorrow_pick_reasons"] = pick.get("reasons", [])
                sc = r["swing_score"]
            # 섹터 가중치 적용 (Phase 3) — 미국 동조 섹터 강세/약세 반영
            sec_w = tp_sector_weights.get(sector, 0)
            if sec_w:
                r["swing_score"] = r.get("swing_score", 0) + sec_w
                r["sector_bonus"] = sec_w
                sc = r["swing_score"]
            # swing_signal (점수≥65 + 안전조건) 또는 momentum_signal (급등 +3%/vol+200%) 둘 중 하나
            if (r.get("swing_signal") and sc >= SWING_SCORE_MIN) or r.get("momentum_signal"):
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

    # 오늘 이미 매수한 종목 (회차 간 중복 매수 차단 — 5/6 추가)
    # GitHub Actions cron 지연으로 두 회차가 거의 동시 실행 시 held가 비어 있어도
    # history는 git pull로 동기화돼 있을 가능성 ↑. 이중 안전장치.
    today_bought = {h.get("code") for h in pos.get("history", [])
                    if h.get("date") == today and h.get("side") == "buy"}

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
        if code in today_bought:
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
        # 매수 발생 X에도 대시보드 갱신 (현재가/평가손익/매도시점 거리 등 항상 최신)
        try:
            build_and_save_dashboard()
        except Exception as e:
            print(f"  [자동매수] 대시보드 갱신 오류: {e}")
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
        # 급등 모멘텀 종목은 🚀, tomorrow_picks 사전 후보는 🎯 표시
        if s.get("momentum_signal"):
            tag = "🚀 급등"
        elif s.get("from_tomorrow_picks"):
            tag = "🎯 사전 후보"
        else:
            tag = "📊 스윙"
        preview_lines.append(
            f"• {tag} <b>{s['name']}</b> ({s['swing_score']}점, {sec}) — {qty}주 약 {amt:,}원"
        )
    tg_send("\n".join(preview_lines))

    if _poll_cancel_during_sleep(SWING_PRE_ALERT_SEC):
        tg_send(f"🛑 {mode_tag} 사용자 /취소 — 자동 매수 중단")
        # 취소 플래그 정리 (이번 회차 한정)
        return

    # 매수 실행
    # Regime-Adaptive 임계 미리 1회 계산 (루프 안 매 종목 재계산 X)
    thresholds = _get_dynamic_thresholds(risk['level'])

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

        # A: 일일 한도 동적 (5/13 회장 결정) — 시장 환경 따라 조정
        # 강세 5종목/1,000만원 / 중립 4종목/800만원 / 약세 3종목/600만원
        _dyn_daily_buy = SWING_MAX_DAILY_BUY
        _dyn_daily_amt = SWING_MAX_DAILY_AMT
        if thresholds:
            _dyn_daily_buy = thresholds.get("daily_buy_limit", SWING_MAX_DAILY_BUY)
            _dyn_daily_amt = thresholds.get("daily_amt_limit", SWING_MAX_DAILY_AMT)
        if daily["buy_amount"] + amt > _dyn_daily_amt:
            tg_send(f"🛑 일일 매수 한도 도달 ({_dyn_daily_amt//10000}만원, {thresholds['label'] if thresholds else ''}) — 추가 매수 중단")
            break
        if daily["buy_count"] >= _dyn_daily_buy:
            tg_send(f"🛑 일일 종목 한도 도달 ({_dyn_daily_buy}개, {thresholds['label'] if thresholds else ''}) — 추가 매수 중단")
            break
        if daily["trade_count"] >= SWING_DAILY_TRADE_CAP:
            tg_send(f"🛑 일일 매매 횟수 한도 도달 ({SWING_DAILY_TRADE_CAP}건) — 비정상 폭주 차단")
            break

        # ── 한국 스윙 확률 룰 가드 (5/12, NEW_RULES_ENABLED 토글) ──
        # 백테스트 검증된 룰만 활성화. 검증 전 false (기본).
        if NEW_RULES_ENABLED:
            # 외국인+기관 3일 합산 매수 검증
            f_sum, i_sum, ok = _check_foreign_inst_3day(code)
            if not ok:
                print(f"  [new_rules] {s['name']} 외인 {f_sum:+.0f}억 / 기관 {i_sum:+.0f}억 — "
                      f"둘 다 3일 합산 매도세, 매수 차단")
                continue
            # 거래량 과열 차단 (analyze 결과의 vol_ratio 사용)
            vol_ratio = s.get("vol_ratio", 0)
            if vol_ratio > NEW_RULES_VOL_OVERHEAT_PCT:
                print(f"  [new_rules] {s['name']} 거래량 +{vol_ratio:.0f}% 과열 — 매수 차단")
                continue

        # ── Regime-Adaptive 동적 임계 가드 (5/13 회장 결정) ──
        # 시장 위험 등급에 따라 매수 임계 자동 조정
        # 강세=공격 (점수 45) / 중립=중간 (점수 50) / 약세=보수 (점수 55)
        # thresholds는 루프 시작 전 1회 계산 (위에서 정의)
        if thresholds:
            real_score = s.get("score", 0)  # 진짜 점수 (보너스 제외)
            if real_score < thresholds["score_min"]:
                print(f"  [regime] {s['name']} 진짜 점수 {real_score} < {thresholds['score_min']} "
                      f"({thresholds['label']}) — 매수 차단")
                continue
            vol_ratio_check = s.get("vol_ratio", 0)
            if vol_ratio_check and vol_ratio_check < thresholds["vol_min"]:
                print(f"  [regime] {s['name']} 거래량 {vol_ratio_check:.0f}% < {thresholds['vol_min']}% "
                      f"({thresholds['label']}) — 매수 차단")
                continue
            # RSI는 analyze에서 이미 차단되지만 추가 보수 (약세장 RSI<60)
            rsi_check = s.get("rsi", 50)
            if rsi_check and rsi_check >= thresholds["rsi_max"]:
                print(f"  [regime] {s['name']} RSI {rsi_check:.1f} ≥ {thresholds['rsi_max']} "
                      f"({thresholds['label']}) — 매수 차단")
                continue
            # ── C: 모멘텀 신호 차단 (약세장에서) — 5/13 회장 결정 ──
            # 약세장에서 momentum_signal(급등 추격)으로만 통과한 종목 차단
            # swing_signal(진짜 점수 + 안전조건)으로 통과한 종목만 매수
            if not thresholds.get("allow_momentum", True):
                if s.get("momentum_signal") and not s.get("swing_signal"):
                    print(f"  [regime] {s['name']} momentum_signal만 통과 — "
                          f"{thresholds['label']} 모멘텀 추격 차단")
                    continue
            # ── B: 추격매수 차단 동적 — 5/13 회장 결정 ──
            # 당일 등락률이 chase_max_pct 초과 시 차단 (강세 5% / 중립 3% / 약세 2%)
            today_change = s.get("today_change", s.get("change_pct", 0)) or 0
            chase_max = thresholds.get("chase_max_pct", 5.0)
            if today_change > chase_max:
                print(f"  [regime] {s['name']} 당일 +{today_change:.1f}% > 한도 +{chase_max}% "
                      f"({thresholds['label']}) — 추격매수 차단")
                continue

        result = client.buy(code, qty)
        if result.get("ok"):
            pos["positions"][code] = {
                "name":          s["name"],
                "qty":           qty,
                "buy_price":     price,
                "buy_date":      today,
                "buy_time":      _now_kst().strftime("%H:%M"),
                "buy_amount":    amt,
                "partial_sold":  False,
                "score":         s.get("score", 0),
                "swing_score":   s.get("swing_score", 0),
                "sector":        s.get("sector", ""),
                "order_no":      result.get("order_no", ""),
            }
            pos["history"].append({
                "date": today,
                "time": _now_kst().strftime("%H:%M"),
                "side": "buy", "code": code, "name": s["name"],
                "qty": qty, "price": price, "amount": amt,
                "reason": f"swing_score {s.get('swing_score',0)}",
                "sector": s.get("sector", ""),
                "tag": "🚀 급등" if s.get("momentum_signal") else ("🎯 사전 후보" if s.get("from_tomorrow_picks") else "📊 스윙"),
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
        # 매수 요약은 정보성 (실제 매수 체결 알림은 이미 받음) → 무음
        tg_send(
            f"📊 <b>{mode_tag} 매수 요약</b>\n"
            f"이번 회차 신규: {new_buys}종목\n"
            f"오늘 누적: {daily['buy_count']}종목 / 총 {daily['buy_amount']:,}원\n"
            f"현재 보유: {len(pos.get('positions', {}))}종목",
            silent=True,
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
    # 5/29: 대기 중인 텔레그램 메시지 처리 (회장 수동 매도 통보 등)
    try:
        n = handle_pending_telegram_messages()
        if n > 0:
            print(f"  [autosell] 대기 메시지 {n}건 처리 완료")
    except Exception as e:
        print(f"  [autosell] 텔레그램 메시지 처리 오류: {e}")

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
    ai_mood_cache = None  # 매도 발생 시 1번만 조회 (lazy)

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
        # 5/29 유연 보유 룰 (회장 통찰)
        # 🔴 하락 종목 빨리 청산: 3일 + 수익 -1% 미만 (5일 안 기다리고 자금 회수)
        elif days >= SWING_QUICK_EXIT_DAYS and pct < SWING_QUICK_EXIT_PCT:
            sell_qty = held_qty
            sell_reason = f"하락 추세 빨리 청산 ({days}일/{pct:+.1f}%)"
            is_force = True
        elif days >= SWING_MAX_HOLD_DAYS:
            # 💚 상승 추세 보유 연장: +3% 이상 + 10일 한도 → 5일 룰 무시
            # 회장 5/29 의도: 좋은 종목은 익절선(+6) 넘었어도 +10% 향해 더 보유
            extend = (
                days < SWING_MAX_HOLD_EXTENDED
                and pct >= SWING_EXTEND_MIN_PCT
            )
            if extend:
                print(f"  [{p.get('name', code)}] 💚 보유 연장 ({days}일/{pct:+.1f}%) — 상승 추세")
                continue  # 매도 X, 다음 종목으로
            # 🟡 정체: 5일 후 청산 (기존 룰)
            sell_qty = held_qty
            sell_reason = f"{days}거래일 경과 강제 매도 ({pct:+.1f}%)"
            is_force = True

        if sell_qty <= 0:
            continue
        if daily["trade_count"] >= SWING_DAILY_TRADE_CAP:
            print(f"  [자동매도] 일일 매매 한도 도달")
            break

        # AI 매도 의견 조회 (참고용 — 자동 매도 룰은 그대로 진행)
        # B1: 1~2주 운영 후 신뢰도 검증되면 의견을 자동 결정에 반영하는 단계로 진화
        ai_opinion = ""
        try:
            if ai_mood_cache is None:
                m = get_market_mood() or {}
                fg = get_fear_greed(m) if m else {"score": 50}
                m["fg_score"] = fg.get("score", 50)
                ai_mood_cache = m
            ai_opinion = ai_sell_advisor(
                stock_info={
                    "name":       p.get("name", code),
                    "code":       code,
                    "sector":     p.get("sector", ""),
                    "buy_price":  buy_price,
                    "curr_price": cur_price,
                },
                mood=ai_mood_cache,
                sell_reason=sell_reason,
                pct=pct,
                days=days,
            )
        except Exception as e:
            print(f"  [자동매도] AI 의견 조회 오류: {e}")

        # KIS API 호출 try/except로 감싸서 timeout 등 예외 시 다음 종목 진행 보장
        # (5/7 사고: 카카오뱅크 timeout 시 미래에셋 매도 commit이 누락됨 → race)
        try:
            result = client.sell(code, sell_qty)
        except Exception as e:
            tg_send(f"❌ {mode_tag} 매도 호출 오류: {p.get('name','?')} — {str(e)[:100]}")
            print(f"  [자동매도] {p.get('name','?')} sell() 예외: {e}")
            # 보유종목 잔고 없음 → KIS에서 거부 메시지
            if "잔고" in str(e) or "없습니다" in str(e):
                # 봇 기록 vs 실계좌 불일치 — 다음 회차에서 시세 조회 실패로 자연 정리
                print(f"  [자동매도] {p.get('name','?')} 잔고 불일치 — positions에서 정리")
                if code in pos["positions"]:
                    del pos["positions"][code]
                    save_positions(pos)
            continue
        if not result.get("ok"):
            tg_send(f"❌ {mode_tag} 매도 실패: {p.get('name','?')} — {result.get('msg','')}")
            # 잔고 없음 메시지 시 봇 기록 정리 (불일치 자가 회복)
            msg_lower = str(result.get("msg", ""))
            if "잔고" in msg_lower and "없" in msg_lower:
                if code in pos["positions"]:
                    del pos["positions"][code]
                    save_positions(pos)
                    print(f"  [자동매도] {p.get('name','?')} 잔고 없음 → positions에서 자동 정리")
            continue

        amt = cur_price * sell_qty
        profit = (cur_price - buy_price) * sell_qty

        # AI 트레이딩 일기 — 매도 후 1줄 회고 (학습 누적)
        # 과거 5건 일기를 함께 전달 → AI가 패턴 학습 → 다음 매매 인사이트 ↑
        journal = ""
        try:
            past_journals = _get_recent_journals(limit=5)
            journal = ai_trade_journal(
                stock_info={
                    "name":       p.get("name", code),
                    "sector":     p.get("sector", ""),
                    "buy_price":  buy_price,
                    "curr_price": cur_price,
                    "buy_time":   p.get("buy_time", ""),
                    "sell_time":  _now_kst().strftime("%H:%M"),
                },
                hold_days=days,
                pct=pct,
                sell_reason=sell_reason,
                mood=ai_mood_cache or {},
                past_journals=past_journals,
            )
        except Exception as e:
            print(f"  [자동매도] AI 일기 작성 오류: {e}")

        pos["history"].append({
            "date": today,
            "time": _now_kst().strftime("%H:%M"),
            "side": "sell", "code": code, "name": p["name"],
            "qty": sell_qty, "price": cur_price, "amount": amt,
            "reason": sell_reason, "pct": round(pct, 2),
            "sector": p.get("sector", ""),
            # 사용자 메모 대체용 — 봇이 모든 정보 자동 저장
            "buy_price": buy_price,
            "profit": round(profit),
            "ai_opinion": ai_opinion,
            "journal":    journal,  # AI 회고 (매도 후 1줄, 학습 누적용)
        })

        # AI 어드바이저 v2 — 의견 + 매도 정보 누적 (5일 후 정확도 평가용)
        try:
            log_advisor_decision(
                stock_info={
                    "code":       code,
                    "name":       p.get("name", code),
                    "curr_price": cur_price,
                },
                ai_opinion=ai_opinion,
                sell_executed=True,
                sell_pct=pct,
            )
        except Exception as e:
            print(f"  [자동매도] advisor_log 저장 오류: {e}")
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
        msg = (
            f"{emoji} <b>{p['name']}</b> {sell_qty}주 @ {cur_price:,}원 "
            f"({pct:+.1f}%) — {sell_reason}"
        )
        if ai_opinion:
            msg += f"\n  💭 AI: {ai_opinion}"
        if journal:
            msg += f"\n  📝 일기: {journal}"
        sold_msgs.append(msg)
        save_positions(pos)
        time.sleep(1)

    if sold_msgs:
        header = f"🤖 <b>{mode_tag} 자동 매도</b> ({_now_kst().strftime('%H:%M')})"
        tg_send("\n".join([header, ""] + sold_msgs))
    else:
        print(f"  [자동매도] 매도 조건 충족 종목 없음")

    # 대시보드 갱신 (매도 발생 X에도 항상 — 현재가/평가손익/매도시점 거리 항상 최신)
    try:
        build_and_save_dashboard()
    except Exception as e:
        print(f"  [자동매도] 대시보드 갱신 오류: {e}")

    # DART 공시 — 30분마다: 텔레그램(새것만 핵심) + 대시보드(오늘 전체 갱신)
    try:
        # 1) 텔레그램용: 새 공시만 (보유종목/⚠️/✅ 키워드만 발송)
        collect_new_disclosures(context_label=f"{_now_kst().strftime('%H:%M')} 자동매도")
        # 2) 대시보드용: 오늘 전체 공시 새로 받아서 캐시 갱신
        all_today = fetch_today_disclosures_for_dashboard(days=1)
        if all_today:
            try:
                _dc = _load_dashboard_cache()
                _dc["disclosures"] = all_today
                _save_dashboard_cache(_dc)
                print(f"  [자동매도] 대시보드 공시 {len(all_today)}건 갱신")
            except Exception as e:
                print(f"  [자동매도] 공시 캐시 갱신 오류: {e}")
    except Exception as e:
        print(f"  [자동매도] 공시 처리 오류: {e}")

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
            partial = p.get("partial_sold", False)  # 절반 매도 완료 여부

            # 등급 판정 — partial_sold 반영하여 다음 트리거에 맞게
            # 절반 매도 안 한 경우 → 다음 트리거 +6% (1차)
            # 절반 매도 완료 → 다음 트리거 +10% (2차)
            new_state = ""
            if not partial and 5.0 <= pnl_pct < SWING_TARGET1_PCT * 100:
                new_state = "near_target1"
            elif partial and (SWING_TARGET2_PCT * 100 - 1.0) <= pnl_pct < SWING_TARGET2_PCT * 100:
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
                        f"2차 익절 임박 (목표 +{SWING_TARGET2_PCT*100:.0f}%, 잔여 전량 매도)"
                    )
                elif new_state == "near_stop":
                    imminent_alerts.append(
                        f"🔴 <b>{p.get('name', code)}</b> {pnl_pct:+.2f}% — "
                        f"손절 임박 (기준 -{SWING_STOP_LOSS_PCT*100:.0f}%, 자동 매도)"
                    )
        except Exception:
            continue

    if imminent_alerts:
        # 텔레그램 X — 대시보드 알림 센터로 이동 (정보성 다이어트)
        for line in imminent_alerts:
            # 간단한 HTML 태그 제거
            clean = re.sub(r"<[^>]+>", "", line)
            # level 추측: 손절은 danger, 익절 임박은 info
            if "손절" in clean:
                lvl = "danger"
            else:
                lvl = "info"
            log_alert("imminent", lvl, "매매 임박", clean, "📊")
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
                system=_ai_system_messages(),
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
def _pykrx_retry(call_name: str, fn, *args, retries: int = 3, backoff: float = 1.5, **kwargs):
    """KRX API 호출 재시도 헬퍼 (5/14, 5/13 16:00 'Expecting value' JSON 오류 7회 사고 대응).

    Args:
        call_name: 로그용 함수 이름
        fn: pykrx 함수
        retries: 최대 시도 횟수 (기본 3)
        backoff: 시도 사이 지수 백오프 베이스 (기본 1.5초)

    Returns: fn 결과 또는 None (모두 실패 시)
    """
    last_err = None
    for attempt in range(retries):
        try:
            return fn(*args, **kwargs)
        except Exception as e:
            last_err = e
            if attempt < retries - 1:
                wait = backoff * (2 ** attempt)
                print(f"  [pykrx_retry] {call_name} 실패 ({attempt+1}/{retries}): {e} — {wait:.1f}초 후 재시도")
                time.sleep(wait)
    print(f"  [pykrx_retry] {call_name} 최종 실패 ({retries}회): {last_err}")
    return None


# 5/14: 시장 스캔 1회 내에서 동일 (날짜, 마켓) 조합은 1번만 호출 (사이클당 cap×2 + fund×2 = 4회)
# 600종목 × 2 호출 (cap + fund) = 1200회 → 4회. 5/13 사고 방지 + 성능 5배 개선.
_PYKRX_FUND_CACHE: dict = {}  # (date_str, mkt) → DataFrame or None
_PYKRX_CAP_CACHE:  dict = {}  # (date_str, mkt) → DataFrame or None


def _get_market_fundamental_cached(date_str: str, mkt: str):
    """get_market_fundamental_by_ticker — KRX 펀더 endpoint 사고(5/9~)로 항상 None.

    5/29: 재시도 자체가 종목당 ~4.5초 낭비(marketscan timeout 주범) → 즉시 None.
    펀더멘털(PER/PBR/ROE)은 dart_calc_per_pbr_roe가 담당. KRX 복구 시 아래 재시도 부활.
    """
    key = (date_str, mkt)
    if key in _PYKRX_FUND_CACHE:
        return _PYKRX_FUND_CACHE[key]
    _PYKRX_FUND_CACHE[key] = None
    return None


def _get_market_cap_cached(date_str: str, mkt: str):
    """get_market_cap_by_ticker 결과 캐싱 + 3회 재시도 + FDR 우회 (5/15 KRX 사고).

    KRX 'Expecting value' 사고 시 FinanceDataReader로 우회.
    """
    key = (date_str, mkt)
    if key in _PYKRX_CAP_CACHE:
        return _PYKRX_CAP_CACHE[key]

    # 5/29: KRX 시총 endpoint 사고(5/9~)로 pykrx는 항상 실패 → 재시도 건너뛰고 FDR 직행.
    # (이전엔 pykrx 3회 재시도하느라 종목당 ~4.5초 낭비 → marketscan timeout 주범)
    df = None
    if _FDR_OK:
        try:
            print(f"  [fallback] FDR로 {mkt} 시가총액 조회...")
            fdr_df = _fdr.StockListing(mkt)  # 'KOSPI' / 'KOSDAQ'
            if fdr_df is not None and not fdr_df.empty:
                # FDR 컬럼명을 pykrx 형식으로 변환
                # FDR: Code, Name, Marcap, Stocks → pykrx: index=종목코드, 시가총액
                df = pd.DataFrame({
                    "시가총액": fdr_df.set_index("Code")["Marcap"],
                })
                df = df[df["시가총액"] > 0]
                print(f"  [fallback] FDR {mkt}: {len(df)}종목 시총 데이터 확보")
        except Exception as e:
            print(f"  [fallback] FDR 시가총액 실패: {e}")

    _PYKRX_CAP_CACHE[key] = df
    return df


def _fetch_top_market_stocks_fdr(n: int = MARKET_SCAN_N) -> list:
    """5/15: FDR로 시장 시총 상위 n개 종목 리스트 (KRX 사고 우회).

    Returns: [(code, name, mkt), ...]
    """
    if not _FDR_OK:
        return []
    try:
        df_kospi  = _fdr.StockListing("KOSPI")
        df_kosdaq = _fdr.StockListing("KOSDAQ")
        if df_kospi is None or df_kosdaq is None:
            return []
        df_kospi  = df_kospi[df_kospi["Marcap"] > 0].copy()
        df_kospi["mkt"] = "KOSPI"
        df_kosdaq = df_kosdaq[df_kosdaq["Marcap"] > 0].copy()
        df_kosdaq["mkt"] = "KOSDAQ"
        df_all = pd.concat([df_kospi, df_kosdaq], axis=0)
        df_all = df_all.sort_values("Marcap", ascending=False).head(n)

        result = []
        for _, row in df_all.iterrows():
            code = row.get("Code", "")
            name = row.get("Name", code)
            mkt  = row.get("mkt", "KOSPI")
            if code and len(code) == 6:
                result.append((code, name, mkt))
        print(f"  [FDR] 시가총액 상위 {len(result)}종목 로드 (KRX 우회)")
        return result
    except Exception as e:
        print(f"  [FDR] 종목 리스트 로드 실패: {e}")
        return []


def fetch_top_market_stocks(n: int = MARKET_SCAN_N) -> list:
    """pykrx로 코스피+코스닥 시가총액 상위 n개 종목 리스트 반환 [(code, name, mkt), ...].

    5/15: KRX 전체 endpoint 사고 시 FDR 우회 (시장 종목 풀 유지 — 회장 결정).
    5/29: KRX 시총 endpoint 사고(5/9~) 장기화 → FDR 직행 (pykrx 날짜루프+재시도 ~수분 낭비 제거).
          KRX 복구 시 아래 pykrx 경로로 되돌리면 됨.
    """
    if _FDR_OK:
        return _fetch_top_market_stocks_fdr(n)

    if not _PYKRX_OK:
        print("  [pykrx] 미설치 — FDR 우회 시도")
        return _fetch_top_market_stocks_fdr(n)

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
        # 5/15: 7일 거슬러도 KRX 시가총액 X → FDR 우회
        print("  [pykrx] 시가총액 데이터 7일 모두 없음 — FDR 우회 시도")
        return _fetch_top_market_stocks_fdr(n)

    # 5/14: 3회 재시도 + 백오프 (5/13 'Expecting value' JSON 오류 7회 사고 방지)
    df_kospi  = _pykrx_retry("cap_by_ticker(KOSPI)",  _pykrx.get_market_cap_by_ticker, date_str, market="KOSPI")
    df_kosdaq = _pykrx_retry("cap_by_ticker(KOSDAQ)", _pykrx.get_market_cap_by_ticker, date_str, market="KOSDAQ")
    if df_kospi is None and df_kosdaq is None:
        # 5/15: 양 시장 모두 KRX 실패 → FDR 우회
        print("  [pykrx] 시가총액 조회 모두 실패 — FDR 우회 시도")
        return _fetch_top_market_stocks_fdr(n)

    kospi_set = set(df_kospi.index.tolist()) if df_kospi is not None else set()
    frames = [f for f in [df_kospi, df_kosdaq] if f is not None and not f.empty]
    if not frames:
        return _fetch_top_market_stocks_fdr(n)
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

        # 펀더멘털 (pykrx) — 5/14: 모듈 캐시로 동일 날짜·마켓 1회만 호출 (5/13 사고 방지)
        per = pbr = None
        div = roe = mktcap = 0.0
        try:
            fund_df = _get_market_fundamental_cached(end_str, mkt)
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

        # 시가총액 — 모듈 캐시 사용 (5/13 사고 방지)
        cap_df = _get_market_cap_cached(end_str, mkt)
        if cap_df is not None and code in cap_df.index:
            try:
                mktcap = float(cap_df.loc[code, "시가총액"])
            except Exception:
                pass

        # 5/29 최적화: DART 우선 (한국 종목 정확) → yfinance fallback만 (DART 실패 시)
        # 이전(5/15): pykrx → yfinance(항상) → DART(또) → 1398종목 × 5초 = ~2시간 타임아웃 위험
        # 신(5/29): pykrx → DART(빠름+정확) → yfinance(DART 매핑 X 종목만)
        # 효과: 종목당 호출 1초로 단축, marketscan 10분 내 완료
        if (per is None or pbr is None) and mktcap > 0:
            try:
                dart_pp = dart_calc_per_pbr_roe(code, mktcap)
                if dart_pp:
                    if per is None and "per" in dart_pp:
                        per = dart_pp["per"]
                    if pbr is None and "pbr" in dart_pp:
                        pbr = dart_pp["pbr"]
                    if roe <= 0 and "roe" in dart_pp:
                        roe = dart_pp["roe"]
            except Exception:
                pass

        # yfinance fallback — DART도 못 받은 종목만 (소형주, 미상장 등)
        # 5/29 영구 차단: yfinance .info 무한 대기 → signal.SIGALRM 3초 timeout
        # 사고 26620221277: 1시간 22분 무한 대기 → 진단 결과 yfinance 응답 없음 의심
        if per is None and pbr is None and roe <= 0:
            try:
                yf_ticker = code + (".KS" if mkt == "KOSPI" else ".KQ")
                yf_info = _yf_info_with_timeout(yf_ticker, timeout_sec=3)
                if yf_info:
                    yf_per = yf_info.get("trailingPE")
                    yf_pbr = yf_info.get("priceToBook")
                    yf_div = yf_info.get("dividendYield") or 0
                    yf_roe = yf_info.get("returnOnEquity") or 0
                    if yf_per and yf_per > 0:
                        per = round(float(yf_per), 2)
                    if yf_pbr and yf_pbr > 0:
                        pbr = round(float(yf_pbr), 2)
                    if yf_div:
                        div = round(float(yf_div) * 100, 2) if yf_div < 1 else round(float(yf_div), 2)
                    if yf_roe:
                        roe = round(float(yf_roe) * 100, 1) if yf_roe < 1 else round(float(yf_roe), 1)
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
            "macd_hist": round(macd_hist, 4),
            "near_support": sr["near_support"], "near_resistance": sr["near_resistance"],
            "manipulation_signal": manipulation_signal, "momentum_bad": momentum_bad,
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

    # 5/29: DART corpCode.xml 매핑 사전 로드 (1번만 API 호출, 1398종목 lookup 즉시)
    # 효과: marketscan 중 DART PER/PBR 계산 가능 → 단기/중기/장기 트랙 작동
    dart_load_corp_code_map()

    stocks = fetch_top_market_stocks(n)
    if not stocks:
        print("[오류] 종목 목록 로드 실패")
        return

    kr_codes = {t.split(".")[0] for t in KR_STOCKS}

    # 5/29 병렬화: 종목당 DART/KIS 호출(1~7초)을 8워커 동시 처리 → ~175분 → ~20분.
    # 사전 캐시 워밍: (date, mkt) 캐시를 단일 스레드에서 미리 채워 워커 경합 방지.
    _warm_str = _now_kst().strftime("%Y%m%d")
    for _mkt in ("KOSPI", "KOSDAQ"):
        _get_market_cap_cached(_warm_str, _mkt)
        _get_market_fundamental_cached(_warm_str, _mkt)

    targets = [(c, nm, mk) for (c, nm, mk) in stocks if c not in kr_codes]
    total   = len(targets)
    results = []
    # analyze_market_stock은 내부에서 모든 예외를 잡고 None 반환 → ex.map 중단 위험 없음.
    with ThreadPoolExecutor(max_workers=8) as ex:
        done = 0
        for r in ex.map(lambda t: analyze_market_stock(*t), targets):
            done += 1
            if done % 100 == 0 or done == 1:
                print(f"  진행: {done}/{total}")
            if r:
                results.append(r)

    # 5/14: 4트랙 점수 계산 (스윙/단기/중기/장기) — 각 종목별 추가
    for s in results:
        swing  = _calc_swing_score(s)
        short  = _calc_short_term_score(s)
        midd   = _calc_mid_term_score(s)
        longt  = _calc_long_term_score(s)
        s["swing_score"]      = swing[0]  if swing  else None
        s["swing_reasons"]    = swing[1]  if swing  else []
        s["short_score"]      = short[0]  if short  else None
        s["short_reasons"]    = short[1]  if short  else []
        s["mid_score"]        = midd[0]   if midd   else None
        s["mid_reasons"]      = midd[1]   if midd   else []
        s["long_score"]       = longt[0]  if longt  else None
        s["long_reasons"]     = longt[1]  if longt  else []

    # 5/15 fix: 4트랙 통과 종목은 *무조건 캐시 포함* — general score top50 컷에 의존 X
    # 사고: top50(general score) 안에 4트랙 통과 종목 0개 → 카드 빈 채 (회장 발견)
    track_passed = [
        r for r in results
        if (r.get("swing_score") is not None or r.get("short_score") is not None
            or r.get("mid_score") is not None or r.get("long_score") is not None)
    ]
    # general score top 100 + 4트랙 통과 종목 합집합 (ticker 중복 제거)
    results_sorted = sorted(results, key=lambda x: x["score"], reverse=True)
    top100_general = results_sorted[:100]
    combined_map = {r["ticker"]: r for r in top100_general}
    for r in track_passed:
        combined_map[r["ticker"]] = r  # 4트랙 통과 종목 추가 (이미 있어도 OK)
    top_cache = sorted(combined_map.values(), key=lambda x: x["score"], reverse=True)

    print(f"\n[스캔 완료] {len(results)}종목 분석 완료 / 캐시 {len(top_cache)}개 저장")
    # 4트랙 통과 종목 수
    n_sw = sum(1 for s in results if s.get("swing_score") is not None)
    n_sh = sum(1 for s in results if s.get("short_score") is not None)
    n_md = sum(1 for s in results if s.get("mid_score") is not None)
    n_lg = sum(1 for s in results if s.get("long_score") is not None)
    print(f"  4트랙 통과: 🚀스윙 {n_sw} / 📈단기 {n_sh} / 📊중기 {n_md} / 💎장기 {n_lg}")
    print(f"  캐시 구성: general top100 + 4트랙 통과 {len(track_passed)}개 = 합집합 {len(top_cache)}개")
    for s in top_cache[:10]:
        print(f"  {s['name']} ({s['ticker']}) -- {s['score']}점  buy={s['buy_signal']}")

    cache = {
        "updated": _now_kst().strftime("%Y-%m-%d %H:%M"),
        "count":   len(top_cache),
        "stocks":  top_cache,
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

    # DART 공시 — 텔레그램(새것만 핵심) + 대시보드(오늘 전체)
    try:
        # 1) 텔레그램용: 새 공시만 (seen 차단), 보유종목/⚠️✅ 키워드만 발송
        new_count = len(collect_new_disclosures(context_label="08:00 일일 점검"))
        if new_count:
            print(f"  [DART] 새 공시 {new_count}건 (중요만 텔레그램)")
        # 2) 대시보드용: 오늘 모든 공시 (seen 무관)
        disclosures_data = fetch_today_disclosures_for_dashboard(days=1)
        if disclosures_data:
            held_n = sum(1 for d in disclosures_data if d.get("is_held"))
            print(f"  [DART] 대시보드용 오늘 공시 {len(disclosures_data)}건 (보유 {held_n}건)")
    except Exception as e:
        print(f"  [DART] 공시 처리 오류: {e}")
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
    holdings_diagnosis = {}
    try:
        raw_brief = ai_personal_coach(
            "오늘 보유 종목별 진단(JSON) + 시장 코멘트 + 추천 + 주의 + 오늘 행동 1줄.",
            mood=mood, fg=fg, kr_top=kr_top5, ai_macro=ai_macro,
            max_tokens=1500,
        )
        # JSON 종목별 진단 + brief 분리
        holdings_diagnosis, personal_brief = _parse_coach_response(raw_brief)
        if personal_brief:
            print(f"  → 개인 코칭 생성됨 ({len(personal_brief)}자)")
        if holdings_diagnosis:
            print(f"  → 종목별 진단 {len(holdings_diagnosis)}건 (가치주 row에 표시)")
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
            "holdings_diagnosis": holdings_diagnosis,
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
            holdings_diagnosis=holdings_diagnosis,
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


# 5/29 Phase 2 3단계: 알림부로 이동
# _notify_fatal → from notify import


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
