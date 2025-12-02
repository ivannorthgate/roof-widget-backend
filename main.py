from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import requests
from shapely.geometry import Polygon
from pyproj import Transformer

app = FastAPI()

# Allow your GHL page to call this API
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # later you can restrict to your domain
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class MeasureRequest(BaseModel):
    address: str = ""
    lat: float = None
    lng: float = None

from typing import Optional, Union

class LeadRequest(BaseModel):
    name: str
    email: Optional[str] = ""
    phone: Optional[str] = ""
    address: str = ""
    squares: Union[float, str] = 0
    pitch_class: str = "unknown"
    ghl_webhook_url: str


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
    # Finds nearest building polygon within ~25 meters
    query = f"""
    [out:json];
    (
      way["building"](around:25,{lat},{lng});
      relation["building"](around:25,{lat},{lng});
    );
    out geom;
    """
    r = requests.post("https://overpass-api.de/api/interpreter",
                      data=query.encode("utf-8"),
                      headers={"User-Agent": USER_AGENT},
                      timeout=30)
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

    # Pick the biggest polygon (usually the main roof, not a shed)
    def area_of(poly_points):
        poly = Polygon(poly_points)
        return poly.area

    best = max(candidates, key=area_of)
    return best

def polygon_area_sqft(poly_points):
    """
    poly_points are (lon, lat). Convert to meters, compute area, then to sqft.
    """
    transformer = Transformer.from_crs("EPSG:4326", "EPSG:3857", always_xy=True)
    meter_points = [transformer.transform(lon, lat) for lon, lat in poly_points]
    poly_m = Polygon(meter_points)
    area_sqm = poly_m.area
    area_sqft = area_sqm * 10.7639
    return area_sqft

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

    # Get roof footprint from OpenStreetMap
    poly_points = overpass_building_polygon(lat, lng)
    if not poly_points:
        return {"error": "no_footprint"}

    flat_sqft = polygon_area_sqft(poly_points)

    # MVP: no LiDAR pitch yet -> ask user / default medium
    pitch_class = "medium"

    # Convert flat to rough roof area using simple multiplier per pitch class
    # low ~ 1.05, medium ~ 1.15, steep ~ 1.25
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
def create_lead(req: LeadRequest):
    # Convert squares to float if possible
    try:
        squares_val = float(req.squares)
    except:
        squares_val = 0

    payload = {
        "name": req.name,
        "email": req.email or "",
        "phone": req.phone or "",
        "address": req.address or "",
        "squares": squares_val,
        "pitch_class": req.pitch_class,
        "source": "Roof Widget"
    }

    r = requests.post(req.ghl_webhook_url, json=payload, timeout=10)
    return {"status": "sent", "ghl_status": r.status_code, "ghl_body": r.text}


