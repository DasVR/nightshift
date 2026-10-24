"""Tests for authorship hygiene scripts (commit-msg hook, installer, audit)."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
HOOK = REPO_ROOT / "scripts" / "hooks" / "commit-msg"
INSTALLER = REPO_ROOT / "scripts" / "install-hooks.sh"
AUDIT = REPO_ROOT / "scripts" / "check_authorship.mjs"

OWNER_NAME = "DasVR"
OWNER_EMAIL = "arriqaalraee@gmail.com"

requires_node = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")


def run_hook(message: str) -> subprocess.CompletedProcess[str]:
    import tempfile

    with tempfile.NamedTemporaryFile("w", delete=False, suffix=".txt") as f:
        f.write(message)
        path = f.name
    try:
        return subprocess.run(
            ["sh", str(HOOK), path],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
        )
    finally:
        os.unlink(path)


def git(cmd: list[str], *, cwd: Path, env: dict | None = None) -> subprocess.CompletedProcess[str]:
    base = os.environ.copy()
    base.setdefault("GIT_AUTHOR_NAME", OWNER_NAME)
    base.setdefault("GIT_AUTHOR_EMAIL", OWNER_EMAIL)
    base.setdefault("GIT_COMMITTER_NAME", OWNER_NAME)
    base.setdefault("GIT_COMMITTER_EMAIL", OWNER_EMAIL)
    if env:
        base.update(env)
    return subprocess.run(
        ["git", *cmd],
        cwd=cwd,
        capture_output=True,
        text=True,
        env=base,
    )


def init_repo(path: Path) -> None:
    git(["init", "-b", "main"], cwd=path)
    git(["config", "user.name", OWNER_NAME], cwd=path)
    git(["config", "user.email", OWNER_EMAIL], cwd=path)


class TestCommitMsgHook:
    def test_accepts_conventional_feat(self) -> None:
        msg = "feat(authorship): reject ai trailers in commit messages\n"
        result = run_hook(msg)
        assert result.returncode == 0, result.stderr

    def test_accepts_merge_subject(self) -> None:
        msg = "Merge branch 'main' into authorship-hygiene\n"
        result = run_hook(msg)
        assert result.returncode == 0, result.stderr

    @pytest.mark.parametrize(
        "message",
        [
            "feat(x): ok\n\nCo-Authored-By: Claude <noreply@anthropic.com>\n",
            "feat(x): ok\n\nGenerated with Cursor\n",
            "feat(x): ok\n\nclaude helped here\n",
            "feat(x): summary\n\nAI-assisted refactor\n",
            "fix(co-authored by cursor): x\n",
            "update stuff\n",
        ],
    )
    def test_rejects_bad_messages(self, message: str) -> None:
        result = run_hook(message)
        assert result.returncode == 1, (message, result.stderr)


@requires_node
class TestAuthorshipAudit:
    def _copy_audit_script(self, dest_repo: Path) -> None:
        scripts = dest_repo / "scripts"
        scripts.mkdir(parents=True)
        shutil.copy(AUDIT, scripts / "check_authorship.mjs")

    def _run_audit(
        self,
        repo: Path,
        *,
        strict: bool = False,
        rev_range: str | None = None,
    ) -> tuple[int, dict]:
        cmd = ["node", str(repo / "scripts" / "check_authorship.mjs"), "--json"]
        if strict:
            cmd.append("--strict")
        if rev_range:
            cmd.append(f"--range={rev_range}")
        proc = subprocess.run(cmd, cwd=repo, capture_output=True, text=True)
        data = json.loads(proc.stdout)
        return proc.returncode, data

    def test_clean_repo_strict_passes(self, tmp_path: Path) -> None:
        repo = tmp_path / "clean"
        repo.mkdir()
        init_repo(repo)
        self._copy_audit_script(repo)
        (repo / "README.md").write_text("# hello\n", encoding="utf-8")
        git(["add", "README.md"], cwd=repo)
        git(["commit", "-m", "chore: initial readme"], cwd=repo)
        code, data = self._run_audit(repo, strict=True)
        assert code == 0
        assert data["violations"] == []

    def test_trailer_commit_strict_fails(self, tmp_path: Path) -> None:
        repo = tmp_path / "trailer"
        repo.mkdir()
        init_repo(repo)
        self._copy_audit_script(repo)
        (repo / "a.txt").write_text("x\n", encoding="utf-8")
        git(["add", "a.txt"], cwd=repo)
        git(
            ["commit", "-m", "chore: x\n\nCo-Authored-By: Claude <noreply@anthropic.com>"],
            cwd=repo,
        )
        short = git(["rev-parse", "--short", "HEAD"], cwd=repo).stdout.strip()
        code, data = self._run_audit(repo, strict=True)
        assert code == 1
        kinds = {v["kind"] for v in data["violations"]}
        assert "trailer" in kinds
        assert any(v.get("commit") == short for v in data["violations"])

    def test_author_identity_strict_fails(self, tmp_path: Path) -> None:
        repo = tmp_path / "identity"
        repo.mkdir()
        init_repo(repo)
        self._copy_audit_script(repo)
        (repo / "b.txt").write_text("y\n", encoding="utf-8")
        git(["add", "b.txt"], cwd=repo)
        git(
            ["commit", "-m", "chore: add file"],
            cwd=repo,
            env={
                "GIT_AUTHOR_NAME": "Cursor Agent",
                "GIT_AUTHOR_EMAIL": "cursoragent@cursor.com",
                "GIT_COMMITTER_NAME": "Cursor Agent",
                "GIT_COMMITTER_EMAIL": "cursoragent@cursor.com",
            },
        )
        code, data = self._run_audit(repo, strict=True)
        assert code == 1
        assert any(v["kind"] == "author-identity" for v in data["violations"])

    def test_file_violation_generated_by_cursor(self, tmp_path: Path) -> None:
        repo = tmp_path / "fileviol"
        repo.mkdir()
        init_repo(repo)
        self._copy_audit_script(repo)
        (repo / "gen.txt").write_text("# Generated by Cursor\n", encoding="utf-8")
        git(["add", "gen.txt"], cwd=repo)
        git(["commit", "-m", "chore: add generated file"], cwd=repo)
        code, data = self._run_audit(repo, strict=True)
        assert code == 1
        assert any(v["kind"] == "file-violation" for v in data["violations"])

    def test_advisory_not_fatal_under_strict(self, tmp_path: Path) -> None:
        repo = tmp_path / "advisory"
        repo.mkdir()
        init_repo(repo)
        self._copy_audit_script(repo)
        (repo / "doc.md").write_text("Claude Code sessions\n", encoding="utf-8")
        git(["add", "doc.md"], cwd=repo)
        git(["commit", "-m", "chore: doc"], cwd=repo)
        code, data = self._run_audit(repo, strict=True)
        assert code == 0
        assert data["violations"] == []
        assert len(data["advisories"]) >= 1

    def test_subject_matter_mention_in_message_is_advisory(self, tmp_path: Path) -> None:
        repo = tmp_path / "mention"
        repo.mkdir()
        init_repo(repo)
        self._copy_audit_script(repo)
        (repo / "m.txt").write_text("x\n", encoding="utf-8")
        git(["add", "m.txt"], cwd=repo)
        git(
            ["commit", "-m", "feat(ingest): parse external session bundles\n\nThe parser reads jsonl transcripts from the session store."],
            cwd=repo,
        )
        code, data = self._run_audit(repo, strict=True)
        assert code == 0
        assert data["violations"] == []

        git(["commit", "--allow-empty", "-m", "feat(ingest): read claude session bundles"], cwd=repo)
        code, data = self._run_audit(repo, strict=True)
        assert code == 0
        assert data["violations"] == []
        assert any(a.get("commit") for a in data["advisories"])

    def test_agent_branded_branch_name_in_merge_subject(self, tmp_path: Path) -> None:
        repo = tmp_path / "branch"
        repo.mkdir()
        init_repo(repo)
        self._copy_audit_script(repo)
        (repo / "n.txt").write_text("x\n", encoding="utf-8")
        git(["add", "n.txt"], cwd=repo)
        git(
            ["commit", "-m", "Merge pull request #7 from DasVR/cursor/nightly-run"],
            cwd=repo,
            env={"GIT_AUTHOR_NAME": "Arriq", "GIT_AUTHOR_EMAIL": "157200408+DasVR@users.noreply.github.com"},
        )
        code, data = self._run_audit(repo, strict=True)
        assert code == 1
        assert any(v["kind"] == "branch-name" for v in data["violations"])

    def test_report_only_exits_zero_with_violations(self, tmp_path: Path) -> None:
        repo = tmp_path / "report"
        repo.mkdir()
        init_repo(repo)
        self._copy_audit_script(repo)
        (repo / "c.txt").write_text("x\n", encoding="utf-8")
        git(["add", "c.txt"], cwd=repo)
        git(
            ["commit", "-m", "chore: bad\n\nCo-Authored-By: x"],
            cwd=repo,
        )
        code, data = self._run_audit(repo, strict=False)
        assert code == 0
        assert len(data["violations"]) >= 1

    def test_range_limits_history_scan(self, tmp_path: Path) -> None:
        repo = tmp_path / "range"
        repo.mkdir()
        init_repo(repo)
        self._copy_audit_script(repo)
        (repo / "one.txt").write_text("1\n", encoding="utf-8")
        git(["add", "one.txt"], cwd=repo)
        git(
            ["commit", "-m", "chore: one\n\nCo-Authored-By: bad"],
            cwd=repo,
        )
        bad_short = git(["rev-parse", "--short", "HEAD"], cwd=repo).stdout.strip()
        (repo / "two.txt").write_text("2\n", encoding="utf-8")
        git(["add", "two.txt"], cwd=repo)
        git(["commit", "-m", "chore: two"], cwd=repo)
        code, data = self._run_audit(repo, strict=True, rev_range="HEAD~1..HEAD")
        commits = {v.get("commit") for v in data["violations"] if v.get("commit")}
        assert bad_short not in commits
        assert code == 0


class TestInstallHooks:
    def test_installs_hook_and_blocks_bad_commit(self, tmp_path: Path) -> None:
        repo = tmp_path / "hooked"
        repo.mkdir()
        init_repo(repo)
        scripts = repo / "scripts" / "hooks"
        scripts.mkdir(parents=True)
        shutil.copy(HOOK, scripts / "commit-msg")
        shutil.copy(INSTALLER, repo / "scripts" / "install-hooks.sh")
        subprocess.run(["sh", str(repo / "scripts" / "install-hooks.sh")], cwd=repo, check=True)
        hook_path = repo / ".git" / "hooks" / "commit-msg"
        assert hook_path.is_file()
        assert os.access(hook_path, os.X_OK)

        (repo / "f.txt").write_text("z\n", encoding="utf-8")
        git(["add", "f.txt"], cwd=repo)
        bad = git(
            ["commit", "-m", "feat: x\n\nCo-Authored-By: Claude <noreply@anthropic.com>"],
            cwd=repo,
        )
        assert bad.returncode != 0

        good = git(["commit", "-m", "feat(test): add file"], cwd=repo)
        assert good.returncode == 0
