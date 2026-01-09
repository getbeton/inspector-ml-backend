import os
import json
import argparse
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Dict, Any, List, Tuple

import requests
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.dates as mdates


# -----------------------------
# PostHog API helpers
# -----------------------------

def rest_get(host: str, key: str, path: str, params: dict | None = None, timeout: int = 60) -> dict:
    url = f"{host}{path}"
    r = requests.get(
        url,
        headers={"Authorization": f"Bearer {key}"},
        params=params or {},
        timeout=timeout,
    )
    if not r.ok:
        raise RuntimeError(f"GET {path} -> HTTP {r.status_code}: {r.text[:2000]}")
    return r.json()


def rest_post(host: str, key: str, path: str, payload: dict, timeout: int = 300) -> dict:
    url = f"{host}{path}"
    r = requests.post(
        url,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        data=json.dumps(payload),
        timeout=timeout,
    )
    if not r.ok:
        raise RuntimeError(f"POST {path} -> HTTP {r.status_code}: {r.text[:2000]}")
    return r.json()


def ph_query(host: str, key: str, project_id: str, hogql: str, name: str = "hogql") -> dict:
    payload = {"query": {"kind": "HogQLQuery", "query": hogql}, "name": name}
    return rest_post(host, key, f"/api/projects/{project_id}/query/", payload)


def to_df(qjson: dict) -> pd.DataFrame:
    rows = qjson.get("results", [])
    cols_raw = qjson.get("columns")

    cols = None
    if isinstance(cols_raw, list) and cols_raw:
        if isinstance(cols_raw[0], dict):
            cols = [c.get("name") for c in cols_raw]
        elif isinstance(cols_raw[0], str):
            cols = cols_raw

    if cols is None:
        types_raw = qjson.get("types")
        if isinstance(types_raw, list) and types_raw and isinstance(types_raw[0], list):
            cols = [t[0] for t in types_raw]

    return pd.DataFrame(rows, columns=cols)


def hogql_quote(s: str) -> str:
    # Экранирование для строковых литералов
    return "'" + s.replace("\\", "\\\\").replace("'", "\\'") + "'"


# -----------------------------
# Plot helpers
# -----------------------------

def _format_date_axis_no_year(ax):
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%m-%d"))
    ax.tick_params(axis="x", rotation=45)


def save_fig(path: Path, title: str = None, xlabel: str = None, ylabel: str = None):
    ax = plt.gca()
    _format_date_axis_no_year(ax)
    if title:
        plt.title(title)
    if xlabel:
        plt.xlabel(xlabel)
    if ylabel:
        plt.ylabel(ylabel)
    plt.tight_layout()
    plt.savefig(path, dpi=160)
    plt.close()


def plot_user_event_timeline(df: pd.DataFrame, out_png: Path, title: str):
    """
    df columns: timestamp, event
    """
    d = df.copy()
    d["timestamp"] = pd.to_datetime(d["timestamp"], utc=True, errors="coerce")
    d = d.dropna(subset=["timestamp"])
    d = d.sort_values("timestamp")
    if d.empty:
        return

    events = d["event"].astype(str).unique().tolist()
    event_to_y = {ev: i for i, ev in enumerate(events)}
    y = d["event"].astype(str).map(event_to_y)

    plt.figure(figsize=(12, max(4, 0.35 * len(events))))
    plt.scatter(d["timestamp"], y, s=10)

    ax = plt.gca()
    ax.set_yticks(range(len(events)))
    ax.set_yticklabels(events, fontsize=8)

    save_fig(out_png, title=title, xlabel="date", ylabel="event")


def plot_barh(df: pd.DataFrame, label_col: str, value_col: str, out_png: Path, title: str, topn: int = 15):
    d = df.copy().head(topn)
    if d.empty:
        return
    d = d.iloc[::-1]  # largest on top visually
    plt.figure(figsize=(12, 6))
    plt.barh(d[label_col].astype(str), d[value_col])
    # bar chart: no need date formatter
    if title:
        plt.title(title)
    plt.xlabel(value_col)
    plt.ylabel(label_col)
    plt.tight_layout()
    plt.savefig(out_png, dpi=160)
    plt.close()


