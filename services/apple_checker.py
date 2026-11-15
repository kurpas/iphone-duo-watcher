"""Queries Apple's in-store pickup availability endpoint (same one the product page uses)."""
from __future__ import annotations

import json
import math
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Dict, List, Tuple

PICKUP_URL = "https://www.apple.com/shop/retail/pickup-message"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"
    ),
    "Accept": "application/json",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.apple.com/shop/buy-iphone/iphone-duo",
}


@dataclass(frozen=True)
class Availability:
    store_number: str
    store_name: str
    city: str
    part: str
    model: str
    miles: float
    quote: str

    @property
    def key(self) -> Tuple[str, str]:
        return (self.store_number, self.part)


def miles_between(a_lat: float, a_lon: float, b_lat: float, b_lon: float) -> float:
    r = 3958.8
    p1, p2 = math.radians(a_lat), math.radians(b_lat)
    dp, dl = p2 - p1, math.radians(b_lon - a_lon)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


def fetch_stores(parts: List[str], zip_code: str, timeout: int = 20) -> List[dict]:
    params = [("pl", "true"), ("location", zip_code)]
    params += [(f"parts.{i}", p) for i, p in enumerate(parts)]
    url = f"{PICKUP_URL}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.load(resp)
    return data["body"]["stores"]


def check(cfg: dict) -> Tuple[List[Availability], int, List[str]]:
    """Returns (available items, number of stores checked, errors)."""
    parts: Dict[str, str] = cfg["parts"]
    origin = cfg["origin"]
    stores: Dict[str, dict] = {}
    errors: List[str] = []

    for zip_code in cfg["search_zips"]:
        try:
            for s in fetch_stores(list(parts), zip_code):
                stores[s["storeNumber"]] = s
        except Exception as exc:  # network error, block, format change
            errors.append(f"ZIP {zip_code}: {type(exc).__name__}: {exc}")

    found: List[Availability] = []
    checked = 0
    for s in stores.values():
        miles = miles_between(
            origin["lat"], origin["lon"], float(s["storelatitude"]), float(s["storelongitude"])
        )
        if miles > cfg["max_miles"]:
            continue
        checked += 1
        for part, info in s.get("partsAvailability", {}).items():
            if info.get("pickupDisplay") == "available":
                found.append(
                    Availability(
                        store_number=s["storeNumber"],
                        store_name=s["storeName"],
                        city=s.get("city", ""),
                        part=part,
                        model=parts.get(part, part),
                        miles=miles,
                        quote=info.get("pickupSearchQuote", ""),
                    )
                )
    found.sort(key=lambda a: a.miles)
    return found, checked, errors
