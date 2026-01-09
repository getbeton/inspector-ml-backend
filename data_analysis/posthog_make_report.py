import os
import json
import argparse
from datetime import datetime
from pathlib import Path
import matplotlib.dates as mdates

import requests
import pandas as pd
import matplotlib.pyplot as plt


# -----------------------------
# PostHog API helpers
# -----------------------------

def ph_post(host: str, key: str, path: str, payload: dict, timeout: int = 300) -> dict:
    url = f"{host}{path}"
    r = requests.post(
        url,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        data=json.dumps(payload),
        timeout=timeout,
    )
    if r.status_code == 401:
        raise RuntimeError(
            "401 Unauthorized. Скорее всего это не Personal API key или не хватает scope (нужен query:read)."
        )
    if not r.ok:
        raise RuntimeError(f"PostHog HTTP {r.status_code}: {r.text[:2000]}")
    r.raise_for_status()
    return r.json()


def ph_query(host: str, key: str, project_id: str, hogql: str, name: str = "hogql") -> dict:
    payload = {"query": {"kind": "HogQLQuery", "query": hogql}, "name": name}
    return ph_post(host, key, f"/api/projects/{project_id}/query/", payload)


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


# -----------------------------
# Plot helpers
# -----------------------------

def save_fig(path: Path, title: str = None, xlabel: str = None, ylabel: str = None):
    ax = plt.gca()

    # Формат оси X: без года (месяц-день)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%m-%d"))
    ax.tick_params(axis="x", rotation=45)

    if title:
        plt.title(title)
    if xlabel:
        plt.xlabel(xlabel)
    if ylabel:
        plt.ylabel(ylabel)

    plt.tight_layout()
    plt.savefig(path, dpi=160)
    plt.close()

def plot_series(df: pd.DataFrame, x: str, y: str, out_png: Path, title: str):
    d = df.copy()
    d[x] = pd.to_datetime(d[x])
    d = d.sort_values(x)
    plt.figure()
    plt.plot(d[x], d[y])
    save_fig(out_png, title=title, xlabel="date", ylabel=y)


def plot_multi_event_timeseries(df: pd.DataFrame, out_png: Path, title: str):
    """
    df: columns = [day, event, cnt]
    """
    d = df.copy()
    d["day"] = pd.to_datetime(d["day"])
    pivot = d.pivot_table(index="day", columns="event", values="cnt", aggfunc="sum").fillna(0)
    pivot = pivot.sort_index()

    plt.figure()
    for col in pivot.columns:
        plt.plot(pivot.index, pivot[col], label=str(col))
    plt.legend(loc="best", fontsize=8)
    save_fig(out_png, title=title, xlabel="date", ylabel="events/day")


def plot_bar_top(df: pd.DataFrame, label_col: str, value_col: str, out_png: Path, title: str, topn: int = 20):
    d = df.copy().head(topn)
    d = d.iloc[::-1]  # чтобы самый большой был сверху на горизонтальном баре
    plt.figure(figsize=(10, 6))
    plt.barh(d[label_col].astype(str), d[value_col])
    save_fig(out_png, title=title, xlabel=value_col, ylabel=label_col)


