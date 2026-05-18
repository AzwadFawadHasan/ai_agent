from typing import Any

from research.models import Finding, ResearchSession


class DatabaseTools:
    def __init__(self, session: ResearchSession):
        self.session = session

    def save_finding(
        self,
        file_path: str,
        note: str,
        line_start: int | None = None,
        line_end: int | None = None,
        excerpt: str = "",
        symbol: str = "",
    ) -> dict[str, Any]:
        finding = Finding.objects.create(
            session=self.session,
            repository=self.session.repository,
            file_path=file_path,
            line_start=line_start,
            line_end=line_end,
            symbol=symbol,
            note=note,
            excerpt=excerpt,
        )
        return {
            "id": finding.id,
            "file_path": finding.file_path,
            "line_start": finding.line_start,
            "line_end": finding.line_end,
            "note": finding.note,
        }

    def get_previous_findings(self, limit: int = 10) -> dict[str, Any]:
        findings = (
            Finding.objects.filter(repository=self.session.repository)
            .exclude(session=self.session)
            .select_related("session")
            .order_by("-created_at")[:limit]
        )
        return {
            "findings": [
                {
                    "session_id": finding.session_id,
                    "question": finding.session.question,
                    "file_path": finding.file_path,
                    "line_start": finding.line_start,
                    "line_end": finding.line_end,
                    "note": finding.note,
                    "excerpt": finding.excerpt[:500],
                }
                for finding in findings
            ]
        }

    def list_past_sessions(self, limit: int = 10) -> dict[str, Any]:
        sessions = (
            ResearchSession.objects.filter(repository=self.session.repository)
            .exclude(id=self.session.id)
            .order_by("-created_at")[:limit]
        )
        return {
            "sessions": [
                {
                    "id": session.id,
                    "question": session.question,
                    "status": session.status,
                    "created_at": session.created_at.isoformat(),
                    "completed_at": session.completed_at.isoformat()
                    if session.completed_at
                    else None,
                }
                for session in sessions
            ]
        }
