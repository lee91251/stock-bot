# AI 투자 비서 — CLAUDE.md

> 새 Claude Code 세션 시작 시 가장 먼저 읽을 문서. 프로젝트 컨텍스트 + 작업 히스토리.

---

## 0. 새 세션 시작 시 (Quick Start)

**📕 다음 세션에서 가장 먼저 읽을 것**:
1. 이 문서 §0 (현재 상태) — 5분
2. `Obsidian Vault/02 - 진행 일지.md` 최상단 — 1분
3. **`Obsidian Vault/09 - 투자 전략 (헌법).md`** — 필요 시만 (봇 핵심 미션 참고)

## ✅ 5/14 회장 결정 — 4트랙 진짜 알고리즘 신설 (Day 1 완성) ⭐⭐⭐⭐⭐

**회장 본질 통찰 (5/14)**:
> "스윙/단기/중기/장기 각각 다른 점수 계산 / 필터 조건 / 매수·매도 룰 — 4개 알고리즘 신설 필요. 응급처치 X, 원인 알고 고쳐야지."

**Day 1 완성 (5/14, 단일 세션)** — 원래 3일 계획을 하루에 완수:
- ✅ 4트랙 점수 함수 신설 (`stock.py` `_calc_swing_score` 외 3개) — 가중치 60/40/50/70 분리
- ✅ 4트랙 로더 (`_load_swing_top3` 외 3개, 공통 `_load_track_top3`)
- ✅ `run_market_scan` 4트랙 점수 저장 (swing_score / short_score / mid_score / long_score)
- ✅ 브리핑 + 대시보드 카드 4트랙 표시 (장기 카드 동적화)
- ✅ `card_html` period 라벨 버그 수정 (스윙 1~5일 / 단기 1~3주 / 중기 1~3개월 / 장기 3개월+)
- ✅ 자동매매 풀 우선순위 — 스윙 필터 통과 종목 먼저 분석
- ✅ 백테스트 프레임워크 (`TRACK_CONFIG` + `simulate_track` + `--4track` CLI)
- ✅ GitHub Actions `backtest-4track` job 추가
- ✅ KRX API 사고 영구 차단 (3회 재시도 + 모듈 캐시 1200→4 호출, 5/13 사고 재발 0%)

**회장 결정 (5/14 승인)**:
- 장기 트랙 = **자동매매 X** / 추천만 (헌법 원칙 그대로)
- 스윙 트랙 = OK (가치투자와 별도 자동매매 트랙)
- 구현 순서: 스윙 → 단기 → 중기 → 장기 (자동매매 안정 우선)
- 매수 결정은 여전히 `analyze()` 검증된 swing_score 사용 (점진 적용, 백테스트 검증 후 통합)

**설계 문서**: `Obsidian Vault/16 - 4트랙 알고리즘 설계 (5월 14일).md`
**커밋**: `9c254d1` (점수+로더+카드+버그) + `a17685c` (자동매매 풀) + `4e3715b` (백테스트+KRX fallback)

**효과 시점**:
- `5/15 02:00 KST` cron-job.org marketscan 트리거 → 새 캐시 + 대시보드 + GitHub Pages 자동 갱신
- 4트랙 백테스트: GitHub UI `workflow_dispatch` → `--backtest4track` 모드

**남은 작업 (다음 세션 / 5/17 이후)**:
- 실전 1회 작동 확인 + 텔레그램 메시지 검증
- 부재 매뉴얼 (`15 - 회장 부재 매뉴얼`) 4트랙 반영
- 백테스트 결과 보고 트랙별 임계값 미세 조정 (현재 모든 트랙 ≥50)
- 검증 통과 시 `analyze()` swing_score를 새 `_calc_swing_score`로 통합

## 🏛️ 6/1 복귀 후 — Phase 2 14부서 분리 (5/14 회장 확정 ⭐)

**원칙**: 4트랙 알고리즘이 *직원의 일하는 방식*을 정한 것 → 이제 *직원을 부서로 나누는 작업*.

**일정 합의**:
- 5/17~5/31 (2주) 봇 자동 운영 + 데이터 누적
- 6/1 복귀 시 회장님이 "Phase 2 시작"이라고 말씀하면 즉시 진행
- 첫 단계: 재무부 (`finance.py`, 1~2h) — [[12 - Phase 2 실행 체크리스트]]

**관련 문서**:
- [[11 - Phase 2 설계도 (14부서)]] (재무부/매수부/매도부/추천부…)
- [[12 - Phase 2 실행 체크리스트]] (7단계 / 11~14일)
- [[14 - Phase 2 의존관계 매핑]] (부서 호출 순서)

**다음 세션 행동**:
- 6/1 이전: "Phase 2 시작" 명시적 발화 없으면 *현 상태 유지* (부서 분리 자체 시작 X)
- 6/1 이후: "Phase 2 시작" 또는 "부서 만들자" 발화 시 즉시 12번 체크리스트 1단계부터 진행

---

**현재 상태 (2026-05-13 KST 기준)** — Regime-Adaptive Strategy 완성 ⭐⭐⭐⭐⭐:

🎯 **자동매매 알고리즘 전면 재설계 (5/12~5/13)**:
- 5/6~5/12 3연속 손절 (-211,400원) + 5/12 5건 매수 폭주 사고
- 회장 통찰 5건 모두 검증 (점수 결함 / 임계 우회 / 과적합 / Walk-forward / Regime-Adaptive)
- 백테스트 2회 (A 순차 + B Multi-env Grid 54조합 × 2환경)
- **Regime-Adaptive Strategy** 10중 동적 시스템 적용

