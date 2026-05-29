"""💼 재무부 (finance.py) — Phase 2 1단계 (5/29 회장 복귀 후 시작)

설계 문서: Obsidian Vault/11 - Phase 2 설계도 (14부서).md
체크리스트: Obsidian Vault/12 - Phase 2 실행 체크리스트.md

책임: 봇이 데이터 읽고 쓰는 모든 곳 통합
- positions.json (자동매매 한투 모의)
- mirae_paper.json (가치주 추천 검증)
- alerts.json (알림 히스토리)
- tomorrow_picks.json (다음날 매수 후보)

원칙 (회장 메모리):
- Read 자유 / Write는 재무부만
- 한 부서가 다른 부서 일 직접 X (위임만)
- 평단/수량 cross-check 필수

5/29 시작 — stock.py는 끝까지 유지. 부서 코드는 신규 파일에 추가 후 stock.py가 import만.
"""

import os
import json
from datetime import datetime, timedelta

try:
    from zoneinfo import ZoneInfo
    _KST = ZoneInfo("Asia/Seoul")
except ImportError:
    _KST = None


# ════════════════════════════════════════════════
# 시간 헬퍼 (재무부 자체 보유 — stock.py와 동기화)
# ════════════════════════════════════════════════
def _now_kst() -> datetime:
    """현재 한국 시각 (tzaware)."""
    if _KST:
        return datetime.now(_KST)
    return datetime.now()


def _today_str() -> str:
    return _now_kst().strftime("%Y-%m-%d")


# ════════════════════════════════════════════════
# 데이터 파일 경로 (단일 진실 원천)
# ════════════════════════════════════════════════
_BASE_DIR = os.path.dirname(os.path.abspath(__file__))

POSITIONS_FILE       = os.path.join(_BASE_DIR, "positions.json")
MIRAE_PAPER_FILE     = os.path.join(_BASE_DIR, "mirae_paper.json")
ALERTS_FILE          = os.path.join(_BASE_DIR, "alerts.json")
TOMORROW_PICKS_CACHE = os.path.join(_BASE_DIR, "tomorrow_picks.json")


# ════════════════════════════════════════════════
# positions.json — 자동매매 (한투 모의)
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
    """positions.json 저장."""
    try:
        with open(POSITIONS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2, default=str)
    except Exception as e:
        print(f"  [재무부/포지션] 저장 실패: {e}")


# ════════════════════════════════════════════════
# mirae_paper.json — 가치주 추천 검증 (미래에셋 모의)
# ════════════════════════════════════════════════
def load_mirae_paper() -> dict:
    """mirae_paper.json 로드. 없으면 빈 구조 반환."""
    try:
        if os.path.exists(MIRAE_PAPER_FILE):
            with open(MIRAE_PAPER_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception as e:
        print(f"  [재무부/mirae_paper] 로드 오류: {e}")
    return {"positions": {}, "history": []}


def save_mirae_paper(data: dict) -> None:
    """mirae_paper.json 저장."""
    try:
        with open(MIRAE_PAPER_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"  [재무부/mirae_paper] 저장 오류: {e}")


# ════════════════════════════════════════════════
# alerts.json — 알림 히스토리 (대시보드 알림 센터)
# ════════════════════════════════════════════════
def load_alerts() -> list:
    """alerts.json 로드. 24시간 이내 알림만 반환."""
    try:
        if os.path.exists(ALERTS_FILE):
            with open(ALERTS_FILE, "r", encoding="utf-8") as f:
                alerts = json.load(f)
            if not isinstance(alerts, list):
                return []
            cutoff = (_now_kst() - timedelta(hours=24)).isoformat()
            return [a for a in alerts if a.get("time", "") >= cutoff]
    except Exception as e:
        print(f"  [재무부/alerts] 로드 오류: {e}")
    return []


def save_alerts(alerts: list) -> None:
    """alerts.json 저장."""
    try:
        with open(ALERTS_FILE, "w", encoding="utf-8") as f:
            json.dump(alerts, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"  [재무부/alerts] 저장 오류: {e}")


# 5/29: stock.py 호환을 위한 옛 이름 별칭 (점진 마이그레이션)
_load_alerts = load_alerts
_save_alerts = save_alerts


# ════════════════════════════════════════════════
# tomorrow_picks.json — 다음날 매수 우선 후보
# ════════════════════════════════════════════════
def load_tomorrow_picks() -> dict:
    """tomorrow_picks.json 로드. 신선도 체크: 오늘 날짜와 일치해야 유효.

    원본 함수가 today 비교 — 즉 *오늘이 그 picks 적용일*일 때 반환.
    """
    try:
        if not os.path.exists(TOMORROW_PICKS_CACHE):
            return {}
        with open(TOMORROW_PICKS_CACHE, "r", encoding="utf-8") as f:
            data = json.load(f)
        today = _today_str()
        if data.get("date") != today:
            print(f"  [재무부/tomorrow_picks] 날짜 불일치 ({data.get('date')} ≠ {today}) — 무시")
            return {}
        return data
    except Exception as e:
        print(f"  [재무부/tomorrow_picks] 로드 실패: {e}")
        return {}


def save_tomorrow_picks(data: dict) -> None:
    """tomorrow_picks.json 저장."""
    try:
        with open(TOMORROW_PICKS_CACHE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"  [재무부/tomorrow_picks] 저장 실패: {e}")


# 5/29: stock.py 호환 별칭
_load_tomorrow_picks = load_tomorrow_picks
_save_tomorrow_picks = save_tomorrow_picks
