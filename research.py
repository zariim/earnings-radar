"""
research.py — 东财研报列表 (reportapi.eastmoney.com)
=====================================================
提供单只股最新 N 条研报 + 字段(publishDate/orgName/title/预测 EPS/PE)。
"""
import os
import time
import json
import requests

URL = "https://reportapi.eastmoney.com/report/list"
_CACHE_PATH = os.path.join(os.path.dirname(__file__), "data", "research_cache.json")
_TTL = 6 * 3600  # 6h
_CACHE = {}


def _load_cache():
    global _CACHE
    try:
        with open(_CACHE_PATH, "r", encoding="utf-8") as f:
            _CACHE = json.load(f)
    except Exception:
        _CACHE = {}


def _save_cache():
    try:
        os.makedirs(os.path.dirname(_CACHE_PATH), exist_ok=True)
        with open(_CACHE_PATH, "w", encoding="utf-8", newline="\n") as f:
            json.dump(_CACHE, f, ensure_ascii=False)
    except Exception:
        pass


_load_cache()


def _parse_response(d):
    """解析 reportapi 返回 (兼容 JSONP / JSON)。"""
    if isinstance(d, dict):
        items = d.get("data") or []
        return [
            {
                "infoCode": x.get("infoCode"),
                "title": x.get("title", ""),
                "orgName": x.get("orgName") or x.get("orgSName", ""),
                "publishDate": (x.get("publishDate") or "")[:10],
                "rating": x.get("ratingName") or x.get("level"),
                "predictNextYearEps": x.get("predictNextYearEps"),
                "predictNextTwoYearEps": x.get("predictNextTwoYearEps"),
                "pdf": f"https://pdf.dfcfw.com/pdf/H3_{x.get('infoCode')}_1.pdf"
                if x.get("infoCode") else "",
            }
            for x in items
        ]
    return []


def fetch(code, page_size=10, max_pages=2):
    """拉单只股研报 (最新 N 条)。"""
    code = str(code).zfill(6)
    now = time.time()
    cached = _CACHE.get(code)
    if cached and now - cached.get("_ts", 0) < _TTL:
        return cached.get("data", [])
    rows = []
    for page in range(1, max_pages + 1):
        params = {
            "industryCode": "*", "pageSize": str(page_size), "industry": "*",
            "rating": "*", "ratingChange": "*",
            "beginTime": "2000-01-01", "endTime": "2030-01-01",
            "pageNo": str(page), "fields": "", "qType": "0",
            "orgCode": "", "code": code, "rcode": "",
            "p": str(page), "pageNum": str(page), "pageNumber": str(page),
        }
        try:
            r = requests.get(URL, params=params,
                             headers={"Referer": "https://data.eastmoney.com/",
                                      "User-Agent": "Mozilla/5.0"}, timeout=15)
            rows.extend(_parse_response(r.json()))
        except Exception:
            continue
    _CACHE[code] = {"_ts": now, "data": rows}
    _save_cache()
    return rows


def build(codes, top_n=3):
    """批量拉每只股的 top_n 研报, 返回 {code -> [report, ...]}."""
    out = {}
    for c in codes:
        out[c] = fetch(c)[:top_n]
    return out


if __name__ == "__main__":
    import sys
    codes = sys.argv[1:] or ["603986", "301308"]
    out = build(codes, top_n=3)
    for code, reps in out.items():
        print(f"\n{code}:")
        for r in reps:
            eps = r.get("predictNextYearEps")
            print(f"  {r['publishDate']} | {r['orgName']:<12s} | {r['title'][:50]}")
            if eps:
                print(f"      预测 26E EPS={eps} | 评级={r.get('rating')}")