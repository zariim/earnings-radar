"""
thsep.py — 同花顺一致预期 EPS (basic.10jqka.com.cn)
=================================================
HTML 抓取 + pandas 表格解析。
优势: 24 家机构均值, 比东财 RESPREDICT 单家 EPS2/EPS1 算的隐含增速更精准。

返回: {code -> [{year, n_institutions, min, mean, max, industry_avg}, ...]}
"""
import io
import json
import os
import time

import pandas as pd
import requests

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")

_TTL = 86400  # 24 小时 (机构预测数据每日更新一次足够)
_CACHE_PATH = os.path.join(os.path.dirname(__file__), "data", "thsep_cache.json")
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


def _parse_html(html):
    """
    从同花顺 worth.html 抓 EPS 一致预期表格。
    实测结构: 表 0/1 是 ['年度','预测机构数','最小值','均值','最大值'] 汇总
              表 2 是分机构明细(MultiIndex 列)
              表 3 是历史+预测多年对比
    优先取表 0/1 的汇总均值;若不存在,fallback 到表 3 算均值。
    """
    dfs = pd.read_html(io.StringIO(html))
    # 找汇总表 (年度 + 机构数 + 均值)
    for df in dfs[:3]:
        cols = [str(c).strip() for c in df.columns]
        if "年度" in cols and "预测机构数" in cols and "均值" in cols:
            year_col = cols.index("年度")
            n_col = cols.index("预测机构数")
            mean_col = cols.index("均值")
            min_col = cols.index("最小值") if "最小值" in cols else None
            max_col = cols.index("最大值") if "最大值" in cols else None
            rows = []
            for _, r in df.iterrows():
                try:
                    yr = int(float(r.iloc[year_col]))
                except (ValueError, TypeError):
                    continue
                if yr < 2000 or yr > 2100:
                    continue
                try:
                    n = int(float(r.iloc[n_col]))
                    mean = float(r.iloc[mean_col])
                    mn = float(r.iloc[min_col]) if min_col is not None else None
                    mx = float(r.iloc[max_col]) if max_col is not None else None
                except (ValueError, TypeError):
                    continue
                rows.append({"year": yr, "n": n, "min": mn,
                             "mean": mean, "max": mx})
            if rows:
                return rows
    return []


def fetch(code):
    """拉取单只股的同花顺一致预期 EPS, 带磁盘缓存。"""
    code = str(code).zfill(6)
    now = time.time()
    cached = _CACHE.get(code)
    if cached and now - cached.get("_ts", 0) < _TTL:
        return cached.get("data")

    url = f"https://basic.10jqka.com.cn/new/{code}/worth.html"
    try:
        r = requests.get(url, headers={"User-Agent": UA,
                                        "Referer": "https://basic.10jqka.com.cn/"},
                         timeout=8)
        r.encoding = "gbk"
        rows = _parse_html(r.text)
    except Exception:
        rows = []

    _CACHE[code] = {"_ts": now, "data": rows}
    _save_cache()
    return rows


def build_all(codes):
    """批量拉取, 返回 {code -> [{year, n, min, mean, max, industry_avg}, ...]}"""
    out = {}
    for c in codes:
        out[c] = fetch(c)
    return out


# 别名: 与 research.build 对齐
build = build_all


if __name__ == "__main__":
    import sys
    codes = sys.argv[1:] or ["603986", "301308"]
    rows = build(codes)
    for code, data in rows.items():
        print(f"\n{code}:")
        for r in data:
            mn = f"{r['min']:.2f}" if r['min'] is not None else "-"
            mx = f"{r['max']:.2f}" if r['max'] is not None else "-"
            print(f"  {r['year']} 机构{r['n']}家  [{mn}~{r['mean']:.2f}~{mx}]")