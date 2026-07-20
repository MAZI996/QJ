# Hyperliquid Real-Time Stream Archive

This layer uses the official `hyperliquid-python-sdk` WebSocket subscriptions
to archive real-time events for scanner freshness checks, paper evidence, and
future L2 replay.

It does not place orders.

## Default Public Stream

```powershell
python -m cli.main crypto-hyperliquid-stream --mainnet --symbols BTC,ETH,SOL,HYPE --seconds 60
```

Default subscriptions:

- `allMids`
- `l2Book` for each symbol
- `trades` for each symbol
- `candle` for each symbol and configured interval
- `activeAssetCtx` for each symbol

The archive is written to:

```text
TRADINGAGENTS_CRYPTO_STATE_DIR/events/hyperliquid-ws-YYYYMMDD.jsonl
```

Each JSONL row includes:

- received timestamp
- WebSocket channel
- normalized symbols
- a compact summary for quick analysis
- the original payload

## Continuous Service Loop

```powershell
python -m cli.main crypto-hyperliquid-stream --mainnet --symbols BTC,ETH,SOL,HYPE --seconds 0
```

Run this on the trading VPS when building live paper evidence. Use a process
manager such as systemd, Docker, or Windows Task Scheduler so it restarts after
machine reboots.

## Account Events

Account events are disabled by default. Enable them only on the trading machine
after setting the wallet address locally:

```powershell
$env:TRADINGAGENTS_CRYPTO_HYPERLIQUID_WALLET_ADDRESS="0xYourMainWallet"
python -m cli.main crypto-hyperliquid-stream --mainnet --symbols BTC,ETH --user-events --seconds 0
```

Additional account subscriptions:

- `userEvents`
- `userFills`
- `orderUpdates`
- `userFundings`
- `userNonFundingLedgerUpdates`

Do not paste wallet/private-key material into chat. This stream needs only the
public wallet address for account event subscriptions; signed order execution is
still handled by the separate SDK execution adapter and live-readiness gates.

## Next Integration

The stream archive should next feed:

- scanner data freshness checks
- paper-mode evidence windows
- position recovery comparisons
- L2/order-book replay for hftbacktest-inspired validation
