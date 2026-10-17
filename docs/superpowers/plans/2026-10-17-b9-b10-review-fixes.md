# B9/B10 Review Fixes

**Spec:** `docs/spec.md`; the existing B9/B10 plans and independent review of `b79812f`.

## Global Constraints

- Fix only the three reported issues and regressions introduced by their fixes.
- Preserve six read-only hosted tools, seven shared stdio tools, public tenant admission, 30 tool calls/IP/minute, direct-to-Caddy ingress, and existing REST behavior.
- No dependency additions, live ingest, GitHub writes, deployment, push, or modifications to real tenant graphs. Preserve unrelated work.
- Use existing provisioned dependencies. Graph-dependent tests require disposable owned data; do not run broader graph-writing suites against real tenants.

### Task 1: Bound hosted input and fix review regressions

**Files:** `api/mcp_http.py`, `tests/test_mcp_http.py`, `tests/test_second_tenant.py`, and a focused offline test file if needed for graph ownership assertions; README only if limits need documentation.

- [x] Reproduce and add failing regressions before implementation: large/chunked body buffers before quota; list/object tool names cause TypeError; isolation assertion rejects unrelated test graphs.
- [x] Add finite body-size enforcement during ASGI receive, not just Content-Length. Use a documented 1 MiB maximum; reject oversized bodies with 413 before parsing/dispatch, even with missing/lying Content-Length and exhausted quota. Stop consuming on overflow.
- [x] Bound total body-upload time at 10 seconds using existing Python async facilities; return a controlled 408 on timeout without execution. Keep the timeout injectable for deterministic fast tests and do not timeout tool execution. Handle disconnect without executing partial payloads.
- [x] Validate tool names are strings before whitelist membership; return sanitized JSON-RPC rejection, preserving valid IDs and no execution. Cover list, object, null, boolean, and numeric names.
- [x] Replace global prefix equality in the disposable graph test with per-invocation graph membership checks. Verify unrelated graph names are tolerated while a missing owned graph still fails, without creating/deleting unrelated graphs. Delete only invocation-owned graphs.
- [x] Run focused red/green tests and deterministic backend regression suite. Record commands/output and any skips/unavailable runtime checks. Do not claim live deployment acceptance.
- [x] Self-review diff and report; commit only scoped fixes and this plan with a B9/B10 review-fix message.

### Review gates

- [ ] Fresh independent subagent reviews the fix-only diff for spec compliance and code quality.
- [ ] Separate scoped branch re-review checks all three original findings and their integration with unchanged B9/B10 boundaries; no unrelated redesign.
- [ ] Controller verifies final commands and clean/scoped working-tree status, then reports remaining limits.
