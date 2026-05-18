from rest_framework import serializers

from .models import Finding, Repository, ResearchSession, ToolCallLog


class FindingSerializer(serializers.ModelSerializer):
    class Meta:
        model = Finding
        fields = [
            "id",
            "file_path",
            "line_start",
            "line_end",
            "symbol",
            "note",
            "excerpt",
            "created_at",
        ]


class ToolCallLogSerializer(serializers.ModelSerializer):
    class Meta:
        model = ToolCallLog
        fields = [
            "id",
            "tool_name",
            "arguments",
            "result_preview",
            "success",
            "error_message",
            "duration_ms",
            "created_at",
        ]


class RepositorySerializer(serializers.ModelSerializer):
    session_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = Repository
        fields = [
            "id",
            "url",
            "name",
            "source_type",
            "local_path",
            "default_branch",
            "last_analyzed_at",
            "created_at",
            "updated_at",
            "session_count",
        ]


class ResearchSessionSerializer(serializers.ModelSerializer):
    repository = RepositorySerializer(read_only=True)
    findings = FindingSerializer(many=True, read_only=True)
    tool_calls = ToolCallLogSerializer(many=True, read_only=True)

    class Meta:
        model = ResearchSession
        fields = [
            "id",
            "repository",
            "question",
            "final_answer",
            "status",
            "error_message",
            "token_usage",
            "metadata",
            "findings",
            "tool_calls",
            "created_at",
            "updated_at",
            "completed_at",
        ]


class ResearchSessionListSerializer(serializers.ModelSerializer):
    repository = RepositorySerializer(read_only=True)

    class Meta:
        model = ResearchSession
        fields = [
            "id",
            "repository",
            "question",
            "status",
            "created_at",
            "completed_at",
        ]


class CreateResearchSessionSerializer(serializers.Serializer):
    repo_url = serializers.CharField(max_length=1000)
    question = serializers.CharField()

    def validate_question(self, value: str) -> str:
        value = value.strip()
        if len(value) < 10:
            raise serializers.ValidationError("Question must be at least 10 characters.")
        return value

    def validate_repo_url(self, value: str) -> str:
        value = value.strip()
        if not value:
            raise serializers.ValidationError("Repository URL or local path is required.")
        return value
