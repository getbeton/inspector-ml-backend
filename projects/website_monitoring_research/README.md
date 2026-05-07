# Tracking 100 Prospect Websites — Tooling Research

**Goal:** continuously monitor ~100 prospective customer websites for changes to:

1. **Job ads** (careers / job board pages — hiring signals)
2. **Pricing structure** (pricing pages, plan tables)
3. **Documentation** (docs, changelogs, release notes)

This document maps the option space across three categories — **DIY**, **OSS plug-n-play (self-hosted)**, and **proprietary SaaS** — with pros, cons, pricing, and proof points from G2, Capterra, TechRadar, and Reddit.

---

## TL;DR

| If you want…                                              | Pick                                 | Approx. monthly cost (100 sites, ~3 pages each, daily checks) |
| --------------------------------------------------------- | ------------------------------------ | ------------------------------------------------------------- |
| Cheapest, full control, you own the plumbing              | **changedetection.io** (self-hosted) | $5–15 (VPS only)                                              |
| Plug-n-play SaaS, semantic alerts, non-technical users    | **Visualping** or **PageCrawl**      | $50–100                                                       |
| Most flexible monitor types (visual + DOM + tech stack)   | **Hexowatch**                        | $42–83                                                        |
| Sales-team-grade competitive intel with battlecards       | **Crayon / Klue / Kompyte**          | $300–5,000+ (annual contract)                                 |
| Bespoke logic, internal data warehouse, low marginal cost | **DIY (Playwright + cron + diff)**   | $5–30 in compute                                              |

---

## Category 1 — Proprietary SaaS

### Comparison table

