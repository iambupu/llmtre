# TRE A1 Version Play Guide

This guide is intended for players who want to directly try the current Web demo. Follow this document in order to complete one new session creation, turn input, scene review, memory review, and continue playing after restart. The current recommendation is `/app`, while `/play` is retained as a legacy comparison entry.

## 1. Preparation before playing

### 1.1 Install dependencies

The project requires Python `3.14+`.

If you want to avoid handling virtual environments too much, it is recommended to install `uv` first, then run in the project root:

```bash
uv sync
```

`uv` will automatically create/use a local environment and install project dependencies. For official installation instructions, see the [uv installation page](https://docs.astral.sh/uv/getting-started/installation/).

If you prefer the traditional approach, you can also use:

```bash
pip install -r requirements.txt
```

### 1.2 Prepare local models

The currently verified default configuration uses Ollama:

- LLM: `qwen3:8b`
- Embedding: `bge-m3`

If you use other models, you should also modify:

- RAG model: `config/rag_config.yml`
- Agent narrative model: `config/agent_model_config.yml`

### 1.3 Initialize database, import documents, and load MODs

For first-time runs, it is recommended to execute these three commands in order. They handle three different tasks:

- `uv run python state/tools/db_initializer.py`: initialize or rebuild the SQLite game state database and write seed data, so session, character, inventory, quests, and other basic state are available.
- `uv run python tools/doc_importer.py docs/ --group core --sync`: import rulebooks and setting documents from `docs/` into RAG and immediately rebuild the index so the system can read the latest documents.
- `uv run python tools/mod_manager.py scan`: scan MODs under `mods/` and update `config/mod_registry.yml`, so new or modified MODs are registered.

```bash
uv run python state/tools/db_initializer.py
uv run python tools/doc_importer.py docs/ --group core --sync
uv run python tools/mod_manager.py scan
```

Notes:

- `docs/` is the knowledge base input directory. Put rulebooks and setting documents into `docs/` before importing them.
- `db_initializer.py` only prepares the database and seed state; it does not import documents or enable MODs for you.
- `doc_importer.py` only imports documents and rebuilds indexes; it does not create the database or scan MODs.
- `mod_manager.py scan` only scans and registers MODs; it does not automatically enable them all.
- `uv run python app.py` will also try to auto-complete SQLite and vector indexes at startup; if auto indexing fails, return to this section and run the initialization commands manually.
- Main loop rules are not fixed to `config/main_loop_rules.json`: at runtime the engine merges base rules + enabled MOD overrides + scenario overrides + extra overrides. Ordinary players do not need to manually edit rules files.

#### 1.3.1 Import `docs/` into RAG

`tools/doc_importer.py` does more than just "put `docs/` into the index." It has three common usages:

1. Run without arguments: sync directly according to `config/rag_import_rules.json`.
2. Specify a path: register a single file, normal directory, or MinerU export directory into a group.
3. Add `--sync`: rebuild the knowledge index immediately after import.

When you add or update rulebooks or setting documents, you usually run:

```bash
uv run python tools/doc_importer.py docs/ --group core --sync
```

This command does two things:

1. Registers documents under `docs/` into `config/rag_import_rules.json`
2. Rebuilds `knowledge_base/indices/` so RAG can read the latest documents

Common usages:

- When importing the entire `docs/` directory, keep using `--group core`.
- If you only want to import a single file, replace the path with a specific file such as `docs/rules.md`.
- If you organize files as a MinerU export directory, add `--mineru` to force MinerU-style processing.

Full parameter description:

- `path`: the file or directory to import.
- `--group <name>`: the target group name. If you explicitly provide `path`, you must also provide `--group`.
- `--tags tag1,tag2`: attach tags to the group.
- `--desc "description"`: add a group description.
- `--sync`: rebuild indexes immediately after import.
- `--mineru`: treat the directory as a MinerU export directory even if the name does not contain standard markers.

Supported path types:

- A single document file, e.g. `docs/rules.md`
- A normal directory, e.g. `docs/`
- A MinerU export directory containing `.md` and `.json`

Examples:

```bash
uv run python tools/doc_importer.py docs/rules.md --group core --tags rules,story --desc "Core rulebook" --sync
uv run python tools/doc_importer.py docs/setting/ --group setting --sync
uv run python tools/doc_importer.py docs/mineru_export/ --group lore --mineru --sync
```

If you omit both `path` and `--group`, the script will simply read the existing import rules and sync the index, without adding a new group.

#### 1.3.2 Scan and load MODs

`tools/mod_manager.py scan` scans MODs under `mods/` and writes back `config/mod_registry.yml`. It reads each MOD directory containing `mod_info.json` and registers new MODs into the registry.

After you add or modify a MOD under `mods/`, run:

```bash
uv run python tools/mod_manager.py scan
```

This command scans MODs under `mods/` that contain `mod_info.json` and updates `config/mod_registry.yml`.

After scanning, check the corresponding MOD's `enabled` setting in `config/mod_registry.yml`:

- `enabled: true` means the MOD is loaded
- `enabled: false` means it is only registered and not enabled

You should also inspect:

- `priority`: priority value, higher means higher precedence
- `conflict_strategy`: conflict resolution strategy
- `hooks_manifest`: which hooks and write fields this MOD declares

## 2. Start the game

```bash
uv run python app.py
```

After startup, open in browser (recommended):

```text
http://localhost:5000/app
```

For legacy comparison, also open:

```text
http://localhost:5000/play
```

If `/app` shows a prompt that `frontend/dist/index.html` is missing, it means the frontend has not been built yet. Run in the `frontend/` directory:

```bash
npm install
npm run build
```

## 3. Page section explanations

`/app` mainly consists of four parts:

- Top bar: fill in character ID, session ID, execute `New Session` / `Load` / `Reset`, and toggle `Console/Debug`. `New Session` / `Load` only appear here.
- Scene area: shows the current location title, exit badges, visible object cards, and current status tips (no longer raw JSON output).
- Turn log: shows system/player/GM messages, quick action buttons, output mode (`stream` / `sync`), input box, and `Send` / `Stop`.
- Right state area: character status, inventory/equipment, quests, memory summary, and sandbox control buttons. Character info is driven by backend session data and shows `--` before a session is created.

Daily play mainly uses:

- `New Session`: create a new adventure.
- `Send`: submit the current input.
- `/app` memory buttons: `Read` / `Refresh` / `Clear`.
- `/app` sandbox buttons: `Commit` / `Discard`.

If you are looking at the legacy `/play` page, the corresponding buttons are:

- `Memory` / `Refresh Memory`
- `Merge to Mainline` / `Rollback Sandbox`

## 4. Recommended first run flow

1. Confirm the top "Character" is `player_01`.
2. Click `New Session`.
3. Wait for the GM opening narrative to appear in the turn log.
4. Read the current scene, focusing on location description, exits, visible objects, and suggested actions.
5. Click a suggested action on the page, or enter `Observe surroundings` in the input box and click `Send`.
6. Continue entering 3 to 5 clear actions, for example:

```text
Observe surroundings
Check inventory
Head to the forest
Attack the goblin
Talk to the goblin
Use a potion
```

7. Click the memory panel's `Read` button to view recent memory text (generated by valid turns and segment summary).
8. Click the memory panel's `Refresh` button to trigger backend re-computation of the summary and confirm memory text updates.
9. After closing and restarting `uv run python app.py`, enter the original `session_id` in the top session input box and click `Load` to continue playing.

## 5. How to enter actions

The current NLU is mostly rule- and keyword-based, so clearer input is more stable.

| Desired action | Recommended input |
| :--- | :--- |
| View environment | `Observe surroundings`, `Look around` |
| Check status | `Check inventory`, `View current scene` |
| Move | `Go to the forest`, `Move to the camp`, `Continue forward` |
| Talk | `Talk to the goblin`, `Speak with the traveler` |
| Interact | `Investigate the camp`, `Try to interact` |
| Use item | `Use potion`, `Drink potion` |
| Rest/wait | `Rest`, `Wait a moment` |
| Attack | `Attack the goblin` |

Avoid overly short or goal-less input such as `go`, `see`, or `mess with`. If the system deems the action too unclear, it will return a clarification question; answer with more specific goals or directions.

## 6. Scene, memory, and suggested actions

The "current scene" comes from the backend response `SceneSnapshot` and refreshes with session creation, session load, and turn submission.

You should pay attention to:

- Current location: current location name and description.
- Exits: where you can move.
- Visible objects: currently visible NPCs, items, or quests.
- Suggested actions: quick next actions displayed by the page.
- Recent memory: recent memory text recorded by the system (normalized concatenation + configurable step summary).

Suggested actions are only shortcuts, not the only options; you can also enter other clear actions directly.

## 7. Output modes (`/app`)

The default recommendation is `stream`. After submitting a turn, the frontend receives SSE events and GM text appears in segments; the debug panel can show `lastSseEvent` and status logs.

If streaming output fails, switch the output mode to `sync` and retry the same input, then compare `lastRequest`, `trace`, and status logs in the debug panel.

Debug panel layout (same as `/app`):

- Top: `State / Trace / Logs / Memory` tabs.
- Bottom: the corresponding content area.
- `Trace` tab includes stat cards, event retrieval/filtering, and timeline list; if no events exist it shows an empty state prompt.

## 8. Sandbox commit and rollback

The page directly exposes two sandbox buttons for demonstrating and operating sandbox branches. The current sandbox is still a display/experiment feature and should not be treated as a formal branching system.

- `Commit to Mainline`: merges current Shadow branch changes into Active mainline state.
- `Rollback Sandbox`: discards current Shadow branch changes and restores Active mainline state.

The sandbox feature allows you to inspect or manage sandbox changes without affecting the mainline. It does not automatically switch sandbox mode for certain inputs; only explicit sandbox button operations trigger the related API.

If you are unsure whether the current session is in a sandbox branch, continue entering normal actions. The formal sandbox contract endpoints are `POST /api/sessions/{session_id}/sandbox/commit` and `POST /api/sessions/{session_id}/sandbox/discard`; do not treat similarly named text actions in ordinary `/turns` inputs as general save/undo commands.

## 9. Reset and resume session

- `Reset`: clears the current session's turns and memories, usually keeps character info, and resets `current_turn_id` to `0`.
- If the current session is in sandbox mode, it must meet sandbox owner/lease conditions before reset, otherwise it returns a controlled error.
- `Load`: restore a session using an existing `session_id`.

Session data is stored in SQLite. As long as you do not delete or rebuild `state/core_data/tre_state.db`, you can load old sessions after restarting Flask.

## 10. How to read character status

- The right-side "Character Status" card's HP/MP, status summary, and status tags all come from backend `active_character`.
- The status summary is derived from SQLite `state_flags_json`, HP/MP thresholds, and layered rules in `character_status`; the frontend only displays it and does not write it.
- When there are no status effects, it shows "Stable"; before creating or loading a session it shows `--`.
- The debug panel's `State` page can show raw `state_flags/status_effects/status_context` for backend verification.

## 11. Common issues

### Page does not open

Confirm the service is running:

```bash
uv run python app.py
```

Then visit:

```text
http://localhost:5000/app
```

If `/app` shows a frontend build missing prompt, run in `frontend/`:

```bash
npm install
npm run build
```

### Creating a session or turn is slow

Local model startup may be slow on first use. Confirm Ollama is running and that `qwen3:8b` and `bge-m3` are available.

If you only want to verify the purely deterministic chain, disable both NLU and GM model calls. Disabling only GM still allows NLU to use an LLM fallback.

In `config/agent_model_config.yml`:

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

When the RAG index is unavailable, startup may still try to initialize it and embedding may still depend on Ollama. For purely deterministic acceptance, prepare the index first or temporarily disable it in your rule override layer (base file / MOD override / scenario override):

```json
{
  "rag": {
    "read_only_enabled": false,
    "auto_initialize": false
  }
}
```

### RAG or knowledge base error

Re-import and sync the knowledge base:

```bash
uv run python tools/doc_importer.py docs/ --group core --sync
```

Also confirm that the embedding model configured in `config/rag_config.yml` is available.

### "System needs clarification" explanation

This is not a failure. It means your input lacked a target, direction, or action type. Answer the clarification question with a more specific action, for example change `go` to `go to the forest`.

### Want to verify runtime logs are healthy

After playing at least one turn, run:

```bash
python -m tools.logs.check_runtime_logs --since-minutes 15
```

Output containing `RUNTIME_LOG_CHECK_OK` indicates recent evidence of complete main loop, event bus, and outer loop runtime logs.
