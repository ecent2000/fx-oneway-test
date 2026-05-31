# FX Oneway Factor Test

本流程用于快速走通一遍外汇因子策略生命周期：

```text
FX QuoteTick 数据
-> ParquetDataCatalog
-> 自定义因子策略
-> 单次回测
-> 参数优化
-> 样本外验证
-> 成本/滑点压力测试
-> 生成完整 MT5 EA
-> MT5 Strategy Tester 回测 EA
-> MT5 demo/paper 执行验证
-> MT5 小资金实盘
```

第一遍目标不是找到完美 alpha，而是把完整工程链路闭环。

## 第一遍策略目标

- 标的：`EUR/USD`
- 数据：Dukascopy bid/ask quote tick
- 聚合周期：15 分钟 bar
- 因子：滚动 z-score
- 逻辑：价格偏离均值过多时做均值回归
- 交易：市价单
- 仓位：固定名义金额，例如 `100,000 EUR`
- 成本：第一轮让 bid/ask spread 自然进入回测，后续再加手续费、滑点、延迟

因子定义：

```text
mid = (bid + ask) / 2
factor = (mid_close - rolling_mean(mid_close, N)) / rolling_std(mid_close, N)

factor < -entry_z: 做多 EUR/USD
factor >  entry_z: 做空 EUR/USD
abs(factor) < exit_z: 平仓
```

## 推荐目录结构

```text
project-root\
  data\
    raw\
      <symbol>\
    catalog\
  reports\
  scripts\
    01_ingest_data.py
    02_backtest.py
    03_optimize.py
    04_walk_forward.py
  mt5\
    <strategy_ea>.mq5
  src\
    <package>\
      strategies\
        <strategy>.py
  README.md
```

具体文件名可以按项目习惯调整，但建议保留数据、研究、报告和 MT5 执行代码的边界。

## 环境初始化

```powershell
cd <project-root>

py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1

python -m pip install -U pip
pip install -U nautilus_trader pandas numpy pyarrow
```

可视化依赖不是第一步必须项。需要画 equity curve、drawdown 或 tearsheet 时再装：

```powershell
pip install -U "nautilus_trader[visualization]"
```

验证 NautilusTrader 可导入：

```powershell
python -c "import nautilus_trader; print('nautilus ok')"
```

## 1. 下载 Dukascopy Tick 数据

原始 tick 数据建议整理成按品种和日期分区的 daily 文件：

```text
data\raw\<symbol>\YYYY-MM\<symbol>_tick_YYYY-MM-DD.csv
```

这个目录作为后续 ingest 的标准输入位置。

注意事项：

- 脚本会跳过已经存在的非空 daily 文件。
- 中断后直接重跑同一条命令即可续下。
- 周六/周日 FX 闭市时可能返回空文件，需要显式处理。
- 后续 ingest 只读取标准 raw 目录，不读取下载临时目录。

Dukascopy tick CSV 字段通常为：

```text
timestamp,askPrice,bidPrice,askVolume,bidVolume
```

## 2. 转换为 Nautilus Catalog

目标：

```text
Daily tick CSV
-> Nautilus QuoteTick
-> ParquetDataCatalog
```

建议第一轮只导入一个月数据，例如：

```text
2024-10-01 到 2024-11-01
```

跑通后再导入三个月、一年、三年。

Nautilus 侧核心组件：

- `QuoteTick`
- `QuoteTickDataWrangler`
- `ParquetDataCatalog`
- `TestInstrumentProvider.default_fx_ccy("EUR/USD")`

目标 catalog 路径：

```text
data\catalog\
```

## 3. 实现 Rolling Z-Score 策略

策略结构：

- 继承 `Strategy`
- 定义策略配置
- `on_start()` 获取 instrument 并订阅 bar
- `on_bar()` 更新窗口、计算 z-score、下单或平仓
- `on_stop()` 取消订单、清理状态

策略伪代码：

