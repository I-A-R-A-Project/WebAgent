# Copilot Instructions for IA Browser

## Project Overview

IA Browser is a desktop web browser built with PyQt6 that manages isolated browser profiles for working with AI agents (Copilot, Claude, Gemini, Codex). Each profile has independent sessions, cookies, cache, and credentials. "Collections" are workspace groupings of bookmarks across profiles with optional Git versioning support.

**Key architecture layers:**
- **main.py**: QMainWindow with tabbed interface, profile/collection management, menu system
- **Profiles**: Isolated browser instances with separate storage, cache, home URLs, and zoom levels
- **Collections**: Bookmarks/URLs with optional download folders and Git integration
- **Agents**: Copilot CLI, Codex, and AnyAPI integration via `ai_manager.py`
- **Git Versioning**: Downloads can be version-controlled instead of numbered duplicates
- **web_common** (sibling directory): Shared UI components (tabs, navbars, sidebars, web profiles)

## Setup and Execution

**Installation:**
```bash
pip install -r requirements.txt
python main.py
```

Data persists in `~/.ia_browser/`:
- `profiles.json`: Profile definitions, download folders, home URLs, zoom
- `collections.json`: Collection definitions with bookmarks and Git settings
- `session.json`: Tab restoration data
- `profiles/<id>/storage/`: Chromium cookies and storage per profile
- `profiles/<id>/cache/`: Chromium cache per profile
- `copilot_profiles/<id>/`: Copilot CLI state per profile

## Architecture & Key Patterns

### Profile Isolation
- Each profile has a unique ID, separate QWebEngineProfile, and independent data directory
- The "Default" profile cannot be deleted and uses `~/.minibrowser/profile` as its storage path
- Profile switching preserves tab state and restores metadata (profile_id, collection_id)
- Git integration branches automatically: `profile/<id>-raw` for downloads, `profile/<id>` for reviewed changes

### Tab Management
- Tab metadata stored in `self.tab_data[id(webview)]` contains profile_id and collection_id
- UnifiedWebTab from `web_common.tabs` handles most tab rendering
- Sidebar panels (AppPanelOverlay, SidebarRail) managed non-modally
- Download and agent dialogs are kept in lists (`_download_dialogs`, `_agent_dialogs`) to prevent garbage collection

### Collections & File Organization
Collections link multiple profiles to a single workspace with optional Git versioning:
- Each collection has items (URLs with optional profile overrides)
- Download folder optional; Git versioning only enabled if folder + flag are set
- Bookmarks UI rendered as expandable tree in the sidebar

### AI Agent Integration
- **Codex**: Reorganizes raw download commits into descriptive commits on a clean branch
- **Copilot**: Primary AI agent by default — can read and modify code and documentation, create commits, and run verification commands when configured
- Both run in terminal with profile-specific `COPILOT_HOME` environment
- Device flow authentication auto-detects completion and closes tabs
- Quota rotation can switch to next available profile and retry (if configured)

## Common Tasks

### Adding a New Feature
1. **UI changes**: Edit `main.py` for windows/dialogs, or extend web_common components
2. **Profile/Collection logic**: Add methods to ProfileManager or CollectionManager
3. **File operations**: Add to `file_ops.py` (GitVersioning or FileOps classes)
4. **Web rendering**: Extend web_common modules (tabs.py, navbar.py, sidebar.py, web_profiles.py)

### Modifying Profile or Collection Persistence
- Always call `.save_profiles()` or `.save_collections()` after mutations
- JSON is stored directly, no ORM; load on init, save on change
- Default profile is always present; handle via `is_default` flag

### Handling Downloads
- `downloads.py` contains DownloadDialog and download request handling
- Downloads can be versioned with git (GitVersioning) or numbered

