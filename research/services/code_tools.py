import os
import re
from pathlib import Path
from typing import Any


class CodeToolError(ValueError):
    pass


IGNORED_DIRS = {
    ".git",
    ".hg",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".tox",
    ".venv",
    "__pycache__",
    "build",
    "coverage",
    "dist",
    "htmlcov",
    "node_modules",
    "site-packages",
    "venv",
}

IGNORED_SUFFIXES = {
    ".7z",
    ".a",
    ".class",
    ".dll",
    ".exe",
    ".gif",
    ".ico",
    ".jar",
    ".jpeg",
    ".jpg",
    ".lock",
    ".min.js",
    ".pdf",
    ".png",
    ".pyc",
    ".pyd",
    ".so",
    ".svg",
    ".ttf",
    ".webp",
    ".woff",
    ".woff2",
    ".zip",
}


class CodeTools:
    def __init__(self, repo_root: str | Path):
        self.repo_root = Path(repo_root).resolve()
        if not self.repo_root.exists():
            raise CodeToolError(f"Repository path does not exist: {self.repo_root}")

    def list_files(self, path: str = "", limit: int = 200) -> dict[str, Any]:
        start = self._safe_path(path or ".")
        if not start.is_dir():
            raise CodeToolError(f"Not a directory: {path}")

        files = []
        for file_path in self._iter_files(start):
            files.append(self._relative(file_path))
            if len(files) >= limit:
                break
        return {"files": files, "count": len(files), "truncated": len(files) >= limit}

    def search_code(
        self,
        query: str,
        path: str = "",
        max_results: int = 20,
    ) -> dict[str, Any]:
        start = self._safe_path(path or ".")
        if not start.is_dir():
            raise CodeToolError(f"Not a directory: {path}")

        terms = self._terms(query)
        scored_matches = []
        for file_path in self._iter_files(start):
            for line_number, line in self._read_lines(file_path):
                lower_line = line.lower()
                score = sum(1 for term in terms if term in lower_line)
                if score:
                    scored_matches.append(
                        {
                            "file_path": self._relative(file_path),
                            "line": line_number,
                            "score": score,
                            "excerpt": line.strip()[:300],
                        }
                    )

        scored_matches.sort(key=lambda item: (-item["score"], item["file_path"], item["line"]))
        matches = scored_matches[:max_results]
        return {"query": query, "matches": matches, "count": len(matches)}

    def read_file(
        self,
        path: str,
        start_line: int | None = None,
        end_line: int | None = None,
        max_lines: int = 200,
        line_start: int | None = None,
        line_end: int | None = None,
    ) -> dict[str, Any]:
        # Some models may use line_start/line_end despite the schema. Accept both.
        if start_line is None and line_start is not None:
            start_line = line_start
        if end_line is None and line_end is not None:
            end_line = line_end

        file_path = self._safe_path(path)
        if not file_path.is_file():
            raise CodeToolError(f"Not a file: {path}")
        if self._is_binary(file_path):
            raise CodeToolError(f"Refusing to read binary file: {path}")

        lines = [line for _, line in self._read_lines(file_path)]
        total_lines = len(lines)
        start = max(start_line or 1, 1)
        end = min(end_line or total_lines, total_lines)
        if end < start:
            raise CodeToolError("end_line must be greater than or equal to start_line.")

        if end - start + 1 > max_lines:
            end = start + max_lines - 1

        numbered = [
            f"{line_number}: {lines[line_number - 1].rstrip()}"
            for line_number in range(start, end + 1)
        ]
        return {
            "file_path": self._relative(file_path),
            "line_start": start,
            "line_end": end,
            "total_lines": total_lines,
            "truncated": end < (end_line or total_lines),
            "content": "\n".join(numbered),
        }

    def get_file_snippet(
        self,
        path: str,
        line: int,
        context: int = 20,
    ) -> dict[str, Any]:
        start_line = max(line - context, 1)
        end_line = line + context
        return self.read_file(path, start_line=start_line, end_line=end_line)

    def _safe_path(self, path: str) -> Path:
        candidate = (self.repo_root / path).resolve()
        try:
            candidate.relative_to(self.repo_root)
        except ValueError as exc:
            raise CodeToolError("Path escapes repository root.") from exc
        return candidate

    def _iter_files(self, start: Path):
        for root, dirs, files in os.walk(start):
            dirs[:] = sorted(
                directory
                for directory in dirs
                if directory not in IGNORED_DIRS and not directory.startswith(".cache")
            )
            for filename in sorted(files):
                file_path = Path(root) / filename
                if self._should_skip(file_path):
                    continue
                yield file_path

    def _should_skip(self, file_path: Path) -> bool:
        lower_name = file_path.name.lower()
        if any(lower_name.endswith(suffix) for suffix in IGNORED_SUFFIXES):
            return True
        return self._is_binary(file_path)

    def _is_binary(self, file_path: Path) -> bool:
        try:
            with file_path.open("rb") as handle:
                chunk = handle.read(1024)
        except OSError:
            return True
        return b"\0" in chunk

    def _read_lines(self, file_path: Path):
        try:
            with file_path.open("r", encoding="utf-8", errors="replace") as handle:
                for index, line in enumerate(handle, start=1):
                    yield index, line
        except OSError as exc:
            raise CodeToolError(f"Unable to read {self._relative(file_path)}") from exc

    def _relative(self, file_path: Path) -> str:
        return str(file_path.resolve().relative_to(self.repo_root)).replace("\\", "/")

    def _terms(self, query: str) -> list[str]:
        terms = [
            term.lower()
            for term in re.findall(r"[A-Za-z_][A-Za-z0-9_]{2,}", query)
            if term.lower() not in STOP_WORDS
        ]
        return terms or [query.lower()]


STOP_WORDS = {
    "about",
    "and",
    "are",
    "can",
    "code",
    "does",
    "for",
    "from",
    "handle",
    "how",
    "implemented",
    "internally",
    "logic",
    "the",
    "this",
    "what",
    "when",
    "where",
    "which",
    "with",
}