```python
class StrategyExample(Strategy):
    def on_start(self):
        self.instrument = self.cache.instrument(self.config.instrument_id)
        self.subscribe_bars(self.config.bar_type)
        self.request_bars(self.config.bar_type)

    def on_bar(self, bar):
        close = float(bar.close)
        self.window.append(close)

        if len(self.window) < self.config.lookback:
            return

        mean = np.mean(self.window)
        std = np.std(self.window)
        z = (close - mean) / std if std > 0 else 0.0

        is_flat = self.portfolio.is_flat(self.config.instrument_id)
        is_long = self.portfolio.is_net_long(self.config.instrument_id)
        is_short = self.portfolio.is_net_short(self.config.instrument_id)

        if is_flat and z < -self.config.entry_z:
            self.buy_market()
        elif is_flat and z > self.config.entry_z:
            self.sell_market()
        elif is_long and z > -self.config.exit_z:
            self.close_position()
        elif is_short and z < self.config.exit_z:
            self.close_position()
```

第一版可以参考 Nautilus 官方 `ema_cross` 示例的订单创建方式，只替换信号逻辑。

## 4. 单次回测

第一轮配置：

```text
instrument_id: EUR/USD.SIM
bar_type: EUR/USD.SIM-15-MINUTE-BID-INTERNAL
lookback: 96
entry_z: 1.5
exit_z: 0.2
trade_size: 100_000
start_time: 2024-10-01T00:00:00Z
end_time: 2024-11-01T00:00:00Z
```

使用 Nautilus high-level API：

- `BacktestVenueConfig`
- `BacktestDataConfig`
- `ImportableStrategyConfig`
- `BacktestRunConfig`
- `BacktestNode`

## 5. 回测报告

每次回测至少保存：

- orders report
- fills report
- positions report
- account report
- PnL
- Sharpe
- Profit factor
- Win rate
- 最大回撤
- equity curve
- drawdown curve

报告输出目录：

```text
reports\
```

## 6. 参数优化

第一轮用 grid search，不先上复杂优化器：

```text
lookback: 48, 96, 192
entry_z: 1.0, 1.5, 2.0
exit_z: 0.0, 0.2, 0.5
bar_period: 5m, 15m, 60m
```

流程：

1. 固定训练区间，例如 `2024-01-01` 到 `2024-06-30`
2. 为每组参数生成一个 `BacktestRunConfig`
3. 用 `BacktestNode(configs=configs).run()`
4. 汇总收益、回撤、Sharpe、交易次数
5. 不选收益最高的，优先选附近参数都不差的稳定区域

## 7. 样本外验证

推荐切分：

```text
训练 / 优化：2024-01 到 2024-06
验证：      2024-07 到 2024-09
最终测试：  2024-10 到 2024-12
```

规则：

- 优化时不看最终测试集。
- 最终测试集只跑一次。
- 如果测试集不行，不反复回去调参。
- 记录失败原因，而不是硬调到赚钱。

## 8. Walk-Forward

滚动验证：

```text
用 6 个月优化 -> 跑未来 1 个月
窗口向前滚动 1 个月
重复 12 次
```

输出：

- 每个窗口的最佳参数
- 每个窗口的样本外收益
- 参数是否稳定
- 收益是否集中在少数月份
- 换手和成本是否失控

## 9. 成本和压力测试

第一轮 quote tick 已经自然包含点差，因为买在 ask、卖在 bid。

第二轮开始加入：

- 手续费模型 `fee_model`
- 滑点模型 `fill_model`
- 延迟模型 `latency_model`
- `price_protection_points`
- `liquidity_consumption`
- spread 放大倍数
- 不同杠杆
- 不同交易时间过滤

## 10. 生成完整 MT5 EA

第 10 步开始，将研究阶段确定下来的策略逻辑实现为完整 MT5 EA。NautilusTrader 只负责研究、回测和压力测试；执行阶段由 MT5 负责。

EA 必须是可编译、可回测、可 demo 运行的完整版本，而不是只输出信号的半成品。第一版目标不是重新优化策略，而是复现研究阶段已经确定的交易逻辑。

EA 至少需要覆盖：

- 信号计算
- 开仓和平仓
- 仓位识别
- 基础风控
- 异常保护
- 运行日志

实现原则：

1. 只在新 bar 上计算信号，避免同一根 bar 重复触发。
2. 默认支持只观察信号和允许交易两种运行模式。
3. 下单逻辑先保持简单，优先支持单品种、单方向净持仓。
4. 遇到 symbol 不可用、spread 异常、bar 不完整、已有未知持仓等情况时拒绝交易。
5. EA 完成后必须先进入 MT5 Strategy Tester 回测，不直接上 demo。

