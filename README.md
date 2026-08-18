# hypetrain-scanner

Detects "hype-train" stocks — sudden retail-attention spikes (StockTwits + Reddit) confirmed by
price/volume, options, and news activity — logs every evaluated candidate to a local SQLite
database, and emails an alert when a ticker's combined hype score crosses a configurable
threshold. Includes a backtest mode to evaluate the scanner's own signals against what actually
happened afterward.

## This is not investment advice

- **`paper_mode` is on by default** and nothing in this project ever places a trade. Alerts are
  just a log entry + an email.
- By the time a move is loud enough on Reddit/StockTwits to trip these signals, it is very often
  **already priced in** — that's the whole phenomenon this tool is trying to detect, and it cuts
  both ways: the "confirming" price/volume move may already be over. Treat every alert as a
  research prompt, not a buy signal.
- Run `backtest` mode extensively against your own logged signals before you ever consider
  acting on an alert manually. Nothing here should inform real trading decisions without that
  step, and even then, this is not financial advice from a licensed advisor.

## Architecture

```
discovery/      candidate discovery via two independent paths, merged into one list:
                  1. mention-driven: StockTwits trending + Reddit mentions (via ApeWisdom), each
                     with a mentions_now/mentions_baseline ratio
                  2. catalyst-news-driven: a rotating scan of the whitelist for fresh headlines
                     matching config.yaml keywords (contract wins, FDA approvals, etc.) --
                     catches material news before social buzz exists, see discovery/catalyst.py
signals/        confirmation signals for the discovery shortlist only, never the whole market:
                price/volume/volatility (yfinance), options (yfinance, pluggable), news (Finnhub, pluggable)
scoring.py      combines the four signal types into one 0-100 score using config.yaml weights;
                a catalyst-news hit forces an alert regardless of the numeric score
alerting.py     emails an alert (smtplib) when score crosses threshold OR a catalyst news hit
                fires, deduped by cooldown window
storage.py      SQLite: every evaluated candidate (not just alerts), plus mention history + backtest results
backtest.py     pulls forward returns for logged signals and reports hit rate / avg return by signal type
main.py         CLI: `live` (polling loop) and `backtest`
```

## Setup

```bash
cd hypetrain-scanner
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Then fill in `.env`:

**Finnhub** (for the news signal) — register a free account at https://finnhub.io/register and
copy the API key from your dashboard into `FINNHUB_API_KEY`. Free tier is rate-limited; the
scanner caches calls per polling interval to stay within it, but if you add many candidates or
shorten the polling interval you may need to upgrade.

**SMTP** (for email alerts) — for Gmail, create an [App Password](https://myaccount.google.com/apppasswords)
(requires 2FA enabled) and use that as `SMTP_PASSWORD`, not your login password. Any SMTP
provider works; set `SMTP_HOST`/`SMTP_PORT` accordingly.

StockTwits' trending endpoint needs no auth/key.

Reddit mention data comes via [ApeWisdom](https://apewisdom.io/api), a free public API that
aggregates mention counts across the same investing subreddits (r/wallstreetbets, r/stocks,
r/options) -- no credentials needed. This sidesteps Reddit's own Data API, which now requires a
separate "Responsible Builder Policy" access request that isn't guaranteed to be approved even
for small personal/non-commercial tools. See `discovery/apewisdom.py` for details.

If Finnhub credentials are missing, the scanner logs a warning and runs with the news signal
disabled rather than failing — you can start with just StockTwits + Reddit + price/volume if you
want.

## Configuration

Everything tunable lives in `config.yaml`: subreddit list, excluded tickers, all scoring
weights and per-signal thresholds, alert threshold, cooldown window, polling interval, database
path. Comments in the file explain what each value controls. Secrets never go in this file.

`discovery.excluded_tickers` is a short seed list of common false-positive "words" (ARE, ON, IT,
etc.) that also happen to be valid ticker symbols — extend it as you notice more.

`data/tickers.csv` is a bundled, curated list of known ticker symbols used for regex-based
extraction (not NLP — a match must be an exact token against this list). It's a snapshot, not
the full market, and will go stale; refresh it periodically from an authoritative source (e.g.
[NASDAQ's symbol directory](https://www.nasdaqtrader.com/trader.aspx?id=symboldirdefs)) if you
want broader coverage.

## Running

**Live mode** — runs the discovery → signals → scoring → storage → alert loop every
`polling.interval_seconds`:

```bash
python main.py live
```

Run a single cycle and exit (useful for driving it from cron/systemd-timer instead of an
in-process loop):

```bash
python main.py live --once
```

Structured logs go to stdout and to `data/hypetrain.log` (configurable under `logging:` in
config.yaml).

**Backtest mode** — pulls forward price data (1d/3d/7d) for every logged signal and reports hit
rate, average return, and distribution, broken down by which signal type (social/price-volume/
options/news) dominated that signal's score:

```bash
# only signals that crossed the alert threshold (default)
python main.py backtest

