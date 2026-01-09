import random

def mock_companies():
    random.seed(7)
    names = [
        ("Northwind Logistics", "northwind.io"),
        ("LusoBank", "lusobank.pt"),
        ("Helio Insurance", "helioinsure.com"),
        ("PortoManufacturing", "portomfg.pt"),
        ("Beacon Telecom", "beacon-tel.eu"),
        ("Artemis Retail", "artemisretail.com"),
        ("Sapphire SaaS", "sapphiresaas.com"),
        ("Atlas Energy", "atlasenergy.eu"),
        ("Vega Shipping", "vegashipping.com"),
        ("Aurora Health", "aurorahealth.io"),
        ("Zenith HR", "zenithhr.com"),
        ("Quanta Security", "quantasec.eu"),
    ]
    out = []
    for i, (n, d) in enumerate(names):
        out.append({
            "attio_record_id": f"mock-{i}",
            "name": n,
            "domain": d,
            "web_url": None,
            "arr": None,
            "seats": None,
            "plan": random.choice(["free", "starter", "pro"]),
            "owner_email": random.choice([None, "owner@" + d]),
            "stage": random.choice(["trial", "active", "customer"]),
        })
    return out