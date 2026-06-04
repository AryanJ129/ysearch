"""Config loading: .env secrets + YAML criteria/companies, pydantic-validated."""

from __future__ import annotations

import os
from pathlib import Path

import yaml
from dotenv import dotenv_values
from pydantic import BaseModel, Field, field_validator

CONFIG_DIR = Path("config")


class QuerySpec(BaseModel):
    """One JSearch query. Plain YAML strings coerce to specs; `remote: false`
    marks an on-site pass (e.g. "AI product manager in Chennai")."""

    q: str
    remote: bool | None = None  # None → inherit Criteria.remote_ok


class Criteria(BaseModel):
    # Daily pass — run on EVERY scan. Keep small: each query = 1 quota request.
    queries: list[QuerySpec]
    # Rotation pool — rotate_per_scan of these run each scan, round-robin, so
    # on-site city passes are built into the cadence (not backlog).
    rotating_queries: list[QuerySpec] = Field(default_factory=list)
    rotate_per_scan: int = 2
    country: str = "in"
    remote_ok: bool = True
    locations: list[str] = Field(default_factory=list)
    # Scoring input only — flags below_floor / salary_unknown. NEVER a hard filter.
    salary_floor_lpa: float | None = None
    positive_keywords: list[str] = Field(default_factory=list)
    negative_keywords: list[str] = Field(default_factory=list)

    @field_validator("queries", "rotating_queries", mode="before")
    @classmethod
    def _coerce_strings(cls, value: list) -> list:
        return [{"q": item} if isinstance(item, str) else item for item in (value or [])]

    def resolved_remote(self, spec: QuerySpec) -> bool:
        return self.remote_ok if spec.remote is None else spec.remote

    def as_prompt_text(self) -> str:
        """Render for the scoring prompt."""
        all_queries = [s.q for s in self.queries + self.rotating_queries]
        parts = [
            f"Target roles (search queries): {', '.join(all_queries)}",
            f"Locations: {', '.join(self.locations) or 'any'}"
            + (" (remote OK)" if self.remote_ok else ""),
        ]
        if self.salary_floor_lpa is not None:
            parts.append(f"Salary floor: {self.salary_floor_lpa} LPA (flag, don't filter)")
        if self.positive_keywords:
            parts.append(f"Positive signals: {', '.join(self.positive_keywords)}")
        if self.negative_keywords:
            parts.append(f"Negative signals: {', '.join(self.negative_keywords)}")
        return "\n".join(parts)


class Companies(BaseModel):
    greenhouse: list[str] = Field(default_factory=list)
    lever: list[str] = Field(default_factory=list)
    ashby: list[str] = Field(default_factory=list)


def load_env(dotenv_path: Path | str | None = None) -> None:
    """Load .env with project-wins semantics.

    A filled .env value OVERRIDES a shell-exported var (so a per-project key
    beats a global one in ~/.zshrc), but blank .env lines (as shipped in
    .env.example) never clobber shell values. python-dotenv's default does
    neither: it silently ignores .env when the shell already exports the name.
    """
    values = dotenv_values(dotenv_path) if dotenv_path else dotenv_values()
    os.environ.update({k: v for k, v in values.items() if v})


def _load_yaml(path: Path) -> dict:
    if not path.exists():
        example = path.with_name(f"{path.stem}.example{path.suffix}")
        raise FileNotFoundError(f"{path} not found — copy {example} to {path.name} and edit it.")
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def load_criteria(path: Path | None = None) -> Criteria:
    return Criteria(**_load_yaml(path or CONFIG_DIR / "criteria.yaml"))


def load_companies(path: Path | None = None) -> Companies:
    return Companies(**_load_yaml(path or CONFIG_DIR / "companies.yaml"))
