"""Email alerting with cooldown-based dedup. SMTP credentials come from environment variables
only -- never hardcode them in config.yaml or in code.
"""
from __future__ import annotations

import logging
import os
import smtplib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.message import EmailMessage
from typing import Optional

from scoring import ScoreBreakdown
from storage import Storage

logger = logging.getLogger(__name__)


@dataclass
class AlertLinks:
    stocktwits: Optional[str] = None
    reddit: Optional[str] = None
    news: Optional[str] = None


@dataclass
class AlertContext:
    ticker: str
    breakdown: ScoreBreakdown
    price: Optional[float]
    links: AlertLinks = field(default_factory=AlertLinks)


def _build_message(ctx: AlertContext, paper_mode: bool) -> EmailMessage:
    b = ctx.breakdown
    price_str = f"${ctx.price:.2f}" if ctx.price is not None else "n/a"
    options_str = f"{b.options_score:.1f}" if b.options_available else "n/a (no options data)"

    lines = [
        f"Hype score: {b.total_score:.1f} / 100 (threshold crossed)",
        f"Ticker: {ctx.ticker}",
        f"Current price: {price_str}",
        "",
        "Signal breakdown:",
        f"  Social (mentions spike): {b.social_score:.1f}",
        f"  Price/volume:            {b.price_volume_score:.1f}",
        f"  Options:                 {options_str}",
        f"  News:                    {b.news_score:.1f}",
        "",
        "Sources:",
    ]
    if ctx.links.stocktwits:
        lines.append(f"  StockTwits: {ctx.links.stocktwits}")
    if ctx.links.reddit:
        lines.append(f"  Reddit:     {ctx.links.reddit}")
    if ctx.links.news:
        lines.append(f"  News:       {ctx.links.news}")

    lines += [
        "",
        "---",
        (
            "PAPER MODE: this is a logged/emailed signal only, not a trade or investment "
            "advice. Hype-driven moves are often already priced in by the time they're "
            "detectable this way."
            if paper_mode
            else "This is not investment advice."
        ),
    ]

    msg = EmailMessage()
    msg["Subject"] = f"[hypetrain-scanner] {ctx.ticker} crossed threshold — score {b.total_score:.0f}"
    msg["From"] = os.environ["ALERT_EMAIL_FROM"]
    msg["To"] = os.environ["ALERT_EMAIL_TO"]
    msg.set_content("\n".join(lines))
    return msg


def send_alert_email(ctx: AlertContext, paper_mode: bool) -> None:
    msg = _build_message(ctx, paper_mode)
    host = os.environ["SMTP_HOST"]
    port = int(os.environ.get("SMTP_PORT", "587"))
    username = os.environ["SMTP_USERNAME"]
    password = os.environ["SMTP_PASSWORD"]

    with smtplib.SMTP(host, port, timeout=15) as server:
        server.starttls()
        server.login(username, password)
        server.send_message(msg)


def maybe_alert(
    storage: Storage,
    signal_id: int,
    ctx: AlertContext,
    cooldown_hours: float,
    paper_mode: bool,
) -> bool:
    """Send + record an alert if the ticker isn't in its cooldown window. Returns True if sent."""
    if storage.is_in_cooldown(ctx.ticker, cooldown_hours):
        logger.info("Skipping alert for %s: still in cooldown window", ctx.ticker)
        return False

    try:
        send_alert_email(ctx, paper_mode)
    except Exception:
        logger.exception("Failed to send alert email for %s", ctx.ticker)
        return False

    now_iso = datetime.now(timezone.utc).isoformat()
    storage.mark_alerted(signal_id, ctx.ticker, ctx.breakdown.total_score, now_iso)
    logger.info("Alert sent for %s (score=%.1f)", ctx.ticker, ctx.breakdown.total_score)
    return True
