# Codebase Research Agent

A Django REST API for researching public GitHub repositories or local codebases with a bounded tool-calling agent. The API accepts a repository source and a natural-language question, inspects the code through explicit tools, persists the session, and returns an answer with file/line evidence.

## Features

- Django + Django REST Framework API
- Repository records, research sessions, findings, and tool-call logs
- Public GitHub clone/reuse plus local path support
- Safe code tools: list files, search code, read files, read snippets
- Path traversal protection for all file-reading tools
- Database tools for previous findings and past sessions
- Evidence-first agent flow that saves code findings before LLM synthesis
- OpenAI-compatible providers (Groq, OpenAI, Ollama), Anthropic, and deterministic heuristic fallback for no-key demos/tests
- Seed command for sample database records
- Focused tests for models, API, and code tools

## Setup

```powershell
venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
```

By default the project uses SQLite if no database env vars are set. For PostgreSQL:

```powershell
docker compose up -d postgres
```

Then uncomment the `POSTGRES_*` values in `.env.example`, or set:

```text
DATABASE_URL=postgresql://postgres:postgres@localhost:5432/codebase_research
```

Run migrations:

```powershell
python manage.py migrate
```

Start the API:

```powershell
python manage.py runserver
```

## LLM Configuration

The agent supports tool calling with OpenAI-compatible providers (Groq, OpenAI, Ollama) and Anthropic.

Groq:

```text
AGENT_PROVIDER=groq
GROQ_API_KEY=your-key
GROQ_MODEL=openai/gpt-oss-120b
```

Ollama (local, no key):

```text
AGENT_PROVIDER=ollama
OLLAMA_BASE_URL=http://localhost:11434/v1
OLLAMA_MODEL=llama3.2
```

OpenAI:

```text
AGENT_PROVIDER=openai
OPENAI_API_KEY=your-key
OPENAI_MODEL=gpt-4.1-mini
OPENAI_BASE_URL=https://api.openai.com/v1
```

Anthropic:

```text
AGENT_PROVIDER=anthropic
ANTHROPIC_API_KEY=your-key
ANTHROPIC_MODEL=claude-sonnet-4-5-20250929
```

For local development without any API key, set `AGENT_PROVIDER=heuristic`. `AGENT_PROVIDER=auto` picks Groq/OpenAI/Anthropic when a corresponding API key is present; otherwise it falls back to heuristic mode.

## API Examples

Start a research session:

```powershell
curl -X POST http://127.0.0.1:8000/api/research-sessions/ `
  -H "Content-Type: application/json" `
  -d "{\"repo_url\":\"https://github.com/psf/requests\",\"question\":\"Where is the main request sending flow implemented?\"}"
```

Retrieve a session:

```powershell
curl http://127.0.0.1:8000/api/research-sessions/1/
```

List sessions for a repository:

```powershell
curl "http://127.0.0.1:8000/api/repositories/sessions/?repo_url=https://github.com/psf/requests"
```

List repositories:

```powershell
curl http://127.0.0.1:8000/api/repositories/
```

## Sample Data

Create demo records without calling an LLM:

```powershell
python manage.py seed_sample_data
```

The seeded data creates a sample `psf/requests` repository, one completed session, findings, and tool-call logs.

## Tests

```powershell
python manage.py test
```

## Project Structure

```text
config/                  Django settings and root URLs
research/models.py       Repository, ResearchSession, Finding, ToolCallLog
research/views.py        REST API endpoints
research/services/       Repository manager, code tools, DB tools, agent, LLM client
research/management/     Sample-data command
research/tests.py        Focused test suite
```

## Known Limitations

- Research runs synchronously in the request/response cycle.
- The code search is lexical, not semantic.
- Heuristic mode is a fallback, not a replacement for LLM synthesis.
- GitHub support is limited to public HTTPS repositories.
- No authentication or multi-user isolation is included because the task does not require it.
