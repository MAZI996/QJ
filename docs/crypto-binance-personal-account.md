# Binance 个人账户自动交易接入说明

本项目把 Binance 个人账户接入分成四个阶段：

1. `analysis`：只扫描行情和输出交易意图，不下单。
2. `paper`：使用本地纸面成交日志模拟下单，不访问交易接口。
3. `testnet`：调用 Binance Spot test order，用个人 API 形态验证签名、数量、交易规则，但不真实成交。
4. `live`：真实 Binance 现货市价单。默认关闭，必须显式打开配置并提供确认短语。

## 个人账户准备

- 创建 Binance API Key 时只开启读取和现货交易权限。
- 不开启提现权限。
- 能设置 IP 白名单时，优先绑定运行机器或服务器出口 IP。
- 先使用 Spot Testnet 或小资金账户验证流程。
- 第一阶段只做现货，不接合约和杠杆。

## 环境变量

```env
BINANCE_API_KEY=
BINANCE_API_SECRET=
TRADINGAGENTS_CRYPTO_BINANCE_TESTNET=true
TRADINGAGENTS_CRYPTO_SYMBOLS=BTCUSDT,ETHUSDT,BNBUSDT,SOLUSDT
TRADINGAGENTS_CRYPTO_INTERVAL=15m
TRADINGAGENTS_CRYPTO_ACCOUNT_EQUITY_USDT=10000
TRADINGAGENTS_CRYPTO_RISK_PER_TRADE_PCT=0.005
TRADINGAGENTS_CRYPTO_MAX_LOSS_PER_TRADE_USDT=0
TRADINGAGENTS_CRYPTO_MAX_POSITION_PCT=0.10
TRADINGAGENTS_CRYPTO_MIN_CONFIDENCE=0.62
TRADINGAGENTS_CRYPTO_LANA_STRATEGY_ENABLED=true
TRADINGAGENTS_CRYPTO_LANA_HOT_SYMBOLS=BTCUSDT,ETHUSDT
TRADINGAGENTS_CRYPTO_HOTLIST_ENABLED=true
TRADINGAGENTS_CRYPTO_HOTLIST_PATH=C:\Users\you\.tradingagents\crypto\hotlist.json
TRADINGAGENTS_CRYPTO_HOTLIST_MAX_AGE_HOURS=24
TRADINGAGENTS_CRYPTO_HOTLIST_MIN_SCORE=0
TRADINGAGENTS_CRYPTO_LANA_MIN_PRICE_CHANGE_PCT=3
TRADINGAGENTS_CRYPTO_LANA_MAX_PRICE_CHANGE_PCT=18
TRADINGAGENTS_CRYPTO_LANA_MIN_OI_CHANGE_PCT=8
TRADINGAGENTS_CRYPTO_LANA_FIXED_STOP_LOSS_PCT=0.025
TRADINGAGENTS_CRYPTO_AI_ROUTER=tradingagents
TRADINGAGENTS_CRYPTO_AI_MODEL=
TRADINGAGENTS_CRYPTO_AI_DECISION_POLICY=advisory_only
TRADINGAGENTS_CRYPTO_AI_AGENT_STYLE=tradingagents_crypto
TRADINGAGENTS_CRYPTO_HERMES_BASE_URL=http://127.0.0.1:8000/v1
TRADINGAGENTS_CRYPTO_HERMES_API_KEY=
TRADINGAGENTS_CRYPTO_HERMES_TIMEOUT_SECONDS=45
TRADINGAGENTS_CRYPTO_EXECUTION_MODE=analysis
TRADINGAGENTS_CRYPTO_ENABLE_LIVE_ORDERS=false
TRADINGAGENTS_CRYPTO_EMERGENCY_STOP_FILE=C:\tradingagents-stop.txt
```

## Hermes 和大模型选择

后期 AI 运作层按 `Hermes -> 选择大模型 -> AI 评审/决策` 设计。交易系统不直接绑定某一个模型，行情扫描、风控、执行层也不依赖模型供应商。

当前代码已经预留 `TRADINGAGENTS_CRYPTO_AI_ROUTER` 和 `TRADINGAGENTS_CRYPTO_AI_MODEL`：

- `TRADINGAGENTS_CRYPTO_AI_ROUTER=tradingagents`：当前默认路径，沿用项目已有 LLM factory。
- `TRADINGAGENTS_CRYPTO_AI_ROUTER=hermes`：后期目标路径，当前按 OpenAI-compatible `/chat/completions` 适配。
- `TRADINGAGENTS_CRYPTO_AI_MODEL`：Hermes 要调用的大模型名称；选择 Hermes 时不能为空。
- `TRADINGAGENTS_CRYPTO_HERMES_BASE_URL`：Hermes 服务地址，例如 `http://127.0.0.1:8000/v1`。
- `TRADINGAGENTS_CRYPTO_HERMES_API_KEY`：Hermes 鉴权密钥，没有鉴权时留空。
- `TRADINGAGENTS_CRYPTO_AI_DECISION_POLICY=advisory_only`：AI 只能评审、解释和调整候选信号，不能绕过硬风控直接下单。
- `TRADINGAGENTS_CRYPTO_AI_AGENT_STYLE=tradingagents_crypto`：沿用 TradingAgents 的多角色交接方式，但替换成 Binance 现货语境。

