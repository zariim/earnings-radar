"""
tencent.py — 腾讯财经 qt.gtimg.cn 报价接口
============================================
免费公开接口, 无需 token / 无需登录。补 PB / 换手 / PE 行情字段
(替代已禁用的 iFinD 实时行情层)。

URL: https://qt.gtimg.cn/q={sh,sz}code1,{sh,sz}code2,...
返回: GBK 编码, 每只股一行 'v_sh603986="~...";', 字段用 ~ 分隔。
关键字段索引(经实测确认):
  3  现价 | 4 昨收 | 32 涨跌幅% | 38 换手率% | 39 市盈率(动态PE) | 46 市净率(PB)
"""
import re
import time
import requests

URL = "https://qt.gtimg.cn/q="
REFERER = "https://finance.qq.com/"
_TIMEOUT = 8

# 节流: 避免对免费源频繁拉
_TTL = 1800  # 30 分钟缓存, 与 panorama 缓存一致
_CACHE = {}  # code -> (ts, payload)


def _to_qs(code):
    """6 位代码 → sh/sz 前缀。"""
    code = str(code).zfill(6)
    if code.startswith(("60", "68", "90", "11", "13")):
        return "sh" + code
    return "sz" + code


def _parse(text):
    """解析腾讯响应 → {code -> {price, change_pct, turnover, pe, pb}}。"""
    out = {}
    for m in re.finditer(r'v_([a-z]{2}\d{6})="([^"]+)"', text):
        code6 = m.group(1)[2:]
        fields = m.group(2).split("~")
        if len(fields) < 47:
            continue

        def _f(idx):
            try:
                v = fields[idx].strip()
                return float(v) if v else None
            except Exception:
                return None

        out[code6] = {
            "price": _f(3),
            "change_pct": _f(32),
            "turnover": _f(38),
            "pe": _f(39),
            "pb": _f(46),
        }
    return out


def quotes(codes):
    """批量实时行情。返回 {code -> {price, change_pct, turnover, pe, pb}}。
    自动命中磁盘缓存(30 分钟), 减少外部请求。"""
    codes = [str(c).zfill(6) for c in codes if c]
    if not codes:
        return {}
    now = time.time()
    out, missing = {}, []
    for c in codes:
        hit = _CACHE.get(c)
        if hit and now - hit[0] < _TTL:
            out[c] = hit[1]
        else:
            missing.append(c)
    if not missing:
        return out
    qs = ",".join(_to_qs(c) for c in missing)
    try:
        r = requests.get(URL, params={"q": qs},
                         headers={"Referer": REFERER,
                                  "User-Agent": "Mozilla/5.0"}, timeout=_TIMEOUT)
        # 腾讯响应是 GBK, requests 默认按 ISO-8859-1 解析, 需按 apparent_encoding
        r.encoding = r.apparent_encoding or "gbk"
        parsed = _parse(r.text)
        for code, payload in parsed.items():
            out[code] = payload
            _CACHE[code] = (now, payload)
    except Exception:
        pass
    return out


if __name__ == "__main__":
    q = quotes(["603986", "300750", "688008", "301308"])
    for c in sorted(q):
        v = q[c]
        print(f"{c} 价格={v.get('price')} 涨跌幅={v.get('change_pct')}% "
              f"PE={v.get('pe')} PB={v.get('pb')} 换手={v.get('turnover')}%")