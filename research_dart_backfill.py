# -*- coding: utf-8 -*-
"""연구용(11/19): DART 과거 공시 목록 일괄 내려받기 — '공시 종류별로 그 뒤 주가가 떨어졌나' 사건 연구 재료.

매매와 무관한 일회성 작업(.github/workflows/research.yml, 수동 실행). 결과는 저장소에 커밋하지 않고
GitHub 실행 결과물(artifact)로만 남긴다. 공시 목록은 DART에 공개된 정보.
- list.json 은 기업코드 없이 조회하면 기간이 3개월 이내여야 함 → 분기 단위로 나눠 페이지 끝까지.
- 공시 유형: B=주요사항보고(유상증자·전환사채·자사주·감자·소송 등), I=거래소공시(실적·공급계약·조회공시 등)
"""
import os, sys, json, gzip, time
from datetime import date, timedelta
import requests

KEY = os.environ.get("DART_API_KEY", "")
BASE = "https://opendart.fss.or.kr/api/list.json"
START = date(2018, 1, 1)
END = date.today()
TYPES = sys.argv[1].split(",") if len(sys.argv) > 1 else ["B", "I"]
OUT = "dart_backfill.jsonl.gz"


def quarters(a: date, b: date):
    cur = a
    while cur <= b:
        nxt = min(date(cur.year + (cur.month + 2) // 12, (cur.month + 2) % 12 + 1, 1) - timedelta(days=1), b)
        yield cur, nxt
        cur = nxt + timedelta(days=1)


def main():
    if not KEY:
        print("DART_API_KEY 없음"); sys.exit(1)
    calls = n = 0
    with gzip.open(OUT, "wt", encoding="utf-8") as fo:
        for ty in TYPES:
            for a, b in quarters(START, END):
                page = 1
                while True:
                    for _try in range(3):
                        try:
                            r = requests.get(BASE, params={"crtfc_key": KEY, "bgn_de": a.strftime("%Y%m%d"),
                                                           "end_de": b.strftime("%Y%m%d"), "pblntf_ty": ty,
                                                           "page_no": page, "page_count": 100}, timeout=20)
                            d = r.json(); break
                        except Exception as e:
                            print("  재시도", e); time.sleep(3); d = {}
                    calls += 1
                    st = d.get("status")
                    if st == "013":          # 조회 결과 없음
                        break
                    if st != "000":
                        print(f"  [{ty} {a}~{b} p{page}] 상태 {st} {d.get('message')}")
                        if st == "020":      # 요청 한도 초과
                            print("하루 요청 한도 초과 — 여기까지 저장"); return
                        break
                    for it in d.get("list", []):
                        if it.get("stock_code"):
                            fo.write(json.dumps({"t": ty, "d": it.get("rcept_dt"), "s": it.get("stock_code"),
                                                 "n": it.get("report_nm"), "c": it.get("corp_cls")}, ensure_ascii=False) + "\n")
                            n += 1
                    tot = int(d.get("total_page", 1) or 1)
                    if page >= tot:
                        break
                    page += 1
                    time.sleep(0.08)
                print(f"{ty} {a}~{b}: 누적 {n:,}건 / 호출 {calls:,}")
    print(f"완료: {n:,}건, 호출 {calls:,}")


if __name__ == "__main__":
    main()
