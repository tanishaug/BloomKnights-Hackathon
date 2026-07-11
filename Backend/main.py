import os
import json
import hashlib
import re
import time
import math
import requests
from datetime import datetime, timedelta
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List
from dotenv import load_dotenv
from google import genai
from google.genai import types

load_dotenv()

app = FastAPI(title="GridPulse AI Enterprise Engine", version="3.0.0")
allowed_origins = [
    origin.strip()
    for origin in os.getenv(
        "ALLOWED_ORIGINS",
        "http://localhost:5173,http://127.0.0.1:5173,http://localhost:3000,http://127.0.0.1:3000,https://gridpulse-web.onrender.com",
    ).split(",")
    if origin.strip()
]
allowed_origin_regex = os.getenv(
    "ALLOWED_ORIGIN_REGEX",
    r"^https://([a-z0-9-]+\.)*(onrender\.com|netlify\.app|vercel\.app|github\.dev)$|^http://(localhost|127\.0\.0\.1)(:\d+)?$",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_origin_regex=allowed_origin_regex,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
ai_client = None
if GEMINI_API_KEY:
    ai_client = genai.Client(api_key=GEMINI_API_KEY)
    print("SUCCESS: Actual Gemini AI Engine Online.")
else:
    print("WARNING: GEMINI_API_KEY missing; running deterministic search/report fallback.")


@app.get("/health")
def health_check():
    return {"status": "ok"}


# Real-world physical constants
ELECTRICITY_RATE_KWH = 0.22
SOLAR_COST_PER_KW = 2400.0
ITC_TAX_CREDIT = 0.30
ROOF_USABLE_RATIO = 0.60
SOLAR_PANEL_EFFICIENCY = 0.18
PERFORMANCE_RATIO = 0.75
BERDO_RESOURCE_ID = "87521565-7f15-4b8d-a225-ac4df9e3f309"
BERDO_API_URL = "https://data.boston.gov/api/3/action/datastore_search"
_berdo_cache = {"expires": 0.0, "records": []}
_geocode_cache = {}
_building_cache = {}

INCENTIVES = [
    {
        "name": "Federal Clean Energy Investment Tax Credit",
        "value": "30% of eligible solar project cost",
        "source_url": "https://www.irs.gov/credits-deductions/businesses/clean-electricity-investment-credit",
        "status": "Eligibility review required"
    },
    {
        "name": "Mass Save commercial incentives",
        "value": "Efficiency rebates and technical assistance",
        "source_url": "https://www.masssave.com/en/business/rebates-and-incentives",
        "status": "Utility and measure eligibility varies"
    },
    {
        "name": "Massachusetts SMART Program",
        "value": "Performance-based solar tariff",
        "source_url": "https://www.mass.gov/solar-massachusetts-renewable-target-smart",
        "status": "Capacity-block and utility review required"
    }
]

FLORIDA_INCENTIVES = [
    {
        "name": "Federal Clean Electricity Investment Credit",
        "value": "Credit amount depends on eligibility and prevailing-wage requirements",
        "source_url": "https://www.irs.gov/credits-deductions/clean-electricity-investment-credit",
        "status": "Tax-adviser eligibility review required"
    },
    {
        "name": "Florida property-tax treatment for renewable energy devices",
        "value": "Qualifying renewable-energy property may receive statutory tax treatment",
        "source_url": "https://www.leg.state.fl.us/statutes/index.cfm?App_mode=Display_Statute&URL=0100-0199/0193/Sections/0193.624.html",
        "status": "Confirm with the county property appraiser"
    }
]


class OptimizeRequest(BaseModel):
    budget: float
    lat: float
    lon: float
    radius: int = 10000


class SearchRequest(BaseModel):
    query: str
    lat: float
    lon: float
    radius: int = 10000


def _stable_number(seed: str, minimum: int, maximum: int) -> int:
    value = int(hashlib.sha256(seed.encode("utf-8")).hexdigest()[:8], 16)
    return minimum + value % (maximum - minimum + 1)


def _normalize_address(value: str) -> str:
    value = re.sub(r"[^a-z0-9 ]", " ", (value or "").lower())
    replacements = {"street": "st", "avenue": "ave",
                    "road": "rd", "place": "pl"}
    words = [replacements.get(word, word) for word in value.split()]
    return " ".join(words)


def fetch_berdo_records() -> List[dict]:
    """Loads Boston's keyless public BERDO disclosure dataset with a one-hour cache."""
    if _berdo_cache["expires"] > time.time():
        return _berdo_cache["records"]
    try:
        response = requests.get(
            BERDO_API_URL,
            params={"resource_id": BERDO_RESOURCE_ID, "limit": 6000},
            headers={"User-Agent": "GridPulseAI/3.0 public-energy-research"},
            timeout=30,
        )
        response.raise_for_status()
        records = response.json()["result"]["records"]
        _berdo_cache.update(
            {"expires": time.time() + 3600, "records": records})
        return records
    except (requests.RequestException, KeyError, ValueError) as err:
        print(f"Boston BERDO lookup failed: {err}")
        return _berdo_cache["records"]


def enrich_with_berdo(buildings: List[dict]) -> List[dict]:
    records = fetch_berdo_records()
    by_address = {
        _normalize_address(record.get("Building Address", "")): record
        for record in records if record.get("Building Address")
    }
    by_number = {}
    for record in records:
        normalized = _normalize_address(record.get("Building Address", ""))
        for token in normalized.split():
            if token.isdigit():
                by_number.setdefault(token, []).append((normalized, record))
    for building in buildings:
        normalized_address = _normalize_address(building.get("address", ""))
        record = by_address.get(normalized_address)
        if not record:
            building_tokens = set(normalized_address.split())
            house_numbers = [
                token for token in building_tokens if token.isdigit()]
            street_tokens = {
                token for token in building_tokens if not token.isdigit()}
            best_score = 0.0
            for number in house_numbers:
                for candidate_address, candidate in by_number.get(number, []):
                    candidate_street = {
                        token for token in candidate_address.split() if not token.isdigit()}
                    score = len(street_tokens & candidate_street) / \
                        max(1, len(street_tokens | candidate_street))
                    if score > best_score:
                        best_score, record = score, candidate
            if best_score < 0.6:
                record = None
        if not record:
            continue

        def numeric(field: str):
            try:
                return float(record[field]) if record.get(field) not in (None, "") else None
            except (TypeError, ValueError):
                return None
        building.update({
            "berdo_id": record.get("BERDO ID"),
            "berdo_disclosure_year": 2024,
            "disclosed_electricity_kwh": numeric("Electricity Usage (kWh)"),
            "disclosed_total_energy_kbtu": numeric("Total Site Energy Usage (kBtu)"),
            "disclosed_ghg_tons": (numeric("Estimated Total GHG Emissions (kgCO2e)") or 0) / 1000,
            "site_eui": numeric("Site EUI (Energy Use Intensity kBtu/ft2)"),
            "energy_star_score": numeric("Energy Star Score"),
            "compliance_status": record.get("Compliance Status"),
            "data_quality": "public_disclosure_and_public_footprint",
            "energy_source": "City of Boston BERDO 2024 owner-reported disclosure (not independently verified)"
        })
    return buildings


def _modeled_buildings(lat: float, lon: float, count: int = 10) -> List[dict]:
    """Creates deterministic sites when public footprint services are unavailable."""
    buildings = []
    for idx in range(1, count + 1):
        seed = f"{lat:.4f}:{lon:.4f}:{idx}"
        floors = _stable_number(seed + ":floors", 1, 8)
        roof_area = float(_stable_number(seed + ":roof", 6000, 30000))
        lat_offset = (
            (_stable_number(seed + ":lat", 0, 1000) / 1000) - 0.5) * 0.018
        lon_offset = (
            (_stable_number(seed + ":lon", 0, 1000) / 1000) - 0.5) * 0.018
        buildings.append({
            "id": idx,
            "osm_id": -idx,
            "name": f"Modeled Site #{idx}",
            "address": "Approximate site near selected coordinates",
            "lat": lat + lat_offset,
            "lon": lon + lon_offset,
            "type": "Commercial",
            "year_built": _stable_number(seed + ":year", 1970, 2022),
            "floors": floors,
            "floor_area": roof_area * floors,
            "roof_area": roof_area,
            "data_quality": "modeled_location",
            "building_source": "Deterministic fallback; not a disclosed building record"
        })
    return buildings


def _polygon_area_sqft(geometry: List[dict], latitude: float) -> float | None:
    """Calculates an OSM footprint's planar area from its real polygon vertices."""
    if len(geometry) < 4:
        return None
    meters_per_lon = 111320 * math.cos(math.radians(latitude))
    points = [(p["lon"] * meters_per_lon, p["lat"] * 110540) for p in geometry]
    area_sqm = abs(sum(
        points[i][0] * points[(i + 1) % len(points)][1]
        - points[(i + 1) % len(points)][0] * points[i][1]
        for i in range(len(points))
    )) / 2
    return round(area_sqm * 10.7639, 1) if area_sqm > 0 else None


def fetch_real_buildings(lat: float, lon: float, radius: int = 1500, limit: int = 500, focus_osm_type: str | None = None, focus_osm_id: int | None = None) -> List[dict]:
    """Queries public structural footprints through redundant OSM Overpass services."""
    cache_key = (round(lat, 5), round(lon, 5), radius,
                 limit, focus_osm_type, focus_osm_id)
    cached = _building_cache.get(cache_key)
    if cached and cached["expires"] > time.time():
        return cached["buildings"]
    overpass_urls = [
        "https://overpass-api.de/api/interpreter",
        "https://overpass.kumi.systems/api/interpreter",
        "https://overpass.nchc.org.tw/api/interpreter",
    ]
    focus_query = f"way({focus_osm_id}); out geom;" if focus_osm_type == "way" and focus_osm_id else ""
    query = f"""
    [out:json][timeout:25];
    {focus_query}
    way["building"](around:{radius},{lat},{lon});
    out geom {limit};
    """
    elements = []
    for overpass_url in overpass_urls:
        try:
            res = requests.post(
                overpass_url,
                data={"data": query},
                headers={"User-Agent": "GridPulseAI/3.0 public-energy-research"},
                timeout=30,
            )
            if res.status_code == 200:
                elements = res.json().get("elements", [])
                if elements:
                    break
            else:
                print(
                    f"OSM endpoint {overpass_url} returned HTTP {res.status_code}: {res.text[:200]}")
        except (requests.RequestException, ValueError) as err:
            print(f"OSM endpoint {overpass_url} failed: {err}")

    try:
        buildings = []
        idx = 1
        for e in elements:
            tags = e.get("tags", {})
            if not tags.get("building"):
                continue
            name = tags.get("name") or tags.get("addr:housename")
            building_tag = tags.get("building", "yes")
            b_type = "Building" if building_tag == "yes" else building_tag.replace(
                "_", " ").title()

            center = e.get("center", {})
            clat = center.get("lat", e.get("lat"))
            clon = center.get("lon", e.get("lon"))
            geometry = e.get("geometry", [])
            if (clat is None or clon is None) and geometry:
                clat = sum(point["lat"] for point in geometry) / len(geometry)
                clon = sum(point["lon"] for point in geometry) / len(geometry)
            if not clat or not clon:
                continue

            try:
                floors = max(1, int(float(tags.get("building:levels", ""))))
            except (TypeError, ValueError):
                floors = None
            try:
                # OSM roof:area is in square metres unless a unit is explicitly supplied.
                roof_area = float(tags.get("roof:area", "")) * 10.7639
            except (TypeError, ValueError):
                roof_area = _polygon_area_sqft(geometry, clat)
            floor_area = roof_area * floors if roof_area and floors else None
            address_parts = [
                tags.get("addr:housenumber"), tags.get("addr:street")]
            address = " ".join(part for part in address_parts if part) or None
            source_id = f"osm:{e.get('type', 'way')}:{e['id']}"

            buildings.append({
                "id": idx,
                "source_id": source_id,
                "osm_type": e.get("type", "way"),
                "osm_id": e["id"],
                "name": name or address or f"OSM building {e['id']}",
                "address": address,
                "lat": clat,
                "lon": clon,
                "type": b_type,
                "year_built": tags.get("start_date"),
                "floors": floors,
                "floor_area": floor_area,
                "roof_area": roof_area,
                "data_quality": "public_osm_footprint",
                "building_source": "OpenStreetMap contributors via Overpass API",
                "source_url": f"https://www.openstreetmap.org/{e.get('type', 'way')}/{e['id']}",
                "source_fields": "Location, footprint geometry, and available OSM tags",
                "energy_data_quality": "not_available"
            })
            idx += 1
            if idx > limit:
                break
        buildings.sort(key=lambda b: (
            b["lat"] - lat) ** 2 + (b["lon"] - lon) ** 2)
        for idx, building in enumerate(buildings, 1):
            building["id"] = idx
        if buildings:
            _building_cache[cache_key] = {
                "expires": time.time() + 900, "buildings": buildings}
            _building_cache[(round(lat, 5), round(lon, 5), radius, limit, None, None)] = {
                "expires": time.time() + 900, "buildings": buildings}
        return buildings
    except Exception as err:
        print(f"OSM parsing failed: {err}")
        return []


def gather_multi_temporal_weather(lat: float, lon: float) -> dict:
    """Pulls five-year history, present conditions, and a 16-day forecast from Open-Meteo."""
    today = datetime.now()
    five_years_ago = (today - timedelta(days=365 * 5)).strftime("%Y-%m-%d")
    yesterday = (today - timedelta(days=1)).strftime("%Y-%m-%d")
    historical_ghi_sum = None
    historical_years = 0

    try:
        hist_url = f"https://archive-api.open-meteo.com/v1/archive?latitude={lat}&longitude={lon}&start_date={five_years_ago}&end_date={yesterday}&daily=shortwave_radiation_sum&timezone=auto"
        h_res = requests.get(hist_url, timeout=15)
        if h_res.status_code == 200:
            rad_list = h_res.json().get("daily", {}).get("shortwave_radiation_sum", [])
            valid_rad = [r for r in rad_list if r is not None]
            if valid_rad:
                historical_years = max(1, round(len(valid_rad) / 365))
                # Open-Meteo daily radiation is MJ/m²; convert annual average to kWh/m².
                historical_ghi_sum = sum(valid_rad) / historical_years / 3.6
    except Exception as e:
        print(f"Historical weather API timeout: {e}")

    try:
        live_url = f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&current=temperature_2m,cloud_cover,shortwave_radiation&daily=shortwave_radiation_sum,temperature_2m_max&temperature_unit=fahrenheit&forecast_days=16&timezone=auto"
        l_res = requests.get(live_url, timeout=4)
        if l_res.status_code == 200:
            data = l_res.json()
            curr = data.get("current", {})
            daily = data.get("daily", {})
            forecast_radiation = [value for value in daily.get(
                "shortwave_radiation_sum", []) if value is not None]
            forecast_temps = [value for value in daily.get(
                "temperature_2m_max", []) if value is not None]

            return {
                "annual_historical_ghi": round(historical_ghi_sum, 1) if historical_ghi_sum else None,
                "historical_years": historical_years,
                "current_temp": curr.get("temperature_2m"),
                "current_cloud_cover": curr.get("cloud_cover"),
                "current_radiation": curr.get("shortwave_radiation"),
                "future_forecast_16d_avg_daily_radiation": round(sum(forecast_radiation) / len(forecast_radiation), 2) if forecast_radiation else None,
                "future_forecast_max_temp": max(forecast_temps) if forecast_temps else None,
                "is_live": True,
                "historical_is_live": historical_ghi_sum is not None,
            }
    except Exception as e:
        print(f"Live Weather lookup failed: {e}")

    return {
        "annual_historical_ghi": historical_ghi_sum,
        "historical_years": historical_years,
        "current_temp": None,
        "current_cloud_cover": None,
        "current_radiation": None,
        "future_forecast_16d_avg_daily_radiation": None,
        "future_forecast_max_temp": None,
        "is_live": False,
        "historical_is_live": historical_ghi_sum is not None,
    }


def process_live_analytics(buildings: List[dict], weather: dict) -> List[dict]:
    analyzed_list = []
    ghi = weather.get("annual_historical_ghi")

    for b in buildings:
        floor_area = b.get("floor_area")
        roof_area = b.get("roof_area")
        intensity = 20.0 if b["type"] in [
            "Office", "Hotel", "Retail"] else 11.0
        modeled_elec_kwh = int(floor_area * intensity) if floor_area else None
        annual_elec_kwh = int(b["disclosed_electricity_kwh"]) if b.get(
            "disclosed_electricity_kwh") else None
        forecast_max = weather.get("future_forecast_max_temp")
        climate_growth = max(0.005, min(
            0.035, (forecast_max - 70) * 0.0015)) if forecast_max is not None else None
        historical_usage = [
            {
                "year": datetime.now().year - years_ago,
                "estimated_kwh": int(annual_elec_kwh / ((1 + climate_growth) ** years_ago))
            }
            for years_ago in range(3, 0, -1)
        ] if annual_elec_kwh and climate_growth is not None else []
        future_usage = [
            {
                "year": datetime.now().year + years_ahead,
                "predicted_kwh": int(annual_elec_kwh * ((1 + climate_growth) ** years_ahead))
            }
            for years_ahead in range(1, 6)
        ] if annual_elec_kwh and climate_growth is not None else []

        usable_roof_sqm = roof_area * ROOF_USABLE_RATIO * 0.092903 if roof_area else 0
        solar_capacity_kw = round(
            usable_roof_sqm * SOLAR_PANEL_EFFICIENCY, 2) if roof_area else None

        annual_solar_generation_kwh = int(
            solar_capacity_kw * ghi * PERFORMANCE_RATIO) if solar_capacity_kw and ghi else None

        gross_cost = int(solar_capacity_kw *
                         SOLAR_COST_PER_KW) if solar_capacity_kw else None
        net_cost = int(gross_cost * (1.0 - ITC_TAX_CREDIT)
                       ) if gross_cost else None
        annual_savings = int(annual_solar_generation_kwh *
                             ELECTRICITY_RATE_KWH) if annual_solar_generation_kwh else None

        payback_years = round(net_cost / annual_savings,
                              2) if net_cost and annual_savings else None
        roi_pct = round((annual_savings / net_cost) * 100,
                        2) if net_cost and annual_savings else None
        carbon_reduction_tons = round(
            annual_solar_generation_kwh * 0.00028, 2) if annual_solar_generation_kwh else None

        if roi_pct and payback_years and solar_capacity_kw:
            financial_score = (min(100, roi_pct * 5) +
                               min(100, (10 / payback_years) * 50)) / 2
            scale_score = min(100, math.log1p(
                solar_capacity_kw) / math.log1p(500) * 100)
            source_completeness = sum(bool(b.get(field)) for field in (
                "address", "floors", "year_built", "roof_area")) / 4 * 100
            investment_score = round(
                financial_score * 0.5 + scale_score * 0.3 + source_completeness * 0.2, 1)
        else:
            investment_score = 0

        analyzed_list.append({
            **b,
            "annual_electricity_kwh": annual_elec_kwh,
            "modeled_electricity_kwh": modeled_elec_kwh,
            "energy_baseline_type": "Public disclosure" if b.get("disclosed_electricity_kwh") else "No public building-level energy record",
            "historical_usage_estimates": historical_usage,
            "future_usage_predictions": future_usage,
            "annual_usage_growth_pct": round(climate_growth * 100, 2) if climate_growth is not None else None,
            "solar_capacity_kw": solar_capacity_kw,
            "annual_solar_generation_kwh": annual_solar_generation_kwh,
            "gross_cost": gross_cost,
            "net_cost": net_cost,
            "annual_savings": annual_savings,
            "payback_years": payback_years,
            "roi_pct": roi_pct,
            "carbon_reduction_tons": carbon_reduction_tons,
            "investment_score": investment_score,
            "roi_status": "High" if investment_score >= 75 else "Moderate" if investment_score >= 50 else "Low",
            "energy_data_quality": "public_disclosure" if annual_elec_kwh else "not_available",
            "prediction_confidence": "high" if annual_elec_kwh else "not available",
            "methodology": f"Score weights modeled financial performance 50%, usable solar scale 30%, and public-source field completeness 20%. Solar production uses {weather.get('historical_years', 0)} years of observed Open-Meteo daily radiation and is not adjusted by one day's weather. Present conditions and the 16-day forecast are contextual. Cost, savings, and carbon remain planning estimates.",
            "data_sources": [
                b["building_source"],
                b.get("energy_source",
                      "No public building-level electricity record available"),
                "Open-Meteo historical archive and forecast APIs",
                "Public engineering assumptions; no private utility meter data"
            ],
            "incentives": FLORIDA_INCENTIVES
        })

    analyzed_list = sorted(
        analyzed_list, key=lambda x: x["investment_score"], reverse=True)
    for idx, b in enumerate(analyzed_list):
        b["rank"] = idx + 1
    return analyzed_list


@app.get("/api/geocode")
def geocode_location(q: str = Query(..., min_length=2, max_length=200)):
    """Resolves a Florida city or street address through OpenStreetMap Nominatim."""
    cache_key = q.strip().lower()
    if cache_key in _geocode_cache:
        return _geocode_cache[cache_key]
    results = []
    provider_errors = []
    try:
        response = requests.get(
            "https://nominatim.openstreetmap.org/search",
            params={
                "q": f"{q.strip()}, Florida, USA",
                "format": "jsonv2",
                "addressdetails": 1,
                "limit": 5,
                "countrycodes": "us",
            },
            headers={
                "User-Agent": "GridPulseAI/3.0 (Florida public building research)"},
            timeout=15,
        )
        response.raise_for_status()
        for item in response.json():
            address = item.get("address", {})
            if address.get("state") != "Florida" and address.get("ISO3166-2-lvl4") != "US-FL":
                continue
            results.append({
                "display_name": item["display_name"],
                "lat": float(item["lat"]),
                "lon": float(item["lon"]),
                "type": item.get("type"),
                "boundingbox": [float(value) for value in item.get("boundingbox", [])],
                "source": "OpenStreetMap Nominatim",
                "source_url": f"https://www.openstreetmap.org/{item.get('osm_type')}/{item.get('osm_id')}",
                "osm_type": item.get("osm_type"),
                "osm_id": item.get("osm_id"),
            })
    except (requests.RequestException, ValueError, KeyError) as err:
        provider_errors.append(f"Nominatim: {err}")

    if not results:
        try:
            school_query = any(term in q.lower() for term in (
                "school", "academy", "prep", "college", "university", "high school"))
            arcgis = requests.get(
                "https://geocode.arcgis.com/arcgis/rest/services/World/GeocodeServer/findAddressCandidates",
                params={
                    "SingleLine": f"{q.strip()}, Florida",
                    "f": "json",
                    "outFields": "Match_addr,LongLabel,Type,City,RegionAbbr",
                    "maxLocations": 5,
                    **({"category": "Education"} if school_query else {}),
                },
                headers={
                    "User-Agent": "GridPulseAI/3.0 Florida public building research"},
                timeout=15,
            )
            arcgis.raise_for_status()
            for candidate in arcgis.json().get("candidates", []):
                attributes = candidate.get("attributes", {})
                if attributes.get("RegionAbbr") != "FL" or candidate.get("score", 0) < 75:
                    continue
                location = candidate.get("location", {})
                extent = candidate.get("extent", {})
                results.append({
                    "display_name": attributes.get("LongLabel") or candidate.get("address"),
                    "lat": float(location["y"]),
                    "lon": float(location["x"]),
                    "type": (attributes.get("Type") or "place").lower(),
                    "boundingbox": [extent.get("ymin"), extent.get("ymax"), extent.get("xmin"), extent.get("xmax")],
                    "source": "Esri ArcGIS World Geocoding Service",
                    "source_url": "https://geocode.arcgis.com/",
                    "osm_type": None,
                    "osm_id": None,
                })
        except (requests.RequestException, ValueError, KeyError) as err:
            provider_errors.append(f"ArcGIS: {err}")

    if not results:
        try:
            census = requests.get(
                "https://geocoding.geo.census.gov/geocoder/locations/onelineaddress",
                params={"address": f"{q.strip()}, Florida",
                        "benchmark": "Public_AR_Current", "format": "json"},
                headers={
                    "User-Agent": "GridPulseAI/3.0 Florida public building research"},
                timeout=15,
            )
            census.raise_for_status()
            for match in census.json().get("result", {}).get("addressMatches", []):
                matched_address = match.get("matchedAddress", "")
                if ", FL," not in matched_address.upper():
                    continue
                coordinates = match.get("coordinates", {})
                results.append({
                    "display_name": matched_address,
                    "lat": float(coordinates["y"]),
                    "lon": float(coordinates["x"]),
                    "type": "address",
                    "boundingbox": [],
                    "source": "U.S. Census Geocoder",
                    "source_url": "https://geocoding.geo.census.gov/",
                    "osm_type": None,
                    "osm_id": None,
                })
        except (requests.RequestException, ValueError, KeyError) as err:
            provider_errors.append(f"Census: {err}")

    if not results and len(provider_errors) == 3:
        raise HTTPException(
            status_code=503, detail=f"Geocoding services unavailable: {'; '.join(provider_errors)}")
    payload = {"query": q, "results": results}
    _geocode_cache[cache_key] = payload
    return payload


@app.get("/api/buildings")
def get_scanned_buildings(
    lat: float = Query(..., ge=24.0, le=31.1),
    lon: float = Query(..., ge=-87.8, le=-79.7),
    radius: int = Query(5000, ge=100, le=30000),
    limit: int = Query(3000, ge=1, le=5000),
    focus_osm_type: str | None = Query(None, pattern="^(node|way|relation)$"),
    focus_osm_id: int | None = Query(None, ge=1),
):
    raw_buildings = fetch_real_buildings(
        lat, lon, radius, limit, focus_osm_type, focus_osm_id)
    weather = gather_multi_temporal_weather(lat, lon)
    return process_live_analytics(raw_buildings, weather)


@app.get("/api/buildings/{osm_id}/report")
def get_ai_prediction_report(osm_id: int, lat: float, lon: float, radius: int = Query(10000, ge=100, le=30000)):
    raw_buildings = fetch_real_buildings(lat, lon, radius, 3000)
    target = next((b for b in raw_buildings if b["osm_id"] == osm_id), None)
    if not target:
        raise HTTPException(status_code=404, detail="Asset matching error.")

    weather = gather_multi_temporal_weather(lat, lon)
    analyzed = process_live_analytics([target], weather)[0]
    report_sections = {
        "Executive summary": (
            f"{analyzed['name']} has a planning score of {analyzed['investment_score']}/100. "
            f"The mapped footprint supports an estimated {analyzed['solar_capacity_kw'] or 'unavailable'} kW system. "
            "This is a screening result based on public footprint and climate data, not a construction proposal."
        ),
        "Live environmental context": (
            f"Current temperature is {weather.get('current_temp')}°F with {weather.get('current_cloud_cover')}% cloud cover and "
            f"{weather.get('current_radiation')} W/m² shortwave radiation. The 16-day forecast averages "
            f"{weather.get('future_forecast_16d_avg_daily_radiation')} MJ/m²/day; the long-term model uses "
            f"{weather.get('historical_years')} years of observed radiation."
        ),
        "Financial analysis": (
            f"Modeled gross installation cost is ${analyzed.get('gross_cost') or 0:,.0f}; estimated net cost is "
            f"${analyzed.get('net_cost') or 0:,.0f}, with ${analyzed.get('annual_savings') or 0:,.0f} in modeled annual savings "
            f"and a {analyzed.get('payback_years') or 'not available'}-year simple payback. Verify tariffs, bids, tax eligibility, O&M, financing, and interconnection costs."
        ),
        "Environmental impact": (
            f"Modeled annual solar production is {analyzed.get('annual_solar_generation_kwh') or 0:,.0f} kWh, corresponding to "
            f"approximately {analyzed.get('carbon_reduction_tons') or 0} metric tons of avoided CO2e under the current planning factor. "
            "Actual impact depends on operating profile and the serving utility's marginal generation mix."
        ),
        "Clean energy strategy": (
            "Confirm ownership and roof rights; commission structural, roof-condition, shading, and electrical studies; obtain interval utility data and tariff details; "
            "request competitive EPC pricing; validate federal and Florida incentive eligibility; then compare rooftop solar, storage, efficiency, and demand-management scenarios."
        ),
    }

    prompt = f"""
    You are GridPulse AI, an autonomous system specializing in solar infrastructure metrics and microgrid design.
    Analyze this real physical structure and build an engineering feasibility summary:

    Structure Identity: {analyzed['name']} Located at Coords: ({analyzed['lat']}, {analyzed['lon']})
    Physical Frame: Roof Area {analyzed['roof_area']} sqft, Levels: {analyzed['floors']}.

    CLIMATE METRIC INPUTS:
    - Real Historical Solar GHI Record (Past Year): {weather['annual_historical_ghi']} kWh/m²/year.
    - Current Live Cloud Cover Concentration: {weather['current_cloud_cover']}%.
    - Present Real-Time Shortwave Flux Radiation: {weather['current_radiation']} W/m².
    - Future 16-Day Forecast Average Daily Solar Radiation: {weather['future_forecast_16d_avg_daily_radiation']} MJ/m²/day.

    ENGINEERING PROJECTIONS:
    - Proposed Solar System Cap: {analyzed['solar_capacity_kw']} kW DC.
    - Anticipated Output Capacity: {analyzed['annual_solar_generation_kwh']} kWh/year.
    - Current Modeled Building Consumption: {analyzed['annual_electricity_kwh']} kWh/year.
    - Five-Year Usage Forecast: {json.dumps(analyzed['future_usage_predictions'])}.
    - Forecast Confidence: {analyzed['prediction_confidence']}.
    - Data Provenance: {json.dumps(analyzed['data_sources'])}.
    - Post-Incentive Net System Cost: ${analyzed['net_cost']} USD.
    - Expected Annual Savings Yield: ${analyzed['annual_savings']}/year.
    - Return Metrics: ROI of {analyzed['roi_pct']}% | Payback Amortization: {analyzed['payback_years']} Years.
    - Carbon Interception Scope 2 Offset: {analyzed['carbon_reduction_tons']} MT CO2e/year.

    Write a detailed evaluation under 200 words explaining how the historical records versus future forecasts validates or challenges this asset's deployment viability. Do not use generic formatting filler.
    """
    try:
        if ai_client is None:
            raise RuntimeError("Gemini not configured")
        response = ai_client.models.generate_content(
            model="gemini-2.5-flash", contents=prompt)
        report_sections["Executive summary"] = response.text
        return {"report": response.text, "sections": report_sections, "weather": weather, "metrics": analyzed}
    except Exception as e:
        print(f"Gemini report fallback used: {e}")
        baseline = analyzed["energy_baseline_type"]
        return {
            "report": (
                f"{analyzed['name']} ranks #{analyzed['rank']} with an investment score of "
                f"{analyzed['investment_score']}. Using a {baseline.lower()}, the proposed "
                f"{analyzed['solar_capacity_kw']} kW system could produce "
                f"{analyzed['annual_solar_generation_kwh']:,} kWh annually, save approximately "
                f"${analyzed['annual_savings']:,} per year, and avoid "
                f"{analyzed['carbon_reduction_tons']} metric tons CO2e. Estimated payback is "
                f"{analyzed['payback_years']} years. This is a planning-level result; verify roof "
                "condition, shading, tariff class, structural capacity, and incentive eligibility before procurement."
            ),
            "weather": weather,
            "metrics": analyzed,
            "sections": report_sections,
            "ai_status": "deterministic_fallback"
        }


@app.post("/api/search")
def natural_language_search(req: SearchRequest):
    raw_buildings = fetch_real_buildings(req.lat, req.lon, req.radius, 3000)
    weather = gather_multi_temporal_weather(req.lat, req.lon)
    portfolio = process_live_analytics(raw_buildings, weather)
    normalized_query = req.query.lower()
    if "score" in normalized_query and any(word in normalized_query for word in ("lowest", "highest", "best", "worst", "minimum", "maximum")):
        ascending = any(word in normalized_query for word in (
            "lowest", "worst", "minimum"))
        ordered = sorted(
            portfolio, key=lambda item: item["investment_score"], reverse=not ascending)
        count_match = re.search(r"\b(\d+)\b", normalized_query)
        singular_request = "building" in normalized_query and "buildings" not in normalized_query
        count = min(10, int(count_match.group(1))
                    ) if count_match else 1 if singular_request else 5
        selected = ordered[:count]
        direction = "lowest" if ascending else "highest"
        return {
            "answer": f"Selected {len(selected)} {'building' if len(selected) == 1 else 'buildings'} with the {direction} investment score.",
            "selected_ids": [item["id"] for item in selected],
            "ai_status": "verified_deterministic_query",
        }

    minified_context = [
        {"id": b["id"], "osm_id": b["osm_id"], "name": b["name"], "roi": b["roi_pct"],
            "type": b["type"], "rank": b["rank"], "tons_co2": b["carbon_reduction_tons"]}
        for b in portfolio[:200]
    ]

    prompt = f"""
    Context Discovered Local Properties Data Options:
    {json.dumps(minified_context)}
    User Query: "{req.query}"

    Return your answer in a strict JSON schema structure containing conversational description and an index array of matches:
    {{
      "answer": "A summary explaining your reasoning...",
      "selected_ids": [1, 3]
    }}
    """
    try:
        if ai_client is None:
            raise RuntimeError("Gemini not configured")
        response = ai_client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt,
            config=types.GenerateContentConfig(
                system_instruction="You are GridPulse AI's routing module. Map user natural language questions onto the appropriate building record subset IDs.",
                response_mime_type="application/json",
                temperature=0.2
            )
        )
        return json.loads(response.text)
    except Exception as e:
        print(f"Gemini search fallback used: {e}")
        query = req.query.lower()
        if "carbon" in query or "emission" in query:
            ordered = sorted(
                portfolio, key=lambda item: item["carbon_reduction_tons"] or 0, reverse=True)
            criterion = "projected annual carbon reduction"
        elif "payback" in query or "fastest" in query or "shortest" in query:
            ordered = sorted(
                portfolio, key=lambda item: item["payback_years"] or float("inf"))
            criterion = "shortest modeled payback"
        elif "saving" in query:
            ordered = sorted(
                portfolio, key=lambda item: item["annual_savings"] or 0, reverse=True)
            criterion = "projected annual savings"
        else:
            wants_lowest = any(word in query for word in (
                "lowest", "worst", "bottom", "minimum", "least"))
            ordered = sorted(
                portfolio, key=lambda item: item["investment_score"], reverse=not wants_lowest)
            criterion = "lowest investment score" if wants_lowest else "highest investment score"
        count_match = re.search(r"\b(\d+)\b", query)
        singular_request = "building" in query and "buildings" not in query
        count = min(10, int(count_match.group(1))
                    ) if count_match else 1 if singular_request else 5
        selected = ordered[:count]
        return {
            "answer": f"Selected {len(selected)} {'building' if len(selected) == 1 else 'buildings'} ranked by {criterion}. Gemini was unavailable, so deterministic portfolio analytics were used.",
            "selected_ids": [item["id"] for item in selected],
            "ai_status": "deterministic_fallback"
        }


@app.post("/api/optimize")
def optimize_portfolio(req: OptimizeRequest):
    raw_buildings = fetch_real_buildings(req.lat, req.lon, req.radius, 3000)
    weather = gather_multi_temporal_weather(req.lat, req.lon)
    analyzed_portfolio = process_live_analytics(raw_buildings, weather)

    selected_ids = []
    remaining = req.budget
    for b in analyzed_portfolio:
        if b["net_cost"] is not None and b["net_cost"] <= remaining:
            selected_ids.append(b["id"])
            remaining -= b["net_cost"]

    return {"selected_ids": selected_ids, "remaining_budget": round(remaining, 2), "buildings": analyzed_portfolio}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
