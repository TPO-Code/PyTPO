from __future__ import annotations

import unittest

from pytpo.git.git_clone_service import parse_repo_url


class ParseRepoUrlTests(unittest.TestCase):
    def test_github_repo_page_url_normalizes_to_clone_url(self) -> None:
        parsed = parse_repo_url("https://github.com/example/private-repo")
        self.assertEqual(parsed.normalized_url, "https://github.com/example/private-repo.git")
        self.assertEqual(parsed.folder_name, "private-repo")

    def test_github_subpage_url_normalizes_to_clone_url(self) -> None:
        parsed = parse_repo_url("https://github.com/example/private-repo/tree/main")
        self.assertEqual(parsed.normalized_url, "https://github.com/example/private-repo.git")
        self.assertEqual(parsed.folder_name, "private-repo")

    def test_non_github_http_url_keeps_explicit_path(self) -> None:
        parsed = parse_repo_url("https://gitlab.example.com/group/repo")
        self.assertEqual(parsed.normalized_url, "https://gitlab.example.com/group/repo")
        self.assertEqual(parsed.folder_name, "repo")


if __name__ == "__main__":
    unittest.main()
