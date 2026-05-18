from django.db.models import Count
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import Repository, ResearchSession
from .serializers import (
    CreateResearchSessionSerializer,
    RepositorySerializer,
    ResearchSessionListSerializer,
    ResearchSessionSerializer,
)
from .services.agent import AgentOrchestrator
from .services.repository_manager import RepositoryError, RepositoryManager


class RepositoryListView(APIView):
    def get(self, request):
        repositories = Repository.objects.annotate(session_count=Count("sessions")).order_by(
            "name"
        )
        return Response(RepositorySerializer(repositories, many=True).data)


class RepositorySessionsView(APIView):
    def get(self, request):
        repo_url = request.query_params.get("repo_url", "").strip()
        if not repo_url:
            return Response(
                {"detail": "repo_url query parameter is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        repository = self._find_repository(repo_url)
        if not repository:
            return Response(
                {"detail": "Repository has not been researched yet."},
                status=status.HTTP_404_NOT_FOUND,
            )

        sessions = repository.sessions.select_related("repository").all()
        return Response(
            {
                "repository": RepositorySerializer(repository).data,
                "sessions": ResearchSessionListSerializer(sessions, many=True).data,
            }
        )

    def _find_repository(self, repo_url: str) -> Repository | None:
        candidates = {repo_url, repo_url.rstrip("/")}
        if repo_url.endswith(".git"):
            candidates.add(repo_url.removesuffix(".git"))
        return Repository.objects.filter(url__in=candidates).first()


class ResearchSessionListCreateView(APIView):
    def get(self, request):
        sessions = ResearchSession.objects.select_related("repository").all()
        repo_url = request.query_params.get("repo_url", "").strip()
        if repo_url:
            sessions = sessions.filter(repository__url__in={repo_url, repo_url.rstrip("/")})
        return Response(ResearchSessionListSerializer(sessions, many=True).data)

    def post(self, request):
        serializer = CreateResearchSessionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            repository = RepositoryManager().prepare(serializer.validated_data["repo_url"])
        except RepositoryError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        session = ResearchSession.objects.create(
            repository=repository,
            question=serializer.validated_data["question"],
        )
        AgentOrchestrator(session).run()
        session.refresh_from_db()
        return Response(
            ResearchSessionSerializer(session).data,
            status=status.HTTP_201_CREATED,
        )


class ResearchSessionDetailView(APIView):
    def get(self, request, pk: int):
        try:
            session = ResearchSession.objects.select_related("repository").get(pk=pk)
        except ResearchSession.DoesNotExist:
            return Response(
                {"detail": "Research session not found."},
                status=status.HTTP_404_NOT_FOUND,
            )
        return Response(ResearchSessionSerializer(session).data)
