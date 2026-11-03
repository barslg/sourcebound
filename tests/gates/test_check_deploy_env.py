"""Gates for scripts/check_deploy_env.py: a deploy .env that drifts from the catalogue is red."""

from __future__ import annotations

from pathlib import Path

from gate_tree import ROOT, load_script, write_tree

checker = load_script("check_deploy_env")


def _tree(tmp_path: Path, workflow: str | None = None, config: str | None = None) -> Path:
    files = {
        ".env.example": (ROOT / ".env.example").read_text(encoding="utf-8"),
        "src/app/config.py": config or (ROOT / "src/app/config.py").read_text(encoding="utf-8"),
        ".github/workflows/deploy.yml": workflow
        or (ROOT / checker.WORKFLOW).read_text(encoding="utf-8"),
    }
    return write_tree(tmp_path, files)


def test_real_deploy_env_matches_the_catalogue(tmp_path: Path) -> None:
    """The workflow writes every production-required and sign-in name, all from .env.example."""
    checked, problems = checker.check(_tree(tmp_path))
    assert problems == [] and checked >= len(checker.AUTH_NAMES) + 2


def test_renamed_setting_is_red(tmp_path: Path) -> None:
    """A deploy still writing an old name after a rename in config.py is red twice."""
    workflow = (ROOT / checker.WORKFLOW).read_text(encoding="utf-8")
    mutant = workflow.replace("GOOGLE_OAUTH_CLIENT_SECRET=", "GOOGLE_CLIENT_SECRET=")
    _, problems = checker.check(_tree(tmp_path, workflow=mutant))
    assert [p.split(": ", 1)[1] for p in problems] == [
        "GOOGLE_CLIENT_SECRET is not in .env.example",
        "GOOGLE_OAUTH_CLIENT_SECRET is required in production and not written",
    ]


def test_new_production_requirement_is_red(tmp_path: Path) -> None:
    """A setting config.py starts refusing to boot without must be written by the deploy."""
    config = (ROOT / "src/app/config.py").read_text(encoding="utf-8")
    mutant = config + '\n_ = "NEW_REQUIRED_THING must be set in this APP_ENV"\n'
    _, problems = checker.check(_tree(tmp_path, config=mutant))
    assert problems == [
        f"{checker.WORKFLOW}: NEW_REQUIRED_THING is required in production and not written"
    ]
