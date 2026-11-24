"""Address normalization shared by intake and the department services."""

from __future__ import annotations

import re
import unicodedata
from typing import Dict, Optional

US_STATES: Dict[str, str] = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR", "california": "CA",
    "colorado": "CO", "connecticut": "CT", "delaware": "DE", "district of columbia": "DC",
    "florida": "FL", "georgia": "GA", "hawaii": "HI", "idaho": "ID", "illinois": "IL",
    "indiana": "IN", "iowa": "IA", "kansas": "KS", "kentucky": "KY", "louisiana": "LA",
    "maine": "ME", "maryland": "MD", "massachusetts": "MA", "michigan": "MI", "minnesota": "MN",
    "mississippi": "MS", "missouri": "MO", "montana": "MT", "nebraska": "NE", "nevada": "NV",
    "new hampshire": "NH", "new jersey": "NJ", "new mexico": "NM", "new york": "NY",
    "north carolina": "NC", "north dakota": "ND", "ohio": "OH", "oklahoma": "OK", "oregon": "OR",
    "pennsylvania": "PA", "rhode island": "RI", "south carolina": "SC", "south dakota": "SD",
    "tennessee": "TN", "texas": "TX", "utah": "UT", "vermont": "VT", "virginia": "VA",
    "washington": "WA", "west virginia": "WV", "wisconsin": "WI", "wyoming": "WY",
}
US_STATE_CODES = frozenset(US_STATES.values())


def normalize_state(val: Optional[str]) -> Optional[str]:
    """Map a state name or code to its two-letter USPS code; None if unrecognized."""
    if not val:
        return None
    s = re.sub(r"\s+", " ", val.strip().rstrip(".")).lower()
    if s.upper() in US_STATE_CODES:
        return s.upper()
    return US_STATES.get(s)


def normalize_city(val: Optional[str]) -> Optional[str]:
    """Casefold a city name, strip accents and punctuation, and collapse whitespace.

    "San José", "san jose" and " SAN  JOSE. " all normalize to "san jose".
    """
    if not val:
        return None
    s = unicodedata.normalize("NFKD", val)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = re.sub(r"[^\w\s-]", " ", s.casefold())
    s = re.sub(r"[\s-]+", " ", s).strip()
    return s or None