**Regime-Adaptive 10중 동적** (시장 위험 지수에 따라 자동 조정):
| 항목 | 안전 | 주의 | 경계 |
|---|---|---|---|
| 점수 임계 | ≥45 | ≥50 | ≥55 |
| RSI 차단 | <65 | <65 | <60 |
| 거래량 최소 | ≥100% | ≥150% | ≥200% |
| 일일 종목 | 5 | 4 | 3 |
| 일일 금액 | 1,000만 | 800만 | 600만 |
| 추격매수 | +5% | +3% | +2% |
| 모멘텀 신호 | 허용 | 허용 | 차단 |
| 매수량 | 100% | 75% | 50% |
| + 비상정지 (오늘만, 자동 재개) — 일일 -3% / MDD -15%
| + 위험 70+ 자동 정지

**5/13 첫 실전 검증** ✅:
- 워크플로우 35건 모두 성공 (실패 0)
- 매도 6건 (한솔케미칼 +11.59% 익절 + 4종목 손절)
- 매수 0건 (Regime-Adaptive 차단)
- 비상정지 발동 + 다음날 자동 재개 검증

🚨 **회장 부재 5/17~5/31 (2주)** — 봇 자동 매매 + 데이터 누적
- 강세장 진입: 자동 적극화
- 약세장 진입: 자동 보수화
- 비상정지: 그날만 / 다음날 자동 재개
- 회장 행동: 폰 5분 점검만 (메시지 안 오면 정상)
- 자세히: [[15 - 회장 부재 매뉴얼 (5월 17-31)]]

🎯 **6/1 복귀 후**:
- 2주치 누적 데이터 분석
- C 옵션 (16h 완벽 Multi-env Grid)
- Phase 2 부서 분리 (5/14 합의 연기)

🔖 마지막 커밋: `4e3715b` (4트랙 백테스트 + KRX 사고 영구 차단, 5/14)

---

**옛 상태 (2026-05-06 KST 기준)** — Phase A + B1 + B3 완성 ⭐⭐⭐⭐:
- 🇰🇷 KR 전용 모드 / 자동매매 모의투자 (한국투자증권)
- 💼 가치주 10종목 (미래에셋 수동) — `HOLDINGS_JSON`
- 🤖 **자동매매**: 미래에셋증권 48주 보유 (5/6 첫 매수, +0.31%)
  - 매수 임계 65점 (5/4 70→65 완화) + 🚀 급등 모멘텀 +3~+5% 안전대 (5/6 추가)
  - 같은 종목 하루 1회만 매수 (5/6 추가)
  - 종목당 200만원 / 일일 5종목·1,000만원
- ⏰ cron-job.org: **09:00~15:30 15분 간격 autobuy** (5/6 30분→15분 변경)
- 🎯 **Tomorrow picks 시스템** (5/6 신규):
  - 15:35 close → 다음날 강세 후보 TOP 20 (`tomorrow_picks.json`)
  - 06:00 usclose → 미국 섹터 ETF 영향 → 한국 섹터 가중치 (-5~+5)
  - 09:00~ autobuy → picks 우선 분석 + score_bonus + sector_bonus 적용
  - 텔레그램 매수 시 🎯 사전 후보 태그
- 🦾 **자비스 비서 시스템**:
  - Phase 1: AI 맞춤 코칭 (`/추천` `/진단` `/오늘`)
  - Phase 1.5: 시장 위험 지수 0~100 → 자동매수 강도 자동 조정
  - Phase 2: 자가 학습 인프라 (B3 5/6 확장 — MDD/섹터/보유일/TOP)
  - Phase 3: 능동 비서 — 매매 임박 알림 + 위험 등급 변화 즉시 알림
- 🤖 **AI 매도 어드바이저** (B1, 5/6 신규): 매도 트리거 시 Claude 의견 텔레그램에 추가 (참고용, 자동 매도 룰은 그대로)
- 📊 **대시보드 자비스 v3** https://lee91251.github.io/stock-bot/
  - 🦾 AI 맞춤 비서 카드, 📈 자산 추이 차트, 💼 가치주 sparkline
  - 🎯 **사전 매수 후보 카드** (5/6 신규)
  - 📈 **봇 성적표 카드** (5/6 신규 — 승률/MDD/섹터별/보유일별/TOP)
  - 📢 최근 공시 카드 (DART 30분 폴링, 보유 우선)
- 💸 **Anthropic Prompt Caching** (5/6 신규): system 1500토큰 + ephemeral cache_control → API 비용 ~90% ↓
- 🔇 **알림 무음 차별화** (5/6 신규): 정보성 메시지(브리핑/요약) → 진동 X, 매수/매도/위험은 일반 알림
- 🔐 로그인 페이지 자비스 HUD 톤
- 📨 텔레그램 다이어트 v2: DART 공시 보유 종목 + ⚠️/✅만
- 🚫 휴장일 자동 차단
- 🔖 마지막 커밋: `c20479e` (B3 봇 성적표)

**📁 5/6 신규 추가 파일**:
- `tomorrow_picks.json` — 다음 거래일 매수 우선순위 (close → usclose 갱신)

**📁 기존 파일 (그대로 유지)**:
- `dashboard_cache.json`, `portfolio_history.json`, `dart_seen.json`
- `.github/scripts/encrypt_dashboard.sh`, `inject_login_style.py`

---

**📌 5/6(수) 작업 7건 — 첫 자동매매 발동 + 큰 진화** ⭐⭐⭐:

자동매매 첫 발동 (10:30, 10:36):
- 미래에셋증권 +14.5%에 두 번 매수 (cron 127분 지연 + 가격 상한 부재)
- 사용자 화면 48주 vs 봇 기록 24주 불일치 → 보정

당일 진행한 7건 커밋 (모두 push 완료):
1. `3c03cf0` — 같은 종목 하루 1회 룰 + 미래에셋 48주 보정
2. `92f543e` — 매수 +3~+5% 안전대 (추격매수 차단) + cron 회차 15분 (사용자 cron-job.org 직접 설정)
3. `66f6b31` — Tomorrow picks Phase 1+2 (장 마감 후보 추출 + 매수 우선순위)
4. `dd917aa` — Phase 3 (미국 섹터 ETF → 한국 섹터 가중치)
5. `79e366c` — Phase A (Caching + 사전 후보 카드 + 알림 무음 + 조사)
6. `5e9268e` — B1 (AI 매도 어드바이저, 참고용)
7. `c20479e` — B3 (자가학습 인프라 — MDD/섹터별/보유일별 + 봇 성적표 카드)

