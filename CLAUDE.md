# AI 투자 비서 — CLAUDE.md

> 새 Claude Code 세션 시작 시 가장 먼저 읽을 문서. 프로젝트 컨텍스트 + 작업 히스토리.

---

## 0. 새 세션 시작 시 (Quick Start)

**📕 다음 세션에서 가장 먼저 읽을 것**:
1. 이 문서 §0 (현재 상태) — 5분
2. **`Obsidian Vault/09 - 투자 전략 (헌법).md`** — 10분 ⭐ 봇 개발 기준 문서
3. `Obsidian Vault/02 - 진행 일지.md` 최상단 — 1분

**현재 상태 (2026-04-30 KST 기준)**:
- 🇰🇷 KR 전용 모드 운영 중
- ⏰ cron-job.org 정시 트리거 (8개 Job 활성)
- 🤖 **자동매매 가동 중** (모의투자, 4/30~) — 첫날 매수 0건 (정상)
  - 휴장일(5/1 근로자의 날, 5/5 어린이날) 자동 차단 추가됨
  - 스케줄 드리프트 알림 중복 제거 (autobuy/autosell 내부 호출 정리)
- 📊 백테스팅 엔진 추가됨 (`backtest.py`) — tzaware/tznaive 비교 버그 수정 완료
- 📋 **투자 전략 헌법 정립 완료** (Obsidian 09번)
- 🎯 **봇 핵심 미션: 두산 같은 회생주 자동 발굴**

**다음 세션 작업**: 모의 자동매매(스윙) 점검 + 개선

**자동매매 트랙 어젠다 (우선순위 순)**:
1. **모의 매매 결과 점검** (4/30~ 며칠 결과 보기)
   - 매수 통과 종목 0개 패턴 분석
   - 점수 임계치(70) 적정한지 검토
2. ~~마이너 버그 수정 — 완료~~
   - ✅ 스케줄 어긋남 메시지 중복 제거
   - ✅ 휴장일 매매 시도 차단 (`_KRX_HOLIDAYS` + `_is_trading_day`)
3. ~~백테스팅 0건 버그 수정 — 완료~~
   - ✅ pykrx tznaive 인덱스 vs `_now_kst()` tzaware 비교 TypeError 수정
4. **텔레그램 메시지 다이어트**
   - 가치 TOP 5 + 스윙 TOP 5 분리 표시
   - 짧고 핵심만, 상세는 HTML 첨부
5. **시장 체제 인식 추가** (폭락장 자동 매수 중단)
6. **섹터 분산** (같은 섹터 2종목 max)
7. **자금 분배 결정** (스윙에 얼마? 현금 얼마?)

→ **참고 문서**: `Obsidian Vault/09 - 투자 전략 (헌법).md`
   §4 시장 상황별 전략 / §5 섹터 전략 / §11 절대 하지 말 것

**가치투자 트랙은 별도** (Phase 1~5 코딩은 자동매매 안정화 후 착수)

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

| KST | mode | 작업 | timeout |
|---|---|---|---|
| 새벽 02:00 | `--marketscan` | 코스피/코스닥 1,500종목 전체 스캔 | 120분 |
| 새벽 06:00 | `--usclose` | 미국 시장 마감 브리핑 (매크로 정보용) | 10분 |
| 오전 08:00 | `daily` | 일일 AI 리포트 (국내 TOP 5) | 30분 |
| 오전 08:50 | `--premarket` | 장 시작 전 브리핑 | 10분 |
| 오전 09:05 | `--monitor` | 장중 모니터링 (20분) | 25분 |
| 오후 15:35 | `--close` | 장 마감 결산 | 10분 |

> cron-job.org 대시보드: https://console.cron-job.org/jobs (jobId 7538004~7538009)
> 평일 월~금만 트리거.

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

### 🔴 진행 중 / 다음
- [ ] **모의투자 3개월 진행 및 승률 검증** ← 지금 시작 가능
  - KIS 모의계좌 vs 종이/엑셀 추적 결정 필요
  - 추천 종목 → 실제 매매 결과 [[02 - 진행 일지]] 또는 별도 시트 기록

### 🟡 인프라 개선 (선택)
- [ ] Railway 서버 연결 — 텔레그램 24시간 즉시 응답 (현재 5분 폴링 한계)
- [ ] Claude API prompt caching — 시스템 프롬프트 매번 재전송, 캐싱 적용 시 비용 절감
- [ ] `_sent_alerts` 메모리 누수 — Railway 24h 운영 시 주기적 초기화 필요

### 🟢 향후 (모의투자 검증 후)
- [ ] 자동 매매 연결 — KIS 주문 API + 안전장치 (일일 한도, 비상 정지)
- [ ] 백테스트 기능 — 과거 추천 종목으로 가상 수익률 검증

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
