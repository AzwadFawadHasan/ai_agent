from django.core.management.base import BaseCommand
from django.utils import timezone

from research.models import Finding, Repository, ResearchSession, ToolCallLog


class Command(BaseCommand):
    help = "Create sample records for demoing sessions, findings, and tool calls."

    def handle(self, *args, **options):
        repository, _ = Repository.objects.update_or_create(
            url="https://github.com/psf/requests",
            defaults={
                "name": "psf/requests",
                "source_type": Repository.SourceType.GITHUB,
                "local_path": "data/repositories/psf-requests-demo",
                "default_branch": "main",
                "last_analyzed_at": timezone.now(),
            },
        )
        session, _ = ResearchSession.objects.update_or_create(
            repository=repository,
            question="Where is the main request sending flow implemented?",
            defaults={
                "status": ResearchSession.Status.COMPLETED,
                "final_answer": (
                    "The request sending flow is centered around Session.request, "
                    "which prepares a Request and delegates transport work through "
                    "Session.send. See requests/sessions.py:502-590 and "
                    "requests/sessions.py:673-746."
                ),
                "completed_at": timezone.now(),
                "metadata": {"agent_provider": "seed"},
            },
        )

        Finding.objects.filter(session=session).delete()
        ToolCallLog.objects.filter(session=session).delete()

        Finding.objects.create(
            repository=repository,
            session=session,
            file_path="requests/sessions.py",
            line_start=502,
            line_end=590,
            symbol="Session.request",
            note="Builds and prepares the request before dispatching it.",
            excerpt="502: def request(...)\n...\n590: return self.send(prep, **send_kwargs)",
        )
        Finding.objects.create(
            repository=repository,
            session=session,
            file_path="requests/sessions.py",
            line_start=673,
            line_end=746,
            symbol="Session.send",
            note="Handles adapter selection, redirects, hooks, cookies, and response timing.",
            excerpt="673: def send(self, request, **kwargs):\n...",
        )
        ToolCallLog.objects.create(
            session=session,
            tool_name="search_code",
            arguments={"query": "request send Session"},
            result_preview='{"matches": [{"file_path": "requests/sessions.py", "line": 502}]}',
            success=True,
            duration_ms=12,
        )
        ToolCallLog.objects.create(
            session=session,
            tool_name="get_file_snippet",
            arguments={"path": "requests/sessions.py", "line": 502, "context": 30},
            result_preview='{"file_path": "requests/sessions.py", "line_start": 502, "line_end": 532}',
            success=True,
            duration_ms=8,
        )

        self.stdout.write(
            self.style.SUCCESS(
                f"Seeded repository {repository.id} and research session {session.id}."
            )
        )
