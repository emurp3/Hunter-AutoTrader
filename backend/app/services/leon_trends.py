"""
Leon Trends — Google Trends signal feed for predictive product creation.

Uses pytrends (Google Trends unofficial API) to monitor search volume for
key product-adjacent terms 30-60 days ahead of peaks.

Leon uses this to CREATE products BEFORE demand peaks, not after.
"""
from __future__ import annotations
import logging
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

# Terms Leon monitors for early signals
LEON_TREND_TOPICS = [
    # Heritage / culture
    "juneteenth shirt",
    "black excellence shirt",
    "juneteenth outfit",
    "melanin shirt",
    # Patriotic / America 250
    "america 250 shirt",
    "july 4th polo shirt",
    "independence day outfit",
    "patriotic polo",
    # Seasonal / gift
    "fathers day polo shirt",
    "fathers day gift black man",
    "black history month shirt",
    # Heritage brand
    "royal legacy shirt",
    "black king polo",
]

# Momentum threshold — if 7-day avg is X% above 90-day avg, flag as rising
RISING_THRESHOLD = 1.3   # 30% above baseline = rising trend
HOT_THRESHOLD    = 1.75  # 75% above baseline = hot trend


def fetch_trend_signals(topics: list[str] | None = None) -> list[dict[str, Any]]:
    """
    Fetch Google Trends interest data for Leon's product topics.
    Returns list of trending signals with momentum scores.
    """
    try:
        from pytrends.request import TrendReq
    except ImportError:
        logger.warning("Leon Trends: pytrends not installed. Run: pip install pytrends")
        return []

    topics = topics or LEON_TREND_TOPICS
    signals = []
    pytrends = TrendReq(hl="en-US", tz=360)

    # Process in batches of 5 (Google Trends limit per request)
    for i in range(0, len(topics), 5):
        batch = topics[i:i+5]
        try:
            # Get 90-day interest over time
            pytrends.build_payload(batch, timeframe="today 3-m", geo="US")
            df = pytrends.interest_over_time()
            if df.empty:
                continue

            for topic in batch:
                if topic not in df.columns:
                    continue
                series = df[topic].dropna()
                if len(series) < 14:
                    continue

                recent_7d = float(series.iloc[-7:].mean())
                baseline  = float(series.iloc[:-7].mean())
                if baseline < 1:
                    continue

                momentum = round(recent_7d / baseline, 3)
                current  = int(series.iloc[-1])

                if momentum >= RISING_THRESHOLD:
                    signals.append({
                        "topic": topic,
                        "current_interest": current,
                        "recent_7d_avg": round(recent_7d, 1),
                        "baseline_avg":   round(baseline, 1),
                        "momentum":  momentum,
                        "status": "HOT" if momentum >= HOT_THRESHOLD else "RISING",
                        "leon_action": f"Create product for '{topic}' NOW — demand rising {round((momentum-1)*100)}% above baseline",
                        "checked_at": datetime.now(timezone.utc).isoformat(),
                    })

        except Exception as exc:
            logger.warning("Leon Trends: batch failed for %s: %s", batch, exc)

    signals.sort(key=lambda x: x["momentum"], reverse=True)
    logger.info("Leon Trends: %d rising signals found", len(signals))
    return signals


def get_trend_report() -> dict:
    """Full trend report for the /store/trends endpoint."""
    signals = fetch_trend_signals()
    hot    = [s for s in signals if s["status"] == "HOT"]
    rising = [s for s in signals if s["status"] == "RISING"]
    return {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "hot_count":    len(hot),
        "rising_count": len(rising),
        "hot":    hot,
        "rising": rising,
        "all":    signals,
        "leon_note": (
            "HOT = 75%+ above 90-day baseline. Act immediately. "
            "RISING = 30%+ above baseline. Create products within 48h."
        ),
    }
