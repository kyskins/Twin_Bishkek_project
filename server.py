"""Local HTTP API and frontend host for the Twin Bishkek prototype."""

from __future__ import annotations

import json
import logging
import math
import os
import sqlite3
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from simulator import AREA_PROFILES, TwinBishkekSimulator

ROOT = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("TWINGIS_DATA_DIR", str(ROOT / "data"))).expanduser().resolve()
DATABASE_PATH = DATA_DIR / "twin_bishkek.sqlite3"
SIMULATOR = TwinBishkekSimulator()
ENVIRONMENT_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
ENVIRONMENT_CACHE_LOCK = threading.Lock()
ENVIRONMENT_CACHE_SECONDS = 900
DATABASE_LOCK = threading.Lock()
ENVIRONMENT_COLLECTION_INTERVAL_SECONDS = 900
ENVIRONMENT_COLLECTOR_LOCK = threading.Lock()
ENVIRONMENT_COLLECTOR_THREAD: threading.Thread | None = None
LOGGER = logging.getLogger("twin_bishkek.collector")
BISHKEK_TZ = ZoneInfo("Asia/Bishkek")


@contextmanager
def _database():
    connection = sqlite3.connect(DATABASE_PATH, timeout=10)
    connection.row_factory = sqlite3.Row
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def _initialize_database() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with DATABASE_LOCK, _database() as connection:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("""
            CREATE TABLE IF NOT EXISTS traffic_observations (
                id INTEGER PRIMARY KEY,
                observed_at TEXT NOT NULL,
                area TEXT NOT NULL,
                day_type TEXT NOT NULL,
                hour INTEGER NOT NULL,
                score REAL NOT NULL,
                center_lon REAL NOT NULL,
                center_lat REAL NOT NULL,
                zoom REAL NOT NULL,
                view_key TEXT NOT NULL,
                source TEXT NOT NULL DEFAULT '2GIS MapGL traffic score'
            )
        """)
        connection.execute("CREATE INDEX IF NOT EXISTS traffic_area_time_idx ON traffic_observations(area, observed_at)")
        connection.execute("CREATE INDEX IF NOT EXISTS traffic_view_time_idx ON traffic_observations(view_key, observed_at)")
        connection.execute("""
            CREATE TABLE IF NOT EXISTS environment_snapshots (
                id INTEGER PRIMARY KEY,
                captured_at TEXT NOT NULL,
                area TEXT NOT NULL,
                air_time TEXT,
                weather_time TEXT,
                payload_json TEXT NOT NULL,
                UNIQUE(area, air_time, weather_time)
            )
        """)


def _now_bishkek() -> datetime:
    return datetime.now(BISHKEK_TZ)


def _save_environment_snapshot(area: str, payload: dict[str, Any]) -> None:
    air_time = str(payload.get("updated_at") or "")
    weather_time = str((payload.get("weather") or {}).get("time") or "")
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    with DATABASE_LOCK, _database() as connection:
        connection.execute(
            "INSERT OR IGNORE INTO environment_snapshots(captured_at, area, air_time, weather_time, payload_json) VALUES(?, ?, ?, ?, ?)",
            (_now_bishkek().isoformat(timespec="seconds"), area, air_time, weather_time, encoded),
        )


def _traffic_history(area: str, hour: int, day_type: str) -> dict[str, Any]:
    cutoff = (_now_bishkek() - timedelta(days=90)).isoformat(timespec="seconds")
    with DATABASE_LOCK, _database() as connection:
        rows = connection.execute(
            "SELECT observed_at, hour, score FROM traffic_observations WHERE area = ? AND day_type = ? AND observed_at >= ?",
            (area, day_type, cutoff),
        ).fetchall()
    selected: list[tuple[sqlite3.Row, float]] = []
    for row in rows:
        hour_distance = abs(int(row["hour"]) - hour)
        hour_distance = min(hour_distance, 24 - hour_distance)
        if hour_distance <= 1:
            selected.append((row, 1.0 if hour_distance == 0 else 0.55))
    weighted_count = sum(weight for _, weight in selected)
    score_average = (
        sum(float(row["score"]) * weight for row, weight in selected) / weighted_count
        if weighted_count
        else None
    )
    distinct_days = len({str(row["observed_at"])[:10] for row, _ in selected})
    confidence = min(1.0, distinct_days / 14) * min(1.0, len(selected) / 28)
    usable = len(selected) >= 8 and distinct_days >= 4
    return {
        "source": "2GIS MapGL · observations collected by this app",
        "area": area,
        "hour": hour,
        "day_type": day_type,
        "score_average": round(score_average, 2) if score_average is not None else None,
        "sample_count": len(selected),
        "distinct_days": distinct_days,
        "confidence": round(confidence, 2),
        "usable_for_calibration": usable,
        "period_days": 90,
        "note": "Это индекс видимой области карты, а не замер каждого участка улицы.",
    }


