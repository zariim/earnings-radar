"""
aggregate.py — 组装业绩预告筛选五视图 JSON
==============================================
build_panorama(min_yoy) 返回:
  ① kpi + style_dist        (预告全景)
  ② good_list / bad_list    (好坏榜 = 高增+阈值筛选)   ← 核心
  ③ industry[]              (行业分布 + 行业标注)
  ④ vs_expect[] + stat      (预告 vs 券商一致预期)
  ⑤ redflag{main,gem}       (红旗榜 = 反向筛选)
build_meta() 返回 asof/period/kpi/rule。
"""
import statistics
import forecast
import consensus
import express
import announce
import q1
import tencent
import news
import thsep
import research

REPORT_DATE = "2026-06-30"
Q1_DATE = "2026-03-31"
PERIOD = "2026 中报 (H1 2026)"
GAP_BEAT = 15.0   # 超预期阈值 (pct)
GAP_MISS = -15.0  # 不及预期阈值
REDFLAG_EXPECT = 50.0  # 红旗: 一致预期隐含增速门槛
Q1_SOFT = 20.0    # 红旗证伪: Q1 实际增速低于此值 → 与"全年≥50%预期"背离, 最可疑
LOWBASE_EPS = 0.10   # 上年 EPS 绝对值 ≤ 此值 → 低基数(隐含增速虚高)
LOWBASE_IMPLIED = 500.0  # 隐含增速 ≥ 此值 → 极可能低基数噪声
IND_HOT_RATE = 0.6   # 行业预喜率 ≥ 此值 → 视为景气行业(逆行业沉默更异常)


def _enrich_industry(norm, imap, cmap):
    """给每只预告股打行业标签: 优先全市场行业图(东财行业), 缺则一致预期表行业。"""
    for r in norm:
        code = r["code"]
        ind = ""
        if code in imap and imap[code].get("industry"):
            ind = imap[code]["industry"]
        elif code in cmap and cmap[code].get("industry"):
            ind = cmap[code]["industry"]
        r["industry"] = ind or "未分类"
    return norm


def _slim(r):
    """好坏榜/红旗榜行的精简字段。"""
    return {
        "code": r["code"], "name": r["name"], "board": r["board"],
        "board_zh": r["board_zh"], "industry": r.get("industry", ""),
        "type": r["type"], "cls": r["cls"], "chg": r["chg"],
        "chg_lo": r["chg_lo"], "chg_hi": r["chg_hi"], "np_wan": r["np_wan"],
        "low_base": r["low_base"], "notice_date": r["notice_date"],
        "reason": (r["reason"] or "")[:200],
    }


def _build_pe_pctile(imap):
    """按行业分组, 为每只票算 PE 在同业中的分位 (0-100, 越高越贵)。"""
    ind_pes = {}
    for code, v in imap.items():
        pe = v.get("pe_ttm")
        ind = v.get("industry")
        if pe is not None and pe > 0 and ind:
            ind_pes.setdefault(ind, []).append(pe)
    for k in ind_pes:
        ind_pes[k].sort()
    def pctile(code):
        v = imap.get(code) or {}
        pe, ind = v.get("pe_ttm"), v.get("industry")
        if pe is None or pe <= 0 or ind not in ind_pes:
            return None
        arr = ind_pes[ind]
        if len(arr) < 3:
            return None
        below = sum(1 for x in arr if x <= pe)
        return round(below / len(arr) * 100)
    return pctile


# 关注度排序权重
_CONCERN_RANK = {"high": 3, "mid": 2, "low": 1}


def _triage_counts(flags):
    """统计各分诊标签家数 (按关注度降序)。"""
    from collections import Counter
    c = Counter(x["triage"] for x in flags)
    order = {"真警报·Q1已证伪": 0, "关注·逆行业沉默": 1, "待观察·高预期未表态": 2,
             "低基数·预期虚高": 3, "已出快报·非沉默": 4}
    return [{"label": k, "n": v} for k, v in sorted(c.items(), key=lambda kv: order.get(kv[0], 9))]


def _triage_redflag(implied, eps_a, q1np, q1rev, ind_rate, has_express):
    """
    给一只红旗做分诊, 返回 (triage标签, concern关注度, q1诊断)。
    优先级: 已出快报 > Q1已证伪 > 低基数噪声 > 逆行业沉默 > 待观察。
    """
    # Q1 恶化结构诊断
    q1_diag = None
    if q1np is not None and q1np < Q1_SOFT:
        if q1rev is not None and q1rev < 0:
            q1_diag = "需求走弱(营收↓)"
        else:
            q1_diag = "毛利/费用承压(营收未降)"

    base_weak = (eps_a is not None and 0 < abs(eps_a) <= LOWBASE_EPS) or \
                (implied is not None and implied >= LOWBASE_IMPLIED)

    if has_express:
        return "已出快报·非沉默", "low", q1_diag
    if q1np is not None and q1np < Q1_SOFT:
        return "真警报·Q1已证伪", "high", q1_diag
    if base_weak:
        return "低基数·预期虚高", "low", q1_diag
    if ind_rate is not None and ind_rate >= IND_HOT_RATE:
        return "关注·逆行业沉默", "mid", q1_diag
    return "待观察·高预期未表态", "mid", q1_diag


