"""
q1.py — 一季报实际增速 (用于红旗榜证伪与恶化结构分析)
======================================================
数据源: 东财 datacenter RPT_LICO_FN_CPD (业绩报表 = 实际财报值)
返回每股 Q1 的 净利同比(SJLTZ) + 营收同比(YSTZ), 用于:
- 红旗证伪: 全年预期高但 Q1 净利已疲软
- 恶化结构: 营收也降(需求走弱) vs 只净利降(毛利/费用承压)
"""
import em
import dcache
import consensus

REPORT_NAME = "RPT_LICO_FN_CPD"


def _ashare_whitelist():
    """A 股代码白名单 (从 RPT_VALUEANALYSIS_DET 最近交易日全集取, ~5530 只)。"""
    return dcache.get_or_load("A_SHARE_WHITELIST", {"_q": "v1"}, _build_whitelist,
                              ttl=86400)


def _build_whitelist():
    import datetime
    today = datetime.date.today()
    for back in range(0, 8):
        d = (today - datetime.timedelta(days=back)).strftime("%Y-%m-%d")
        rows = em.paginate("RPT_VALUEANALYSIS_DET",
                            filter_str=f"(TRADE_DATE='{d}')",
                            columns="SECURITY_CODE", page_size=500, max_pages=20)
        if rows and len(rows) > 1000:
            return {str(r.get("SECURITY_CODE") or "").zfill(6) for r in rows
                    if r.get("SECURITY_CODE")}
    return set()


def fetch_q1(q1_date="2026-03-31"):
    """返回 {code -> {'np_yoy':净利同比%, 'rev_yoy':营收同比%}}, 仅 A 股 (磁盘缓存 6h)。"""
    cached = dcache.get(REPORT_NAME, {"_q": "q1_ashare", "date": q1_date})
    if cached is not None:
        return cached
    rows = em.paginate(REPORT_NAME, filter_str=f"(REPORTDATE='{q1_date}')",
                       columns="SECURITY_CODE,SJLTZ,YSTZ", page_size=500,
                       sort_col="SECURITY_CODE", sort_type=1, max_pages=15)
    wl = _ashare_whitelist()
    out = {}
    for r in rows:
        code = str(r.get("SECURITY_CODE") or "").zfill(6)
        if not code or code == "000000" or code not in wl:
            continue
        out[code] = {
            "np_yoy": round(r["SJLTZ"], 1) if r.get("SJLTZ") is not None else None,
            "rev_yoy": round(r["YSTZ"], 1) if r.get("YSTZ") is not None else None,
        }
    dcache.put(REPORT_NAME, {"_q": "q1_ashare", "date": q1_date}, out)
    return out


if __name__ == "__main__":
    m = fetch_q1()
    print("Q1 覆盖:", len(m), "只")
    for c in ("603986", "000039", "601137"):
        print(c, m.get(c))
