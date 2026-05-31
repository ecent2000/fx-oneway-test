# FX Factor Research Framework

这个仓库保留为外汇因子策略从想法到执行验证的通用流程框架。

以后只需要先给出一个因子策略思路，然后按本文档推进：先把想法写成可验证的策略规格，再依次完成数据、研究回测、稳健性验证、MT5 执行、demo/paper 和小资金实盘验证。

## 使用方式

给出新策略时，先尽量用下面格式描述。信息不完整也可以，缺口会在第 0 步补齐。

```text
策略名称：
交易品种：
数据频率：
因子直觉：
因子公式：
做多条件：
做空条件：
平仓条件：
止损/止盈/最大持仓：
交易时段过滤：
仓位方式：
主要风险：
希望验证的时间区间：
```

策略想法进入仓库后，所有代码、报告和 MT5 文件都应围绕一个明确的 `strategy_id` 组织，避免不同策略互相污染。

## 目录约定

```text
project-root\
  data\
    raw\                  # 保留本地原始数据，不随清理删除
    catalog\              # 保留本地 Nautilus catalog，不随清理删除
  reports\                # 运行产物，默认不提交
  scripts\                # 当前策略的研究、回测、优化、验证脚本
  src\                    # 当前策略的 Python 包和可复用模块
  mt5\                    # 当前策略的 MT5 EA、脚本和编译说明
  README.md               # 通用流程标准
```

`data/` 是长期资产，清理仓库时不要删除。`reports/`、`__pycache__/`、`.ex5`、Strategy Tester 导出文件和一次性日志都属于运行产物，默认不提交。

## 0. 策略规格化

把策略想法转成可执行规格，至少确认：

- `strategy_id`：短横线命名，例如 `asian-session-breakout`
- 标的和 symbol 映射：例如 `EUR/USD`、`EUR/USD.SIM`、MT5 broker symbol
- 数据口径：bid、ask、mid、bar close、bar open、tick 或外部 bar
- 因子计算：输入字段、窗口、标准化方式、缺失值和 warmup
- 交易状态机：入场、出场、反向信号、重复信号、最大持仓
- 执行假设：市价单、限价单、点差过滤、滑点、手续费、延迟
- 风控：仓位、杠杆、最大亏损、交易时段、异常停止
- 验证区间：训练、验证、最终测试、walk-forward 窗口

只有规格明确后才开始写代码。

## 1. 数据盘点

检查 `data/raw/` 和 `data/catalog/` 是否已经覆盖策略需要的品种、时间区间和频率。

原始 tick 数据推荐结构：

```text
data\raw\<symbol>\YYYY-MM\<symbol>_tick_YYYY-MM-DD.csv
```

标准 tick CSV 字段建议统一为：

```text
timestamp,askPrice,bidPrice,askVolume,bidVolume
```

如果数据已经存在，不重复下载；如果缺少数据，先补齐缺口，再进入研究。

## 2. Catalog 构建

目标是把原始数据转换成 NautilusTrader 可读的 `ParquetDataCatalog`。

标准输出：

```text
data\catalog\
```

转换阶段要记录：

- 数据源和下载时间
- symbol 映射
- timezone 和 timestamp 口径
- bid/ask/mid 生成规则
- bar 聚合周期和 price type
- 缺失日期、空文件、异常 spread

## 3. 策略实现

在 `src/` 中实现当前策略的 NautilusTrader `Strategy` 和 `StrategyConfig`。

实现最低要求：

- `on_start()` 获取 instrument、订阅数据、初始化状态
- `on_bar()` 或 `on_quote_tick()` 计算因子并驱动状态机
- 独立封装下单、平仓、点差过滤和风控检查
- `on_stop()` 取消订单并按配置处理未平仓
- 参数全部放入 config，避免写死在策略逻辑里

策略实现必须能被 `ImportableStrategyConfig` 加载。

## 4. 单次回测

先跑最小可解释区间，例如 1 个月或 3 个月。

单次回测至少输出：

- config 快照
- orders
- fills
- positions
- account
- summary metrics
- engine log

第一轮只确认链路可运行、订单方向正确、没有重复开仓或漏平仓；不要急着优化收益。

## 5. 参数优化

第一版用 grid search 或少量手工候选参数。

选择参数时优先看：

