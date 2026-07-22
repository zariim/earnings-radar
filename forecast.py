"""
forecast.py — 全市场业绩预告拉取 + 归一化
=========================================
数据源: 东财 datacenter RPT_PUBLIC_OP_NEWPREDICT (全市场业绩预告)
- fetch_universe(): 拉全市场预告 (按归母净利润口径去重)
- normalize(): 增速中值 / 类型分类(预喜/预警/中性) / 板块 / ST
- rank_high_growth(): 按同比增速阈值筛高增
"""
import em

REPORT_NAME = "RPT_PUBLIC_OP_NEWPREDICT"

# PREDICT_TYPE → 情绪类别 (对标 arayaquant clsColor)
GOOD_TYPES = {"预增", "略增", "续盈", "扭亏"}
BAD_TYPES = {"预减", "略减", "首亏", "续亏", "增亏"}
# 其余(减亏/不确定/持平/预平) → neutral (减亏=亏损收窄, 方向改善但仍亏, 归中性)


def _cls(ptype):
    if ptype in GOOD_TYPES:
        return "good"
    if ptype in BAD_TYPES:
        return "bad"
    return "neutral"


def _mid(lo, hi):
    """增速区间取中值; 缺一取另一; 全缺 None。"""
    vals = [v for v in (lo, hi) if v is not None]
    if not vals:
        return None
    return round(sum(vals) / len(vals), 2)


def fetch_universe(report_date="2026-06-30"):
    """拉全市场预告原始行 (含归母/扣非等多行)。"""
    flt = f"(REPORT_DATE='{report_date}')"
    return em.paginate(REPORT_NAME, filter_str=flt, page_size=500,
                       sort_col="NOTICE_DATE", sort_type=-1)


def normalize(rows):
    """
    去重 (每股保留归母净利润 PREDICT_FINANCE_CODE='004', 取最新 NOTICE_DATE),
    产出标准化列表。
    """
    # 1) 只留归母净利润口径; 若某股无 004 行则退回其任意行
    by_code = {}
    for r in rows:
        code = str(r.get("SECURITY_CODE") or "").zfill(6)
        if not code or code == "000000":
            continue
        fin = r.get("PREDICT_FINANCE_CODE")
        prev = by_code.get(code)
        # 优先级: 归母(004) > 其它; 同级取 NOTICE_DATE 最新
        score = (1 if fin == "004" else 0, r.get("NOTICE_DATE") or "")
        if prev is None or score > prev[0]:
            by_code[code] = (score, r)

    out = []
    for code, (_, r) in by_code.items():
        name = r.get("SECURITY_NAME_ABBR") or ""
        ptype = r.get("PREDICT_TYPE") or ""
        lo, hi = r.get("ADD_AMP_LOWER"), r.get("ADD_AMP_UPPER")
        chg = _mid(lo, hi)
        board = em.board_of(code)
        # 净利润预告金额 (万元) — 取上下限均值
        amt = _mid(r.get("PREDICT_AMT_LOWER"), r.get("PREDICT_AMT_UPPER"))
        np_wan = round(amt / 10000.0, 1) if amt is not None else None
        # 扭亏/首亏类同比失真 → 标低基数
        low_base = ptype in ("扭亏", "首亏", "减亏") or (chg is not None and abs(chg) >= 1000)
        out.append({
            "code": code,
            "name": name,
            "board": board,
            "board_zh": em.BOARD_ZH.get(board, board),
            "type": ptype,
            "cls": _cls(ptype),
            "chg": chg,
            "chg_lo": lo,
            "chg_hi": hi,
            "np_wan": np_wan,
            "low_base": low_base,
            "reason": r.get("CHANGE_REASON_EXPLAIN") or "",
            "content": r.get("PREDICT_CONTENT") or "",
            "notice_date": (r.get("NOTICE_DATE") or "")[:10],
            "market": r.get("TRADE_MARKET") or "",
            "is_st": em.is_st(name),
        })
    return out


def rank_high_growth(rows, min_yoy=50.0, exclude_st=True, include_low_base=True):
    """cls=good 且 增速>=阈值, 按增速降序。"""
    res = []
    for r in rows:
        if exclude_st and r["is_st"]:
            continue
        if r["cls"] != "good":
            continue
        if r["chg"] is None:
            # 扭亏无同比: include_low_base 时保留(排最后), 否则丢
            if include_low_base:
                res.append(r)
            continue
        if r["chg"] >= min_yoy:
            res.append(r)
    res.sort(key=lambda x: (x["chg"] is not None, x["chg"] or 0), reverse=True)
    return res


if __name__ == "__main__":
    raw = fetch_universe()
    print("raw rows:", len(raw))
    norm = normalize(raw)
    print("unique stocks:", len(norm))
    from collections import Counter
    print("cls dist:", Counter(x["cls"] for x in norm))
    print("type dist:", Counter(x["type"] for x in norm))
    hi = rank_high_growth(norm, min_yoy=50.0)
    print(f"\nhigh-growth (>=50%, non-ST): {len(hi)}")
    for x in hi[:10]:
        print(f"  {x['code']} {x['name']:<8} {x['board_zh']} {x['type']} "
              f"chg={x['chg']}% {'⚠低基数' if x['low_base'] else ''}")
