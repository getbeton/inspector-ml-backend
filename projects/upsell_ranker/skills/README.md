# Agent Skills for upsell_ranker

Expert-practice playbooks exposed to ADK agents as Agent Skills. Skills here are **shared across versions** of the upsell_ranker project — a future `versions/v0.0.3/` agent reuses this directory by default.

Each skill is loaded by `projects/upsell_ranker/versions/v0.0.2/shared/skills.py` via the native `google.adk.skills.load_skill_from_dir` (added in google-adk 1.25.0; status: experimental).

See: <https://adk.dev/skills/> and <https://agentskills.io/specification>.

## How to add a new skill

1. Create a kebab-case directory: `projects/upsell_ranker/skills/<your-skill-name>/`.
2. Add `SKILL.md` with YAML frontmatter:

   ```markdown
   ---
   name: your-skill-name
   description: One sentence covering what this skill does AND when an agent should reach for it. The model sees this description before loading the body, so the trigger phrasing must be precise.
   ---

   <body — the actual playbook, loaded only when an agent calls load_skill>
   ```

3. (Optional) Add longer-form material under `references/`, `assets/`, or `scripts/` subdirs — agents pull these via `load_skill_resource` only when needed.

## Naming convention

- Directory name == frontmatter `name`. Both kebab-case, lowercase, <= 64 characters.
- Use action-oriented names that describe the practice (`rice-prioritization`, not `rice`).

## When to split into references/

Rule of thumb: if `SKILL.md` body exceeds ~1k words, move detailed formulas, tables, or sub-playbooks into `references/<topic>.md`. Keep the SKILL.md body to the conceptual overview + decision rubric + a one-line pointer to the reference.

Current example: `cohort-retention-analysis/` keeps the conceptual framing in `SKILL.md` and moves the six concrete comparison-metric formulas + the moving-median formula into `references/retention-metrics.md`.

## Env knobs (read by shared/skills.py)

- `UPSELL_SKILLS_ENABLED` — set to `0`, `false`, `no`, or `off` to disable skills entirely (no toolset attached; agents fall back to their normal function-tool list). Default: enabled.
- `UPSELL_SKILLS_DIR` — absolute path override of this directory. Default: this directory, resolved from `shared/skills.py`.

## What each skill is for

- `rice-prioritization/` — Score and rank candidate signals or hypotheses by Reach x Impact x Confidence / Effort.
- `hypotheses-generation/` — Founding-sales playbook (Kazanjy) for framing sales-actionable hypotheses in pre-PMF B2B SaaS.
- `cohort-retention-analysis/` — PostHog cohort/retention playbook with conceptual framing in SKILL.md and concrete metric formulas in references/retention-metrics.md.
- `monetization-pricing/` — Verna/Reforge monetization framework (value metric, WTP, CAC, LTV:CAC, NDR).
- `founding-sales/` — Compact subset of the sales playbook focused on problem-solution narrative, ICP qualification, and pipeline stages.
- `running-lean/` — Maurya's Lean Canvas, three stages, experiment design, and actionable-vs-vanity metrics.
