"""Mocked tests for Open-Meteo/NASA weather defaults (no network required)."""
from datetime import date, datetime, timedelta
import unittest
from unittest.mock import patch

import weather_service as weather


def daily_dates(first_year, last_year):
    current = date(first_year, 1, 1)
    last = date(last_year, 12, 31)
    days = []
    while current <= last:
        days.append(current.isoformat())
        current += timedelta(days=1)
    return days


class Response:
    def __init__(self, payload, status=200):
        self.payload, self.status = payload, status

    def raise_for_status(self):
        if self.status >= 400:
            import requests
            raise requests.HTTPError(f"HTTP {self.status}")

    def json(self):
        return self.payload


class WeatherServiceTests(unittest.TestCase):
    def setUp(self):
        weather.clear_cache()
        self.years = list(range(2021, 2026))
        self.days = daily_dates(2021, 2026)
        self.days = [d for d in self.days if d <= "2026-11-08"]

    def open_meteo_daily(self):
        return {"daily": {"time": self.days,
            "temperature_2m_mean": [20.0] * len(self.days),
            "relative_humidity_2m_mean": [60.0] * len(self.days),
            "precipitation_sum": [2.0] * len(self.days)}}

    def nasa_payload(self):
        days = [d.replace("-", "") for d in self.days]
        return {"properties": {"parameter": {
            "T2M": {d: 21.5 for d in days}, "RH2M": {d: 62.0 for d in days},
            "PRECTOTCORR": {d: 3.0 for d in days}}}}

    def test_historic_khargone_name_uses_current_geocoder_name(self):
        calls = []
        def get(url, params, timeout):
            calls.append(params)
            return Response({"results": [{"country_code": "IN", "name": "Khargone",
                "admin1": "Madhya Pradesh", "latitude": 21.82, "longitude": 75.61}]})
        point = weather.geocode_location("Madhya Pradesh", "Khargone West Nimar", get=get, sleep=lambda _: None)
        self.assertEqual((point["latitude"], point["longitude"]), (21.82, 75.61))
        self.assertEqual(calls[0]["name"], "Khargone, Madhya Pradesh")

    def test_csv_location_and_annual_averaging(self):
        calls = []
        def get(url, params, timeout):
            calls.append((url, params, timeout))
            if url == weather.ARCHIVE_URL:
                return Response(self.open_meteo_daily())
            if url == weather.FORECAST_URL:
                return Response({"current": {"temperature_2m": 28.2,
                    "relative_humidity_2m": 48, "time": "2026-11-13T10:00"}})
            raise AssertionError(f"Unexpected request {url}")
        result = weather.get_weather("Uttar Pradesh", "Greater Noida", today=date(2026, 11, 13),
            now=datetime(2026, 11, 13, 10), get=get, sleep=lambda _: None)
        self.assertEqual(result["values"], {"temperature": 20.0, "humidity": 60.0, "rainfall": 730.4})
        self.assertEqual(result["rainfall_by_year"], {"2021": 730.0, "2022": 730.0,
            "2023": 730.0, "2024": 732.0, "2025": 730.0})
        self.assertEqual(result["years"], self.years)
        self.assertEqual(result["source"], "Open-Meteo Archive")
        self.assertEqual(result["current"]["temperature"], 28.2)
        self.assertEqual(result["recent_30_day"], {"temperature": 20.0, "humidity": 60.0, "rainfall": 60.0})
        self.assertTrue(all(call[2] == weather.TIMEOUT_SECONDS for call in calls))
        archive_params = next(x[1] for x in calls if x[0] == weather.ARCHIVE_URL)
        self.assertEqual(archive_params["start_date"], "2021-01-01")
        self.assertEqual(archive_params["end_date"], "2026-11-08")

    def test_historical_failure_uses_nasa_power(self):
        def get(url, params, timeout):
            if url == weather.ARCHIVE_URL:
                return Response({}, status=503)
            if url == weather.NASA_URL:
                return Response(self.nasa_payload())
            if url == weather.FORECAST_URL:
                return Response({"current": {}})
            raise AssertionError(f"Unexpected request {url}")
        result = weather.get_weather("Uttar Pradesh", "Greater Noida", today=date(2026, 1, 2),
            get=get, sleep=lambda _: None)
        self.assertEqual(result["source"], "NASA POWER Daily")
        self.assertEqual(result["values"], {"temperature": 21.5, "humidity": 62.0, "rainfall": 1095.6})
        self.assertEqual(result["rainfall_by_year"]["2024"], 1098.0)
        self.assertIn("NASA POWER Daily fallback was used", result["fallback_note"])
        self.assertIsNone(result["current"])

    def test_weather_only_cherrapunji_location_is_bundled(self):
        coords = weather.geocode_location("Meghalaya", "Cherrapunji")
        self.assertAlmostEqual(coords["latitude"], 25.2847, places=3)
        self.assertAlmostEqual(coords["longitude"], 91.7211, places=3)

    def test_archive_and_current_results_are_cached(self):
        urls = []
        def get(url, params, timeout):
            urls.append(url)
            if url == weather.ARCHIVE_URL:
                return Response(self.open_meteo_daily())
            if url == weather.FORECAST_URL:
                return Response({"current": {"temperature_2m": 25, "relative_humidity_2m": 50}})
            raise AssertionError(f"Unexpected request {url}")
        for _ in range(2):
            weather.get_weather("Uttar Pradesh", "Greater Noida", today=date(2026, 11, 13),
                get=get, sleep=lambda _: None)
        self.assertEqual(urls.count(weather.ARCHIVE_URL), 1)
        self.assertEqual(urls.count(weather.FORECAST_URL), 1)

    def test_retry_and_timeout(self):
        calls = []
        def get(url, params, timeout):
            calls.append(timeout)
            if len(calls) < 3:
                return Response({}, status=503)
            return Response({"ok": True})
        self.assertEqual(weather._get_json("https://example.invalid", {}, get=get, sleep=lambda _: None), {"ok": True})
        self.assertEqual(calls, [10, 10, 10])

    def test_validates_online_ranges(self):
        self.assertEqual(weather.validate_weather({"temperature": -10, "humidity": 100, "rainfall": 12000}),
            {"temperature": -10.0, "humidity": 100.0, "rainfall": 12000.0})
        with self.assertRaises(weather.WeatherServiceError):
            weather.validate_weather({"temperature": 55.1, "humidity": 50, "rainfall": 500})

    def test_unreachable_loopback_proxy_is_bypassed(self):
        with patch.dict("os.environ", {"HTTPS_PROXY": "http://127.0.0.1:9"}, clear=True), \
             patch("weather_service.socket.create_connection", side_effect=OSError("refused")):
            session = weather._create_weather_session()
        self.assertFalse(session.trust_env)

    def test_working_loopback_proxy_is_preserved(self):
        with patch.dict("os.environ", {"HTTPS_PROXY": "http://127.0.0.1:8080"}, clear=True), \
             patch("weather_service.socket.create_connection"):
            session = weather._create_weather_session()
        self.assertTrue(session.trust_env)


if __name__ == "__main__":
    unittest.main()
