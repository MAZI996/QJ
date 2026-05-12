# 在 TradingAgents 基础上扩展 crypto 自动交易

本项目不是另起一个交易机器人，而是在 `TauricResearch/TradingAgents` 的基础上增加 Hyperliquid 交易中心、Hermes 大模型路由和自动交易风控层。TradingAgents 是底层框架，不是一次性参考资料。

> 2026-05-12 更新：默认交易中心切换为 Hyperliquid。Binance 代码保留为旧适配器参考，不再作为主交易所路线。

## 上游关系

- `upstream`：`https://github.com/TauricResearch/TradingAgents.git`
- `origin`：`git@github.com:MAZI996/QJ.git`

后续升级时优先从 `upstream/main` 吸收原项目的 agent、LLM、CLI、结构化输出改进；我们的 Binance 扩展尽量放在 `tradingagents/crypto/` 和 `docs/`，减少和上游股票分析代码冲突。

## 底层框架约束

- 上游 TradingAgents 的 agent 角色、LangGraph 结构、LLM factory、结构化输出 helpers、CLI 风格、报告/记忆约定是底层能力。
- Binance、Hermes、Lana-inspired 策略、hotlist、执行器都是扩展层，默认放在 `tradingagents/crypto/`。
- 不把项目改造成绕开 TradingAgents 的独立机器人。
- crypto 多角色链路要尽量贴近原始链路：Analyst Team -> Bull/Bear Researchers -> Research Manager -> Trader -> Risk Analysts -> Portfolio Manager。
- LLM 只能评审、排序、解释信号；真实下单必须经过确定性 `RiskManager` 和 `ExecutionRouter`。

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
- `tradingagents/crypto/base_contract.py`：明确 TradingAgents 是底层框架，crypto 只是扩展层。
- `tradingagents/crypto/scanner.py`：规则优先的机会扫描。
- `tradingagents/crypto/attention.py`：把 X、币安广场、论坛、群聊文本解析成候选热币。
- `tradingagents/crypto/hotlist.py`：本地热度名单入口，未来 X、币安广场、论坛采集都写入这里。
- `tradingagents/crypto/lana_strategy.py`：参考 Lana X 动态后的热度、涨幅/波动、固定止损候选层，OI 是我们额外加入的可选增强过滤。
- `tradingagents/crypto/risk.py`：个人账户硬风控。
- `tradingagents/crypto/execution.py`：`analysis/paper/testnet/live` 执行路由。
- `tradingagents/crypto/agent_workflow.py`：TradingAgents 风格的 crypto 多角色 prompt。
- `tradingagents/crypto/llm_router.py`：默认 LLM 与 Hermes 的模型路由边界。

## 后续扩展顺序

1. 接入真实 Hermes endpoint 和模型选择。
2. 增加自动热度采集源，把 X、币安广场、论坛、新闻、链上热度文本喂给 `crypto-attention-ingest`。
3. 增加用户数据流和订单状态恢复。
4. 增加持仓状态机、止损止盈和急停熔断。
5. 增加回测和纸面交易绩效统计。
6. 把 crypto 多角色流程升级成真正的 LangGraph 图，复用 TradingAgents 的 graph/setup/conditional/propagation 思路，而不是长期停留在单次 prompt。
7. 定期从 `upstream/main` 合并上游 TradingAgents 的模型、agent 和结构化输出改进。