实盘前，Hermes 输出必须落到结构化结果：`BUY/HOLD/REJECT`、置信度、理由、主要风险、失效条件。执行层只接受风控通过后的订单意图。

## Lana-inspired 策略参考

`lana-inspired-attention-oi-v1` 是对 X 动态中策略描述的保守改写，放在现货候选扫描层，不开启合约或杠杆。原策略核心是：刷币安广场高流量帖子和高发帖量币种，再从涨幅榜里找波动最大的币，买入同时挂止损，后来从百分比止损改成固定亏损额。我们保留“热度 + 涨幅/波动 + 严格止损”的思路，但不照搬高杠杆合约玩法。

- 热度：可通过 `TRADINGAGENTS_CRYPTO_LANA_HOT_SYMBOLS` 人工输入从 X、币安广场、论坛、社群观察到的热币。
- 涨幅/波动：优先看 24 小时涨幅进入观察区间、成交额足够、短周期成交量放大。
- OI：这是我们额外加的可选增强过滤，读取 Binance USD-M futures open interest history，只作为仓位活跃度参考，不作为合约下单依据。
- 纪律：默认价格止损 `2.5%`，止盈按 `R` 倍数计算；如需模仿原帖“固定亏损额”，设置 `TRADINGAGENTS_CRYPTO_MAX_LOSS_PER_TRADE_USDT=200`，再交给个人账户硬风控确认。

这套策略不会承诺盈利，也不会绕过 `RiskManager`。如果 OI 数据拿不到，扫描不会中断，只会跳过可选 OI 过滤。

## Hotlist 热度入口

`crypto-hotlist` 是后续自动采集的统一入口。当前可以手动把 X、币安广场、论坛、社群里突然升温的币种写入本地文件：

```powershell
python -m cli.main crypto-hotlist --add SOLUSDT,WIFUSDT --source binance-square --reason "高流量帖子和发帖量上升"
```

也可以把帖子、群聊、论坛内容作为文本交给注意力解析器，让它自动提取 `$SOL`、`SOLUSDT`、`#WIF` 这类币种符号并写入 hotlist：

```powershell
python -m cli.main crypto-attention-ingest --source x --text '$SOL 突然很多人讨论，币安广场也在刷，WIFUSDT 成交量拉起来了'
python -m cli.main crypto-attention-ingest --source binance-square --file .\square-posts.txt
python -m cli.main crypto-attention-ingest --source x --file .\posts.txt --dry-run
```

默认文件是 `~/.tradingagents/crypto/hotlist.json`。`crypto-scan` 默认会把 hotlist 里的有效交易对合并进扫描范围，并作为 Lana-inspired 策略的热度信号。需要临时关闭时：

```powershell
python -m cli.main crypto-scan --symbols BTCUSDT,ETHUSDT --no-hotlist --mode analysis
```

## 使用入口

```powershell
python -m cli.main crypto-account
python -m cli.main crypto-attention-ingest --source x --text '$SOL 和 #WIF 在讨论区升温'
python -m cli.main crypto-hotlist --add SOLUSDT --source x --reason "X 和币安广场讨论升温"
python -m cli.main crypto-hotlist
python -m cli.main crypto-scan --symbols BTCUSDT,ETHUSDT --mode analysis
python -m cli.main crypto-scan --symbols BTCUSDT,ETHUSDT,SOLUSDT --hot-symbols SOLUSDT --mode analysis
python -m cli.main crypto-scan --symbols BTCUSDT,ETHUSDT --mode analysis --ai-review
python -m cli.main crypto-scan --symbols BTCUSDT,ETHUSDT --mode paper --execute-top
python -m cli.main crypto-scan --symbols BTCUSDT --mode testnet --execute-top
```

真实下单需要同时满足：

- `TRADINGAGENTS_CRYPTO_BINANCE_TESTNET=false`
- `TRADINGAGENTS_CRYPTO_ENABLE_LIVE_ORDERS=true`
- CLI 使用 `--mode live --execute-top`
- CLI 提供 `--live-confirm`，且内容等于 `TRADINGAGENTS_CRYPTO_LIVE_CONFIRM_PHRASE`

## 当前风控边界

- 只允许现货 `BUY` 候选。
- 置信度低于阈值不交易。
- 没有止损、止盈或盈亏比不足不交易。
- 单笔风险按账户权益百分比计算。
- 仓位上限按账户权益百分比计算。
- 下单数量会按 Binance `LOT_SIZE` 规则向下取整。
- 名义金额必须满足 Binance `MIN_NOTIONAL`/`NOTIONAL` 规则。
- 如果急停文件存在，执行层会拒绝任何订单。

## 后续必须补齐

- 用户数据流监听，用于确认真实订单最终状态。
- 订单查询和未知状态恢复，处理超时、5XX、网络中断。
- 持仓状态机，跟踪止损、止盈、撤单、重复信号冷却。
- 每日亏损熔断和手动急停文件。
- 回测模块，证明策略有正期望后再考虑实盘。
- AI 分析层只允许提高或降低信号置信度，不能绕过风控直接下单。
- Hermes 适配器和结构化模型输出校验。
