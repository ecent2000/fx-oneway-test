# FX Oneway Factor Test

这个项目用于快速走通一遍外汇因子策略生命周期：

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

## 项目结构

```text
D:\fx-oneway-test\
  data\
    raw\
      eurusd\
        2023-01\
          eurusd_tick_2023-01-01.csv
          ...
        2023-02\
        ...
        2025-12\
    catalog\
  reports\
  scripts\
    01_ingest_dukascopy_daily.py
    02_backtest.py
    03_optimize.py
    04_walk_forward.py
    05_live_ib_paper.py
  src\
    fx_factor\
      strategies\
        rolling_zscore_fx.py
  download_dukascopy_fx_monthly.js
  README.md
```

部分脚本还没有实现，先按下面里程碑逐步补齐。

## 环境初始化

```powershell
cd D:\fx-oneway-test

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

本项目当前使用的是已经整理后的 daily tick 目录：

```text
data\raw\eurusd\YYYY-MM\eurusd_tick_YYYY-MM-DD.csv
```

这个目录是后续 ingest 的标准输入位置。不要再按旧的 `data\raw\dukascopy_tick\daily\...` 路径写新脚本。

如果需要重新下载，可以使用 `download_dukascopy_fx_monthly.js` 按天并行下载，然后把 daily 文件整理到上面的标准位置。

下载 `EUR/USD` 三年 tick 数据：

```powershell
cd D:\fx-oneway-test

node .\download_dukascopy_fx_monthly.js `
  --pairs eurusd `
  --from 2023-01-01 `
  --to 2026-01-01 `
  --timeframe tick `
  --concurrency 6 `
  --output-root data\raw\download_tmp `
  --cache-root data\raw\download_cache
```

下载脚本的临时 daily 输出会在：

```text
data\raw\download_tmp\daily\eurusd\YYYY-MM\eurusd_tick_YYYY-MM-DD.csv
```

下载完成后，项目最终使用的 raw 数据位置是：

```text
data\raw\eurusd\YYYY-MM\eurusd_tick_YYYY-MM-DD.csv
```

当前不依赖月度合并文件。月度合并太慢时，可以在 daily 下载完成且 `failed: 0` 后按 `Ctrl+C`，然后只保留 daily 数据。

下载进度文件在临时输出目录下：

```text
data\raw\download_tmp\progress.json
```

注意事项：

- 脚本会跳过已经存在的非空 daily 文件。
- 中断后直接重跑同一条命令即可续下。
- 周六/周日 FX 闭市时可能返回空 CSV，脚本会写入只有表头的 daily 文件。
- 后续 ingest 脚本读取 `data\raw\eurusd\YYYY-MM\*.csv`，不是读取下载临时目录。
- 如果出现网络错误，例如 `TypeError: terminated`，重跑即可；失败较多时把 `--concurrency 6` 降到 `--concurrency 3`。

Dukascopy tick CSV 字段通常为：

```text
timestamp,askPrice,bidPrice,askVolume,bidVolume
```

## 2. 转换为 Nautilus Catalog

下一步要实现：

```text
scripts\01_ingest_dukascopy_daily.py
```

目标：

```text
Dukascopy daily CSV from `data\raw\eurusd`
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

策略文件：

```text
src\fx_factor\strategies\rolling_zscore_fx.py
```

策略结构：

- 继承 `Strategy`
- 定义 `RollingZScoreFxConfig`
- `on_start()` 获取 instrument 并订阅 bar
- `on_bar()` 更新窗口、计算 z-score、下单或平仓
- `on_stop()` 取消订单、清理状态

策略伪代码：

```python
class RollingZScoreFxStrategy(Strategy):
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

回测脚本：

```text
scripts\02_backtest.py
```

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

优化脚本：

```text
scripts\03_optimize.py
```

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

脚本：

```text
scripts\04_walk_forward.py
```

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

NautilusTrader 在本项目中只负责研究阶段：

```text
数据标准化
-> 回测
-> 参数优化
-> 样本外验证
-> walk-forward
-> 成本/压力测试
```

第 10 步开始，将研究阶段确定下来的策略逻辑转写为完整 MT5 EA。NautilusTrader 不再负责 live trading，不接 `TradingNode`，也不接 IB / broker adapter。

