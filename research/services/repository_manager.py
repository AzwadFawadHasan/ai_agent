import hashlib
import re
import subprocess
from pathlib import Path

from django.conf import settings
from django.utils import timezone

from research.models import Repository


class RepositoryError(ValueError):
    pass


class RepositoryManager:
    GITHUB_RE = re.compile(
        r"^https://github\.com/(?P<owner>[A-Za-z0-9_.-]+)/(?P<repo>[A-Za-z0-9_.-]+?)(?:\.git)?/?$"
    )

    def prepare(self, source: str) -> Repository:
        source = source.strip()
        match = self.GITHUB_RE.match(source)
        if match:
            return self._prepare_github_repo(source, match)
        return self._prepare_local_repo(source)

    def _prepare_github_repo(self, source: str, match: re.Match) -> Repository:
        owner = match.group("owner")
        repo_name = match.group("repo").removesuffix(".git")
        canonical_url = f"https://github.com/{owner}/{repo_name}"
        local_path = self._clone_path(owner, repo_name, canonical_url)

        repository, _ = Repository.objects.get_or_create(
            url=canonical_url,
            defaults={
                "name": f"{owner}/{repo_name}",
                "source_type": Repository.SourceType.GITHUB,
                "local_path": str(local_path),
            },
        )

        if not (local_path / ".git").exists():
            self._clone(canonical_url, local_path)

        repository.name = f"{owner}/{repo_name}"
        repository.source_type = Repository.SourceType.GITHUB
        repository.local_path = str(local_path)
        repository.last_analyzed_at = timezone.now()
        repository.save(
            update_fields=[
                "name",
                "source_type",
                "local_path",
                "last_analyzed_at",
                "updated_at",
            ]
        )
        return repository

    def _prepare_local_repo(self, source: str) -> Repository:
        path = Path(source).expanduser().resolve()
        if not path.exists() or not path.is_dir():
            raise RepositoryError(
                "Provide a public GitHub URL or an existing local repository path."
            )

        repository, _ = Repository.objects.get_or_create(
            url=str(path),
            defaults={
                "name": path.name,
                "source_type": Repository.SourceType.LOCAL,
                "local_path": str(path),
            },
        )
        repository.name = path.name
        repository.source_type = Repository.SourceType.LOCAL
        repository.local_path = str(path)
        repository.last_analyzed_at = timezone.now()
        repository.save(
            update_fields=[
                "name",
                "source_type",
                "local_path",
                "last_analyzed_at",
                "updated_at",
            ]
        )
        return repository

    def _clone_path(self, owner: str, repo_name: str, url: str) -> Path:
        digest = hashlib.sha1(url.encode("utf-8")).hexdigest()[:12]
        safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "-", f"{owner}-{repo_name}")
        return Path(settings.REPOSITORY_STORAGE_DIR) / f"{safe_name}-{digest}"

    def _clone(self, url: str, local_path: Path) -> None:
        local_path.parent.mkdir(parents=True, exist_ok=True)
        if local_path.exists() and any(local_path.iterdir()):
            raise RepositoryError(
                f"Repository cache path exists but is not a git clone: {local_path}"
            )

        command = ["git", "clone", "--depth", "1", url, str(local_path)]
        try:
            subprocess.run(
                command,
                check=True,
                capture_output=True,
                text=True,
                timeout=120,
            )
        except FileNotFoundError as exc:
            raise RepositoryError("git is required to clone public repositories.") from exc
        except subprocess.CalledProcessError as exc:
            detail = (exc.stderr or exc.stdout or "").strip()
            raise RepositoryError(f"Unable to clone repository: {detail}") from exc
        except subprocess.TimeoutExpired as exc:
            raise RepositoryError("Repository clone timed out.") from exc
