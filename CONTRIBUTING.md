# Contributing

Thanks for improving AudioCodex.

Before opening a pull request:

1. Keep runtime/user data out of Git.
2. Run `python scripts/repo_check.py`.
3. Run `python -m compileall -q backend/src`.
4. Document database/schema changes and migration impact.
5. For Agent changes, document tools, permissions, confirmation requirements
   and source-grounding behavior.
6. Never add real API keys, private transcripts or machine-specific paths.

Design constraints:

- Preserve the established AudioCodex visual language unless the change is
  explicitly a design-system change.
- Prefer local-first data handling.
- Keep write actions confirmable.
- Keep application capabilities behind stable entities/tools instead of
  coupling them directly to one model provider.
