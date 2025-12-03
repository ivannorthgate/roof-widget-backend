from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import requests
from shapely.geometry import Polygon
from pyproj import Transformer
from typing import Any, Dict, Optional

app = FastAPI()

# ✅ CORS: allow your exact funnel domain(s)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://affiliate.northgate-construction.com",
        "https://northgate-construction.com",
        "https://www.northgate-construction.com"
    ],
    allow_credentials=False,  # IMPORTANT: no cookies here
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------
# Request model for /measure-roof
# ---------------------------
class MeasureRequest(BaseModel):
    address: str = ""
    lat: Optional[float] = None
    lng: Optional[float] = None


USER_AGENT = "YourRoofWidget/1.0 (contact: youremail@yourdomain.com)"


# ---------------------------
# FREE ADDRESS -> LAT/LNG (Photon)
# ---------------------------
def photon_autocomplete(query: str):
    url = f"https://photon.komoot.io/api/?q={query}&limit=1"
    r = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=10)
    r.raise_for_status()
    data = r.json()

    if not data.get("features"):
        return None

    props = data["features"][0]["properties"]
    coords = data["features"][0]["geometry"]["coordinates"]
    return {
        "address": props.get("name") or query,
        "lat": coords[1],
        "lng": coords[0]
    }


# ---------------------------
# GET BUILDING OUTLINE (Overpass / OSM)
# Upgrades:
# 1) Radius 50m (instead of 25m)
# 2) Fallback servers if one is down/busy
# ---------------------------
def overpass_building_polygon(lat: float, lng: float):
    servers = [
        "https://overpass-api.de/api/interpreter",
        "https://overpass.kumi.systems/api/interpreter",
        "https://overpass.nchc.org.tw/api/interpreter"
    ]

    # ✅ Increased search radius to 50m
    query = f"""
    [out:json];
    (
      way["building"](around:50,{lat},{lng});
      relation["building"](around:50,{lat},{lng});
    );
    out geom;
    """

    last_error = None

    for url in servers:
        try:
            r = requests.post(
                url,
                data=query.encode("utf-8"),
                headers={"User-Agent": USER_AGENT},
                timeout=35
            )
            r.raise_for_status()
            data = r.json()

            candidates = []
            for el in data.get("elements", []):
                geom = el.get("geometry")
                if not geom:
                    continue

                points = [(p["lon"], p["lat"]) for p in geom]
                if len(points) >= 3:
                    candidates.append(points)

            if not candidates:
                continue

            # choose biggest polygon (usually main roof)
            def area_of(poly_points):
                return Polygon(poly_points).area

            return max(candidates, key=area_of)

        except Exception as e:
            last_error = e
            continue

    print("Overpass failed:", last_error)
    return None


# ---------------------------
# POLYGON AREA -> SQFT
# ---------------------------
def polygon_area_sqft(poly_points):
    transformer = Transformer.from_crs("EPSG:4326", "EPSG:3857", always_xy=True)
    meter_points = [transformer.transform(lon, lat) for lon, lat in poly_points]
    poly_m = Polygon(meter_points)
    return poly_m.area * 10.7639


# ---------------------------
# /measure-roof  (MVP AREA ONLY)
# ---------------------------
@app.post("/measure-roof")
def measure_roof(req: MeasureRequest):
    lat, lng = req.lat, req.lng

    # If lat/lng missing, try Photon search (free)
    if lat is None or lng is None:
        if not req.address:
            return {"error": "no_location"}

        geo = photon_autocomplete(req.address)
        if not geo:
            return {"error": "geocode_failed"}

        lat, lng = geo["lat"], geo["lng"]

    # Get roof footprint from OpenStreetMap (with fallback servers)
    poly_points = overpass_building_polygon(lat, lng)
    if not poly_points:
        return {"error": "no_footprint"}

    flat_sqft = polygon_area_sqft(poly_points)

    # MVP pitch default
    pitch_class = "medium"

    # rough slope multiplier
    multipliers = {"low": 1.05, "medium": 1.15, "steep": 1.25}
    roof_sqft = flat_sqft * multipliers[pitch_class]
    squares = roof_sqft / 100

    return {
        "flat_sqft": round(flat_sqft, 0),
        "roof_sqft_est": round(roof_sqft, 0),
        "squares": round(squares, 1),
        "pitch_class": pitch_class
    }


# ---------------------------
# /create-lead  (BULLETPROOF + forwards address parts)
# ---------------------------
@app.post("/create-lead")
async def create_lead(request: Request):
    try:
        req: Dict[str, Any] = await request.json()

        first_name = (req.get("first_name") or "").strip()
        last_name = (req.get("last_name") or "").strip()

        name = (req.get("name") or "").strip()
        if not name:
            name = f"{first_name} {last_name}".strip()

        email = req.get("email") or ""
        phone = req.get("phone") or ""
        address = req.get("address") or ""
        pitch_class = req.get("pitch_class") or "unknown"
        ghl_webhook_url = (req.get("ghl_webhook_url") or "").strip()

        # Separate address parts (from widget)
        street = req.get("street") or ""
        city = req.get("city") or ""
        state = req.get("state") or ""
        postal_code = req.get("postal_code") or ""
        country = req.get("country") or "US"

        squares_raw = req.get("squares", 0)
        try:
            squares_val = float(squares_raw)
        except:
            squares_val = 0

        # Pricing / package fields (optional)
        selected_package = req.get("selected_package") or ""
        selected_product = req.get("selected_product") or ""
        price_per_sq = req.get("price_per_sq") or 0
        estimated_package_price = req.get("estimated_package_price") or 0

        if not ghl_webhook_url:
            return {
                "status": "error",
                "message": "Missing ghl_webhook_url from widget",
                "received_payload": req
            }

        payload = {
            "first_name": first_name,
            "last_name": last_name,
            "name": name,
            "email": email,
            "phone": phone,
            "address": address,

            "street": street,
            "city": city,
            "state": state,
            "postal_code": postal_code,
            "country": country,

            "squares": squares_val,
            "pitch_class": pitch_class,

            # ✅ Pricing selection (if they choose)
            "selected_package": selected_package,
            "selected_product": selected_product,
            "price_per_sq": price_per_sq,
            "estimated_package_price": estimated_package_price,

            "source": "Roof Widget"
        }

        try:
            r = requests.post(ghl_webhook_url, json=payload, timeout=15)
            return {
                "status": "sent",
                "ghl_status": r.status_code,
                "ghl_body": r.text,
                "received_payload": req
            }
        except Exception as e:
            return {
                "status": "error",
                "message": f"Failed to send to GHL: {str(e)}",
                "received_payload": req
            }

    except Exception as e:
        return {
            "status": "error",
            "message": f"Server error in create-lead: {str(e)}"
        }
