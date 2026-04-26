# AGENTS.md - AtlasClaw Coding Guidelines

Coding guidelines for AI assistants working on the AtlasClaw enterprise agent framework.

Concrete provider packages do not live in this repository. AtlasClaw core loads
them through `providers_root`, typically from a sibling providers repository.

## Build / Test / Lint Commands

### Backend (Python)

```bash
# Run the service
uvicorn app.atlasclaw.main:app --reload --host 0.0.0.0 --port 8000

# Run all tests
pytest tests/atlasclaw -q

# Run a single test file
pytest tests/atlasclaw/test_agent.py -v

# Run a single test class
pytest tests/atlasclaw/test_agent.py::TestStreamEvent -v

# Run a single test method
pytest tests/atlasclaw/test_agent.py::TestStreamEvent::test_create_lifecycle_start -v

# Run tests with specific markers
pytest -m "not slow"              # Skip slow tests
pytest -m llm                     # Run LLM integration tests (needs API key)
pytest -m e2e                     # Run end-to-end tests

# Run with coverage
pytest --cov=app.atlasclaw --cov-report=term-missing
```

### Frontend (JavaScript)

```bash
cd app/frontend

# Install dependencies
npm install

# Build for production
npm run build

# Build for development (with sourcemap)
npm run build:dev

# Run tests
npm test
```

## Required Reading

Before making any feature changes, bug fixes, or architectural decisions, consult these canonical documents:

| Document | Content | Path |
|----------|---------|------|
| **Architecture** | Design philosophy, system architecture, bootstrap sequence, request lifecycle, security model, extension points | [docs/architecture.md](docs/architecture.md) |
| **Module Details** | Per-module API surface, class/method/enum reference, configuration options, dependencies | [docs/module-details.md](docs/module-details.md) |
| **Development Spec** | Code style, architecture patterns, error handling, security, testing, extension development, deployment & operations, review checklist | [docs/development-spec.md](docs/development-spec.md) |

**All development work MUST be consistent with these documents.** If a proposed change conflicts with documented patterns, update the documentation as part of the same change.

### Additional References

| Document | Content |
|----------|---------|
| [Provider Guide](docs/PROVIDER-GUIDE.MD) | Core-side provider contract and external loading model |
| [Skill Guide](docs/SKILL_GUIDE.md) | Creating executable, markdown, and hybrid skills |
| [Channel Guide](docs/Channel%20Guide.md) | Channel handler implementation and integration |
| [Guide](docs/GUIDE.md) | End-user/developer usage guide |

## Code Style Guidelines

### Python

**Imports:**
- Use `from __future__ import annotations` at the top for forward references
- Standard library imports first, third-party second, local third
- Group imports with a blank line between groups
- Use absolute imports: `from app.atlasclaw.core.deps import SkillDeps`

**Formatting:**
- UTF-8 encoding: Include `# -*- coding: utf-8 -*-` header in Python files
- Copyright headers: Do not blindly copy an old copyright year from another
  file. For newly created files, use the year the file is first added. When
  moving or splitting existing code, keep the original copyright year only if it
  reflects the copied code's provenance.
- 4 spaces for indentation
- Line length: ~100 characters (be reasonable)
- Use double quotes for strings unless single quotes avoid escaping

**Types:**
- Use type hints on all function parameters and return values
- Use `Optional[T]` instead of `T | None` (Python 3.10+ union syntax okay but Optional preferred)
- Use dataclasses for data containers: `@dataclass`
- Prefer enums for string constants: `class EventType(str, Enum)`

**Naming Conventions:**
- `snake_case` for functions, variables, modules
- `PascalCase` for classes, exceptions
- `SCREAMING_SNAKE_CASE` for constants
- Private methods/attributes prefixed with `_`

**Error Handling:**
- Use specific exception types, not bare `except:`
- Include error context in exception messages
- Return result objects for expected failures: `SendResult(success=False, error="timeout")`
- Use `asyncio.Event` for cancellation signals

**Documentation:**
- Docstrings use triple quotes on separate lines
- Include docstrings for all public classes and methods
- Use Google-style or reStructuredText format
- Comments in both English and Chinese acceptable

**Async Patterns:**
- All I/O-bound operations must be async
- Use `asyncio.Event` for coordination
- Properly await coroutines in tests with `@pytest.mark.asyncio`

### Testing

**Test Structure:**
- Test files: `test_<module>.py`
- Test classes: `Test<PascalCase>` (e.g., `TestStreamEvent`)
- Test methods: `test_<description>` (e.g., `test_create_lifecycle_start`)
- Use fixtures in `conftest.py` for shared resources

**Test Markers:**
- `@pytest.mark.slow` - Tests taking > 1 second
- `@pytest.mark.integration` - Integration tests
- `@pytest.mark.e2e` - End-to-end tests (requires services)
- `@pytest.mark.llm` - Tests requiring LLM API calls
- Use `@pytest.mark.asyncio` for async tests

**Test Fixtures:**
- Use `scope="session"` for expensive resources
- Use `scope="function"` (default) for isolated tests
- Clean up in fixture teardown or use `yield`

### JavaScript (Frontend)

**Style:**
- ES modules: `"type": "module"` in package.json
- No semicolons preferred (but be consistent with existing code)
- 2 spaces for indentation
- Single quotes for strings

**Testing:**
- Uses Jest with jsdom environment
- Tests in `tests/frontend/**/*.test.js`

## Project Structure

```
AtlasClaw-Core/
├── app/atlasclaw/          # Main application code
│   ├── agent/            # Agent engine, streaming, routing
│   ├── api/              # REST, WebSocket, SSE endpoints
│   ├── auth/             # Authentication, authorization
│   ├── channels/         # Channel adapters (WebSocket, SSE, REST)
│   ├── core/             # Config, dependencies, provider registry, provider loading
│   ├── memory/           # Memory manager and retrieval
│   ├── session/          # Session management
│   ├── skills/           # Skill loading and registry
│   └── tools/            # Built-in tools
├── tests/                # Test suite
│   ├── atlasclaw/          # Python tests
│   └── frontend/         # JavaScript tests
├── app/frontend/         # Frontend application
├── docs/                 # Documentation
└── openspec/             # Specification-driven development
    └── AGENTS.md         # OpenSpec workflow guide
```

Concrete providers are external packages loaded through `providers_root`. This
repository owns the runtime, contracts, and loading behavior, not provider
implementations.

## Configuration

- **Backend config:** `atlasclaw.json` (in project root)
- **Test config:** `tests/atlasclaw.test.json`
- **Environment variables:** Use `${VAR_NAME}` format in config

## Architecture Patterns

- **Thin core, rich providers:** Keep platform logic in providers, reusable logic in core
- **Pydantic models:** Use for all data validation and serialization
- **Dependency injection:** `SkillDeps` passed through `RunContext`
- **Strict permissions:** Never bypass RBAC, inherit user access rights
- **Async-first:** All I/O operations are async

## OpenSpec Workflow

When implementing features, see `openspec/AGENTS.md` for spec-driven development:
- Create proposals for new features, breaking changes, or architectural changes
- Use `openspec-cn` CLI for spec management
- Follow three-phase workflow: Create → Implement → Archive

## Commit and PR Messages

Use Conventional Commits: `feat`, `fix`, `docs`, `refactor`, `test`, `chore`.

Format:
- `<type>(<scope>): <summary>`

Example:
- `docs(config): align webhook env var examples`

PR titles must use the same Conventional Commit type format. Do not include
AI assistant or AI tool names in PR titles.

---

*Keep this file updated as the project evolves.*