## 11. MT5 Strategy Tester 回测 EA

完整 EA 生成后，先在 MT5 内用 Strategy Tester 回测 EA。这个阶段验证的是 EA 本身是否正确执行，而不是继续用 NautilusTrader 回测结果替代 MT5 回测。

回测流程：

1. 在 MT5 Strategy Tester 中选择 EA、symbol、周期和历史区间。
2. 使用与研究阶段一致的核心策略参数。
3. 第一轮只检查信号，不评估收益。
4. 第二轮开启交易逻辑，检查开仓、平仓、持仓和订单记录。
5. 导出 MT5 回测报告、交易明细和 EA 日志。
6. 对比 NautilusTrader 和 MT5 的信号、交易次数、方向和持仓时长。

允许存在 broker 历史数据和 Dukascopy 数据导致的轻微差异，但不能接受：

- 信号方向系统性相反
- 信号大面积提前或滞后
- 交易次数数量级不一致
- EA 重复开仓、漏平仓、异常加仓
- Strategy Tester 中出现未处理 error

MT5 回测通过后，才进入 demo/paper 执行验证。

## 12. NautilusTrader 与 MT5 对齐复核

进入 demo/paper 前，先把 NautilusTrader 研究结果和 MT5 Strategy Tester 的 EA 输出做一次结构化对齐。这个步骤的目标不是让两边收益完全一致，而是确认策略状态机、信号方向、开平仓节奏和执行风控没有系统性偏差。

对齐输入至少包括：

- NautilusTrader catalog 或回测报告目录
- MT5 EA signal log
- MT5 EA order log
- MT5 EA error log
- MT5 Strategy Tester HTML report
- 完全一致的策略参数：symbol 映射、周期、lookback、entry_z、exit_z、stop_z、max_position_bars

第一步先对齐信号层。建议在 MT5 EA 中每根已完成 bar 输出 `timestamp`、`close`、`mean`、`std`、`z_score`、`signal`、`position_before`、`position_after` 和 `spread_points`。NautilusTrader 侧用相同 bar_type、相同 rolling window 和相同状态机重新生成信号，然后按时间戳 join。

示例命令：

```powershell
python .\scripts\08_align_mt5_nautilus_signals.py `
  --mt5-signal-log "<mt5_common_files>\rolling_zscore_signal_log_<run_id>.csv" `
  --mt5-order-log "<mt5_common_files>\rolling_zscore_order_log_<run_id>.csv" `
  --mt5-error-log "<mt5_common_files>\rolling_zscore_error_log_<run_id>.csv" `
  --catalog .\data\catalog `
  --bar-type EUR/USD.SIM-15-MINUTE-BID-EXTERNAL `
  --start 2023-01-01T00:00:00Z `
  --end 2026-01-01T00:00:00Z `
  --lookback 96 `
  --entry-z 1.5 `
  --exit-z 0.2 `
  --stop-z 0.0 `
  --max-position-bars 0 `
  --out-dir .\reports\align_mt5_nautilus_latest
```

信号层重点检查：

- `best_offset_minutes` 是否稳定，避免 bar open time / close time 口径错位。
- `close_mae`、`z_mae` 和 `z_corr` 是否显示两边价格和 z-score 口径接近。
- `raw_entry_match_rate` 是否说明入场阈值区域一致。
- `state_signal_match_rate` 和 `state_non_hold_match_rate` 是否说明状态机一致。
- `state_signal_mismatches.csv` 中是否存在成片的方向相反、提前或滞后。

第二步再对齐订单层。NautilusTrader 回测需要加载能够支持成交的 QuoteTick，并启用和 MT5 一致的交易风控，例如最大点差过滤：

```powershell
python .\scripts\02_backtest.py `
  --catalog .\data\catalog `
  --bar-type EUR/USD.SIM-15-MINUTE-BID-EXTERNAL `
  --data-kind bars `
  --include-quote-ticks `
  --start 2023-01-01T00:00:00Z `
  --end 2026-01-01T00:00:00Z `
  --lookback 96 `
  --entry-z 1.5 `
  --exit-z 0.2 `
  --max-spread-points 30
