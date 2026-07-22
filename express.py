"""
express.py — 业绩快报 (实际值)
==============================
数据源: 东财 datacenter RPT_FCI_PERFORMANCEE
业绩快报 = 公司正式财报前披露的经营业绩快报, 是"实际数"(比业绩预告更硬)。
用途: 与业绩预告(区间预测)做交叉验证 —— 快报落在预告区间内?超/不及预告?
"""
import em

REPORT_NAME = "RPT_FCI_PERFORMANCEE"


def fetch_all(report_date="2026-06-30"):
    """拉本期全部已披露业绩快报。"""
    cols = ("SECURITY_CODE,SECURITY_NAME_ABBR,TOTAL_OPERATE_INCOME,"
            "PARENT_NETPROFIT,YSTZ,JLRTBZCL,WEIGHTAVG_ROE,NOTICE_DATE")
    rows = em.paginate(REPORT_NAME, filter_str=f"(REPORT_DATE='{report_date}')",
                       columns=cols, page_size=500, sort_col="NOTICE_DATE",
                       sort_type=-1, max_pages=10)
    out = {}
    for r in rows:
        code = str(r.get("SECURITY_CODE") or "").zfill(6)
        if not code or code == "000000":
            continue
        np_yuan = r.get("PARENT_NETPROFIT")
        out[code] = {
            "name": r.get("SECURITY_NAME_ABBR") or "",
            "revenue_yi": round(r["TOTAL_OPERATE_INCOME"] / 1e8, 2) if r.get("TOTAL_OPERATE_INCOME") else None,
            "np_yi": round(np_yuan / 1e8, 2) if np_yuan else None,
            "revenue_yoy": round(r["YSTZ"], 1) if r.get("YSTZ") is not None else None,
            "np_yoy": round(r["JLRTBZCL"], 1) if r.get("JLRTBZCL") is not None else None,  # 实际净利同比%
            "roe": round(r["WEIGHTAVG_ROE"], 2) if r.get("WEIGHTAVG_ROE") is not None else None,
            "notice_date": (r.get("NOTICE_DATE") or "")[:10],
        }
    return out


def cross_check(forecast_row, ex):
    """
    预告 vs 快报交叉验证。
    ex: express 记录; forecast_row: normalize() 的一行 (有 chg_lo/chg_hi)。
    返回 {actual_yoy, verdict}: 落区间内/超预告上限/低于预告下限。
    """
    if not ex or ex.get("np_yoy") is None:
        return None
    a = ex["np_yoy"]
    lo, hi = forecast_row.get("chg_lo"), forecast_row.get("chg_hi")
    verdict = "已出快报"
    if lo is not None and hi is not None:
        if a > hi + 0.5:
            verdict = "超预告上限"
        elif a < lo - 0.5:
            verdict = "低于预告下限"
        else:
            verdict = "落预告区间内"
    return {"actual_yoy": a, "verdict": verdict}


if __name__ == "__main__":
    m = fetch_all()
    print("已披露业绩快报:", len(m), "只")
    for code, e in list(m.items())[:8]:
        print(f"  {code} {e['name']:<8} 营收{e['revenue_yi']}亿(YoY {e['revenue_yoy']}%) "
              f"净利{e['np_yi']}亿(YoY {e['np_yoy']}%) ROE {e['roe']}%")
