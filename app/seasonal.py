"""Seasonal/holiday triggers. A hardcoded ag/outdoor/GIS calendar is the load-bearing
source (works offline, deterministic, testable). active_event(date) returns the event
whose window contains the date, or None.

An optional Nager.Date fetch (free, no key) can enrich with public holidays later; it is
NOT relied upon here so a network/outage never affects the daily post.
"""
from __future__ import annotations

from datetime import date
from typing import Optional

# (start_month, start_day) .. (end_month, end_day) inclusive.
CALENDAR = [
    {"id": "new-year", "start": (1, 1), "end": (1, 3),
     "title": "Plan your land projects for the new year",
     "short": "A fresh year is the perfect time to map your fields and set your plans.",
     "benefit": "Start the year organized.", "keywords": ["new year", "land planning"],
     "points": [], "icon": "list-checks", "cta": "Start planning — link in bio"},
    {"id": "spring-planting", "start": (3, 15), "end": (4, 15),
     "title": "Planting season: map every field",
     "short": "Measure and group your fields before planting so nothing gets missed.",
     "benefit": "Plan planting with exact field sizes.", "keywords": ["planting season", "field measurement"],
     "points": [], "icon": "ruler", "cta": "Measure your fields"},
    {"id": "earth-day", "start": (4, 20), "end": (4, 23),
     "title": "Measure and care for your land this Earth Day",
     "short": "Know your land to manage it better. Map your plots and protect what matters.",
     "benefit": "Care for your land with accurate data.", "keywords": ["earth day", "land care"],
     "points": [], "icon": "globe", "cta": "Map your land — link in bio"},
    {"id": "world-environment-day", "start": (6, 4), "end": (6, 6),
     "title": "World Environment Day: know your ground",
     "short": "Accurate measurement helps you plan greener, smarter land use.",
     "benefit": "Plan sustainable land use.", "keywords": ["environment day", "land"],
     "points": [], "icon": "globe", "cta": "Try it free — link in bio"},
    {"id": "harvest", "start": (9, 20), "end": (10, 20),
     "title": "Harvest season: track every field",
     "short": "Keep field areas and notes organized through a busy harvest.",
     "benefit": "Stay organized at harvest time.", "keywords": ["harvest", "field tracking"],
     "points": [], "icon": "folder", "cta": "Organize your fields"},
    {"id": "gis-day", "start": (11, 17), "end": (11, 20),
     "title": "Happy GIS Day!",
     "short": "Celebrate maps and location tech — measure, mark, and navigate right from your phone.",
     "benefit": "Celebrate the power of maps.", "keywords": ["gis day", "mapping"],
     "points": [], "icon": "map-pin", "cta": "Explore the app — link in bio"},
]


def _within(d: date, start: tuple[int, int], end: tuple[int, int]) -> bool:
    md = (d.month, d.day)
    return start <= md <= end


def active_event(d: Optional[date] = None) -> Optional[dict]:
    d = d or date.today()
    for ev in CALENDAR:
        if _within(d, ev["start"], ev["end"]):
            return ev
    return None
