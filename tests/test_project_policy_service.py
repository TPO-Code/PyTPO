from __future__ import annotations

import os
import tempfile
import unittest

from barley_ide.services.project_policy_service import ProjectPolicyService


class ProjectPolicyServiceInterpreterTests(unittest.TestCase):
    def _make_service(self, project_root: str) -> ProjectPolicyService:
        def canonicalize(path: str) -> str:
            return os.path.normcase(os.path.abspath(path))

        def rel_to_project(path: str) -> str:
            try:
                rel = os.path.relpath(canonicalize(path), canonicalize(project_root))
            except ValueError:
                return canonicalize(path)
            return "." if rel == "." else rel.replace("\\", "/")

        def path_has_prefix(path: str, prefix: str) -> bool:
            cpath = canonicalize(path)
            cprefix = canonicalize(prefix)
            try:
                return os.path.commonpath([cpath, cprefix]) == cprefix
            except ValueError:
                return False

        def resolve_path_from_project(path: str) -> str:
            raw = str(path or "")
            if not raw:
                return canonicalize(project_root)
            if os.path.isabs(raw):
                return canonicalize(raw)
            return canonicalize(os.path.join(project_root, raw))

        def resolve_path_from_project_no_symlink_resolve(path: str) -> str:
            raw = os.path.expanduser(str(path or ""))
            if not raw:
                return os.path.abspath(project_root)
            if os.path.isabs(raw):
                return os.path.abspath(raw)
            return os.path.abspath(os.path.join(project_root, raw))

        def normalize_rel(path: str) -> str:
            return str(path or "").replace("\\", "/").strip("/")

        return ProjectPolicyService(
            project_root=project_root,
            canonicalize=canonicalize,
            rel_to_project=rel_to_project,
            path_has_prefix=path_has_prefix,
            resolve_path_from_project=resolve_path_from_project,
            resolve_path_from_project_no_symlink_resolve=resolve_path_from_project_no_symlink_resolve,
            normalize_rel=normalize_rel,
        )

    def test_resolve_interpreter_prefers_project_venv_when_unconfigured(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = os.path.join(tmpdir, "project")
            os.makedirs(os.path.join(project_root, ".venv", "bin"), exist_ok=True)
            interpreter = os.path.join(project_root, ".venv", "bin", "python")
            with open(interpreter, "w", encoding="utf-8") as fh:
                fh.write("#!/usr/bin/env python\n")

            service = self._make_service(project_root)

            resolved = service.resolve_interpreter({}, os.path.join(project_root, "example.py"))

            self.assertEqual(resolved, interpreter)

    def test_resolve_interpreter_still_honors_explicit_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = os.path.join(tmpdir, "project")
            os.makedirs(project_root, exist_ok=True)
            service = self._make_service(project_root)

            resolved = service.resolve_interpreter(
                {"interpreters": {"default": "/custom/python"}},
                os.path.join(project_root, "example.py"),
            )

            self.assertEqual(resolved, "/custom/python")


if __name__ == "__main__":
    unittest.main()
