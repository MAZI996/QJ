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
TRADINGAGENTS_CRYPTO_MAX_POSITION_PCT=0.10
TRADINGAGENTS_CRYPTO_MIN_CONFIDENCE=0.62
TRADINGAGENTS_CRYPTO_AI_ROUTER=tradingagents
TRADINGAGENTS_CRYPTO_AI_MODEL=
TRADINGAGENTS_CRYPTO_AI_DECISION_POLICY=advisory_only
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

实盘前，Hermes 输出必须落到结构化结果：`BUY/HOLD/REJECT`、置信度、理由、主要风险、失效条件。执行层只接受风控通过后的订单意图。

## 使用入口

```powershell
python -m cli.main crypto-account
python -m cli.main crypto-scan --symbols BTCUSDT,ETHUSDT --mode analysis
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
