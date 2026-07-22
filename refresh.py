"""
refresh.py — 每日重建快照 (供 Windows 计划任务 / cron 调用)
=============================================================
用法:
  python refresh.py                # 重建 min_yoy=50 快照, 落盘 data/
  python refresh.py --min-yoy 100  # 指定阈值

Windows 计划任务 (每交易日 18:00):
  程序: C:\\Users\\ASUS\\anaconda3\\python.exe
  参数: refresh.py
  起始于: C:\\Users\\ASUS\\earnings-radar
"""
import argparse
import datetime
import json
import os

import aggregate

HERE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(HERE, "data")


def run(min_yoy=50.0):
    t0 = datetime.datetime.now()
    print(f"[{t0:%Y-%m-%d %H:%M:%S}] 开始重建快照 min_yoy={min_yoy} ...")
    panorama, meta = aggregate.build(min_yoy=min_yoy)
    os.makedirs(DATA_DIR, exist_ok=True)
    stamp = (panorama.get("asof") or t0.strftime("%Y-%m-%d")).replace("-", "")
    path = os.path.join(DATA_DIR, f"panorama_{stamp}_yoy{int(min_yoy)}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"panorama": panorama, "meta": meta}, f, ensure_ascii=False)
    # latest.json 指针 (server 启动可秒加载)
    with open(os.path.join(DATA_DIR, f"latest_yoy{int(min_yoy)}.json"), "w", encoding="utf-8") as f:
        json.dump({"panorama": panorama, "meta": meta}, f, ensure_ascii=False)
    k = panorama["kpi"]
    dt = (datetime.datetime.now() - t0).total_seconds()
    print(f"完成 ({dt:.0f}s) → {path}")
    print(f"  已披露 {k['disclosed']} · 高增 {k['high_growth']} · "
          f"主板红旗 {k['main_flag']}(Q1证伪 {k['main_soft']}) · 已出快报 {k['express_n']}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-yoy", type=float, default=50.0)
    args = ap.parse_args()
    run(args.min_yoy)
