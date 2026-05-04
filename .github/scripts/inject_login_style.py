#!/usr/bin/env python3
"""staticrypt가 만든 로그인 페이지에 자비스 고급 디자인 CSS 주입.

- 다크 그라디언트 배경 (navy → indigo → purple)
- 글래스모피즘 카드 (반투명 + backdrop-blur)
- 그라디언트 글로우 보더
- 자비스 톤 (보라 #7c3aed → 핑크 #ec4899)
"""
import sys

CUSTOM_CSS = """
<style id="jarvis-login-style">
:root {
  --jv-purple: #7c3aed;
  --jv-pink:   #ec4899;
  --jv-amber:  #f59e0b;
}
html, body { margin: 0; padding: 0; height: 100%; }
.staticrypt-html, .staticrypt-body {
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Pretendard", "Apple SD Gothic Neo", sans-serif;
  background: linear-gradient(135deg, #0a0e1a 0%, #1a1d3a 35%, #2d1b6e 75%, #4c1d95 100%);
  background-attachment: fixed;
  min-height: 100vh;
  color: #fff;
}
/* 배경에 별빛 같은 미세한 패턴 */
.staticrypt-body::before {
  content: ""; position: fixed; inset: 0; pointer-events: none; z-index: 0;
  background:
    radial-gradient(circle at 20% 30%, rgba(124,58,237,0.15) 0%, transparent 40%),
    radial-gradient(circle at 80% 70%, rgba(236,72,153,0.12) 0%, transparent 40%),
    radial-gradient(circle at 50% 50%, rgba(245,158,11,0.06) 0%, transparent 50%);
}
.staticrypt-page {
  position: relative; z-index: 1;
  display: flex !important; align-items: center; justify-content: center;
  min-height: 100vh; padding: 24px;
  background: transparent !important;
}
.staticrypt-form {
  position: relative;
  width: 100%; max-width: 420px;
  background: rgba(255,255,255,0.05) !important;
  backdrop-filter: blur(24px) saturate(160%);
  -webkit-backdrop-filter: blur(24px) saturate(160%);
  border: 1px solid rgba(255,255,255,0.12) !important;
  border-radius: 20px !important;
  padding: 44px 40px !important;
  box-shadow:
    0 25px 50px -12px rgba(0,0,0,0.6),
    0 0 0 1px rgba(255,255,255,0.05),
    inset 0 1px 0 rgba(255,255,255,0.1) !important;
  animation: jv-fade-in 0.6s cubic-bezier(0.4,0,0.2,1);
}
/* 그라디언트 글로우 보더 */
.staticrypt-form::before {
  content: "";
  position: absolute; inset: -2px;
  background: linear-gradient(135deg, var(--jv-purple), var(--jv-pink), var(--jv-amber));
  border-radius: 22px;
  z-index: -1; opacity: 0.5; filter: blur(14px);
  animation: jv-glow 4s ease-in-out infinite;
}
@keyframes jv-glow {
  0%, 100% { opacity: 0.4; }
  50% { opacity: 0.7; }
}
@keyframes jv-fade-in {
  from { opacity: 0; transform: translateY(12px); }
  to { opacity: 1; transform: translateY(0); }
}
.staticrypt-title {
  color: #fff !important;
  font-size: 26px !important; font-weight: 800 !important;
  letter-spacing: -0.5px; text-align: center; margin: 0 0 12px !important;
  background: linear-gradient(135deg, #fff 0%, #c4b5fd 100%);
  -webkit-background-clip: text; -webkit-text-fill-color: transparent;
  background-clip: text;
}
.staticrypt-instructions {
  color: rgba(255,255,255,0.7) !important;
  font-size: 13px !important; line-height: 1.6;
  text-align: center; margin: 0 0 28px !important;
  letter-spacing: 0.1px;
}
.staticrypt-hr {
  border: none !important;
  border-top: 1px solid rgba(255,255,255,0.08) !important;
  margin: 0 0 22px !important;
}
.staticrypt-password-container {
  position: relative; margin-bottom: 18px !important;
}
#staticrypt-password {
  background: rgba(255,255,255,0.06) !important;
  border: 1.5px solid rgba(255,255,255,0.12) !important;
  border-radius: 12px !important;
  padding: 14px 48px 14px 18px !important;
  color: #fff !important; font-size: 15px !important;
  width: 100%; box-sizing: border-box;
  transition: all 0.2s ease;
  outline: none !important;
}
#staticrypt-password::placeholder { color: rgba(255,255,255,0.4); font-weight: 400; }
#staticrypt-password:focus {
  border-color: var(--jv-purple) !important;
  background: rgba(255,255,255,0.08) !important;
  box-shadow: 0 0 0 4px rgba(124,58,237,0.18) !important;
}
.staticrypt-toggle-password-visibility {
  position: absolute; right: 12px; top: 50%; transform: translateY(-50%);
  background: transparent !important; border: none; cursor: pointer;
  color: rgba(255,255,255,0.5) !important;
  padding: 6px; transition: color 0.2s;
}
.staticrypt-toggle-password-visibility:hover { color: rgba(255,255,255,0.85) !important; }
.staticrypt-remember {
  display: flex; align-items: center; gap: 8px;
  margin-bottom: 22px !important;
  color: rgba(255,255,255,0.7) !important;
  font-size: 13px;
  cursor: pointer;
}
#staticrypt-remember {
  width: 17px; height: 17px;
  accent-color: var(--jv-purple);
  cursor: pointer;
}
#staticrypt-remember-label { cursor: pointer; user-select: none; }
.staticrypt-decrypt-button {
  width: 100%;
  background: linear-gradient(135deg, var(--jv-purple) 0%, var(--jv-pink) 100%) !important;
  color: #fff !important;
  border: none !important;
  border-radius: 12px !important;
  padding: 15px 24px !important;
  font-weight: 700 !important; font-size: 15px !important;
  letter-spacing: 0.3px;
  cursor: pointer;
  transition: transform 0.15s ease, box-shadow 0.2s ease, filter 0.2s ease !important;
  box-shadow: 0 8px 20px -4px rgba(124,58,237,0.5) !important;
  text-shadow: 0 1px 2px rgba(0,0,0,0.15);
}
.staticrypt-decrypt-button:hover {
  transform: translateY(-1px);
  box-shadow: 0 12px 28px -4px rgba(124,58,237,0.65) !important;
  filter: brightness(1.08);
}
.staticrypt-decrypt-button:active {
  transform: translateY(0);
  box-shadow: 0 4px 12px -2px rgba(124,58,237,0.5) !important;
}
.staticrypt-spinner-container { color: rgba(255,255,255,0.7) !important; }

@media (max-width: 480px) {
  .staticrypt-form { padding: 36px 28px !important; }
  .staticrypt-title { font-size: 22px !important; }
}
</style>
"""

def main():
    if len(sys.argv) < 2:
        print("usage: inject_login_style.py <html_path>")
        return 1
    path = sys.argv[1]
    try:
        with open(path, "r", encoding="utf-8") as f:
            html = f.read()
    except FileNotFoundError:
        print(f"  [login_style] {path} 없음 — 스킵")
        return 0

    if "jarvis-login-style" in html:
        print("  [login_style] 이미 적용됨 — 스킵")
        return 0
    if "</head>" not in html:
        print("  [login_style] </head> 없음 — 스킵")
        return 0
    # staticrypt 페이지인지 확인
    if "staticrypt" not in html:
        print("  [login_style] staticrypt 페이지 아님 — 스킵 (이미 복호화됨)")
        return 0

    new_html = html.replace("</head>", CUSTOM_CSS + "\n</head>", 1)
    with open(path, "w", encoding="utf-8") as f:
        f.write(new_html)
    print(f"  [login_style] 자비스 디자인 주입 완료 → {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
