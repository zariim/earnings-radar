"""
consensus.py — 券商一致预期 + 隐含增速 + 行业标签
=================================================
数据源: 东财 datacenter RPT_WEB_RESPREDICT (全市场一致预期)
- fetch_all(): 整表拉全市场一致预期
- build(): {code -> {eps_a, eps_e, implied_yoy, industry, broker_n, buy, add, target}}
隐含全年增速 = EPS(最近E年) / EPS(最近A年) - 1
行业标签直接取 INDUSTRY_BOARD (东财自有行业分类)。
"""
import em
import dcache

REPORT_NAME = "RPT_WEB_RESPREDICT"
_TTL = 6 * 3600


def fetch_all():
    """整表拉全市场一致预期 (磁盘缓存 6h)。"""
    cached = dcache.get(REPORT_NAME, {"_q": "fetch_all"})
    if cached is not None:
        return cached
    cols = ("SECURITY_CODE,SECURITY_NAME_ABBR,RATING_ORG_NUM,RATING_BUY_NUM,"
            "RATING_ADD_NUM,EPS1,EPS2,EPS3,YEAR1,YEAR2,YEAR_MARK1,YEAR_MARK2,"
            "INDUSTRY_BOARD")
    rows = em.paginate(REPORT_NAME, columns=cols, page_size=500,
                       sort_col="SECURITY_CODE", sort_type=1)
    dcache.put(REPORT_NAME, {"_q": "fetch_all"}, rows)
    return rows


def _implied_yoy(eps_a, eps_e):
    """由 EPS(A) → EPS(E) 推隐含增速%; 仅在 eps_a>0 时可靠。"""
    if eps_a is None or eps_e is None:
        return None
    if eps_a <= 0:
        return None
    return round((eps_e / eps_a - 1) * 100, 2)


def build():
    """返回 dict: code -> 一致预期信息 (磁盘缓存 6h)。"""
    cached = dcache.get(REPORT_NAME, {"_q": "build_dict"})
    if cached is not None:
        return cached
    rows = fetch_all()
    out = {}
    for r in rows:
        code = str(r.get("SECURITY_CODE") or "").zfill(6)
        if not code or code == "000000":
            continue
        eps_a, eps_e = r.get("EPS1"), r.get("EPS2")
        out[code] = {
            "name": r.get("SECURITY_NAME_ABBR") or "",
            "industry": r.get("INDUSTRY_BOARD") or "",
            "eps_a": eps_a,
            "eps_e": eps_e,
            "year_a": r.get("YEAR1"),
            "year_e": r.get("YEAR2"),
            "implied_yoy": _implied_yoy(eps_a, eps_e),
            "broker_n": r.get("RATING_ORG_NUM"),
            "buy": r.get("RATING_BUY_NUM"),
            "add": r.get("RATING_ADD_NUM"),
        }
    dcache.put(REPORT_NAME, {"_q": "build_dict"}, out)
    return out


# ---- 全市场行业映射 (东财估值分析报表, 覆盖全部 ~5500 只) ----
# RPT_VALUEANALYSIS_DET 每交易日全市场一行, 含 BOARD_NAME(东财行业) + PE + 市值。
import datetime

_VAL_REPORT = "RPT_VALUEANALYSIS_DET"


def _latest_trade_date():
    """自动探测最近有数据的交易日 (今天往前回溯)。"""
    today = datetime.date.today()
    for back in range(0, 8):
        d = (today - datetime.timedelta(days=back)).strftime("%Y-%m-%d")
        url = (em.DC_URL + f"?reportName={_VAL_REPORT}&columns=SECURITY_CODE"
               f"&pageSize=1&pageNumber=1&filter=(TRADE_DATE='{d}')")
        r = em.fetch(url)
        if (r.get("result") or {}).get("data"):
            return d
    return None


def build_industry_map(trade_date=None):
    """
    返回 {code -> {'industry','pe_ttm','mktcap'}}, 覆盖全市场。
    industry = 东财行业 BOARD_NAME。磁盘缓存 6h。
    """
    if trade_date is None:
        trade_date = _latest_trade_date()
    if not trade_date:
        return {}
    cached = dcache.get(_VAL_REPORT, {"_q": "industry", "date": trade_date})
    if cached is not None:
        return cached
    cols = "SECURITY_CODE,SECURITY_NAME_ABBR,BOARD_NAME,PE_TTM,TOTAL_MARKET_CAP"
    rows = em.paginate(_VAL_REPORT, filter_str=f"(TRADE_DATE='{trade_date}')",
                       columns=cols, page_size=500, sort_col="SECURITY_CODE",
                       sort_type=1, max_pages=20)
    out = {}
    for r in rows:
        code = str(r.get("SECURITY_CODE") or "").zfill(6)
        if not code or code == "000000":
            continue
        out[code] = {
            "industry": r.get("BOARD_NAME") or "",
            "pe_ttm": r.get("PE_TTM"),
            "mktcap": r.get("TOTAL_MARKET_CAP"),
        }
    dcache.put(_VAL_REPORT, {"_q": "industry", "date": trade_date}, out)
    return out


if __name__ == "__main__":
    import sys
    m = build()
    print("consensus coverage:", len(m))
    imap = build_industry_map()
    print("industry map coverage:", len(imap))
    code = sys.argv[1] if len(sys.argv) > 1 else "603986"
    code = code.zfill(6)
    if code in m:
        c = m[code]
        print(f"{code} {c['name']} 行业={c['industry']} "
              f"EPS{c['year_a']}A={c['eps_a']:.2f} EPS{c['year_e']}E={c['eps_e']:.2f} "
              f"隐含增速={c['implied_yoy']}% 券商{c['broker_n']}家(买入{c['buy']})")
    else:
        print(code, "无一致预期覆盖")
    print("industry_map[603986]:", imap.get("603986"))
