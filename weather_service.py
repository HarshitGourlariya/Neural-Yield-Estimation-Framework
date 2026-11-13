"""Location-aware annual weather defaults using Open-Meteo with NASA POWER fallback."""
from __future__ import annotations

import csv
import ipaddress
import os
import socket
import threading
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import requests

ROOT = Path(__file__).resolve().parent
LOCATION_FILE = ROOT / "data" / "india_locations.csv"
GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
NASA_URL = "https://power.larc.nasa.gov/api/temporal/daily/point"
TIMEOUT_SECONDS = 10
RETRIES = 2
CURRENT_TTL = 30 * 60
HISTORICAL_TTL = 24 * 60 * 60
VALID_RANGES = {"temperature": (-10.0, 55.0), "humidity": (0.0, 100.0), "rainfall": (0.0, 12000.0)}

_cache: dict[tuple, tuple[float, Any]] = {}
_cache_lock = threading.RLock()


def _create_weather_session() -> requests.Session:
    """Reuse HTTP connections, bypassing only a dead loopback proxy.

    Some desktop/sandbox launchers inject a localhost proxy that is not
    running. Requests otherwise retries that dead proxy for every provider,
    making a simple district selection appear stuck. A configured live proxy
    is left alone.
    """
    session = requests.Session()
    proxies = requests.utils.get_environ_proxies(ARCHIVE_URL)
    for proxy in set(proxies.values()):
        try:
            parsed = urlsplit(proxy if "://" in proxy else f"http://{proxy}")
            host, port = parsed.hostname, parsed.port
            is_loopback = host == "localhost" or (host and ipaddress.ip_address(host).is_loopback)
        except (ValueError, TypeError):
            continue
        if is_loopback and host and port:
            try:
                with socket.create_connection((host, port), timeout=0.25):
                    pass
            except OSError:
                session.trust_env = False
                break
    return session


_weather_session = _create_weather_session()


class WeatherServiceError(RuntimeError):
    """A recoverable, user-displayable lookup failure with its failing step."""

    def __init__(self, message: str, stage: str = "weather request"):
        super().__init__(message)
        self.stage = stage


def clear_cache() -> None:
    with _cache_lock:
        _cache.clear()


def bundled_locations_by_state() -> dict[str, list[str]]:
    locations: dict[str, set[str]] = {}
    if not LOCATION_FILE.exists():
        return {}
    with LOCATION_FILE.open(newline="", encoding="utf-8-sig") as stream:
        for row in csv.DictReader(stream):
            locations.setdefault(row["state"].strip(), set()).add(row["district"].strip())
    return {state: sorted(values) for state, values in locations.items()}


def _cached(key: tuple, ttl: int, loader):
    now = time.monotonic()
    with _cache_lock:
        value = _cache.get(key)
        if value and now - value[0] < ttl:
            return value[1]
    result = loader()
    with _cache_lock:
        _cache[key] = (time.monotonic(), result)
    return result


def _get_json(url: str, params: dict[str, Any], *, get=None, sleep=time.sleep) -> dict:
    get = get or _weather_session.get
    last_error = None
    for attempt in range(RETRIES + 1):
        try:
            response = get(url, params=params, timeout=TIMEOUT_SECONDS)
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict):
                raise WeatherServiceError("Parsing failed: the weather provider returned a non-object response.", "parsing")
            if payload.get("error"):
                raise WeatherServiceError(f"Weather request failed: {payload.get('reason') or payload['error']}", "weather request")
            return payload
        except (requests.RequestException, ValueError, WeatherServiceError) as exc:
            if isinstance(exc, WeatherServiceError):
                last_error = exc
            elif isinstance(exc, ValueError):
                last_error = WeatherServiceError(f"Parsing failed: invalid JSON response ({exc}).", "parsing")
            else:
                last_error = WeatherServiceError(f"Weather request failed: {exc}", "weather request")
            if attempt < RETRIES:
                sleep(0.2 * (attempt + 1))
    raise last_error


