# 09 — Working Agreement

## Workflow per feature
1. Write the requirement (2–5 lines) in the relevant doc or issue.
2. Design: models and services first. Note any decision in `08_DECISIONS_LOG.md`.
3. Implement model → migration → service → view → template → admin.
4. Write tests (models, services, permissions, views).
5. Run `python manage.py check`, `makemigrations --check`, tests, and lint.
6. Update docs (roadmap checkbox, context, decisions).
7. Commit.

## Definition of done
- [ ] Works on a phone-width screen
- [ ] Org-scoped, permission-checked, with a cross-tenant test
- [ ] Money logic covered by tests, including edge cases (partial, over-payment, duplicates)
- [ ] Migrations included and reversible where possible
- [ ] Admin registered
- [ ] No secrets in code
- [ ] Docs updated
- [ ] Reviewed (self-review at minimum; use `/code-review`)

## Git
- Branches: `master` is always deployable. Work on `feature/<short-name>`, merge by pull request.
- Commit small and often. Message style: imperative, descriptive (e.g. `Add lease model and services`).
- Never commit `.env`, `db.sqlite3`, `venv/`, `media/`, or credentials.
- Tag releases (`v0.1.0`, ...).

## Testing
- `pytest` with factories. Aim for high coverage on billing, payments, mpesa, permissions.
- Every bug fix comes with a regression test.
- CI must be green before merging.

## Code conventions
- PEP 8 via `ruff`. Type hints on services.
- Fat services, thin views. No business logic in templates.
- All user-facing strings wrapped for translation.
- Name things in domain language (Lease, Unit, Invoice, Allocation).

## Documentation
- Everything decided is written down. If it isn't in `config/docs/`, it isn't decided.
- Keep `PROJECT_CONTEXT.MD` status checklist current.
- Each app gets a short `README` section here or in its folder once it has real logic.

## Security habits
- Never paste real secrets into chats, issues, or commits.
- Rotate any secret that has been exposed.
- MFA on every service account.

## Environments
| Env | Purpose | DB | Notes |
|-----|---------|----|-------|
| local | development | local/Docker Postgres | `DEBUG=True` |
| staging | pilot demos, Daraja sandbox | managed Postgres | Real HTTPS URL for callbacks |
| production | real users | managed Postgres | `DEBUG=False`, backups, monitoring |
