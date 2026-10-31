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
