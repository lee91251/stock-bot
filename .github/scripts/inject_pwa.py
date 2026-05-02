"""staticrypt 처리 후 docs/index.html head에 PWA 메타/매니페스트 주입.

워크플로우에서 staticrypt 다음 단계로 호출:
    python3 .github/scripts/inject_pwa.py docs/index.html
"""
import sys

INJECTION = (
    '<link rel="manifest" href="manifest.json">'
    '<meta name="theme-color" content="#5f6dff">'
    '<link rel="icon" type="image/svg+xml" href="icon.svg">'
    '<meta name="apple-mobile-web-app-capable" content="yes">'
    '<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">'
    '<meta name="apple-mobile-web-app-title" content="투자 비서">'
    '<meta name="mobile-web-app-capable" content="yes">'
)


def inject(path: str) -> None:
    with open(path, encoding="utf-8") as f:
        html = f.read()
    if 'rel="manifest"' in html:
        return  # 이미 주입됨
    html = html.replace("</head>", INJECTION + "</head>", 1)
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"  [PWA] 메타 주입 완료 → {path}")


if __name__ == "__main__":
    inject(sys.argv[1] if len(sys.argv) > 1 else "docs/index.html")
