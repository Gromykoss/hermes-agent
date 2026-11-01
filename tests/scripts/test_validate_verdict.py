from pathlib import Path

from scripts.validate_verdict import main


SHA_A = "a" * 40
SHA_B = "b" * 40


def _write(path: Path, review_id: str, *, head_sha=SHA_B, supersedes=None, status="PASS"):
    lines = [
        f"STATUS: {status}",
        f"review_id: {review_id}",
        "card: P1-42",
        f"base_sha: {SHA_A}",
    ]
    if head_sha is not None:
        lines.append(f"head_sha: {head_sha}")
    lines.extend(["evidence:", "  - real-adapter suite: 3 passed"])
    if supersedes:
        lines.append(f"supersedes: {supersedes}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def test_valid_verdict_passes(tmp_path):
    assert main([str(_write(tmp_path / "one.md", "review-1"))]) == 0


def test_missing_head_sha_fails(tmp_path):
    assert main([str(_write(tmp_path / "one.md", "review-1", head_sha=None))]) == 1


def test_duplicate_card_head_without_supersedes_fails(tmp_path):
    first = _write(tmp_path / "one.md", "review-1")
    second = _write(tmp_path / "two.md", "review-2")
    assert main([str(first), str(second)]) == 1


def test_duplicate_card_head_with_supersedes_passes(tmp_path):
    first = _write(tmp_path / "one.md", "review-1")
    second = _write(tmp_path / "two.md", "review-2", supersedes="review-1")
    assert main([str(first), str(second)]) == 0
    assert main([str(second), str(first)]) == 0


def test_duplicate_field_fails(tmp_path):
    path = _write(tmp_path / "one.md", "review-1")
    path.write_text(path.read_text() + f"head_sha: {SHA_A}\n", encoding="utf-8")
    assert main([str(path)]) == 1


def test_unknown_supersedes_fails(tmp_path):
    path = _write(tmp_path / "one.md", "review-1", supersedes="missing")
    assert main([str(path)]) == 1


def test_revoke_deactivates_superseded_verdict(tmp_path):
    old = _write(tmp_path / "old.md", "review-1")
    revoke = _write(
        tmp_path / "revoke.md", "review-2", supersedes="review-1", status="REVOKE",
    )
    replacement = _write(tmp_path / "new.md", "review-3")
    assert main([str(replacement), str(revoke), str(old)]) == 0


# ---------------------------------------------------------------------------
# Fleet-reviews canon v1.3 (JSON frontmatter)
# ---------------------------------------------------------------------------

import json


def _write_json(
    path: Path,
    review_id: str,
    *,
    head_sha=SHA_B,
    base_sha=SHA_A,
    status="ok",
    verdict=None,
    supersedes=None,
    findings=None,
    escalations=None,
    evidence=None,
    extra=None,
):
    payload = {
        "status": status,
        "reviewed_range": {"base": base_sha, "head": head_sha},
        "spec_hash": f"sha256:{'c' * 64}",
        "evidence": evidence if evidence is not None else {"tool": "git"},
        "findings": findings or [],
        "notes": [],
        "escalations": escalations or [],
    }
    if verdict is not None:
        payload["verdict"] = verdict
    if supersedes:
        payload["supersedes"] = supersedes
    if extra:
        payload.update(extra)
    body = json.dumps(payload, ensure_ascii=False, indent=2)
    path.write_text(f"---\n{body}\n---\n\n# Резюме\n", encoding="utf-8")
    return path


def test_json_canon_verdict_passes(tmp_path):
    assert main([str(_write_json(tmp_path / "one.md", "review-1"))]) == 0


def test_json_canon_without_head_sha_fails(tmp_path):
    path = _write_json(tmp_path / "one.md", "review-1")
    payload = json.loads(path.read_text().split("---\n")[1])
    del payload["reviewed_range"]["head"]
    body = json.dumps(payload, ensure_ascii=False, indent=2)
    path.write_text(f"---\n{body}\n---\n", encoding="utf-8")
    assert main([str(path)]) == 1


def test_json_canon_duplicate_card_head_without_supersedes_fails(tmp_path):
    first = _write_json(tmp_path / "one.md", "review-1")
    second = _write_json(tmp_path / "two.md", "review-2")
    assert main([str(first), str(second)]) == 1


def test_json_canon_supersedes_chain_passes_both_orders(tmp_path):
    first = _write_json(tmp_path / "review-1.md", "review-1")
    second = _write_json(tmp_path / "review-2.md", "review-2", supersedes="review-1")
    assert main([str(first), str(second)]) == 0
    assert main([str(second), str(first)]) == 0


def test_json_canon_self_supersedes_fails(tmp_path):
    path = _write_json(tmp_path / "review-1.md", "review-1", supersedes="review-1")
    assert main([str(path)]) == 1


def test_json_canon_mutual_supersedes_cycle_fails(tmp_path):
    a = _write_json(tmp_path / "review-1.md", "review-1", supersedes="review-2")
    b = _write_json(tmp_path / "review-2.md", "review-2", supersedes="review-1")
    assert main([str(a), str(b)]) == 1


def test_json_canon_legacy_self_supersedes_fails(tmp_path):
    path = _write(tmp_path / "one.md", "review-1", supersedes="review-1")
    assert main([str(path)]) == 1


def test_json_canon_unknown_key_fails(tmp_path):
    path = _write_json(tmp_path / "one.md", "review-1", extra={"supersede": "review-0"})
    assert main([str(path)]) == 1


def test_legacy_unknown_key_fails(tmp_path):
    path = _write(tmp_path / "one.md", "review-1")
    path.write_text(
        path.read_text() + "supersede: review-0\n", encoding="utf-8",
    )
    assert main([str(path)]) == 1


def test_json_canon_ok_without_evidence_fails(tmp_path):
    path = _write_json(tmp_path / "one.md", "review-1", evidence={})
    assert main([str(path)]) == 1


def test_json_canon_escalations_require_issues_status(tmp_path):
    path = _write_json(
        tmp_path / "one.md", "review-1", status="ok", escalations=["нужно решение"],
    )
    assert main([str(path)]) == 1


def test_json_canon_bad_finding_fails(tmp_path):
    path = _write_json(
        tmp_path / "one.md", "review-1", status="issues",
        findings=[{"severity": "nit", "file": "x.py", "line": 1}],
    )
    assert main([str(path)]) == 1
