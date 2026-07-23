"""
check_coverage.py — 数据覆盖对照工具
====================================
跨多个 datacenter 报表交叉验证 "已披露 2026 中报业绩" 的股票数,
与 dashboard 的 kpi.disclosed 对照。差异过大 (>=100) 时报警,
帮用户发现数据滞后 / 口径差异 / datacenter 同步延迟等问题。

用法:
  python check_coverage.py                 # 当前期 (REPORT_DATE 默认 2026-06-30)
  python check_coverage.py --date 2026-09-30   # 三季报
  python check_coverage.py --threshold 100    # 自定义报警阈值
"""
import argparse
import datetime
import em
import forecast
import express


def _fetch_latest_financials(report_date):
    """
    拉最新实际财报 (半年报快报 RPT_FCI_PERFORMANCEE + 完整报表 RPT_LICO_FN_CPD)。
    """
    # 业绩快报 (快报表通常在正式财报前披露)
    ex_rows = em.paginate("RPT_FCI_PERFORMANCEE",
                          filter_str=f"(REPORT_DATE='{report_date}')",
                          page_size=500, max_pages=10)
    ex_codes = set(str(r.get("SECURITY_CODE") or "").zfill(6)
                   for r in ex_rows
                   if str(r.get("SECURITY_CODE") or "").zfill(6) != "000000")

    # 完整半年报 (RPT_LICO_FN_CPD 是财报库, REPORTDATE 即报告期)
    full_rows = em.paginate("RPT_LICO_FN_CPD",
                            filter_str=f"(REPORTDATE='{report_date}')",
                            page_size=500, max_pages=10)
    full_codes = set(str(r.get("SECURITY_CODE") or "").zfill(6)
                     for r in full_rows
                     if str(r.get("SECURITY_CODE") or "").zfill(6) != "000000")
    return ex_codes, full_codes


def run(report_date="2026-06-30", threshold=100):
    print(f"=== 数据覆盖对照 [{report_date}] ===\n")

    # 1. 业绩预告 — 当前 dashboard 的口径
    raw = forecast.fetch_universe(report_date)
    norm = forecast.normalize(raw)
    forecast_codes = {r["code"] for r in norm}
    forecast_unique_raw = len(set(str(r.get("SECURITY_CODE") or "").zfill(6)
                                  for r in raw
                                  if str(r.get("SECURITY_CODE") or "").zfill(6) != "000000"))

    # 2. 业绩快报
    ex_codes, full_codes = _fetch_latest_financials(report_date)

    # 3. dashboard 实际数 (从 API)
    try:
        import requests
        r = requests.get("http://127.0.0.1:3003/api/panorama",
                         params={"min_yoy": 50}, timeout=8)
        d_dashboard = r.json().get("kpi", {}).get("disclosed", "N/A")
    except Exception as e:
        d_dashboard = f"API 不可达 ({e})"

    print(f"{'报表源':<28} {'家数':>6}")
    print("-" * 38)
    print(f"{'① 业绩预告 datacenter 原始':<28} {forecast_unique_raw:>6}  (含归母+扣非+其它多行)")
    print(f"{'① 业绩预告 归母去重(dashboard)':<28} {len(forecast_codes):>6}  ← dashboard disclosed")
    print(f"{'② 业绩快报 (实际值)':<28} {len(ex_codes):>6}")
    print(f"{'③ 完整半年报 (正式)':<28} {len(full_codes):>6}")
    print()
    print(f"{'合 ① + ②(预告 OR 快报)':<28} {len(forecast_codes | ex_codes):>6}")
    print(f"{'合 ① + ② + ③(全披露)':<28} {len(forecast_codes | ex_codes | full_codes):>6}")
    print()
    print(f"{'Dashboard 实际 disclosed':<28} {d_dashboard:>6}")
    print()

    # 报警: 差超阈值
    dashboard_n = d_dashboard if isinstance(d_dashboard, int) else None
    if dashboard_n is None:
        print("⚠ 无法访问 dashboard API (服务未启动?) — 仅做独立计数")
        return

    # 与 ①+②+③ 全披露 对照 (含已出实际值的)
    full_disclosed = len(forecast_codes | ex_codes | full_codes)
    diff_full = full_disclosed - dashboard_n
    diff_pct = (diff_full / dashboard_n * 100) if dashboard_n else 0

    print(f"{'看板 vs 全披露(差)':<28} {diff_full:>+6}  ({diff_pct:+.1f}%)")
    if abs(diff_full) >= threshold:
        sign = "少" if diff_full > 0 else "多"
        print(f"\n⚠⚠ 报警: 看板 disclosed 与全披露源差 {abs(diff_full)} 只, "
              f"超过阈值 {threshold}。可能原因:")
        print("  (1) 第一财经/媒体口径含不同字段, 如 '业绩预告+快报+半年报' 累计")
        print("  (2) datacenter 数据有滞后, 最近披露的预告/半年报未即时入库")
        print("  (3) 跨期统计: 媒体把多个期混合(如 2025 年报+2026 中报)")
        print(f"\n建议:")
        print(f"  - 重新运行 dashboard (python server.py --refresh)")
        print(f"  - 如是 datacenter 滞后, 等待 1-2 天后再跑此脚本")
        print(f"  - 如是媒体口径不同, 这是预期差异, 改 dashboard 注释")
    else:
        print(f"\n✓ 差异 {abs(diff_full)} 在阈值 {threshold} 内, 看板与全披露源一致")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default="2026-06-30", help="REPORT_DATE")
    ap.add_argument("--threshold", type=int, default=100, help="报警阈值")
    args = ap.parse_args()
    run(args.date, args.threshold)