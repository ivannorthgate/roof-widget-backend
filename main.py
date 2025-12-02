from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import requests
from shapely.geometry import Polygon
from pyproj import Transformer
from typing import Any, Dict

app = FastAPI()

# Allow your GHL page to call this API
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://affiliate.northgate-construction.com",
        "https://northgate-construction.com",
        "https://www.northgate-construction.com"
    ],
    allow_credentials=False,   # IMPORTANT: must be False if you don't need cookies
    allow_methods=["*"],
    allow_headers=["*"],
)

class MeasureRequest(BaseModel):
    address: str = ""
    lat: float = None
    lng: float = None

USER_AGENT = "YourRoofWidget/1.0 (contact: youremail@yourdomain.com)"

def photon_autocomplete(query):
    url = f"https://photon.komoot.io/api/?q={query}&limit=1"
    r = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=10)
    r.raise_for_status()
    data = r.json()
    if not data["features"]:
        return None
    props = data["features"][0]["properties"]
    coords = data["features"][0]["geometry"]["coordinates"]
    return {
        "address": props.get("name") or query,
        "lat": coords[1],
        "lng": coords[0]
    }

def overpass_building_polygon(lat, lng):
    query = f"""
    [out:json];
    (
      way["building"](around:25,{lat},{lng});
      relation["building"](around:25,{lat},{lng});
    );
    out geom;
    """
    r = requests.post(
        "https://overpass-api.de/api/interpreter",
        data=query.encode("utf-8"),
        headers={"User-Agent": USER_AGENT},
        timeout=30
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
        return None

    def area_of(poly_points):
        return Polygon(poly_points).area

    best = max(candidates, key=area_of)
    return best

def polygon_area_sqft(poly_points):
    transformer = Transformer.from_crs("EPSG:4326", "EPSG:3857", always_xy=True)
    meter_points = [transformer.transform(lon, lat) for lon, lat in poly_points]
    poly_m = Polygon(meter_points)
    area_sqft = poly_m.area * 10.7639
    return area_sqft

@app.post("/measure-roof")
def measure_roof(req: MeasureRequest):
    lat, lng = req.lat, req.lng

    if lat is None or lng is None:
        if not req.address:
            return {"error": "no_location"}
        geo = photon_autocomplete(req.address)
        if not geo:
            return {"error": "geocode_failed"}
        lat, lng = geo["lat"], geo["lng"]

    poly_points = overpass_building_polygon(lat, lng)
    if not poly_points:
        return {"error": "no_footprint"}

    flat_sqft = polygon_area_sqft(poly_points)

    pitch_class = "medium"
    multipliers = {"low": 1.05, "medium": 1.15, "steep": 1.25}
    roof_sqft = flat_sqft * multipliers[pitch_class]
    squares = roof_sqft / 100

    return {
        "flat_sqft": round(flat_sqft, 0),
        "roof_sqft_est": round(roof_sqft, 0),
        "squares": round(squares, 1),
        "pitch_class": pitch_class
    }

@app.post("/create-lead")
async def create_lead(request: Request):
    req: Dict[str, Any] = await request.json()

    name = req.get("name", "")
    email = req.get("email", "")
    phone = req.get("phone", "")
    address = req.get("address", "")
    pitch_class = req.get("pitch_class", "unknown")
    ghl_webhook_url = req.get("ghl_webhook_url")

    squares_raw = req.get("squares", 0)
    try:
        squares_val = float(squares_raw)
    except:
        squares_val = 0

    if not ghl_webhook_url:
        return {"status": "error", "message": "Missing ghl_webhook_url from widget", "received_payload": req}

    payload = {
        "name": name,
        "email": email,
        "phone": phone,
        "address": address,
        "squares": squares_val,
        "pitch_class": pitch_class,
        "source": "Roof Widget"
    }

    r = requests.post(ghl_webhook_url, json=payload, timeout=10)

    return {
        "status": "sent",
        "ghl_status": r.status_code,
        "ghl_body": r.text,
        "received_payload": req
    }