```

订单层重点检查：

- MT5 成功订单数、失败订单数和 NautilusTrader 订单数是否接近。
- 按 `ENTER_LONG`、`ENTER_SHORT`、`EXIT_LONG_*`、`EXIT_SHORT_*` 分组后的数量是否接近。
- 严格时间窗口内的开平仓匹配率是否足够高。
- 放宽时间窗口后匹配率是否明显上升，用来识别成交延迟或 bar 时间口径差异。
- 未匹配订单是否集中在点差过大、订单被拒、交易时段差异、broker 报价跳动或阈值边界附近。

判断标准：

- 可以接受：broker 历史数据和 Dukascopy 数据源不同导致的少量阈值边界差异。
- 可以接受：MT5 tester 秒级成交时间和 NautilusTrader 事件时间不同导致的小窗口错位。
- 需要修复：方向系统性相反、订单数量数量级不一致、连续漏平仓、重复开仓、持仓状态不同步。
- 需要修复：MT5 error log 中出现大量未解释的交易错误。
- 需要修复：点差、交易权限、未知持仓、bar 不完整等风控在两边口径不同。

对齐报告至少保存：

- `summary.json`
- `offset_candidates.csv`
- `matched_bars.csv`
- `worst_z_diffs.csv`
- `state_signal_mismatches.csv`
- `unmatched_nautilus_orders.csv`
- `unmatched_mt5_orders.csv`

只有当信号层基本一致、订单层差异可以解释，并且 MT5 error log 没有未处理的系统性错误时，才进入 demo/paper 执行验证。

## 13. MT5 Demo / Paper 执行验证

MT5 回测通过后，再进入 demo/paper 执行验证。

流程：

1. 在 demo/paper 账户中加载已经通过 Strategy Tester 的 EA。
2. 第一阶段只观察实时信号，不下单。
3. 第二阶段打开小仓位 demo 下单。
4. 保存信号、订单、成交、持仓和平仓原因。

观察 2 到 4 周：

- 实时信号是否稳定
- 订单和持仓是否正确
- 成交、拒单、滑点是否被记录
- 断线重连后状态是否正常
- demo 成交质量是否明显劣于回测假设

MT5 demo 执行阶段的重点不是优化参数，而是验证：

```text
研究信号
~= MT5 实时信号
~= MT5 demo 成交后的真实持仓变化
```

不要在 demo 阶段反复根据短期收益调参。demo 阶段主要排查工程问题、成交假设偏差和运行稳定性。

实盘执行应该使用独立、可持续运行的 MT5 EA 或服务。

## 14. MT5 小资金实盘

小资金实盘不要改策略核心参数，只改风控和运行参数：

- `demo` -> `live`
- 仓位缩小到最小可接受规模
- 加每日最大亏损
- 加最大连续亏损停止
- 加最大持仓时间
- 加交易时段过滤
- 加异常停止后自动平仓或人工确认机制
- 加连接、账户、持仓异常时的保护逻辑
- 禁止重复开仓或失控加仓

第一阶段目标：

```text
NautilusTrader 回测信号 ~= MT5 demo 信号 ~= MT5 live 信号
NautilusTrader 成交假设 ~= MT5 demo 成交统计 ~= MT5 live 成交统计
本地执行状态 ~= MT5 账户状态
```

## 第一轮里程碑

1. 下载 EUR/USD Dukascopy tick daily 数据
2. 将 daily CSV 转成 Nautilus `ParquetDataCatalog`
3. 跑通官方或最小 FX high-level backtest
4. 替换成自定义策略
5. 单月回测成功
6. 三个月回测成功
7. grid search 成功
8. 样本外回测成功
9. 成本压力测试成功
10. 生成完整 MT5 EA
11. MT5 EA 在 MetaEditor 编译成功
12. MT5 Strategy Tester 回测通过
13. MT5 EA 信号和 NautilusTrader 回测信号对齐
14. MT5 demo 观察信号成功
15. MT5 demo 小仓位下单成功
16. MT5 demo 连续运行 2 周无状态错误
17. MT5 小资金 live

最短路线：先别追求 alpha，先追求流程闭环。