---

**🔥 5/7~5/13 1주일 운영 후 결정할 것**:

**다음 세션 첫 작업 추천**:
- `/bot-status` — 봇 현재 상태 한눈에
- "1주일 운영 데이터 점검" — 매수/매도 빈도, AI 의견 신뢰도
- "Anthropic 콘솔에서 캐시 히트율 확인"

## 🚨 다음 세션 첫 명령 (5/8~)

> **사용자가 "이어서 하자"라고 하면 이 섹션부터 읽으세요.**

### 사용자 통찰 (5/7 마지막 대화) ⭐⭐⭐
**"팀/부서 분리하면 어때? 재무부서, 매수부서, 매도부서, 추천부서, 알림부서로 나누고 순서대로 일해."**

→ 시스템 설계의 정석 (Microservices/Modularization 패턴). **다음 큰 작업의 방향**.

### 5/7 마지막 합의된 진행 순서

#### Phase 1: 안정화 (5/8~5/13, 1주)
- **신기능 X** — 사고 fix만
- autostash fix (`0b1d76d`) 작동 검증 → 워크플로우 실패 0건 확인
- KIS API timeout / 장 마감 매수 차단 (`ec328c7`) 검증
- 데이터 누적 (B1 5건+, B4 진행도)

#### Phase 2: 팀/부서 모듈 분리 (5/14~5/27, 4~5세션)
**사용자 아이디어 적용 — 큰 리팩토링**:
```
stock.py (9,000줄 모놀리식)
  ↓
finance.py    — positions/mirae_paper/ai_advisor_log 관리
buy.py        — 매수 분석 + 실행 + Tomorrow picks 활용
sell.py       — 매도 점검 + 손절 + 일기
recommend.py  — AI 분석 + Tomorrow picks 생성 + 매크로
notify.py     — 텔레그램 + 대시보드 알림 센터
learning.py   — B3 통계 + B4 자가학습 + AI 어드바이저 정확도
dashboard.py  — HTML 카드 생성 + Chart.js
orchestrator.py — 부서 순서대로 호출 + 단일 git push
```

**효과**:
- git push 5번 → 1번 (race condition 영구 차단)
- 한 부서 사고 ≠ 다른 부서 영향 (장애 격리)
- 코드 가독성 ↑ (부서별 1,000~2,000줄)
- 사용자 명령 "재무부 점검해줘" 같은 모듈별 확인 가능

**위험**:
- 큰 리팩토링 (10~15h, 잘못하면 다 망가짐)
- 1주일 검증 필요
- 한 번에 1~2부서씩 분할 진행 권장

#### Phase 3: Railway 이전 (6/3~6/10) ⭐ 실전 전 필수
- cron 지연 영구 해결 (실전에선 cron 지연 = 진짜 돈 손실)
- PWA Push 알림 (폰 진동)
- 부서별 다른 서버 가능 (진짜 microservices)
- 24h 텔레그램 즉시 응답

#### Phase 4: 모의 1~2개월 0사고 검증 (6/10~7월) ⭐⭐ 가장 중요
- 부서 모듈화 + Railway 적용한 봇으로 모의 운영
- **사고 0건 1~2개월 누적**해야 실전 OK
- 5/7 시점 사고 8건 → 0건까지 가야 실전 안전
- 사용자 메모리 "모의도 실전처럼" 원칙

#### Phase 5: 실전 매매 (빨라야 7월~8월)
- ⚠️ **Phase 1~4 모두 검증 완료 후만**
- 별도 KIS 실계좌 키
- `INVEST_PER_STOCK` / `SWING_MAX_DAILY_AMT` 시드 맞춤 조정
- `PAPER_TRADING=false`
- 첫 1~2주는 매수 한도 30%로 제한 (안전 마진)

> [!warning] ⚠️ 5/7 사용자 통찰 — "실전 매매로 가려면 시스템 설계가 제대로"
> 모의 단계 사고 8건 = 실전 시 진짜 돈 손실. 충분한 검증 + 시스템 설계 완료 후만 실전.
> 빠른 일정 X, 안전한 일정 O.

---

## 🎯 5/7~ 진행 작업 (인프라 모두 완료, 데이터 누적 대기)

> **이 섹션이 다음 세션 시작 시 가장 먼저 볼 작업 순서입니다.**

### ✅ 5/7 모두 완료 (이번 세션 — 5건)
1. ✅ encrypt_dashboard.sh 재시도 로직 (안전망)
2. ✅ 트레이딩 일기 AI + 학습 연결 (매도 후 AI 1줄 회고 + 과거 5건 패턴 참조)
3. ✅ 봇 vs 코스피 비교 차트 (Chart.js + α 초과수익)
4. ✅ AI 매도 어드바이저 v2 인프라 (5일 후 정확도 평가, 30건+/60%+ 활성화 권장)
5. ✅ B4 자가학습 가중치 권장 (점수/섹터/시간대/보유일 자동 분석, 30건+ 활성화)

### ⏳ 데이터 누적 후 자동 활성화 (인프라 완료, 봇이 알아서 누적)
- **B1/B4 평가 데이터 누적**: 매매 발생 시 자동 (사용자 작업 X)
- **5/13쯤** (1주일): AI 어드바이저 첫 신뢰도 검증 가능
- **5/27쯤** (~30건): B4 자가학습 권장 알림 자동 발송 (텔레그램 + 대시보드)
- **사용자 결정 후**: 권장 사항 코드 반영 (점수 임계 / 섹터 회피 / 시간대 조정)

