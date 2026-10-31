from pathlib import Path

from scripts.validate_verdict import main


SHA_A = "a" * 40
SHA_B = "b" * 40


def _write(path: Path, review_id: str, *, head_sha=SHA_B, supersedes=None):
    lines = [
        "STATUS: PASS",
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
