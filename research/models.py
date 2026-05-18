from django.db import models


class Repository(models.Model):
    class SourceType(models.TextChoices):
        GITHUB = "github", "GitHub"
        LOCAL = "local", "Local path"

    url = models.CharField(max_length=500, unique=True)
    name = models.CharField(max_length=255)
    source_type = models.CharField(
        max_length=20,
        choices=SourceType.choices,
        default=SourceType.GITHUB,
    )
    local_path = models.CharField(max_length=1000, blank=True)
    default_branch = models.CharField(max_length=255, blank=True)
    last_analyzed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name", "url"]

    def __str__(self) -> str:
        return self.name


class ResearchSession(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        RUNNING = "running", "Running"
        COMPLETED = "completed", "Completed"
        FAILED = "failed", "Failed"

    repository = models.ForeignKey(
        Repository,
        on_delete=models.CASCADE,
        related_name="sessions",
    )
    question = models.TextField()
    final_answer = models.TextField(blank=True)
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
    )
    error_message = models.TextField(blank=True)
    token_usage = models.JSONField(default=dict, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["repository", "-created_at"]),
            models.Index(fields=["status"]),
        ]

    def __str__(self) -> str:
        return f"{self.repository.name}: {self.question[:80]}"


class Finding(models.Model):
    session = models.ForeignKey(
        ResearchSession,
        on_delete=models.CASCADE,
        related_name="findings",
    )
    repository = models.ForeignKey(
        Repository,
        on_delete=models.CASCADE,
        related_name="findings",
    )
    file_path = models.CharField(max_length=1000)
    line_start = models.PositiveIntegerField(null=True, blank=True)
    line_end = models.PositiveIntegerField(null=True, blank=True)
    symbol = models.CharField(max_length=255, blank=True)
    note = models.TextField()
    excerpt = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["file_path", "line_start", "id"]
        indexes = [
            models.Index(fields=["repository", "file_path"]),
            models.Index(fields=["session", "file_path"]),
        ]

    def __str__(self) -> str:
        location = self.file_path
        if self.line_start:
            location = f"{location}:{self.line_start}"
        return location


class ToolCallLog(models.Model):
    session = models.ForeignKey(
        ResearchSession,
        on_delete=models.CASCADE,
        related_name="tool_calls",
    )
    tool_name = models.CharField(max_length=120)
    arguments = models.JSONField(default=dict, blank=True)
    result_preview = models.TextField(blank=True)
    success = models.BooleanField(default=True)
    error_message = models.TextField(blank=True)
    duration_ms = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "id"]
        indexes = [
            models.Index(fields=["session", "created_at"]),
            models.Index(fields=["tool_name"]),
        ]

    def __str__(self) -> str:
        return f"{self.tool_name} ({'ok' if self.success else 'failed'})"