def _data_status() -> dict[str, Any]:
    with DATABASE_LOCK, _database() as connection:
        traffic = connection.execute("SELECT COUNT(*) AS n, MAX(observed_at) AS last FROM traffic_observations").fetchone()
        environment = connection.execute("SELECT COUNT(*) AS n, MAX(captured_at) AS last FROM environment_snapshots").fetchone()
    return {
        "traffic_observations": int(traffic["n"]),
        "traffic_last_observed_at": traffic["last"],
        "environment_snapshots": int(environment["n"]),
        "environment_last_captured_at": environment["last"],
        "traffic_source": "2GIS MapGL traffic score",
        "environment_source": "Open-Meteo · CAMS Global + weather forecast",
        "storage": "local SQLite database",
    }


_initialize_database()


def _open_meteo_json(url: str) -> dict[str, Any]:
    request = Request(url, headers={"User-Agent": "TwinBishkekPrototype/1.0"})
    with urlopen(request, timeout=10) as response:
        return json.loads(response.read().decode("utf-8"))


def get_area_environment(area_name: str, *, force_refresh: bool = False) -> dict[str, Any]:
    area = AREA_PROFILES.get(area_name, AREA_PROFILES["Джал"])
    now = time.monotonic()
    with ENVIRONMENT_CACHE_LOCK:
        cached = ENVIRONMENT_CACHE.get(area.name)
        if cached and now - cached[0] < ENVIRONMENT_CACHE_SECONDS and not force_refresh:
            result = dict(cached[1])
            result["cached"] = True
            return result

    coordinates = {"latitude": area.lat, "longitude": area.lon, "timezone": "Asia/Bishkek"}
    air_params = {
        **coordinates,
        "domains": "cams_global",
        "current": "pm2_5,pm10,nitrogen_dioxide,ozone,carbon_monoxide,sulphur_dioxide,european_aqi,european_aqi_pm2_5",
        "hourly": "pm2_5,pm10,european_aqi",
        "forecast_hours": 24,
    }
    weather_params = {
        **coordinates,
        "current": "temperature_2m,relative_humidity_2m,wind_speed_10m,wind_direction_10m",
        "hourly": "temperature_2m,relative_humidity_2m,wind_speed_10m,wind_direction_10m",
        "forecast_hours": 24,
    }
    air_url = "https://air-quality-api.open-meteo.com/v1/air-quality?" + urlencode(air_params)
    weather_url = "https://api.open-meteo.com/v1/forecast?" + urlencode(weather_params)
    air_response = _open_meteo_json(air_url)
    weather_response = _open_meteo_json(weather_url)
    air_current = air_response.get("current", {})
    air_hourly = air_response.get("hourly", {})
    forecast_times = air_hourly.get("time", [])
    first_forecast_index = next(
        (index for index, forecast_time in enumerate(forecast_times) if forecast_time >= air_current.get("time", "")),
        0,
    )
    forecast_24h = []
    for index in range(first_forecast_index, min(len(forecast_times), first_forecast_index + 9)):
        point = {"time": forecast_times[index]}
        for field in ("pm2_5", "pm10", "european_aqi"):
            values = air_hourly.get(field) or []
            point[field] = values[index] if index < len(values) else None
        forecast_24h.append(point)
    weather_current = weather_response.get("current", {})
    weather_hourly = weather_response.get("hourly", {})
    weather_times = weather_hourly.get("time", [])
    first_weather_index = next(
        (index for index, forecast_time in enumerate(weather_times) if forecast_time >= weather_current.get("time", "")),
        0,
    )
    weather_forecast_24h = []
    for index in range(first_weather_index, min(len(weather_times), first_weather_index + 25)):
        point = {"time": weather_times[index]}
        for field in ("temperature_2m", "relative_humidity_2m", "wind_speed_10m", "wind_direction_10m"):
            values = weather_hourly.get(field) or []
            point[field] = values[index] if index < len(values) else None
        weather_forecast_24h.append(point)
    result: dict[str, Any] = {
        "available": True,
        "area": area.name,
        "source": "Open-Meteo · CAMS Global",
        "air": air_current,
        "air_forecast_24h": forecast_24h,
        "weather": weather_current,
        "weather_forecast_24h": weather_forecast_24h,
        "grid": {
            "latitude": air_response.get("latitude"),
            "longitude": air_response.get("longitude"),
            "elevation": air_response.get("elevation"),
        },
        "updated_at": air_current.get("time"),
        "resolution_km": 45,
        "resolution_note": "Глобальная ячейка CAMS около 45 км; это модельный фон, а не датчик на улице.",
        "cached": False,
    }
    with ENVIRONMENT_CACHE_LOCK:
        ENVIRONMENT_CACHE[area.name] = (time.monotonic(), result)
    _save_environment_snapshot(area.name, result)
    return result


