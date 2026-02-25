from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from src.git.auth_bridge import GitAuthBridge, GitCommandRunner, GitRunError, sanitize_git_text
from src.git.github_auth import GitHubAuthStore


@dataclass(slots=True)
class GitChangeEntry:
    rel_path: str
    state: str  # dirty | untracked
    code: str
    staged: bool
    unstaged: bool
    original_rel_path: str | None = None


@dataclass(slots=True)
class GitRepoStatus:
    project_root: str
    repo_root: str | None
    current_branch: str | None
    file_states: dict[str, str]  # abs path -> clean | dirty | untracked
    folder_states: dict[str, str]  # abs path -> clean | dirty | untracked
    changes: list[GitChangeEntry]


@dataclass(slots=True)
class GitBranchInfo:
    current: str
    branches: list[str]
    remote_branches: list[str] = field(default_factory=list)


@dataclass(slots=True)
class GitPreflightReport:
    repo_root: str
    current_branch: str
    upstream_branch: str | None
    ahead_count: int
    behind_count: int
    staged_count: int
    unstaged_count: int
    untracked_count: int
    ignored_count: int
    staged_paths: list[str] = field(default_factory=list)
    unstaged_paths: list[str] = field(default_factory=list)
    untracked_paths: list[str] = field(default_factory=list)
    ignored_paths: list[str] = field(default_factory=list)
    sample_limit: int = 40


@dataclass(slots=True)
class GitRemoteConfigResult:
    remote_name: str
    action: str  # added | updated | unchanged
    url: str


class GitServiceError(RuntimeError):
    def __init__(self, message: str, *, kind: str = "git_error") -> None:
        super().__init__(message)
        self.kind = kind


