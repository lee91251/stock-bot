#!/bin/bash
# AI 투자 비서 — 대시보드 암호화 (staticrypt) + 한국어 + 자비스 컬러
# 8개 workflow job에서 공통 호출 → 일관된 로그인 페이지 디자인.
set -uo pipefail

if [ ! -f docs/index.html ]; then
  echo "[encrypt_dashboard] docs/index.html 없음 — 스킵"
  exit 0
fi

# 이미 staticrypt 처리된 docs면 이중 처리 방지 (staticryptInitiator 중복 → JS SyntaxError)
# stock.py가 raw HTML로 덮어쓰지 않은 경우 (휴장일 즉시 return 등) 발생 가능.
if grep -q 'staticryptInitiator' docs/index.html; then
  echo "::warning::docs/index.html에 이미 staticryptInitiator 존재 — 이중 staticrypt 방지로 스킵"
  echo "  (다음 stock.py 실행에서 새 raw HTML 만들어야 정상화됨)"
  exit 0
fi

STATICRYPT_OPTS=(
  --password "${DASHBOARD_PASSWORD:-changeme}"
  --short
  -d docs/
  --template-color-primary "#03060f"
  --template-color-secondary "#0a0e1a"
  --template-page-title "AI 투자 비서"
  --template-title "🦾 AI 투자 비서"
  --template-instructions "이제훈님 전용 대시보드입니다. 비밀번호를 입력하세요."
  --template-button "잠금 해제"
  --template-placeholder "비밀번호"
  --template-remember "이 기기에서 기억"
  --template-error "비밀번호가 올바르지 않습니다."
  --template-toggle-show "표시"
  --template-toggle-hide "숨김"
)

# staticrypt 1차 시도 — 일시 장애(npm CDN, network) 대비 재시도 로직
if ! npx -y staticrypt docs/index.html "${STATICRYPT_OPTS[@]}"; then
  echo "::warning::staticrypt 1차 실패 — 5초 후 재시도"
  sleep 5
  if ! npx -y staticrypt docs/index.html "${STATICRYPT_OPTS[@]}"; then
    echo "::warning::staticrypt 2차 실패 — 10초 후 마지막 재시도"
    sleep 10
    if ! npx -y staticrypt docs/index.html "${STATICRYPT_OPTS[@]}"; then
      echo "::warning::staticrypt 최종 실패 — docs 변경 폐기, 평문 노출 차단"
      git checkout HEAD -- docs/index.html 2>/dev/null || rm -f docs/index.html
      exit 0
    fi
  fi
fi

# 자비스 고급 로그인 디자인 (다크 그라디언트 + 글래스모피즘) 주입
python3 .github/scripts/inject_login_style.py docs/index.html || echo "::warning::login style 주입 실패"
python3 .github/scripts/inject_pwa.py docs/index.html || echo "::warning::PWA 메타 주입 실패"