def build(min_yoy=50.0, report_date=REPORT_DATE, with_announce=True):
    """一次拉全量, 返回 (panorama, meta) 两个 dict。"""
    raw = forecast.fetch_universe(report_date)
    norm = forecast.normalize(raw)
    cmap = consensus.build()
    imap = consensus.build_industry_map()
    exmap = express.fetch_all(report_date)   # 业绩快报(实际值)
    q1map = q1.fetch_q1(Q1_DATE)             # 一季报实际增速(净利+营收, 红旗证伪)
    pe_pctile = _build_pe_pctile(imap)       # 同业 PE 分位
    _enrich_industry(norm, imap, cmap)

    disclosed_codes = {r["code"] for r in norm}
    good = [r for r in norm if r["cls"] == "good"]
    bad = [r for r in norm if r["cls"] == "bad"]
    neutral = [r for r in norm if r["cls"] == "neutral"]
    asof = max((r["notice_date"] for r in norm if r["notice_date"]), default="")

    # ---- ② 好坏榜 (高增+阈值) ----
    good_ranked = forecast.rank_high_growth(norm, min_yoy=min_yoy, include_low_base=True)
    n_high_growth = len(good_ranked)  # 真实命中数 (不受 top100 截断)
    good_list = [_slim(r) for r in good_ranked[:100]]
    # 快报交叉验证 (预告 vs 实际快报)
    fmap = {r["code"]: r for r in good_ranked[:100]}
    for row in good_list:
        code = row["code"]
        ck = express.cross_check(fmap[code], exmap.get(code))
        row["express"] = ck  # {actual_yoy, verdict} 或 None
        # 估值: PE(TTM) / 市值(亿) / 同业PE分位
        v = imap.get(code) or {}
        row["pe_ttm"] = round(v["pe_ttm"], 1) if v.get("pe_ttm") else None
        row["mktcap_yi"] = round(v["mktcap"] / 1e8, 1) if v.get("mktcap") else None
        row["pe_pctile"] = pe_pctile(code)
    # 公告链接 (仅榜单前100, 带磁盘缓存)
    if with_announce:
        anns = announce.enrich([r["code"] for r in good_list])
        for row in good_list:
            row["ann"] = anns.get(row["code"])
    # 同花顺一致预期 EPS — 只对已有一致预期覆盖的股(避免无谓请求), 并行拉
    import concurrent.futures as _cf
    ths_targets = [r["code"] for r in good_list
                   if cmap.get(r["code"], {}).get("implied_yoy") is not None]
    thsep_data = {}
    if ths_targets:
        with _cf.ThreadPoolExecutor(max_workers=8) as ex:
            for code, rows in zip(ths_targets,
                                  ex.map(thsep.fetch, ths_targets)):
                thsep_data[code] = rows
    for row in good_list:
        ths_eps_list = thsep_data.get(row["code"]) or []
        if ths_eps_list:
            cur_year = max(ths_eps_list, key=lambda x: x["year"])
            row["ths_eps"] = {"year": cur_year["year"], "n": cur_year["n"],
                              "mean": cur_year["mean"], "min": cur_year["min"],
                              "max": cur_year["max"]}
        else:
            row["ths_eps"] = None
    # 东财最新研报 (top 3, 并行)
    rep_targets = [r["code"] for r in good_list[:60]]  # 只前 60 只拉研报
    rep_data = {}
    if rep_targets:
        with _cf.ThreadPoolExecutor(max_workers=6) as ex:
            for code, reps in zip(rep_targets,
                                  ex.map(lambda c: (c, research.fetch(c, page_size=3, max_pages=1)), rep_targets)):
                rep_data[code] = reps[1][:3] if isinstance(reps, tuple) else []
    for row in good_list:
        row["reports"] = rep_data.get(row["code"], [])
    # 腾讯财经实时行情补 PB / 换手 (免费, 不限频)
    tq = tencent.quotes([r["code"] for r in good_list])
    for row in good_list:
        v = tq.get(row["code"])
        if v:
            row["pb"] = v["pb"]
            row["turnover"] = v["turnover"]
            row["price"] = v["price"]
            row["change_pct"] = v["change_pct"]
    bad_sorted = sorted([r for r in bad if r["chg"] is not None],
                        key=lambda x: x["chg"])
    bad_list = [_slim(r) for r in bad_sorted[:100]]

    # ---- ③ 行业分布 ----
    ind_map = {}
    for r in norm:
        ind = r.get("industry") or "未分类"
        d = ind_map.setdefault(ind, {"ind": ind, "good": 0, "bad": 0,
                                     "neutral": 0, "chgs": []})
        d[r["cls"]] += 1
        if r["cls"] == "good" and r["chg"] is not None and not r["low_base"]:
            d["chgs"].append(r["chg"])
    industry = []
    for d in ind_map.values():
        total = d["good"] + d["bad"] + d["neutral"]
        industry.append({
            "ind": d["ind"], "good": d["good"], "bad": d["bad"],
            "neutral": d["neutral"], "total": total,
            "good_rate": round(d["good"] / total, 3) if total else 0,
            "median_chg": round(statistics.median(d["chgs"]), 1) if d["chgs"] else None,
        })
    industry.sort(key=lambda x: x["total"], reverse=True)

    # ---- ④ vs 一致预期 ----
    vs_expect, beat, inline, miss = [], 0, 0, 0
    for r in norm:
        if r["cls"] != "good" or r["chg"] is None or r["low_base"]:
            continue
        c = cmap.get(r["code"])
        if not c or c["implied_yoy"] is None:
            continue
        gap = round(r["chg"] - c["implied_yoy"], 1)
        if gap >= GAP_BEAT:
            verdict = "超预期"; beat += 1
        elif gap <= GAP_MISS:
            verdict = "不及预期"; miss += 1
        else:
            verdict = "符合"; inline += 1
        vs_expect.append({
            "code": r["code"], "name": r["name"], "industry": r["industry"],
            "board": r["board"], "board_zh": r["board_zh"],
            "chg": r["chg"], "expect": c["implied_yoy"], "gap": gap,
            "verdict": verdict,
        })
    vs_expect.sort(key=lambda x: x["gap"])  # 不及预期在前

    # ---- ⑤ 红旗榜 (反向筛选 + 分诊) ----
    # 一致预期隐含增速>=50% 但未在预告名单出现
    ind_rate = {x["ind"]: x["good_rate"] for x in industry}  # 行业预喜率(逆行业对照)
    flag_main, flag_gem = [], []
    for code, c in cmap.items():
        if c["implied_yoy"] is None or c["implied_yoy"] < REDFLAG_EXPECT:
            continue
        if code in disclosed_codes:
            continue  # 已披露预告, 不算红旗
        if consensus.em.is_st(c["name"]):
            continue
        board = consensus.em.board_of(code)
        # EPS 豁免: 上年 EPS 绝对值 <=0.03 (小基数) 不算强信号
        exempt = c["eps_a"] is not None and abs(c["eps_a"]) <= 0.03
        ind = (imap.get(code, {}).get("industry") or c["industry"] or "未分类")
        q1d = q1map.get(code) or {}
        q1np, q1rev = q1d.get("np_yoy"), q1d.get("rev_yoy")
        soft_q1 = q1np is not None and q1np < Q1_SOFT
        has_express = code in exmap  # 已出业绩快报 → 非沉默
        triage, concern, q1_diag = _triage_redflag(
            c["implied_yoy"], c["eps_a"], q1np, q1rev, ind_rate.get(ind), has_express)
        item = {
            "code": code, "name": c["name"], "industry": ind,
            "board": board, "board_zh": consensus.em.BOARD_ZH.get(board, board),
            "expect": c["implied_yoy"], "broker_n": c["broker_n"], "exempt": exempt,
            "q1_yoy": q1np, "q1_rev_yoy": q1rev, "soft_q1": soft_q1,
            "ind_good_rate": ind_rate.get(ind),
            "has_express": has_express,
            "triage": triage, "concern": concern, "q1_diag": q1_diag,
        }
        if board == "main" and not exempt:
            flag_main.append(item)
        elif board in ("gem", "star"):
            flag_gem.append(item)
    # 排序: 关注度(高→低) → 预期降序
    _sk = lambda x: (_CONCERN_RANK.get(x["concern"], 0), x["expect"])
    flag_main.sort(key=_sk, reverse=True)
    flag_gem.sort(key=_sk, reverse=True)
    main_soft = sum(1 for x in flag_main if x["soft_q1"])
    gem_soft = sum(1 for x in flag_gem if x["soft_q1"])
    main_high = sum(1 for x in flag_main if x["concern"] == "high")
    # 红旗行业分布
    rf_ind = {}
    for it in flag_main + flag_gem:
        d = rf_ind.setdefault(it["industry"], {"ind": it["industry"], "main": 0, "gem": 0})
        if it["board"] == "main":
            d["main"] += 1
        else:
            d["gem"] += 1
    rf_ind_list = sorted(rf_ind.values(), key=lambda x: x["main"] + x["gem"], reverse=True)

    # ---- ① KPI ----
    disclosed = len(norm)
    n_good, n_bad, n_neutral = len(good), len(bad), len(neutral)
    kpi = {
        "disclosed": disclosed, "good": n_good, "bad": n_bad, "neutral": n_neutral,
        "good_rate": round(n_good / disclosed, 3) if disclosed else 0,
        "high_growth": n_high_growth,
        "main_flag": len(flag_main), "gem_flag": len(flag_gem),
        "main_soft": main_soft, "gem_soft": gem_soft, "main_high": main_high,
        "universe": len(cmap),
        "express_n": len(exmap),
    }
    from collections import Counter
    tcnt = Counter(r["type"] for r in norm)
    style_dist = [{"style": t, "n": n,
                   "cls": forecast._cls(t)} for t, n in tcnt.most_common()]

    panorama = {
        "asof": asof, "period": PERIOD, "min_yoy": min_yoy,
        "telegraph": news.cls_telegraph(15),  # 财联社电报 15 条 (5 分钟缓存)
        "kpi": kpi, "style_dist": style_dist,
        "good_list": good_list, "bad_list": bad_list,
        "industry": industry,
        "vs_expect": vs_expect,
        "vs_expect_stat": {"beat": beat, "inline": inline, "miss": miss},
        "redflag": {
            "main": flag_main, "gem": flag_gem, "industry": rf_ind_list,
            "board_split": {"main": len(flag_main), "gem": len(flag_gem),
                            "main_soft": main_soft, "gem_soft": gem_soft,
                            "main_high": main_high},
            "triage_dist": _triage_counts(flag_main),
        },
    }
    meta = build_meta(kpi, asof, min_yoy)
    return panorama, meta