MT5 EA 第一版必须是可编译、可回测、可 demo 运行的完整 EA，而不是只给信号的半成品。它的目标不是重新优化策略，而是忠实复现 NautilusTrader 中已经验证过的逻辑：

- 相同 symbol 映射，例如 `EUR/USD` -> `EURUSD` 或 `EURUSDm`
- 相同 bar 周期，例如 15 分钟
- 相同价格口径，优先用 bid/ask 计算 mid，避免只用 close 造成信号偏差
- 相同 rolling window、z-score、entry_z、exit_z
- 相同开仓、平仓、止损、最大持仓 bar 规则
- 相同交易时间过滤和仓位规则
- 保存每根 bar 的 close、mean、std、z-score、signal、position

完整 EA 至少包含：

- input 参数：symbol、timeframe、lookback、entry_z、exit_z、stop_z、max_position_bars、lot_size、magic_number
- signal 模块：只在新 bar 上计算 z-score，避免每个 tick 重复触发
- position 模块：识别当前 symbol + magic number 的持仓状态
- order 模块：开仓、平仓、拒绝重复开仓
- risk gate：spread 过大、交易未启用、bar 不完整、未知持仓、交易时间外时拒绝交易
- execution mode：`SignalOnly`、`DemoTrade`、`LiveTrade` 三种模式
- logging：signal log、order log、deal log、position snapshot、error log

迁移流程：

1. 生成完整 `.mq5` EA 文件
2. 在 MetaEditor 中编译通过，不能有 error
3. 下单模块先只支持单品种、单方向净持仓，不做加仓
4. 所有异常先拒绝交易，例如 symbol 不存在、spread 过大、bar 不完整、已有未知持仓
5. 默认运行模式为 `SignalOnly`，只有明确切换到 `DemoTrade` 或 `LiveTrade` 才允许下单
6. EA 完成后先进入 MT5 Strategy Tester 回测，不直接上 demo

EA 输出至少包括：

- signal log
- order log
- fill/deal log
- position snapshot
- error log

当前已有一个只记录信号的参考版本：

```text
mt5\RollingZScoreSignalLogger.mq5
```

这版 EA 只用于参考信号计算和日志格式，不能作为第 10 步最终交付。第 10 步最终交付应是完整 EA，既能 `SignalOnly` 记录信号，也能在 Strategy Tester / demo 中按配置执行下单。参考版本只在每根已完成 bar 后复现 rolling z-score 状态机，并把信号写入 MT5 `MQL5\Files\rolling_zscore_signal_log.csv`。默认参数对齐当前压力测试 baseline：

```text
symbol: 由 MT5 图表或 InpSymbol 决定，例如 EURUSD / EURUSDm
timeframe: M15
lookback: 96
entry_z: 1.5
exit_z: 0.2
stop_z: 0.0
max_position_bars: 0
price_mode: PRICE_BID_BAR_CLOSE
```

安装方式：