def _normalize(value: str) -> str:
    return " ".join(str(value).strip().casefold().replace("&", "and").split())


def _csv_coordinates(state: str, location: str) -> dict[str, float] | None:
    if not LOCATION_FILE.exists():
        return None
    target_state, target_location = _normalize(state), _normalize(location)
    with LOCATION_FILE.open(newline="", encoding="utf-8-sig") as stream:
        for row in csv.DictReader(stream):
            if _normalize(row["state"]) == target_state and _normalize(row["district"]) == target_location:
                return {"latitude": float(row["latitude"]), "longitude": float(row["longitude"]),
                        "name": row["district"], "admin1": row["state"], "country_code": "IN"}
    return None


def geocode_location(state: str, location: str, *, get=None, sleep=time.sleep) -> dict[str, float]:
    if not state.strip() or not location.strip():
        raise WeatherServiceError("Select a state and district or city first.", "location lookup")
    key = ("geo", _normalize(state), _normalize(location))

    # Some yield files retain the historic district label, while current
    # geocoders index this administrative district as Khargone.
    query_location = location
    if _normalize(state) == "madhya pradesh" and _normalize(location) in {
        "khargone west nimar", "khargone (west nimar)"
    }:
        query_location = "Khargone"

    def lookup():
        known = _csv_coordinates(state, query_location)
        if known:
            return known
        try:
            payload = _get_json(GEOCODING_URL, {"name": f"{query_location}, {state}", "count": 10,
                "language": "en", "format": "json"}, get=get, sleep=sleep)
        except WeatherServiceError as exc:
            raise WeatherServiceError(f"Location lookup failed for {location}, {state}: {exc}", "location lookup") from exc
        results = payload.get("results", [])
        if not isinstance(results, list):
            raise WeatherServiceError("Parsing failed: geocoding results were not a list.", "parsing")
        matches = [x for x in results if isinstance(x, dict) and x.get("country_code") == "IN"]
        if not matches:
            raise WeatherServiceError(f"Location lookup failed: no India result for {location}, {state}. Check the location or enter weather manually.", "location lookup")
        match = matches[0]
        try:
            return {"latitude": float(match["latitude"]), "longitude": float(match["longitude"]),
                    "name": match.get("name", location), "admin1": match.get("admin1", state), "country_code": "IN"}
        except (KeyError, TypeError, ValueError) as exc:
            raise WeatherServiceError(f"Parsing failed: geocoding coordinates are invalid ({exc}).", "parsing") from exc

    return _cached(key, HISTORICAL_TTL, lookup)


def _full_years(today: date) -> list[int]:
    return list(range(today.year - 5, today.year))


def _annual_from_daily(times: list[str], series: dict[str, list], years: list[int]) -> dict[str, float]:
    if not times:
        raise WeatherServiceError("Parsing failed: the archive returned no daily weather records.", "parsing")
    fields = {"temperature": "temperature_2m_mean", "humidity": "relative_humidity_2m_mean",
              "rainfall": "precipitation_sum"}
    grouped = {year: {name: [] for name in fields} for year in years}
    for index, stamp in enumerate(times):
        year = int(stamp[:4])
        if year not in grouped:
            continue
        for name, key in fields.items():
            values = series.get(key) or []
            if index < len(values) and values[index] is not None:
                grouped[year][name].append(float(values[index]))
    annual = {name: [] for name in fields}
    annual_by_year = {}
    for year, values in grouped.items():
        # Reject incomplete calendar years instead of silently treating partial
        # data as a normal year. Rainfall sums use daily precipitation totals.
        if len(values["temperature"]) < 300 or len(values["humidity"]) < 300 or len(values["rainfall"]) < 300:
            raise WeatherServiceError(f"Parsing failed: archive coverage is incomplete for {year}.", "parsing")
        annual_by_year[str(year)] = {
            "temperature": sum(values["temperature"]) / len(values["temperature"]),
            "humidity": sum(values["humidity"]) / len(values["humidity"]),
            "rainfall": sum(values["rainfall"]),
        }
        for name in fields:
            annual[name].append(annual_by_year[str(year)][name])
    return {"values": {key: sum(vals) / len(vals) for key, vals in annual.items()},
            "by_year": annual_by_year}


