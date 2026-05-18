from django.contrib import admin

from .models import Finding, Repository, ResearchSession, ToolCallLog


@admin.register(Repository)
class RepositoryAdmin(admin.ModelAdmin):
    list_display = ("name", "source_type", "url", "last_analyzed_at", "created_at")
    search_fields = ("name", "url", "local_path")
    list_filter = ("source_type",)


class FindingInline(admin.TabularInline):
    model = Finding
    extra = 0
    fields = ("file_path", "line_start", "line_end", "symbol", "note")


class ToolCallLogInline(admin.TabularInline):
    model = ToolCallLog
    extra = 0
    fields = ("tool_name", "success", "duration_ms", "result_preview")
    readonly_fields = ("result_preview",)


@admin.register(ResearchSession)
class ResearchSessionAdmin(admin.ModelAdmin):
    list_display = ("id", "repository", "status", "created_at", "completed_at")
    search_fields = ("question", "final_answer", "repository__url", "repository__name")
    list_filter = ("status", "repository__source_type")
    inlines = (FindingInline, ToolCallLogInline)


@admin.register(Finding)
class FindingAdmin(admin.ModelAdmin):
    list_display = ("file_path", "line_start", "line_end", "session", "created_at")
    search_fields = ("file_path", "note", "excerpt", "symbol")
    list_filter = ("repository",)


@admin.register(ToolCallLog)
class ToolCallLogAdmin(admin.ModelAdmin):
    list_display = ("tool_name", "session", "success", "duration_ms", "created_at")
    search_fields = ("tool_name", "result_preview", "error_message")
    list_filter = ("success", "tool_name")