# -----------------------------
# Main logic
# -----------------------------

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=90, help="window for time series")
    parser.add_argument("--topk", type=int, default=8, help="top K events to plot as time series")
    parser.add_argument("--out", type=str, default="", help="output dir (default auto)")
    args = parser.parse_args()

    host = os.getenv("POSTHOG_HOST", "https://us.posthog.com")
    key = os.getenv("POSTHOG_PERSONAL_API_KEY")
    project_id = os.getenv("POSTHOG_PROJECT_ID")

    if not key:
        raise SystemExit("Set env POSTHOG_PERSONAL_API_KEY")
    if not project_id:
        raise SystemExit("Set env POSTHOG_PROJECT_ID")

    # Output directory
    stamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    out_dir = Path(args.out) if args.out else Path(f"posthog_report_{stamp}")
    out_dir.mkdir(parents=True, exist_ok=True)

    # -----------------------------
    # 1) Core stats (period, counts)
    # -----------------------------
    q_meta = ph_query(
        host, key, project_id,
        f"""
        select
            min(timestamp) as min_ts,
            max(timestamp) as max_ts,
            count() as total_events_last_{args.days}d
        from events
        where timestamp >= now() - interval {args.days} day
        """,
        name="meta"
    )
    df_meta = to_df(q_meta)
    df_meta.to_csv(out_dir / "meta.csv", index=False)

    # -----------------------------
    # 2) Top events in window
    # -----------------------------
    q_top_events = ph_query(
        host, key, project_id,
        f"""
        select event, count() as cnt
        from events
        where timestamp >= now() - interval {args.days} day
        group by event
        order by cnt desc
        limit 200
        """,
        name="top_events"
    )
    df_top_events = to_df(q_top_events)
    df_top_events.to_csv(out_dir / f"top_events_{args.days}d.csv", index=False)

    # Plot top events bar
    fig_top_events = out_dir / "fig_top_events.png"
    plot_bar_top(df_top_events, "event", "cnt", fig_top_events, f"Top events (last {args.days} days)", topn=20)

    # -----------------------------
    # 3) Pageviews daily + DAU daily
    # -----------------------------
    q_pageviews_daily = ph_query(
        host, key, project_id,
        f"""
        select toDate(timestamp) as day, count() as pageviews
        from events
        where event = '$pageview'
          and timestamp >= now() - interval {args.days} day
        group by day
        order by day
        """,
        name="pageviews_daily"
    )
    df_pageviews = to_df(q_pageviews_daily)
    df_pageviews.to_csv(out_dir / f"pageviews_daily_{args.days}d.csv", index=False)
    plot_series(df_pageviews, "day", "pageviews", out_dir / "fig_pageviews_daily.png",
                f"$pageview / day (last {args.days} days)")

    q_dau = ph_query(
        host, key, project_id,
        f"""
        select toDate(timestamp) as day, count(distinct distinct_id) as dau
        from events
        where event = '$pageview'
          and timestamp >= now() - interval {args.days} day
        group by day
        order by day
        """,
        name="dau_daily"
    )
    df_dau = to_df(q_dau)
    df_dau.to_csv(out_dir / f"dau_daily_{args.days}d.csv", index=False)
    plot_series(df_dau, "day", "dau", out_dir / "fig_dau_daily.png",
                f"DAU (distinct_id with $pageview) / day (last {args.days} days)")

    # -----------------------------
    # 4) Time series for top K events
    # -----------------------------
    topk_events = df_top_events["event"].head(args.topk).tolist()

    # Важно: экранируем строки событий
    def hogql_quote(s: str) -> str:
        # Экранируем backslash и одиночную кавычку для ClickHouse/HogQL
        return "'" + s.replace("\\", "\\\\").replace("'", "\\'") + "'"

    events_to_plot = [
        "$pageview",
        "$web_vitals",
        "integration_landing_view",
        "$pageleave",
        "$autocapture",
        "integration_hero_secondary_cta_click",
    ]

    # Оставим только те, что реально есть в данных (чтобы запрос не был пустым)
    existing = set(df_top_events["event"].astype(str).tolist())
    events_to_plot_present = [e for e in events_to_plot if e in existing]

    if not events_to_plot_present:
        raise RuntimeError("Ни одно из заданных событий не найдено в данных за выбранное окно.")

    in_list = ", ".join(hogql_quote(e) for e in events_to_plot_present)

    q_ev_ts = ph_query(
        host, key, project_id,
        f"""
        select toDate(timestamp) as day, event, count() as cnt
        from events
        where timestamp >= now() - interval {args.days} day
          and event in ({in_list})
        group by day, event
        order by day asc, cnt desc
        """,
        name="events_timeseries_topk"
    )
    df_ev_ts = to_df(q_ev_ts)
    df_ev_ts.to_csv(out_dir / f"events_timeseries_top{args.topk}_{args.days}d.csv", index=False)

    # Совместный график
    plot_multi_event_timeseries(
        df_ev_ts,
        out_dir / "fig_events_timeseries_all.png",
        f"Events/day (selected events, last {args.days} days)"
    )

    # Отдельные графики по каждому событию
    for ev in events_to_plot_present:
        sub = df_ev_ts[df_ev_ts["event"] == ev][["day", "cnt"]].copy()
        # Если для события нет данных (на всякий), пропускаем
        if sub.empty:
            continue
        plot_series(
            sub,
            "day",
            "cnt",
            out_dir / f"fig_events_timeseries_{ev.replace('$', 'dollar_')}.png",
            f"{ev} / day (last {args.days} days)"
        )

    # -----------------------------
    # 5) Top URLs (pageviews) last N days
    # -----------------------------
    q_top_urls = ph_query(
        host, key, project_id,
        f"""
        select properties.$current_url as url, count() as views
        from events
        where event = '$pageview'
          and timestamp >= now() - interval {args.days} day
        group by url
        order by views desc
        limit 200
        """,
        name="top_urls"
    )
    df_urls = to_df(q_top_urls)
    df_urls.to_csv(out_dir / f"top_urls_{args.days}d.csv", index=False)
    plot_bar_top(df_urls, "url", "views", out_dir / "fig_top_urls.png",
                 f"Top URLs by $pageview (last {args.days} days)", topn=15)

    # -----------------------------
    # 6) Markdown report
    # -----------------------------
    meta = df_meta.iloc[0].to_dict() if len(df_meta) else {}
    min_ts = meta.get("min_ts")
    max_ts = meta.get("max_ts")
    total_events = meta.get(f"total_events_last_{args.days}d")

    top_events_md = df_top_events.head(15).to_markdown(index=False)
    top_urls_md = df_urls.head(15).to_markdown(index=False)

    report_md = f"""# PostHog mini-report

    **Generated (UTC):** {datetime.utcnow().isoformat(timespec="seconds")}  
    **Host:** {host}  
    **Project ID:** {project_id}  
    **Window:** last {args.days} days
    
    ## Snapshot
    - **Data range (in window):** min={min_ts}, max={max_ts}
    - **Total events (last {args.days}d):** {total_events}
    
    ## Top events (last {args.days}d)
    {top_events_md}
    
    ![Top events](fig_top_events.png)
    
    ## Time series
    ### $pageview / day
    ![Pageviews](fig_pageviews_daily.png)
    
    ### DAU / day (distinct_id with $pageview)
    ![DAU](fig_dau_daily.png)
    
    ### Top {args.topk} events/day
    Events plotted: {", ".join(events_to_plot_present)}
    ![Event TS](fig_events_timeseries.png)
    
    ## Top URLs by pageviews (last {args.days}d)
    {top_urls_md}
    
    ![Top URLs](fig_top_urls.png)
    
    ## Notes / interpretation hints (for upsell ranking)
    - Таймсерии помогут ловить **кампании/анонсы/пики** и связывать их с конверсионными событиями.
    - Для апсейла чаще всего полезны признаки:
      - **recency/frequency** по ключевым событиям
      - **pricing intent** (страницы pricing, checkout-started и т.п.)
      - **рост активности** (trend features) за 7/14/30 дней
    - Следующий шаг: добавить funnel (landing → pricing → signup/checkout), как только вы определите реальные имена custom events.
    """

    (out_dir / "report.md").write_text(report_md, encoding="utf-8")

    print(f"\nDone. Output dir: {out_dir.resolve()}")
    print(f"- Report: {out_dir / 'report.md'}")
    print(f"- Figures: {', '.join([p.name for p in out_dir.glob('fig_*.png')])}")
    print(f"- CSVs: {', '.join([p.name for p in out_dir.glob('*.csv')])}")


if __name__ == "__main__":
    main()