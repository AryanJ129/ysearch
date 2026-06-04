"""First-run onboarding: AI-generated criteria from a freeform description.

One Haiku call on the USER'S OWN key (Settings tab collects it first). The
generated YAML is never auto-saved — it lands in the Settings editor for the
user to review, then save_criteria_yaml validates it.
"""

from __future__ import annotations

from ysearch import llm, prompts


def generate_criteria_yaml(description: str, resume_text: str | None = None) -> str:
    """Freeform description (+ optional resume) → criteria YAML text."""
    user = prompts.build_criteria_user_prompt(description, resume_text)
    text, cost = llm.chat(prompts.CRITERIA_SYSTEM, user, max_tokens=500)
    llm.log_cost("criteria", job_id=0, cost_usd=cost)
    # Strip markdown fences if the model added them despite instructions.
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    return text + "\n"
