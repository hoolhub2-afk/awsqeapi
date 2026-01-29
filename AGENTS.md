# Repository Guidelines

## Project Structure & Module Organization
- `app.py` is the FastAPI entry point, registering middleware, exception handlers, and routers.
- `src/` contains backend code, organized by `routers/`, `services/`, `core/`, `integrations/`, `security/`, and `api/`.
- `frontend/` holds static HTML/CSS/JS for the admin UI. There is no build step.
- `templates/` stores server-rendered templates.
- `tests/` contains pytest suites. `scripts/` includes maintenance utilities.
- `data/`, `logs/`, and `backups/` are runtime artifacts and should not be committed.

## Build, Test, and Development Commands
```bash
uv venv
uv pip install -r requirements.txt
uvicorn app:app --host 0.0.0.0 --port 8000 --reload
pytest
docker-compose up -d
docker-compose logs -f
```
- `uv venv` and `uv pip install` create the virtual env and install dependencies.
- `uvicorn ... --reload` runs the API with hot reload for local development.
- `pytest` runs the test suite.
- `docker-compose up -d` starts the service in Docker; `logs -f` tails output.

## Coding Style & Naming Conventions
- Python uses 4-space indentation, PEP 8 style, and type hints for public functions.
- Naming: `snake_case` for functions and variables, `PascalCase` for classes, `UPPER_SNAKE_CASE` for constants.
- JavaScript uses `camelCase`, `const/let`, and avoids frameworks.
- Configuration lives in `.env`; update `.env.example` when adding new keys.

## Testing Guidelines
- Frameworks: `pytest` and `pytest-asyncio`.
- Test files follow `tests/test_*.py` naming.
- Keep tests deterministic and focused on one behavior per test.

## Commit & Pull Request Guidelines
- Git history is not available in this workspace; use concise, imperative commit messages and explain why.
- PRs should include: a clear description, test results, config changes, and UI screenshots when frontend changes.

## Security & Configuration Tips
- Never commit `.env` or secrets.
- Production requires `MASTER_KEY`, `ADMIN_API_KEY`, `ADMIN_PASSWORD`, and `OPENAI_KEYS`.
- Optional settings include `POOL_SERVICE_URL`, `HTTP_PROXY`, and `DATABASE_URL`.
- Logs write to `logs/` by default or `LOG_FILE_PATH` if set.
