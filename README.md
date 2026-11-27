# Ribbon scanner: daily Telegram alerts for the CoinGecko top 500

Every day at 00:05 UTC this does four things:

1. Gets the top 500 coins by market cap from CoinGecko (stablecoins removed).
2. Downloads each coin's daily candles from a major exchange.
3. Works out the ribbon colour (gold, blue or grey) using the same 15/19/25/29 rules as your backtest.
4. Sends you a Telegram message listing every coin that turned **blue → grey** on the candle that just closed, and updates a web dashboard.

It runs free on GitHub. You don't need to install anything on your computer.

---

## Setup (about 15 minutes)

### 1. Make a Telegram bot
1. In Telegram, open a chat with **@BotFather** and send `/newbot`.
2. Pick a name and a username ending in `bot`.
3. BotFather replies with a **token** that looks like `123456789:AAH...`. Copy it.
4. Open a chat with your new bot and send it any message, e.g. `hi`. (A bot can't message you until you've messaged it.)
5. Get your **chat ID**: open this in your browser, replacing `<TOKEN>` with your token:
   `https://api.telegram.org/bot<TOKEN>/getUpdates`
   Find `"chat":{"id":` in the page. The number after it is your chat ID.

### 2. Get a free CoinGecko key (recommended)
Sign up at coingecko.com → Developer Dashboard → create a **Demo** API key. The scanner works without one, but keyless requests are heavily rate-limited.

### 3. Put the files on GitHub
1. Create a GitHub account if you don't have one.
2. Click **New repository**, name it e.g. `ribbon-scanner`, set it to **Public**, and create it.
   (Free GitHub Pages needs a public repo. This only makes the code and dashboard public; your Telegram token stays private in Secrets.)
3. Click **uploading an existing file** and drag in everything from this folder, **including the `.github` folder**.
   If your file browser hides `.github`, create the file in GitHub instead: **Add file → Create new file**, name it `.github/workflows/scan.yml`, and paste in the contents of `scan.yml`.
4. Commit.

### 4. Add your secrets
Repository → **Settings → Secrets and variables → Actions**.

**Secrets** tab → **New repository secret**, add:
| Name | Value |
|---|---|
| `TELEGRAM_BOT_TOKEN` | token from step 1 |
| `TELEGRAM_CHAT_ID` | chat ID from step 1 |
| `COINGECKO_API_KEY` | key from step 2 (optional) |

**Variables** tab (optional):
| Name | Value | What it does |
|---|---|---|
| `DASHBOARD_URL` | your Pages URL from step 5 | adds a dashboard link to each message |
| `ALERT_EVENTS` | e.g. `blue_to_grey,back_to_gold` | which events to send (default: `blue_to_grey`) |
| `TOP_N` | e.g. `200` | scan fewer coins |

Event names: `blue_to_grey`, `back_to_gold`, `gold_to_grey`, `back_to_blue`, `grey_to_gold`, `grey_to_blue`. The dashboard always shows all of them.

### 5. Turn on the dashboard and run it once
1. **Actions** tab → if asked, click **I understand my workflows, go ahead and enable them**.
2. Click **Daily ribbon scan → Run workflow**. It takes about 5–15 minutes. You should get a Telegram message at the end.
3. **Settings → Pages** → Source: **Deploy from a branch**, Branch: **main**, Folder: **/docs** → Save.
4. After a minute your dashboard is at `https://<your-username>.github.io/ribbon-scanner/`. Add that as the `DASHBOARD_URL` variable if you want the link in your messages.

From then on it runs by itself every day.

---

## Things to know

- **Timing:** GitHub's scheduler can start jobs late, sometimes by 15–60 minutes at busy times. The alert is still based on the closed daily candle.
- **Exchanges:** candles come from the first exchange that lists the coin, in this order: Binance, Coinbase, Kraken, OKX, Bybit, KuCoin, Gate, Bitget, MEXC. GitHub's servers are in the US, so Binance, OKX and Bybit may refuse the connection. The scanner skips any exchange it can't reach and uses the next one; the run log shows which ones loaded.
- **Coins that get skipped:** coins not listed on any of those exchanges, coins with less than 60 days of history, and coins where the exchange price is more than 25% off CoinGecko's (usually a different token with the same ticker). They're listed at the bottom of the dashboard.
- **Accuracy:** each coin uses its last 300 daily candles. On your BTC and ETH files this gave exactly the same colours as the full history for the last 30 days. Colours can still differ slightly from TradingView on a different exchange's feed.
- **Keeping it alive:** GitHub pauses scheduled workflows in public repos after 60 days without activity. If the messages stop, open the Actions tab and re-enable it.
- **Messages:** you get one digest a day, even when nothing flipped, so you know it ran. Set the repository variable `SEND_EMPTY` to `0` and add `SEND_EMPTY: ${{ vars.SEND_EMPTY }}` to the workflow's `env` if you only want messages when something flips.

The signals are untested on most altcoins and are not financial advice.
