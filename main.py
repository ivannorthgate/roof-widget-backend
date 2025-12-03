payload = {
    "first_name": first_name,
    "last_name": last_name,
    "name": name,
    "email": email,
    "phone": phone,
    "address": address,

    # ✅ new address parts
    "street": req.get("street", ""),
    "city": req.get("city", ""),
    "state": req.get("state", ""),
    "postal_code": req.get("postal_code", ""),
    "country": req.get("country", "US"),

    "squares": squares_val,
    "pitch_class": pitch_class,
    "source": "Roof Widget"
}
