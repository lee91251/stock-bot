"""📊 대시보드부 (dashboard.py) — Phase 2 4단계 (5/29 회장 복귀 후 진행)

설계 문서: Obsidian Vault/11 - Phase 2 설계도 (14부서).md
체크리스트: Obsidian Vault/12 - Phase 2 실행 체크리스트.md

책임: HTML 대시보드 카드 + Chart.js + PWA 메타 + staticrypt

# Phase 2 4단계 — 점진 마이그레이션 (회장 청사진 원칙)

회장 결정: "한 번에 9000줄 다 옮기지 X" (12번 노트)

1차 (현재 commit):
- _empty_section: 빈 카드 컴팩트 헬퍼 (5/29 회장 통찰)
- _make_short_term_card: 📈 단기 추천 TOP 3
- _make_mid_term_card: 📊 중기 추천 TOP 3
- _make_long_term_card: 💎 장기 가치주 TOP 3 (헌법)
- _make_avoid_card: 🚫 오늘 피해야 할 종목

2차 (다음 commit):
- card_html (400+줄, 핵심 카드 빌더 — 신중 이동)
- _make_recommend_card (card_html 의존)
- _dashboard_css (CSS, ~430줄)

3차:
- 보유 카드 (_make_value/auto_positions/paper_mirae)
- 성적표 / 비교 / 시장 / AI 카드들
- _make_sidebar

4차 (최종):
- build_and_save_dashboard (메인 함수)

5/29 시작 — 함수 이동 시 stock.py 동작 그대로 유지.
"""

import json  # Chart.js 데이터 직렬화용 (stdlib — 순환 import 위험 없음)


# ════════════════════════════════════════════════
# 헬퍼 — _empty_section (5/29 회장 통찰 적용)
# ════════════════════════════════════════════════
def _empty_section(sid: str, icon: str, icon_cls: str, title: str, badge: str,
                   empty_title: str, empty_desc: str) -> str:
    """5/29: 빈 섹션 컴팩트 모드 — 큰 빈 박스 대신 한 줄 안내.

    회장 통찰: 빈 카드(단기/중기 추천 등)가 페이지 절반 차지 → 핵심 정보 가려짐
    fix: 1줄 컴팩트 헤더만 표시. 클릭 시 자세히 보기 가능.
    """
    return f"""
<section class="section section--compact" id="{sid}" aria-label="{title}">
  <div class="section__head" style="padding-bottom:8px;">
    <div class="section__title">
      <span class="section__icon {icon_cls}" style="opacity:0.4;">{icon}</span>
      <h2 style="opacity:0.6;">{title}</h2>
      <span class="section__badge" style="opacity:0.6;">{badge}</span>
    </div>
    <div style="font-size:12px;color:var(--text-3);padding:4px 16px 0;">
      ⏳ {empty_title}
    </div>
  </div>
</section>
"""


# ════════════════════════════════════════════════
# 4트랙 추천 카드 (3/4 — 스윙은 card_html 의존이라 stock.py 잔존)
# ════════════════════════════════════════════════
def _make_short_term_card(short_top: list) -> str:
    """📈 단기 추천 TOP 3 (1~3주, 수동 매수) — 모멘텀 + 안정성."""
    if not short_top:
        return _empty_section("short-term", "📈", "section__icon--rec", "단기 추천 TOP 3",
                              "1~3주 · 수동 매수", "추천 데이터 준비 중",
                              "16:00 시장 스캔 후 RSI 45~65 + 거래량 100%+ 종목 추출.")
    rows = ""
    for i, s in enumerate(short_top, 1):
        name = s.get("name", "?")
        price = s.get("price", 0)
        score = s.get("short_score") or s.get("score", 0)
        rsi = s.get("rsi", 0)
        vol = s.get("vol_ratio", 0)
        macd = "✅" if s.get("macd_cross") else "—"
        ret_1m = s.get("ret_1m", 0)
        rows += f"""
        <div class="row">
          <div class="row__main">
            <div class="row__name">#{i} {name} <span style="color:var(--text-3);font-weight:400;font-size:12px;">{score}점</span></div>
            <div class="row__sub">RSI {rsi:.0f} · 거래량 {vol:.0f}% · MACD {macd} · 1달 {ret_1m:+.1f}%</div>
          </div>
          <div class="row__right">
            <div class="row__value">{price:,.0f}원</div>
            <div class="row__sub" style="text-align:right;">+8~+15% 익절</div>
          </div>
        </div>"""
    return f"""
<section class="section" id="short-term" aria-label="단기 추천 TOP 3">
  <div class="section__head">
    <div class="section__title">
      <span class="section__icon section__icon--rec">📈</span>
      <h2>단기 추천 TOP 3</h2>
      <span class="section__badge">1~3주 · 수동 매수</span>
    </div>
  </div>
  <div class="section__body">{rows}</div>
  <div class="section__foot" style="padding:8px 16px;color:var(--text-3);font-size:12px;">
    💡 미래에셋 모의 / 한투 모의에서 수동 매수 → 1~3주 후 매도. 손절 -5~-7%
  </div>
</section>
"""


def _make_mid_term_card(mid_top: list) -> str:
    """📊 중기 추천 TOP 3 (1~3개월, 수동 매수) — 가치 + 펀더멘털."""
    if not mid_top:
        return _empty_section("mid-term", "📊", "section__icon--rec", "중기 추천 TOP 3",
                              "1~3개월 · 수동 매수", "추천 데이터 준비 중",
                              "16:00 시장 스캔 후 PER ≤ 시장평균 + ROE ≥ 8% 종목 추출.")
    rows = ""
    for i, s in enumerate(mid_top, 1):
        name = s.get("name", "?")
        price = s.get("price", 0)
        score = s.get("mid_score") or s.get("score", 0)
        per = s.get("per", 0) or 0
        roe = s.get("roe", 0)
        div = s.get("div", 0)
        ret_3m = s.get("ret_3m", 0)
        rows += f"""
        <div class="row">
          <div class="row__main">
            <div class="row__name">#{i} {name} <span style="color:var(--text-3);font-weight:400;font-size:12px;">{score}점</span></div>
            <div class="row__sub">PER {per:.1f} · ROE {roe:.1f}% · 배당 {div:.1f}% · 3달 {ret_3m:+.1f}%</div>
          </div>
          <div class="row__right">
            <div class="row__value">{price:,.0f}원</div>
            <div class="row__sub" style="text-align:right;">+15~+30% 익절</div>
          </div>
        </div>"""
    return f"""
<section class="section" id="mid-term" aria-label="중기 추천 TOP 3">
  <div class="section__head">
    <div class="section__title">
      <span class="section__icon section__icon--rec">📊</span>
      <h2>중기 추천 TOP 3</h2>
      <span class="section__badge">1~3개월 · 수동 매수</span>
    </div>
  </div>
  <div class="section__body">{rows}</div>
  <div class="section__foot" style="padding:8px 16px;color:var(--text-3);font-size:12px;">
    💡 미래에셋 모의에서 수동 매수 → 1~3개월 후 매도. 손절 -8~-10%
  </div>
</section>
"""


