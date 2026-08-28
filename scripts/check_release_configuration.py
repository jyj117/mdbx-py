#!/usr/bin/env python3
"""Reject a release while public project and security contacts are placeholders."""

from __future__ import annotations

import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
REQUIRED_URLS = {"Repository", "Issues", "Changelog"}


def project_urls(text: str) -> dict[str, str]:
    match = re.search(r"(?ms)^\[project\.urls\]\s*$\n(.*?)(?=^\[|\Z)", text)
    if match is None:
        return {}
    return dict(re.findall(r'(?m)^([A-Za-z][A-Za-z0-9_-]*)\s*=\s*"([^"]+)"\s*$', match.group(1)))


def validate(root: pathlib.Path) -> list[str]:
    urls = project_urls((root / "pyproject.toml").read_text(encoding="utf-8"))
    errors: list[str] = []
    for name in sorted(REQUIRED_URLS):
        value = urls.get(name, "")
        if not value.startswith("https://") or any(marker in value.lower() for marker in ("example.", "todo", "replace-me")):
            errors.append(f"pyproject.toml [project.urls] needs a real HTTPS {name} URL")

    security = (root / "SECURITY.md").read_text(encoding="utf-8").lower()
    if "must replace this sentence" in security or "eventual repository" in security:
        errors.append("SECURITY.md needs a concrete monitored private reporting channel")
    return errors


def main() -> int:
    errors = validate(ROOT)
    if errors:
        print("\n".join(errors), file=sys.stderr)
        return 1
    print("release repository URLs and private security reporting channel are configured")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
