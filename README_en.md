# TRE: Text TRPG Engine

Chinese Version: [README.md](README.md) | English Version: `README_en.md`

## Table of Contents

- [Overview](#overview)
- [Core Features](#core-features)
- [Glossary](#glossary)
- [Architecture & Minimal Turn Flow](#architecture--minimal-turn-flow)
- [Quick Start](#quick-start)
- [Running & Playing](#running--playing)
- [Common Dev Commands](#common-dev-commands)
- [Configuration Guide](#configuration-guide)
- [API Overview](#api-overview)
- [Main Directories & Entry Points](#main-directories--entry-points)
- [Known Limitations](#known-limitations)
- [Contributing](#contributing)
- [FAQ](#faq)
- [Version Info](#version-info)

## Overview

TRE (Text TRPG Engine) is a text-based TRPG engine built on the principle of **deterministic-logic-first**. It anchors numerical rules, state changes, and persistent facts in code and SQLite, while confining natural language understanding and narrative expression to the Agent layer. The goal is to build an AI-powered tabletop RPG engine skeleton that is continuously playable, rollback-capable, verifiable, and extensible.

## Core Features

- **Deterministic Logic First**: Action validation, numerical resolution, and state mutations are governed by backend rules and the database.
- **Graceful Agent Degradation**: NLU, GM, and outer-loop evolution can connect to real LLMs, or fall back to rule-based or template-driven paths.
- **Dual-Track Workflow**: The inner loop (LangGraph StateGraph-driven synchronous main loop) and outer loop (LlamaIndex Workflows-driven async event flow) run decoupled — the inner loop handles player turns, the outer loop handles world evolution and async compensation.
- **Dual Web API**: Both standard JSON turn endpoints and SSE streaming turn endpoints are available.
- **Structured Scene Snapshots**: Each turn returns location, exits, visible objects, available actions, and recommended actions.
- **Rollback-capable Sandbox**: Active/Shadow dual-table snapshots with sandbox commit/discard support.
- **MOD & RAG Extensibility**: MOD layered overrides and read-only RAG context supplementation.
- **Built-in Observability**: Main loop, event bus, outer loop, TurnTrace, and SSE events are all traceable.

## Glossary

- **TRPG**: Tabletop Role-Playing Game.
- **NLU**: Natural Language Understanding.
- **GM**: Game Master.
- **RAG**: Retrieval-Augmented Generation.
- **MOD**: Modification, an extension module.
- **SSE**: Server-Sent Events, used for streaming responses.
- **Active/Shadow**: The main state table and the sandbox state table, used to isolate narrative changes that have not yet been merged into the main timeline.
- **LangGraph**: A state-graph-based synchronous workflow framework used for the inner loop turn-processing main loop.
- **Workflows**: LlamaIndex's async event-driven workflow framework, used for the outer loop world evolution and event compensation.
- **AST**: Abstract Syntax Tree, used for safe whitelist-based expression evaluation in task scripts.

## Architecture & Minimal Turn Flow

### Four-Layer Architecture

- **Resource Layer**: `docs/`, `mods/`, RAG indices, and external model configuration.
- **Persistence Layer**: SQLite, Pydantic contracts, Active/Shadow dual-table snapshots.
- **Logic Layer**: Inner loop (LangGraph StateGraph-driven synchronous main loop), event bus, scene snapshots, deterministic tools, outer loop bridge (LlamaIndex Workflows).
- **Intelligence Layer**: NLUAgent, GMAgent, ClarifierAgent, EvolutionAgent.

### Dual-Track Workflow

The engine uses a decoupled dual-track architecture:

- **Inner Loop**: A synchronous turn-processing loop based on LangGraph StateGraph. The flow is: NLU → Scene Building → GM Rendering → Response Output → Action Resolution. Each turn completes synchronously and returns results directly to the player.
- **Outer Loop**: An async event-processing flow based on LlamaIndex Workflows. It listens for events such as `state_changed`, `turn_ended`, and `world_evolution`, and handles world evolution, memory summarization, compensation replay, and other non-immediate tasks concurrently.

### Minimal Turn Flow

1. Player input enters the inner loop main loop.
2. `NLUAgent` parses natural language into a structured action.
3. The main loop reads character state and `SceneSnapshot`, performing validation, clarification, deterministic resolution, state writes, and turn advancement.
4. `GMAgent` renders the narrative; when a model is unavailable, it falls back to template or deterministic paths.
5. The Web API outputs either a standard JSON response or SSE streaming events.
6. The outer loop asynchronously processes events such as `state_changed`, `turn_ended`, and `world_evolution`.
7. RAG provides read-only context supplementation and does not participate in action validation, numerical resolution, or state writes.

## Quick Start

### 1. Environment & Dependencies

- Python: `3.14+`
- Using `uv` is recommended for automatic environment creation and dependency installation. See the [uv installation guide](https://docs.astral.sh/uv/getting-started/installation/):

```bash
uv sync
```

- If you prefer the traditional approach, you can also install dependencies with `pip`:

```bash
pip install -r requirements.txt
```

### 2. Configure Models (Optional)

- RAG configuration: `config/rag_config.yml`
- Agent configuration: `config/agent_model_config.yml`

The verified local model combination for this repository is `ollama/qwen3:8b` (LLM) and `ollama/bge-m3` (embedding).

### 3. Initialize State, Knowledge Base, and MODs

If you already created an environment using `uv sync`, you can also run these commands with `uv run`:

```bash
uv run python state/tools/db_initializer.py
uv run python tools/doc_importer.py docs/ --group core --sync
uv run python tools/mod_manager.py scan
```

Notes:

- `docs/` is ignored by `.gitignore` by default. You need to place rulebooks or setting documents there before importing.
- If you skip manual initialization, `uv run python app.py` will attempt to auto-complete SQLite and vector indices on startup.
- If vector index initialization fails, a warning is logged, and the system degrades to no-RAG read-only context while the Web service continues to start.

### 4. Start the Server

```bash
uv run python app.py
```

## Running & Playing

After starting the server, the following entry points are available:

- Recommended new frontend: `http://localhost:5000/app`
- Legacy playground: `http://localhost:5000/play`

Frontend development and build commands must be executed in the `frontend/` directory:

```bash
npm install
npm run dev
npm run build
```

Further notes:

- For the player experience walkthrough, see [PLAY_GUIDE.md](PLAY_GUIDE.md).
- `/app` is the currently recommended play entry point; `/play` is retained for compatibility, comparison, and debugging verification.

## Common Dev Commands

### Initialize the Database

Creates or rebuilds `state/core_data/tre_state.db` and writes seed data from `state/data/`.

```bash
python state/tools/db_initializer.py
```

Common scenarios:

- First-time project run.
- Database is missing or needs a playback state reset.
- After modifying `state/models/` or `state/data/` to rebuild the local state store.

### Import Knowledge Base & Rebuild Index

Registers rulebooks, setting documents, or other materials from `docs/` into `config/rag_import_rules.json`, and rebuilds `knowledge_base/indices/` with `--sync`.

```bash
python tools/doc_importer.py docs/ --group core --sync
```

Common parameters:

- `path`: The file or directory to import, e.g., `docs/`, `docs/rules.md`
- `--group <name>`: Knowledge base group name, e.g., `core`, `rules`, `mod_xxx`
- `--tags tag1,tag2`: Additional tags
- `--desc "description"`: Group description
- `--sync`: Immediately rebuild vector indices after import
- `--mineru`: Force MinerU export directory processing

When run without parameters, it syncs indices based on the existing `config/rag_import_rules.json`:

```bash
python tools/doc_importer.py
```

### Scan & Register MODs

Scans `mods/` for directories containing `mod_info.json` and updates `config/mod_registry.yml`.

```bash
python tools/mod_manager.py scan
```

The registry records:

- `enabled`: Whether the MOD is enabled
- `priority`: Load priority (higher values take precedence)
- `conflict_strategy`: Field conflict handling strategy
- `hooks_manifest`: Hooks, trigger points, and write fields declared by the MOD

### Generate JSON Schemas

Regenerates JSON schemas from the Pydantic models in `state/models/`.

```bash
python state/tools/generate_schemas.py
```

### Validate RAG & Outer Loop

```bash
python -m tools.rag.main_loop_rag_smoke
python -m tools.rag.main_loop_rag_integration_check
python -m game_workflows.outer_loop_smoke
```

These commands verify the RAG read path, main-loop integration, and outer-loop event dispatch.

### Log Verification & Compensation Replay

```bash
python -m tools.logs.check_runtime_logs
python -m tools.logs.check_runtime_logs --since-minutes 30
python -m tools.logs.replay_outer_outbox --limit 50
```

- `check_runtime_logs`: Checks whether the main loop, event bus, and outer loop have left runtime evidence.
- `replay_outer_outbox`: Replays pending events in the outer-loop compensation queue.

## Configuration Guide

### `config/rag_config.yml`

Controls the RAG knowledge base, LLM, embedding, and graph construction.

Commonly modified fields:

- `llm.provider` / `llm.model` / `llm.base_url` / `llm.api_key`: RAG-side LLM configuration
- `embedding.provider` / `embedding.model` / `embedding.base_url` / `embedding.api_key`: Embedding model configuration
- `property_graph.enabled`: Whether to build the property graph
- `property_graph.extraction_prompt`: Graph triple extraction prompt
- `metadata_extraction.enable_custom_scoring`: Whether to enable LLM custom importance scoring

Local Ollama example:

```yaml
llm:
  provider: "ollama"
  model: "qwen3:8b"
  base_url: "http://localhost:11434"

embedding:
  provider: "ollama"
  model: "bge-m3"
  base_url: "http://localhost:11434"
```

After modifying the embedding or import rules, re-sync the knowledge base:

```bash
python tools/doc_importer.py --sync
```

### `config/agent_model_config.yml`

Controls whether NLU, GM, evolution, and other Agents call real models, and which model profile each binds to.

Core structure:

- `defaults`: Agent default on/off, mode, timeout, and retry strategy
- `profiles.llm`: Reusable LLM connection configurations
- `profiles.embedding`: Reusable embedding configurations
- `bindings.agents.nlu`: NLU binding; currently `rule_first`
- `bindings.agents.gm`: GM binding; currently `llm_first`
- `bindings.agents.evolution`: Outer-loop evolution Agent binding; currently disabled by default

To verify the purely deterministic main loop, disable both NLU and GM models:

```yaml
bindings:
  agents.nlu:
    enabled: false
    mode: "deterministic"
    llm_profile: null
  agents.gm:
    enabled: false
    mode: "deterministic"
    llm_profile: null
```

If the RAG index does not exist, whether to auto-initialize on startup is governed by `rag.auto_initialize` in the layered rules snapshot. For purely deterministic verification, explicitly disable it:

```json
{
  "rag": {
    "read_only_enabled": false,
    "auto_initialize": false
  }
}
```

### `config/main_loop_rules.json`

The base-layer configuration file for main-loop rules. The engine uses the "layered merged rules snapshot" at runtime, not this single file alone.

Rule loading order (later layers override earlier ones):

1. Built-in defaults `DEFAULT_MAIN_LOOP_RULES`
2. `config/main_loop_rules.json`
3. Enabled MOD rule overrides
4. Scenario rule overrides (env var `LLMTRE_SCENARIO_RULES_PATH`)
5. Additional rule overrides (env var `LLMTRE_MAIN_LOOP_RULES_EXTRA`)

MOD rule override files support the following paths (checked in order):

- `mods/<mod_id>/main_loop_rules.override.json`
- `mods/<mod_id>/rules/main_loop_rules.override.json`
- `mods/<mod_id>/rules/main_loop_rules.json`

Commonly modified fields:

- `nlu.action_keywords`: Action keyword mappings
- `nlu.target_aliases` / `location_aliases` / `item_aliases`: Target, location, and item aliases
- `resolution`: Deterministic resolution rules, e.g., attack DC, damage dice, movement cost, rest recovery
- `rag.read_only_enabled`: Whether the main loop reads RAG context
- `rag.auto_initialize`: Whether to allow runtime auto-initialization of RAG when vector indices are missing
- `memory.summary_step`: Memory summary compression interval
- `memory.summary_context_size`: Maximum context window for memory construction
- `outer_loop`: Outer-loop event dispatch, compensation replay, timeout, and world evolution interval
- `scene_defaults`: Default scenes, available actions, and recommended actions
- `narrative_templates`: Narrative templates to use when a model is unavailable

Scenario override example (Windows PowerShell):

```powershell
$env:LLMTRE_SCENARIO_RULES_PATH = "D:\path\to\scenario_rules.json"
uv run python app.py
```

Additional override example (multiple files):

```powershell
$env:LLMTRE_MAIN_LOOP_RULES_EXTRA = "D:\a.json;D:\b.json"
uv run python app.py
```

### `.agent_context/`

Stores local Agent runtime context specifications and long-term narrative summaries.

Core files:

- `AGENTS.md`: Agent context layering, read/write boundaries, and collaboration specifications
- `OPS.md`: Tool invocation, data flow, and error logging specifications
- `MEMORY.md`: Cross-session long-term story summary pool

At runtime, the main loop reads `.agent_context/MEMORY.md` in a read-only fashion, filters out empty templates and placeholder comments, and merges it with the Web session's recent memory into `SceneSnapshot.recent_memory`. This content only affects the Agent's narrative context and does not participate in action validation, numerical resolution, or state writes.

### `config/rag_import_rules.json`

Records knowledge base groups, tags, and file paths. It is recommended to update this file via `tools/doc_importer.py` rather than editing it manually.

Group field meanings:

- `group_name`: Group name
- `description`: Group description
- `tags`: Retrieval tags
- `file_paths`: Document paths included in the group
- `enable_graph`: Whether the group participates in graph construction

### `config/mod_registry.yml`

Records currently scanned MODs along with their enable status, priority, conflict strategy, and hook manifest. Typically generated or synced by `python tools/mod_manager.py scan`.

Commonly modified fields:

- `active_mods[].enabled`: Temporarily enable/disable a MOD
- `active_mods[].priority`: Adjust MOD override order
- `active_mods[].conflict_strategy`: Adjust conflict handling strategy

## API Overview

- Create session: `POST /api/sessions`
- Standard turn: `POST /api/sessions/{session_id}/turns`
- SSE streaming turn: `POST /api/sessions/{session_id}/turns/stream`

## Main Directories & Entry Points

- `agents/`: Intelligent agents (NLU, GM, evolution, etc.)
- `config/`: RAG, Agent model, main-loop rules, MOD registry, and other configuration
- `core/`: Central event bus and runtime logging infrastructure
- `game_workflows/`: Main loop, outer-loop bridge, RAG read-only bridge, and scene helper logic
- `state/`: Pydantic data contracts, seed data, SQLite initialization, and runtime schemas
- `tools/`: Deterministic tools, RAG import, MOD management, log verification, and compensation replay tools
- `web_api/`: Flask contract API, Blueprints, and `/play` page entry
- `mods/`: MOD extensions and scripts
- `static/`: Legacy playground frontend scripts and styles
- `templates/`: Flask page templates
- `frontend/`: React + Vite + TypeScript frontend project, entry at `/app`
- `tests/`: pytest regression tests
- `docs/`: Local rulebook and setting document input directory, Git-ignored by default
- `knowledge_base/`: RAG vector and graph index output directory
- `.agent_context/`: Local Agent context specifications and long-term narrative summaries
- `app.py`: Flask development server entry point
- `pyproject.toml`: Project metadata, packaging configuration, and lint/type-check settings

## Known Limitations

- `/app` (React) and `/play` (legacy) currently coexist. Their API contracts are consistent, but the presentation layer and debug rendering are not identical.
- `/app` top toolbar and scene card have been deduplicated: `New Session` / `Load` appear only in the top toolbar.
- `/app` character status card uses the data returned by the backend after session creation or loading. Before a session is created, placeholder values `--` are displayed.
- `/app` character status summary and status labels are provided by the backend `active_character.status_summary/status_effects/state_flags/status_context`. The frontend only displays; it does not infer status.
- `/app` debug console uses a fixed top-bottom layout: the top section has `Status / Trace / Logs / Memory` tabs, and the bottom section has the corresponding functional area.
- The A1 page directly exposes `Merge into Mainline` / `Rollback Sandbox` buttons, but normal new sessions are not sandbox branches by default.
- Task script evaluation has been switched to AST whitelist expression evaluation, but this implementation is not a strong security sandbox. In production, script sources must still be trusted.

## Contributing

### Reporting Issues

- Use [GitHub Issues](https://github.com/iambupu/llmtre/issues) to report bugs or suggest features.
- Provide a detailed description, reproduction steps, and environment information.

### Submitting Code

1. Fork this repository.
2. Create a feature branch: `git checkout -b feature/your-feature`
3. Commit your changes: `git commit -m "Add your feature"`
4. Push the branch: `git push origin feature/your-feature`
5. Create a Pull Request.

### Code Standards

- Lint check: `python -m ruff check .`
- Type check: `python -m mypy .`
- Regression tests: `python -m pytest tests -q`

### License

This project is licensed under the [GNU GPL v3](LICENSE).

## FAQ

### How do I start developing?

1. Clone the repo: `git clone https://github.com/iambupu/llmtre.git`
2. It is recommended to use `uv sync` to install dependencies; you can also use `pip install -r requirements.txt`
3. Initialize the database: `uv run python state/tools/db_initializer.py`
4. Start the server: `uv run python app.py`

### What hardware is needed?

- Python 3.14+
- To run local models, a GPU-capable device is recommended.

### How do I customize rules?

Modify `config/main_loop_rules.json`, or add and enable MOD rule overrides.

### What if I encounter problems?

First, check the runtime logs:

```bash
python -m tools.logs.check_runtime_logs
```

## Version Info

- Current version: A2 (Alpha 2)
- Changelog: see [CHANGELOG.md](CHANGELOG.md)
