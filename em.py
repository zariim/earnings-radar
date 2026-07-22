"""
em.py — 东方财富 datacenter 直连封装
================================
只用东财公开 HTTP 接口, 不依赖 iFinD / 本地 bridge。
- fetch(): 统一 GET (固定 UA + Referer + 令牌桶限流 + JSONP 剥离 + 异常兜底)
- paginate(): datacenter 报表分页聚合
- board_of(): 按代码前缀判板块 (主板/创业板/科创板/北交所)
参考移植自 full-market-funnel/eastmoney_provider.py:_fetch
"""
import time
import threading
import requests

DATA_REFERER = "https://data.eastmoney.com/"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")
DC_URL = "https://datacenter-web.eastmoney.com/api/data/v1/get"


class _HostBucket:
    """令牌桶: 限制每分钟请求数, 避免被东财限流。"""
    def __init__(self, per_min=120):
        self.capacity = per_min
        self.tokens = per_min
        self.rate = per_min / 60.0
        self.last = time.time()
        self.lock = threading.Lock()

    def take(self):
        with self.lock:
            now = time.time()
            self.tokens = min(self.capacity, self.tokens + (now - self.last) * self.rate)
            self.last = now
            if self.tokens < 1:
                sleep = (1 - self.tokens) / self.rate
                time.sleep(sleep)
                self.tokens = 0
            else:
                self.tokens -= 1


_BUCKET = _HostBucket(120)
_SESSION = requests.Session()


def fetch(url, referer=DATA_REFERER, timeout=10, retries=2):
    """统一 GET, 返回解析后的 dict; 失败返回 {'_err': ...}。"""
    _BUCKET.take()
    headers = {"User-Agent": UA, "Accept": "application/json, text/plain, */*",
               "Referer": referer}
    last_err = None
    for _ in range(retries + 1):
        try:
            r = _SESSION.get(url, headers=headers, timeout=timeout)
            r.raise_for_status()
            txt = r.text.strip()
            # 剥离 jQuery(...) / callback(...) JSONP 包裹
            if txt and not txt.startswith("{") and not txt.startswith("["):
                lp, rp = txt.find("("), txt.rfind(")")
                if lp != -1 and rp != -1:
                    txt = txt[lp + 1:rp]
            import json
            return json.loads(txt)
        except Exception as e:  # noqa
            last_err = str(e)
            time.sleep(0.6)
    return {"_err": last_err}


def paginate(report_name, filter_str="", columns="ALL", page_size=500,
             sort_col=None, sort_type=-1, referer=DATA_REFERER, max_pages=60):
    """
    分页拉取 datacenter 报表, 聚合所有行。
    注意: 只用 sty=ALL 语义 (columns=ALL), 不要传 st/sr 排序会导致 null 的报表。
    """
    rows = []
    page = 1
    while page <= max_pages:
        parts = [
            f"reportName={report_name}",
            f"columns={columns}",
            f"pageNumber={page}",
            f"pageSize={page_size}",
        ]
        if filter_str:
            parts.append(f"filter={filter_str}")
        if sort_col:
            parts.append(f"sortColumns={sort_col}")
            parts.append(f"sortTypes={sort_type}")
        url = DC_URL + "?" + "&".join(parts)
        d = fetch(url, referer=referer)
        if not d or d.get("_err"):
            break
        result = d.get("result") or {}
        data = result.get("data") or []
        if not data:
            break
        rows.extend(data)
        pages = result.get("pages") or 1
        if page >= pages:
            break
        page += 1
    return rows


def board_of(code):
    """按代码前缀判板块。移植自 full-market-funnel/dashboard.html:boardOf()。
    返回 'main'(主板) | 'gem'(创业板) | 'star'(科创板) | 'bj'(北交所)。"""
    c = str(code).zfill(6)
    p3 = c[:3]
    p2 = c[:2]
    p1 = c[:1]
    if p3 in ("600", "601", "603", "605") or p3 in ("000", "001", "002", "003"):
        return "main"
    if p3 in ("300", "301"):
        return "gem"
    if p3 == "688" or p2 == "68":
        return "star"
    if p1 in ("8", "4", "9"):
        return "bj"
    return "main"


BOARD_ZH = {"main": "主板", "gem": "创业板", "star": "科创板", "bj": "北交所"}


def is_st(name):
    n = (name or "").upper()
    return "ST" in n or "*" in n


if __name__ == "__main__":
    # 自测: 拉一页预告
    r = fetch(DC_URL + "?reportName=RPT_PUBLIC_OP_NEWPREDICT&columns=ALL"
              "&pageSize=3&pageNumber=1&filter=(REPORT_DATE='2026-06-30')")
    d = (r.get("result") or {}).get("data") or []
    print("fetch ok, rows:", len(d))
    for x in d[:3]:
        print(x.get("SECURITY_CODE"), x.get("SECURITY_NAME_ABBR"),
              x.get("PREDICT_TYPE"), x.get("ADD_AMP_LOWER"), board_of(x.get("SECURITY_CODE")))