### 🔵 큰 결정 (1~2주 운영 데이터 보고)
- **Railway 이전** ($5/월) — cron 지연 영구 해결 + 24h 응답 + PWA Push 알림 가능
- **실전 매매 전환** — PAPER → 실계좌. 시드머니 ÷ 5 = 종목당 매수금액 결정
  - 변경 위치: `INVEST_PER_STOCK` / `SWING_MAX_DAILY_AMT` / `SWING_MAX_DAILY_BUY`

### 📊 대시보드 카드 새 구조 (5/7 마지막)
```
[요약]      AI 비서 / 자산 추이 / 자산 배분
[내 계좌]   가치주(1번) / 미래에셋 모의(2번) / 자동매매(3번)
[매매]      거래 이력
[학습]      봇 성적표 / 봇 vs 코스피 / AI 신뢰도 / 자가학습(B4) / 사전 후보
[추천]      추천 / 회피
[시장]      시장 / 매크로 / AI 분석
[알림]      최근 알림 / DART / 공시 목록
```

문제 발생 시 디버깅 진입점: §9 디버깅 진입점 표.

**작업 디렉토리 구조**:
```
C:\Users\eeun4\OneDrive\Desktop\주식봇\
├── files/                            ← 실제 stock-bot 저장소 (lee91251/stock-bot, public)
│   ├── stock.py                      ← 메인 코드 (~3,500줄)
│   ├── CLAUDE.md                     ← 이 문서
│   ├── requirements.txt
│   ├── .github/workflows/daily.yml   ← workflow_dispatch만 (schedule 제거됨)
│   └── performance.json              ← 성과 추적 (자동 생성/커밋)
├── Obsidian Vault/                   ← 사용자용 위키 (8개 노트)
│   ├── 00 - 주식봇 홈.md
│   ├── 02 - 진행 일지.md             ← 매일 업데이트되는 작업 기록
│   ├── 03 - 로드맵.md
│   └── ...
└── .claude/skills/                   ← 사용자 정의 Claude Code Skills
    ├── bot-status/SKILL.md
    ├── bot-commit/SKILL.md
    ├── bot-deploy/SKILL.md
    ├── bot-bug-scan/SKILL.md
    └── bot-schedule-verify/SKILL.md
```

**git 작업 시 주의**: 파파 폴더(`주식봇/`)가 아니라 **`files/` 안의 git이 실제 저장소**. 명령어는 `git -C files <cmd>` 형식 사용.

---

## 1. 프로젝트 개요

- **소유자**: 이제훈 (eeun4623@naver.com) — **비개발자**
- **목적**: AI 기반 자동 투자 비서. 매일 정해진 시간에 텔레그램으로 국내 추천 종목 자동 전송
- **저장소**: https://github.com/lee91251/stock-bot (**public**)
- **메인 파일**: `stock.py`
- **사용자 응답 스타일**: 한국어, 비개발자 친화, 핵심 요점 위주, 줄번호/함수명 나열 X

---

## 2. 사용 기술

| 기술 | 용도 |
|---|---|
| Python (`stock.py`) | 메인 프로그램 |
| Anthropic Claude API | AI 종합 판단 (모델: `claude-sonnet-4-6`, env로 변경 가능) |
| 한국투자증권 API (KIS) | 실시간 주가 / 외국인·기관 데이터 |
| OpenDart API | 공시 정보 |
| Yahoo Finance | 매크로 데이터 (TNX, IRX, DXY) — 해외 종목 분석은 비활성화 |
| 네이버 뉴스 RSS | 뉴스 감성 분석 |
| pykrx | 코스피/코스닥 1,500종목 스캔 |
| GitHub Actions | 봇 실행 환경 (workflow_dispatch만 사용) |
| **cron-job.org** | 정시 트리거 (Asia/Seoul 시간대) |
| 텔레그램 봇 | 알림 + 명령어 응답 |

---

## 3. 환경변수 / GitHub Secrets

### 필수 Secrets (https://github.com/lee91251/stock-bot/settings/secrets/actions)
```
TELEGRAM_TOKEN
TELEGRAM_CHAT_ID
DART_API_KEY
KIS_APP_KEY
KIS_APP_SECRET
ANTHROPIC_API_KEY
HOLDINGS_JSON          ← JSON 배열, 형식: [{"code":"012450","name":"한화에어로","qty":10,"avg_price":180000}]
```

### 선택적 환경변수 (workflow `env:` 블록 또는 Secrets)
| 이름 | 기본값 | 설명 |
|---|---|---|
| `CLAUDE_MODEL` | `claude-sonnet-4-6` | Claude 모델 변경 (한 곳만 수정하면 9곳 적용) |
| `KR_ONLY` | `true` | KR 전용 모드. `false`로 변경 시 해외 종목 분석 재활성화 |

### 외부 서비스 키 (코드에 직접 반영 안 되는 것)
- **GitHub PAT** (`workflow` scope) — cron-job.org Job의 Authorization 헤더에 저장됨. 분실 시 재발급 + 6개 Job 헤더 갱신 필요
- **cron-job.org API Key** — 6개 Job 일괄 관리 시 필요. 평소엔 불필요

---

## 4. 자동 실행 스케줄 (KST 기준)

**트리거 구조**: `cron-job.org` (Asia/Seoul) → POST → `https://api.github.com/.../actions/workflows/daily.yml/dispatches` → GitHub Actions workflow → `python stock.py <mode>`

**GitHub Actions schedule은 2026-04-29 제거됨** (cron 5~9시간 지연 문제).
**텔레그램 다이어트는 2026-05-01 적용** (대시보드 도입).