1. 打开 MT5 -> File -> Open Data Folder
2. 复制 `mt5\RollingZScoreSignalLogger.mq5` 到 `MQL5\Experts\FXOneway\`
3. 用 MetaEditor 编译
4. 把 EA 挂到目标 symbol 的 M15 图表
5. 对参考版本而言，`Algo Trading` 打开或关闭都可以，因为它不下单；完整 EA 回测和 demo 运行时需要按执行模式确认交易权限

日志字段：

```text
timestamp,symbol,timeframe,price_mode,close,mean,std,z_score,signal,
position_before,position_after,position_bars,entry_z,exit_z,stop_z,
max_position_bars,spread_points
```

注意：MT5 broker 历史 bar 常见口径是 bid close，而研究阶段 catalog 默认使用 `MID` bar。EA 提供两个价格口径：

- `PRICE_BID_BAR_CLOSE`: 使用 MT5 bar close，最稳定，适合第一轮跑通
- `PRICE_MID_FROM_TICK`: 尝试用每根 bar 收盘附近 tick 的 `(bid + ask) / 2`，取不到时回退到 bar close

MT5 回测前，先导出 `rolling_zscore_signal_log.csv`，再和 NautilusTrader 同时间段的 bar/z-score/signal 做逐行比较。允许 broker 报价源造成少量数值差异，但不能有系统性提前、滞后或方向相反。

## 11. MT5 Strategy Tester 回测 EA

完整 EA 生成后，先在 MT5 内用 Strategy Tester 回测 EA。这个阶段验证的是 EA 本身是否正确执行，而不是继续用 NautilusTrader 回测结果替代 MT5 回测。

回测流程：

1. 在 MT5 Strategy Tester 中选择 EA、symbol 和 15 分钟周期
2. 使用和 NautilusTrader 研究阶段尽量接近的历史区间
3. 设置和研究阶段一致的 lookback、entry_z、exit_z、stop_z、max_position_bars
4. 第一轮用 `SignalOnly` 模式，只检查 bar、z-score、signal
5. 第二轮用 `DemoTrade` / tester trade 模式，检查开仓、平仓、持仓和订单记录
6. 导出 MT5 backtest report、交易明细和 EA log
7. 对比 NautilusTrader 的回测信号、交易次数、方向、持仓时长、盈亏分布

允许存在 broker 历史数据和 Dukascopy 数据导致的轻微差异，但不能接受：

- 信号方向系统性相反
- 信号大面积提前或滞后
- 交易次数数量级不一致
- EA 重复开仓、漏平仓、异常加仓
- Strategy Tester 中出现未处理 error

MT5 回测通过后，才进入 demo/paper 执行验证。

## 12. MT5 Demo / Paper 执行验证

第一轮 paper/live 验证优先使用当前实际账户体系，例如 Exness MT5 demo account。

流程：

1. 开 MT5 demo account，例如 Exness demo
2. 在 MT5 中确认交易品种名称，例如 `EURUSD`、`EURUSDm` 或 broker 自定义 symbol
3. 用 MT5 的历史数据和实时行情复现研究阶段的 15 分钟 bar
4. 加载已经通过 Strategy Tester 回测的 MT5 EA
5. 第一阶段用 `SignalOnly` 模式实时跑，确认实时信号正常
6. 第二阶段切换到 `DemoTrade`，打开 demo 小仓位下单
7. 保存每笔信号、订单、成交、持仓和平仓原因

观察 2 到 4 周：

- 实时 bar 是否正确生成
- 信号是否和回测一致
- 订单是否正确提交
- 成交、拒单、滑点是否被完整记录
- 断线重连后状态是否正确
- 本地状态和 MT5 持仓是否一致
- spread 是否比历史数据更差
- 滑点是否可接受
- 日志是否足够定位问题

MT5 demo 执行阶段的重点不是优化参数，而是验证：

```text
研究信号
~= MT5 实时信号
~= MT5 demo 成交后的真实持仓变化
```

不要在 demo 阶段反复根据短期收益调参。demo 阶段主要排查工程问题、成交假设偏差和运行稳定性。

实盘执行不要跑在 Jupyter 里，应该使用独立 MT5 EA、独立 Python 脚本或服务。

## 13. MT5 小资金实盘

小资金实盘不要改策略核心参数，只改风控和运行参数：

- `demo` -> `live`
- 仓位缩小到最小可接受规模
- 加每日最大亏损
- 加最大连续亏损停止
- 加最大持仓时间
- 加交易时段过滤
- 加异常停止后自动平仓或人工确认机制
- 加网络断线、MT5 重启、账户状态异常时的保护逻辑
- 禁止同一品种重复开仓或失控加仓

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
4. 替换成 `RollingZScoreFxStrategy`
5. 单月回测成功
6. 三个月回测成功
7. grid search 成功
8. 样本外回测成功
9. 成本压力测试成功
10. 生成完整 MT5 EA
11. MT5 EA 在 MetaEditor 编译成功
12. MT5 Strategy Tester 回测通过
13. MT5 EA 信号和 NautilusTrader 回测信号对齐
14. MT5 demo 只记录信号成功
15. MT5 demo 小仓位下单成功
16. MT5 demo 连续运行 2 周无状态错误
17. MT5 小资金 live

最短路线：先别追求 alpha，先追求流程闭环。
