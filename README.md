# 业绩预告高增雷达 (Earnings Radar)

> 独立实现的**全市场业绩预告高增筛选工具**: 从全市场业绩预告里筛高增股、与券商一致预期对比、按行业标注,并对"应披露而未披露"的主板股反向打红旗。
> 全部走东方财富 datacenter 直连 HTTP,无需付费数据源;可选接入 iFinD 增强行情维度(实时报价、PB、换手率)。

---

## ✨ 核心功能

| 模块 | 内容 |
|---|---|
| ① 预告全景 | 全市场已披露预告的类型分布(预喜/预警/中性)+ 甜甜圈 + 行业聚集 |
| ② **高增榜** | 预喜公司按归母净利同比增速排序,阈值可调(默认≥50%),每行带 PE/市值/同业分位/快报实际/公告 PDF |
| ③ 行业分布 | 行业维度高增聚集:每行业预喜率 + 净利变动中位 |
| ④ vs 券商一致预期 | 预告 H1 增速 vs 分析师全年一致预期,算缺口,标超预期(+15pct)/不及预期(-15pct) |
| ⑤ **红旗榜(反向筛选)** | 主板股一致预期隐含全年≥50% 却未披露预告 → 反向信号;接 Q1 实际增速做证伪(分诊五类结论) |

亮点:
- **全市场一次拉全** — `em.paginate()` 自动循环翻页(每页 500 行),A 股 5530 只约 12 次请求完成
- **行业覆盖率 100%** — 通过 `RPT_VALUEANALYSIS_DET` 一次性拿全市场 5529 只的行业标签
- **公告可读** — 看板内弹窗直接看公告全文,附下载 PDF/详情页
- **红旗分诊** — 把"一致预期高却沉默"按"Q1已证伪/低基数噪声/逆行业沉默/待观察/已出快报"五类自动标签

## 🏗 架构

```
em.py            # 东方财富 datacenter GET 封装 (限流桶+JSONP剥离+分页+板块分类)
forecast.py      # 全市场业绩预告拉取 + normalize(增速中值/类型分类/低基数标记)
consensus.py     # 券商一致预期 + EPS 隐含增速 + 全市场行业图(5529只)
express.py       # 业绩快报(实际值) + 预告 vs 快报交叉验证
q1.py            # 一季报实际增速(红旗榜 Q1 证伪 + 恶化结构诊断)
announce.py      # 公司公告链接(上交所/深交所 PDF + 详情页),12h 磁盘缓存
ifind.py         # 同花顺 iFinD 接入层(经本地 bridge,优雅降级,不通回落)
aggregate.py     # 组装五视图 JSON(build 入口)
server.py        # Flask 端口 3003,内存缓存 30min + 磁盘快照秒加载 + --daily-refresh 自刷
refresh.py       # 每日快照脚本(供 Windows 计划任务)
dashboard.html   # 单页看板(Chart.js CDN, 五段锚点)
```

## 📊 数据源(全东财 datacenter,curl 实测稳定)

| 用途 | reportName / 端点 | 说明 |
|---|---|---|
| 全市场业绩预告(增速区间/类型/公告原文/板块) | `RPT_PUBLIC_OP_NEWPREDICT` | 934 页全量,filter `REPORT_DATE='YYYY-MM-DD'` |
| 券商一致预期(EPS/评级/目标价/行业) | `RPT_WEB_RESPREDICT` | 整表分页; 隐含增速=EPS2(E)/EPS1(A)-1 |
| 全市场行业+PE+市值(5529只) | `RPT_VALUEANALYSIS_DET` | filter `TRADE_DATE='最近交易日'` 自动探测 |
| 业绩快报(实际值) | `RPT_FCI_PERFORMANCEE` | YSTZ=营收同比 JLRTBZCL=净利同比 |
| 一季报实际(Q1证伪) | `RPT_LICO_FN_CPD` | `(REPORTDATE='YYYY-MM-DD')` SJLTZ=净利同比 |
| 公告链接 | `np-anotice-stock.eastmoney.com/api/security/ann` | 按标题关键词过滤业绩类 |
| (可选)iFinD 实时行情 | 本地 bridge 5001 端口 `/realtime` | 优雅降级,bridge 不通自动跳过 |

## 🚀 快速开始

### 环境要求
- Python 3.10+ (anaconda 已测)
- Flask 2.0+
- requests 2.25+

### 安装与启动

```bash
# 1. 克隆
git clone https://github.com/你的用户名/earnings-radar.git
cd earnings-radar

# 2. 安装依赖
pip install -r requirements.txt

# 3. 启动 (单服务,东财全量)
python server.py --port 3003 --warm
# 浏览器打开 http://127.0.0.1:3003/
```

### 可选:启用 iFinD 行情层(补 PB / 换手 / 成交额)

```bash
# 终端1: iFinD bridge
python "C:/Users/ASUS/finance-mcp-server/python-bridge/ifind_bridge.py" --port 5001
# 终端2: 高增雷达 (会自动检测 iFinD 是否在线)
python server.py --port 3003 --daily-refresh 18
```

- bridge 在线时:高增榜多出 **PB / 换手率 / 成交额**(iFinD 独有,东财估值表没有)
- bridge 离线时:自动跳过,看板照常工作,顶部显示 "iFinD 离线·回落东财"

### 每日自动刷新

`--daily-refresh 18` 让服务每天 18:00 自动重建快照;启动时还会从当天磁盘快照秒加载,首访不再等待。

也可独立用 `refresh.py` 挂 Windows 计划任务:
```bash
python refresh.py --min-yoy 50
```

## 🔄 换报告期复用

财报期变化时(中报→三季报→年报),只需改 `aggregate.py` 顶部两个常量:
```python
REPORT_DATE = "2026-09-30"  # 三季报
Q1_DATE = "2026-06-30"      # 上期中报(用于红旗榜 Q1 证伪)
```
其余代码、看板、数据源映射全部不动。

## 📈 红旗榜分诊逻辑

每只主板红旗自动打 5 类标签(从高到低排序):
1. 🔴 **真警报·Q1已证伪** — 一致预期≥50% 但 Q1 实际净利同比<20%,最该排查
2. 🟡 **关注·逆行业沉默** — 所在行业普遍预喜(≥60%)、它却沉默
3. 🟡 **待观察·高预期未表态** — 预期高、未披露、Q1 尚可
4. ⚪ **低基数·预期虚高** — 上年 EPS 极小(≤0.10),隐含增速数学噪声
5. ⚪ **已出快报·非沉默** — 已披露业绩快报,实际已表态

**这不是投资建议**,只是把需要重点尽调的标的挑出来。

## ⚠️ 局限与已知问题

- iFinD 高价值数据(盈利预测/研报/事件流)在当前账号下报 -4001/-208/-5100 不可用,只用其稳定的实时行情
- datacenter 部分表(财务三表详细科目)偶有 -209 字段权限问题,核心三表已规避
- 当前账号下 iwencai 不可用(参见 full-market-funnel 项目)
- 数据为研报与公司自报口径,本工具**不做投资建议**

## 🙏 致谢

- 数据源:东方财富 datacenter (公开 HTTP 接口)
- iFinD:同花顺 iFinD SDK

## License

AGPL-3.0
