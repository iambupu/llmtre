# TRE: Text TRPG Engine

English Version: `README_en.md` | 中文版本: [README.md](README.md)

## Table of Contents

- [Project Overview](#project-overview)
- [Core Features](#core-features)
- [Glossary](#glossary)
- [Architecture and Minimal Flow](#architecture-and-minimal-flow)
- [Quick Start](#quick-start)
- [Running and Playing](#running-and-playing)
- [Common Development Commands](#common-development-commands)
- [Configuration Files Guide](#configuration-files-guide)
- [API Overview](#api-overview)
- [Main Directories and Entry Points](#main-directories-and-entry-points)
- [Known Limitations](#known-limitations)
- [Contributing](#contributing)
- [FAQ](#faq)
- [Version Information](#version-information)

## Project Overview

TRE (Text TRPG Engine) is a text TRPG engine built around a "deterministic logic first" principle. It fixes numeric rules, state transitions, and persisted facts in code and SQLite, while limiting natural language understanding and narrative rendering to the agent layer. The goal is to provide a playable, rollbackable, verifiable, and extensible AI tabletop RPG engine skeleton.

## Core Features

- **Deterministic logic first**: Action legality, numeric resolution, and state writes are governed by backend rules and the database.
- **Agent fallback support**: NLU, GM, and outer-loop evolution can use real models or fall back to rule-based or template-based paths.
- **Dual Web interfaces**: Both ordinary JSON turn APIs and SSE streaming turn APIs are available.
- **Structured scene snapshots**: Each turn returns location, exits, visible objects, available actions, and suggested actions.
- **Rollbackable sandbox**: Supports Active/Shadow table snapshots and sandbox commit/discard.
- **MOD and RAG extensibility**: Supports layered MOD overrides and read-only RAG context.
- **Built-in observability**: Main loop, event bus, outer loop, TurnTrace, and SSE events are all traceable.

## Glossary

- **TRPG**: Tabletop Role-Playing Game.
- **NLU**: Natural Language Understanding.
- **GM**: Game Master.
- **RAG**: Retrieval-Augmented Generation.
- **MOD**: Modification or extension module.
- **SSE**: Server-Sent Events, used for streaming responses.
- **Active/Shadow**: The mainline state table and sandbox state table used to isolate narrative changes that have not been merged into the mainline.

## Architecture and Minimal Flow

### Four-layer architecture

- **Resource layer**: `docs/`, `mods/`, RAG indexes, and external model configuration.
- **Persistence layer**: SQLite, Pydantic contracts, and Active/Shadow dual-table snapshots.
- **Logic layer**: Main loop, event bus, scene snapshots, deterministic tools, and outer-loop bridging.
- **Intelligence layer**: `NLUAgent`, `GMAgent`, `ClarifierAgent`, and evolution agents.

### Minimal turn flow

1. Player input enters the inner main loop.
2. `NLUAgent` parses natural language into a structured action.
3. The main loop reads character state and `SceneSnapshot`, then performs validation, clarification, deterministic resolution, state writing, and turn advancement.
4. `GMAgent` renders the narrative; if the model is unavailable, it falls back to a template or deterministic path.
5. The Web API returns either an ordinary JSON response or SSE streaming events.
6. The outer loop asynchronously handles events such as `state_changed`, `turn_ended`, and `world_evolution`.
7. RAG supplements context in read-only mode and does not participate in action legality, numeric resolution, or state writes.

## Quick Start

### 1. Environment and dependencies

- Python: `3.14+`
- Recommended: use `uv` to create the environment and install dependencies. See the official [uv installation guide](https://docs.astral.sh/uv/getting-started/installation/):

```bash
uv sync
```

- If you prefer the traditional flow, you can still install dependencies with `pip`:

```bash
pip install -r requirements.txt
```

### 2. Configure models (optional)

- RAG configuration: `config/rag_config.yml`
- Agent configuration: `config/agent_model_config.yml`

The currently verified local model combination in this repository is `ollama/qwen3:8b` for the LLM and `ollama/bge-m3` for embeddings.

### 3. Initialize state, knowledge base, and MOD

If you have already run `uv sync`, you can execute the following commands with `uv run`:

```bash
uv run python state/tools/db_initializer.py
uv run python tools/doc_importer.py docs/ --group core --sync
uv run python tools/mod_manager.py scan
```

Notes:

- `docs/` is ignored by `.gitignore` by default, so you need to place your rulebooks or setting documents there before importing them.
- If you skip manual initialization, `uv run python app.py` will also try to initialize SQLite and vector indexes automatically at startup.
- If vector index initialization fails, the service logs a warning and degrades to running without read-only RAG context, while the Web service still starts.

### 4. Start the service

```bash
uv run python app.py
```

## Running and Playing

After the service starts, you can use the following entry points:

- Recommended new frontend: `http://localhost:5000/app`
- Legacy playground: `http://localhost:5000/play`

Frontend development and build commands must be run inside the `frontend/` directory:

```bash
npm install
npm run dev
npm run build
```

Further references:

- Player flow guide: [PLAY_GUIDE_en.md](PLAY_GUIDE_en.md)
- `/app` is the recommended play entry point; `/play` is retained for compatibility, comparison, and debugging acceptance.

## Common Development Commands

### Initialize the database

Used to create or rebuild `state/core_data/tre_state.db` and write seed data from `state/data/`.

```bash
python state/tools/db_initializer.py
```

Common cases:

- First project startup
- Missing database file or need to reset play state
- Rebuild needed after modifying `state/models/` or `state/data/`

### Import the knowledge base and rebuild indexes

Used to register rulebooks, setting documents, or other materials from `docs/` into `config/rag_import_rules.json`, and rebuild `knowledge_base/indices/` when `--sync` is provided.

```bash
python tools/doc_importer.py docs/ --group core --sync
```

Common parameters:

- `path`: File or directory to import, such as `docs/` or `docs/rules.md`
- `--group <name>`: Knowledge base group name, such as `core`, `rules`, or `mod_xxx`
- `--tags tag1,tag2`: Extra tags
- `--desc "Description"`: Group description
- `--sync`: Rebuild the vector index immediately after import
- `--mineru`: Force MinerU export directory handling

When run without arguments, it syncs directly from the existing `config/rag_import_rules.json`:

```bash
python tools/doc_importer.py
```

### Scan and register MODs

Used to scan MOD directories under `mods/` that contain `mod_info.json`, then update `config/mod_registry.yml`.

```bash
python tools/mod_manager.py scan
```

The registry records:

- `enabled`: Whether the MOD is enabled
- `priority`: Load priority; higher values win
- `conflict_strategy`: Field conflict resolution strategy
- `hooks_manifest`: Hooks declared by the MOD, along with trigger points and write fields

### Generate JSON Schema

Used to regenerate JSON Schema from Pydantic models in `state/models/`.

```bash
python state/tools/generate_schemas.py
```

### Verify RAG and the outer loop

```bash
python -m tools.rag.main_loop_rag_smoke
python -m tools.rag.main_loop_rag_integration_check
python -m game_workflows.outer_loop_smoke
```

These commands verify the RAG read path, main loop integration, and outer-loop event delivery.

### Run log acceptance and compensation replay

```bash
python -m tools.logs.check_runtime_logs
python -m tools.logs.check_runtime_logs --since-minutes 30
python -m tools.logs.replay_outer_outbox --limit 50
```

- `check_runtime_logs`: Checks whether the main loop, event bus, and outer loop produced runtime evidence
- `replay_outer_outbox`: Replays pending events in the outer-loop compensation queue

## Configuration Files Guide

### `config/rag_config.yml`

Controls the RAG knowledge base, LLM, embeddings, and graph construction.

Commonly changed fields:

- `llm.provider` / `llm.model` / `llm.base_url` / `llm.api_key`: LLM configuration on the RAG side
- `embedding.provider` / `embedding.model` / `embedding.base_url` / `embedding.api_key`: Vector model configuration
- `property_graph.enabled`: Whether to build the property graph
- `property_graph.extraction_prompt`: Prompt used for graph triple extraction
- `metadata_extraction.enable_custom_scoring`: Whether to enable LLM-based custom importance scoring

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

After changing embeddings or import rules, resync the knowledge base:

```bash
python tools/doc_importer.py --sync
```

### `config/agent_model_config.yml`

Controls whether NLU, GM, evolution, and other agents use real models, and which model profile each one binds to.

Core structure:

- `defaults`: Default enablement, mode, timeout, and retry policy for agents
- `profiles.llm`: Reusable LLM connection settings
- `profiles.embedding`: Reusable embedding settings
- `bindings.agents.nlu`: NLU binding; currently `rule_first`
- `bindings.agents.gm`: GM binding; currently `llm_first`
- `bindings.agents.evolution`: Outer-loop evolution binding; currently disabled by default

When you want to verify only the pure deterministic main path, disable both NLU and GM models:

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

If no RAG index exists, whether startup auto-initialization runs is controlled by `rag.auto_initialize` in the layered rules snapshot. For deterministic acceptance, you can disable it explicitly:

```json
{
  "rag": {
    "read_only_enabled": false,
    "auto_initialize": false
  }
}
```

### `config/main_loop_rules.json`

This is the base-layer configuration file for main loop rules. The engine actually uses a layered merged rules snapshot rather than this file alone.

Rule load order (later overrides earlier):

1. Built-in default rules `DEFAULT_MAIN_LOOP_RULES`
2. `config/main_loop_rules.json`
3. Enabled MOD rule overrides
4. Scenario rule overrides (environment variable `LLMTRE_SCENARIO_RULES_PATH`)
5. Extra rule overrides (environment variable `LLMTRE_MAIN_LOOP_RULES_EXTRA`)

MOD rule override files support the following paths, checked in order:

- `mods/<mod_id>/main_loop_rules.override.json`
- `mods/<mod_id>/rules/main_loop_rules.override.json`
- `mods/<mod_id>/rules/main_loop_rules.json`

Commonly changed fields:

- `nlu.action_keywords`: Action keyword mappings
- `nlu.target_aliases` / `location_aliases` / `item_aliases`: Target, location, and item aliases
- `resolution`: Deterministic resolution rules, such as attack DC, damage dice, movement cost, and rest recovery
- `rag.read_only_enabled`: Whether the main loop reads RAG context
- `rag.auto_initialize`: Whether runtime RAG auto-initialization is allowed when vector indexes are missing
- `memory.summary_step`: Step size for memory summary compression
- `memory.summary_context_size`: Maximum context window for memory building
- `outer_loop`: Outer-loop event delivery, compensation replay, timeout, and world evolution step size
- `scene_defaults`: Default scenes, available actions, and suggested actions
- `narrative_templates`: Narrative templates used when the model is unavailable

Scenario override example (Windows PowerShell):

```powershell
$env:LLMTRE_SCENARIO_RULES_PATH = "D:\path\to\scenario_rules.json"
uv run python app.py
```

Extra override example (multiple files):

```powershell
$env:LLMTRE_MAIN_LOOP_RULES_EXTRA = "D:\a.json;D:\b.json"
uv run python app.py
```

### `.agent_context/`

Stores local agent runtime context specifications and long-term narrative summaries.

Core files:

- `AGENTS.md`: Agent context layering, read/write boundaries, and collaboration rules
- `OPS.md`: Tool-call, data-flow, and error-recording rules
- `MEMORY.md`: Cross-session long-term narrative summary pool

At runtime, the main loop read-loads `.agent_context/MEMORY.md`, filters out empty templates and placeholder comments, and merges it into `SceneSnapshot.recent_memory` together with recent Web session memory. This content affects only agent narrative context and does not participate in action legality, numeric resolution, or state writes.

### `config/rag_import_rules.json`

Stores knowledge base groups, tags, and file paths. In most cases it should be updated through `tools/doc_importer.py` rather than edited by hand.

Group fields:

- `group_name`: Group name
- `description`: Group description
- `tags`: Retrieval tags
- `file_paths`: Document paths included in the group
- `enable_graph`: Whether the group participates in graph construction

### `config/mod_registry.yml`

Stores scanned MODs together with their enablement status, priorities, conflict strategies, and hook manifests. It is usually generated or synchronized by `python tools/mod_manager.py scan`.

Commonly changed fields:

- `active_mods[].enabled`: Temporarily enable or disable a MOD
- `active_mods[].priority`: Adjust MOD override order
- `active_mods[].conflict_strategy`: Adjust conflict handling strategy

## API Overview

- Create session: `POST /api/sessions`
- Ordinary turn: `POST /api/sessions/{session_id}/turns`
- SSE streaming turn: `POST /api/sessions/{session_id}/turns/stream`

## Main Directories and Entry Points

- `agents/`: Agents such as NLU, GM, and evolution
- `config/`: Configuration for RAG, agent models, main loop rules, and the MOD registry
- `core/`: Central event bus and runtime logging infrastructure
- `game_workflows/`: Main loop, outer-loop bridge, RAG read-only bridge, and scene helper logic
- `state/`: Pydantic data contracts, seed data, SQLite initialization, and runtime schemas
- `tools/`: Deterministic tools, RAG import, MOD management, log acceptance, and compensation replay tools
- `web_api/`: Flask contract APIs, blueprints, and the `/play` entry point
- `mods/`: MOD extensions and scripts
- `static/`: Frontend scripts and styles for the legacy playground
- `templates/`: Flask page templates
- `frontend/`: React + Vite + TypeScript frontend project with `/app` as the entry point
- `tests/`: Pytest regression tests
- `docs/`: Input directory for local rulebooks and setting documents, ignored by Git by default
- `knowledge_base/`: Output directory for RAG vector and graph indexes
- `.agent_context/`: Local agent context specifications and long-term narrative summaries
- `.code_md/`: Macro architecture design documents
- `.coding_docs/`: Implementation notes
- `app.py`: Flask development server entry point
- `pyproject.toml`: Project metadata, packaging configuration, and lint/type-check configuration

## Known Limitations

- `/app` (React) and `/play` (legacy) currently coexist. Their API contracts are consistent, but their presentation and debugging surfaces are not fully identical.
- The top toolbar and scene cards in `/app` are now deduplicated: `New Session` and `Load` exist only in the top toolbar.
- The character status card in `/app` is driven by backend responses after session creation or loading; before that it shows placeholder `--` values.
- Status summaries and status badges in `/app` come from backend fields `active_character.status_summary/status_effects/state_flags/status_context`; the frontend only displays them and does not infer state on its own.
- The debug console in `/app` uses a fixed upper/lower layout: `Status / Trace / Logs / Memory` tabs on top, and the corresponding functional areas below.
- The A1 page directly exposes `Commit Mainline` and `Rollback Sandbox` buttons, but a normal new session is not a sandbox branch by default.
- The quest script evaluation path now uses AST whitelist expression evaluation, but it is still not a strong security sandbox; production use still requires trusted script sources.

## Contributing

### Reporting issues

- Use [GitHub Issues](https://github.com/iambupu/llmtre/issues) to report bugs or suggest features.
- Include a detailed description, reproduction steps, and environment information.

### Submitting code

1. Fork the repository.
2. Create a feature branch: `git checkout -b feature/your-feature`
3. Commit your changes: `git commit -m "Add your feature"`
4. Push the branch: `git push origin feature/your-feature`
5. Open a Pull Request.

### Code standards

- Style checks: `python -m ruff check .`
- Type checks: `python -m mypy .`
- Regression tests: `python -m pytest tests -q`

### License

This project is licensed under [GNU GPL v3](LICENSE).

## FAQ

### How do I start development?

1. Clone the repository: `git clone https://github.com/iambupu/llmtre.git`
2. Prefer `uv sync` to install dependencies; you can also use `pip install -r requirements.txt`
3. Initialize the database: `uv run python state/tools/db_initializer.py`
4. Start the service: `uv run python app.py`

### What hardware do I need?

- Python 3.14+
- If you want to run local models, a GPU-capable device is recommended

### How do I customize rules?

Modify `config/main_loop_rules.json`, or add and enable MOD rule overrides.

### What should I do if I hit a problem?

Check the runtime logs first:

```bash
python -m tools.logs.check_runtime_logs
```

## Version Information

- Current version: A1 (Alpha 1)
- Changelog: see [CHANGELOG.md](CHANGELOG.md)
