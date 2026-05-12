# 在 TradingAgents 基础上扩展 crypto 自动交易

本项目不是另起一个交易机器人，而是在 `TauricResearch/TradingAgents` 的基础上增加 Binance 个人账户、Hermes 大模型路由和自动交易风控层。

## 上游关系

- `upstream`：`https://github.com/TauricResearch/TradingAgents.git`
- `origin`：`git@github.com:MAZI996/QJ.git`

后续升级时优先从 `upstream/main` 吸收原项目的 agent、LLM、CLI、结构化输出改进；我们的 Binance 扩展尽量放在 `tradingagents/crypto/` 和 `docs/`，减少和上游股票分析代码冲突。

## 保留的 TradingAgents 思路

原项目核心链路是：

`Analyst Team -> Bull/Bear Researchers -> Research Manager -> Trader -> Risk Analysts -> Portfolio Manager`

crypto 扩展沿用这个链路，但数据和约束换成：

- 股票行情数据替换为 Binance Spot K线、24h ticker、交易规则和个人账户余额。
- Fundamentals Analyst 暂不用于币种基本面，第一阶段以技术、成交量、波动率和风险为主。
- Sentiment Analyst 默认把情绪视为未知，除非后续明确接入新闻、社媒或链上数据。
- Trader 只能提出现货 `BUY/HOLD/REJECT`，不允许做空、合约、杠杆。
- Portfolio Manager 的最终意见仍必须经过确定性风控，不能直接下单。

## 当前代码落点

- `tradingagents/crypto/binance_client.py`：Binance Spot REST、余额、交易规则、订单接口。
- `tradingagents/crypto/scanner.py`：规则优先的机会扫描。
- `tradingagents/crypto/hotlist.py`：本地热度名单入口，未来 X、币安广场、论坛采集都写入这里。
- `tradingagents/crypto/lana_strategy.py`：参考 Lana X 动态后的热度、涨幅/波动、固定止损候选层，OI 是我们额外加入的可选增强过滤。
- `tradingagents/crypto/risk.py`：个人账户硬风控。
- `tradingagents/crypto/execution.py`：`analysis/paper/testnet/live` 执行路由。
- `tradingagents/crypto/agent_workflow.py`：TradingAgents 风格的 crypto 多角色 prompt。
- `tradingagents/crypto/llm_router.py`：默认 LLM 与 Hermes 的模型路由边界。

## 后续扩展顺序

1. 接入真实 Hermes endpoint 和模型选择。
2. 增加自动热度采集源，把 X、币安广场、论坛、新闻、链上热度转成 `LANA_HOT_SYMBOLS`。
3. 增加用户数据流和订单状态恢复。
4. 增加持仓状态机、止损止盈和急停熔断。
5. 增加回测和纸面交易绩效统计。
6. 再考虑把 crypto 多角色流程升级成真正的 LangGraph 图，而不是单次结构化评审 prompt。
