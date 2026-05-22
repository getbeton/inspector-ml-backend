"""Skill loader for upsell_ranker agents.

Wraps ADK's native Agent Skills (added in google-adk 1.25.0; status: experimental)
so agent wiring can stay a single line and so a missing/disabled skills directory
never crashes startup.

Layout assumed at <repo_root>/projects/upsell_ranker/skills/<skill-name>/SKILL.md
(plus optional references/, assets/, scripts/ per the agentskills.io spec).

Env knobs:
- UPSELL_SKILLS_ENABLED: falsy ("0"/"false"/"no") -> return None (no toolset).
- UPSELL_SKILLS_DIR: absolute path override of the default skills directory.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Optional

from google.adk.skills import load_skill_from_dir
from google.adk.tools.skill_toolset import SkillToolset

logger = logging.getLogger(__name__)

_FALSY = {"0", "false", "no", "off", ""}

# Tools SkillToolset exposes by default that we suppress.
# run_skill_script needs a `scripts/` dir + a code_executor; none of our skills
# carry scripts, so the tool only invites the model to hallucinate script names
# (e.g. "finalize_experiment_report.py") and waste a round-trip on SCRIPT_NOT_FOUND.
_SUPPRESSED_TOOLS = frozenset({"run_skill_script"})


class _FilteredSkillToolset(SkillToolset):
    async def get_tools(self, readonly_context=None):
        tools = await super().get_tools(readonly_context)
        return [t for t in tools if t.name not in _SUPPRESSED_TOOLS]


def _default_skills_dir() -> Path:
    # shared/skills.py -> versions/v0.0.2 -> versions -> upsell_ranker -> skills/
    return Path(__file__).resolve().parents[3] / "skills"


def _is_enabled() -> bool:
    return os.getenv("UPSELL_SKILLS_ENABLED", "1").strip().lower() not in _FALSY


def build_skill_toolset(
    skills_dir: Optional[Path] = None,
    additional_tools: Optional[list[Any]] = None,
) -> Optional[SkillToolset]:
    """Return a configured SkillToolset, or None if disabled/empty/missing.

    Callers wire it in as ``tools=[*existing, *([toolset] if toolset else [])]``
    so that a None result simply means the agent uses its normal tool list.
    """
    if not _is_enabled():
        logger.info(
            "skills disabled (UPSELL_SKILLS_ENABLED=%s)",
            os.getenv("UPSELL_SKILLS_ENABLED"),
        )
        return None

    resolved = skills_dir or Path(os.getenv("UPSELL_SKILLS_DIR") or _default_skills_dir())
    if not resolved.is_dir():
        logger.info("skills dir not found: %s", resolved)
        return None

    loaded = []
    for child in sorted(resolved.iterdir()):
        if not child.is_dir():
            continue
        if not (child / "SKILL.md").is_file():
            continue
        try:
            loaded.append(load_skill_from_dir(child))
        except Exception as exc:
            logger.warning("skipping malformed skill %s: %s", child.name, exc)

    if not loaded:
        logger.info("no valid skills found in %s", resolved)
        return None

    names = ", ".join(s.frontmatter.name for s in loaded)
    logger.info("loaded skills: %s", names)

    return _FilteredSkillToolset(skills=loaded, additional_tools=list(additional_tools or []))
