"""Offline persisted-triage/apply harness shared by webhook retry regressions."""

import json

import httpx

from api import schemas
from api.actions import ActionService, ApplyContext
from api.github import SandboxGitHub

SANDBOX = "acme/keystone-sandbox"


class RetryHarness:
    def __init__(self):
        self.contexts = {}
        self.runs = []
        self.record_attempts = []
        self.recorded = []
        self.comments = []
        self.fail_record = True
        self.fail_triage = False
        self.client = httpx.Client(
            transport=httpx.MockTransport(self.github),
            headers={"Authorization": "Bearer offline-test-token"},
        )
        self.writer = SandboxGitHub(SANDBOX, self.client)
        self.service = ActionService(
            store=self, writer=self.writer, sandbox_repo=SANDBOX, api_mode="live"
        )

    def github(self, request):
        path = request.url.path
        if request.method == "GET" and "/collaborators/" in path:
            return httpx.Response(204)
        if path.endswith("/comments"):
            if request.method == "POST":
                self.comments.append((path, json.loads(request.content)["body"]))
                return httpx.Response(201, json={"id": len(self.comments)})
            return httpx.Response(
                200,
                json=[
                    {"body": body} for comment_path, body in self.comments if comment_path == path
                ],
            )
        raise AssertionError(f"unexpected request: {request.method} {path}")

    async def triage(self, tenant, issue_key):
        if self.fail_triage:
            self.fail_triage = False
            raise RuntimeError("triage failed before persistence")
        run_key = f"run:00000000-0000-4000-8000-{len(self.runs) + 1:012d}"
        result = schemas.TriageResult(
            run_key=run_key,
            labels=[],
            reviewers=[],
            risks=[],
            decisions=[],
            comment_markdown="Offline triage.",
            trace=[],
        )
        self.runs.append((tenant, issue_key, run_key))
        self.contexts[tenant, run_key] = ApplyContext(
            run_key=run_key,
            issue_key=issue_key,
            repo=SANDBOX,
            issue_number=int(issue_key.rsplit("#", 1)[1]),
            incoming=True,
            actions=[
                schemas.RecordedAction(
                    action_key=f"action:{run_key}",
                    kind="comment",
                    value="comment",
                    status="proposed",
                    module=None,
                    note=None,
                    feedback=[],
                )
            ],
            comment_markdown=result.comment_markdown,
        )
        return result

    def load_context(self, tenant, run_key):
        return self.contexts[tenant, run_key]

    def record_results(self, tenant, run_key, results):
        self.record_attempts.append((tenant, run_key))
        if self.fail_record:
            self.fail_record = False
            raise RuntimeError("record failed after comment accepted")
        self.recorded.append((tenant, run_key, results))
        context = self.contexts[tenant, run_key]
        for action, result in zip(context.actions, results):
            action.status = result.status