def plot_line_date(df: pd.DataFrame, x: str, y: str, out_png: Path, title: str):
    d = df.copy()
    d[x] = pd.to_datetime(d[x], utc=True, errors="coerce")
    d = d.dropna(subset=[x]).sort_values(x)
    if d.empty:
        return
    plt.figure(figsize=(12, 4))
    plt.plot(d[x], d[y])
    save_fig(out_png, title=title, xlabel="date", ylabel=y)


# -----------------------------
# Person enrichment
# -----------------------------

EMAIL_KEYS = ["email", "$email", "Email", "e-mail", "work_email", "user_email"]


def extract_identity_from_properties(props: Any) -> Dict[str, Any]:
    """
    props может быть dict или строка JSON (иногда PostHog возвращает JSON как string)
    """
    if props is None:
        return {}

    d = None
    if isinstance(props, dict):
        d = props
    else:
        # попробуем распарсить строку
        try:
            d = json.loads(props)
        except Exception:
            return {}

    out = {}
    # email
    for k in EMAIL_KEYS:
        if k in d and d[k]:
            out["email"] = d[k]
            break

    # common fields
    for k in ["name", "$name", "full_name", "company", "org", "plan", "role"]:
        if k in d and d[k]:
            out.setdefault("notes", {})[k] = d[k]

    # geo/browser hints
    for k in ["$geoip_country_name", "$geoip_country_code", "$geoip_city_name",
              "$browser", "$browser_version", "$os", "$os_version", "$device_type"]:
        if k in d and d[k]:
            out.setdefault("hints", {})[k] = d[k]

    return out


def try_get_person_props_via_hogql(host: str, key: str, project_id: str, distinct_id: str) -> Optional[Dict[str, Any]]:
    """
    Пытаемся через HogQL получить person properties (если доступны таблицы persons / person_distinct_ids).
    Если в вашем инстансе нет таких таблиц/прав — вернём None.
    """
    candidates = [
        # вариант 1: persons + person_distinct_ids (часто встречается)
        f"""
        select
          p.id as person_id,
          p.properties as person_properties
        from persons p
        left join person_distinct_ids pd on pd.person_id = p.id
        where pd.distinct_id = {hogql_quote(distinct_id)}
        limit 1
        """,
        # вариант 2: person_distinct_ids без алиаса (на всякий)
        f"""
        select
          p.id as person_id,
          p.properties as person_properties
        from persons p
        join person_distinct_ids on person_distinct_ids.person_id = p.id
        where person_distinct_ids.distinct_id = {hogql_quote(distinct_id)}
        limit 1
        """,
    ]

    for q in candidates:
        try:
            res = ph_query(host, key, project_id, q, name="person_props_try")
            df = to_df(res)
            if len(df):
                return {
                    "person_id": df.iloc[0].get("person_id"),
                    "person_properties_raw": df.iloc[0].get("person_properties"),
                }
        except Exception:
            continue

    return None


def try_get_person_props_via_rest(host: str, key: str, project_id: str, distinct_id: str) -> Optional[Dict[str, Any]]:
    """
    Fallback: persons search endpoint (если доступен).
    """
    try:
        data = rest_get(
            host, key,
            f"/api/projects/{project_id}/persons/",
            params={"search": distinct_id, "limit": 5}
        )
        results = data.get("results") or []
        if not results:
            return None

        # попробуем найти точное совпадение по distinct_ids
        picked = None
        for p in results:
            dids = p.get("distinct_ids") or []
            if distinct_id in dids:
                picked = p
                break
        if picked is None:
            picked = results[0]

        return {
            "person_id": picked.get("id"),
            "person_properties_raw": picked.get("properties"),
            "distinct_ids": picked.get("distinct_ids"),
            "created_at": picked.get("created_at"),
        }
    except Exception:
        return None


# -----------------------------
# Dwell time estimation
# -----------------------------

def _safe_json(x: Any) -> dict:
    if isinstance(x, dict):
        return x
    if isinstance(x, str):
        try:
            return json.loads(x)
        except Exception:
            return {}
    return {}


