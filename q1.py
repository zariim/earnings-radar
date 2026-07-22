"""
q1.py — 一季报实际增速 (用于红旗榜证伪与恶化结构分析)
======================================================
数据源: 东财 datacenter RPT_LICO_FN_CPD (业绩报表 = 实际财报值)
返回每股 Q1 的 净利同比(SJLTZ) + 营收同比(YSTZ), 用于:
- 红旗证伪: 全年预期高但 Q1 净利已疲软
- 恶化结构: 营收也降(需求走弱) vs 只净利降(毛利/费用承压)
"""
import em

REPORT_NAME = "RPT_LICO_FN_CPD"


def fetch_q1(q1_date="2026-03-31"):
    """返回 {code -> {'np_yoy':净利同比%, 'rev_yoy':营收同比%}}, 覆盖全市场。"""
    rows = em.paginate(REPORT_NAME, filter_str=f"(REPORTDATE='{q1_date}')",
                       columns="SECURITY_CODE,SJLTZ,YSTZ", page_size=500,
                       sort_col="SECURITY_CODE", sort_type=1, max_pages=15)
    out = {}
    for r in rows:
        code = str(r.get("SECURITY_CODE") or "").zfill(6)
        if not code or code == "000000":
            continue
        out[code] = {
            "np_yoy": round(r["SJLTZ"], 1) if r.get("SJLTZ") is not None else None,
            "rev_yoy": round(r["YSTZ"], 1) if r.get("YSTZ") is not None else None,
        }
    return out


if __name__ == "__main__":
    m = fetch_q1()
    print("Q1 覆盖:", len(m), "只")
    for c in ("603986", "000039", "601137"):
        print(c, m.get(c))