class GitService:
    def __init__(
        self,
        *,
        canonicalize: Callable[[str], str] | None = None,
        ide_app_dir: str | Path | None = None,
        github_token_provider: Callable[[], str | None] | None = None,
        use_token_for_git_provider: Callable[[], bool] | None = None,
        command_timeout_seconds: int = 120,
    ) -> None:
        self._canonicalize = canonicalize
        self._auth_store = GitHubAuthStore(ide_app_dir) if ide_app_dir is not None else None
        self._github_token_provider = github_token_provider
        self._use_token_for_git_provider = use_token_for_git_provider
        self._runner = GitCommandRunner(
            auth_bridge=GitAuthBridge(
                token_provider=self._github_token,
                enabled_provider=self._use_token_for_git,
            ),
            default_timeout_seconds=max(20, int(command_timeout_seconds)),
        )

    # ---------- Detection / Status ----------

    def ensure_repo_initialized(self, project_root: str) -> str:
        project = self._canonical(project_root)
        if not project or not os.path.isdir(project):
            raise GitServiceError("Project folder is not available.", kind="invalid_project")

        found = self.find_repo_root(project)
        if found:
            return found

        self._run_git(project, ["init"], check=True)
        found = self.find_repo_root(project)
        if not found:
            raise GitServiceError("Failed to initialize Git repository.", kind="init_failed")
        return found

    def find_repo_root(self, path: str) -> str | None:
        base = self._canonical(path)
        try:
            out = self._run_git(base, ["rev-parse", "--show-toplevel"], check=True)
        except GitServiceError:
            return None
        text = str(out).strip()
        if not text:
            return None
        return self._canonical(text)

    def read_status(self, project_root: str) -> GitRepoStatus:
        project = self._canonical(project_root)
        repo_root = self.find_repo_root(project)
        if not repo_root:
            return GitRepoStatus(
                project_root=project,
                repo_root=None,
                current_branch=None,
                file_states={},
                folder_states={},
                changes=[],
            )

        current_branch = self._read_current_branch(repo_root)
        dirty_untracked, changes = self._parse_porcelain_status(repo_root=repo_root, project_root=project)
        tracked_rel = self._read_tracked_files(repo_root)

        file_states: dict[str, str] = {}
        for rel_path, state in dirty_untracked.items():
            abs_path = self._canonical(os.path.join(repo_root, rel_path))
            if not self._is_within(project, abs_path):
                continue
            if os.path.isdir(abs_path):
                continue
            file_states[abs_path] = state

        changed_rels = set(dirty_untracked.keys())
        for rel_path in tracked_rel:
            if rel_path in changed_rels:
                continue
            abs_path = self._canonical(os.path.join(repo_root, rel_path))
            if not self._is_within(project, abs_path):
                continue
            if not os.path.exists(abs_path):
                continue
            if os.path.isdir(abs_path):
                continue
            file_states[abs_path] = "clean"

        folder_states = self._build_folder_states(project_root=project, file_states=file_states)
        return GitRepoStatus(
            project_root=project,
            repo_root=repo_root,
            current_branch=current_branch,
            file_states=file_states,
            folder_states=folder_states,
            changes=changes,
        )

    def _read_current_branch(self, repo_root: str) -> str:
        try:
            out = self._run_git(repo_root, ["branch", "--show-current"], check=True)
        except GitServiceError:
            return ""
        return str(out).strip()

    def _read_upstream_branch(self, repo_root: str) -> str | None:
        try:
            out = self._run_git(
                repo_root,
                ["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}"],
                check=True,
            )
        except GitServiceError:
            return None
        text = str(out or "").strip()
        return text or None

    def _read_ahead_behind(self, repo_root: str) -> tuple[int, int]:
        try:
            out = self._run_git(
                repo_root,
                ["rev-list", "--left-right", "--count", "@{upstream}...HEAD"],
                check=True,
            )
        except GitServiceError:
            return 0, 0
        tokens = str(out or "").strip().split()
        if len(tokens) != 2:
            return 0, 0
        try:
            behind = int(tokens[0])
            ahead = int(tokens[1])
        except Exception:
            return 0, 0
        return max(0, ahead), max(0, behind)

    def _read_tracked_files(self, repo_root: str) -> set[str]:
        try:
            out = self._run_git(repo_root, ["ls-files", "-z"], check=True)
        except GitServiceError:
            return set()
        return {item.strip() for item in str(out).split("\x00") if item.strip()}

    def _parse_porcelain_status(self, *, repo_root: str, project_root: str) -> tuple[dict[str, str], list[GitChangeEntry]]:
        try:
            out = self._run_git(repo_root, ["status", "--porcelain=1", "-z", "-uall"], check=True)
        except GitServiceError:
            return {}, []

        tokens = str(out).split("\x00")
        changes: list[GitChangeEntry] = []
        path_state: dict[str, str] = {}
        idx = 0

        while idx < len(tokens):
            entry = tokens[idx]
            idx += 1
            if not entry:
                continue
            if len(entry) < 4:
                continue

            code = entry[:2]
            rel_path = entry[3:]
            original: str | None = None

            if code and code[0] in {"R", "C"} and idx < len(tokens):
                original = rel_path
                rel_path = tokens[idx]
                idx += 1

            rel_path = rel_path.strip()
            if not rel_path:
                continue

            if code == "!!":
                continue
            state = "untracked" if code == "??" else "dirty"
            path_state[rel_path] = state

            abs_path = self._canonical(os.path.join(repo_root, rel_path))
            if not self._is_within(project_root, abs_path):
                continue

            changes.append(
                GitChangeEntry(
                    rel_path=rel_path,
                    state=state,
                    code=code,
                    staged=code[0] not in {" ", "?"},
                    unstaged=code[1] not in {" ", "?"},
                    original_rel_path=original.strip() if isinstance(original, str) and original.strip() else None,
                )
            )

        changes.sort(key=lambda item: item.rel_path.lower())
        return path_state, changes

    def _build_folder_states(self, *, project_root: str, file_states: dict[str, str]) -> dict[str, str]:
        folder_states: dict[str, str] = {}
        for abs_file, state in file_states.items():
            folder = self._canonical(os.path.dirname(abs_file))
            while self._is_within(project_root, folder):
                current = folder_states.get(folder)
                folder_states[folder] = self._merge_state_priority(current, state)
                if folder == project_root:
                    break
                parent = self._canonical(os.path.dirname(folder))
                if parent == folder:
                    break
                folder = parent
        return folder_states

    @staticmethod
    def _merge_state_priority(current: str | None, incoming: str) -> str:
        priority = {"dirty": 3, "untracked": 2, "clean": 1}
        if not current:
            return incoming
        if priority.get(incoming, 0) >= priority.get(current, 0):
            return incoming
        return current

    # ---------- Commit / Push ----------

    def stage_paths(self, repo_root: str, rel_paths: list[str]) -> None:
        root = self._require_repo(repo_root)
        files = [str(item).strip() for item in rel_paths if str(item).strip()]
        if not files:
            raise GitServiceError("No files selected to stage.", kind="validation")
        self._run_git(root, ["add", "-A", "--", *files], check=True)

    def stage_all_changes(self, repo_root: str) -> None:
        root = self._require_repo(repo_root)
        self._run_git(root, ["add", "-A", "--", "."], check=True)

    def unstage_paths(self, repo_root: str, rel_paths: list[str]) -> None:
        root = self._require_repo(repo_root)
        files = [str(item).strip() for item in rel_paths if str(item).strip()]
        if not files:
            raise GitServiceError("No files selected to unstage.", kind="validation")
        try:
            self._run_git(root, ["restore", "--staged", "--", *files], check=True)
        except GitServiceError:
            # Fallback for older git versions without `restore`.
            self._run_git(root, ["reset", "HEAD", "--", *files], check=True)

    def commit_files(self, repo_root: str, rel_paths: list[str], message: str) -> str:
        root = self._require_repo(repo_root)
        commit_message = str(message or "").strip()
        if not commit_message:
            raise GitServiceError("Commit message is required.", kind="validation")
        files: list[str] = []
        seen: set[str] = set()
        for item in rel_paths:
            text = str(item).strip()
            if not text:
                continue
            key = text.lower()
            if key in seen:
                continue
            seen.add(key)
            files.append(text)
        if not files:
            raise GitServiceError("Select at least one file to commit.", kind="validation")

        # Deterministic transaction: clear index first to avoid stale staged leftovers.
        try:
            self._run_git(root, ["reset"], check=True)
        except GitServiceError as exc:
            detail = str(exc).lower()
            if "ambiguous argument 'head'" not in detail and "bad revision 'head'" not in detail:
                raise
            # Repositories without an initial commit do not have HEAD yet.
            self._run_git(root, ["rm", "-r", "--cached", "--ignore-unmatch", "--", "."], check=True)

        self._run_git(root, ["add", "-A", "--", *files], check=True)
        try:
            out = self._run_git(root, ["commit", "-m", commit_message], check=True)
        except GitServiceError as exc:
            text = str(exc).lower()
            if "nothing to commit" in text:
                raise GitServiceError("Nothing to commit.", kind="nothing_to_commit") from None
            if "please tell me who you are" in text or "unable to auto-detect email address" in text:
                raise GitServiceError("Git user.name/user.email is not configured.", kind="identity_missing") from None
            raise
        return str(out).strip()

    def push_current_branch(self, repo_root: str) -> str:
        root = self._require_repo(repo_root)
        try:
            out = self._run_git(root, ["push"], check=True)
        except GitServiceError as exc:
            self._raise_push_error(exc)
        return str(out).strip()

    def fetch(self, repo_root: str, *, remote_name: str = "", prune: bool = True) -> str:
        root = self._require_repo(repo_root)
        args = ["fetch"]
        if prune:
            args.append("--prune")
        remote = str(remote_name or "").strip()
        if remote:
            args.append(remote)
        try:
            out = self._run_git(root, args, check=True)
        except GitServiceError as exc:
            self._raise_fetch_error(exc)
        return str(out).strip()

    def pull_current_branch(self, repo_root: str) -> str:
        root = self._require_repo(repo_root)
        try:
            out = self._run_git(root, ["pull"], check=True)
        except GitServiceError as exc:
            self._raise_pull_error(exc)
        return str(out).strip()

    def push_head_to_origin(self, repo_root: str, *, remote_name: str = "origin", set_upstream: bool = True) -> str:
        root = self._require_repo(repo_root)
        remote = str(remote_name or "").strip() or "origin"
        args = ["push"]
        if set_upstream:
            args.extend(["-u", remote, "HEAD"])
        else:
            args.extend([remote, "HEAD"])
        try:
            out = self._run_git(root, args, check=True)
        except GitServiceError as exc:
            self._raise_push_error(exc)
        return str(out).strip()

    def tag_exists(self, repo_root: str, tag_name: str) -> bool:
        root = self._require_repo(repo_root)
        tag = str(tag_name or "").strip()
        if not tag:
            return False
        try:
            out = self._run_git(root, ["tag", "--list", tag], check=True)
        except GitServiceError:
            return False
        return bool(str(out or "").strip())

    def list_tags(self, repo_root: str, *, pattern: str = "") -> list[str]:
        root = self._require_repo(repo_root)
        args = ["tag", "--list"]
        pattern_text = str(pattern or "").strip()
        if pattern_text:
            args.append(pattern_text)
        try:
            out = self._run_git(root, args, check=True)
        except GitServiceError:
            return []
        tags = [line.strip() for line in str(out or "").splitlines() if line.strip()]
        return sorted(tags, key=str.lower)

    def create_annotated_tag(self, repo_root: str, tag_name: str, *, message: str = "") -> None:
        root = self._require_repo(repo_root)
        tag = str(tag_name or "").strip()
        if not tag:
            raise GitServiceError("Tag name is required.", kind="validation")
        note = str(message or "").strip() or tag
        try:
            self._run_git(root, ["tag", "-a", tag, "-m", note], check=True)
        except GitServiceError as exc:
            text = str(exc).lower()
            if "already exists" in text:
                raise GitServiceError("Tag already exists.", kind="tag_exists") from None
            raise

    def push_tag(self, repo_root: str, tag_name: str, *, remote_name: str = "origin") -> str:
        root = self._require_repo(repo_root)
        tag = str(tag_name or "").strip()
        if not tag:
            raise GitServiceError("Tag name is required.", kind="validation")
        remote = str(remote_name or "").strip() or "origin"
        try:
            out = self._run_git(root, ["push", remote, tag], check=True)
        except GitServiceError as exc:
            self._raise_push_error(exc)
        return str(out).strip()

    def get_remote_url(self, repo_root: str, remote_name: str = "origin") -> str | None:
        root = self._require_repo(repo_root)
        remote = str(remote_name or "").strip() or "origin"
        try:
            out = self._run_git(root, ["remote", "get-url", remote], check=True)
        except GitServiceError as exc:
            text = str(exc).lower()
            if "no such remote" in text or "not a git repository" in text:
                return None
            return None
        url = str(out or "").strip()
        return url or None

    def configure_remote(
        self,
        repo_root: str,
        *,
        remote_name: str,
        remote_url: str,
        replace_existing: bool = False,
    ) -> GitRemoteConfigResult:
        root = self._require_repo(repo_root)
        name = str(remote_name or "").strip() or "origin"
        url = str(remote_url or "").strip()
        if not url:
            raise GitServiceError("Remote URL is required.", kind="validation")

        current = self.get_remote_url(root, name)
        if current:
            if self._normalize_remote_url(current) == self._normalize_remote_url(url):
                return GitRemoteConfigResult(remote_name=name, action="unchanged", url=current)
            if not replace_existing:
                raise GitServiceError(
                    f"Remote '{name}' already exists with a different URL.",
                    kind="origin_exists",
                )
            self._run_git(root, ["remote", "set-url", name, url], check=True)
            return GitRemoteConfigResult(remote_name=name, action="updated", url=url)

        self._run_git(root, ["remote", "add", name, url], check=True)
        return GitRemoteConfigResult(remote_name=name, action="added", url=url)

    # ---------- Branches ----------

    def list_branches(self, repo_root: str, *, include_remote: bool = False) -> GitBranchInfo:
        root = self._require_repo(repo_root)
        current = self._read_current_branch(root)
        out = self._run_git(root, ["branch", "--format=%(refname:short)"], check=True)
        branches = sorted({line.strip() for line in str(out).splitlines() if line.strip()}, key=str.lower)
        remote_branches: list[str] = []
        if include_remote:
            remotes_out = self._run_git(root, ["branch", "-r", "--format=%(refname:short)"], check=True)
            remote_branches = sorted(
                {
                    line.strip()
                    for line in str(remotes_out).splitlines()
                    if line.strip() and not line.strip().endswith("/HEAD")
                },
                key=str.lower,
            )
        return GitBranchInfo(current=current, branches=branches, remote_branches=remote_branches)

    def preflight_push_check(self, repo_root: str, *, sample_limit: int = 40) -> GitPreflightReport:
        root = self._require_repo(repo_root)
        limit = max(1, int(sample_limit))
        current = self._read_current_branch(root)
        upstream = self._read_upstream_branch(root)
        ahead, behind = self._read_ahead_behind(root) if upstream else (0, 0)

        staged_count = 0
        unstaged_count = 0
        untracked_count = 0
        staged_paths: list[str] = []
        unstaged_paths: list[str] = []
        untracked_paths: list[str] = []

        try:
            out = self._run_git(root, ["status", "--porcelain=1", "-z", "-uall"], check=True)
        except GitServiceError:
            out = ""

        tokens = str(out).split("\x00")
        idx = 0
        while idx < len(tokens):
            entry = tokens[idx]
            idx += 1
            if not entry or len(entry) < 4:
                continue
            code = entry[:2]
            rel_path = entry[3:]
            if code and code[0] in {"R", "C"} and idx < len(tokens):
                rel_path = tokens[idx]
                idx += 1
            rel_path = rel_path.strip()
            if not rel_path:
                continue

            if code == "??":
                untracked_count += 1
                if len(untracked_paths) < limit:
                    untracked_paths.append(rel_path)
                continue
            if code == "!!":
                continue

            is_staged = code[0] not in {" ", "?"}
            is_unstaged = code[1] not in {" ", "?"}
            if is_staged:
                staged_count += 1
                if len(staged_paths) < limit:
                    staged_paths.append(rel_path)
            if is_unstaged:
                unstaged_count += 1
                if len(unstaged_paths) < limit:
                    unstaged_paths.append(rel_path)

        ignored_count = 0
        ignored_paths: list[str] = []
        try:
            ignored_out = self._run_git(
                root,
                ["ls-files", "--others", "--ignored", "--exclude-standard", "--directory", "-z"],
                check=True,
            )
            for rel_path in [item.strip() for item in str(ignored_out).split("\x00") if item.strip()]:
                ignored_count += 1
                if len(ignored_paths) < limit:
                    ignored_paths.append(rel_path)
        except GitServiceError:
            ignored_count = 0
            ignored_paths = []

        return GitPreflightReport(
            repo_root=root,
            current_branch=current,
            upstream_branch=upstream,
            ahead_count=int(max(0, ahead)),
            behind_count=int(max(0, behind)),
            staged_count=int(max(0, staged_count)),
            unstaged_count=int(max(0, unstaged_count)),
            untracked_count=int(max(0, untracked_count)),
            ignored_count=int(max(0, ignored_count)),
            staged_paths=sorted(set(staged_paths), key=str.lower),
            unstaged_paths=sorted(set(unstaged_paths), key=str.lower),
            untracked_paths=sorted(set(untracked_paths), key=str.lower),
            ignored_paths=sorted(set(ignored_paths), key=str.lower),
            sample_limit=limit,
        )

    def checkout_branch(self, repo_root: str, branch: str) -> None:
        root = self._require_repo(repo_root)
        target = str(branch or "").strip()
        if not target:
            raise GitServiceError("Branch name is required.", kind="validation")
        try:
            self._run_git(root, ["checkout", target], check=True)
        except GitServiceError as exc:
            text = str(exc).lower()
            if "did not match any file(s) known to git" in text or "pathspec" in text:
                raise GitServiceError("Branch not found.", kind="branch_not_found") from None
            raise

    def checkout_remote_branch(
        self,
        repo_root: str,
        remote_branch: str,
        *,
        local_branch: str | None = None,
    ) -> str:
        root = self._require_repo(repo_root)
        remote_ref = str(remote_branch or "").strip()
        if not remote_ref:
            raise GitServiceError("Remote branch name is required.", kind="validation")
        if "/" not in remote_ref:
            raise GitServiceError("Remote branch must include remote name (for example: origin/main).", kind="validation")

        local_name = str(local_branch or "").strip()
        if not local_name:
            local_name = remote_ref.split("/", 1)[1].strip()
        if not local_name:
            raise GitServiceError("Could not derive a local branch name.", kind="validation")

        # If local branch already exists, simply check it out.
        try:
            self._run_git(root, ["show-ref", "--verify", f"refs/heads/{local_name}"], check=True)
            self._run_git(root, ["checkout", local_name], check=True)
            return local_name
        except GitServiceError:
            pass

        try:
            self._run_git(root, ["checkout", "-b", local_name, "--track", remote_ref], check=True)
            return local_name
        except GitServiceError as exc:
            text = str(exc).lower()
            if "did not match any file(s) known to git" in text or "pathspec" in text:
                raise GitServiceError("Remote branch not found.", kind="branch_not_found") from None
            if "already exists" in text:
                self._run_git(root, ["checkout", local_name], check=True)
                return local_name
            raise

    def create_branch(self, repo_root: str, branch: str, *, checkout: bool = True) -> None:
        root = self._require_repo(repo_root)
        target = str(branch or "").strip()
        if not target:
            raise GitServiceError("Branch name is required.", kind="validation")
        if checkout:
            args = ["checkout", "-b", target]
        else:
            args = ["branch", target]
        try:
            self._run_git(root, args, check=True)
        except GitServiceError as exc:
            text = str(exc).lower()
            if "already exists" in text:
                raise GitServiceError("Branch already exists.", kind="branch_exists") from None
            raise

    # ---------- Rollback ----------

    def rollback_file(self, repo_root: str, path: str) -> None:
        root = self._require_repo(repo_root)
        abs_path = self._canonical(path)
        if not self._is_within(root, abs_path):
            raise GitServiceError("Path is outside the repository.", kind="invalid_path")
        rel_path = str(Path(abs_path).relative_to(Path(root))).replace("\\", "/")

        tracked = self._is_tracked(root, rel_path)
        if tracked:
            self._run_git(root, ["restore", "--source=HEAD", "--staged", "--worktree", "--", rel_path], check=True)
            return

        # Untracked rollback: remove path from disk.
        try:
            if os.path.isdir(abs_path):
                for base, dirs, files in os.walk(abs_path, topdown=False):
                    for name in files:
                        os.unlink(os.path.join(base, name))
                    for name in dirs:
                        os.rmdir(os.path.join(base, name))
                os.rmdir(abs_path)
            elif os.path.exists(abs_path):
                os.unlink(abs_path)
        except Exception as exc:
            raise GitServiceError(f"Could not remove untracked path: {exc}", kind="rollback_failed") from exc

    def discard_unstaged_changes(self, repo_root: str) -> None:
        root = self._require_repo(repo_root)
        self._run_git(root, ["restore", "--worktree", "--", "."], check=True)

    def unstage_all(self, repo_root: str) -> None:
        root = self._require_repo(repo_root)
        self._run_git(root, ["reset"], check=True)

    def hard_reset_head(self, repo_root: str) -> None:
        root = self._require_repo(repo_root)
        self._run_git(root, ["reset", "--hard", "HEAD"], check=True)

    def is_tracked_path(self, repo_root: str, rel_path: str) -> bool:
        root = self._require_repo(repo_root)
        target = str(rel_path or "").strip()
        if not target:
            return False
        return self._is_tracked(root, target)

    # ---------- Internals ----------

    def _is_tracked(self, repo_root: str, rel_path: str) -> bool:
        try:
            self._run_git(repo_root, ["ls-files", "--error-unmatch", "--", rel_path], check=True)
            return True
        except GitServiceError:
            return False

    def _require_repo(self, repo_root: str) -> str:
        root = self._canonical(repo_root)
        if not root or not os.path.isdir(root):
            raise GitServiceError("Git repository is not available.", kind="not_repo")
        return root

    def _run_git(self, cwd: str, args: list[str], *, check: bool) -> str:
        git_bin = shutil_which_git()
        if not git_bin:
            raise GitServiceError("Git is not installed or not in PATH.", kind="git_not_installed")
        try:
            proc = self._runner.run(
                git_bin=git_bin,
                cwd=cwd,
                args=args,
                timeout_seconds=180,
            )
        except GitRunError as exc:
            raise GitServiceError(str(exc), kind=str(exc.kind or "git_error")) from exc

        if check and proc.returncode != 0:
            detail = self._pick_error_detail(proc.stderr, proc.stdout)
            raise GitServiceError(detail, kind=self._infer_error_kind(detail))
        # Preserve exact stdout content for parsers that depend on leading
        # whitespace and NUL separators (for example: `git status -z`).
        return str(proc.stdout or "")

    @staticmethod
    def _pick_error_detail(stderr: str, stdout: str) -> str:
        merged = "\n".join([str(stderr or ""), str(stdout or "")]).strip()
        if not merged:
            return "Git command failed."
        lines = [line.strip() for line in merged.splitlines() if line.strip()]
        if not lines:
            return "Git command failed."
        for line in lines:
            low = line.lower()
            if "fatal:" in low or "error:" in low:
                return sanitize_git_text(line)
        return sanitize_git_text(lines[-1])

    @staticmethod
    def _infer_error_kind(detail: str) -> str:
        text = str(detail or "").lower()
        if "not a git repository" in text:
            return "not_repo"
        if "permission denied" in text or "authentication failed" in text:
            return "auth_failed"
        if "terminal prompts disabled" in text or "could not read username for" in text:
            return "auth_failed"
        if "timed out" in text:
            return "timeout"
        if "could not resolve host" in text or "couldn't connect to server" in text or "timed out" in text:
            return "network"
        return "git_error"

    def _github_token(self) -> str | None:
        if self._github_token_provider is not None:
            try:
                token = self._github_token_provider()
                text = str(token or "").strip()
                return text or None
            except Exception:
                return None
        if self._auth_store is None:
            return None
        try:
            token = self._auth_store.get()
        except Exception:
            return None
        text = str(token or "").strip()
        return text or None

    def _use_token_for_git(self) -> bool:
        if self._use_token_for_git_provider is not None:
            try:
                return bool(self._use_token_for_git_provider())
            except Exception:
                return False
        return True

    def _canonical(self, path: str) -> str:
        if self._canonicalize is not None:
            try:
                return self._canonicalize(path)
            except Exception:
                pass
        try:
            return str(Path(path).expanduser().resolve())
        except Exception:
            return os.path.abspath(os.path.expanduser(path))

    def _is_within(self, root: str, target: str) -> bool:
        try:
            return os.path.commonpath([self._canonical(root), self._canonical(target)]) == self._canonical(root)
        except Exception:
            return False

    @staticmethod
    def _normalize_remote_url(url: str) -> str:
        text = str(url or "").strip().rstrip("/")
        if text.lower().endswith(".git"):
            text = text[:-4]
        return text.lower()

    @staticmethod
    def _raise_push_error(exc: GitServiceError) -> None:
        text = str(exc).lower()
        if "has no upstream branch" in text:
            raise GitServiceError(
                "No upstream branch configured. Push once manually with -u to set tracking.",
                kind="no_upstream",
            ) from None
        if "could not resolve host" in text or "couldn't connect to server" in text or "connection timed out" in text:
            raise GitServiceError("Network error while pushing.", kind="network") from None
        if (
            "permission denied" in text
            or "authentication failed" in text
            or "could not read username for" in text
            or "terminal prompts disabled" in text
        ):
            raise GitServiceError(
                "Push authentication failed. Verify token permissions and Git transport bridge setting.",
                kind="auth_failed",
            ) from None
        raise exc

    @staticmethod
    def _raise_fetch_error(exc: GitServiceError) -> None:
        text = str(exc).lower()
        if "could not resolve host" in text or "couldn't connect to server" in text or "connection timed out" in text:
            raise GitServiceError("Network error while fetching.", kind="network") from None
        if (
            "permission denied" in text
            or "authentication failed" in text
            or "could not read username for" in text
            or "terminal prompts disabled" in text
        ):
            raise GitServiceError(
                "Fetch authentication failed. Verify token permissions and Git transport bridge setting.",
                kind="auth_failed",
            ) from None
        raise exc

    @staticmethod
    def _raise_pull_error(exc: GitServiceError) -> None:
        text = str(exc).lower()
        if "has no upstream branch" in text or "no tracking information for the current branch" in text:
            raise GitServiceError(
                "No upstream branch configured. Set tracking for the current branch, then pull again.",
                kind="no_upstream",
            ) from None
        if (
            "local changes to the following files would be overwritten" in text
            or "would be overwritten by merge" in text
            or "please commit your changes or stash them" in text
        ):
            raise GitServiceError(
                "Pull would overwrite local changes. Commit/stash your work, then pull again.",
                kind="dirty_worktree",
            ) from None
        if "could not resolve host" in text or "couldn't connect to server" in text or "connection timed out" in text:
            raise GitServiceError("Network error while pulling.", kind="network") from None
        if (
            "permission denied" in text
            or "authentication failed" in text
            or "could not read username for" in text
            or "terminal prompts disabled" in text
        ):
            raise GitServiceError(
                "Pull authentication failed. Verify token permissions and Git transport bridge setting.",
                kind="auth_failed",
            ) from None
        raise exc


def shutil_which_git() -> str | None:
    import shutil

    return shutil.which("git")
