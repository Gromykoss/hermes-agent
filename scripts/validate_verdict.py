#!/usr/bin/env python3
"""Validate immutable review-verdict markdown files.

Two formats are accepted (auto-detected):

1. Fleet-reviews canon v1.3 — JSON frontmatter between ``---`` fences
   (status / reviewed_range / spec_hash / evidence / findings / notes /
   escalations), optionally extended with review_id / card / base_sha /
   head_sha / verdict / supersedes.
2. The factory card format — first line ``STATUS: PASS|REQUEST-CHANGES|REVOKE``
   followed by ``key: value`` fields.

Cross-file rules for both formats: unique review_id, supersedes must reference
a known verdict on the same card+head, supersedes cycles (A→A, A↔B) are an
error, and at most one active verdict may exist per card+head (a REVOKE or a
superseded verdict is not active). Unknown keys are an error in both formats.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path


STATUS_RE = re.compile(r"STATUS:\s*(PASS|REQUEST-CHANGES|REVOKE)\s*\Z")
SHA_RE = re.compile(r"[0-9a-fA-F]{40}\Z")
FIELD_RE = re.compile(r"^([a-z_]+):\s*(.*)$")

CANON_SEVERITIES = {"blocker", "major", "minor"}
CANON_STATUSES = {"ok", "issues", "incomplete"}

# Fields of the legacy line format (the STATUS first line carries the status).
LEGACY_ALLOWED = {"review_id", "card", "base_sha", "head_sha", "evidence", "supersedes"}
# Top-level keys of the JSON canon (canon v1.3 plus the extension keys the
# corpus actually uses). A typo like "supersede" is an error, not a silent drop.
JSON_ALLOWED = {
    "status", "verdict", "review_id", "card", "base_sha", "head_sha", "task",
    "triage", "reviewed_range", "spec_hash", "spec_hash_algo", "spec_ref",
    "evidence", "findings", "notes", "escalations", "supersedes",
    "superseded_by", "reverify_of", "correction", "corrections", "carried_open",
    "acceptance_ref", "cutover_readiness", "issue_url", "post_verdict_heads",
    "pr", "review",
}


@dataclass(frozen=True)
class Verdict:
    path: Path
    status: str  # normalized: PASS | REQUEST-CHANGES | REVOKE
    fields: dict[str, str] = field(default_factory=dict)


def _normalize_status(verdict: object, status: object) -> str:
    """Map a canon verdict/status value onto PASS|REQUEST-CHANGES|REVOKE."""
    text = str(verdict if verdict not in (None, "") else status or "").strip()
    head = re.split(r"[\s:;,.]", text, maxsplit=1)[0].lower()
    if head in ("pass", "accept", "accepted", "approved", "ok"):
        return "PASS"
    if head in ("revoke", "revoked"):
        return "REVOKE"
    return "REQUEST-CHANGES"


def _validate_findings(payload: dict) -> None:
    findings = payload.get("findings", [])
    if not isinstance(findings, list):
        raise ValueError("findings must be a list")
    for index, finding in enumerate(findings):
        if not isinstance(finding, dict):
            raise ValueError(f"findings[{index}] must be an object")
        if finding.get("severity") not in CANON_SEVERITIES:
            raise ValueError(
                f"findings[{index}].severity must be one of "
                f"{'|'.join(sorted(CANON_SEVERITIES))}"
            )
        if not str(finding.get("file") or "").strip():
            raise ValueError(f"findings[{index}].file is required")
        if not isinstance(finding.get("line"), int):
            raise ValueError(f"findings[{index}].line must be an integer")


# Form A (preferred): sha256:<64 hex> with optional single-line annotation
# of balanced parens (any nesting depth), optionally followed by a
# descriptive tail with balanced parens too (real canon corpus:
# "(algo …); срез-пакет tar-sha256 …").
_SPEC_HASH_A_RE = re.compile(r"sha256:[0-9a-fA-F]{64}\s.+\Z")
# Form B (legacy): sha256:<non-empty single-line text>, ≤200 chars after prefix.
_SPEC_HASH_PREFIX = "sha256:"
_SPEC_HASH_B_MAX = 200
# Existing alternative: 40-hex blob SHA (optional trailing annotation after space).
_SPEC_HASH_BLOB_RE = re.compile(r"[0-9a-fA-F]{40}(?:\s|\Z)")


def _valid_spec_hash(value: object) -> bool:
    """Accept Form A, Form B, or a 40-hex blob SHA; reject empty/multiline/unprefixed."""
    if not isinstance(value, str):
        return False
    text = value.strip()
    if not text or "\n" in text or "\r" in text:
        return False
    if _SPEC_HASH_BLOB_RE.match(text):
        return True
    m = _SPEC_HASH_A_RE.fullmatch(text)
    if m:
        # Tail after the 64-hex head: must carry a real character, have
        # balanced parens overall (unbalanced "(", ")" → rejected — covers
        # nested parens of any depth AND the old flat-only regex), ≤500.
        tail = text[len("sha256:") + 64:]
        if not re.search(r"[^\s()]", tail) or len(tail) > 500:
            return False
        depth = 0
        for ch in tail:
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
            if depth < 0:
                return False
        return depth == 0
    if text.startswith(_SPEC_HASH_PREFIX):
        rest = text[len(_SPEC_HASH_PREFIX):]
        # Form A: plain 64-hex (no annotation) — canonical preferred form.
        if re.fullmatch(r"[0-9a-fA-F]{64}", rest):
            return True
        # Form B / descriptive: reject empty / whitespace-only after the
        # prefix; cap legacy length. (No hex-guard: per WP3 Form B is any
        # non-empty single-line text; a near-64-hex string is accepted as
        # legacy text — corpus has 0 such cases, spec and code agree.)
        if not bool(re.search(r"[^\s()]", rest)) or len(rest) > _SPEC_HASH_B_MAX:
            return False
        return True
    return False


def _parse_json_verdict(path: Path, lines: list[str]) -> Verdict:
    closing = next(
        (i for i, line in enumerate(lines[1:], start=1) if line.strip() == "---"),
        None,
    )
    if closing is None:
        raise ValueError("JSON frontmatter is not closed by a second --- fence")
    try:
        payload = json.loads("\n".join(lines[1:closing]))
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON frontmatter: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError("JSON frontmatter must be an object")

    unknown = sorted(set(payload) - JSON_ALLOWED)
    if unknown:
        raise ValueError(f"unknown field(s): {', '.join(unknown)}")

    status = payload.get("status")
    if status not in CANON_STATUSES:
        raise ValueError("status must be ok | issues | incomplete")
    escalations = payload.get("escalations", [])
    notes = payload.get("notes", [])
    if not isinstance(escalations, list) or not isinstance(notes, list):
        raise ValueError("notes and escalations must be lists")
    if escalations and status != "issues":
        raise ValueError("non-empty escalations requires status: issues")
    if status == "ok" and not (payload.get("evidence") or ""):
        raise ValueError("status ok requires a filled evidence block")
    _validate_findings(payload)

    spec_hash = payload.get("spec_hash")
    if spec_hash is not None and not _valid_spec_hash(spec_hash):
        raise ValueError(
            "spec_hash must be sha256:<64 hex> [optional (annotation)], "
            "sha256:<legacy text ≤200>, or a 40-hex blob SHA"
        )

    range_ = payload.get("reviewed_range")
    if isinstance(range_, dict):
        base = range_.get("base") or payload.get("base_sha")
        head = range_.get("head") or payload.get("head_sha")
    elif range_ is None:
        base, head = payload.get("base_sha"), payload.get("head_sha")
    else:
        raise ValueError("reviewed_range must be an object")
    for name, value in (("base", base), ("head", head)):
        if not isinstance(value, str) or not SHA_RE.fullmatch(value.strip()):
            raise ValueError(f"reviewed_range {name} must be a 40-hex SHA")

    card = payload.get("card") or path.parent.name
    review_id = payload.get("review_id") or path.stem
    fields = {
        "review_id": str(review_id).strip(),
        "card": str(card).strip(),
        "base_sha": str(base).strip(),
        "head_sha": str(head).strip(),
        "supersedes": str(payload.get("supersedes") or "").strip(),
    }
    return Verdict(path, _normalize_status(payload.get("verdict"), status), fields)


def _parse_legacy_verdict(path: Path, lines: list[str]) -> Verdict:
    if not (status_match := STATUS_RE.fullmatch(lines[0].strip())):
        raise ValueError("first line must be STATUS: PASS|REQUEST-CHANGES|REVOKE")
    fields: dict[str, str] = {}
    current: str | None = None
    for line in lines[1:]:
        match = FIELD_RE.match(line)
        if match:
            current = match[1]
            if current not in LEGACY_ALLOWED:
                raise ValueError(f"unknown field: {current}")
            if current in fields:
                raise ValueError(f"duplicate field: {current}")
            fields[current] = match[2].strip()
        elif current == "evidence" and line.strip():
            fields[current] = "\n".join(filter(None, (fields[current], line.strip())))
    missing = [
        name for name in ("review_id", "card", "base_sha", "head_sha", "evidence")
        if not fields.get(name, "").strip()
    ]
    if missing:
        raise ValueError(f"missing required field(s): {', '.join(missing)}")
    for name in ("base_sha", "head_sha"):
        if not SHA_RE.fullmatch(fields[name]):
            raise ValueError(f"{name} must be a 40-hex SHA")
    return Verdict(path, status_match[1], fields)


def parse_verdict(path: Path) -> Verdict:
    lines = path.read_text(encoding="utf-8-sig").splitlines()
    while lines and not lines[0].strip():
        lines = lines[1:]
    if not lines:
        raise ValueError("file is empty")
    if lines[0].strip() == "---":
        return _parse_json_verdict(path, lines)
    return _parse_legacy_verdict(path, lines)


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

    # supersedes edges: unknown target, cross-card, self-reference, cycles.
    for review_id, verdict in seen_ids.items():
        target_id = verdict.fields.get("supersedes")
        if not target_id:
            continue
        if target_id == review_id:
            errors.append(f"{verdict.path}: supersedes itself ({target_id})")
            continue
        target = seen_ids.get(target_id)
        if target is None:
            errors.append(f"{verdict.path}: supersedes unknown review_id {target_id}")
            continue
        key = (verdict.fields["card"], verdict.fields["head_sha"].lower())
        target_key = (target.fields["card"], target.fields["head_sha"].lower())
        if key != target_key:
            errors.append(f"{verdict.path}: supersedes must reference the same card+head")

    def _key(v: Verdict) -> tuple[str, str]:
        return (v.fields["card"], v.fields["head_sha"].lower())

    for start_id, verdict in seen_ids.items():
        walked: set[str] = {start_id}
        current_id = start_id
        while True:
            next_id = seen_ids[current_id].fields.get("supersedes") or ""
            if not next_id or next_id not in seen_ids or next_id == current_id:
                break
            if next_id in walked:
                errors.append(f"{verdict.path}: supersedes cycle involving {next_id}")
                break
            walked.add(next_id)
            current_id = next_id

    superseded: set[str] = set()
    for verdict in seen_ids.values():
        target_id = verdict.fields.get("supersedes")
        if not target_id or target_id == verdict.fields["review_id"]:
            continue
        target = seen_ids.get(target_id)
        if target is not None and _key(target) == _key(verdict):
            superseded.add(target_id)

    active: dict[tuple[str, str], list[Verdict]] = {}
    for review_id, verdict in seen_ids.items():
        if review_id in superseded or verdict.status == "REVOKE":
            continue
        active.setdefault(_key(verdict), []).append(verdict)
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
