# Repository Guidelines

## Project Structure & Module Organization

- `backend/`: Django/DRF; `config/` handles settings/routing, `catalog/` books/shelves, `assessment/` questionnaires/profiles, and `health/` health checks. App tests use `tests.py`; schema changes use `migrations/`.
- `frontend/src/`: React/TypeScript, shared requests in `api.ts`, styles in `styles.css`; builds output to `frontend/dist/`.
- `产品讨论/` and root documents/spreadsheets/images: requirements and source materials. `imports/catalog-review.md` tracks unresolved books.
- `compose.yaml`: PostgreSQL. `.github/workflows/ci.yml`: checks. `deployment/`: placeholder.
- `specs/`: specs/plans; follow `specs/README.md` and templates.

## Build, Test, and Development Commands

Use Python 3.11 and Node.js 22; follow `README.md` for setup. Run `docker compose up -d db` for PostgreSQL, `.venv/bin/python backend/manage.py migrate` for migrations, and `.venv/bin/python backend/manage.py runserver 127.0.0.1:8000` for Django.

From `frontend/`, run `npm ci` and `npm run dev`; use `http://127.0.0.1:5173` consistently for cookies.

Backend checks: `.venv/bin/ruff check backend`, `.venv/bin/ruff format --check backend`, and `.venv/bin/python backend/manage.py check`.

Frontend checks: `npm run lint`, `npm run format:check`, `npm run typecheck`; `npm run build` produces production output.

## Coding Style & Naming Conventions

Use UTF-8, LF, and spaces: four for Python, two elsewhere. Python: Ruff, 100-character lines, `snake_case` functions/modules, `PascalCase` classes. TypeScript: strict typing, ESLint, Prettier (single quotes, no semicolons, trailing commas), `PascalCase` components, `camelCase` functions/variables.

## Testing Guidelines

From `backend/`, run `../.venv/bin/python manage.py test`; PostgreSQL must permit test-database creation. Use Django `TestCase`/`SimpleTestCase` and descriptive `test_*` methods; cover authentication, CSRF, user isolation, and assessment versioning. No coverage threshold or frontend test runner exists; manually verify UI changes.

## Commit & Pull Request Guidelines

No commits exist yet. Use imperative messages, e.g. `assessment: preserve completed answers`. PRs must link issues/specs/plans, describe behavior, validation and migrations, include relevant UI screenshots, and pass CI.

## Spec & Plan Maintenance

After every requirement discussion, before ending the turn, create or update `specs/NNN-slug/spec.md`, its paired `plan.md`, and the index in `specs/README.md`. Preserve the initial request verbatim; append dated changes and distinguish user decisions, agent proposals, and open questions.

Keep stable `REQ-001` and `AC-001` identifiers. Spec records goals, scope, behavior, and acceptance criteria; plan records tasks, files, and validation. Plans must link their spec, name its baseline version, and map tasks/validation to requirement IDs. Synchronize scope changes; record deferrals and reasons. Discussion-only work may leave tasks unstarted. Never infer requirements from implementation or mark unverified acceptance criteria complete.

## Configuration & Domain Constraints

Keep secrets in untracked `.env`. Preserve `/baby/study`, session authentication, CSRF, immutable completed assessments, and traceable profile conclusions. Never embed server credentials in frontend code or invent reading levels/book metadata.