| KST | mode | 텔레그램 메시지 | 대시보드 갱신 | timeout |
|---|---|---|---|---|
| 오후 16:00 | `--marketscan` | 없음 | ✅ | 120분 (당일 마감 후 1,500종목 분석 — 5/6 회장 결정으로 02:00→16:00 변경, 한국 데이터 신선도 우선) |
| 새벽 06:00 | `--usclose` | 핵심 3줄 + 대시보드 링크 | ✅ | 10분 |
| 오전 08:00 | `daily` | 없음 (다이어트로 삭제) | ✅ | 30분 |
| 오전 08:50 | `--premarket` | **가치주 TOP 5 + 시장지표 + AI 한 줄** ⭐ | ✅ | 10분 |
| 오전 09:05 | `--monitor` | 눌림목 신호만 (4번) | — | 25분 |
| 오전 09:10 | `--autobuy` | 사전알림 + 매수 결과 | ✅ | 15분 |
| 09:30~15:30 | `--autosell` | 매도 발생 시만 | ✅ | 10분 |
| 오후 15:35 | `--close` | 없음 (다이어트로 삭제) | ✅ | 10분 |

> cron-job.org 대시보드: https://console.cron-job.org/jobs
> 평일 월~금만 트리거. 휴장일(`_KRX_HOLIDAYS`)에는 자동 차단.

---

## 5. 텔레그램 봇 명령어

| 명령어 | 동작 |
|---|---|
| `국내 뭐 사?` 또는 `뭐 사?` | 국내 매수 신호 종목 TOP 5 |
| `해외 뭐 사?` | KR 전용 안내 메시지 (KR_ONLY=true 상태) |
| `종목명 어때?` | 즉시 상세 분석 |
| `종목A vs 종목B` | 두 종목 비교 |
| `종목명 200만원` | 투자 시뮬레이션 |
| `오늘 시장 어때?` | AI 시장 종합 판단 |
| `/리포트` | 즉시 리포트 전송 |

봇은 5분 폴링 방식. 즉시 응답 안 됨 (TODO: Railway 연결 시 24시간 응답).

---

## 6. 주요 분석 기능

- **가치투자**: PER, PBR, ROE, 배당수익률
- **기술적 분석**: RSI, MACD, 볼린저밴드, 지지/저항선 자동 계산
- **수급**: 외국인/기관 순매수 실시간 (KIS API)
- **심리 지표**: 공포/탐욕 지수, 투자자 심리 분석
- **뉴스**: 네이버 RSS 감성 분석, DART 공시 키워드 감지
- **필터**: 유동성/모멘텀 필터, RSI 70+ 종목 자동 제외
- **매크로**: 미국 경제지표 (금리, CPI, DXY) → KR 시장 영향 분석

---

## 7. 투자 설정

| 항목 | 값 |
|---|---|
| 종목당 투자금액 | 200만원 |
| 손절선 | −7% |
| 1차 목표 | +10% (절반 매도) |
| 2차 목표 | +20% (수익 실현 강력 권장) |
| 장기 목표 | +40% |
| 관심 섹터 | 조선, 방산, 원전, 바이오, 재생에너지 |

---

## 8. 작업 히스토리 (커밋 기반)

### 2026-05-02 (토) — 빅 업데이트 ⭐⭐⭐

#### `28ef6ef` — feat: 채팅창 보유종목 관리 (bot-holdings Skill)
- 자연어 명령: "한화에어로 10주 180000원에 샀어"
- `.claude/skills/bot-holdings/SKILL.md` + `stocks.json` (61개 종목 매핑)
- `holdings_local.json` (저장소 외부, OneDrive 동기화)
- gh CLI로 GitHub Secret HOLDINGS_JSON 자동 갱신
- 첫 등록 10종목 (총 +967만원, +44.81%): 두산에너빌리티, 롯데에너지머티리얼즈, GS건설, 한화솔루션, LG화학, SK이노베이션, SKC, HD한국조선해양, 삼성전자, 보령

#### `67d6c35` + `131f966` — security: 대시보드 비밀번호 보호 (staticrypt)
- placeholder + robots.txt로 즉시 노출 차단
- GitHub Secret `DASHBOARD_PASSWORD` 등록
- staticrypt CLI로 docs/index.html 자동 암호화 (AES-256)
- 7개 워크플로우 job에 staticrypt 단계 통합
- 안전 패턴: staticrypt 실패 시 docs 변경 폐기 (평문 노출 차단)

#### `09acc60` — feat: 대시보드 사이드바 + Hero 헤더 + 카드 시스템
- 좌측 사이드바 (섹션 네비, 활성 추적, 모바일 햄버거)
- Hero 헤더 (보라 그라데이션 + KPI 3개)
- 자산 배분 카드 (progress bar)
- CSS 디자인 시스템 (변수 기반, 다크모드 자동)
- 빈 섹션 안내 ("준비 중" 메시지)
- fixed sidebar + margin-left main 패턴 (Grid 안정성 이슈 해결)

#### `ef8b92b` — feat: 대시보드 차트 + 모달 + PWA 풀 리뉴얼 ⭐
- **Chart.js v4 도입**:
  - 자산 배분 도넛 차트 (가치주/자동매매 비중 + 중앙 총액)
  - 시장 브리핑 sparkline (코스피/S&P/VIX/달러/WTI 7일 추세)
  - 매크로 라인 차트 (TNX/IRX/DXY 30일, 이중 Y축)
- **상세 모달** (시장 브리핑 카드 클릭):
  - 320px 큰 라인 차트 + 통계 4개 + 외부 링크 (네이버/TradingView/Yahoo/Investing)
  - ESC/바깥 클릭/× 버튼 닫기
- **시계열 데이터**:
  - `_fetch_market_history()` — yfinance batch (8개 지표)
  - `history_cache.json` 1시간 캐시 (.gitignore)
- **PWA 셋업**:
  - `docs/manifest.json` + `docs/icon.svg` (보라 그라데이션 + 차트 아이콘)
  - theme-color, apple-mobile-web-app meta
  - `.github/scripts/inject_pwa.py` — staticrypt 처리 후 head 메타 자동 주입
- **레이아웃**: max-width 1100 → 1400 (우측 빈 공간 줄임)
- **달러/원 (시장가)** 라벨 — 네이버 매매기준율과 5~10원 차이 안내

