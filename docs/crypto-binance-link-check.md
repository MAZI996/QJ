# Binance Account Link Check

Use this when connecting a personal Binance account. Do not paste API keys into
chat. Set them as environment variables on the machine that will run trading.

## Real Binance Account

```powershell
$env:BINANCE_API_KEY="your_api_key"
$env:BINANCE_API_SECRET="your_api_secret"
$env:TRADINGAGENTS_CRYPTO_BINANCE_TESTNET="false"
$env:TRADINGAGENTS_CRYPTO_ENABLE_LIVE_ORDERS="false"
python -m cli.main crypto-binance-check --real-binance --symbol BTCUSDT --quote-order-usdt 11
```

The check runs:

- public REST ping
- server time offset check
- symbol exchange rules
- signed account endpoint
- safe `POST /api/v3/order/test` market BUY using `quoteOrderQty`

`order/test` validates signing, timestamp, key permissions, and order filters,
but it does not create a real order.

## Read-Only Check

If the API key does not have spot trading permission yet:

```powershell
python -m cli.main crypto-binance-check --real-binance --symbol BTCUSDT --no-test-order
```

## Common Failures

- `-1021` timestamp error: fix machine clock sync or increase
  `TRADINGAGENTS_CRYPTO_BINANCE_RECV_WINDOW_MS`.
- `-2015` invalid API key/IP/permissions: check API key, secret, IP whitelist,
  and whether spot trading permission is enabled.
- `Filter failure`: increase `--quote-order-usdt` above the symbol minimum
  notional.
- Real account keys against testnet, or testnet keys against real Binance, will
  fail signed requests. Use `--real-binance` for real Binance.

## Safety Boundary

This command never calls `POST /api/v3/order`. Live orders still require
`TRADINGAGENTS_CRYPTO_ENABLE_LIVE_ORDERS=true`, live mode, and the explicit
live confirmation phrase.
