"""Config loading: .env secrets + YAML criteria/companies, pydantic-validated."""

from __future__ import annotations

from pathlib import Path

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field

CONFIG_DIR = Path("config")


class Criteria(BaseModel):
    queries: list[str]
    country: str = "in"
    remote_ok: bool = True
    locations: list[str] = Field(default_factory=list)
    # Scoring input only — flags below_floor / salary_unknown. NEVER a hard filter.
    salary_floor_lpa: float | None = None
    positive_keywords: list[str] = Field(default_factory=list)
    negative_keywords: list[str] = Field(default_factory=list)

    def as_prompt_text(self) -> str:
        """Render for the scoring prompt."""
        parts = [
            f"Target queries: {', '.join(self.queries)}",
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


def load_env() -> None:
    load_dotenv()


def _load_yaml(path: Path) -> dict:
    if not path.exists():
        example = path.with_name(f"{path.stem}.example{path.suffix}")
        raise FileNotFoundError(f"{path} not found — copy {example} to {path.name} and edit it.")
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def load_criteria(path: Path | None = None) -> Criteria:
    return Criteria(**_load_yaml(path or CONFIG_DIR / "criteria.yaml"))


def load_companies(path: Path | None = None) -> Companies:
    return Companies(**_load_yaml(path or CONFIG_DIR / "companies.yaml"))