**중요 발견 (사용자 cross-check)**:
- 네이버 1,477원 = 한국은행 매매기준율
- yfinance 1,471원 = 외환시장 미드 가격 (봇 사용)
- 5~10원 차이는 정상 (외환시장 구조 — OTC 시장)
- → A 옵션 (yfinance 시장가 유지) 결정

**메모리 영구 저장 (다음 세션 적용)**:
- 돈/숫자 cross-check 필수 (평단×수량=매입금액, 시세 추정 X 도구로 조회)
- 두 증권 계좌 분리 (가치주=미래에셋, 자동매매=한국투자증권)
- Mock data 사용 시 사용자에게 명확히 표시 (혼동 방지)

### 2026-05-01 (금)

#### `9f7a16c` — feat: 텔레그램 다이어트 + 대시보드 (GitHub Pages) 도입 ⭐
- 텔레그램 메시지 하루 7~12건 → 3~7건
- 일일 리포트(08:00) 텔레그램 발송 X (대시보드만)
- 08:50 장 시작 전 메시지 통합 (가치주 TOP 5 + 시장지표 + AI 한 줄)
- 미국 마감(06:00) 핵심 3줄 + 대시보드 링크
- 장마감 결산(15:35) 텔레그램 발송 X
- 모니터링 시작 알림 / 자동매수 헤더 알림 삭제
- 모니터링 신호 정리: 1,2,3,5,6,7 삭제, 4(눌림목)만 유지 (가치주 트랙 정합성)
- `build_and_save_dashboard()` 함수 추가 — `docs/index.html` 자동 생성
- `DASHBOARD_URL` 상수 (https://lee91251.github.io/stock-bot/)
- 자동매매 보유 종목 섹션 + 5분 자동 새로고침
- 모든 모드(usclose/premarket/close/marketscan/autobuy/autosell) 끝에 대시보드 갱신 + git push
- workflow YAML: `permissions: contents:write` + 각 job에 `docs/` add+commit+push

#### `f96e123` — fix: 시장 지표 표시 정밀도 통일 (소수점 2자리)
- 코스피 지수: 정수 → 2자리 (예: 2,658 → 2,658.45)
- USD/KRW: 정수 → 2자리 (예: 1,352원 → 1,352.50원)
- VIX: 1자리 → 2자리 (예: 17.5 → 17.45)
- WTI: 1자리 → 2자리, 금: 정수 → 2자리
- 외국인/기관 수급: `:.0f`/`:.1f` → `:.2f` (예: +12억 → +12.34억)
- 그대로 유지: TNX/IRX 3자리, DXY 2자리, KOSPI 변동률 2자리

#### `cfdf983` — fix: 모든 모드에 휴장일 차단 (KIS 휴장일 노이즈 방지)
- 5/1 근로자의 날에 일일 리포트/장 시작 전/모니터링/장마감 메시지 다 와서 발견
- 원인: KIS API가 휴장일에 0 또는 어제 잔여 데이터 반환 → 봇이 잘못 해석
- `_skip_if_holiday()` 헬퍼 추가 (콘솔 로그만, 텔레그램 X)
- run/run_premarket/run_monitor/run_close/run_market_scan에 가드 추가
- `_KRX_HOLIDAYS`에 2026-06-03 (제10회 지방선거) 추가
- run_us_briefing은 미국 시장이라 차단 X

### 2026-04-30 (목)

#### `49478a1` — feat: 자동매수 종목 풀 확대 + 폭락장 매수 중단 ⭐
- KR_STOCKS 26개 → KR_STOCKS + market_scan_cache 상위 50 = 최대 76개
- `_load_auto_buy_pool()` 헬퍼 추가 (캐시 신선도 3영업일 체크)
- 폭락장(KOSPI -2% 이상) 자동매수 중단
- 극도 공포(공포탐욕 ≤25) 자동매수 중단
- 공포(25~40) 매수량 50% 축소
- SWING_SCORE_MIN(70) 그대로 — 풀 확대로 자연스럽게 70점 통과 종목 ↑

#### `994830e` — feat: 백테스트 점수 임계치 분리 (BACKTEST_SCORE_MIN=55)
- 백테스트는 DART 공시 + 뉴스 빠져 있어 stock.py 대비 점수 10~15점 낮음
- stock.py SWING_SCORE_MIN(70) 그대로 유지, 백테스트만 55로 분리
- 데이터 품질 진단 추가 (외국인/기관 인식률, 최고 점수 TOP 5)

#### `50f26ec` — feat: 백테스팅 진단 출력 (점수 분포 + 차단 원인)

#### `1804a61` — fix: 자동매매 마이너 버그 + 백테스팅 tzaware 비교 오류
- 스케줄 드리프트 알림 중복 제거 (`__main__`만 호출)
- 휴장일 매매 차단 (`_KRX_HOLIDAYS` + `_is_trading_day`)
- 백테스팅 TypeError 수정 (`_now_kst().replace(tzinfo=None, ...)`)

#### `1507655` — feat: 백테스팅 엔진 추가
- pykrx 일봉 + 외국인/기관 + PER 캐시 (KR_STOCKS 26종목)
- 매매 시뮬: 다음 영업일 시초가 (현실적 갭 반영)
- 비용 반영: 수수료 0.015% + 매도세 0.18% + 슬리피지 0.1%
- 메트릭: 누적/연환산/MDD/Sharpe/승률/점수 구간별/섹터별

### 2026-04-29 (수)

#### `f026d16` — feat: KR 전용 모드 추가
- `KR_ONLY` 환경변수 (기본 true) — 해외 종목 분석/추천 비활성화
- `run()` Phase 5에서 US_STOCKS 분석 루프 스킵
- HTML/텔레그램 리포트의 해외 섹션 조건부 (us_top 비어있으면 출력 X)
- "해외 뭐 사?" → KR 전용 안내 메시지
- "뭐 사?" → 국내 TOP 5 (기존 국내 3 + 해외 3)
- AI 시장 요약 프롬프트 KR 집중 (해외 TOP3 라인 제거, "한국 섹터" 명시)
- **유지**: 미국 매크로 분석 (금리/CPI/DXY), 06:00 미국 마감 브리핑

#### `75d7c03` — chore: GitHub Actions schedule 제거
- 4월 29일 운영 결과: cron-job.org 정시 도착 검증, GitHub Actions schedule이 5~9시간 늦게 같은 작업 또 실행 → 중복 메시지
- `daily.yml`의 `on.schedule:` 블록 6개 cron 라인 모두 제거
- 각 job `if:` 단순화 (workflow_dispatch + mode만 검증)
- CLAUDE.md §4 스케줄 표 갱신

#### `4dc2773` — fix: 시간대/CPI/AI 연도 정확도 보강
- **`time.tzset()`이 GitHub Actions Ubuntu에서 datetime에 적용 안 되는 문제 발견** (16:58 KST에 모니터링이 정상 시작됨 = 코드가 UTC 07:58로 인식)
- `zoneinfo.ZoneInfo("Asia/Seoul")` 기반 `_now_kst()` 헬퍼 추가 — 시스템 TZ 무관하게 항상 KST 반환
- `datetime.now()` 31곳 모두 `_now_kst()`로 일괄 교체
- CPI 파싱 강건화 (FRED 미발표월 "." 필터링 + 디버그 로그)
- AI 시스템 프롬프트 동적화 (`_AI_SYSTEM` 상수 → `_ai_system()` 함수, KST 날짜 주입) — Claude 학습 컷오프로 인한 잘못된 연도 표기 차단

### 2026-04-28 (화)

#### `cb99bb8` — fix: 시간대/스케줄/메시지 분할 정확도 개선
- 시간대 강제 설정 1차 시도 (`os.environ['TZ']='Asia/Seoul'` + `time.tzset()`)
- `_balance_html_tags`: HTML 태그 자동 균형 보정 — 텔레그램 글자 잘림 방지
- `_check_schedule_drift`: cron 지연/조기 실행 시 텔레그램 경고 메시지
- `_is_after_market_close` / `_is_market_open` 헬퍼: 모니터 조기 종료 (장 마감 후 의미 없음)
- 일일 리포트 시간 07:30 → 08:00 KST

#### `66c06a5` — refactor: 모델명 상수화 + 봇 실행 실패 알림
- Claude 모델명 9곳 하드코딩 → `CLAUDE_MODEL` 상수 1개로 통합
- `_notify_fatal`: __main__ try/except로 감싸 처리되지 않은 예외 발생 시 텔레그램 즉시 알림 (트레이스백 마지막 8줄 포함)

#### `ac4d095` — fix: KIS 수급/거래량 파싱 + 메시지 오타
- `get_investor`: output / output1 / output2 순회 + zero_fallback 패턴 — 한 키 전체 0이어도 다른 키 시도
- 장중 모니터 거래량 지표: `avg_vol`(미존재) → `prdy_vrss_vol_rate`(전일 대비 거래량 비율) 사용
- 1차 목표 알림 오타 수정: "매수하세요" → "매도하세요"
- 시장 스캔 KIS rate limit: sleep 0.05s → 0.2s

### 2026-04-27 (월)

봇 v6.0 개발 완료 (commit `76f0435` 이전):
- Claude API + 텔레그램 인터랙티브 봇 + 지지/저항 + 성과 추적
- pykrx 1,500종목 스캔
- 미국 경제지표 + AI 매크로 분석
- 6개 시점 자동 실행 스케줄
- DART API 연동
- 알림 채널: 이메일 → 텔레그램 봇

---

## 9. 운영 메모 (새 세션을 위한)

### 자주 쓰는 명령어 (현재 작업 디렉토리: `C:\Users\eeun4\OneDrive\Desktop\주식봇`)

```bash
# 현재 봇 상태 (skill 사용 가능)
/bot-status

# 변경 후 안전 배포
/bot-deploy

# 버그 패턴 검사
/bot-bug-scan

# cron 시간 검증
/bot-schedule-verify

# git 명령은 항상 -C files 또는 cd files
git -C files status
git -C files log --oneline -10
python -c "import ast; ast.parse(open('files/stock.py', encoding='utf-8').read()); print('OK')"
```

### 자동 업로드 규칙 (이제훈님 요청)

**모든 코드 작업/배포 완료 후 자동으로 Obsidian 진행 일지에 추가** (사용자 명시 요청 불필요).

위치: `C:\Users\eeun4\OneDrive\Desktop\주식봇\Obsidian Vault\02 - 진행 일지.md`
타이밍: commit + push 직후
형식: 파일 상단(가장 최근이 위), `## YYYY-MM-DD (요일)` 섹션 추가

### 알아둘 함정/실수했던 것들

1. **`time.tzset()` 함정**: GitHub Actions Ubuntu에서 `os.environ['TZ']` + `time.tzset()` 만으로는 `datetime.now()`가 KST 반환을 보장 안 함. `zoneinfo.ZoneInfo("Asia/Seoul")` 명시적 사용 필수 (`_now_kst()` 헬퍼 사용).

2. **`datetime.now()` 직접 사용 금지**: 코드 추가/수정 시 반드시 `_now_kst()` 사용. 31곳 일괄 교체했으니 일관성 유지.

3. **워크플로우 YAML cron**:
   - cron 라인과 `github.event.schedule == '...'` 조건이 글자 단위로 정확히 일치해야 함 (현재는 schedule 트리거 제거됨)
   - KST 새벽 시간(00:00~08:59)은 UTC 전날 → DOW 한 칸 당겨짐 (`1-5` → `0-4`)

4. **서브에이전트 결과 신뢰**: 버그 스캔 등 서브에이전트가 보고한 줄번호/이슈는 직접 ±5줄 Read로 검증해야 함. 거짓 양성 흔함.

5. **저장소가 public**: 4월 29일 public 전환됨. workflow_dispatch에 PAT는 `workflow` scope만으로 충분 (private면 `repo` scope 필요했음).

6. **API 키 노출 시**: 즉시 폐기 + 재발급. 채팅에 키 값 절대 X. 본인 PC에서만 사용 후 파일 삭제.

7. **이중 실행 방지**: 워크플로우 YAML schedule 블록은 절대 다시 추가하지 말 것 (cron-job.org와 중복돼서 5~9시간 지연된 메시지가 추가로 옴).

### 디버깅 진입점 (메시지 이상 시)

| 증상 | 확인할 곳 |
|---|---|
| 메시지 시간이 이상 | `_now_kst()` 사용 확인. `zoneinfo` import 여부 |
| 글자 잘림 | `_balance_html_tags` 호출 여부 |
| 모니터링이 장 마감 후 실행 | `_is_after_market_close` 가드 |
| AI 응답에 잘못된 연도 | `_ai_system()` 동적 호출 여부 |
| KIS 외국인/기관 0 | `get_investor` zero_fallback 패턴 |
| 메시지 안 옴 (전체) | https://console.cron-job.org/jobs (Job History 응답코드) → https://github.com/lee91251/stock-bot/actions |
| 봇 실행 실패 알림 | `_notify_fatal` 트레이스백 확인 |

### 사용자 정의 Skills (.claude/skills/)

| Skill | 자동 트리거 키워드 |
|---|---|
| `bot-status` | "지금 상황", "현재 상태", "어디까지 했어" |
| `bot-commit` | "커밋해줘", "GitHub에 올려줘" |
| `bot-deploy` | "배포해줘", "안전하게 올려줘" — 검증 + 커밋 + 푸시 + Obsidian 자동 갱신 |
| `bot-bug-scan` | "버그 있어?", "문제 찾아줘" |
| `bot-schedule-verify` | "스케줄 확인", "cron 맞아?" |

---

## 10. 로드맵 (앞으로 할 것)

### 🔴 다음 세션 우선 작업

#### Phase B: 보유종목 텔레그램 등록 기능 (1~2시간)
- 자연어 명령어 파싱:
  - "삼성 10주 70,000원에 샀어" → 등록
  - "삼성 10주 샀어" → 현재가로 등록 확인
  - "삼성 5주 더 샀어" → 평균단가 재계산
  - "삼성 팔았어" → 등록 해제
  - "내 종목" / "보유 알려줘" → 평가손익 조회
- 저장 방식: `holdings_user.json` 파일 (HOLDINGS_JSON Secret과 별도)
- 자동매매 positions.json과 같은 패턴 (git 자동 커밋)
- 등록된 종목 → 대시보드에 자동 반영

#### Phase C: 가치주 적합 알림 재설계 (1~2시간)
- 단기 손절/익절 룰 폐기 (2026-05-01 삭제 완료)
- 가치주 트랙 알림 4종:
  - 🚨 펀더멘털 악화 (PER 50% 상승, ROE -5%p, 부채 증가)
  - 📰 악재 공시 (유증, 임원 매도, 큰 손실, 영업정지)
  - 💎 추매 기회 (-15% 이상 큰 폭 하락 + 펀더멘털 양호)
  - 🎯 장기 목표 (+40%, +100%) 도달 (수익 분할 매도)

### 🟡 작은 정돈 (선택)
- [ ] 자동매수 결과 알림 통합 (5종목 매수 시 5건 → 1건)
- [ ] 대시보드 디자인 다듬기 (모바일 친화 카드 레이아웃)
- [ ] 누적 성과 추적 (자동매매 시작 이후 누적 수익률/승률)
- [ ] 일일 리포트 본격 다이어트 (현재 텔레그램만 막음, 데이터는 그대로)

### 🟢 운영 데이터 수집 후 (1~2주 후)
- [ ] 5/4~5/10 운영 결과 점검 — 매수 빈도 / 점수 임계치 적정성
- [ ] 임계치 조정 (70 → 65 검토)
- [ ] 모의투자 3개월 진행 및 승률 검증

### 🔵 인프라 개선 (장기)
- [ ] Railway 서버 연결 — 텔레그램 24시간 즉시 응답 (현재 5분 폴링 한계)
- [ ] Claude API prompt caching — 비용 절감
- [ ] `_sent_alerts` 메모리 누수 — Railway 24h 운영 시 주기적 초기화

### 🟣 미래 (검증 완료 후)
- [ ] 실전 자동매매 연결 — `PAPER_TRADING=false` (별도 KIS 실계좌 키)
- [ ] 가치주 자동매매 트랙 추가 (현재 수동 + 봇 추천)
- [ ] 시장 전체 스캔 → 자동매매 통합 확장 (현재 KR_STOCKS + 시장스캔 50)

---

## 11. 사용자 요청 시 우선 참고할 곳

| 사용자 질문 | 응답 진입점 |
|---|---|
| "지금 상황" / "오늘 한 일" | `/bot-status` 또는 §0, §8 최근 커밋 |
| "버그 있어?" / "문제" | `/bot-bug-scan` |
| "스케줄 / 시간 맞아?" | `/bot-schedule-verify` (참고: cron-job.org가 트리거) |
| "수정 / 고쳐 / 추가" | 코드 수정 → `/bot-deploy` (검증+커밋+푸시+Obsidian 갱신) |
| "잘 작동하나?" | https://console.cron-job.org/jobs (History 탭) + https://github.com/lee91251/stock-bot/actions |
| "메시지 시간이 이상" | §9 디버깅 진입점 표 + `_now_kst()` 점검 |
| "비용 / 사용량" | https://console.anthropic.com/settings/usage + https://github.com/settings/billing |

---

> **이 문서가 새 세션의 시작점입니다.** 작업 전 §0 (Quick Start) → §9 (운영 메모) 먼저 읽으세요.
