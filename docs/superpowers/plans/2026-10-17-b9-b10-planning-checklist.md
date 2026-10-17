# B9/B10 planning checklist

- [x] Create a separate worktree and planning branch from local `main` (`b62c416`).
- [x] Read `docs/tasks.md`, `docs/spec.md`, and repository working agreements.
- [x] Interview: use non-overlapping repositories; tenant 2 is `pallets/flask`.
- [x] Interview: both configured tenants are public through the read-only hosted MCP endpoint; GitHub write tools remain unavailable over HTTP.
- [x] Separate subagent drafts B9 implementation plan against existing code.
- [x] Separate subagent drafts B10 implementation plan against existing code.
- [x] Review both plans for spec coverage, exact interfaces, test cycles, and scope; reduce B9 test infrastructure to fit its optional timebox.
- [x] Resolve material questions with the user and hand over plans for review.

Approved ingress: Caddy directly faces the internet; Cloudflare is DNS-only if used. Public application ports are 80/443; API 8000 and FalkorDB 6379/3000 remain private. Caddy manages HTTPS. Actual API domain/client access is needed for execution-time remote acceptance, not to draft the plan. Pinned FastMCP HTTP API compatibility also requires execution-time verification; no dependency change is approved.

Planning only: no product code, dependency installs, ingest, deployment, or task completion claims.
