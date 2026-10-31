#!/usr/bin/env python3
"""Validate immutable fleet-review verdict markdown files."""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path


STATUS_RE = re.compile(r"STATUS:\s*(PASS|REQUEST-CHANGES|REVOKE)\s*\Z")
SHA_RE = re.compile(r"[0-9a-fA-F]{40}\Z")
FIELD_RE = re.compile(r"^([a-z_]+):\s*(.*)$")
REQUIRED = ("review_id", "card", "base_sha", "head_sha", "evidence")


@dataclass(frozen=True)
class Verdict:
    path: Path
    status: str
    fields: dict[str, str]


def parse_verdict(path: Path) -> Verdict:
    lines = path.read_text(encoding="utf-8-sig").splitlines()
    if not lines or not (status_match := STATUS_RE.fullmatch(lines[0].strip())):
        raise ValueError("first line must be STATUS: PASS|REQUEST-CHANGES|REVOKE")
    fields: dict[str, str] = {}
    current: str | None = None
    for line in lines[1:]:
        match = FIELD_RE.match(line)
        if match:
            current = match[1]
            fields[current] = match[2].strip()
        elif current == "evidence" and line.strip():
            fields[current] = "\n".join(filter(None, (fields[current], line.strip())))
    missing = [name for name in REQUIRED if not fields.get(name, "").strip()]
    if missing:
        raise ValueError(f"missing required field(s): {', '.join(missing)}")
    for name in ("base_sha", "head_sha"):
        if not SHA_RE.fullmatch(fields[name]):
            raise ValueError(f"{name} must be a 40-hex SHA")
    return Verdict(path, status_match[1], fields)


def validate_paths(paths: list[Path]) -> list[str]:
    errors: list[str] = []
    verdicts: list[Verdict] = []
    for path in paths:
        try:
            verdicts.append(parse_verdict(path))
        except (OSError, UnicodeError, ValueError) as exc:
            errors.append(f"{path}: {exc}")
    seen_ids: dict[str, Path] = {}
    active: dict[tuple[str, str], Verdict] = {}
    for verdict in verdicts:
        review_id = verdict.fields["review_id"]
        if review_id in seen_ids:
            errors.append(
                f"{verdict.path}: duplicate review_id {review_id} "
                f"(already used by {seen_ids[review_id]})"
            )
            continue
        seen_ids[review_id] = verdict.path
        key = (verdict.fields["card"], verdict.fields["head_sha"].lower())
        previous = active.get(key)
        if previous is not None:
            supersedes = verdict.fields.get("supersedes")
            if supersedes != previous.fields["review_id"]:
                errors.append(
                    f"{verdict.path}: active verdict already exists for card+head; "
                    f"supersedes must be {previous.fields['review_id']}"
                )
                continue
        active[key] = verdict
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("files", nargs="+", type=Path)
    args = parser.parse_args(argv)
    errors = validate_paths(args.files)
    for error in errors:
        print(error, file=sys.stderr)
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
