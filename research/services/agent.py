from __future__ import annotations

import json
import re
import time
from typing import Any, Callable

import httpx
from django.conf import settings
from django.utils import timezone

from research.models import ResearchSession, ToolCallLog

from .code_tools import CodeTools
from .db_tools import DatabaseTools
from .llm import LLMConfigurationError, LLMResponse, build_llm_client


SOURCE_SUFFIXES = {
    ".c",
    ".cc",
    ".cpp",
    ".cs",
    ".go",
    ".java",
    ".js",
    ".jsx",
    ".kt",
    ".php",
    ".py",
    ".rb",
    ".rs",
    ".swift",
    ".ts",
    ".tsx",
}


class AgentOrchestrator:
    def __init__(self, session: ResearchSession):
        self.session = session
        self.repository = session.repository
        self.code_tools = CodeTools(self.repository.local_path)
        self.db_tools = DatabaseTools(session)
        self.max_tool_calls = settings.AGENT_MAX_TOOL_CALLS
        self.tool_registry: dict[str, Callable[..., dict[str, Any]]] = {
            "list_files": self.code_tools.list_files,
            "search_code": self.code_tools.search_code,
            "read_file": self.code_tools.read_file,
            "get_file_snippet": self.code_tools.get_file_snippet,
            "save_finding": self.db_tools.save_finding,
            "get_previous_findings": self.db_tools.get_previous_findings,
            "list_past_sessions": self.db_tools.list_past_sessions,
        }

    def run(self) -> ResearchSession:
        self._mark_running()
        try:
            provider = self._select_provider()
            # Persist the provider choice early so failures still show what we attempted.
            self.session.metadata = {
                **self.session.metadata,
                "agent_provider": provider,
                **self._provider_metadata(provider),
            }
            self.session.save(update_fields=["metadata", "updated_at"])

            if provider == "anthropic":
                final_answer = self._run_anthropic_loop()
            elif provider in {"groq", "openai", "ollama"}:
                final_answer = self._run_openai_compatible_loop(provider)
            elif provider == "heuristic":
                final_answer = self._run_heuristic_loop()
            else:
                raise LLMConfigurationError(f"Unknown agent provider: {provider}")

            self.session.final_answer = final_answer
            self.session.status = ResearchSession.Status.COMPLETED
            self.session.completed_at = timezone.now()
            # Keep metadata in sync on success as well.
            self.session.metadata = {
                **self.session.metadata,
                "agent_provider": provider,
                **self._provider_metadata(provider),
            }
            self.session.save(
                update_fields=[
                    "final_answer",
                    "status",
                    "completed_at",
                    "metadata",
                    "updated_at",
                ]
            )
        except Exception as exc:
            self.session.status = ResearchSession.Status.FAILED
            self.session.error_message = str(exc)
            self.session.completed_at = timezone.now()
            self.session.save(
                update_fields=["status", "error_message", "completed_at", "updated_at"]
            )
        return self.session

    def _mark_running(self) -> None:
        self.session.status = ResearchSession.Status.RUNNING
        self.session.save(update_fields=["status", "updated_at"])

    def _select_provider(self) -> str:
        provider = settings.AGENT_PROVIDER.lower().strip()
        if provider and provider != "auto":
            return provider
        if settings.GROQ_API_KEY:
            return "groq"
        if settings.OPENAI_API_KEY:
            return "openai"
        if settings.ANTHROPIC_API_KEY:
            return "anthropic"
        if self._ollama_available():
            return "ollama"
        return "heuristic"

    def _ollama_available(self) -> bool:
        base_url = settings.OLLAMA_BASE_URL.rstrip("/")
        try:
            response = httpx.get(f"{base_url}/models", timeout=1.0)
            return response.status_code == 200
        except httpx.HTTPError:
            return False

    def _provider_metadata(self, provider: str) -> dict[str, Any]:
        if provider == "groq":
            return {
                "llm_base_url": "https://api.groq.com/openai/v1",
                "llm_model": settings.GROQ_MODEL,
            }
        if provider == "openai":
            return {
                "llm_base_url": settings.OPENAI_BASE_URL,
                "llm_model": settings.OPENAI_MODEL,
            }
        if provider == "ollama":
            return {
                "llm_base_url": settings.OLLAMA_BASE_URL,
                "llm_model": settings.OLLAMA_MODEL,
            }
        if provider == "anthropic":
            return {"llm_model": settings.ANTHROPIC_MODEL}
        return {}

    def _bootstrap_evidence(self) -> list[dict[str, Any]]:
        """Collect a small, bounded evidence set before trusting LLM synthesis."""
        evidence: list[dict[str, Any]] = []
        self._execute_tool("get_previous_findings", {"limit": 5})
        file_listing = self._execute_tool("list_files", {"path": "", "limit": 200})
        search_paths = self._candidate_search_paths(file_listing)

        matches: list[dict[str, Any]] = []
        for query in self._search_queries()[:3]:
            for path in search_paths:
                result = self._execute_tool(
                    "search_code",
                    {"query": query, "path": path, "max_results": 8},
                )
                if not isinstance(result, dict):
                    continue
                for match in result.get("matches", []):
                    matches.append({**match, "query": query})

        selected_matches = self._select_matches(matches, limit=4)
        for match in selected_matches:
            snippet = self._execute_tool(
                "get_file_snippet",
                {
                    "path": match["file_path"],
                    "line": match["line"],
                    "context": 20,
                },
            )
            if not isinstance(snippet, dict) or "content" not in snippet:
                continue

            note = (
                f"Evidence found while searching for {match['query']!r} near "
                f"line {match['line']}."
            )
            finding = self._execute_tool(
                "save_finding",
                {
                    "file_path": snippet["file_path"],
                    "line_start": snippet.get("line_start"),
                    "line_end": snippet.get("line_end"),
                    "note": note,
                    "excerpt": snippet.get("content", "")[:1600],
                },
            )
            evidence.append(
                {
                    "finding": finding,
                    "file_path": snippet["file_path"],
                    "line_start": snippet.get("line_start"),
                    "line_end": snippet.get("line_end"),
                    "note": note,
                    "excerpt": snippet.get("content", "")[:1600],
                }
            )

        self.session.metadata = {
            **self.session.metadata,
            "bootstrap_evidence_count": len(evidence),
            "bootstrap_search_paths": search_paths,
        }
        self.session.save(update_fields=["metadata", "updated_at"])
        return evidence

    def _initial_research_message(self, evidence: list[dict[str, Any]]) -> dict[str, str]:
        evidence_text = self._format_evidence_for_prompt(evidence)
        return {
            "role": "user",
            "content": (
                f"Repository: {self.repository.url}\n"
                f"Question: {self.session.question}\n\n"
                "I already inspected the repository with code/database tools and saved "
                "the evidence below. If this is enough, produce the final answer from "
                "only this evidence. If not, make targeted tool calls before answering. "
                "Keep the final answer concise, do not use code fences, and cite file "
                "paths with line ranges.\n\n"
                f"{evidence_text}"
            ),
        }

    def _candidate_search_paths(self, file_listing: dict[str, Any]) -> list[str]:
        files = file_listing.get("files", []) if isinstance(file_listing, dict) else []
        top_level_dirs = {
            file_path.split("/", 1)[0]
            for file_path in files
            if isinstance(file_path, str) and "/" in file_path
        }
        repo_slug = self.repository.name.rsplit("/", 1)[-1].lower()
        normalized_slug = repo_slug.replace("-", "_")

        candidates = []
        for option in (normalized_slug, repo_slug, "src", "app", "lib"):
            if option in top_level_dirs and option not in candidates:
                candidates.append(option)

        return candidates[:1] or [""]

    def _search_queries(self) -> list[str]:
        keywords = self._question_keywords()
        expanded = []
        for keyword in keywords:
            expanded.extend(self._expand_keyword(keyword))

        queries = []
        if expanded:
            queries.append(" ".join(expanded[:6]))
            queries.extend(term for term in expanded[:5] if len(term) >= 4)
        if self.session.question not in queries:
            queries.append(self.session.question)

        unique_queries = []
        for query in queries:
            normalized = query.strip()
            if normalized and normalized.lower() not in {item.lower() for item in unique_queries}:
                unique_queries.append(normalized)
        return unique_queries or [self.session.question]

    def _expand_keyword(self, keyword: str) -> list[str]:
        terms = [keyword]
        if keyword.endswith("ies") and len(keyword) > 4:
            terms.append(f"{keyword[:-3]}y")
        if keyword.endswith("y") and len(keyword) > 4:
            terms.append(keyword[:-1])
        if keyword.endswith("s") and len(keyword) > 4:
            terms.append(keyword[:-1])
        return terms

    def _select_matches(self, matches: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
        seen_locations = set()
        unique_matches = []
        for match in matches:
            key = (match.get("file_path"), match.get("line"))
            if not key[0] or not key[1] or key in seen_locations:
                continue
            seen_locations.add(key)
            unique_matches.append(match)

        unique_matches.sort(
            key=lambda match: (
                -self._match_priority(match.get("file_path", "")),
                -int(match.get("score") or 0),
                match.get("file_path", ""),
                int(match.get("line") or 0),
            )
        )
        return unique_matches[:limit]

    def _match_priority(self, file_path: str) -> int:
        suffix = ""
        if "." in file_path.rsplit("/", 1)[-1]:
            suffix = "." + file_path.rsplit(".", 1)[-1].lower()
        priority = 3 if suffix in SOURCE_SUFFIXES else 1
        lowered = file_path.lower()
        if "/docs/" in lowered or lowered.startswith("docs/"):
            priority -= 1
        if "/.github/" in lowered or lowered.startswith(".github/"):
            priority -= 2
        if "/.agents/" in lowered or lowered.startswith(".agents/"):
            priority -= 2
        return priority

    def _format_evidence_for_prompt(self, evidence: list[dict[str, Any]]) -> str:
        if not evidence:
            return "No strong code evidence was found during the initial search."

        blocks = []
        for item in evidence:
            location = item["file_path"]
            if item.get("line_start") and item.get("line_end"):
                location = f"{location}:{item['line_start']}-{item['line_end']}"
            blocks.append(
                "Evidence:\n"
                f"Location: {location}\n"
                f"Note: {item['note']}\n"
                f"Excerpt:\n{item['excerpt']}"
            )
        return "\n\n".join(blocks)

    def _run_anthropic_loop(self) -> str:
        client = build_llm_client("anthropic")
        evidence = self._bootstrap_evidence()
        messages: list[dict[str, Any]] = [self._initial_research_message(evidence)]
        seen_tool_calls: set[str] = set()
        tool_calls_used = 0

        while tool_calls_used < self.max_tool_calls:
            response = client.respond(
                system=self._system_prompt(),
                messages=messages,
                tools=self._anthropic_tool_schemas(),
            )
            self._merge_token_usage(response.token_usage)

            if not response.tool_calls:
                messages.append({"role": "assistant", "content": response.assistant_content})
                return self._finalize_llm_answer(client, messages, response)

            messages.append({"role": "assistant", "content": response.assistant_content})
            tool_results = []
            for tool_call in response.tool_calls:
                signature = f"{tool_call.name}:{json.dumps(tool_call.arguments, sort_keys=True)}"
                if signature in seen_tool_calls:
                    tool_result = {"error": "Repeated tool call skipped."}
                else:
                    seen_tool_calls.add(signature)
                    tool_result = self._execute_tool(tool_call.name, tool_call.arguments)
                    tool_calls_used += 1

                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": tool_call.id,
                        "content": json.dumps(tool_result, ensure_ascii=True),
                    }
                )

                if tool_calls_used >= self.max_tool_calls:
                    break

            messages.append({"role": "user", "content": tool_results})

        messages.append(
            {
                "role": "user",
                "content": (
                    "The tool-call budget is exhausted. Produce the best supported "
                    "answer from saved findings and tool results. Cite files and lines."
                ),
            }
        )
        response = client.respond(system=self._system_prompt(), messages=messages, tools=[])
        self._merge_token_usage(response.token_usage)
        messages.append({"role": "assistant", "content": response.assistant_content})
        return self._finalize_llm_answer(client, messages, response)

    def _run_openai_compatible_loop(self, provider: str) -> str:
        client = build_llm_client(provider)
        evidence = self._bootstrap_evidence()
        messages: list[dict[str, Any]] = [self._initial_research_message(evidence)]
        seen_tool_calls: set[str] = set()
        tool_calls_used = 0

        while tool_calls_used < self.max_tool_calls:
            response = client.respond(
                system=self._system_prompt(),
                messages=messages,
                tools=self._openai_tool_schemas(),
            )
            self._merge_token_usage(response.token_usage)

            messages.append(response.assistant_content or {"role": "assistant", "content": response.text})
            if not response.tool_calls:
                return self._finalize_llm_answer(client, messages, response)

            for tool_call in response.tool_calls:
                signature = f"{tool_call.name}:{json.dumps(tool_call.arguments, sort_keys=True)}"
                if signature in seen_tool_calls:
                    tool_result = {"error": "Repeated tool call skipped."}
                else:
                    seen_tool_calls.add(signature)
                    tool_result = self._execute_tool(tool_call.name, tool_call.arguments)
                    tool_calls_used += 1

                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "content": json.dumps(tool_result, ensure_ascii=True),
                    }
                )
                if tool_calls_used >= self.max_tool_calls:
                    break

        messages.append(
            {
                "role": "user",
                "content": (
                    "The tool-call budget is exhausted. Produce the best supported "
                    "answer from saved findings and tool results. Cite files and lines."
                ),
            }
        )
        response = client.respond(system=self._system_prompt(), messages=messages, tools=[])
        self._merge_token_usage(response.token_usage)
        messages.append(response.assistant_content or {"role": "assistant", "content": response.text})
        return self._finalize_llm_answer(client, messages, response)

    def _run_heuristic_loop(self) -> str:
        evidence = self._bootstrap_evidence()
        if not evidence:
            return (
                "I could not find strong textual matches for the question in this "
                "repository. I listed the repository files, but no finding was strong "
                "enough to cite confidently."
            )
        return self._answer_from_findings()

    def _finalize_llm_answer(
        self,
        client: Any,
        messages: list[dict[str, Any]],
        response: LLMResponse,
    ) -> str:
        text = response.text.strip()
        if text and self._looks_incomplete_answer(text, response.finish_reason):
            messages.append(
                {
                    "role": "user",
                    "content": (
                        "Your previous answer appears incomplete. Finish with a concise "
                        "complete answer under 250 words. Use only saved evidence and "
                        "include file:line citations. Do not use code fences."
                    ),
                }
            )
            continuation = client.respond(
                system=self._system_prompt(),
                messages=messages,
                tools=[],
            )
            self._merge_token_usage(continuation.token_usage)
            if continuation.text:
                text = f"{text}\n{continuation.text.strip()}".strip()

        if not text or not self._is_supported_answer(text):
            return self._answer_from_findings()
        return text

    def _looks_incomplete_answer(self, text: str, finish_reason: str) -> bool:
        stripped = text.strip()
        if finish_reason == "length":
            return True
        if stripped.count("```") % 2:
            return True
        return bool(
            re.search(
                r"\b(and|as|by|for|from|in|of|or|the|to|with|import)$",
                stripped,
                re.IGNORECASE,
            )
        )

    def _is_supported_answer(self, text: str) -> bool:
        findings = list(self.session.findings.all()[:8])
        if not findings:
            return False
        has_saved_path = any(finding.file_path in text for finding in findings)
        has_line_reference = bool(re.search(r":\d+(-\d+)?|\blines?\s+\d+", text, re.IGNORECASE))
        return has_saved_path and has_line_reference

    def _answer_from_findings(self) -> str:
        findings = list(self.session.findings.order_by("file_path", "line_start")[:6])
        if not findings:
            return (
                "I do not have enough saved evidence to answer confidently. No findings "
                "were saved for this session."
            )

        lines = [
            f"Based on the code I inspected, the likely answer is grounded in {len(findings)} saved finding(s):"
        ]
        for finding in findings:
            location = finding.file_path
            if finding.line_start and finding.line_end:
                location = f"{location}:{finding.line_start}-{finding.line_end}"
            elif finding.line_start:
                location = f"{location}:{finding.line_start}"
            lines.append(f"- {location}: {finding.note}")
        lines.append(
            "This fallback answer is intentionally conservative. Configure a real LLM provider "
            "for synthesis over the same tools, for example: AGENT_PROVIDER=groq (GROQ_API_KEY), "
            "AGENT_PROVIDER=openai (OPENAI_API_KEY), AGENT_PROVIDER=ollama (local), or "
            "AGENT_PROVIDER=anthropic (ANTHROPIC_API_KEY)."
        )
        return "\n".join(lines)

    def _execute_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        started = time.perf_counter()
        if name not in self.tool_registry:
            result = {"error": f"Unknown tool: {name}"}
            self._log_tool(name, arguments, result, False, "Unknown tool", started)
            return result

        try:
            result = self.tool_registry[name](**arguments)
            self._log_tool(name, arguments, result, True, "", started)
            return result
        except Exception as exc:
            result = {"error": str(exc)}
            self._log_tool(name, arguments, result, False, str(exc), started)
            return result

    def _log_tool(
        self,
        name: str,
        arguments: dict[str, Any],
        result: dict[str, Any],
        success: bool,
        error_message: str,
        started: float,
    ) -> None:
        duration_ms = int((time.perf_counter() - started) * 1000)
        preview = json.dumps(result, ensure_ascii=True, default=str)[:4000]
        ToolCallLog.objects.create(
            session=self.session,
            tool_name=name,
            arguments=arguments,
            result_preview=preview,
            success=success,
            error_message=error_message,
            duration_ms=duration_ms,
        )

    def _merge_token_usage(self, usage: dict[str, int]) -> None:
        if not usage:
            return
        current = dict(self.session.token_usage or {})
        for key, value in usage.items():
            current[key] = current.get(key, 0) + value
        self.session.token_usage = current
        self.session.save(update_fields=["token_usage", "updated_at"])

    def _question_keywords(self) -> list[str]:
        stop_words = {
            "about",
            "and",
            "does",
            "from",
            "how",
            "implemented",
            "internally",
            "the",
            "what",
            "where",
            "which",
            "with",
        }
        return [
            term.lower()
            for term in re.findall(r"[A-Za-z_][A-Za-z0-9_]{2,}", self.session.question)
            if term.lower() not in stop_words
        ]

    def _system_prompt(self) -> str:
        return (
            "You are a codebase research agent. You may only inspect the repository "
            "through the provided tools. Use previous findings when relevant, search "
            "before reading files, save concise findings for evidence, and answer only "
            "from inspected evidence. Always cite file paths and line ranges. Stop when "
            "you have enough evidence or the tool budget is nearly exhausted."
        )

    def _anthropic_tool_schemas(self) -> list[dict[str, Any]]:
        return [
            {
                "name": "get_previous_findings",
                "description": "Retrieve useful findings from past sessions for this repository.",
                "input_schema": {
                    "type": "object",
                    "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 20}},
                },
            },
            {
                "name": "list_past_sessions",
                "description": "List past research sessions for this repository.",
                "input_schema": {
                    "type": "object",
                    "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 20}},
                },
            },
            {
                "name": "list_files",
                "description": "List source files under a repository path.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "limit": {"type": "integer", "minimum": 1, "maximum": 500},
                    },
                },
            },
            {
                "name": "search_code",
                "description": "Search repository text files for relevant terms.",
                "input_schema": {
                    "type": "object",
                    "required": ["query"],
                    "properties": {
                        "query": {"type": "string"},
                        "path": {"type": "string"},
                        "max_results": {"type": "integer", "minimum": 1, "maximum": 50},
                    },
                },
            },
            {
                "name": "read_file",
                "description": "Read a line-numbered range from a repository file.",
                "input_schema": {
                    "type": "object",
                    "required": ["path"],
                    "properties": {
                        "path": {"type": "string"},
                        "start_line": {"type": "integer", "minimum": 1},
                        "end_line": {"type": "integer", "minimum": 1},
                        "max_lines": {"type": "integer", "minimum": 1, "maximum": 300},
                    },
                },
            },
            {
                "name": "get_file_snippet",
                "description": "Read a line-numbered snippet around a specific line.",
                "input_schema": {
                    "type": "object",
                    "required": ["path", "line"],
                    "properties": {
                        "path": {"type": "string"},
                        "line": {"type": "integer", "minimum": 1},
                        "context": {"type": "integer", "minimum": 1, "maximum": 80},
                    },
                },
            },
            {
                "name": "save_finding",
                "description": "Persist a concise evidence finding that may support the final answer.",
                "input_schema": {
                    "type": "object",
                    "required": ["file_path", "note"],
                    "properties": {
                        "file_path": {"type": "string"},
                        "line_start": {"type": "integer"},
                        "line_end": {"type": "integer"},
                        "symbol": {"type": "string"},
                        "note": {"type": "string"},
                        "excerpt": {"type": "string"},
                    },
                },
            },
        ]

    def _openai_tool_schemas(self) -> list[dict[str, Any]]:
        tools = []
        for tool in self._anthropic_tool_schemas():
            tools.append(
                {
                    "type": "function",
                    "function": {
                        "name": tool["name"],
                        "description": tool.get("description", ""),
                        "parameters": tool.get("input_schema") or {"type": "object", "properties": {}},
                    },
                }
            )
        return tools
