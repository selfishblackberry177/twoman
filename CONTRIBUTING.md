# Contributing

Twoman is a production-oriented relay project. Changes should be small,
reviewable, and backed by tests or clear runtime validation.

## Before you open a pull request

1. Read the top-level [README.md](/home/shahab/dev/hobby/mintm/README.md).
2. Use [docs/EASY_DEPLOY.md](/home/shahab/dev/hobby/mintm/docs/EASY_DEPLOY.md) for the current preferred install flow.
3. Use [docs/MANUAL_DEPLOY.md](/home/shahab/dev/hobby/mintm/docs/MANUAL_DEPLOY.md) when you need to inspect host or hidden-server stages directly.
4. Keep secrets, live tokens, and private host details out of the public tree.

## Development expectations

- Use conventional commits such as `feat:`, `fix:`, `docs:`, `refactor:`,
  `test:`, and `chore:`.
- Prefer targeted fixes over broad rewrites.
- Keep Android and shared Python runtime behavior aligned when touching both
  `twoman_transport.py` and the Android copy.
- When adding or changing deploy behavior, update the docs in the same change.

## Validation

At minimum, run the relevant focused checks for your change. Common commands:

```bash
python3 -m unittest \
  tests.test_transport_proxy \
  tests.test_twoman_control_cpanel \
  tests.test_twoman_control_installer \
  tests.test_twoman_control_manager \
  tests.test_desktop_client_tui

bash tests/run_e2e.sh
bash tests/run_e2e_node_http.sh
bash tests/run_desktop_client_e2e.sh
```

For release-oriented changes, also follow
[docs/RELEASE_CHECKLIST.md](/home/shahab/dev/hobby/mintm/docs/RELEASE_CHECKLIST.md).

## Pull request guidance

- Explain what changed, why it changed, and the expected operational impact.
- Mention any deployment or rollback considerations.
- Include screenshots for UI changes.
- Call out anything that was not tested.

## Reporting issues

Good issue reports include:

- the backend family in use
- whether the hidden server uses a direct route or an upstream proxy such as
  WireProxy
- exact failing command, screen, or runtime mode
- logs with secrets removed