def _collect_environment_forever() -> None:
    """Keep a local 15-minute history of public air-quality and weather data."""
    while True:
        for area_name in AREA_PROFILES:
            try:
                get_area_environment(area_name, force_refresh=True)
            except Exception:
                # One provider outage must not stop collection for later areas.
                LOGGER.warning("Could not collect environmental snapshot for %s", area_name, exc_info=True)
        time.sleep(ENVIRONMENT_COLLECTION_INTERVAL_SECONDS)


def _start_environment_collector() -> None:
    global ENVIRONMENT_COLLECTOR_THREAD
    with ENVIRONMENT_COLLECTOR_LOCK:
        if ENVIRONMENT_COLLECTOR_THREAD and ENVIRONMENT_COLLECTOR_THREAD.is_alive():
            return
        ENVIRONMENT_COLLECTOR_THREAD = threading.Thread(
            target=_collect_environment_forever,
            name="twin-bishkek-environment-collector",
            daemon=True,
        )
        ENVIRONMENT_COLLECTOR_THREAD.start()


class TwinBishkekHandler(BaseHTTPRequestHandler):
    server_version = "TwinBishkek/1.0"

    def _headers(self, status: int, content_type: str = "application/json; charset=utf-8") -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Private-Network", "true")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

    def do_OPTIONS(self) -> None:  # noqa: N802
        self._headers(204)

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path == "/api/health":
            body = {
                "ok": True,
                "model": SIMULATOR.model_name,
                "version": SIMULATOR.model_version,
                "calibrated": False,
            }
            self._json(200, body)
            return
        if path == "/api/config":
            # MapGL keys are client-side API keys; keep the value out of source
            # control and restrict its allowed origins in the 2GIS dashboard.
            self._json(200, {"mapgl_key": os.environ.get("TWINGIS_MAPGL_KEY", "").strip()})
            return
        if path == "/api/environment":
            query = parse_qs(urlparse(self.path).query)
            area_name = query.get("area", ["Джал"])[0]
            try:
                self._json(200, get_area_environment(area_name))
            except Exception:
                self._json(200, {
                    "available": False,
                    "area": area_name,
                    "source": "Open-Meteo · CAMS Global",
                    "reason": "Не удалось получить данные о воздухе и погоде.",
                })
            return
        if path == "/api/data-status":
            self._json(200, _data_status())
            return
        if path == "/api/traffic-history":
            query = parse_qs(urlparse(self.path).query)
            area_name = query.get("area", ["Джал"])[0]
            if area_name not in AREA_PROFILES:
                self._json(400, {"error": "Неизвестный район"})
                return
            try:
                hour = max(0, min(23, int(query.get("hour", ["8"])[0])))
            except ValueError:
                self._json(400, {"error": "hour должен быть целым числом 0–23"})
                return
            day_type = query.get("day_type", ["weekday"])[0]
            if day_type not in ("weekday", "weekend"):
                self._json(400, {"error": "day_type должен быть weekday или weekend"})
                return
            self._json(200, _traffic_history(area_name, hour, day_type))
            return
        if path in ("/", "/index.html", "/frontend/index.html"):
            page = ROOT / "frontend" / "index.html"
            self._headers(200, "text/html; charset=utf-8")
            self.wfile.write(page.read_bytes())
            return
        self._json(404, {"error": "Not found"})

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path not in ("/api/simulate", "/api/traffic-observation"):
            self._json(404, {"error": "Not found"})
            return
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if size <= 0 or size > 64_000:
                raise ValueError("Request body must be between 1 and 64000 bytes")
            params: Any = json.loads(self.rfile.read(size).decode("utf-8"))
            if not isinstance(params, dict):
                raise ValueError("Expected a JSON object")
            if path == "/api/traffic-observation":
                self._record_traffic_observation(params)
                return
            result = SIMULATOR.simulate(params)
            result["traffic_history"] = _traffic_history(
                result["area"], int(float(result["selected_time"]["hour"])), result["selected_time"]["day_type"]
            )
            self._json(200, result)
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            self._json(400, {"error": str(exc)})

    def _record_traffic_observation(self, params: dict[str, Any]) -> None:
        area = str(params.get("area", ""))
        if area not in AREA_PROFILES:
            self._json(400, {"error": "Неизвестный район"})
            return
        try:
            score = float(params.get("score"))
            center_lon = float(params.get("center_lon"))
            center_lat = float(params.get("center_lat"))
            zoom = float(params.get("zoom"))
        except (TypeError, ValueError):
            self._json(400, {"error": "Нужны числовые score, координаты карты и zoom"})
            return
        if not (0 <= score <= 10 and 74.46 <= center_lon <= 74.78 and 42.74 <= center_lat <= 42.94 and 1 <= zoom <= 22):
            self._json(400, {"error": "Значения замера находятся вне допустимого диапазона"})
            return
        area_profile = AREA_PROFILES[area]
        center_distance_m = math.hypot(
            (center_lon - area_profile.lon) * 111_320 * math.cos(area_profile.lat * math.pi / 180),
            (center_lat - area_profile.lat) * 111_320,
        )
        if center_distance_m > 4_000:
            self._json(400, {"error": "Замер не относится к выбранному району карты"})
            return
        now = _now_bishkek()
        day_type = "weekend" if now.weekday() >= 5 else "weekday"
        view_key = f"{area}:{center_lon:.2f}:{center_lat:.2f}:{round(zoom)}"
        cutoff = (now - timedelta(minutes=15)).isoformat(timespec="seconds")
        with DATABASE_LOCK, _database() as connection:
            previous = connection.execute(
                "SELECT id FROM traffic_observations WHERE view_key = ? AND observed_at >= ? LIMIT 1",
                (view_key, cutoff),
            ).fetchone()
            if previous is None:
                connection.execute(
                    "INSERT INTO traffic_observations(observed_at, area, day_type, hour, score, center_lon, center_lat, zoom, view_key) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (now.isoformat(timespec="seconds"), area, day_type, now.hour, score, round(center_lon, 3), round(center_lat, 3), round(zoom, 1), view_key),
                )
                created = True
            else:
                created = False
        status = _data_status()
        self._json(200, {"stored": created, "throttled": not created, "status": status})

    def _json(self, status: int, value: dict[str, Any]) -> None:
        payload = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self._headers(status)
        self.wfile.write(payload)

    def log_message(self, fmt: str, *args: Any) -> None:
        # Keep the local prototype's console quiet during slider interaction.
        return


def create_server(host: str | None = None, port: int | None = None) -> ThreadingHTTPServer:
    host = host or os.environ.get("TWINGIS_HOST", "127.0.0.1")
    port = port if port is not None else int(os.environ.get("TWINGIS_PORT", "8765"))
    httpd = ThreadingHTTPServer((host, port), TwinBishkekHandler)
    _start_environment_collector()
    return httpd


if __name__ == "__main__":
    httpd = create_server()
    print(f"Twin Bishkek is running at http://{httpd.server_address[0]}:{httpd.server_address[1]}")
    print("Prototype coefficients are illustrative and not calibrated against measured data.")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping Twin Bishkek server.")
    finally:
        httpd.server_close()
