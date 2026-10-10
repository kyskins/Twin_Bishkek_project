"""Fast, interpretable Twin Bishkek scenario model.

The coefficients below are prototype assumptions, not values calibrated from
Bishkek traffic or air-quality observations. Replace them with measured data or
trained model outputs when those datasets are available.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class AreaProfile:
    name: str
    lat: float
    lon: float
    pm25: float
    delay_min: float
    comfort: float
    traffic_sensitivity: float = 1.0
    air_sensitivity: float = 1.0


AREA_PROFILES: dict[str, AreaProfile] = {
    "Джал": AreaProfile("Джал", 42.824, 74.578, 39.0, 20.0, 74.0, 1.04, 1.02),
    "Асанбай": AreaProfile("Асанбай", 42.808, 74.626, 36.0, 17.0, 77.0, 0.92, 0.94),
    "Южная магистраль": AreaProfile("Южная магистраль", 42.790, 74.604, 41.0, 22.0, 71.0, 1.18, 1.14),
    "Центр": AreaProfile("Центр", 42.875, 74.605, 38.0, 21.0, 73.0, 1.12, 1.08),
    "Микрорайон 7": AreaProfile("Микрорайон 7", 42.842, 74.619, 36.0, 17.0, 77.0, 0.95, 0.95),
}


OBJECT_TRAFFIC: dict[str, float] = {
    "Жилой комплекс": 1.35,
    "Торговый центр": 1.65,
    "Парк / сквер": -0.38,
}


class TwinBishkekSimulator:
    """Superpose fleet, land-use, greenery and event effects in under 0.5 s."""

    model_name = "TwinBishkekSimulator"
    model_version = "prototype-1.2"

    def __init__(self, areas: dict[str, AreaProfile] | None = None) -> None:
        self.areas = areas or AREA_PROFILES

    @staticmethod
    def _number(value: Any, default: float, low: float, high: float) -> float:
        try:
            number = float(value)
            if math.isfinite(number):
                return max(low, min(high, number))
        except (TypeError, ValueError):
            pass
        return default

    @staticmethod
    def _aqi_from_pm25(pm25: float) -> int:
        # Prototype display index. Replace with the chosen regulatory AQI method
        # when the project specifies its jurisdiction and averaging period.
        return max(0, round(pm25 * 2.08))

    @staticmethod
    def _peak(hour: float, center: float, width: float) -> float:
        distance = abs(hour - center)
        distance = min(distance, 24.0 - distance)
        return math.exp(-0.5 * (distance / width) ** 2)

    @classmethod
    def _traffic_time_factor(cls, hour: float, day_type: str) -> float:
        morning = cls._peak(hour, 8.0, 1.45)
        evening = cls._peak(hour, 18.0, 1.8)
        midday = cls._peak(hour, 13.0, 2.8)
        if day_type == "weekend":
            return 0.72 + 0.35 * morning + 0.50 * evening + 0.44 * midday
        return 0.62 + 0.78 * morning + 0.88 * evening + 0.22 * midday

    @classmethod
    def _object_time_factor(cls, object_name: str, hour: float, day_type: str) -> float:
        morning = cls._peak(hour, 8.0, 1.6)
        evening = cls._peak(hour, 18.0, 2.0)
        midday = cls._peak(hour, 13.0, 2.8)
        if object_name == "Торговый центр":
            return 0.22 + 0.12 * morning + 0.40 * midday + 0.58 * evening
        if object_name == "Парк / сквер":
            weekend_factor = 0.22 if day_type == "weekend" else 0.0
            return 0.32 + 0.12 * morning + (0.28 + weekend_factor) * midday + (0.22 + weekend_factor) * evening
        return 0.44 + 0.42 * morning + 0.50 * evening + 0.14 * midday

    @staticmethod
    def _nearest_hourly_record(records: Any, hour: float) -> dict[str, Any]:
        if not isinstance(records, list):
            return {}
        best_record: dict[str, Any] = {}
        best_distance = float("inf")
        for record in records:
            if not isinstance(record, dict):
                continue
            timestamp = str(record.get("time", ""))
            try:
                forecast_hour = int(timestamp[11:13]) + int(timestamp[14:16]) / 60
            except (ValueError, IndexError):
                continue
            distance = abs(forecast_hour - hour)
            distance = min(distance, 24.0 - distance)
            if distance < best_distance:
                best_record = record
                best_distance = distance
        return best_record

    def simulate(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        started = time.perf_counter()
        params = params or {}

        area_name = str(params.get("area", "Джал"))
        area = self.areas.get(area_name, self.areas["Джал"])
        current_environment = params.get("current_environment") or {}
        environment_is_current = (
            isinstance(current_environment, dict)
            and current_environment.get("available") is True
            and current_environment.get("area") == area.name
        )
        if not isinstance(current_environment, dict):
            current_environment = {}
        selected_hour = self._number(params.get("time_hour", 8), 8, 0, 23)
        day_type = str(params.get("day_type", "weekday"))
        if day_type not in ("weekday", "weekend"):
            day_type = "weekday"
        traffic_time_factor = self._traffic_time_factor(selected_hour, day_type)
        object_name = str(params.get("development_object", "Жилой комплекс"))
        if object_name not in OBJECT_TRAFFIC:
            object_name = "Жилой комплекс"
        object_time_factor = self._object_time_factor(object_name, selected_hour, day_type)
        selected_air_period = self._nearest_hourly_record(
            current_environment.get("air_forecast_24h", []) if environment_is_current else [],
            selected_hour,
        )
        selected_weather_period = self._nearest_hourly_record(
            current_environment.get("weather_forecast_24h", []) if environment_is_current else [],
            selected_hour,
        )
        air_observation = current_environment.get("air", {}) if environment_is_current else {}
        if not isinstance(air_observation, dict):
            air_observation = {}
        else:
            air_observation = dict(air_observation)
        air_observation.update({key: value for key, value in selected_air_period.items() if key != "time" and value is not None})
        weather_observation = dict(current_environment.get("weather", {})) if environment_is_current and isinstance(current_environment.get("weather"), dict) else {}
        weather_observation.update({key: value for key, value in selected_weather_period.items() if key != "time" and value is not None})
        baseline_air_time = selected_air_period.get("time")
        baseline_pm25 = self._number(air_observation.get("pm2_5"), area.pm25, 0.0, 500.0)
        baseline_aqi = self._number(
            air_observation.get("european_aqi"),
            self._aqi_from_pm25(baseline_pm25),
            0.0,
            500.0,
        )
        effects_in = params.get("effects") or {}
        if not isinstance(effects_in, dict):
            effects_in = {}
        fleet_pct = self._number(params.get("cars_change_pct", 12), 12, -50, 100) if effects_in.get("fleet", True) else 0.0
        ev_share_pct = self._number(params.get("ev_share_pct", 28), 28, 0, 100)
        greenery_pct = self._number(params.get("greenery_change_pct", 0), 0, -80, 100) if effects_in.get("greenery", True) else 0.0
        scale = self._number(params.get("project_scale", 4), 4, 0, 10) if effects_in.get("development", True) else 0.0
        horizon_years = self._number(params.get("horizon_years", 5), 5, 0, 5)
        rollout = horizon_years / 5
        events_in = params.get("events") or {}
        events = {
            "street_closure": bool(events_in.get("street_closure", False)),
            "concert": bool(events_in.get("concert", False)),
            "bridge_repair": bool(events_in.get("bridge_repair", False)),
        }

        # Traffic is affected by all vehicles. ICE share determines the fleet's
        # pollution component; EVs do not remove their road-space demand.
        ice_share = 1 - ev_share_pct / 100
        object_factor = OBJECT_TRAFFIC[object_name]
        evening_peak = self._peak(selected_hour, 18.0, 1.8)
        midday_peak = self._peak(selected_hour, 13.0, 2.8)
        event_activity = 0.4 + 0.45 * evening_peak + 0.15 * midday_peak
        repair_activity = 0.3 + 0.7 * midday_peak
        event_delay = {
            "street_closure": 3.0 * traffic_time_factor if events["street_closure"] else 0.0,
            "concert": 2.6 * event_activity if events["concert"] else 0.0,
            "bridge_repair": 2.2 * repair_activity if events["bridge_repair"] else 0.0,
        }
        traffic_parts = {
            "fleet": fleet_pct * rollout * 0.16 * area.traffic_sensitivity * traffic_time_factor,
            "development": scale * rollout * object_factor * object_time_factor,
            **event_delay,
        }
        traffic_interactions = {
            "fleet_and_development": max(0, fleet_pct) * rollout * (scale * rollout / 10) * 0.055 * traffic_time_factor,
            "adjacent_streets": (1.0 if events["street_closure"] else 0.0)
            * (0.5 + area.traffic_sensitivity * 0.45) * traffic_time_factor,
        }
        delay_delta = sum(traffic_parts.values()) + sum(traffic_interactions.values())
        baseline_delay = max(3.0, area.delay_min * traffic_time_factor)
        delay_min = max(3.0, baseline_delay + delay_delta)
        speed_kmh = max(5.0, 31.0 - (delay_min - 18.0) * 0.56)

        pm_parts = {
            "combustion_vehicles": fleet_pct * rollout * ice_share * 0.12 * area.air_sensitivity * traffic_time_factor,
            "development_activity": max(0, scale * rollout * object_factor * object_time_factor) * 0.075,
            "greenery": -greenery_pct * rollout * 0.11,
            "street_closure_rerouting": 0.68 * traffic_time_factor if events["street_closure"] else 0.0,
            "concert_emissions": 1.35 * event_activity if events["concert"] else 0.0,
            "bridge_repair_activity": 0.42 * repair_activity if events["bridge_repair"] else 0.0,
        }
        pm_interaction = max(0.0, delay_delta) * 0.018 * traffic_time_factor + max(0, fleet_pct) * rollout * (scale * rollout / 10) * ice_share * 0.012 * traffic_time_factor
        pm_delta = sum(pm_parts.values()) + pm_interaction
        pm25 = max(2.0, baseline_pm25 + pm_delta)

        park_benefit = scale * rollout * 0.62 if object_name == "Парк / сквер" else 0.0
        baseline_comfort = area.comfort - max(0.0, baseline_delay - area.delay_min) * 0.55 + max(0.0, area.delay_min - baseline_delay) * 0.15
        comfort = baseline_comfort - max(0.0, delay_delta) * 1.15 - max(0.0, pm_delta) * 0.62
        comfort += max(0, greenery_pct) * rollout * 0.13 + park_benefit
        comfort = max(0.0, min(100.0, comfort))

        baseline_speed = max(5.0, 31.0 - (baseline_delay - 18.0) * 0.56)
        baseline = {
            "pm25": round(baseline_pm25, 1),
            "aqi": round(baseline_aqi),
            "traffic_delay_min": round(baseline_delay, 1),
            "average_speed_kmh": round(baseline_speed, 1),
            "comfort_score": round(max(0.0, min(100.0, baseline_comfort))),
        }
        scenario_aqi = max(
            0,
            round(baseline_aqi + self._aqi_from_pm25(pm25) - self._aqi_from_pm25(baseline_pm25)),
        )
        scenario = {
            "pm25": round(pm25, 1),
            "aqi": scenario_aqi,
            "traffic_delay_min": round(delay_min, 1),
            "average_speed_kmh": round(speed_kmh, 1),
            "comfort_score": round(comfort),
        }

        pm_local = round(pm25 + (max(0.0, delay_delta) * 0.05), 1)
        traffic_level = (
            "Час пик" if traffic_time_factor >= 1.15
            else "Повышенный поток" if traffic_time_factor >= 0.9
            else "Свободнее обычного"
        )
        summary = (
            f"{object_name} в районе «{area.name}» в {selected_hour:02.0f}:00 ({'будни' if day_type == 'weekday' else 'выходной'}) "
            f"даёт расчётную задержку {scenario['traffic_delay_min']:.0f} мин "
            f"({scenario['average_speed_kmh']:.0f} км/ч). PM2.5: {scenario['pm25']:.1f} µg/m³, "
            f"локальная оценка: {pm_local:.1f} µg/m³. Комфорт района: {scenario['comfort_score']}/100. "
            f"Горизонт сценария: {horizon_years:.0f} лет."
        )
        elapsed_ms = (time.perf_counter() - started) * 1000
        return {
            "model": self.model_name,
            "version": self.model_version,
            "calibrated": False,
            "area": area.name,
            "horizon_years": horizon_years,
            "selected_time": {
                "hour": selected_hour,
                "label": f"{selected_hour:02.0f}:00",
                "day_type": day_type,
                "day_label": "Будни" if day_type == "weekday" else "Выходной",
                "traffic_factor": round(traffic_time_factor, 3),
                "traffic_level": traffic_level,
                "object_activity_factor": round(object_time_factor, 3),
                "air_model_time": baseline_air_time,
                "weather_model_time": selected_weather_period.get("time"),
                "wind_speed_kmh": weather_observation.get("wind_speed_10m"),
                "wind_direction_degrees": weather_observation.get("wind_direction_10m"),
                "temperature_c": weather_observation.get("temperature_2m"),
                "humidity_pct": weather_observation.get("relative_humidity_2m"),
            },
            "baseline": baseline,
            "scenario": scenario,
            "delta": {key: round(scenario[key] - baseline[key], 1) for key in baseline},
            "pm25_local": pm_local,
            "summary": summary,
            "factor_breakdown": {
                "traffic": {**{key: round(value, 3) for key, value in traffic_parts.items()},
                            **{key: round(value, 3) for key, value in traffic_interactions.items()}},
                "pm25": {**{key: round(value, 3) for key, value in pm_parts.items()}, "interaction": round(pm_interaction, 3)},
            },
            "events": events,
            "environment_source": current_environment.get("source") if environment_is_current else None,
            "traffic_source": "2GIS live map layer; numeric delay remains an uncalibrated area-level estimate",
            "elapsed_ms": round(elapsed_ms, 3),
        }

    def run(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """Compatibility alias for callers that prefer ``run``."""
        return self.simulate(params)
