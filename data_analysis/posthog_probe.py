import os
import json
import requests
import pandas as pd

PH_HOST = os.getenv("POSTHOG_HOST", "https://us.posthog.com")
PH_KEY  = os.getenv("POSTHOG_PERSONAL_API_KEY")  # Personal API key
PROJECT_ID = os.getenv("POSTHOG_PROJECT_ID")     # можно оставить пустым — скрипт поможет найти

def ph_get(path, params=None):
    r = requests.get(
        f"{PH_HOST}{path}",
        headers={"Authorization": f"Bearer {PH_KEY}"},
        params=params or {},
        timeout=60,
    )
    if r.status_code == 401:
        raise RuntimeError("401 Unauthorized. Похоже, ключ не Personal API key или не хватает scope.")
    r.raise_for_status()
    return r.json()

def ph_query(project_id: str, hogql: str, name: str):
    url = f"{PH_HOST}/api/projects/{project_id}/query/"
    payload = {"query": {"kind": "HogQLQuery", "query": hogql}, "name": name}
    r = requests.post(
        url,
        headers={"Authorization": f"Bearer {PH_KEY}", "Content-Type": "application/json"},
        data=json.dumps(payload),
        timeout=300,
    )
    r.raise_for_status()
    return r.json()

def to_df(qjson):
    # 1) rows
    rows = qjson.get("results", [])

    # 2) columns могут быть:
    #    - list[str]
    #    - list[dict] с ключом "name"
    #    - иногда можно восстановить имена из "types": [[name, type], ...]
    cols_raw = qjson.get("columns")

    cols = None
    if isinstance(cols_raw, list) and len(cols_raw) > 0:
        if isinstance(cols_raw[0], dict):
            cols = [c.get("name") for c in cols_raw]
        elif isinstance(cols_raw[0], str):
            cols = cols_raw

    if cols is None:
        types_raw = qjson.get("types")
        if isinstance(types_raw, list) and len(types_raw) > 0 and isinstance(types_raw[0], list) and len(types_raw[0]) >= 1:
            cols = [t[0] for t in types_raw]

    return pd.DataFrame(rows, columns=cols)

def main():
    assert PH_KEY, "Set POSTHOG_PERSONAL_API_KEY env var"

    # 1) org + projects
    org = ph_get("/api/organizations/@current")
    org_id = org["id"]
    projects = ph_get(f"/api/organizations/{org_id}/projects/", params={"limit": 100})
    print("Organization:", org_id)
    print("Projects:")
    for p in projects["results"]:
        print(" -", p["id"], p["name"])

    pid = PROJECT_ID or str(projects["results"][0]["id"])
    print("\nUsing project_id =", pid)

    # 2) небольшой сэмпл событий (чтобы увидеть поля)
    q1 = ph_query(
        pid,
        "select timestamp, event, distinct_id, properties "
        "from events order by timestamp desc limit 20",
        "sample last 20 events",
    )
    df1 = to_df(q1)
    print("\nSample events:")
    print(df1.head())

    # 3) какие события самые частые за 90 дней
    q2 = ph_query(
        pid,
        "select event, count() as cnt "
        "from events where timestamp >= now() - interval 90 day "
        "group by event order by cnt desc limit 200",
        "top events last 90d",
    )
    df2 = to_df(q2)
    df2.to_csv("posthog_top_events_90d.csv", index=False)

    # 4) pageviews по дням (если есть $pageview)
    q3 = ph_query(
        pid,
        "select toDate(timestamp) as day, count() as pageviews "
        "from events "
        "where event = '$pageview' and timestamp >= now() - interval 90 day "
        "group by day order by day",
        "pageviews daily last 90d",
    )
    df3 = to_df(q3)
    df3.to_csv("posthog_pageviews_daily_90d.csv", index=False)

    # 5) топ страниц (по $current_url)
    q4 = ph_query(
        pid,
        "select properties.$current_url as url, count() as views "
        "from events "
        "where event = '$pageview' and timestamp >= now() - interval 30 day "
        "group by url order by views desc limit 200",
        "top urls last 30d",
    )
    df4 = to_df(q4)
    df4.to_csv("posthog_top_urls_30d.csv", index=False)

    print("\nSaved:")
    print(" - posthog_top_events_90d.csv")
    print(" - posthog_pageviews_daily_90d.csv")
    print(" - posthog_top_urls_30d.csv")

if __name__ == "__main__":
    main()