| Tool              | Entry price (paid) | What 100 sites costs                              | Job-ad / pricing / docs fit                                                                                                                            | Strongest review signal                                                                                                                                                                                                                                                                          | Weakest review signal                                                                                                                                              |
| ----------------- | ------------------ | ------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|--------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| **Visualping**    | ~$10–14/mo         | ~$50–100/mo (Pro tier; visual + AI alerts)        | Visual screenshots + AI condition alerts ("only ping me if a new job role appears") — best for non-technical users and pricing-page tracking.          | #1 on G2 in Website Change Monitoring, Competitive Intelligence, Regulatory Change Management. Capterra users praise ease of setup. ([G2](https://www.g2.com/products/visualping/reviews), [Visualping reviews](https://visualping.io/reviews))                                                  | Free tier is "very limited"; users complain features keep shifting to higher tiers; layout-shift false positives. ([G2 reviews](https://www.g2.com/products/visualping/reviews)) |
| **Distill.io**    | $15/mo Starter     | ~$15–80/mo (Starter handles 50 monitors @ 10 min) | CSS/XPath selectors are surgical — best for picking out a single price element or a `<jobs>` list inside a page.                                       | 4.6/5 on G2; TechRadar 4.2/5; "Reddit loves it for stock alerts". Fine-grained selectors. ([TechRadar](https://www.techradar.com/reviews/distillio-web-content-monitoring), [G2 pros/cons](https://www.g2.com/products/distill-io/reviews?qs=pros-and-cons))                                     | Local mode dies when browser closes; Chrome-only extension; selector saving sometimes flaky; one Trustpilot reviewer reported a ~$100 surprise renewal charge.    |
| **Hexowatch**     | $12.49/mo Standard | $41.66/mo Business covers ~10k checks @ 5 min     | 13 monitor types — visual, source code, tech stack, content, availability, sitemap. Best if you want to track *both* docs (text) and pricing (visual). | Capterra: "team pumps out updates weekly"; lifetime AppSumo deal made it Reddit/G2-popular. ([Capterra](https://www.capterra.com/p/206900/Hexowatch/reviews/), [G2](https://www.g2.com/products/hexowatch/reviews))                                                                              | Learning curve, "many options for each monitor and most don't have clear documentation"; visual diff trips on tiny shifts. ([Capterra](https://www.capterra.com/p/206900/Hexowatch/reviews/)) |
| **Wachete**       | $5.40/mo Starter   | ~$5–55/mo (50 pages → 3,000 pages tier)           | Cheapest paid tier. Daily checks fine for hiring/pricing — too slow for breaking-news use cases.                                                       | "Among the cheapest plans in the industry" — TechRadar called it "a great budget option". ([TechRadar](https://www.techradar.com/pro/wachete-web-content-monitoring-review))                                                                                                                     | Older UI, weaker JS rendering than the leaders.                                                                                                                    |
| **ChangeTower**   | $15/mo Starter     | $35–80/mo Pro/Flexi for 100 monitors              | Has a dedicated "Monitor Jobs + Listings" use-case page; supports keyword + visual; built-in compliance archiving.                                     | TechRadar: "very customizable… any size of business". ([TechRadar](https://www.techradar.com/reviews/changetower-web-content-monitoring))                                                                                                                                                        | Smaller community than Visualping/Distill; UI feels dated.                                                                                                          |
| **PageCrawl.io**  | $8/mo Standard     | $8/mo Standard covers 100 pages + 15k checks      | Cheap, has AI importance scoring + REST API, built specifically around use-cases like "Job Listing & Career Page Monitoring".                          | Independent reviewers rate it a better-value Visualping alt. ([PageCrawl free guide](https://pagecrawl.io/blog/best-free-website-change-monitoring-tools))                                                                                                                                       | Newer, smaller player — fewer third-party reviews to triangulate.                                                                                                  |
| **UptimeRobot**   | Free / $7+         | Free tier 50 monitors @ 5 min                     | Best known for uptime, but supports keyword change. Use if budget is the only constraint.                                                              | "Free plan offers up to 50 monitors at 5-min intervals — most competitors limit free checks to once per hour or daily". ([UptimeRobot KB](https://uptimerobot.com/knowledge-hub/monitoring/9-best-website-change-monitoring-tools-compared/))                                                    | Designed for uptime, not change context — alerts are minimal, no diff visualization.                                                                                |
| **Fluxguard / Versionista** | $5.40/mo+ | ~$50–200/mo for 100+ pages                        | Compliance-grade archiving + page versioning; Versionista now part of Fluxguard (LegitScript acq. 2023).                                               | Used by enterprises for regulatory compliance.                                                                                                                                                                                                                                                   | Heavier and pricier than the modern crop.                                                                                                                          |

### Competitive-intelligence specialty SaaS (different category)

These are not just "page monitors" — they bundle careers/pricing/job-ads tracking with battlecards, win-loss, and CRM enrichment. Worth it only if a sales/PMM team is the primary consumer.

| Tool                | Pricing                                                                                                                                                                                                                              | Notes                                                                                                                                                                                                                                                                                                              |
| ------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| **Crayon**          | $20–45K/yr ([Vendr](https://www.vendr.com/marketplace/crayon))                                                                                                                                                                       | "Automatically tracks changes across competitor websites, pricing pages, job postings, and reviews" — closest fit to the brief, at enterprise price.                                                                                                                                                               |
| **Klue**            | $25–60K/yr ([Kompyte vs Klue/Crayon](https://www.kompyte.com/kompyte-klue-crayon-comparison))                                                                                                                                        | Strongest battlecards (G2 9.5/10). Sales-enablement focus.                                                                                                                                                                                                                                                         |
| **Kompyte**         | From $300/mo (~$3.6–15K/yr) — Semrush-owned ([Parano comparison](https://parano.ai/blog/crayon-vs-kompyte))                                                                                                                          | Cheapest of the three; auto-tracks websites, social, reviews, content, ads, **and job postings**.                                                                                                                                                                                                                  |
| **Contify**         | Custom (low 5-figure)                                                                                                                                                                                                                | News + regulatory + business-signal aggregator with AI summaries.                                                                                                                                                                                                                                                  |

---

## Category 2 — OSS plug-n-play (self-hosted)

### Comparison table

| Tool                  | License / Repo                            | Hosting cost       | Strengths for this brief                                                                                                                                                | Weaknesses                                                                                                                                                                                                  |
| --------------------- | ----------------------------------------- | ------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **changedetection.io** | Apache-2.0 ([dgtlmoon/changedetection.io](https://github.com/dgtlmoon/changedetection.io)) | $5–15/mo VPS       | Visual selector, **Browser Steps** (login flows, cookie banners), XPath/JSONPath/CSS, restock + price triggers, **85+ notification channels**, Playwright headless. Reddit-favorite. | Browser-rendered checks at scale need RAM — "hundreds of monitors" hits limits per page. Self-host = you maintain it. Hosted SaaS option exists at **$8.99/mo**. ([changedetection.io](https://changedetection.io/)) |
| **urlwatch**          | BSD-3 ([thp/urlwatch](https://github.com/thp/urlwatch)) | <$5/mo (cron box) | Pure-CLI, file-based config, jobs in YAML; ideal if you want to pipe diffs into Slack/email and store in git.                                                           | No GUI, no browser rendering by default → JS-heavy SaaS pricing pages won't render properly without `playwright` plugin glue.                                                                              |
| **Huginn**            | MIT ([huginn/huginn](https://github.com/huginn/huginn)) | $10–20/mo VPS    | "If-this-then-that" agents — chain a `WebsiteAgent` → `ChangeDetectorAgent` → `EmailAgent`. Most popular self-hosted urlwatch alt per AlternativeTo.                    | Heavier (Ruby on Rails), steeper learning curve; overkill for pure page diffing.                                                                                                                            |
| **Diffy / monitoro / ChangeBuffer** | various MIT/Apache              | varies             | Smaller / niche projects; usually don't beat changedetection.io on features.                                                                                            | Limited community, slower releases.                                                                                                                                                                         |

### What people actually say about changedetection.io

- **Reddit/r/Ubiquiti, /r/selfhosted**: "I was finally able to order a power distribution pro thanks to the changedetection.io docker container I finally got around to setting up." Frequently recommended for restock and price tracking. ([XDA Developers writeup](https://www.xda-developers.com/self-hosted-tool-perfect-for-monitoring-website-changes-price-drops/))
- **Resource cost**: lightweight idle (CPU <1%, ~100 MB RAM) — but each browser-rendered check is heavy. ([PageCrawl breakdown](https://pagecrawl.io/blog/changedetection-io-vs-pagecrawl-self-hosted-managed))
- **Trustpilot / SourceForge**: ~3-year users report "no complaints, the team is responsive, the service reliable, and everything wonderfully configurable". ([SourceForge reviews](https://sourceforge.net/projects/changedetection-io.mirror/reviews/))
- **31,000+ GitHub stars** and active weekly releases — signals project longevity.

---

## Category 3 — DIY (build it)

The build pattern is well-trodden and looks roughly like:

```
[ scheduler ]  ──→  [ fetcher ]  ──→  [ extractor ]  ──→  [ differ ]  ──→  [ store ]  ──→  [ notifier ]
   cron /            requests /         BeautifulSoup /     difflib /        S3 / DB /        Slack /
   EventBridge       Playwright         CSS+XPath           jsondiff         Postgres         email
```

Reference patterns:
- **[Oxylabs price-tracker tutorial](https://oxylabs.io/blog/how-to-build-a-price-tracker)** — Requests + BeautifulSoup + cron, ~50 lines for static sites.
- **[ScrapingBee Playwright guide](https://www.scrapingbee.com/blog/playwright-for-python-web-scraping/)** — for JS-heavy pages (most modern pricing/docs pages).
- **[Medium: Scrapy + Playwright + Cron](https://medium.com/@arslandevs/automate-web-scraping-with-scrapy-playwright-and-cron-a-powerful-combination-458f48fdba21)** — full automated pipeline.
- **[techwithtim/Price-Tracking-Web-Scraper](https://github.com/techwithtim/Price-Tracking-Web-Scraper)** — Bright Data + Playwright + React/Flask reference.

### DIY building-block costs (100 sites × 3 pages × daily check ≈ 9,000 checks/month)

| Component                    | Option                                    | Monthly cost                                                                                                  |
| ---------------------------- | ----------------------------------------- | ------------------------------------------------------------------------------------------------------------- |
| Compute                      | AWS Lambda (well within free 1M req tier) | $0–5 ([AWS Lambda pricing](https://aws.amazon.com/lambda/pricing/))                                            |
| Compute (alt)                | $5 VPS w/ cron (Hetzner / DigitalOcean)   | $5                                                                                                            |
| Headless browser if needed   | Self-hosted Playwright on same box        | $0                                                                                                            |
| Headless browser as service  | ScrapingBee / Browserless                 | $49+/mo for ~50K calls                                                                                         |
| Anti-bot proxies (if blocked)| Bright Data / Smartproxy residential      | $50–500+/mo                                                                                                    |
| Storage                      | S3 + Postgres                             | <$2                                                                                                            |
| Notifications                | Slack webhook / SES                       | <$1                                                                                                            |
| **Total realistic**          |                                           | **$5–30/mo** with no anti-bot, **$100–600/mo** if every site needs proxies                                     |

### Alternative SaaS-as-building-blocks (between OSS and full SaaS)

| Tool             | Pricing                                                                                                                                                                                                                                                            | Use as…                                                                                                                                                          |
| ---------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Browse AI**    | Free–$249/mo                                                                                                                                                                                                                                                       | Visual point-and-click "robot" trains on a page; cron → row in Google Sheets. Good for non-technical SDRs.                                                       |
| **ScrapingBee**  | $49–$599/mo ([pricing](https://www.scrapingbee.com/pricing/))                                                                                                                                                                                                      | API for headless browser + proxy. Pair with your own diff/store layer.                                                                                           |
| **Diffbot**      | Free 10K credits → $299/mo Startup → $899/mo Plus ([pricing](https://www.diffbot.com/pricing/))                                                                                                                                                                    | AI auto-extraction (no selectors); enterprise/knowledge-graph use cases.                                                                                         |

### DIY pros & cons

**Pros**
- Marginal cost per added site is ~$0; scaling 100 → 1,000 sites is mostly storage.
- Full control: store full HTML history, run NLP on diffs, push directly into your CRM/data warehouse.
- No vendor lock-in; no per-monitor pricing trap.
- Tailor logic per page type ("only alert when *job title* matches Director|VP|Head of").

**Cons**
- Engineering time to build + ~10–20% of an engineer's time to maintain (selector breakage, anti-bot rotation, JS rendering edge cases).
- You have to solve notification routing, deduping, login flows, and observability yourself.
- "Hidden" costs surface fast: residential proxies, headless-browser RAM, Captcha solvers.

---

## Pros & cons by category — at a glance

| Dimension                     | DIY                          | OSS plug-n-play (changedetection.io etc.) | Proprietary SaaS                |
| ----------------------------- | ---------------------------- | ----------------------------------------- | ------------------------------- |
| Time to first alert           | Days–weeks                   | Hours                                     | Minutes                         |
| Marginal cost per site        | ~$0                          | ~$0 (CPU/RAM only)                        | $0.10–$2/site/month             |
| Custom logic / NLP on diffs   | ★★★★★                        | ★★★ (limited)                             | ★★ (vendor's logic only)        |
| Maintenance burden            | High                         | Medium                                    | None                            |
| Data ownership / privacy      | ★★★★★                        | ★★★★★                                     | ★★ (data leaves your perimeter) |
| Anti-bot / JS handling        | You build it                 | Built-in (Playwright)                     | Built-in + proxies              |
| Non-technical user UI         | ★                            | ★★★ (changedetection has UI)              | ★★★★★                           |
| Total ~$ for 100 sites        | $5–30 + eng time             | $5–15 (VPS)                               | $50–500 ($300+ for CI suites)   |

---

## Recommendation for "100 prospect sites — jobs + pricing + docs"

1. **If the consumer is a sales / GTM team** and budget allows: start with **Visualping** ($50–100/mo) for the visual diffs + AI condition alerts on pricing/jobs, and pipe alerts into Slack. Upgrade to **Kompyte** if you want battlecards + CRM sync.
2. **If the consumer is a product / RevOps team that wants the data**: self-host **changedetection.io** on a $10 VPS. 100 sites × 3 pages is well within its comfort zone. Pipe diffs into Slack + a Postgres archive.
3. **If you want the diffs in your own warehouse for ML/scoring**: build DIY with **Playwright + cron + Postgres + Slack**. Add **ScrapingBee** only if/when bot blocking shows up. Reuse [techwithtim/Price-Tracking-Web-Scraper](https://github.com/techwithtim/Price-Tracking-Web-Scraper) as a starting skeleton.

A pragmatic hybrid: run **changedetection.io** as the capture layer, then write a small consumer that reads its API, classifies each diff (job/pricing/docs) with an LLM, and routes to the right channel. Cheapest path to "good enough" without locking into a vendor.

---

## Sources

### Roundups & comparisons
- [9 Best Website Change Monitoring Tools in 2026 — UptimeRobot KB](https://uptimerobot.com/knowledge-hub/monitoring/9-best-website-change-monitoring-tools-compared/)
- [12 Best Website Change Monitoring Tools (2026) — Guru99](https://www.guru99.com/monitor-websites-change-detection.html)
- [9 Best Tools to Monitor Website Changes in 2026 — Geekflare](https://geekflare.com/cybersecurity/monitor-website-changes/)
- [14 Best Free and Paid Tools to Monitor Website Changes — Oxylabs](https://oxylabs.io/blog/website-monitoring-tools)
- [Best website change monitoring software 2025 — TechRadar](https://www.techradar.com/best/best-online-content-monitoring-software)
- [Best Free Website Change Monitoring Tools 2026 — PageCrawl](https://pagecrawl.io/blog/best-free-website-change-monitoring-tools)
- [Distill vs ChangeDetection.io vs Visualping — MonitorSensei](https://www.monitorsensei.com/blog/distill-changedetection-visualping-vs-monitorsensei-url-vs-screen-2026)
- [Top Visualping Alternatives in 2025 — ChangeTower](https://changetower.com/visualping-alternative-2025/)
- [8 Best Distill Alternatives — Visualping](https://visualping.io/blog/distill-alternatives)
- [Visualping Review by an ex-CEO + Reddit/X comments — Nubela](https://nubela.co/blog/visualping-review/)

### Vendor / review-site pages
- [Visualping G2 reviews](https://www.g2.com/products/visualping/reviews)
- [Visualping Capterra (CA)](https://www.capterra.ca/software/211816/visualping)
- [Visualping reviews summary](https://visualping.io/reviews)
- [Distill.io G2 pros/cons](https://www.g2.com/products/distill-io/reviews?qs=pros-and-cons)
- [Distill.io TechRadar review](https://www.techradar.com/reviews/distillio-web-content-monitoring)
- [Distill.io Trustpilot](https://www.trustpilot.com/review/distill.io)
- [Distill.io pricing](https://distill.io/pricing/)
- [Hexowatch Capterra reviews](https://www.capterra.com/p/206900/Hexowatch/reviews/)
- [Hexowatch G2 reviews](https://www.g2.com/products/hexowatch/reviews)
- [Hexowatch pricing](https://www.capterra.com/p/206900/Hexowatch/pricing/)
- [ChangeTower TechRadar review](https://www.techradar.com/reviews/changetower-web-content-monitoring)
- [Wachete TechRadar review](https://www.techradar.com/pro/wachete-web-content-monitoring-review)
- [changedetection.io Trustpilot](https://www.trustpilot.com/review/changedetection.io)
- [changedetection.io SourceForge reviews](https://sourceforge.net/projects/changedetection-io.mirror/reviews/)
- [changedetection.io vs PageCrawl](https://pagecrawl.io/blog/changedetection-io-vs-pagecrawl-self-hosted-managed)
- [Self-hosting changedetection.io — Jussi Roine](https://jussiroine.com/2023/12/self-hosting-changedetection-io-for-monitoring-websites/)
- [Track changes with ChangeDetection — Techno Tim](https://technotim.com/posts/change-detection-docker/)
- [XDA Developers — self-hosted changedetection.io](https://www.xda-developers.com/self-hosted-tool-perfect-for-monitoring-website-changes-price-drops/)
- [urlwatch alternatives — AlternativeTo](https://alternativeto.net/software/urlwatch/)

### Competitive-intel SaaS
- [Kompyte vs Klue vs Crayon comparison](https://www.kompyte.com/kompyte-klue-crayon-comparison)
- [Crayon Software Pricing — Vendr](https://www.vendr.com/marketplace/crayon)
- [Crayon vs Kompyte 2026 — Parano](https://parano.ai/blog/crayon-vs-kompyte)
- [Top Crayon Alternatives 2026 — Contify](https://www.contify.com/resources/blog/crayon-alternatives/)
- [Klue vs Crayon — Klue](https://klue.com/klue-vs-crayon)
- [Competitive Intelligence Tools Compared 2026 — Elevated Signal](https://elevatedsignal.com/compare/)

### DIY references
- [How to Build a Price Tracker With Python — Oxylabs](https://oxylabs.io/blog/how-to-build-a-price-tracker)
- [Playwright for Python Web Scraping Tutorial — ScrapingBee](https://www.scrapingbee.com/blog/playwright-for-python-web-scraping/)
- [Automate Web Scraping with Scrapy, Playwright, and Cron — Medium](https://medium.com/@arslandevs/automate-web-scraping-with-scrapy-playwright-and-cron-a-powerful-combination-458f48fdba21)
- [techwithtim/Price-Tracking-Web-Scraper — GitHub](https://github.com/techwithtim/Price-Tracking-Web-Scraper)
- [AWS Lambda as a website monitoring tool — Luke Dorosz / Medium](https://medium.com/@mrdoro/aws-lambda-as-the-website-monitoring-tool-184b09202ae2)
- [AWS Lambda pricing](https://aws.amazon.com/lambda/pricing/)
- [Diffbot pricing](https://www.diffbot.com/pricing/)
- [ScrapingBee pricing](https://www.scrapingbee.com/pricing/)

### Job-posting use case
- [Competitor Job Posting Monitoring — PageCrawl](https://pagecrawl.io/blog/competitor-job-posting-monitoring-hiring-signals)
- [How to Get Job Alerts on Any Company's Careers Page — Visualping](https://visualping.io/blog/how-to-get-job-alerts)
- [Job Posting Tracking Guide — Distill](https://distill.io/blog/hr-and-recruitment/)
- [ChangeTower: Monitor Jobs + Listings](https://changetower.com/monitor-jobs/)
- [How to Monitor Job Postings on Any Website — ChangeNotifier](https://changenotifier.com/guides/monitor-job-postings-website)
