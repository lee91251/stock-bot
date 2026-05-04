#!/usr/bin/env python3
"""staticrypt 로그인 페이지에 자비스 HUD 디자인 주입 (Iron Man 톤).

- 검정 우주 배경 + 별빛 + nebula
- 미세한 헬로그래픽 그리드 (HUD 느낌)
- 다크 글래스 카드 (rgba 검정 + cyan 보더)
- cyan/blue/purple 그라디언트 액센트
- 스캔 라인 애니메이션 (위에서 아래로 흘러감)
"""
import sys

CUSTOM_CSS = """
<style id="jarvis-login-style">
:root {
  --jv-cyan:   #06b6d4;
  --jv-cyan-2: #0891b2;
  --jv-blue:   #3b82f6;
  --jv-purple: #7c3aed;
  --jv-pink:   #ec4899;
}
* { box-sizing: border-box; }
html, body {
  margin: 0 !important; padding: 0 !important;
  height: 100%; width: 100%;
  background: #03060f !important;
  color: #fff;
  overflow-x: hidden;
}
.staticrypt-html, .staticrypt-body {
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Pretendard", "Apple SD Gothic Neo", sans-serif;
  background: #03060f !important;
  min-height: 100vh;
  color: #fff;
  position: relative;
  overflow-x: hidden;
}

/* ── 1. 우주 배경: nebula ── */
.staticrypt-body::before {
  content: ""; position: fixed; inset: 0; pointer-events: none; z-index: 0;
  background:
    radial-gradient(ellipse 700px 500px at 25% 20%, rgba(6,182,212,0.18), transparent 60%),
    radial-gradient(ellipse 600px 800px at 75% 80%, rgba(124,58,237,0.16), transparent 60%),
    radial-gradient(ellipse 500px 400px at 50% 50%, rgba(59,130,246,0.10), transparent 70%),
    radial-gradient(ellipse 400px 300px at 90% 30%, rgba(236,72,153,0.10), transparent 60%),
    #03060f;
  background-attachment: fixed;
}

/* ── 2. 별빛 (CSS 다중 box-shadow 트릭) ── */
.staticrypt-body::after {
  content: ""; position: fixed; inset: 0; pointer-events: none; z-index: 0;
  background:
    radial-gradient(1.5px 1.5px at 8% 12%, #fff, transparent 60%),
    radial-gradient(1px 1px at 23% 45%, rgba(255,255,255,0.8), transparent),
    radial-gradient(2px 2px at 47% 18%, rgba(167,243,253,0.9), transparent 60%),
    radial-gradient(1px 1px at 62% 73%, #fff, transparent),
    radial-gradient(1.5px 1.5px at 78% 35%, rgba(255,255,255,0.7), transparent 60%),
    radial-gradient(1px 1px at 89% 88%, rgba(167,139,250,0.9), transparent),
    radial-gradient(2px 2px at 12% 78%, #fff, transparent 60%),
    radial-gradient(1px 1px at 35% 92%, rgba(255,255,255,0.6), transparent),
    radial-gradient(1.5px 1.5px at 55% 8%, rgba(186,230,253,0.9), transparent 60%),
    radial-gradient(1px 1px at 71% 56%, #fff, transparent),
    radial-gradient(1.2px 1.2px at 95% 15%, rgba(255,255,255,0.7), transparent 60%),
    radial-gradient(1px 1px at 4% 60%, #fff, transparent),
    radial-gradient(2.5px 2.5px at 38% 38%, rgba(255,255,255,0.8), transparent 60%),
    radial-gradient(1px 1px at 67% 22%, rgba(255,255,255,0.6), transparent),
    radial-gradient(1.5px 1.5px at 19% 65%, rgba(244,114,182,0.7), transparent 60%);
  animation: jv-twinkle 4s ease-in-out infinite alternate;
}
@keyframes jv-twinkle {
  0%   { opacity: 0.55; }
  100% { opacity: 0.95; }
}

/* ── 3. HUD 그리드 (헬로그래픽) ── */
.staticrypt-page {
  position: relative; z-index: 1;
  display: flex !important; align-items: center; justify-content: center;
  min-height: 100vh; padding: 24px;
  background: transparent !important;
}
.staticrypt-page::before {
  content: ""; position: absolute; inset: 0; pointer-events: none;
  background-image:
    linear-gradient(rgba(6,182,212,0.06) 1px, transparent 1px),
    linear-gradient(90deg, rgba(6,182,212,0.06) 1px, transparent 1px);
  background-size: 60px 60px;
  -webkit-mask-image: radial-gradient(ellipse at center, black 20%, transparent 75%);
          mask-image: radial-gradient(ellipse at center, black 20%, transparent 75%);
}

/* ── 4. 카드: 다크 글래스 + cyan 보더 ── */
.staticrypt-form {
  position: relative;
  width: 100%; max-width: 420px;
  background: rgba(8, 12, 22, 0.72) !important;
  backdrop-filter: blur(40px) saturate(180%);
  -webkit-backdrop-filter: blur(40px) saturate(180%);
  border: 1px solid rgba(6,182,212,0.30) !important;
  border-radius: 18px !important;
  padding: 44px 40px !important;
  box-shadow:
    0 0 0 1px rgba(6,182,212,0.15),
    0 0 60px rgba(6,182,212,0.20),
    0 25px 50px -12px rgba(0,0,0,0.8),
    inset 0 1px 0 rgba(255,255,255,0.08),
    inset 0 0 40px rgba(6,182,212,0.04) !important;
  animation: jv-fade-in 0.7s cubic-bezier(0.4,0,0.2,1);
  overflow: hidden;
}
@keyframes jv-fade-in {
  from { opacity: 0; transform: translateY(20px) scale(0.97); }
  to   { opacity: 1; transform: translateY(0) scale(1); }
}

/* 카드 모서리 cyan 코너 액센트 */
.staticrypt-form::before {
  content: ""; position: absolute; top: 12px; left: 12px;
  width: 28px; height: 28px;
  border-top: 2px solid var(--jv-cyan);
  border-left: 2px solid var(--jv-cyan);
  opacity: 0.7;
}
/* 스캔 라인 애니메이션 */
.staticrypt-form::after {
  content: ""; position: absolute; left: 0; right: 0; top: 0;
  height: 2px;
  background: linear-gradient(90deg, transparent, rgba(6,182,212,0.6), transparent);
  animation: jv-scan 3.5s ease-in-out infinite;
  pointer-events: none;
}
@keyframes jv-scan {
  0%, 100% { transform: translateY(0); opacity: 0; }
  10%      { opacity: 1; }
  50%      { transform: translateY(420px); opacity: 1; }
  60%      { opacity: 0; }
}

/* ── 5. 타이틀 (cyan 글로우) ── */
.staticrypt-title {
  color: #fff !important;
  font-size: 26px !important; font-weight: 800 !important;
  letter-spacing: 0px;
  text-align: center;
  margin: 0 0 14px !important;
  background: linear-gradient(135deg, #fff 0%, #67e8f9 100%);
  -webkit-background-clip: text; -webkit-text-fill-color: transparent;
  background-clip: text;
  text-shadow: 0 0 30px rgba(6,182,212,0.4);
}
.staticrypt-instructions {
  color: rgba(186,230,253,0.65) !important;
  font-size: 13px !important; line-height: 1.6;
  text-align: center;
  margin: 0 0 28px !important;
  letter-spacing: 0.2px;
}
.staticrypt-hr {
  border: none !important;
  border-top: 1px solid rgba(6,182,212,0.15) !important;
  margin: 0 0 22px !important;
}

/* ── 6. 입력창 ── */
.staticrypt-password-container { position: relative; margin-bottom: 18px !important; }
#staticrypt-password {
  background: rgba(0,0,0,0.4) !important;
  border: 1.5px solid rgba(6,182,212,0.30) !important;
  border-radius: 10px !important;
  padding: 14px 48px 14px 18px !important;
  color: #ffffff !important;
  font-size: 16px !important;
  font-weight: 600 !important;
  width: 100%;
  outline: none !important;
  caret-color: var(--jv-cyan) !important;
  transition: all 0.2s ease;
  -webkit-text-fill-color: #ffffff !important;
}
#staticrypt-password::placeholder {
  color: rgba(186,230,253,0.45) !important;
  font-weight: 400 !important;
  -webkit-text-fill-color: rgba(186,230,253,0.45) !important;
}
/* staticrypt 동작 안 막음 — display 클래스 보존 */
.hidden { display: none !important; }
#staticrypt-form, #staticrypt_content { pointer-events: auto !important; }
#staticrypt-password:focus {
  border-color: var(--jv-cyan) !important;
  background: rgba(6,182,212,0.06) !important;
  box-shadow: 0 0 0 3px rgba(6,182,212,0.15),
              0 0 25px rgba(6,182,212,0.25) !important;
}
.staticrypt-toggle-password-visibility {
  position: absolute; right: 12px; top: 50%; transform: translateY(-50%);
  background: transparent !important; border: none; cursor: pointer;
  color: rgba(186,230,253,0.5) !important;
  padding: 6px; transition: color 0.2s;
}
.staticrypt-toggle-password-visibility:hover { color: var(--jv-cyan) !important; }

/* ── 7. Remember me ── */
.staticrypt-remember {
  display: flex; align-items: center; gap: 8px;
  margin-bottom: 22px !important;
  color: rgba(186,230,253,0.7) !important;
  font-size: 13px;
  cursor: pointer;
}
#staticrypt-remember {
  width: 16px; height: 16px;
  accent-color: var(--jv-cyan);
  cursor: pointer;
}
#staticrypt-remember-label { cursor: pointer; user-select: none; }

/* ── 8. 버튼 (cyan→blue→purple 그라디언트) ── */
.staticrypt-decrypt-button {
  width: 100%;
  background: linear-gradient(135deg, var(--jv-cyan) 0%, var(--jv-blue) 50%, var(--jv-purple) 100%) !important;
  color: #fff !important;
  border: none !important;
  border-radius: 10px !important;
  padding: 15px 24px !important;
  font-weight: 700 !important;
  font-size: 14px !important;
  letter-spacing: 0.5px !important;
  cursor: pointer;
  transition: transform 0.15s, box-shadow 0.2s, filter 0.2s !important;
  box-shadow:
    0 0 30px rgba(6,182,212,0.35),
    0 8px 20px -4px rgba(6,182,212,0.5),
    inset 0 1px 0 rgba(255,255,255,0.2) !important;
  text-shadow: 0 1px 3px rgba(0,0,0,0.3);
  position: relative;
  overflow: hidden;
}
.staticrypt-decrypt-button:hover {
  transform: translateY(-1px);
  box-shadow:
    0 0 45px rgba(6,182,212,0.55),
    0 12px 28px -4px rgba(6,182,212,0.65),
    inset 0 1px 0 rgba(255,255,255,0.25) !important;
  filter: brightness(1.1);
}
.staticrypt-decrypt-button:active { transform: translateY(0); }

.staticrypt-spinner-container { color: rgba(186,230,253,0.7) !important; }

@media (max-width: 480px) {
  .staticrypt-form { padding: 36px 26px !important; }
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

    if "staticrypt" not in html:
        print("  [login_style] staticrypt 페이지 아님 — 스킵 (이미 복호화됨)")
        return 0
    if "</head>" not in html:
        print("  [login_style] </head> 없음 — 스킵")
        return 0

    # 기존 jarvis-login-style 제거 후 새로 주입 (디자인 갱신 대응)
    import re
    html = re.sub(r'<style id="jarvis-login-style">.*?</style>\s*', '', html, flags=re.DOTALL)
    new_html = html.replace("</head>", CUSTOM_CSS + "\n</head>", 1)
    with open(path, "w", encoding="utf-8") as f:
        f.write(new_html)
    print(f"  [login_style] 자비스 HUD 디자인 주입 완료 → {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
