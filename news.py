"""
news.py — 公司新闻 + 财联社电报
=================================
两个零成本公共源:
- 东财个股新闻 (search-api-web) - 每只股最近相关新闻
- 财联社电报 (cls.cn, 本地签名) - 全市场 7x24 实时电报

这两个补 earnings-radar 的两个空缺:
1. 个股新闻 — 红旗榜排查时知道"为什么这只票沉默/异动"
2. 实时电报 — 披露季集中期,看市场即时反应
"""
import hashlib
import time
import json
import os
import datetime
import requests

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
_TIMEOUT = 8

# 磁盘缓存
_CACHE_PATH = os.path.join(os.path.dirname(__file__), "data", "news_cache.json")
_NEWS_TTL = 3600       # 个股新闻 1 小时
_TELEGRAPH_TTL = 300   # 财联社电报 5 分钟(实时性强)
_CACHE = {}


def _load_cache():
    try:
        with open(_CACHE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_cache(c):
    try:
        os.makedirs(os.path.dirname(_CACHE_PATH), exist_ok=True)
        with open(_CACHE_PATH, "w", encoding="utf-8", newline="\n") as f:
            json.dump(c, f, ensure_ascii=False)
    except Exception:
        pass


_CACHE = _load_cache()


def _strip_html(s):
    """去除东财返回的 <em>603986</em> 高亮标签。"""
    import re
    if not s:
        return ""
    return re.sub(r"<[^>]+>", "", s)


# ============================ 个股新闻 ============================

def eastmoney_stock_news(code, page_size=10):
    """东财个股新闻(零 key)。返回 [{title, content, time, mediaName, url}]。
    注: 2026-07-23 验证时东财 search-api-web 端点已失效, 只返回 passportWeb (1 条),
    不再返回 cmsArticleWebOld 内容。函数保留供端点恢复时启用。"""
    code = str(code).zfill(6)
    now = time.time()
    cached = _CACHE.get(code)
    if cached and now - cached.get("_ts", 0) < _NEWS_TTL:
        return cached.get("data", [])

    param = json.dumps({
        "uid": "", "keyword": code, "type": ["cmsArticleWebOld"],
        "client": "web", "clientType": "web", "clientVersion": "curr",
        "param": {"cmsArticleWebOld": {
            "searchScope": "default", "sort": "default",
            "pageIndex": 1, "pageSize": page_size,
            "preTag": "<em>", "postTag": "</em>"}}
    }, separators=(",", ":"))
    url = ("https://search-api-web.eastmoney.com/search/jsonp?"
           f"cb=jQuery&param={requests.utils.quote(param)}")
    rows = []
    try:
        r = requests.get(url,
                         headers={"User-Agent": UA,
                                  "Referer": "https://so.eastmoney.com/"},
                         timeout=_TIMEOUT)
        text = r.text
        import re as _re
        m = _re.match(r"^[^(]*\((.*)\)\s*$", text.strip(), _re.DOTALL)
        if m:
            text = m.group(1)
        d = json.loads(text)
        items = (d.get("result") or {}).get("cmsArticleWebOld") or []
        for x in items:
            rows.append({
                "title": _strip_html(x.get("title", "")),
                "content": _strip_html(x.get("content", ""))[:200],
                "time": x.get("date", ""),
                "mediaName": x.get("mediaName", ""),
                "url": x.get("url", ""),
            })
    except Exception:
        pass
    # 端点失效时记一条标记 (不抛错, 但返回空)
    if not rows and now - (cached.get("_ts", 0) if cached else 0) > _NEWS_TTL:
        # 端点坏了 → 返回空 (调用方应 fall back 到公告)
        rows = []
    _CACHE[code] = {"_ts": now, "data": rows}
    _save_cache(_CACHE)
    return rows


# ============================ 财联社电报 ============================

def cls_telegraph(page_size=50):
    """财联社全市场实时电报(本地 md5(sha1()) 签名, 零 key)。
    返回 [{title, content, time}]"""
    now = time.time()
    cached = _CACHE.get("__cls__")
    if cached and now - cached.get("_ts", 0) < _TELEGRAPH_TTL:
        return cached.get("data")

    params = {"appName": "CailianpressWeb", "os": "web", "sv": "7.7.5",
              "last_time": "", "refresh_type": "1", "rn": str(page_size)}
    qs = "&".join(f"{k}={params[k]}" for k in sorted(params))
    sign = hashlib.md5(hashlib.sha1(qs.encode()).hexdigest().encode()).hexdigest()
    url = f"https://www.cls.cn/v1/roll/get_roll_list?{qs}&sign={sign}"
    try:
        r = requests.get(url, headers={"User-Agent": UA,
                                        "Referer": "https://www.cls.cn/"},
                         timeout=_TIMEOUT)
        d = r.json()
        rows = []
        for item in (d.get("data") or {}).get("roll_data", []) or []:
            ts = item.get("ctime")
            t = (datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
                 if ts else "")
            rows.append({
                "title": item.get("title") or item.get("brief") or "",
                "content": item.get("content") or item.get("brief") or "",
                "time": t,
            })
    except Exception:
        rows = []
    _CACHE["__cls__"] = {"_ts": now, "data": rows}
    _save_cache(_CACHE)
    return rows


if __name__ == "__main__":
    print("=== 江波龙 (301308) 个股新闻 ===")
    n = eastmoney_stock_news("301308", 5)
    for x in n:
        print(f"  {x['time']} | {x['mediaName']} | {x['title'][:60]}")
    print()
    print("=== 财联社电报 (10条) ===")
    t = cls_telegraph(10)
    for x in t:
        print(f"  {x['time']} | {x['title'][:70]}")