### Local automation scripts
- `scripts/refactor_py_rope.py`: helper (rope-based) for Python refactorings (rename/move)
- `scripts/refactor_js.py`: wrapper to run jscodeshift transforms (JS/TS) via npx
- `scripts/refactor_prettier.py`: wrapper to run prettier (JS/TS/HTML/CSS)
- `scripts/refactor_rust.py`: wrapper to run cargo fmt and cargo fix (Rust)
- `scripts/refactor_comby.py`: wrapper to run comby structural search/replace for multiple languages
- `scripts/refactor_ast_grep.py`: AST-based structural search/rewrite with ast-grep
- `scripts/toggle_autorun.py`: toggle per-folder autorun settings (edits ~/.ia_browser/codex_config.json)
- New `autorun` config per-folder: `{ "autorun": { "enabled": false, "command": "" } }` — if enabled, the configured command runs automatically after an agent completes. The AIAgents dialog runs the command, streams its output to the "Salida" panel, and shows a warning if the autorun exits with a non-zero code.

### Adding Agent Functionality
- Edit `ai_manager.py` to modify agent prompts or commands
- Prompts are templates with `{target_branch}` and `{source_branch}` placeholders
- Both agents check GitVersioning availability before running
- Agents require a repo initialized in the target folder

## Code Style & Conventions

- **Spanish docstrings and comments**: Project documentation is in Spanish; maintain this for consistency
- **Path handling**: Use `pathlib.Path` throughout (sys.path.insert pattern in main.py for local imports)
- **JSON for persistence**: Direct JSON dumps/loads in managers, no database
- **Non-modal dialogs**: Download and agent dialogs stored in lists to stay alive
- **Profile ID generation**: Use `uuid.uuid4().hex[:12]` for short IDs
- **Git safety**: Always check `GitVersioning.is_available()` before subprocess calls
- **Subprocess timeouts**: Use timeout=10–20 seconds for git operations, capture_output=True

## Testing & Validation

- No existing test suite; manual testing via running `python main.py`
- Verify:
  - Profile isolation (sessions, cookies, storage separate)
  - Collection bookmark persistence
  - Git integration (branching, commits, safety checks)
  - Download flow (single/split files, versioning)
  - Agent execution (Codex, Copilot authentication and output)
  - Tab restoration after restart

## Dependencies

**Core:**
- PyQt6>=6.7.1
- PyQt6-WebEngine>=6.7.1
- rope>=1.8.0 (optional — used by scripts/refactor_py_rope.py)

**External tools** (optional, checked at runtime):
- `git`: For versioning downloads and agent operations
- `codex`, `copilot`: CLI tools for agent integration
- AnyAPI: unified REST API provider, configured with an `ANYAPI_API_KEY`
  through the AI Manager UI
- `ast-grep` or `sg`: optional AST-based search and rewriting
- `uv`: optional, only needed to run the ast-grep MCP server

### ast-grep y MCP

Usa `scripts/refactor_ast_grep.py` para búsquedas y refactors estructurales
compactos, sin enviar cada archivo coincidente al agente:

```bash
python scripts/refactor_ast_grep.py . --pattern "console.log($$$ARGS)" --lang ts
python scripts/refactor_ast_grep.py . --pattern "var $A = $B" --rewrite "let $A = $B" --lang js --dry-run
```

Instalación del CLI (opcional):

```bash
npm install --global @ast-grep/cli
```

El servidor `ast-grep-mcp` es opcional y se configura en el cliente MCP. Ofrece
visualización del AST, prueba de reglas YAML y búsquedas estructurales para
agentes compatibles. No se inicia automáticamente desde IA Browser.

Referencias:

- https://github.com/ast-grep/ast-grep
- https://github.com/ast-grep/ast-grep-mcp
- https://ast-grep.github.io/advanced/prompting

AnyAPI documentation:

- https://docs.anyapi.ai/

## Important Warnings

⚠️ **No linting/formatting configured**: Code style is informal; follow existing conventions

⚠️ **Local imports**: main.py inserts parent directory into sys.path; web_common is a sibling, not submodule

⚠️ **Persistent data**: Never commit `~/.ia_browser/` if it contains sessions, credentials, or cookies

⚠️ **Subprocess safety**: Always use timeouts and capture_output=True; check availability before calling git/codex/copilot