def estimate_dwell_times(df_events: pd.DataFrame, cap_seconds: int = 30 * 60) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """
    Вход: события только для одного distinct_id.
    Нужны колонки: timestamp, event, properties
    Возврат:
      - page_dwell_df: per (session_id,url) durations
      - sessions_df: per session duration
    """
    d = df_events.copy()
    d["timestamp"] = pd.to_datetime(d["timestamp"], utc=True, errors="coerce")
    d = d.dropna(subset=["timestamp"])
    d = d.sort_values("timestamp")
    if d.empty:
        return pd.DataFrame(), pd.DataFrame()

    props = d["properties"].apply(_safe_json)
    d["url"] = props.apply(lambda p: p.get("$current_url") or p.get("$initial_current_url"))
    d["session_id"] = props.apply(lambda p: p.get("$session_id") or p.get("session_id"))
    d["browser"] = props.apply(lambda p: p.get("$browser"))
    d["country"] = props.apply(lambda p: p.get("$geoip_country_name") or p.get("$geoip_country_code"))
    d["city"] = props.apply(lambda p: p.get("$geoip_city_name"))

    # берем только pageview/pageleave для dwell логики
    pv = d[d["event"].isin(["$pageview", "$pageleave"])].copy()
    pv = pv.dropna(subset=["session_id"])  # без session_id сложно
    if pv.empty:
        return pd.DataFrame(), pd.DataFrame()

    # dwell: для каждого (session_id,url) парсим последовательность pv/pl
    records = []
    for (sid, url), g in pv.groupby(["session_id", "url"], dropna=False):
        if pd.isna(sid) or not url:
            continue
        g = g.sort_values("timestamp")
        # стек pageviews
        last_pv_ts = None
        for _, row in g.iterrows():
            ev = row["event"]
            ts = row["timestamp"]
            if ev == "$pageview":
                last_pv_ts = ts
            elif ev == "$pageleave" and last_pv_ts is not None:
                dur = (ts - last_pv_ts).total_seconds()
                if dur < 0:
                    continue
                dur = min(dur, cap_seconds)
                records.append({"session_id": sid, "url": url, "start": last_pv_ts, "end": ts, "seconds": dur})
                last_pv_ts = None

        # fallback если pageleave нет: оценим как до следующего события в этом session_id (любой url) — делаем позже

    page_dwell = pd.DataFrame(records)

    # fallback dwell (если мало pageleave): по соседним pageview в рамках session_id
    if page_dwell.empty:
        pv2 = pv[pv["event"] == "$pageview"].copy()
        pv2 = pv2.sort_values(["session_id", "timestamp"])
        pv2["next_ts"] = pv2.groupby("session_id")["timestamp"].shift(-1)
        pv2["seconds"] = (pv2["next_ts"] - pv2["timestamp"]).dt.total_seconds()
        pv2["seconds"] = pv2["seconds"].clip(lower=0, upper=cap_seconds)
        pv2 = pv2.dropna(subset=["seconds", "url"])
        page_dwell = pv2.rename(columns={"timestamp": "start", "next_ts": "end"})[["session_id", "url", "start", "end", "seconds"]]

    # sessions duration (по любым событиям, где есть session_id)
    d_sess = d.dropna(subset=["session_id"]).copy()
    sessions = (
        d_sess.groupby("session_id")
        .agg(start=("timestamp", "min"), end=("timestamp", "max"), events=("event", "count"))
        .reset_index()
    )
    sessions["seconds"] = (sessions["end"] - sessions["start"]).dt.total_seconds().clip(lower=0)

    return page_dwell.sort_values("seconds", ascending=False), sessions.sort_values("start")


