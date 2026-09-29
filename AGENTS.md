# Repository Guidelines

## Current Project State

Updated 2026-09-29 against the current working tree. Read `README.md` for setup and `specs/README.md` for scope and validation status; distinguish committed code from local changes with `git status` and `git diff`.

- Implemented features include edition-level books and evidence, shared series members, family shelves, versioned assessments/profiles, administrator catalog/series management, and photo intake drafts with optional enrichment.
- `backend/ai/` integrates MiMo text, image and audio understanding, plus Batch jobs. Photo intake runs in a separate worker and requires human confirmation before catalog writes. New intakes default to realtime; existing Batch jobs keep their mode.
- `deployment/family/` provides a local Cloudflare Quick Tunnel trial with Gunicorn, Caddy, the intake worker, backups and process/log management. Read its README before operating services; do not assume a historical URL or PID is current.
- Specs 004, 005 and 007 remain `in-progress`: supplier Batch completion and physical-phone/mobile-network acceptance are recorded as pending in their validation documents. Recommendations, reading plans and the parent Agent interface are not implemented.

## Project Structure & Module Organization

- `backend/`: Django 5.2/DRF; `config/` handles settings/routing/observability, `catalog/` books, series, shelves, enrichment and intake, `assessment/` questionnaires/profiles, `ai/` model clients/Batch, and `health/` health checks. Tests use `tests.py` and `test_*.py`; schema changes use `migrations/`.
- `frontend/src/`: React/TypeScript; shared requests in `api.ts`, intake handling in `intake.ts`, styles in `styles.css`, and catalog/series/intake/assessment components. `frontend/scripts/test-requests.mjs` tests request failures; builds output to `frontend/dist/`.
- `产品讨论/` and root documents/spreadsheets/images: requirements and source materials. `imports/catalog-review.md` tracks unresolved books.
- `compose.yaml`: PostgreSQL 17. `.github/workflows/ci.yml`: backend, deployment and frontend checks. `deployment/family/`: trial scripts, supervisor, Caddy configuration and tests.
- `specs/`: specs/plans; follow `specs/README.md` and templates.

## Build, Test, and Development Commands

Use Python 3.11 and Node.js 22; follow `README.md` for dependency installation and loading the untracked root `.env` into the backend shell. Run `docker compose up -d db` for PostgreSQL, `.venv/bin/python backend/manage.py migrate` for migrations, and `.venv/bin/python backend/manage.py runserver 127.0.0.1:8000` for Django.

From `frontend/`, run `npm ci` and `npm run dev`; use `http://127.0.0.1:5173` consistently for cookies. If that port is occupied, follow the README's 5174/CSRF configuration rather than stopping another project's service.

- Intake worker: `.venv/bin/python backend/manage.py process_book_intakes --interval 5 --batch-interval 60`; `--once` performs one cycle. It can process real pending jobs and incur supplier charges; use mocked tests for routine validation.
- Backend checks from the root: `.venv/bin/ruff check backend`, `.venv/bin/ruff format --check backend`, and `.venv/bin/python backend/manage.py check`.
- Frontend checks from `frontend/`: `npm run lint`, `npm run format:check`, `npm run test:requests`, `npm run typecheck`, and `npm run build`.
- Deployment checks from the root: `.venv/bin/python -m unittest discover -s deployment/family -p 'test_*.py'`.
- Trial operations: `deployment/family/start.sh`, `deployment/family/stop.sh`, `.venv/bin/python deployment/family/family.py status`, and `.venv/bin/python deployment/family/family.py logs --component worker --lines 100`. Startup backs up/migrates the database and starts the worker; follow `deployment/family/README.md`.

## Coding Style & Naming Conventions

Use UTF-8, LF, and spaces: four for Python, two elsewhere. Python: Ruff, 100-character lines, `snake_case` functions/modules, `PascalCase` classes. TypeScript: strict typing, ESLint, Prettier (single quotes, no semicolons, trailing commas), `PascalCase` components, `camelCase` functions/variables.

## Testing Guidelines

From `backend/`, run `../.venv/bin/python manage.py test` so all app tests are discovered; PostgreSQL must permit test-database creation. Use Django `TestCase`/`SimpleTestCase` and descriptive `test_*` methods; cover authentication, CSRF, user isolation, immutable completed assessments, catalog/series consistency, intake idempotency and worker behavior as relevant.