# every evaluated candidate, not just alerts
python main.py backtest --all-signals

# filter by time / cap how many signals to pull forward data for
python main.py backtest --since 2026-07-01T00:00:00+00:00 --limit 200
```

Run this after every change to `scoring.weights` or the per-signal thresholds in config.yaml to
see how the change would have performed against your logged history — that's the point of
logging every candidate, not just alerts.

## Known limitations

- **yfinance is unofficial** and has no SLA; Yahoo can and does rate-limit or block aggressive
  callers. The scanner only ever calls it for the current discovery shortlist (never the whole
  market) and caches responses for `polling.interval_seconds`, but if you shorten the interval
  or raise `candidate_limit` significantly you're more exposed to this.
- **yfinance's options data is a free, delayed, thin snapshot** of end-of-day-ish volume/open
  interest — it is not real-time options flow, and many tickers have sparse or stale chains.
  The options sub-score is the lowest-confidence of the four for this reason. `signals/options.py`
  defines an `OptionsProvider` interface with a `PaidOptionsFlowProvider` stub so you can swap in
  a paid flow API (Unusual Whales, CBOE LiveVol, etc.) without touching scoring or main.py.
- StockTwits' public trending endpoint is unofficial-ish, undocumented, and can change shape or
  start requiring auth without notice.
- **ApeWisdom is an independent third party, not an official Reddit product** — it's a free,
  unauthenticated API with no SLA. It can change shape, rate-limit, or go offline without notice,
  same caveat as StockTwits above. If it ever stops working, `discovery/apewisdom.py` is the only
  file that needs to change; `discovery/candidates.py` just expects a `get_mention_counts()` call
  returning `(counts, links)`, so any replacement source can drop in the same way.
- **Catalyst-news discovery only ever sees tickers already in `data/tickers.csv`.** A ticker that
  has never been added to the whitelist is completely invisible to this scanner, no matter how
  big its news catalyst is — there is no free-tier API offering a broad "any ticker, any
  headline" firehose with ticker-tagging; that's specifically what paid news terminals sell.
  Catching a move like Xos's overnight Air Force contract announcement (before it shows up on a
  "top movers" page) only works if `XOS` was already on the whitelist beforehand. Keep adding
  small/micro-cap tickers you care about to `data/tickers.csv` — there's no automatic way around
  this ceiling.
- **Catalyst scanning is rate-limit-bounded, not real-time across the whole whitelist.** It scans
  a rotating batch (`discovery.catalyst_news.batch_size` in config.yaml, default 40) of the
  whitelist each poll cycle to stay within Finnhub's free-tier 60-calls/minute limit — full
  whitelist coverage takes several cycles, not one. A larger whitelist means longer average time
  to detect a catalyst on any given ticker; there's a real tradeoff between whitelist breadth and
  detection latency.
- **Keyword matching is a blunt instrument.** `discovery.catalyst_news.keywords` matches plain
  substrings against headline text (e.g. "contract", "fda approval") — it will miss differently
  worded catalysts and can false-positive on unrelated headlines that happen to contain a keyword.
  Tune the list in config.yaml as you see what does and doesn't fire.

## Tests

```bash
pytest tests/
```

Covers `scoring.compute_hype_score` (weight normalization, threshold boundary, missing-options
handling) and `discovery.tickers.extract_tickers` (the regex ticker-extraction, since a silent
regression there would quietly break discovery without any errors).