def build_meta(kpi, asof, min_yoy):
    return {
        "asof": asof, "period": PERIOD, "min_yoy": min_yoy, "kpi": kpi,
        "rule": {
            "title": "全市场业绩预告高增筛选 · 口径与规则",
            "bullets": [
                f"高增定义: 预告类型为预喜(预增/略增/续盈/扭亏)且归母净利润同比增速≥{min_yoy:.0f}%; "
                "增速取预告上下限中值。",
                "沪深主板强制披露: 预计半年度①净利为负 ②扭亏为盈 ③盈利且净利同比≥±50% "
                "须在报告期结束后15日内(约7/15前)披露预告。上年EPS绝对值≤0.03的小基数公司豁免'±50%'条。",
                "创业板/科创板/北交所: 中报预告自愿披露, 无强制。",
                "红旗榜(反向): 主板股一致预期隐含全年增速≥50% 却未披露预告 → 若真能兑现依法应披露, "
                "未披露 → 大概率不及乐观预期。已剔除EPS≤0.03豁免。创科板同筛为弱信号(自愿披露)。"
                f"其中 Q1实际净利同比<{Q1_SOFT:.0f}% 的标'Q1已证伪'(预期高但一季度已疲软/转亏, 最可疑, 排最前)。",
                "扭亏/首亏/减亏型同比失真(低基数), 标⚠不参与超预期对比。",
                "业绩快报(实际值)已接入: 高增榜显示快报净利同比并与预告区间交叉验证(超预告上限/落区间内/低于下限); "
                "季初出快报的公司少, 随披露推进逐步填充。公告栏提供上交所/深交所官方 PDF 与详情页链接。",
                "数据源: 东方财富 datacenter (RPT_PUBLIC_OP_NEWPREDICT 预告 / RPT_WEB_RESPREDICT 一致预期与行业 / "
                "RPT_FCI_PERFORMANCEE 业绩快报 / RPT_VALUEANALYSIS_DET 全市场行业 / np-anotice-stock 公告)。",
            ],
        },
    }


if __name__ == "__main__":
    import json
    p, m = build(min_yoy=50.0)
    print("asof:", p["asof"])
    print("kpi:", json.dumps(p["kpi"], ensure_ascii=False))
    print("vs_expect_stat:", p["vs_expect_stat"])
    print("top industries:", [(x["ind"], x["total"], x["good_rate"]) for x in p["industry"][:5]])
    print("good_list[0]:", json.dumps(p["good_list"][0], ensure_ascii=False))
    print("redflag main top3:", [(x["name"], x["industry"], x["expect"]) for x in p["redflag"]["main"][:3]])
