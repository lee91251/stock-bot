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
