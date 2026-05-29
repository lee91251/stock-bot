"""📨 알림부 (notify.py) — Phase 2 3단계 (5/29 회장 복귀 후 진행)

설계 문서: Obsidian Vault/11 - Phase 2 설계도 (14부서).md
체크리스트: Obsidian Vault/12 - Phase 2 실행 체크리스트.md

책임: 텔레그램 푸시 (OUT) + 대시보드 알림 센터 기록
- tg_send: 메시지 전송 (4096자 분할 + HTML 균형 보정)
- tg_send_document: HTML 파일 첨부 전송
- _notify_fatal: 봇 실행 실패 즉시 알림
- log_alert: 대시보드 알림 센터 기록 (텔레그램과 분리)

원칙 (회장 메모리):
- silent=True: 알림 진동/소리 X (브리핑·요약 등 정보성)
- silent=False (기본): 매수·매도·위험·오류 (즉시 인지 필요)
- 한 부서가 다른 부서 일 직접 X (알림부가 send 독점)

# 텔레그램 IN (tg_get_updates 등)은 자비스 비서부(별도 부서)로 분리 예정
# 현재는 stock.py에 그대로 유지 (점진 마이그레이션)
"""

import os
import re
import sys
import requests
from datetime import datetime

from finance import _load_alerts, _save_alerts, _now_kst


# ════════════════════════════════════════════════
# 환경변수 (단일 진실 원천 — stock.py와 동기화)
# ════════════════════════════════════════════════
TELEGRAM_TOKEN   = os.environ.get("TELEGRAM_TOKEN",   "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")


# ════════════════════════════════════════════════
# 텔레그램 OUT — tg_send / tg_send_document
# ════════════════════════════════════════════════
def _tg_base() -> str:
    """텔레그램 Bot API base URL."""
    return f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"


_HTML_TAG_RE = re.compile(r"<(/?)([a-zA-Z][a-zA-Z0-9]*)([^>]*)>")


def _balance_html_tags(chunk: str) -> str:
    """미닫힌/미열린 HTML 태그를 자동 보정해 텔레그램 400 오류 방지.

    4096자 분할 시 태그 중간에서 잘리면 텔레그램 sendMessage가 400 반환.
    이 함수가 분할 직후 chunk의 미닫힌 태그를 닫아 안전.
    """
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
    for tag in reversed(open_stack):
        chunk += f"</{tag}>"
    return chunk


def tg_send(text: str, chat_id: str = "", silent: bool = False):
    """텔레그램 메시지 전송 (4096자 자동 분할).

    Args:
        text: HTML 형식 메시지
        chat_id: 대상 chat (빈 값이면 기본 TELEGRAM_CHAT_ID)
        silent: True면 알림 진동/소리 X (브리핑·요약)
                False면 매수·매도·위험·오류 즉시 인지

    회장 메모리 (5/6 fix 유지):
        매수/매도/위험/오류는 silent=False 유지 (즉시 인지 필요).
    """
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
            payload = {"chat_id": cid, "text": chunk, "parse_mode": "HTML"}
            if silent:
                payload["disable_notification"] = True
            r = requests.post(
                f"{_tg_base()}/sendMessage",
                json=payload,
                timeout=15,
            )
            if not r.ok:
                # HTML 파싱 실패 시 plain text로 재시도 (잘림 방지)
                print(f"  [알림부] HTML 전송 실패 ({r.status_code}) — plain text 재시도")
                plain = re.sub(r"<[^>]+>", "", chunk)
                requests.post(
                    f"{_tg_base()}/sendMessage",
                    json={"chat_id": cid, "text": plain},
                    timeout=15,
                )
        except Exception as e:
            print(f"  [알림부] 전송 오류: {e}")


def tg_send_document(html: str, caption: str = ""):
    """HTML 파일을 텔레그램에 첨부 전송 (브리핑 풀 리포트용)."""
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
            print("  [알림부] 텔레그램 전송 완료!")
        else:
            print(f"  [알림부] 파일 전송 실패: {r.text[:200]}")
    except Exception as e:
        print(f"  [알림부] 전송 오류: {e}")


# ════════════════════════════════════════════════
# _notify_fatal — 봇 실행 실패 즉시 알림
# ════════════════════════════════════════════════
def notify_fatal(mode: str, exc: BaseException) -> None:
    """봇 실행 중 처리되지 않은 예외 발생 시 텔레그램으로 즉시 알림."""
    import traceback
    tb_full = traceback.format_exc()
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


# 5/29 stock.py 호환 별칭
_notify_fatal = notify_fatal


# ════════════════════════════════════════════════
# 대시보드 알림 센터 (alerts.json) — 정보성 알림 누적
# 텔레그램과 분리: alerts는 *대시보드에서 봄*, 텔레그램은 *즉시 푸시*
# ════════════════════════════════════════════════
_ALERT_EMOJI_MAP = {
    "imminent":   "📊",
    "disclosure": "📰",
    "briefing":   "🔔",
    "recommend":  "🎯",
    "risk":       "⚠️",
    "system":     "🤖",
    "emergency":  "🚨",
    "autobuy":    "🛒",
    "autosell":   "💸",
}


def log_alert(category: str, level: str, title: str, detail: str = "", emoji: str = "") -> None:
    """대시보드 알림 센터에 기록 (텔레그램 X — 정보성 알림 분리).

    Args:
        category: "imminent" | "disclosure" | "briefing" | "recommend" | "risk" | "system" | ...
        level:    "info" | "warning" | "danger"
        title:    제목
        detail:   상세 (HTML 허용)
        emoji:    카테고리 이모지 (자동 매핑됨, 빈 값 시)

    회장 메모리 적용:
        텔레그램 다이어트 후 *정보성 알림 분리* — 대시보드 카드에서 확인
    """
    if not emoji:
        emoji = _ALERT_EMOJI_MAP.get(category, "🔵")

    alerts = _load_alerts()
    alerts.append({
        "time":     _now_kst().isoformat(),
        "category": category,
        "level":    level,
        "emoji":    emoji,
        "title":    title,
        "detail":   detail,
    })
    # 최근 100건만 보관 (24h cutoff와 별개로 안전 캡)
    if len(alerts) > 100:
        alerts = alerts[-100:]
    _save_alerts(alerts)