# -----------------------------
# Main report
# -----------------------------

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=30, help="window for analysis")
    parser.add_argument("--min_events", type=int, default=30, help="minimum events to consider a user 'active'")
    parser.add_argument("--out", type=str, default="", help="output dir")
    parser.add_argument("--cap_seconds", type=int, default=1800, help="cap for dwell time per page")
    args = parser.parse_args()

    host = os.getenv("POSTHOG_HOST", "https://us.posthog.com")
    key = os.getenv("POSTHOG_PERSONAL_API_KEY")
    project_id = os.getenv("POSTHOG_PROJECT_ID")

    if not key:
        raise SystemExit("Set env POSTHOG_PERSONAL_API_KEY")
    if not project_id:
        raise SystemExit("Set env POSTHOG_PROJECT_ID")

    stamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    out_dir = Path(args.out) if args.out else Path(f"posthog_single_user_report_{stamp}")
    out_dir.mkdir(parents=True, exist_ok=True)

    # 1) pick most active distinct_id
    q_pick = ph_query(
        host, key, project_id,
        f"""
        select distinct_id, count() as cnt
        from events
        where timestamp >= now() - interval {args.days} day
        group by distinct_id
        having cnt >= {args.min_events}
        order by cnt desc
        limit 1
        """,
        name="pick_most_active_user"
    )
    df_pick = to_df(q_pick)
    if df_pick.empty:
        raise RuntimeError(f"No users found with >= {args.min_events} events in last {args.days} days.")

    distinct_id = str(df_pick.iloc[0]["distinct_id"])
    total_cnt = int(df_pick.iloc[0]["cnt"])
    (out_dir / "picked_user.txt").write_text(distinct_id, encoding="utf-8")

    # 2) fetch user events (trim some useful props)
    q_events = ph_query(
        host, key, project_id,
        f"""
        select
          timestamp,
          event,
          distinct_id,
          properties
        from events
        where distinct_id = {hogql_quote(distinct_id)}
          and timestamp >= now() - interval {args.days} day
        order by timestamp asc
        """,
        name="user_events_raw"
    )
    df_events = to_df(q_events)
    df_events.to_csv(out_dir / "user_events_raw.csv", index=False)

    # Extract some top-level columns from properties for easier viewing
    props = df_events["properties"].apply(_safe_json) if "properties" in df_events.columns else pd.Series([{}]*len(df_events))
    df_events_view = df_events.copy()
    df_events_view["url"] = props.apply(lambda p: p.get("$current_url") or p.get("$initial_current_url"))
    df_events_view["referrer"] = props.apply(lambda p: p.get("$referrer") or p.get("$initial_referrer"))
    df_events_view["session_id"] = props.apply(lambda p: p.get("$session_id") or p.get("session_id"))
    df_events_view["browser"] = props.apply(lambda p: p.get("$browser"))
    df_events_view["os"] = props.apply(lambda p: p.get("$os"))
    df_events_view["country"] = props.apply(lambda p: p.get("$geoip_country_name") or p.get("$geoip_country_code"))
    df_events_view["city"] = props.apply(lambda p: p.get("$geoip_city_name"))
    df_events_view.to_csv(out_dir / "user_events_flat.csv", index=False)

    # 3) latest known environment snapshot
    q_latest = ph_query(
        host, key, project_id,
        f"""
        select
          timestamp,
          properties.$geoip_country_name as country,
          properties.$geoip_city_name as city,
          properties.$browser as browser,
          properties.$browser_version as browser_version,
          properties.$os as os,
          properties.$os_version as os_version,
          properties.$device_type as device_type,
          properties.$current_url as url
        from events
        where distinct_id = {hogql_quote(distinct_id)}
          and timestamp >= now() - interval {args.days} day
        order by timestamp desc
        limit 1
        """,
        name="latest_env"
    )
    df_latest = to_df(q_latest)
    df_latest.to_csv(out_dir / "latest_env.csv", index=False)

    # 4) try to identify person (email etc.)
    person_info = try_get_person_props_via_hogql(host, key, project_id, distinct_id)
    if person_info is None:
        person_info = try_get_person_props_via_rest(host, key, project_id, distinct_id)

    identity = {}
    if person_info and "person_properties_raw" in person_info:
        identity = extract_identity_from_properties(person_info["person_properties_raw"])

    # 5) dwell time estimation + sessions
    page_dwell_df, sessions_df = estimate_dwell_times(df_events, cap_seconds=args.cap_seconds)
    if not page_dwell_df.empty:
        page_dwell_df.to_csv(out_dir / "page_dwell.csv", index=False)
        # aggregate per url
        page_agg = (
            page_dwell_df.groupby("url", dropna=False)["seconds"].sum()
            .reset_index()
            .sort_values("seconds", ascending=False)
        )
        page_agg["minutes"] = (page_agg["seconds"] / 60.0).round(2)
        page_agg.to_csv(out_dir / "page_dwell_agg.csv", index=False)
    else:
        page_agg = pd.DataFrame(columns=["url", "seconds", "minutes"])

    if not sessions_df.empty:
        sessions_df.to_csv(out_dir / "sessions.csv", index=False)
        # sessions per day
        spd = sessions_df.copy()
        spd["day"] = pd.to_datetime(spd["start"], utc=True).dt.floor("D")
        spd = spd.groupby("day").size().reset_index(name="sessions")
        spd.to_csv(out_dir / "sessions_per_day.csv", index=False)
    else:
        spd = pd.DataFrame(columns=["day", "sessions"])

    # 6) plots
    # timeline: лучше не рисовать 200 уникальных ивентов — ограничим top 25 по частоте
    ev_counts = df_events_view["event"].value_counts().reset_index()
    ev_counts.columns = ["event", "cnt"]
    top_events = ev_counts["event"].head(25).tolist()
    df_timeline = df_events_view[df_events_view["event"].isin(top_events)][["timestamp", "event"]].copy()
    if not df_timeline.empty:
        plot_user_event_timeline(
            df_timeline,
            out_dir / "fig_user_timeline.png",
            title=f"User timeline (distinct_id={distinct_id}) — top 25 events"
        )

    # page dwell top
    if not page_agg.empty:
        plot_barh(
            page_agg,
            label_col="url",
            value_col="minutes",
            out_png=out_dir / "fig_top_pages_by_dwell.png",
            title=f"Top pages by total dwell time (minutes), cap={args.cap_seconds}s",
            topn=15
        )

    # sessions per day
    if not spd.empty:
        plot_line_date(spd, "day", "sessions", out_dir / "fig_sessions_per_day.png",
                       title="Sessions per day")

    # 7) markdown report
    # summary blocks
    latest_env = df_latest.iloc[0].to_dict() if len(df_latest) else {}
    env_lines = []
    if latest_env:
        for k in ["country", "city", "browser", "browser_version", "os", "os_version", "device_type", "url", "timestamp"]:
            v = latest_env.get(k)
            if v is not None and v != "":
                env_lines.append(f"- **{k}**: {v}")

    identity_lines = []
    if identity.get("email"):
        identity_lines.append(f"- **email (found)**: {identity['email']}")
    hints = identity.get("hints") or {}
    notes = identity.get("notes") or {}
    if hints:
        identity_lines.append(f"- **hints**: {json.dumps(hints, ensure_ascii=False)}")
    if notes:
        identity_lines.append(f"- **notes**: {json.dumps(notes, ensure_ascii=False)}")

    # top events markdown
    ev_counts_md = ev_counts.head(15).to_markdown(index=False)

    # dwell markdown
    dwell_md = page_agg[["url", "minutes"]].head(15).to_markdown(index=False) if not page_agg.empty else "_No dwell data computed (no session_id or no pageview/pageleave)._"

    now_utc = datetime.now(timezone.utc).isoformat(timespec="seconds")
    report_md = f"""# PostHog single-user activity report

**Generated (UTC):** {now_utc}  
**Host:** {host}  
**Project ID:** {project_id}  
**Window:** last {args.days} days  
**Picked user (most active):** `{distinct_id}`  
**Events in window:** {total_cnt}

## Identification / enrichment
{"\\n".join(identity_lines) if identity_lines else "_No email/identity fields found in person properties (or not accessible)._"}  

## Latest environment snapshot (from latest event)
{"\\n".join(env_lines) if env_lines else "_No latest-env snapshot available._"}

## Top events for this user (by count)
{ev_counts_md}

## Timeline (top 25 events)
![Timeline](fig_user_timeline.png)

## Sessions
- sessions.csv: per-session duration (start/end/seconds)
- sessions_per_day.csv + chart:

![Sessions per day](fig_sessions_per_day.png)

## Page dwell time (approx.)
**Method:**
- Prefer `$pageview` → `$pageleave` (per session_id + url)
- Fallback: time until next `$pageview` in same session_id
- Cap per page: {args.cap_seconds} seconds

Top pages by total dwell time (minutes):
{dwell_md}

![Top pages by dwell](fig_top_pages_by_dwell.png)

## Files
- `user_events_raw.csv` — raw events with `properties`
- `user_events_flat.csv` — flattened columns (url/referrer/session/browser/geo)
- `latest_env.csv` — latest browser/geo snapshot
- `page_dwell.csv`, `page_dwell_agg.csv` — dwell estimate
- `sessions.csv`, `sessions_per_day.csv` — session stats
"""

    (out_dir / "report.md").write_text(report_md, encoding="utf-8")

    print(f"\nDone. Output dir: {out_dir.resolve()}")
    print(f"Picked distinct_id: {distinct_id} (events={total_cnt})")
    print(f"Report: {out_dir / 'report.md'}")


if __name__ == "__main__":
    main()
