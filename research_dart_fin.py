# -*- coding: utf-8 -*-
"""연구용(11/20): DART 분기 재무 숫자 일괄 내려받기 — '실적 서프라이즈 뒤 주가가 더 오르나(PEAD)' 연구 재료.

매매와 무관한 일회성 작업(.github/workflows/research_fin.yml, 수동 실행). 결과는 커밋하지 않고 artifact로만.
- corpCode.xml(전체 기업코드) → 상장사(종목코드 있는 곳)만
- fnlttMultiAcnt(다중회사 주요계정, 한 번에 100사) × 연도 × 보고서(1Q/반기/3Q/사업) — 호출 약 1,300번
- 남기는 계정: 매출액·영업이익·당기순이익·자산총계·자본총계 (연결 CFS / 별도 OFS 둘 다)
"""
import os, sys, io, json, gzip, time, zipfile
import xml.etree.ElementTree as ET
from datetime import date
import requests

KEY = os.environ.get("DART_API_KEY", "")
OUT = "dart_fin.jsonl.gz"
REPRTS = ["11013", "11012", "11014", "11011"]   # 1분기, 반기, 3분기, 사업보고서
KEEP = {"매출액", "영업이익", "당기순이익", "당기순이익(손실)", "자산총계", "자본총계", "영업이익(손실)"}
Y0 = int(sys.argv[1]) if len(sys.argv) > 1 else 2016


def corp_codes():
    r = requests.get("https://opendart.fss.or.kr/api/corpCode.xml", params={"crtfc_key": KEY}, timeout=60)
    z = zipfile.ZipFile(io.BytesIO(r.content))
    root = ET.fromstring(z.read(z.namelist()[0]))
    out = []
    for el in root.iter("list"):
        sc = (el.findtext("stock_code") or "").strip()
        if sc:
            out.append(el.findtext("corp_code").strip())
    return out


def main():
    if not KEY:
        print("DART_API_KEY 없음"); sys.exit(1)
    cc = corp_codes()
    print("상장사 기업코드", len(cc))
    batches = [cc[i:i + 100] for i in range(0, len(cc), 100)]
    calls = n = 0
    with gzip.open(OUT, "wt", encoding="utf-8") as fo:
        for y in range(Y0, date.today().year + 1):
            for rp in REPRTS:
                got = 0
                for b in batches:
                    d = {}
                    for _try in range(3):
                        try:
                            r = requests.get("https://opendart.fss.or.kr/api/fnlttMultiAcnt.json",
                                             params={"crtfc_key": KEY, "corp_code": ",".join(b),
                                                     "bsns_year": str(y), "reprt_code": rp}, timeout=30)
                            d = r.json(); break
                        except Exception as e:
                            print("  재시도", e); time.sleep(3)
                    calls += 1
                    st = d.get("status")
                    if st == "020":
                        print("하루 요청 한도 초과 — 여기까지 저장"); return
                    if st != "000":
                        if st != "013":
                            print(f"  [{y} {rp}] 상태 {st} {d.get('message')}")
                        continue
                    for it in d.get("list", []):
                        if it.get("account_nm") not in KEEP:
                            continue
                        fo.write(json.dumps({k: it.get(k) for k in (
                            "rcept_no", "bsns_year", "stock_code", "reprt_code", "account_nm", "fs_div",
                            "thstrm_amount", "thstrm_add_amount", "frmtrm_amount", "frmtrm_add_amount")},
                            ensure_ascii=False) + "\n")
                        n += 1; got += 1
                    time.sleep(0.1)
                print(f"{y} {rp}: {got:,}행 (누적 {n:,}, 호출 {calls:,})", flush=True)
    print(f"완료: {n:,}행, 호출 {calls:,}")


if __name__ == "__main__":
    main()
