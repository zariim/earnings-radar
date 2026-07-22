"""
announce.py — 公司公告链接 (业绩类)
===================================
数据源: 东财 np-anotice-stock 公告接口 (per-stock)。
拿每只股最近的"业绩类"公告 (预告/快报/年报/季报), 拼上交所/深交所 PDF + 详情页链接。
带磁盘缓存 (data/announce_cache.json), 避免重复拉。
"""
import json
import os
import time

import em

ANN_URL = "https://np-anotice-stock.eastmoney.com/api/security/ann"
CONTENT_URL = "https://np-cnotice-stock.eastmoney.com/api/content/ann"
PERF_KW = ("业绩", "预告", "快报", "预增", "预减", "扭亏", "减亏", "首亏",
           "年度报告", "季度报告", "半年度报告")

_CACHE_PATH = os.path.join(os.path.dirname(__file__), "data", "announce_cache.json")
_TTL = 12 * 3600


def _load():
    try:
        with open(_CACHE_PATH, "r", encoding="utf-8", errors="replace") as f:
            return json.load(f)
    except Exception:
        return {}


def _save(c):
    try:
        os.makedirs(os.path.dirname(_CACHE_PATH), exist_ok=True)
        with open(_CACHE_PATH, "w", encoding="utf-8", newline="\n") as f:
            json.dump(c, f, ensure_ascii=False)
    except Exception:
        pass


_CACHE = _load()


def _detail_url(code, art_code):
    return f"https://data.eastmoney.com/notices/detail/{code}/{art_code}.html"


def _pdf_url(art_code):
    # 东财公告真 PDF (dfcfw), 直接可下载; 不是 /api/content JSON 接口
    return f"https://pdf.dfcfw.com/pdf/H2_{art_code}_1.pdf"


def latest_perf_ann(code):
    """返回该股最近一条业绩类公告 {date,title,detail_url,pdf_url}; 无则 None。"""
    code = str(code).zfill(6)
    now = time.time()
    hit = _CACHE.get(code)
    if hit and (now - hit.get("_ts", 0)) < _TTL:
        return hit.get("ann")

    url = (f"{ANN_URL}?sr=-1&page_size=20&page_index=1&ann_type=A"
           f"&stock_list={code}&f_node=0")
    d = em.fetch(url, referer="https://data.eastmoney.com/")
    lst = ((d.get("data") or {}).get("list")) or []
    ann = None
    for a in lst:
        title = a.get("title") or ""
        if any(k in title for k in PERF_KW):
            art = a.get("art_code")
            ann = {
                "art_code": art,
                "date": (a.get("notice_date") or "")[:10],
                "title": title,
                "detail_url": _detail_url(code, art),
                "pdf_url": _pdf_url(art),
            }
            break
    _CACHE[code] = {"_ts": now, "ann": ann}
    return ann


def enrich(codes, save=True):
    """批量补公告 (顺序, 受限流), 返回 {code -> ann}。"""
    out = {}
    for code in codes:
        out[code] = latest_perf_ann(code)
    if save:
        _save(_CACHE)
    return out


def fetch_content(art_code):
    """拉单条公告全文 (供看板内嵌查看)。返回 {title,date,content,pdf_url,detail_url}。"""
    art_code = str(art_code).strip()
    url = f"{CONTENT_URL}?art_code={art_code}&client_source=web&page_index=1"
    d = em.fetch(url, referer="https://data.eastmoney.com/")
    data = d.get("data") or {}
    content = data.get("notice_content") or ""
    # JSON 解析后通常已是真实换行; 兜底处理残留的字面 \n
    if "\\n" in content:
        content = content.replace("\\n", "\n")
    sec = (data.get("security") or [{}])
    code = sec[0].get("stock") if sec else ""
    return {
        "art_code": art_code,
        "title": data.get("notice_title") or "",
        "date": (data.get("notice_date") or "")[:10],
        "content": content.strip(),
        "pdf_url": _pdf_url(art_code),
        "detail_url": _detail_url(code, art_code) if code else "",
    }


if __name__ == "__main__":
    import sys
    code = sys.argv[1] if len(sys.argv) > 1 else "603986"
    a = latest_perf_ann(code)
    print(code, "->", json.dumps(a, ensure_ascii=False, indent=2))