The frontend has request-layer tests, but no component/UI test runner or coverage threshold. Manually verify affected UI flows, including desktop/mobile layouts when relevant. Record mocked tests, browser emulation, real model calls and physical-phone acceptance separately; one does not prove another.

## Commit & Pull Request Guidelines

- After the relevant validation passes, agents may automatically create a local `git commit` for completed task changes. Execute `git push` only when the user explicitly requests it; a local commit does not authorize a push. Existing explicit authorization remains valid within its scope; do not ask again for an already authorized step. Creating a PR authorizes pushing the required task branch. Use `codex/` when creating a branch unless the user specifies another prefix.
- Inspect the branch, `git status --short`, and unstaged/staged diffs before staging. Preserve pre-existing changes and the user's staging choices. Stage explicit task paths or hunks; do not use blanket `git add .`/`git add -A` in a dirty workspace or include unrelated changes merely because they share a file. Do not commit pre-existing staged changes outside the authorized scope.
- Keep commits focused on one coherent task; include its required migrations, meaningful tests, spec/plan updates and relevant documentation. Use imperative messages such as `catalog: preserve intake submission keys` or `docs: update repository guidelines`; reference the relevant spec/issue in the body when applicable.
- Before a code commit, run the checks above for affected areas: backend checks and Django tests for backend changes; all frontend checks for frontend changes; deployment tests for supervisor/deployment changes. For model changes also run `.venv/bin/python backend/manage.py makemigrations --check --dry-run` and review migration/data compatibility. Validate all affected areas for cross-cutting changes. Documentation-only changes require diff, links and consistency checks, not a full application test run.
- Review `git diff --cached --check`, `git diff --cached --stat`, and the complete staged diff before committing. Relevant checks must pass; failed or unrun checks must be reported with their reason. Only create a WIP commit with incomplete validation when the user explicitly requests it, and label the outstanding checks accurately.
- Never stage secrets, credentials, `API_KEY`, real `.env` files, private photos, database dumps, runtime logs/state, caches, dependencies or generated build/static output. Review new untracked files explicitly; `.gitignore` is not sufficient. Example configuration must use placeholders. Only include reviewed, non-sensitive screenshots or fixtures needed for the task.
- Do not amend existing commits, rewrite/rebase shared history, force-push, delete remote branches, merge PRs, or discard changes with destructive Git commands without explicit authorization for that action. Normal commit/push authorization does not include these operations; never bypass repository hooks or CI to hide failures.
- After a commit, report its hash, included scope and validation, and inspect `git status --short` for remaining changes. After a push, report the branch/remote. PRs must link issues/specs/plans, describe behavior, validation and migrations, include relevant UI screenshots, and pass CI.

## Spec & Plan Maintenance

After every requirement discussion, before ending the turn, create or update `specs/NNN-slug/spec.md`, its paired `plan.md`, and the index in `specs/README.md`. Preserve the initial request verbatim; append dated changes and distinguish user decisions, agent proposals, and open questions.

Keep stable `REQ-001` and `AC-001` identifiers. Spec records goals, scope, behavior, and acceptance criteria; plan records tasks, files, and validation. Plans must link their spec, name its baseline version, and map tasks/validation to requirement IDs. Synchronize scope changes; record deferrals and reasons. Discussion-only work may leave tasks unstarted. Never infer requirements from implementation or mark unverified acceptance criteria complete.

## Configuration & Domain Constraints

Keep secrets in untracked root `.env` and `deployment/family/.env`; never print their contents or embed server credentials in frontend code. Keep private intake media behind authenticated owner checks, and exclude `backend/private-media/` and `deployment/family/.runtime/` from commits. Runtime backups contain real family data.

Preserve `/baby/study`, session authentication, CSRF, immutable completed assessments, traceable profile conclusions and existing shelf states. Shared catalog writes require administrator permissions; photo/model/enrichment output stays a draft until human confirmation. Do not invent reading levels, book metadata, series completeness or supplier usage; missing usage is not zero cost. Keep editions and sources traceable, and do not silently switch or resubmit existing Batch tasks.
