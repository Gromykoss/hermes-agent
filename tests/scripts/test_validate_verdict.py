from pathlib import Path

import pytest

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


# --- fix-round (RC 68d2e27): spec_hash — обе документированные формы ---

def test_json_canon_spec_hash_hex_with_annotation_passes(tmp_path):
    """Форма А (предпочтительна): sha256:<64 hex> + аннотация в скобках."""
    path = _write_json(
        tmp_path / "one.md", "review-a1",
        extra={"spec_hash": f"sha256:{'c' * 64} (fleet-reviews blob)"},
    )
    assert main([str(path)]) == 0


def test_json_canon_spec_hash_legacy_descriptive_passes(tmp_path):
    """Форма Б (легаси): sha256:<непустой однострочный текст>."""
    path = _write_json(
        tmp_path / "one.md", "review-b1",
        extra={"spec_hash": "sha256:spec-fleet-reviews-v1.3 WP3 (описание формы)"},
    )
    assert main([str(path)]) == 0


def test_json_canon_spec_hash_empty_after_prefix_fails(tmp_path):
    path = _write_json(tmp_path / "one.md", "review-x1", extra={"spec_hash": "sha256:"})
    assert main([str(path)]) == 1


def test_json_canon_spec_hash_multiline_fails(tmp_path):
    path = _write_json(
        tmp_path / "one.md", "review-x2",
        extra={"spec_hash": "sha256:line one\nline two"},
    )
    assert main([str(path)]) == 1


def test_json_canon_spec_hash_without_prefix_fails(tmp_path):
    path = _write_json(tmp_path / "one.md", "review-x3", extra={"spec_hash": "просто описание"})
    assert main([str(path)]) == 1


def test_json_canon_spec_hash_legacy_overlong_fails(tmp_path):
    path = _write_json(
        tmp_path / "one.md", "review-x4",
        extra={"spec_hash": "sha256:" + "x" * 201},
    )
    assert main([str(path)]) == 1


# --- minors-P1 (директива: расширение регэкспа аннотации формы А) ---

def test_spec_hash_annotation_nested_parens_pass(tmp_path):
    """Вложенные скобки в аннотации разрешены (реальный корпус)."""
    path = _write_json(
        tmp_path / "one.md", "review-n1",
        extra={"spec_hash": f"sha256:{'c' * 64} (blob abc (gate 7/7), canon x)"},
    )
    assert main([str(path)]) == 0


def test_spec_hash_annotation_nested_double_pass(tmp_path):
    path = _write_json(
        tmp_path / "one.md", "review-n2",
        extra={"spec_hash": f"sha256:{'c' * 64} ((a) (b))"},
    )
    assert main([str(path)]) == 0


def test_spec_hash_annotation_empty_fails(tmp_path):
    path = _write_json(tmp_path / "one.md", "review-n3", extra={"spec_hash": f"sha256:{'c' * 64} ()"})
    assert main([str(path)]) == 1


def test_spec_hash_annotation_whitespace_only_fails(tmp_path):
    path = _write_json(tmp_path / "one.md", "review-n4", extra={"spec_hash": f"sha256:{'c' * 64} (( ))"})
    assert main([str(path)]) == 1


def test_spec_hash_annotation_multiline_fails(tmp_path):
    path = _write_json(
        tmp_path / "one.md", "review-n5",
        extra={"spec_hash": f"sha256:{'c' * 64} (a\nb)"},
    )
    assert main([str(path)]) == 1


# --- fix per RC a8411ace (P1 minors fix-round) ---

def test_spec_hash_hex_tail_unbalanced_open_paren_fails(tmp_path):
    """HEX_TAIL-ветка не принимает незакрытую скобку (Major 1 RC)."""
    path = _write_json(
        tmp_path / "one.md", "review-t1",
        extra={"spec_hash": f"sha256:{'c' * 64} (abc"},
    )
    assert main([str(path)]) == 1


def test_spec_hash_hex_tail_unbalanced_close_paren_fails(tmp_path):
    path = _write_json(
        tmp_path / "one.md", "review-t2",
        extra={"spec_hash": f"sha256:{'c' * 64} abc)"},
    )
    assert main([str(path)]) == 1


def test_spec_hash_hex_tail_balanced_mixed_pass(tmp_path):
    """Реальный корпус: аннотация + хвост за закрывающей скобкой."""
    path = _write_json(
        tmp_path / "one.md", "review-t3",
        extra={
            "spec_hash": "sha256:db4d1f9fc40ad6056b03e3a9a8715a56ca9981d13"
            "91a66bec369d12a51649117 (algo=sha256, per-file specs); "
            "срез-пакет tar-sha256 8dd88446de97fb1b4d35757f544df63f3e7ce53"
            "57341a985cc86cb2ac6a9415b",
        },
    )
    assert main([str(path)]) == 0


def test_spec_hash_63_hex_head_rejected(tmp_path):
    """~64-hex голова (типо-blob) не проходит Form B (гекс-гвард)."""
    path = _write_json(
        tmp_path / "one.md", "review-t4",
        extra={"spec_hash": "sha256:" + "c" * 63 + " x"},
    )
    assert main([str(path)]) == 1


def test_spec_hash_65_hex_head_rejected(tmp_path):
    path = _write_json(
        tmp_path / "one.md", "review-t5",
        extra={"spec_hash": "sha256:" + "c" * 64 + "d"},
    )
    assert main([str(path)]) == 1