def _recent_30_day(daily: dict, today: date) -> dict | None:
    times = daily.get("time", [])
    values = {"temperature": daily.get("temperature_2m_mean") or [],
              "humidity": daily.get("relative_humidity_2m_mean") or [],
              "rainfall": daily.get("precipitation_sum") or []}
    end = today - timedelta(days=5)  # avoid the archive's usual near-real-time backfill window
    start = end - timedelta(days=29)
    samples = {key: [] for key in values}
    for i, stamp in enumerate(times):
        day = date.fromisoformat(stamp)
        if start <= day <= end:
            for key, series in values.items():
                if i < len(series) and series[i] is not None:
                    samples[key].append(float(series[i]))
    if min(map(len, samples.values())) < 20:
        return None
    return {"temperature": sum(samples["temperature"]) / len(samples["temperature"]),
            "humidity": sum(samples["humidity"]) / len(samples["humidity"]),
            "rainfall": sum(samples["rainfall"])}


def _open_meteo_history(coords: dict, years: list[int], today: date, *, get=None, sleep=time.sleep) -> dict:
    daily_end = today - timedelta(days=5)
    payload = _get_json(ARCHIVE_URL, {"latitude": coords["latitude"], "longitude": coords["longitude"],
        "start_date": f"{years[0]}-01-01", "end_date": daily_end.isoformat(),
        "daily": "temperature_2m_mean,relative_humidity_2m_mean,precipitation_sum", "timezone": "auto",
        "temperature_unit": "celsius", "precipitation_unit": "mm"}, get=get, sleep=sleep)
    daily = payload.get("daily", {})
    try:
        annual = _annual_from_daily(daily.get("time", []), daily, years)
        return {"annual": annual, "recent_30_day": _recent_30_day(daily, today)}
    except (KeyError, TypeError, ValueError) as exc:
        raise WeatherServiceError(f"Parsing failed for Open-Meteo daily weather: {exc}", "parsing") from exc


def _nasa_history(coords: dict, years: list[int], today: date, *, get=None, sleep=time.sleep) -> dict:
    daily_end = today - timedelta(days=5)
    payload = _get_json(NASA_URL, {"parameters": "T2M,RH2M,PRECTOTCORR", "community": "AG",
        "longitude": coords["longitude"], "latitude": coords["latitude"],
        "start": f"{years[0]}0101", "end": daily_end.strftime("%Y%m%d"), "format": "JSON",
        "time-standard": "LST"}, get=get, sleep=sleep)
    try:
        params = payload.get("properties", {}).get("parameter", {})
        if not all(key in params for key in ("T2M", "RH2M", "PRECTOTCORR")):
            raise WeatherServiceError("Parsing failed: NASA POWER did not return all required daily weather variables.", "parsing")
        days = sorted(set(params["T2M"]) & set(params["RH2M"]) & set(params["PRECTOTCORR"]))
        daily = {"temperature_2m_mean": [], "relative_humidity_2m_mean": [], "precipitation_sum": []}
        valid_days = []
        for day in days:
            row = [params[k].get(day) for k in ("T2M", "RH2M", "PRECTOTCORR")]
            if any(v is None or float(v) <= -900 for v in row):
                continue
            valid_days.append(f"{day[:4]}-{day[4:6]}-{day[6:]}")
            for key, value in zip(daily, row):
                daily[key].append(float(value))
        annual = _annual_from_daily(valid_days, daily, years)
        return {"annual": annual, "recent_30_day": _recent_30_day(daily | {"time": valid_days}, today)}
    except WeatherServiceError:
        raise
    except (KeyError, TypeError, ValueError) as exc:
        raise WeatherServiceError(f"Parsing failed for NASA POWER daily weather: {exc}", "parsing") from exc


