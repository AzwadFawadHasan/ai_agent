from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.test import SimpleTestCase, TestCase, override_settings
from rest_framework.test import APIClient

from .models import Finding, Repository, ResearchSession, ToolCallLog
from .services.agent import AgentOrchestrator
from .services.code_tools import CodeToolError, CodeTools
from .services.llm import LLMResponse


class ModelTests(TestCase):
    def test_session_finding_and_tool_call_relationships(self):
        repository = Repository.objects.create(
            url="https://github.com/example/project",
            name="example/project",
            local_path="/tmp/project",
        )
        session = ResearchSession.objects.create(
            repository=repository,
            question="Where is retry logic implemented?",
        )
        Finding.objects.create(
            repository=repository,
            session=session,
            file_path="app/retry.py",
            line_start=10,
            line_end=20,
            note="Retry logic lives here.",
        )
        ToolCallLog.objects.create(
            session=session,
            tool_name="search_code",
            arguments={"query": "retry"},
            result_preview="{}",
        )

        self.assertEqual(repository.sessions.count(), 1)
        self.assertEqual(session.findings.count(), 1)
        self.assertEqual(session.tool_calls.count(), 1)


class CodeToolsTests(SimpleTestCase):
    def setUp(self):
        self.temp_dir = TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        (self.root / "app").mkdir()
        (self.root / "app" / "client.py").write_text(
            "def send_request():\n"
            "    adapter = select_adapter()\n"
            "    return adapter.send()\n",
            encoding="utf-8",
        )
        (self.root / "outside.txt").write_text("secret", encoding="utf-8")
        self.tools = CodeTools(self.root / "app")

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_read_file_returns_line_numbers(self):
        result = self.tools.read_file("client.py")

        self.assertEqual(result["line_start"], 1)
        self.assertIn("1: def send_request():", result["content"])
        self.assertIn("3:     return adapter.send()", result["content"])

    def test_path_traversal_is_blocked(self):
        with self.assertRaises(CodeToolError):
            self.tools.read_file("../outside.txt")

    def test_search_code_finds_relevant_lines(self):
        result = self.tools.search_code("request send")

        self.assertEqual(result["count"], 2)
        self.assertEqual(result["matches"][0]["file_path"], "client.py")


class FakeLLMClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def respond(self, **kwargs):
        self.calls.append(kwargs)
        if self.responses:
            return self.responses.pop(0)
        return LLMResponse(text="")


@override_settings(AGENT_PROVIDER="openai", OPENAI_API_KEY="test-key")
class AgentOrchestratorTests(TestCase):
    def setUp(self):
        self.temp_dir = TemporaryDirectory()
        self.repo_path = Path(self.temp_dir.name)
        (self.repo_path / "client.py").write_text(
            "class Client:\n"
            "    def send_request(self):\n"
            "        return self.transport.send()\n",
            encoding="utf-8",
        )
        self.repository = Repository.objects.create(
            url=str(self.repo_path),
            name="demo",
            source_type=Repository.SourceType.LOCAL,
            local_path=str(self.repo_path),
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    @patch("research.services.agent.build_llm_client")
    def test_agent_collects_code_evidence_before_accepting_llm_answer(self, build_client):
        session = ResearchSession.objects.create(
            repository=self.repository,
            question="Where is send_request implemented?",
        )
        build_client.return_value = FakeLLMClient(
            [LLMResponse(text="It is implemented in the client layer.")]
        )

        result = AgentOrchestrator(session).run()

        self.assertEqual(result.status, ResearchSession.Status.COMPLETED)
        self.assertIn("client.py:", result.final_answer)
        self.assertGreaterEqual(result.findings.count(), 1)
        tool_names = list(result.tool_calls.values_list("tool_name", flat=True))
        self.assertIn("search_code", tool_names)
        self.assertIn("get_file_snippet", tool_names)
        self.assertIn("save_finding", tool_names)

    @patch("research.services.agent.build_llm_client")
    def test_agent_continues_when_provider_truncates_answer(self, build_client):
        session = ResearchSession.objects.create(
            repository=self.repository,
            question="Where is send_request implemented?",
        )
        fake_client = FakeLLMClient(
            [
                LLMResponse(
                    text="The request sending flow is in client.py:1-3 and",
                    finish_reason="length",
                ),
                LLMResponse(
                    text="it delegates to transport.send(). See client.py:1-3.",
                    finish_reason="stop",
                ),
            ]
        )
        build_client.return_value = fake_client

        result = AgentOrchestrator(session).run()

        self.assertEqual(result.status, ResearchSession.Status.COMPLETED)
        self.assertIn("transport.send()", result.final_answer)
        self.assertEqual(len(fake_client.calls), 2)


@override_settings(AGENT_PROVIDER="heuristic")
class ResearchSessionAPITests(TestCase):
    def setUp(self):
        self.temp_dir = TemporaryDirectory()
        self.repo_path = Path(self.temp_dir.name)
        (self.repo_path / "client.py").write_text(
            "class Client:\n"
            "    def send_request(self):\n"
            "        return self.transport.send()\n",
            encoding="utf-8",
        )
        self.client = APIClient()

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_create_research_session_runs_agent_and_persists_records(self):
        response = self.client.post(
            "/api/research-sessions/",
            {
                "repo_url": str(self.repo_path),
                "question": "Where is the request sending flow implemented?",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["status"], ResearchSession.Status.COMPLETED)
        self.assertGreaterEqual(len(response.data["tool_calls"]), 3)
        self.assertGreaterEqual(len(response.data["findings"]), 1)

    def test_retrieve_research_session(self):
        repository = Repository.objects.create(
            url=str(self.repo_path),
            name="demo",
            source_type=Repository.SourceType.LOCAL,
            local_path=str(self.repo_path),
        )
        session = ResearchSession.objects.create(
            repository=repository,
            question="Where is the request sending flow implemented?",
            status=ResearchSession.Status.COMPLETED,
            final_answer="See client.py:2-3.",
        )

        response = self.client.get(f"/api/research-sessions/{session.id}/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["id"], session.id)
        self.assertEqual(response.data["final_answer"], "See client.py:2-3.")
