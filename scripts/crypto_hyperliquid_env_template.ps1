param(
    [switch]$Live,
    [string]$StateDir = "$env:USERPROFILE\.tradingagents\crypto",
    [string]$EmergencyStopFile = "C:\tradingagents-stop.txt"
)

$ErrorActionPreference = "Stop"

$env:TRADINGAGENTS_CRYPTO_EXCHANGE_PROVIDER = "hyperliquid"
$env:TRADINGAGENTS_CRYPTO_HYPERLIQUID_TESTNET = if ($Live) { "false" } else { "true" }
$env:TRADINGAGENTS_CRYPTO_HYPERLIQUID_MAX_LEVERAGE = "1"
$env:TRADINGAGENTS_CRYPTO_HYPERLIQUID_SDK_EXECUTION_ENABLED = if ($Live) { "true" } else { "false" }
$env:TRADINGAGENTS_CRYPTO_HYPERLIQUID_MARKET_SLIPPAGE = "0.01"
$env:TRADINGAGENTS_CRYPTO_HYPERLIQUID_REQUIRE_PROTECTIVE_ORDERS = "true"
$env:TRADINGAGENTS_CRYPTO_PROTECTIVE_OCO_ENABLED = if ($Live) { "true" } else { "false" }
$env:TRADINGAGENTS_CRYPTO_ENABLE_LIVE_ORDERS = if ($Live) { "true" } else { "false" }
$env:TRADINGAGENTS_CRYPTO_LIVE_CONFIRM_PHRASE = "I_UNDERSTAND_THIS_PLACES_REAL_HYPERLIQUID_ORDERS"
$env:TRADINGAGENTS_CRYPTO_STATE_DIR = $StateDir
$env:TRADINGAGENTS_CRYPTO_EMERGENCY_STOP_FILE = $EmergencyStopFile
$env:TRADINGAGENTS_CRYPTO_POSITION_GUARDIAN_ENABLED = "true"
$env:TRADINGAGENTS_CRYPTO_POSITION_GUARDIAN_CLOSE_ON_STOP = "true"
$env:TRADINGAGENTS_CRYPTO_POSITION_GUARDIAN_CLOSE_ON_TAKE_PROFIT = "true"
$env:TRADINGAGENTS_CRYPTO_POSITION_GUARDIAN_SKIP_ENTRIES_AFTER_CLOSE = "true"

Write-Host "Hyperliquid crypto env template loaded for this PowerShell session."
Write-Host "Live mode: $Live"
Write-Host "State dir: $StateDir"
Write-Host "Emergency stop file: $EmergencyStopFile"
Write-Host ""
Write-Host "Set these manually on the trading machine only; never paste them into chat:"
Write-Host '$env:TRADINGAGENTS_CRYPTO_HYPERLIQUID_WALLET_ADDRESS="0xYourMainWallet"'
Write-Host '$env:TRADINGAGENTS_CRYPTO_HYPERLIQUID_API_WALLET_ADDRESS="0xYourApiWallet"'
Write-Host '$env:TRADINGAGENTS_CRYPTO_HYPERLIQUID_PRIVATE_KEY="0xYourApiWalletPrivateKey"'