def _current_weather(coords: dict, *, get=None, sleep=time.sleep) -> dict | None:
    payload = _get_json(FORECAST_URL, {"latitude": coords["latitude"], "longitude": coords["longitude"],
        "current": "temperature_2m,relative_humidity_2m", "temperature_unit": "celsius"}, get=get, sleep=sleep)
    try:
        current = payload.get("current") or {}
        if current.get("temperature_2m") is None or current.get("relative_humidity_2m") is None:
            return None
        return {"temperature": float(current["temperature_2m"]), "humidity": float(current["relative_humidity_2m"]),
                "time": str(current.get("time", ""))}
    except (AttributeError, TypeError, ValueError) as exc:
        raise WeatherServiceError(f"Parsing failed for current conditions: {exc}", "parsing") from exc


def validate_weather(values: dict[str, float]) -> dict[str, float]:
    result = {}
    try:
        for name, (low, high) in VALID_RANGES.items():
            value = float(values[name])
            if not low <= value <= high:
                raise WeatherServiceError(f"Parsing failed: provider {name} is outside the allowed range ({low:g} to {high:g}).", "parsing")
            result[name] = round(value, 1)
    except (KeyError, TypeError, ValueError) as exc:
        raise WeatherServiceError(f"Parsing failed: required weather value is missing or invalid: {exc}", "parsing") from exc
    return result


def get_weather(state: str, location: str, *, today: date | None = None, get=None,
                sleep=time.sleep, now: datetime | None = None) -> dict:
    """Return five-complete-year climate means plus best-effort current values.

    Historical defaults match the training table's district/year weather
    features. Current forecast values are supplementary and are not defaults.
    """
    today = today or date.today()
    years = _full_years(today)
    coords = geocode_location(state, location, get=get, sleep=sleep)
    historical_key = ("history", round(coords["latitude"], 3), round(coords["longitude"], 3), tuple(years))
    source = "Open-Meteo Archive"
    archive_error = None
    try:
        values = _cached(historical_key, HISTORICAL_TTL,
                         lambda: _open_meteo_history(coords, years, today, get=get, sleep=sleep))
        values["annual"]["values"] = validate_weather(values["annual"]["values"])
    except WeatherServiceError as open_error:
        archive_error = open_error
        source = "NASA POWER Daily"
        try:
            values = _cached(historical_key + ("nasa",), HISTORICAL_TTL,
                             lambda: _nasa_history(coords, years, today, get=get, sleep=sleep))
            values["annual"]["values"] = validate_weather(values["annual"]["values"])
        except WeatherServiceError as nasa_error:
            raise WeatherServiceError(f"Open-Meteo archive failed ({open_error}); NASA POWER fallback failed ({nasa_error}).") from nasa_error
    current_key = ("current", round(coords["latitude"], 3), round(coords["longitude"], 3))
    current = None
    try:
        current = _cached(current_key, CURRENT_TTL, lambda: _current_weather(coords, get=get, sleep=sleep))
    except WeatherServiceError:
        # Current data is informative only; annual defaults remain usable.
        pass
    fetched_at = (now or datetime.now().astimezone()).strftime("%Y-%m-%d %H:%M %Z").strip()
    fallback_note = f"Open-Meteo archive failed ({archive_error}); NASA POWER Daily fallback was used." if source.startswith("NASA") else None
    annual_by_year = values["annual"]["by_year"]
    return {"values": values["annual"]["values"], "rainfall_by_year": {
                year: round(item["rainfall"], 1) for year, item in annual_by_year.items()},
            "source": source, "period": f"{years[0]}–{years[-1]} annual average",
            "years": years, "fetched_at": fetched_at,
            "location": f"{coords.get('name', location)}, {state}", "coordinates": coords,
            "current": current, "recent_30_day": values.get("recent_30_day"),
            "fallback_note": fallback_note}
