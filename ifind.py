"""
ifind.py — 同花顺 iFinD 接入层 (通过本地 bridge, 优雅降级)
=============================================================
iFinD 当前账号下稳定可用的是"实时行情"(THS_RealtimeQuotes): PB / 换手率 / 成交额 / 现价。
bridge 未启动或未登录时, 所有函数返回空 (不报错), 上层自动回落东财。

启动 bridge:
  python "C:/Users/ASUS/finance-mcp-server/python-bridge/ifind_bridge.py" --port 5001
"""
import os
import requests

BRIDGE = os.environ.get("IFIND_BRIDGE_URL", "http://127.0.0.1:5001")
_TIMEOUT = 8


def available():
    """bridge 是否在线且已登录 iFinD。"""
    try:
        r = requests.get(f"{BRIDGE}/health", timeout=4)
        d = r.json()
        return bool(d.get("logged_in") and d.get("ifind_available"))
    except Exception:
        return False


def quotes(codes, batch=40):
    """
    批量实时行情。返回 {code -> {pb, turnover, amount_yi, price, change_pct}}。
    bridge 不通 → 返回 {} (优雅降级)。
    """
    codes = [str(c).zfill(6) for c in codes if c]
    if not codes:
        return {}
    out = {}
    for i in range(0, len(codes), batch):
        chunk = codes[i:i + batch]
        try:
            r = requests.get(f"{BRIDGE}/realtime",
                             params={"codes": ",".join(chunk)}, timeout=_TIMEOUT)
            d = r.json()
            if not d.get("success"):
                continue
            for x in d.get("data", []):
                code = str(x.get("code") or "").zfill(6)
                if not code:
                    continue
                amt = x.get("amount")
                out[code] = {
                    "pb": round(x["pb"], 2) if x.get("pb") is not None else None,
                    "turnover": round(x["turnoverRate"], 2) if x.get("turnoverRate") is not None else None,
                    "amount_yi": round(amt / 1e8, 2) if amt else None,
                    "price": x.get("price"),
                    "change_pct": round(x["changePercent"], 2) if x.get("changePercent") is not None else None,
                }
        except Exception:
            continue
    return out


if __name__ == "__main__":
    print("iFinD bridge available:", available())
    q = quotes(["603986", "300750"])
    for c, v in q.items():
        print(c, v)
