from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass


@dataclass(slots=True)
class GitHubRepo:
    name: str
    full_name: str
    private: bool
    clone_url: str
    default_branch: str


@dataclass(slots=True)
class GitHubCreatedRepo:
    name: str
    full_name: str
    private: bool
    clone_url: str
    default_branch: str
    html_url: str


@dataclass(slots=True)
class GitHubCreatedRelease:
    id: int
    tag_name: str
    name: str
    html_url: str
    draft: bool
    prerelease: bool


class GitHubClientError(RuntimeError):
    def __init__(self, message: str, *, kind: str = "unknown", status_code: int | None = None) -> None:
        super().__init__(message)
        self.kind = kind
        self.status_code = status_code


class GitHubClient:
    def __init__(
        self,
        token: str,
        *,
        api_base_url: str = "https://api.github.com",
        timeout_s: float = 15.0,
    ) -> None:
        token_text = str(token or "").strip()
        if not token_text:
            raise GitHubClientError("No GitHub token is configured.", kind="no_token")
        self._token = token_text
        self._api_base_url = str(api_base_url).rstrip("/")
        self._timeout_s = max(0.5, float(timeout_s))

    def test_connection(self) -> str:
        payload = self._request_json("/user")
        if not isinstance(payload, dict):
            raise GitHubClientError("Unexpected response from GitHub.", kind="invalid_response")
        username = str(payload.get("login") or "").strip()
        if not username:
            raise GitHubClientError("GitHub did not return a username.", kind="invalid_response")
        return username

    def list_repos(self, *, per_page: int = 100) -> list[GitHubRepo]:
        page_size = max(1, min(100, int(per_page)))
        repos: list[GitHubRepo] = []
        seen_full_names: set[str] = set()
        query_modes = [
            {
                "type": "all",
            },
            {
                "visibility": "all",
                "affiliation": "owner,collaborator,organization_member",
            },
        ]

        for mode in query_modes:
            self._collect_repos_for_mode(
                mode=mode,
                page_size=page_size,
                repos=repos,
                seen_full_names=seen_full_names,
            )

        repos.sort(key=lambda item: item.full_name.lower())
        return repos

    def create_repo(self, *, name: str, description: str = "", private: bool = True) -> GitHubCreatedRepo:
        repo_name = str(name or "").strip()
        if not repo_name:
            raise GitHubClientError("Repository name is required.", kind="validation")
        payload = {
            "name": repo_name,
            "description": str(description or "").strip(),
            "private": bool(private),
            "auto_init": False,
        }
        raw = self._request_json("/user/repos", method="POST", payload=payload)
        if not isinstance(raw, dict):
            raise GitHubClientError("Unexpected response from GitHub.", kind="invalid_response")
        created = GitHubCreatedRepo(
            name=str(raw.get("name") or "").strip(),
            full_name=str(raw.get("full_name") or "").strip(),
            private=bool(raw.get("private", False)),
            clone_url=str(raw.get("clone_url") or "").strip(),
            default_branch=str(raw.get("default_branch") or "").strip(),
            html_url=str(raw.get("html_url") or "").strip(),
        )
        if not created.name or not created.full_name or not created.clone_url:
            raise GitHubClientError("GitHub did not return repository details.", kind="invalid_response")
        return created

    def create_release(
        self,
        *,
        owner: str,
        repo: str,
        tag_name: str,
        name: str = "",
        body: str = "",
        draft: bool = False,
        prerelease: bool = False,
        target_commitish: str = "",
    ) -> GitHubCreatedRelease:
        owner_text = str(owner or "").strip()
        repo_text = str(repo or "").strip()
        tag_text = str(tag_name or "").strip()
        if not owner_text or not repo_text:
            raise GitHubClientError("Repository owner/name is required.", kind="validation")
        if not tag_text:
            raise GitHubClientError("Tag name is required.", kind="validation")

        payload = {
            "tag_name": tag_text,
            "name": str(name or "").strip() or tag_text,
            "body": str(body or ""),
            "draft": bool(draft),
            "prerelease": bool(prerelease),
        }
        target_text = str(target_commitish or "").strip()
        if target_text:
            payload["target_commitish"] = target_text

        try:
            raw = self._request_json(
                f"/repos/{owner_text}/{repo_text}/releases",
                method="POST",
                payload=payload,
            )
        except GitHubClientError as exc:
            if int(getattr(exc, "status_code", 0) or 0) == 422 or str(exc.kind or "") == "already_exists":
                raise GitHubClientError(
                    "Release or tag already exists on GitHub.",
                    kind="already_exists",
                    status_code=getattr(exc, "status_code", 422),
                ) from None
            raise

        if not isinstance(raw, dict):
            raise GitHubClientError("Unexpected response from GitHub.", kind="invalid_response")
        created = GitHubCreatedRelease(
            id=int(raw.get("id") or 0),
            tag_name=str(raw.get("tag_name") or "").strip(),
            name=str(raw.get("name") or "").strip(),
            html_url=str(raw.get("html_url") or "").strip(),
            draft=bool(raw.get("draft", False)),
            prerelease=bool(raw.get("prerelease", False)),
        )
        if created.id <= 0 or not created.tag_name or not created.html_url:
            raise GitHubClientError("GitHub did not return release details.", kind="invalid_response")
        return created

    def _collect_repos_for_mode(
        self,
        *,
        mode: dict[str, str],
        page_size: int,
        repos: list[GitHubRepo],
        seen_full_names: set[str],
    ) -> None:
        base_params = {
            "per_page": page_size,
            "sort": "full_name",
            "direction": "asc",
        }
        base_params.update(mode)

        page = 1
        max_pages = 100
        while page <= max_pages:
            params = dict(base_params)
            params["page"] = page
            query = urllib.parse.urlencode(params)
            url = f"{self._api_base_url}/user/repos?{query}"
            payload, _headers = self._request_json_url(url)
            if not isinstance(payload, list):
                raise GitHubClientError("Unexpected repository payload.", kind="invalid_response")

            for raw in payload:
                if not isinstance(raw, dict):
                    continue
                repo = GitHubRepo(
                    name=str(raw.get("name") or "").strip(),
                    full_name=str(raw.get("full_name") or "").strip(),
                    private=bool(raw.get("private", False)),
                    clone_url=str(raw.get("clone_url") or "").strip(),
                    default_branch=str(raw.get("default_branch") or "").strip(),
                )
                if not repo.name or not repo.full_name or not repo.clone_url:
                    continue
                dedupe = repo.full_name.lower()
                if dedupe in seen_full_names:
                    continue
                seen_full_names.add(dedupe)
                repos.append(repo)

            if len(payload) < page_size:
                break
            page += 1

    def _request_json(self, path: str, *, method: str = "GET", payload: dict | None = None):
        url = f"{self._api_base_url}{path}"
        body, _headers = self._request_json_url(url, method=method, payload=payload)
        return body

    def _request_json_url(self, url: str, *, method: str = "GET", payload: dict | None = None):
        body_bytes: bytes | None = None
        if payload is not None:
            try:
                body_bytes = json.dumps(payload).encode("utf-8")
            except Exception as exc:
                raise GitHubClientError("Invalid request payload.", kind="validation") from exc
        request = urllib.request.Request(
            url=url,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {self._token}",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "PyTPO-GitHub-Integration",
                "Content-Type": "application/json",
            },
            data=body_bytes,
            method=str(method or "GET").upper(),
        )
        try:
            with urllib.request.urlopen(request, timeout=self._timeout_s) as response:
                body = response.read()
                headers = dict(response.headers.items())
        except urllib.error.HTTPError as exc:
            status = int(getattr(exc, "code", 0) or 0)
            if status in {401, 403}:
                raise GitHubClientError(
                    "GitHub authentication failed. Verify your token and permissions.",
                    kind="auth",
                    status_code=status,
                ) from None
            if status == 422:
                raise GitHubClientError(
                    "Repository name already exists on GitHub account.",
                    kind="already_exists",
                    status_code=status,
                ) from None
            if status == 404:
                raise GitHubClientError("GitHub endpoint not found.", kind="not_found", status_code=status) from None
            raise GitHubClientError(
                f"GitHub request failed with HTTP {status}.",
                kind="http",
                status_code=status,
            ) from None
        except urllib.error.URLError as exc:
            raise GitHubClientError("Network error while contacting GitHub.", kind="network") from exc
        except TimeoutError as exc:
            raise GitHubClientError("GitHub request timed out.", kind="network") from exc

        try:
            payload = json.loads(body.decode("utf-8"))
        except Exception as exc:
            raise GitHubClientError("Failed to decode GitHub response.", kind="invalid_response") from exc

        return payload, headers
