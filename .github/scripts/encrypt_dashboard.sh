#!/bin/bash
# AI 투자 비서 — 대시보드 암호화 (staticrypt) + 한국어 + 자비스 컬러
# 8개 workflow job에서 공통 호출 → 일관된 로그인 페이지 디자인.
set -uo pipefail

if [ ! -f docs/index.html ]; then
  echo "[encrypt_dashboard] docs/index.html 없음 — 스킵"
  exit 0
fi

STATICRYPT_OPTS=(
  --password "${DASHBOARD_PASSWORD:-changeme}"
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

if ! npx -y staticrypt docs/index.html "${STATICRYPT_OPTS[@]}"; then
  echo "::warning::staticrypt 실패 — docs 변경 폐기, 평문 노출 차단"
  git checkout HEAD -- docs/index.html 2>/dev/null || rm -f docs/index.html
  exit 0
fi

# 자비스 고급 로그인 디자인 (다크 그라디언트 + 글래스모피즘) 주입
python3 .github/scripts/inject_login_style.py docs/index.html || echo "::warning::login style 주입 실패"
python3 .github/scripts/inject_pwa.py docs/index.html || echo "::warning::PWA 메타 주입 실패"