def _make_long_term_card(long_top: list | None = None) -> str:
    """💎 장기 가치주 TOP 3 (3개월+, 미래에셋 실계좌 / 자동매매 X).

    5/14: 헌법 1차 필터(PER≤12, PBR≤1.2, ROE≥10, 시총≥1조) 통과 종목 TOP 3.
    빈 경우 헌법 안내 카드로 폴백.
    """
    if not long_top:
        return f"""
<section class="section" id="long-term" aria-label="장기 가치주">
  <div class="section__head">
    <div class="section__title">
      <span class="section__icon section__icon--rec">💎</span>
      <h2>장기 가치주</h2>
      <span class="section__badge">3개월+ · 미래에셋 실</span>
    </div>
  </div>
  <div class="section__body" style="padding:16px;color:var(--text-2);">
    <p style="margin:0 0 8px 0;font-size:14px;">
      <strong>회장 가치투자 헌법 기반 (수동 발굴)</strong>
    </p>
    <ul style="margin:0;padding-left:20px;font-size:13px;line-height:1.8;">
      <li>PER ≤ 12 (저평가)</li>
      <li>PBR ≤ 1.2 / ROE ≥ 10%</li>
      <li>시가총액 ≥ 1조 (안정성)</li>
      <li>배당수익률 ≥ 2% / 영업현금흐름 양수</li>
    </ul>
    <p style="margin:12px 0 0 0;font-size:12px;color:var(--text-3);">
      💡 미래에셋 실계좌 / 봇이 자동 추천 X / 회장이 직접 발굴
    </p>
  </div>
</section>
"""
    # 헌법 통과 종목 있음 — TOP 3 표시
    rows = ""
    for i, s in enumerate(long_top, 1):
        name = s.get("name", "?")
        price = s.get("price", 0)
        score = s.get("long_score") or s.get("score", 0)
        per = s.get("per", 0) or 0
        pbr = s.get("pbr", 0) or 0
        roe = s.get("roe", 0)
        div = s.get("div", 0)
        mktcap = s.get("mktcap", 0)
        mktcap_str = f"{mktcap/1_0000_0000_0000:.1f}조" if mktcap >= 1_0000_0000_0000 else f"{mktcap/1_0000_0000:.0f}억"
        rows += f"""
        <div class="row">
          <div class="row__main">
            <div class="row__name">#{i} {name} <span style="color:var(--text-3);font-weight:400;font-size:12px;">{score}점</span></div>
            <div class="row__sub">PER {per:.1f} · PBR {pbr:.2f} · ROE {roe:.1f}% · 배당 {div:.1f}% · {mktcap_str}</div>
          </div>
          <div class="row__right">
            <div class="row__value">{price:,.0f}원</div>
            <div class="row__sub" style="text-align:right;">+40~+100% 익절</div>
          </div>
        </div>"""
    return f"""
<section class="section" id="long-term" aria-label="장기 가치주 TOP 3">
  <div class="section__head">
    <div class="section__title">
      <span class="section__icon section__icon--rec">💎</span>
      <h2>장기 가치주 TOP 3</h2>
      <span class="section__badge">3개월+ · 미래에셋 실 (수동)</span>
    </div>
  </div>
  <div class="section__body">{rows}</div>
  <div class="section__foot" style="padding:8px 16px;color:var(--text-3);font-size:12px;">
    💡 헌법 1차 필터 통과 (PER≤12, PBR≤1.2, ROE≥10, 시총≥1조). 봇은 추천만 — 매수는 수동.
  </div>
</section>
"""


# ════════════════════════════════════════════════
# 피해야 할 종목 카드
# ════════════════════════════════════════════════
def _make_avoid_card(avoid: list) -> str:
    """🚫 오늘 피해야 할 종목 카드."""
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


# ════════════════════════════════════════════════
# 종목 카드 빌더 (Phase 2 4단계 2차, 5/29) — card_html
# 의존: _fmt_krw / SIGNAL_DEFS (stock.py lazy import)
# ════════════════════════════════════════════════
def card_html(rank: int, s: dict, ai_insight: str = "", period_override: str | None = None) -> str:
    from stock import _fmt_krw, SIGNAL_DEFS  # lazy: 순환 import 회피
    medals  = ["🥇", "🥈", "🥉"]
    medal   = medals[rank] if rank < 3 else f"{rank + 1}위"
    cur     = s["currency"]
    chg_col = "#e03131" if s["change"] >= 0 else "#1971c2"
    chg_arr = "▲" if s["change"] >= 0 else "▼"

    # 5/14: 4트랙 라벨 정확화 — period_override로 트랙별 라벨 주입 가능
    period_key = period_override or s.get("period", "중기")
    period_badge = {
        "스윙": ("#fff5e6", "#d9480f", "스윙 1~5일"),
        "단기": ("#fff0f6", "#c2255c", "단기 1~3주"),
        "중기": ("#e8f4fd", "#1971c2", "중기 1~3개월"),
        "장기": ("#ebfbee", "#2f9e44", "장기 3개월+"),
    }.get(period_key, ("#f8f9fa", "#495057", period_key))

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

  <details class="card-detail">
  <summary class="card-detail__summary">📋 상세 분석 보기 — 지표·재무·매수 시뮬·전략</summary>
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
  </details>
</div>"""



# ════════════════════════════════════════════════
# 대시보드 CSS/JS 디자인 시스템 (Phase 2 4단계 2차, 5/29)
# ════════════════════════════════════════════════
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

.row__diag { font-size: 12px; margin-top: 4px; line-height: 1.4;
             font-weight: 600; letter-spacing: 0.1px; }

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

/* ──────────────────────────────────────────────
   5/29 디자인 개선 — 빈 카드 컴팩트 + 펼침 + 가독성
   회장 통찰: "누가 봐도 보기 쉽고 멋지게"
   ────────────────────────────────────────────── */

/* 빈 카드 컴팩트 — 큰 빈 박스 대신 한 줄 안내 */
.section--compact {
  padding: 8px 0;
  opacity: 0.85;
}
.section--compact .section__head {
  padding-top: 12px;
  padding-bottom: 12px;
}
.section--compact .section__head::after {
  content: none;
}

/* details/summary 펼침 (거래 이력 더보기) */
details > summary {
  list-style: none;
  user-select: none;
  padding: 10px 12px;
  border-radius: var(--radius-sm);
  background: var(--surface-2);
  border: 1px dashed var(--border);
  transition: background 0.15s, border-color 0.15s;
}
details > summary::-webkit-details-marker { display: none; }
details > summary::before {
  content: "▶ ";
  display: inline-block;
  margin-right: 4px;
  font-size: 10px;
  transition: transform 0.2s;
}
details[open] > summary::before {
  transform: rotate(90deg);
}
details > summary:hover {
  background: var(--surface);
  border-color: var(--accent);
}

/* 섹션 간격 일관 (위계 명확) */
.main > section {
  margin-bottom: 16px;
}
.main > section.section--compact {
  margin-bottom: 8px;
}

/* 카드 hover 부드러운 강조 (멋진 인터랙션) */
.section:not(.section--compact):hover {
  box-shadow: var(--shadow-lg);
  transition: box-shadow 0.2s ease;
}

/* 모바일 (회장 폰 5분 점검 친화) */
@media (max-width: 768px) {
  .main { padding: 12px; }
  .section { border-radius: var(--radius-sm); }
  .section__head h2 { font-size: 16px; }
  .section__body { padding: 12px; }
  details > summary { padding: 8px 10px; font-size: 13px; }
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

    // 사이드바 링크 클릭 시: 접힌 부서(details) 펼치기 + 모바일 사이드바 닫기
    links.forEach(function (l) {
      l.addEventListener('click', function () {
        var t = l.getAttribute('data-target');
        var tgt = t ? document.getElementById(t) : null;
        if (tgt && tgt.tagName === 'DETAILS') tgt.open = true;
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



# ════════════════════════════════════════════════
# 스윙 추천 TOP 3 카드 (Phase 2 4단계 2차, 5/29) — card_html 활용
# ════════════════════════════════════════════════
def _make_recommend_card(kr_top: list, ai_insights: dict) -> str:
    """🚀 스윙 추천 TOP 3 카드 (1~5일, 자동매매 후보) — 기존 card_html 활용."""
    if not kr_top:
        return _empty_section("recommend", "🚀", "section__icon--rec", "스윙 추천 TOP 3",
                              "1~5일 / 자동매매", "추천 데이터 준비 중",
                              "평일 16:00 시장 스캔 + 08:50 장 시작 전 분석 후 표시됩니다.")
    cards = "".join(
        card_html(i, s, (ai_insights or {}).get(s.get('ticker',''), ""), period_override="스윙")
        for i, s in enumerate(kr_top[:3])
    )
    return f"""
