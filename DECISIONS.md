# Decisions

## Architecture

This project is intentionally small: a Django REST Framework API calls a service layer that prepares a repository, creates a research session, runs a bounded agent, and persists the result. I kept the API synchronous for the first version because the take-home should be easy to run and review. A production version would move research execution to Celery or another worker system, but adding that now would mostly add operational weight rather than demonstrate the core agent/database design.

The service layer is split into clear pieces: `RepositoryManager` validates public GitHub URLs or local paths and clones/reuses repositories; `CodeTools` exposes safe file listing, lexical search, file reading, and snippets; `DatabaseTools` lets the agent read prior findings and save new findings; `AgentOrchestrator` owns the loop and stopping conditions; provider-specific LLM clients are isolated in `research/services/llm.py`.

## Database Schema

The schema has four main models. `Repository` stores one researched codebase and its local path. `ResearchSession` stores one question and final answer for a repository. `ToolCallLog` stores raw tool executions for audit/debugging. `Finding` stores curated evidence used to support answers.

The important choice is separating `ToolCallLog` from `Finding`. Tool calls answer “what did the agent do?” Findings answer “what evidence mattered?” That separation makes the database useful for both debugging the workflow and reusing previous research in future sessions. `Finding` includes a denormalized repository foreign key as well as the session so prior findings can be fetched directly for the same repository without awkward joins.

## Agent Design

The agent supports tool calling with OpenAI-compatible providers (Groq, OpenAI, Ollama) and Anthropic. Before asking the LLM to synthesize, the orchestrator now performs a small evidence-first pass: it checks previous findings, lists files, searches the most likely source directory, reads snippets, and saves findings. The model can still make additional targeted tool calls, but a session cannot be considered well-supported unless it has saved evidence and file/line citations. This avoids the failure mode where a model answers from general knowledge without actually inspecting the repository.

There is also a deterministic heuristic provider. It exists so tests and no-key demos can still exercise the same code/database tools and persistence flow. It is deliberately conservative and labels itself in the final answer, so it does not pretend to be equivalent to LLM synthesis.

## Trade-Offs

Search is lexical rather than vector-based; for the intended weekend scope, lexical search plus snippets is easier to inspect and explain. I did not add authentication, Celery, WebSockets, a frontend, or private GitHub support because they are outside the evaluation focus.

## AI Tool Usage

I used Codex to help read the task, scaffold the Django project, implement service boundaries, write tests, and draft documentation. I kept the architecture, scope control, data model, safety checks, and final review under manual direction. The main useful AI contribution was speeding up boilerplate and surfacing edge cases like path traversal and no-key demo behavior. The parts that needed manual judgment were deciding what not to build, keeping the agent loop bounded, and making persistence meaningful rather than a log dump.

## What I Would Change At Scale

At scale I would run sessions asynchronously, stream progress, add per-user auth, store repository metadata and commit hashes, use semantic indexing for large repositories, add stronger observability, and periodically refresh cloned repos. I would also add richer evaluation tests around answer faithfulness, because the hardest production problem is not making an agent answer, but proving that the answer is grounded in inspected code.

