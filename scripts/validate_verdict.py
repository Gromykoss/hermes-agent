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
            if current in fields:
                raise ValueError(f"duplicate field: {current}")
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
    seen_ids: dict[str, Verdict] = {}
    for verdict in verdicts:
        review_id = verdict.fields["review_id"]
        if review_id in seen_ids:
            errors.append(
                f"{verdict.path}: duplicate review_id {review_id} "
                f"(already used by {seen_ids[review_id].path})"
            )
            continue
        seen_ids[review_id] = verdict

    superseded: set[str] = set()
    for verdict in seen_ids.values():
        target_id = verdict.fields.get("supersedes")
        if not target_id:
            continue
        target = seen_ids.get(target_id)
        if target is None:
            errors.append(f"{verdict.path}: supersedes unknown review_id {target_id}")
            continue
        key = (verdict.fields["card"], verdict.fields["head_sha"].lower())
        target_key = (target.fields["card"], target.fields["head_sha"].lower())
        if key != target_key:
            errors.append(f"{verdict.path}: supersedes must reference the same card+head")
            continue
        superseded.add(target_id)

    active: dict[tuple[str, str], list[Verdict]] = {}
    for review_id, verdict in seen_ids.items():
        if review_id in superseded or verdict.status == "REVOKE":
            continue
        key = (verdict.fields["card"], verdict.fields["head_sha"].lower())
        active.setdefault(key, []).append(verdict)
    for verdicts_for_key in active.values():
        if len(verdicts_for_key) > 1:
            paths = ", ".join(str(verdict.path) for verdict in verdicts_for_key)
            errors.append(f"{paths}: multiple active verdicts exist for card+head")
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