<section class="section" id="recommend" aria-label="스윙 추천 TOP 3">
  <div class="section__head">
    <div class="section__title">
      <span class="section__icon section__icon--rec">🚀</span>
      <h2>스윙 추천 TOP 3</h2>
      <span class="section__badge">1~5일 · 자동매매</span>
    </div>
  </div>
  <div class="embed-wrap">{cards}</div>
</section>
"""


# ════════════════════════════════════════════════
# 보유 카드 (Phase 2 4단계 3차, 5/29)
# 의존: _pnl_class / _make_sparkline_svg / _safe_float / _kis (stock lazy)
#       load_mirae_paper (finance lazy) / PAPER_MIRAE_* 상수 (stock lazy)
# _empty_section 은 dashboard.py 내부 함수 — import 불필요
# ════════════════════════════════════════════════
def _make_value_holdings_section(value_holdings: list, sparklines: dict = None,
                                   diagnosis: dict = None) -> str:
    """가치주 보유 섹션 — 미래에셋증권 (HOLDINGS_JSON 기반).

    sparklines: {code: {values, labels, change_pct}} — 종목별 7일 종가 추세선 데이터.
    diagnosis: {name: 'AI 진단 한 줄'} — ai_personal_coach가 만든 종목별 진단.
    """
    from stock import _pnl_class, _make_sparkline_svg  # lazy: 순환 import 회피
    if not value_holdings:
        return _empty_section("value", "💼", "section__icon--value", "가치주 보유",
                              "미래에셋증권", "등록된 가치주가 없습니다",
                              "채팅창에서 '한화에어로 10주 180000원에 샀어' 같이 등록하면 표시됩니다.")
    sparklines = sparklines or {}
    diagnosis = diagnosis or {}
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

        # AI 진단 한 줄 (있으면)
        diag_text = diagnosis.get(name) or ""
        diag_html = ""
        if diag_text:
            # 키워드별 색상 (홀드/매수/매도/주의)
            d_color = "#10b981" if any(k in diag_text for k in ("홀드", "보유", "유망", "강세", "매수")) \
                else ("#ef4444" if any(k in diag_text for k in ("매도", "주의", "약세", "차익", "리스크")) \
                else "#94a3b8")
            diag_html = (f'<div class="row__diag" style="color:{d_color};">'
                         f'🤖 {diag_text}</div>')

        rows.append(f"""
    <div class="row">
      <div class="row__main">
        <div class="row__name">{name}{badge}</div>
        <div class="row__sub">{qty:,}주 · 평단 {avg:,.0f}원</div>
        {diag_html}
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
    from stock import _pnl_class, _safe_float, _kis  # lazy: 순환 import 회피
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
        # 매수 일시 (B3+ — 시간대별 성과 검증용)
        bd = e.get("buy_date", "")
        bt = e.get("buy_time", "")
        when = ""
        if bd:
            when = f" · 매수 {bd[5:].replace('-', '/')}"
            if bt:
                when += f" {bt}"
        rows.append(f"""
    <div class="row">
      <div class="row__main">
        <div class="row__name">{name}</div>
        <div class="row__sub">{qty:,}주 · 매수가 {bp:,.0f}원{when}{partial}</div>
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


def _make_paper_mirae_section() -> str:
    """미래에셋 모의 (추천 검증용) 카드.

    봇이 추천한 가치주를 모의로 매수해서 추천 정확도 검증.
    가치주 룰: -7% 손절 / +10% 1차 / +20% 2차 / +40% 장기.
    각 종목별 매도시점 도달 여부를 시각적으로 표시.
    """
    from stock import (  # lazy: 순환 import 회피
        _pnl_class, _safe_float, _kis,
        PAPER_MIRAE_STOP_LOSS_PCT, PAPER_MIRAE_TARGET1_PCT,
        PAPER_MIRAE_TARGET2_PCT, PAPER_MIRAE_TARGET3_PCT,
    )
    from finance import load_mirae_paper
    data = load_mirae_paper()
    positions = data.get("positions", {})

    if not positions:
        return _empty_section(
            "paper-mirae", "🧪", "section__icon--auto", "미래에셋 모의 (추천 검증)",
            "가치주 검증", "아직 등록된 종목 없음",
            "봇 추천 가치주를 모의로 매수해서 정확도 검증. 채팅 또는 텔레그램으로 등록.",
        )

    rows = []
    total_cost   = 0
    total_value  = 0

    for code, p in positions.items():
        name      = p.get("name", code)
        qty       = p.get("qty", 0)
        bp        = p.get("buy_price", 0)
        partial   = p.get("partial_sold", False)
        peak_pct  = p.get("peak_pct", 0)
        rec_score = p.get("rec_score", 0)
        buy_date  = p.get("buy_date", "")
        rec_date  = p.get("rec_date", "")

        # 시세 조회
        cur_price = 0
        try:
            info = _kis.get_price(code) if _kis.available() else {}
            cur_price = _safe_float(info.get("stck_prpr")) if info else 0
        except Exception:
            pass

        cost = bp * qty
        value = cur_price * qty if cur_price > 0 else cost
        profit = value - cost
        pct = ((cur_price - bp) / bp * 100) if (cur_price and bp) else 0
        total_cost  += cost
        total_value += value

        # 매도시점 도달 표시
        cls = _pnl_class(pct)
        sign = "+" if profit >= 0 else ""

        target_tag = ""
        if pct <= -PAPER_MIRAE_STOP_LOSS_PCT * 100:
            target_tag = f' <span style="color:#dc2626;font-weight:700">🔴 손절 도달</span>'
        elif pct >= PAPER_MIRAE_TARGET3_PCT * 100:
            target_tag = f' <span style="color:#16a34a;font-weight:700">🏆 +40% 장기 목표!</span>'
        elif pct >= PAPER_MIRAE_TARGET2_PCT * 100:
            target_tag = f' <span style="color:#16a34a;font-weight:700">🟢 2차 목표 (+20%)</span>'
        elif pct >= PAPER_MIRAE_TARGET1_PCT * 100:
            if partial:
                target_tag = f' <span style="color:#0369a1">🟢 1차 매도 완료 — 잔여 +20% 까지</span>'
            else:
                target_tag = f' <span style="color:#16a34a;font-weight:700">🟢 1차 목표 (+10%) — 절반 매도 권장</span>'

        # 매도시점까지 거리 표시 (도달 안 한 경우)
        progress_html = ""
        if pct < PAPER_MIRAE_TARGET1_PCT * 100 and pct > -PAPER_MIRAE_STOP_LOSS_PCT * 100:
            to_t1 = PAPER_MIRAE_TARGET1_PCT * 100 - pct
            to_sl = pct - (-PAPER_MIRAE_STOP_LOSS_PCT * 100)
            progress_html = f'<small>1차까지 +{to_t1:.1f}%p · 손절까지 -{to_sl:.1f}%p</small>'
        elif PAPER_MIRAE_TARGET1_PCT * 100 <= pct < PAPER_MIRAE_TARGET2_PCT * 100:
            to_t2 = PAPER_MIRAE_TARGET2_PCT * 100 - pct
            progress_html = f'<small>2차까지 +{to_t2:.1f}%p</small>'

        # peak_pct 표시 (트레일링 정보)
        peak_html = ""
        if peak_pct > pct + 2:  # 최고가에서 2%p 이상 빠진 경우만 표시
            peak_html = f' <small style="color:#888">(최고 +{peak_pct:.1f}%)</small>'

        partial_html = " · 1차매도완료" if partial else ""
        bt_str = p.get("buy_time", "")
        when = ""
        if buy_date:
            when = buy_date[5:].replace('-', '/')
            if bt_str:
                when += f" {bt_str}"
        rec_html = ""
        if rec_date:
            rec_html = f" · 추천일 {rec_date[5:].replace('-', '/')}"

        rows.append(f"""
    <div class="row">
      <div class="row__main">
        <div class="row__name">🧪 {name}{target_tag}</div>
        <div class="row__sub">{qty}주 · 매수 {bp:,.0f}원 ({when}{rec_html}){partial_html}<br>{progress_html}</div>
      </div>
      <div class="row__price">
        <div class="row__current">{cur_price:,.0f}원{peak_html}</div>
        <div class="row__pnl {cls}">{sign}{int(round(profit)):,}원<small>({sign}{pct:.2f}%)</small></div>
      </div>
    </div>""")

    total_pnl = total_value - total_cost
    total_pct = (total_pnl / total_cost * 100) if total_cost > 0 else 0
    pnl_cls = _pnl_class(total_pct)
    pnl_sign = "+" if total_pnl >= 0 else ""

    return f"""
<section class="section" id="paper-mirae" aria-label="미래에셋 모의">
  <div class="section__head">
    <div class="section__title">
      <span class="section__icon section__icon--auto">🧪</span>
      <h2>미래에셋 모의 (추천 검증)</h2>
      <span class="section__badge">가치주 룰 -7/+10/+20/+40</span>
      <span class="section__count">{len(positions)}종목</span>
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


# ════════════════════════════════════════════════
# 성적표/통계 카드 (Phase 2 4단계 4차-A, 5/29)
# 의존: calc_weight_recommendations/calc_advisor_accuracy/load_positions (stock lazy)
#       B4_MIN_SAMPLES/AI_ADVISOR_MIN_SAMPLES (stock lazy) / json (top-level)
# ════════════════════════════════════════════════
def _make_b4_learning_card() -> str:
    """B4 자가학습 카드 (#5) — 매매 데이터 기반 가중치 자동 조정 권장.

    데이터 30건+ 누적 시 자동 활성화. 그 전엔 진행도 표시.
    """
    from stock import calc_weight_recommendations, B4_MIN_SAMPLES  # lazy: 순환 import 회피
    data = calc_weight_recommendations()
    trades   = data.get("trades", 0)
    ready    = data.get("ready", False)
    win_rate = data.get("win_rate", 0)
    recs     = data.get("recommendations", [])

    if not ready:
        progress = trades / B4_MIN_SAMPLES * 100
        progress_bar = f"""
    <div style="background:#f3f4f6;border-radius:8px;height:10px;margin:10px 0">
      <div style="width:{progress:.0f}%;background:#7c3aed;height:10px;border-radius:8px"></div>
    </div>
    <div style="font-size:13px;color:#666">{trades} / {B4_MIN_SAMPLES}건 ({progress:.0f}%) — 남은 {B4_MIN_SAMPLES - trades}건</div>
    """
        return f"""
<section class="section" id="learning" aria-label="자가학습">
  <div class="section__head">
    <div class="section__title">
      <span class="section__icon section__icon--auto">🧠</span>
      <h2>자가학습 (B4)</h2>
      <span class="section__badge">데이터 누적 중</span>
    </div>
    <div class="section__subtitle">
      <div class="section__amount" style="color:#7c3aed">{trades}/{B4_MIN_SAMPLES}건</div>
      <div>현재 승률 {win_rate:.1f}%</div>
    </div>
  </div>
  <div class="section__body">
    <div style="padding:14px">
      {progress_bar}
      <div style="font-size:13px;color:#444;line-height:1.7;margin-top:12px">
        <b>봇이 자기 매매를 분석해 가중치를 스스로 조정하는 시스템.</b><br>
        매매 30건+ 누적 시 자동으로:
        <ul style="margin:6px 0 0 18px;padding:0">
          <li>점수대별 승률 차이 → <b>매수 점수 임계 조정 권장</b></li>
          <li>섹터별 승률 → <b>강세 섹터 우대 / 약세 섹터 회피 권장</b></li>
          <li>시간대별 승률 → <b>매수 회차 시간 조정 권장</b></li>
          <li>보유일별 승률 → <b>강제 매도 일수 조정 권장</b></li>
        </ul>
        <br>
        권장 사항은 사용자 OK 후 코드에 반영. 자동 변경 X (안전 우선).
      </div>
    </div>
  </div>
</section>
"""

    # 30건+ 활성화된 경우
    if not recs:
        body = """
    <div style="background:#dcfce7;border:1px solid #16a34a;padding:14px;border-radius:8px;margin:12px">
      ✅ <b>현재 가중치 적정</b> — 매매 패턴 분석 결과 추가 조정 권장 없음.
      현재 룰 그대로 유지.
    </div>
    """
    else:
        rec_rows = []
        level_colors = {"high": "#dc2626", "medium": "#d97706", "low": "#0369a1"}
        for r in recs:
            color = level_colors.get(r.get("level", "low"), "#0369a1")
            rec_rows.append(f"""
    <div class="row" style="border-left:4px solid {color}">
      <div class="row__main">
        <div class="row__name">{r.get('title', '')}</div>
        <div class="row__sub">{r.get('reason', '')}</div>
      </div>
    </div>""")
        body = f"""
    <div style="background:#fef3c7;border:1px solid #d97706;padding:10px;border-radius:8px;margin:12px">
      🎯 <b>가중치 자동 조정 권장 {len(recs)}건</b> — 사용자 결정 후 코드 반영.
      "코드 수정해줘 [권장 항목]" 채팅으로 적용 가능.
    </div>
    {''.join(rec_rows)}
    """

    return f"""
<section class="section" id="learning" aria-label="자가학습">
  <div class="section__head">
    <div class="section__title">
      <span class="section__icon section__icon--auto">🧠</span>
      <h2>자가학습 (B4)</h2>
      <span class="section__badge">활성화됨</span>
      <span class="section__count">{trades}건</span>
    </div>
    <div class="section__subtitle">
      <div class="section__amount">권장 {len(recs)}건</div>
      <div>현재 승률 {win_rate:.1f}%</div>
    </div>
  </div>
  <div class="section__body">{body}
  </div>
</section>
"""


def _make_advisor_stats_card() -> str:
    """AI 매도 어드바이저 신뢰도 카드 (#4) — B1 → v2 진화.

    누적 30건 미만: "데이터 누적 중 X/30" + 의견만 표시 (default 동작)
    누적 30건+ : 정확도 표시 + 60%+ 시 자동 활성화 권장 알림
    """
    from stock import calc_advisor_accuracy, AI_ADVISOR_MIN_SAMPLES  # lazy: 순환 import 회피
    stats = calc_advisor_accuracy()
    total   = stats["total"]
    pending = stats.get("pending", 0)

    if total == 0 and pending == 0:
        return _empty_section(
            "advisor", "🤖", "section__icon--auto", "AI 매도 신뢰도",
            "데이터 누적 중", "AI 의견 데이터 없음",
            f"매도 발생 시 AI 의견 자동 누적. {AI_ADVISOR_MIN_SAMPLES}건+ 평가 후 자동 결정 활성화 가능.",
        )

    accuracy   = stats["accuracy"]
    correct    = stats["correct"]
    hold_t     = stats["hold_total"]
    hold_c     = stats["hold_correct"]
    sell_t     = stats["sell_total"]
    sell_c     = stats["sell_correct"]
    ready      = stats["ready_to_activate"]

    progress_pct = min(100, total / AI_ADVISOR_MIN_SAMPLES * 100)
    acc_color = "#16a34a" if accuracy >= 60 else ("#d97706" if accuracy >= 40 else "#dc2626")

    # 진행도 바
    progress_bar = f"""
    <div style="background:#f3f4f6;border-radius:8px;height:8px;margin:6px 0">
      <div style="width:{progress_pct:.0f}%;background:{acc_color};height:8px;border-radius:8px"></div>
    </div>
    """

    # 활성화 권장 메시지
    activation_msg = ""
    if ready:
        activation_msg = """
    <div style="background:#dcfce7;border:1px solid #16a34a;padding:10px;border-radius:8px;margin-top:10px">
      🎯 <b>자동 활성화 권장</b> — 30건+ 누적 + 정확도 60%+ 도달.
      AI "보류 검토" 시 매도 1회 미루기 활성화 가능 (사용자 결정 필요).
    </div>
    """
    elif total < AI_ADVISOR_MIN_SAMPLES:
        activation_msg = f"""
    <div style="background:#fef3c7;border:1px solid #d97706;padding:10px;border-radius:8px;margin-top:10px">
      ⏳ <b>데이터 누적 중</b> — {total} / {AI_ADVISOR_MIN_SAMPLES}건 (남은 {AI_ADVISOR_MIN_SAMPLES - total}건).
      평가 대기 중인 5일 미만 의견: {pending}건.
    </div>
    """
    else:
        activation_msg = f"""
    <div style="background:#fee2e2;border:1px solid #dc2626;padding:10px;border-radius:8px;margin-top:10px">
      ⚠️ <b>정확도 부족</b> — 30건+ 누적했지만 정확도 {accuracy:.1f}% (60%+ 필요).
      AI 의견은 참고용 그대로 — 자동 결정 활성화 X.
    </div>
    """

    return f"""
<section class="section" id="advisor" aria-label="AI 매도 신뢰도">
  <div class="section__head">
    <div class="section__title">
      <span class="section__icon section__icon--auto">🤖</span>
      <h2>AI 매도 신뢰도</h2>
      <span class="section__badge">{total}건 평가</span>
    </div>
    <div class="section__subtitle">
      <div class="section__amount" style="color:{acc_color}">정확도 {accuracy:.1f}%</div>
      <div>{correct} / {total} 정확</div>
    </div>
  </div>
  <div class="section__body">
    <div style="padding:10px">
      <div style="font-size:13px;color:#666;margin-bottom:4px">진행도 ({total}/{AI_ADVISOR_MIN_SAMPLES})</div>
      {progress_bar}
      <div class="row">
        <div class="row__main">
          <div class="row__name">📊 의견 분류별 정확도</div>
          <div class="row__sub">
            🟡 보류 권장: {hold_c} / {hold_t} 정확 ({(hold_c/hold_t*100 if hold_t else 0):.0f}%)
            · 🔴 매도 권장: {sell_c} / {sell_t} 정확 ({(sell_c/sell_t*100 if sell_t else 0):.0f}%)
          </div>
        </div>
      </div>
      {activation_msg}
    </div>
  </div>
</section>
"""


def _make_verify_card() -> str:
    """🛡️ 검증부 카드 (Phase 2 6단계) — 매수/매도 게이트 통과율 + 거절 사유.

    shadow 모드: 검증부가 인라인 가드와 별도로 내린 판정을 집계해서 보여줌.
    enforce 모드: 실제 차단 중임을 표시. 데이터 없으면 누적 중 안내.
    """
    from verify import get_verify_stats  # lazy: 순환 import 회피
    try:
        from stock import VERIFY_ENFORCE  # lazy
    except Exception:
        VERIFY_ENFORCE = False

    stats = get_verify_stats(window_days=7)
    total = stats.get("total", 0)

    mode_badge = "🔴 enforce(실제 차단)" if VERIFY_ENFORCE else "🟡 shadow(기록만)"

    if not stats or total == 0:
        return _empty_section(
            "verify", "🛡️", "section__icon--auto", "검증부 게이트",
            mode_badge, "데이터 누적 중",
            "다음 평일 자동매수·매도 때 검증부 판정이 쌓입니다. "
            "shadow 모드라 실제 매매는 인라인 룰 그대로 — 판정만 기록.",
        )

    passed     = stats.get("passed", 0)
    rejected   = stats.get("rejected", 0)
    pass_rate  = stats.get("pass_rate_pct", 0)
    top_rejects = stats.get("top_rejects", [])
    buy        = stats.get("buy", {})
    sell       = stats.get("sell", {})
    window     = stats.get("window_days", 7)

    rate_color = "#16a34a" if pass_rate >= 80 else ("#d97706" if pass_rate >= 50 else "#dc2626")

    # 거절 사유 TOP
    if top_rejects:
        reject_lines = " · ".join(f"{gate} {cnt}건" for gate, cnt in top_rejects)
    else:
        reject_lines = "거절 0건 (전부 통과)"

    return f"""
<section class="section" id="verify">
  <div class="section__head">
    <div class="section__title">
      <span class="section__icon section__icon--auto">🛡️</span>
      <h2>검증부 게이트</h2>
      <span class="section__badge">{mode_badge}</span>
    </div>
    <div class="section__subtitle">
      <div class="section__amount" style="color:{rate_color}">통과율 {pass_rate:.0f}%</div>
      <div>최근 {window}일 · {passed}통과 / {rejected}거절</div>
    </div>
  </div>
  <div class="section__body">
    <div style="padding:10px">
      <div class="row">
        <div class="row__main">
          <div class="row__name">📥 매수 게이트(15) / 📤 매도 게이트(8)</div>
          <div class="row__sub">
            매수: {buy.get('passed',0)}/{buy.get('total',0)} 통과 ({buy.get('pass_rate_pct',0):.0f}%)
            · 매도: {sell.get('passed',0)}/{sell.get('total',0)} 통과 ({sell.get('pass_rate_pct',0):.0f}%)
          </div>
        </div>
      </div>
      <div class="row">
        <div class="row__main">
          <div class="row__name">🚫 거절 사유 TOP</div>
          <div class="row__sub">{reject_lines}</div>
        </div>
      </div>
      <div style="background:#fef9c3;border:1px solid #d97706;padding:10px;border-radius:8px;margin-top:10px;font-size:13px">
        ℹ️ <b>{mode_badge}</b> — shadow는 검증부 판정을 기록만 하고 매매는 인라인 룰대로 진행합니다.
        판정이 인라인과 일치하는지 확인 후 enforce(실제 차단)로 전환합니다.
      </div>
    </div>
  </div>
</section>
"""


def _make_compare_card(compare_data: dict) -> str:
    """봇 vs 코스피 비교 카드 (#3) — Chart.js 라인 차트 + 초과수익 시각화."""
    if not compare_data or not compare_data.get("bot_pct"):
        return _empty_section(
            "compare", "📊", "section__icon--auto", "봇 vs 코스피",
            "초과수익", "데이터 누적 중",
            "자동매매 시작 후 일별 자산이 누적되면 봇과 코스피 비교 차트가 표시됩니다.",
        )

    bot_last   = compare_data.get("bot_last", 0)
    kospi_last = compare_data.get("kospi_last", 0)
    alpha      = compare_data.get("alpha", 0)
    days       = compare_data.get("days", 0)

    bot_color   = "#16a34a" if bot_last >= 0 else "#dc2626"
    kospi_color = "#16a34a" if kospi_last >= 0 else "#dc2626"
    alpha_color = "#16a34a" if alpha >= 0 else "#dc2626"
    alpha_sign  = "+" if alpha >= 0 else ""
    bot_sign    = "+" if bot_last >= 0 else ""
    kos_sign    = "+" if kospi_last >= 0 else ""

    chart_id = "compare-chart"
    labels    = compare_data.get("labels", [])
    bot_pct   = compare_data.get("bot_pct", [])
    kospi_pct = compare_data.get("kospi_pct", [])

    return f"""
<section class="section" id="compare" aria-label="봇 vs 코스피">
  <div class="section__head">
    <div class="section__title">
      <span class="section__icon section__icon--auto">📊</span>
      <h2>봇 vs 코스피</h2>
      <span class="section__badge">{days}일 누적</span>
    </div>
    <div class="section__subtitle">
      <div class="section__amount" style="color:{alpha_color}">초과수익 {alpha_sign}{alpha:.2f}%p</div>
      <div>
        🤖 봇 <span style="color:{bot_color};font-weight:700">{bot_sign}{bot_last:.2f}%</span>
        · 📈 코스피 <span style="color:{kospi_color};font-weight:700">{kos_sign}{kospi_last:.2f}%</span>
      </div>
    </div>
  </div>
  <div class="section__body">
    <div style="height:260px;padding:10px">
      <canvas id="{chart_id}"></canvas>
    </div>
    <div style="padding:8px 12px;font-size:12px;color:#666">
      봇 자동매매 누적 수익률(매도 실현 + 보유 평가)을 코스피 지수 변동률과 비교.
      초과수익(α)이 양수면 봇이 시장보다 우수, 음수면 시장보다 부진.
    </div>
  </div>
  <script>
  (function() {{
    const ctx = document.getElementById('{chart_id}');
    if (!ctx || !window.Chart) return;
    new Chart(ctx, {{
      type: 'line',
      data: {{
        labels: {json.dumps(labels)},
        datasets: [
          {{ label: '🤖 봇', data: {json.dumps(bot_pct)},
             borderColor: '#7c3aed', backgroundColor: 'rgba(124,58,237,0.10)',
             borderWidth: 2.5, tension: 0.3, fill: true, pointRadius: 2 }},
          {{ label: '📈 코스피', data: {json.dumps(kospi_pct)},
             borderColor: '#3b82f6', borderWidth: 2, borderDash: [4, 4],
             tension: 0.3, fill: false, pointRadius: 2 }}
        ]
      }},
      options: {{
        responsive: true, maintainAspectRatio: false,
        interaction: {{ mode: 'index', intersect: false }},
        plugins: {{
          legend: {{ display: true, position: 'top', labels: {{ font: {{ size: 12 }} }} }},
          tooltip: {{
            callbacks: {{
              label: (ctx) => ctx.dataset.label + ': ' + Number(ctx.parsed.y).toFixed(2) + '%'
            }}
          }}
        }},
        scales: {{
          x: {{ grid: {{ display: false }}, ticks: {{ color: 'var(--text-3)', maxTicksLimit: 10 }} }},
          y: {{ grid: {{ color: 'rgba(148,163,184,0.15)' }},
                ticks: {{ color: 'var(--text-3)', callback: (v) => v.toFixed(1) + '%' }} }}
        }}
      }}
    }});
  }})();
  </script>
</section>
"""


def _make_performance_card(perf: dict) -> str:
    """봇 성적표 카드 — analyze_trading_performance() 결과 시각화 (B3).

    누적 매매 / 승률 / 평균 / MDD / 섹터별 / 보유일별 / TOP 종목.
    """
    if not perf or perf.get("trades", 0) == 0:
        return _empty_section(
            "performance", "📈", "section__icon--auto", "봇 성적표",
            "최근 30일 누적", "아직 누적 매매 데이터 없음",
            "자동매매 시작 후 매매 완료(매수→매도) 데이터가 모이면 표시됩니다.",
        )

    trades   = perf.get("trades", 0)
    wins     = perf.get("wins", 0)
    losses   = perf.get("losses", 0)
    win_rate = perf.get("win_rate", 0)
    avg_win  = perf.get("avg_win", 0)
    avg_loss = perf.get("avg_loss", 0)
    avg_hold = perf.get("avg_hold_days", 0)
    wr_color = "#16a34a" if win_rate >= 50 else "#dc2626"

    rows = []

    # MDD
    mdd = perf.get("mdd", {})
    if mdd.get("mdd_pct", 0) < 0:
        rows.append(f"""
    <div class="row">
      <div class="row__main">
        <div class="row__name">📉 최대 낙폭 (MDD)</div>
        <div class="row__sub">{mdd.get('peak_date', '?')} 피크 → {mdd.get('trough_date', '?')} 저점 ({mdd.get('mdd_amount', 0):+,}원)</div>
      </div>
      <div class="row__price">
        <div class="row__current" style="color:#dc2626">{mdd.get('mdd_pct', 0):.1f}%</div>
        <div class="row__pnl"><small>peak→trough</small></div>
      </div>
    </div>""")

    # 점수대별
    sb = perf.get("score_buckets", {})
    bucket_parts = []
    for label, data in sb.items():
        if data.get("trades", 0) > 0:
            wr = data.get("win_rate", 0)
            color = "#16a34a" if wr >= 50 else "#dc2626"
            bucket_parts.append(f'<span style="color:{color}">{label}: {wr:.0f}% ({data["trades"]}건)</span>')
    if bucket_parts:
        rows.append(f"""
    <div class="row">
      <div class="row__main">
        <div class="row__name">🎯 점수대별 승률</div>
        <div class="row__sub">{" · ".join(bucket_parts)}</div>
      </div>
    </div>""")

    # 섹터별 TOP 5
    sp = perf.get("sector_perf", [])
    if sp:
        sec_parts = []
        for s in sp[:5]:
            color = "#16a34a" if s["win_rate"] >= 50 else "#dc2626"
            sec_parts.append(f'<span style="color:{color}">{s["sector"]} {s["win_rate"]:.0f}% ({s["trades"]})</span>')
        rows.append(f"""
    <div class="row">
      <div class="row__main">
        <div class="row__name">🏷️ 섹터별 승률</div>
        <div class="row__sub">{" · ".join(sec_parts)}</div>
      </div>
    </div>""")

    # 보유일별
    hp = perf.get("hold_perf", [])
    if hp:
        h_parts = [f'{h["range"]} {h["win_rate"]:.0f}% ({h["trades"]})' for h in hp]
        rows.append(f"""
    <div class="row">
      <div class="row__main">
        <div class="row__name">⏱️ 보유일별 승률</div>
        <div class="row__sub">{" · ".join(h_parts)}</div>
      </div>
    </div>""")

    # 매수 시간대별 (자가학습 — 어느 시간대 매수가 더 좋은지)
    bhp = perf.get("buy_hour_perf", [])
    if bhp:
        b_parts = []
        for h in bhp:
            color = "#16a34a" if h["win_rate"] >= 50 else "#dc2626"
            b_parts.append(f'<span style="color:{color}">{h["bucket"]}: {h["win_rate"]:.0f}% ({h["trades"]}건, 평균 {h["avg_pnl"]:+.1f}%)</span>')
        rows.append(f"""
    <div class="row">
      <div class="row__main">
        <div class="row__name">🕘 매수 시간대별 승률</div>
        <div class="row__sub">{" · ".join(b_parts)}</div>
      </div>
    </div>""")

    # 매도 시간대별
    shp = perf.get("sell_hour_perf", [])
    if shp:
        s_parts = []
        for h in shp:
            color = "#16a34a" if h["win_rate"] >= 50 else "#dc2626"
            s_parts.append(f'<span style="color:{color}">{h["bucket"]}: {h["win_rate"]:.0f}% ({h["trades"]}건)</span>')
        rows.append(f"""
    <div class="row">
      <div class="row__main">
        <div class="row__name">🕒 매도 시간대별 승률</div>
        <div class="row__sub">{" · ".join(s_parts)}</div>
      </div>
    </div>""")

    # 최고 / 최악 TOP 3
    winners = perf.get("top_winners", [])
    losers  = perf.get("top_losers", [])
    if winners:
        w_parts = [f'{w["name"]} +{w["pnl_pct"]:.1f}%' for w in winners if w["pnl_pct"] > 0]
        if w_parts:
            rows.append(f"""
    <div class="row">
      <div class="row__main">
        <div class="row__name">🏆 최고 수익 TOP 3</div>
        <div class="row__sub" style="color:#16a34a">{" · ".join(w_parts)}</div>
      </div>
    </div>""")
    if losers:
        l_parts = [f'{l["name"]} {l["pnl_pct"]:.1f}%' for l in losers if l["pnl_pct"] < 0]
        if l_parts:
            rows.append(f"""
    <div class="row">
      <div class="row__main">
        <div class="row__name">⚠️ 최대 손실 TOP 3</div>
        <div class="row__sub" style="color:#dc2626">{" · ".join(l_parts)}</div>
      </div>
    </div>""")

    return f"""
<section class="section" id="performance" aria-label="봇 성적표">
  <div class="section__head">
    <div class="section__title">
      <span class="section__icon section__icon--auto">📈</span>
      <h2>봇 성적표</h2>
      <span class="section__badge">최근 30일</span>
      <span class="section__count">{trades}건</span>
    </div>
    <div class="section__subtitle">
      <div class="section__amount" style="color:{wr_color}">승률 {win_rate:.0f}%</div>
      <div>{wins}승 / {losses}패 · 평균 수익 +{avg_win:.1f}% / 평균 손실 {avg_loss:.1f}% · 평균 보유 {avg_hold:.1f}일</div>
    </div>
  </div>
  <div class="section__body">{"".join(rows)}
  </div>
</section>
"""


def _make_trade_history_card(limit: int = 30) -> str:
    """거래 이력 카드 — positions.json history 기반.

    사용자 메모 대체용: 봇이 모든 매수/매도 기록을 자동 저장.
    매수: 종목/수량/가격/시각/태그(🚀/🎯/📊)
    매도: 종목/수량/가격/사유/손익/AI 의견/시각
    최근 N건 (날짜+시각 역순).
    """
    from stock import load_positions  # lazy: 순환 import 회피
    try:
        pos = load_positions()
        history = pos.get("history", [])
        if not history:
            return _empty_section(
                "trades", "📜", "section__icon--auto", "거래 이력",
                "최근 30건", "아직 매매 기록 없음",
                "자동매매 시작 후 모든 매수/매도 기록이 여기에 자동으로 누적됩니다.",
            )

        # 최근순 정렬 (date + time)
        def _sort_key(h):
            return (h.get("date", ""), h.get("time", "00:00"))
        recent = sorted(history, key=_sort_key, reverse=True)[:limit]

        rows = []
        for h in recent:
            side  = h.get("side", "")
            name  = h.get("name", "?")
            qty   = h.get("qty", 0)
            price = h.get("price", 0)
            date  = h.get("date", "")
            tm    = h.get("time", "")
            stamp = f"{date[5:].replace('-', '/')} {tm}" if date else tm

            if side == "buy":
                tag    = h.get("tag", "📊 스윙")
                reason = h.get("reason", "")
                amount = h.get("amount", 0)
                rows.append(f"""
    <div class="row">
      <div class="row__main">
        <div class="row__name">🟦 매수: {tag} {name}</div>
        <div class="row__sub">{qty}주 × {price:,.0f}원 = {amount:,.0f}원 · {reason} · <small>{stamp}</small></div>
      </div>
    </div>""")
            elif side == "sell":
                profit = h.get("profit", 0)
                pct    = h.get("pct", 0)
                bp     = h.get("buy_price", 0)
                reason = h.get("reason", "")
                ai_op  = h.get("ai_opinion", "")
                journal = h.get("journal", "")
                emoji  = "🟢" if profit > 0 else ("🔴" if profit < 0 else "⚪")
                p_color = "#16a34a" if profit > 0 else "#dc2626"
                p_sign  = "+" if profit > 0 else ""
                # AI 의견 (매도 직전) + 일기 (매도 후 회고, 학습용)
                extras = []
                if ai_op:
                    extras.append(f'<small style="color:#0369a1">💭 {ai_op}</small>')
                if journal:
                    extras.append(f'<small style="color:#7c3aed">📝 {journal}</small>')
                extras_html = ("<br>  " + "<br>  ".join(extras)) if extras else ""
                rows.append(f"""
    <div class="row">
      <div class="row__main">
        <div class="row__name">{emoji} 매도: {name}</div>
        <div class="row__sub">{qty}주 × {price:,.0f}원 (매수가 {bp:,.0f}원) · {reason} · <small>{stamp}</small>{extras_html}</div>
      </div>
      <div class="row__price">
        <div class="row__current" style="color:{p_color}">{p_sign}{profit:,.0f}원</div>
        <div class="row__pnl" style="color:{p_color}"><small>{p_sign}{pct:.1f}%</small></div>
      </div>
    </div>""")

        # 통계: 총 매수 / 매도 / 누적 손익
        total_buys    = sum(1 for h in history if h.get("side") == "buy")
        total_sells   = sum(1 for h in history if h.get("side") == "sell")
        total_profit  = sum(h.get("profit", 0) for h in history if h.get("side") == "sell")
        profit_color  = "#16a34a" if total_profit >= 0 else "#dc2626"
        profit_sign   = "+" if total_profit >= 0 else ""

        # 5/29: 거래 이력 압축 — 5건 펼침 + 나머지 접힘
        visible_rows = rows[:5]
        hidden_rows  = rows[5:]
        hidden_html = ""
        if hidden_rows:
            hidden_html = f"""
  <details style="margin-top:8px;padding:8px 16px;cursor:pointer;">
    <summary style="color:var(--text-2);font-size:13px;font-weight:600;">
      📂 이전 {len(hidden_rows)}건 더 보기
    </summary>
    <div style="margin-top:8px;">{"".join(hidden_rows)}</div>
  </details>"""

        return f"""
<section class="section" id="trades" aria-label="거래 이력">
  <div class="section__head">
    <div class="section__title">
      <span class="section__icon section__icon--auto">📜</span>
      <h2>거래 이력</h2>
      <span class="section__badge">최근 {len(visible_rows)}건 표시 / 총 {len(history)}건</span>
    </div>
    <div class="section__subtitle">
      <div class="section__amount" style="color:{profit_color}">{profit_sign}{total_profit:,.0f}원</div>
      <div>매도 누적 손익 (매수 {total_buys} / 매도 {total_sells})</div>
    </div>
  </div>
  <div class="section__body">{"".join(visible_rows)}</div>
  {hidden_html}
</section>
"""
    except Exception as e:
        print(f"  [trade_history] 카드 생성 오류: {e}")
        return ""


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


# ===== Phase 2 4단계 4차-B (5/29): AI 카드 → 대시보드부 =====

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


def _make_personal_coach_card(personal_brief: str, risk: dict = None) -> str:
    """🦾 AI 맞춤 비서 카드 — 마크다운→HTML 변환 + 위험 지수 게이지."""
    from stock import _md_to_html  # _make_risk_gauge_html은 4차-C로 같은 모듈에 있음
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
    from stock import _md_to_html
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


# ===== Phase 2 4단계 4차-C (5/29): 시장 카드 → 대시보드부 =====

def dart_alerts_section_html(dart_alerts: list) -> str:
    from stock import SIGNAL_DEFS
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


# ===== Phase 2 4단계 5차 (5/29): 사이드바·헤더 → 대시보드부 =====

def _make_hero_header(today: str, time_str: str, mood: dict, fg: dict,
                      total_value: float, total_pnl: float, total_pct: float) -> str:
    """대시보드 상단 Hero 헤더 — 그라데이션 배경 + 핵심 KPI."""
    from stock import _pnl_class
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


def _make_sidebar(sections_status: dict, last_update: str) -> str:
    """좌측 사이드바 — 부서별 네비게이션 (5/31 부서 재편).

    화면이 부서별 <details> 섹션으로 묶여 있어 사이드바도 부서 단위로 이동.
    클릭 시 접힌 부서는 JS가 자동으로 펼침 (dashboard JS).
    """
    # 부서 단위 네비 (각 부서 <details id="dept-..."> 앵커로 이동)
    items = [
        ("overview",       "🏠", "요약",       True),
        ("dept-finance",   "💰", "재무부",     True),
        ("dept-verify",    "🛡️", "검증부",     True),
        ("dept-recommend", "📈", "추천부",     True),
        ("dept-market",    "🌐", "시장정보부", True),
        ("dept-learning",  "🧠", "학습부",     True),
        ("dept-alert",     "🔔", "알림부",     True),
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


# ===== Phase 2 4단계 6차 (5/29): 잔여 순수 렌더 카드 → 대시보드부 =====
# (조립자 build_and_save_dashboard는 데이터 함수 ~20개 의존 → stock.py 컨트롤러로 유지)

def _make_total_summary_section(value_holdings: list, auto_positions: list) -> str:
    """총 자산 요약 섹션 — 가치주+자동매매 합계."""
    from stock import _pnl_class
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


def _make_alerts_section() -> str:
    """📢 최근 알림 카드 — alerts.json 기반 (24시간 이내).

    텔레그램 다이어트 후 정보성 알림(임박/공시/브리핑/추천/위험)이 여기로 모임.
    카테고리별 이모지 + 시간순 (최신 위).
    """
    from finance import _load_alerts
    from datetime import datetime
    alerts = _load_alerts()
    if not alerts:
        return _empty_section(
            "alerts", "📢", "section__icon--auto", "최근 알림",
            "24시간 누적", "최근 24시간 누적된 알림이 없습니다",
            "임박 알림 / 공시 / 브리핑 / 추천 변화 / 위험 등급 등이 여기에 자동 기록됩니다.",
        )

    # 시간 역순 (최신 위)
    sorted_alerts = sorted(alerts, key=lambda a: a.get("time", ""), reverse=True)
    # 최근 30건만 표시
    sorted_alerts = sorted_alerts[:30]

    rows = []
    for a in sorted_alerts:
        emoji   = a.get("emoji", "🔵")
        title   = a.get("title", "")
        detail  = a.get("detail", "")
        level   = a.get("level", "info")
        time_iso = a.get("time", "")

        # 시간 표시 (HH:MM)
        try:
            dt = datetime.fromisoformat(time_iso)
            time_str = dt.strftime("%m/%d %H:%M")
        except Exception:
            time_str = ""

        # 레벨별 색상
        color_map = {
            "info":    "#0369a1",
            "warning": "#d97706",
            "danger":  "#dc2626",
        }
        color = color_map.get(level, "#0369a1")

        rows.append(f"""
    <div class="row">
      <div class="row__main">
        <div class="row__name">{emoji} {title}</div>
        <div class="row__sub" style="color:{color}">{detail} · <small>{time_str}</small></div>
      </div>
    </div>""")

    # 카테고리별 카운트
    cat_counts = {}
    for a in alerts:
        cat = a.get("category", "기타")
        cat_counts[cat] = cat_counts.get(cat, 0) + 1
    cat_summary = " · ".join(
        f"{c}: {n}"
        for c, n in sorted(cat_counts.items(), key=lambda x: -x[1])
    )

    return f"""
<section class="section" id="alerts" aria-label="최근 알림">
  <div class="section__head">
    <div class="section__title">
      <span class="section__icon section__icon--auto">📢</span>
      <h2>최근 알림</h2>
      <span class="section__badge">최근 24h</span>
      <span class="section__count">{len(alerts)}건</span>
    </div>
    <div class="section__subtitle">
      <div class="section__amount">{cat_summary}</div>
    </div>
  </div>
  <div class="section__body">{"".join(rows)}
  </div>
</section>
"""


def _make_tomorrow_picks_section(tp_data: dict) -> str:
    """내일/오늘 사전 후보 섹션 — tomorrow_picks.json 기반.

    어제 장마감/미장마감 분석으로 추출된 강세 후보. autobuy 우선 매수 대상.
    카드에는 점수 보너스 + 사유 + 섹터 가중치 표시.
    """
    if not tp_data or not tp_data.get("picks"):
        return _empty_section(
            "tomorrow", "🎯", "section__icon--auto", "사전 매수 후보",
            "tomorrow_picks", "아직 사전 후보가 없습니다",
            "평일 15:35 장 마감 분석 후 다음 거래일 강세 종목 TOP 20이 자동 등록됩니다.",
        )
    picks  = tp_data.get("picks", [])
    date   = tp_data.get("date", "")
    sw     = tp_data.get("sector_weights", {})
    rows = []
    for p in picks:
        name   = p.get("name", "?")
        sector = p.get("sector", "")
        bonus  = p.get("score_bonus", 0)
        chg    = p.get("today_change", 0)
        score  = p.get("today_score", 0)
        sec_w  = sw.get(sector, 0)
        # 섹터 가중치 표시
        sec_tag = ""
        if sec_w > 0:
            sec_tag = f' · <span style="color:#16a34a">섹터 +{sec_w}</span>'
        elif sec_w < 0:
            sec_tag = f' · <span style="color:#dc2626">섹터 {sec_w}</span>'
        rows.append(f"""
    <div class="row">
      <div class="row__main">
        <div class="row__name">🎯 {name}</div>
        <div class="row__sub">{sector} · 어제 +{chg:.1f}% / 점수 {score} · 보너스 +{bonus}{sec_tag}</div>
      </div>
      <div class="row__price">
        <div class="row__current" style="color:#0369a1">+{bonus}점</div>
        <div class="row__pnl"><small>가산</small></div>
      </div>
    </div>""")

    # 섹터 가중치 요약 (TOP 영향 섹터)
    sw_summary = ""
    if sw:
        sw_sorted = sorted(sw.items(), key=lambda x: -abs(x[1]))[:5]
        sw_parts = [
            f'<span style="color:{"#16a34a" if v>0 else "#dc2626"}">{k} {"+" if v>0 else ""}{v}</span>'
            for k, v in sw_sorted
        ]
        sw_summary = f'<div class="section__subtitle"><div class="section__amount">섹터 영향:</div><div>{" · ".join(sw_parts)}</div></div>'

    return f"""
<section class="section" id="tomorrow" aria-label="사전 매수 후보">
  <div class="section__head">
    <div class="section__title">
      <span class="section__icon section__icon--auto">🎯</span>
      <h2>사전 매수 후보</h2>
      <span class="section__badge">{date} 우선순위</span>
      <span class="section__count">{len(picks)}종목</span>
    </div>
    {sw_summary}
  </div>
  <div class="section__body">{"".join(rows)}
  </div>
</section>
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

