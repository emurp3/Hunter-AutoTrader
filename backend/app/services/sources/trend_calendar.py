"""TrendCalendarAdapter detects upcoming calendar/cultural event windows."""
from __future__ import annotations
import json
import logging
from datetime import date, timedelta
from typing import Any

logger = logging.getLogger(__name__)

CALENDAR_EVENTS = [
    (2, 14, "Valentine's Day",    "calendar", "merchandise",     0.85, "low",    14),
    (3, 17, "St. Patrick's Day",  "cultural",  "merchandise",    0.70, "low",     7),
    (5,  0, "Mother's Day",       "calendar", "merchandise",     0.90, "low",    14),
    (5, 26, "Memorial Day",       "seasonal",  "merchandise",    0.72, "low",     7),
    (6, 19, "Juneteenth",         "cultural",  "merchandise",    0.88, "medium", 14),
    (6,  0, "Father's Day",       "calendar", "merchandise",     0.88, "low",    14),
    (7,  4, "Independence Day",   "calendar", "merchandise",     0.90, "low",    14),
    (10,31, "Halloween",          "calendar", "merchandise",     0.85, "low",    21),
    (11,28, "Black Friday",       "calendar", "bundle",          0.92, "low",     7),
    (12, 2, "Cyber Monday",       "calendar", "digital_product", 0.92, "low",     5),
    (12,25, "Christmas",          "calendar", "merchandise",     0.90, "low",    30),
    (12,26, "Kwanzaa",            "cultural",  "merchandise",    0.72, "medium",  7),
]

EVENT_META: dict[str, dict] = {
    "Juneteenth": {
        "desc": "Culturally resonant merchandise celebrating Juneteenth freedom.",
        "audience": "African-American community, allies, educators",
        "ideas": [{"product": "Juneteenth T-shirt", "vendor": "printful", "price": 28, "cogs": 12},
                  {"product": "Freedom Day mug", "vendor": "printful", "price": 18, "cogs": 8},
                  {"product": "Historical timeline digital print", "vendor": "gumroad", "price": 9, "cogs": 0}],
        "fulfillment": "print_on_demand", "vendor": "printful",
    },
    "Independence Day": {
        "desc": "Patriotic merchandise for July 4th.",
        "audience": "US patriotic consumers",
        "ideas": [{"product": "Stars & Stripes hoodie", "vendor": "printify", "price": 45, "cogs": 18},
                  {"product": "July 4th party pack digital", "vendor": "gumroad", "price": 12, "cogs": 0}],
        "fulfillment": "print_on_demand", "vendor": "printify",
    },
    "Black Friday": {
        "desc": "Bundle deals and limited-time digital offers.",
        "audience": "Bargain hunters, existing customers",
        "ideas": [{"product": "Digital bundle 5-pack", "vendor": "gumroad", "price": 29, "cogs": 0},
                  {"product": "Limited-run branded hoodie", "vendor": "printify", "price": 55, "cogs": 22}],
        "fulfillment": "digital", "vendor": "gumroad",
    },
}

_DEFAULT = {
    "desc": "Seasonal merchandise opportunity.", "audience": "General consumers",
    "ideas": [{"product": "Event t-shirt", "vendor": "printful", "price": 25, "cogs": 10}],
    "fulfillment": "print_on_demand", "vendor": "printful",
}

class TrendCalendarAdapter:
    def source_name(self) -> str: return "trend_calendar"

    def get_upcoming(self, look_ahead_days: int = 60) -> list[dict[str, Any]]:
        today = date.today()
        horizon = today + timedelta(days=look_ahead_days)
        results = []
        for month, day, name, ttype, opp_type, conf, effort, win in CALENDAR_EVENTS:
            ev = self._resolve(today.year, month, day, name)
            if ev and ev < today:
                ev = self._resolve(today.year + 1, month, day, name)
            if not ev or ev > horizon:
                continue
            days_away = (ev - today).days
            meta = EVENT_META.get(name, _DEFAULT)
            ideas = meta["ideas"]
            avg_price = sum(i["price"] for i in ideas) / len(ideas) if ideas else 25
            avg_cogs = sum(i.get("cogs", 0) for i in ideas) / len(ideas) if ideas else 10
            margin = round((avg_price - avg_cogs) / avg_price, 2) if avg_price else 0.5
            results.append({
                "trigger_type": ttype, "trigger_name": name, "trigger_date": ev,
                "window_open": today, "window_close": ev,
                "opportunity_type": opp_type,
                "title": f"{name} {today.year} — {opp_type.replace('_',' ').title()} Opportunity",
                "description": meta["desc"], "target_audience": meta["audience"],
                "product_ideas_json": json.dumps(ideas),
                "fulfillment_model": meta["fulfillment"], "vendor_name": meta["vendor"],
                "effort_level": effort, "confidence_score": conf,
                "days_to_launch": max(1, min(days_away - 3, 5)),
                "days_to_cash": max(3, days_away + 7),
                "price_point": avg_price, "cogs_estimate": avg_cogs,
                "estimated_margin_pct": margin, "estimated_units": 15,
                "estimated_revenue": round(avg_price * 15, 2),
            })
        return sorted(results, key=lambda x: x["trigger_date"])

    def _resolve(self, year: int, month: int, day: int, name: str):
        if day == 0:
            from calendar import monthcalendar
            try:
                cal = monthcalendar(year, month)
                sundays = [w[6] for w in cal if w[6] != 0]
                mondays = [w[0] for w in cal if w[0] != 0]
                if "Mother" in name: return date(year, month, sundays[1]) if len(sundays) >= 2 else None
                if "Father" in name: return date(year, month, sundays[2]) if len(sundays) >= 3 else None
                if "Labor" in name: return date(year, month, mondays[0]) if mondays else None
            except Exception: return None
        try: return date(year, month, day)
        except Exception: return None
