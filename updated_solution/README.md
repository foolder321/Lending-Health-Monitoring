# Aave V3 Arbitrum Lending Monitor

This repository contains a production‑minded MVP of a Telegram bot that
monitors lending positions on **Aave V3** on the **Arbitrum** network. It
periodically checks a list of wallet addresses, computes risk metrics
such as health factor, total collateral, total debt and loan‑to‑value
ratio, and sends alert notifications to Telegram when positions enter
dangerous territory.

## Features

* Support for multiple wallet addresses
* Fetches per‑asset positions and prices via the [Expand.network](https://expand.network)
  API (requires an API key)
* Retrieves accurate health factors directly from the on‑chain Aave
  contract via Web3
* Calculates loan‑to‑value (LTV) and collateral ratios
* Configurable risk thresholds (warning, critical, liquidation)
* Deduplication logic prevents spamming the user with identical
  alerts – repeat alerts can be throttled via configuration
* Alerts are delivered to Telegram using the Bot API
* Persists alert history in a SQLite database (easily swapped for
  PostgreSQL)
* Clean, extensible architecture with an adapter pattern for adding
  new protocols or networks
* Docker‑ready for simplified deployment

## Getting Started

### Prerequisites

* Python 3.11 or higher
* A Telegram bot token and chat ID. Create a bot via
  [@BotFather](https://core.telegram.org/bots#6-botfather) and start
  a chat with it to obtain the chat ID.
* An API key from [Expand.network](https://expand.network) to access
  per‑asset position data.
* Optionally, an RPC endpoint for Arbitrum to fetch health factors
  directly from the blockchain (e.g. via Infura, Alchemy or QuickNode).

### Configuration

Copy the provided `.env.example` to `.env` and fill in the required
values:

```sh
cp .env.example .env
```

Edit `.env` to specify your Telegram credentials, the list of wallet
addresses to monitor, your Expand.network API key, database URL and
other options. At minimum you must set:

* `TELEGRAM_BOT_TOKEN` – your bot token
* `TELEGRAM_CHAT_ID` – the chat (user or group) where alerts should be sent
* `ADDRESSES` – comma‑separated list of wallet addresses on Arbitrum
* `EXPAND_NETWORK_API_KEY` – your Expand.network API key

Optionally set `WEB3_PROVIDER_URI` to enable on‑chain health factor
retrieval. If omitted the bot will fall back to off‑chain data and
health factors will not be available.

### Running Locally

Install dependencies and start the bot:

```sh
python -m venv .venv
source .venv/bin/activate
pip install -r project/requirements.txt
python project/app/main.py
```

The bot will create a SQLite database at `project/data/alerts.db` on
first run. You should see log output indicating that polling has
started. Alerts will be sent to the configured Telegram chat when
risk thresholds are breached.

### Running with Docker

Build the Docker image and run the container:

```sh
docker build -t aave-monitor project
docker run --env-file=project/.env -v $(pwd)/project/data:/app/data aave-monitor
```

This will start the monitor inside a container. The database is
persisted by mounting the host `project/data` directory into the
container.

## Adding Another Protocol or Network

The architecture follows an adapter pattern. To support additional
lending protocols (e.g. Compound, Morpho) or deploy the bot on a
different chain, implement a new adapter class under `app/adapters/`
that conforms to `LendingProtocolAdapter`. The adapter is responsible
for fetching per‑asset positions, computing aggregated values and (if
necessary) obtaining on‑chain risk metrics. You can then register
addresses to monitor in your `.env` and update the instantiation in
`main.py`.

## Limitations and Future Improvements

* The per‑asset USD values calculated by the current adapter assume
  18‑decimal tokens for simplicity. Tokens with differing decimals
  (e.g. USDC with 6 decimals) will produce inaccurate amounts. A
  production implementation should derive token decimals either from
  on‑chain metadata or the Expand.network API and adjust conversions
  accordingly.
* Health factors are fetched on‑chain if a Web3 provider is
  configured. Without an RPC endpoint the bot cannot compute health
  factors and will treat positions with debt as healthy.
* The Expand.network API key is required for per‑asset positions. If
  the API is unavailable the bot will still report aggregated health
  but cannot break down supplies and borrows by token.
* The scheduling is implemented using APScheduler. For large numbers
  of addresses or very frequent polling, consider running tasks in
  parallel with proper rate limiting and backoff.

## License

This project is provided for educational purposes and comes with no
warranty. Use at your own risk. Aave and Expand.network are
independent third‑party services and subject to their own terms.