- 相邻参数区域是否稳定
- 交易次数是否足够
- 收益是否集中在少数极端交易
- 回撤和换手是否可接受
- 成本敏感性是否过高

不要只选训练集收益最高的一组。

## 6. 样本外验证

推荐拆分：

```text
训练 / 优化：一段连续历史
验证：      后续连续历史
最终测试：  最后一段只运行一次
```

如果最终测试失败，记录失败原因。不要反复回到训练阶段把测试集调到好看。

## 7. Walk-Forward

滚动验证示例：

```text
用 6 个月优化 -> 跑未来 1 个月
窗口向前滚动 1 个月
重复多轮
```

检查：

- 每个窗口的最佳参数是否稳定
- 样本外收益是否连续
- 回撤是否集中在某些行情
- 成本、换手和持仓时间是否失控

## 8. 成本和压力测试

逐步加入真实执行约束：

- bid/ask spread
- 手续费
- 滑点
- 延迟
- `price_protection_points`
- `liquidity_consumption`
- spread 放大
- 杠杆变化
- 交易时段过滤

策略只有在成本压力下仍有合理余量，才进入 MT5 实现。

## 9. MT5 EA 实现

MT5 阶段目标是复现已经确定的策略逻辑，不在 EA 里重新发明策略。

EA 最低要求：

- 只在新 bar 或明确事件上触发
- 支持 signal-only 和 allow-trading 两种模式
- 单品种净持仓状态清晰
- 开仓、平仓、拒单和错误都有日志
- 对 symbol 不可用、spread 过大、bar 不完整、未知持仓等情况拒绝交易
- 参数和研究阶段保持一一映射

## 10. MT5 Strategy Tester

先在 Strategy Tester 里验证 EA：

1. 使用与研究阶段一致的 symbol、周期、参数和时间区间。
2. 先 signal-only，检查信号方向和时间。
3. 再 allow-trading，检查订单、持仓和平仓原因。
4. 导出 HTML report、signal log、order log、error log。
5. 与 NautilusTrader 输出做结构化对齐。

不能接受：

- 信号方向系统性相反
- 交易次数数量级不一致
- 连续漏平仓或重复开仓
- 未解释的大量 error
- 风控口径在研究和 MT5 中明显不同

## 11. NautilusTrader 与 MT5 对齐

对齐输入：

- NautilusTrader catalog 或回测报告
- MT5 signal log
- MT5 order log
- MT5 error log
- MT5 Strategy Tester HTML report
- 完全一致的策略参数

对齐检查：

- bar timestamp 口径是否一致
- price、factor、signal 是否接近
- 状态机信号是否一致
- 开平仓数量和方向是否一致
- 未匹配订单是否可解释

对齐报告至少保存到 `reports/<strategy_id>/alignment/`。

## 12. Demo / Paper

MT5 回测和对齐通过后，进入 demo/paper。

流程：

1. 第一阶段只观察信号，不下单。
2. 第二阶段打开小仓位 demo 下单。
3. 保存信号、订单、成交、持仓、错误和平仓原因。
4. 连续观察 2 到 4 周。

demo 阶段主要验证工程稳定性和成交假设，不根据短期收益反复调参。

## 13. 小资金实盘

小资金实盘只调整运行风控，不改变策略核心逻辑。

必须具备：

- 最小可接受仓位
- 每日最大亏损
- 最大连续亏损停止
- 最大持仓时间
- 交易时段过滤
- 连接和账户异常保护
- 未知持仓处理
- 手动干预流程

目标是确认：

```text
研究信号 ~= MT5 demo 信号 ~= MT5 live 信号
研究成交假设 ~= MT5 demo 成交统计 ~= MT5 live 成交统计
本地执行状态 ~= MT5 账户状态
```

## 标准里程碑

1. 策略规格化完成
2. 数据覆盖确认
3. Catalog 可用
4. NautilusTrader 策略实现
5. 单次回测通过
6. 参数优化完成
7. 样本外验证完成
8. Walk-forward 完成
9. 成本和压力测试完成
10. MT5 EA 编译通过
11. MT5 Strategy Tester 通过
12. NautilusTrader 与 MT5 对齐通过
13. Demo signal-only 观察通过
14. Demo 小仓位交易通过
15. 小资金实盘准备完成

最短路线：先闭环，再优化。每个新因子策略都从规格化开始，所有阶段都留下可复核产物。
