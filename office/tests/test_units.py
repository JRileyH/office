"""Smaller pieces: playbooks, intake, the harness's reading of Claude Code output, Matrix, the deploy CLI."""

from __future__ import annotations

import asyncio
import gzip
import json
import stat
import sys

import httpx
import pytest
from conftest import OWNER, REPO

from office import deploy_cli, intake
from office import playbook as playbooks
from office.deployer_client import DeployerClient, DeployerError
from office.harness import ClaudeHarness, interpret
from office.matrix import Matrix
from office.store import Step, Store

# --- playbooks ----------------------------------------------------------------


@pytest.mark.parametrize(
    "name, mode, agents",
    [
        ("reckless", "reckless", ["build"]),
        ("standard", "standard", ["spec", "scaffold", "mvp", "buildout", "review", "docs"]),
        ("rigorous", "rigorous", ["spec", "spec-review", "scaffold", "mvp", "buildout", "review", "second-pass", "docs"]),
        ("feature", "reckless", ["change", "docs"]),
        ("feature", "standard", ["change", "review", "docs"]),
        ("feature", "rigorous", ["change", "review", "second-pass", "docs"]),
    ],
)
def test_playbooks_load(name, mode, agents):
    book = playbooks.load(REPO / "playbooks" / f"{name}.md")
    steps = book.for_mode(mode)
    assert [s.name for s in steps if s.kind == "agent"] == agents
    assert all(s.instructions and s.max_turns > 0 for s in steps if s.kind == "agent")
    assert [s.kind for s in steps][0] == "setup" and [s.kind for s in steps][-2:] == ["deploy", "alert"]
    assert book.intro


@pytest.mark.parametrize(
    "steps, sections, error",
    [
        ("[setup, {agent: a, turns: 5}, deploy, alert]", "## b\nstuff", "agent step a has no '## a' section"),
        ("[setup, {agent: a, turns: 5}, deploy, alert]", "## a\nx\n\n## b\nstuff", "sections without an agent step: b"),
        ("[setup, {agent: a, turns: 5}, deploy]", "## a\nx", "must start with setup and end with deploy, alert"),
        ("[{agent: a, turns: 5}, deploy, alert]", "## a\nx", "must start with setup and end with deploy, alert"),
        ("[setup, {agent: a, turns: 5}, deploy, {do: alert, modes: [rigorous]}]", "## a\nx", "must start with setup"),
        ("[setup, {agent: a, turns: 5}, ship-it, deploy, alert]", "## a\nx", "unknown step 'ship-it'"),
        ("[setup, {agent: a, turns: 5, modes: [yolo]}, deploy, alert]", "## a\nx", "not yolo"),
    ],
)
def test_bad_playbooks_are_refused(tmp_path, steps, sections, error):
    path = tmp_path / "bad.md"
    path.write_text(f"---\nsteps: {steps}\n---\nintro\n\n{sections}\n")
    with pytest.raises(ValueError, match=error):
        playbooks.load(path)


# --- intake -------------------------------------------------------------------


@pytest.mark.parametrize(
    "text, slug",
    [("Tree Cards", "tree-cards"), ("  Hello, World!! ", "hello-world"), ("x", "xapp"), ("", "project"), ("a" * 40, "a" * 31)],
)
def test_slugify(text, slug):
    assert intake.slugify(text) == slug
    assert intake.valid_slug(slug)


@pytest.mark.parametrize("slug", ["office", "-x", "x", "ab-", "UPPER", "a_b", "a" * 32])
def test_invalid_slugs(slug):
    assert not intake.valid_slug(slug)


def test_unique_slug():
    taken = {"trees", "trees-2", "office-app-2"}

    async def is_taken(slug):
        return slug in taken

    assert asyncio.run(intake.unique_slug("oaks", is_taken)) == "oaks"
    assert asyncio.run(intake.unique_slug("trees", is_taken)) == "trees-3"
    assert asyncio.run(intake.unique_slug("office", is_taken)) == "office-app-3"


# --- reading Claude Code's result ---------------------------------------------


def result(**fields):
    return {"type": "result", "subtype": "success", "is_error": False, "session_id": "s", "num_turns": 4,
            "total_cost_usd": 0.5, "usage": {"input_tokens": 10, "cache_read_input_tokens": 90, "output_tokens": 7},
            **fields}  # fmt: skip


def test_interpret_verdict():
    r = interpret(result(structured_output={"verdict": "done", "message": "hi"}), None, 0)
    assert (r.outcome, r.verdict, r.session_id) == ("verdict", {"verdict": "done", "message": "hi"}, "s")
    assert r.usage == {"num_turns": 4, "cost_usd": 0.5, "input_tokens": 100, "output_tokens": 7}


@pytest.mark.parametrize(
    "fields, outcome",
    [
        ({"subtype": "error_max_turns", "is_error": True}, "max_turns"),
        ({"is_error": True, "result": "Claude usage limit reached. Your limit will reset at 5pm"}, "limit"),
        ({"is_error": True, "api_error_status": 429, "result": "x"}, "limit"),
        ({"is_error": True, "result": "API Error: 500"}, "error"),
        ({"structured_output": {"verdict": "maybe", "message": ""}}, "error"),
        ({}, "error"),
    ],
)
def test_interpret_failures(fields, outcome):
    assert interpret(result(**fields), None, 1).outcome == outcome


def test_interpret_without_a_result():
    assert interpret(None, "s", 1, "You've hit your usage limit").outcome == "limit"
    r = interpret(None, "s", 1, "segfault")
    assert (r.outcome, r.error) == ("error", "claude exited 1 without a result: segfault")


FAKE_CLAUDE = """#!{python}
import json, sys
prompt = sys.stdin.read()
print(json.dumps({{"type": "system", "subtype": "init", "session_id": "sess-1"}}))
print(json.dumps({{"type": "assistant", "message": {{"content": [{{"type": "text", "text": "working"}}]}}}}))
print(json.dumps({{"type": "result", "subtype": "success", "is_error": False, "num_turns": 2,
                  "structured_output": {{"verdict": "done", "message": "argv=" + " ".join(sys.argv[1:]) + " prompt=" + prompt}}}}))
"""


def test_claude_harness_runs_the_cli(tmp_path):
    binary = tmp_path / "claude"
    binary.write_text(FAKE_CLAUDE.format(python=sys.executable))
    binary.chmod(binary.stat().st_mode | stat.S_IEXEC)
    log_path = tmp_path / "logs" / "001-spec.jsonl.gz"

    r = asyncio.run(
        ClaudeHarness(str(binary)).run(
            cwd=tmp_path, prompt="build it", model="sonnet", max_turns=60, log_path=log_path, env={}, resume="old"
        )
    )
    assert (r.outcome, r.session_id) == ("verdict", "sess-1")
    message = r.verdict["message"]
    assert "-p --output-format stream-json --verbose --model sonnet --max-turns 60 --permission-mode bypassPermissions" in message
    assert "--resume old" in message and message.endswith("prompt=build it")
    with gzip.open(log_path, "rt") as f:
        assert [json.loads(line)["type"] for line in f] == ["system", "assistant", "result"]


# --- Matrix -------------------------------------------------------------------


def matrix(tmp_path, handler) -> Matrix:
    client = httpx.AsyncClient(base_url="http://tuwunel:8008", transport=httpx.MockTransport(handler))
    return Matrix("http://tuwunel:8008", "@office:x", "pw", OWNER, Store(tmp_path / "db"), client)


def test_matrix_logs_in_once_and_sends(tmp_path):
    seen = []

    def handler(request):
        seen.append((request.method, request.url.raw_path.decode(), request.headers.get("authorization")))
        if request.url.path.endswith("/login"):
            body = json.loads(request.content)
            assert body["password"] == "pw" and body["device_id"] == "OFFICE_BOT"
            return httpx.Response(200, json={"access_token": "tok"})
        if request.method == "PUT":
            assert json.loads(request.content) == {"msgtype": "m.notice", "body": "hi"}
            return httpx.Response(200, json={"event_id": "$e"})
        return httpx.Response(404)

    m = matrix(tmp_path, handler)

    async def go():
        await m.login()
        assert await m.send("!r:x", "hi", notice=True) == "$e"

    asyncio.run(go())
    assert m.store.get("matrix_token") == "tok"
    assert seen[-1][2] == "Bearer tok" and "/rooms/%21r%3Ax/send/m.room.message/" in seen[-1][1]


def test_matrix_room_alias_taken_falls_back(tmp_path):
    bodies = []

    def handler(request):
        body = json.loads(request.content)
        bodies.append(body)
        if "room_alias_name" in body:
            return httpx.Response(400, json={"errcode": "M_ROOM_IN_USE"})
        return httpx.Response(200, json={"room_id": "!new:x"})

    m = matrix(tmp_path, handler)
    m.token = "tok"
    assert asyncio.run(m.create_room("Trees", "topic", alias="trees")) == "!new:x"
    assert bodies[0]["invite"] == [OWNER] and bodies[0]["power_level_content_override"]["users"][OWNER] == 100
    assert "room_alias_name" not in bodies[1]


def test_matrix_only_listens_to_the_owner(tmp_path):
    m = matrix(tmp_path, lambda r: httpx.Response(404))

    def event(sender, body="hi", **content):
        return {"type": "m.room.message", "sender": sender, "event_id": "$" + body,
                "content": {"msgtype": "m.text", "body": body, **content}}  # fmt: skip

    data = {
        "rooms": {
            "join": {
                "!a:x": {
                    "timeline": {
                        "events": [
                            event(OWNER, " hello "),
                            event("@mallory:x", "evil"),
                            event("@office:x", "self"),
                            event(OWNER, "* edit", **{"m.relates_to": {"rel_type": "m.replace"}}),
                            event(OWNER, "looks good", **{"m.relates_to": {"m.in_reply_to": {"event_id": "$x"}}}),
                            event(OWNER, "> <@office:x> Try it\n\nbigger photos", **{"m.relates_to": {"m.in_reply_to": {"event_id": "$y"}}}),
                            {**event(OWNER, "note"), "content": {"msgtype": "m.notice", "body": "note"}},
                        ]
                    }
                }
            }
        }
    }
    assert [msg.body for msg in m.messages(data)] == ["hello", "looks good", "bigger photos"]


def test_matrix_accepts_invites_from_the_owner_only(tmp_path):
    joined = []

    def handler(request):
        joined.append(request.url.raw_path.decode())
        return httpx.Response(200, json={})

    m = matrix(tmp_path, handler)
    m.token = "tok"

    def invite(sender):
        return {"invite_state": {"events": [{"type": "m.room.member", "state_key": "@office:x", "sender": sender}]}}

    asyncio.run(m.accept_invites({"rooms": {"invite": {"!dm:x": invite(OWNER), "!spam:x": invite("@mallory:x")}}}))
    assert joined == ["/_matrix/client/v3/join/%21dm%3Ax"]


def test_matrix_close_room_frees_aliases_removes_the_owner_and_leaves(tmp_path):
    calls = []

    def handler(request):
        calls.append((request.method, request.url.raw_path.decode().removeprefix("/_matrix/client/v3")))
        if request.url.path.endswith("/aliases"):
            return httpx.Response(200, json={"aliases": ["#trees:x", "#old-trees:x"]})
        if request.url.path.endswith("/kick"):
            assert json.loads(request.content) == {"user_id": OWNER, "reason": "Trees was deleted."}
            return httpx.Response(403, json={"errcode": "M_FORBIDDEN"})  # best effort: carry on
        return httpx.Response(200, json={})

    m = matrix(tmp_path, handler)
    m.token = "tok"
    asyncio.run(m.close_room("!r:x", "Trees was deleted."))
    assert calls == [
        ("GET", "/rooms/%21r%3Ax/aliases"),
        ("DELETE", "/directory/room/%23trees%3Ax"),
        ("DELETE", "/directory/room/%23old-trees%3Ax"),
        ("POST", "/rooms/%21r%3Ax/kick"),
        ("POST", "/rooms/%21r%3Ax/leave"),
        ("POST", "/rooms/%21r%3Ax/forget"),
    ]


# --- deployer client ----------------------------------------------------------


def deployer(handler) -> DeployerClient:
    client = httpx.AsyncClient(
        base_url="http://deployer:8000", headers={"X-Deployer-Secret": "s"}, transport=httpx.MockTransport(handler)
    )
    return DeployerClient("http://deployer:8000", "s", "admin", client)


def test_deployer_client_deletes_with_the_admin_secret():
    seen = []

    def handler(request):
        seen.append(request.headers.get("x-deployer-admin-secret"))
        if json.loads(request.content)["slug"] == "never":
            return httpx.Response(422, json={"ok": False, "reasons": ["`never` has not been deployed"]})
        if json.loads(request.content)["slug"] == "off":
            return httpx.Response(403, json={"ok": False, "reasons": ["deleting is off: DEPLOYER_ADMIN_SECRET is not set"]})
        return httpx.Response(200, json={"ok": True, "deleted": "trees"})

    d = deployer(handler)
    asyncio.run(d.delete("trees"))
    asyncio.run(d.delete("never"))  # nothing to delete is fine
    with pytest.raises(DeployerError, match="^deleting is off: DEPLOYER_ADMIN_SECRET is not set$"):
        asyncio.run(d.delete("off"))
    assert seen == ["admin"] * 3


def test_deployer_client_lists_projects():
    d = deployer(lambda request: httpx.Response(200, json={"ok": True, "projects": ["hello", "trees"]}))
    assert asyncio.run(d.projects()) == ["hello", "trees"]
    d = deployer(lambda request: httpx.Response(500, text="boom"))
    with pytest.raises(DeployerError, match="^the deployer said 500$"):
        asyncio.run(d.projects())


# --- store --------------------------------------------------------------------


def test_store_inserts_steps_in_order(tmp_path):
    store = Store(tmp_path / "db")
    store.add_project("trees", "Trees", "s")
    job = store.add_job("trees", "new", "idea", "standard")
    store.add_steps(job.id, [Step(0, job.id, 0, kind, kind) for kind in ("setup", "checks", "deploy")])
    store.add_steps(job.id, [Step(0, job.id, 0, "agent", "fix", 150, problem="CI failed"), Step(0, job.id, 0, "push", "push")], after=2)
    steps = store.steps(job.id)
    assert [(s.position, s.name) for s in steps] == [(1, "setup"), (2, "checks"), (3, "fix"), (4, "push"), (5, "deploy")]
    assert (steps[2].max_turns, steps[2].problem, steps[2].state, steps[2].links) == (150, "CI failed", "pending", {})
    assert store.update_step(steps[0].id, links={"repo": "https://x"}).links == {"repo": "https://x"}


def test_store_delete_project_leaves_other_projects_alone(tmp_path):
    store = Store(tmp_path / "db")
    for slug in ("trees", "birds"):
        store.add_project(slug, slug.title(), "s")
        job = store.add_job(slug, "new", "idea", "standard")
        store.add_note(job.id, f"note for {slug}")
        store.start_run(job.id, "spec", "opus/high", "log")
    assert store.delete_project("trees") == [1]
    assert [p.slug for p in store.projects()] == ["birds"]
    assert [j.slug for j in store.jobs()] == ["birds"]
    assert [r["job_id"] for r in store.runs()] == [2]
    assert store.pending_notes(1) == [] and store.pending_notes(2) == [(2, "note for birds")]


# --- office-deploy ------------------------------------------------------------


def test_deploy_cli_is_scoped_to_the_job_project(monkeypatch):
    calls = []
    monkeypatch.setattr(deploy_cli, "call", lambda method, path, body=None: calls.append((method, path, body)) or 0)
    monkeypatch.setenv("OFFICE_SLUG", "trees")
    assert deploy_cli.main(["status"]) == 0
    assert deploy_cli.main(["logs", "web", "50"]) == 0
    assert deploy_cli.main(["stop"]) == 2
    assert deploy_cli.main(["deploy", "abc"]) == 2  # deploying is office's own step
    assert calls == [
        ("GET", "/status?slug=trees", None),
        ("GET", "/logs?slug=trees&service=web&tail=50", None),
    ]
    monkeypatch.delenv("OFFICE_SLUG")
    assert deploy_cli.main(["status"]) == 2


# --- status page --------------------------------------------------------------


def test_status_page_shows_each_jobs_steps(office):
    from starlette.testclient import TestClient

    from office.web import create_app

    office.store.add_project("trees", "Trees <b>", "Flashcards")
    job = office.store.add_job("trees", "new", "idea", "standard")
    client = TestClient(create_app(office.settings, office.store, office.scheduler))
    assert client.get("/healthz").json() == {"ok": True, "harness": "claude"}
    page = client.get("/").text
    assert "Trees &lt;b&gt;" in page and "https://trees.unlaunched.dev/" in page
    # Before it starts: the steps it will get.
    assert "queued · set up (1/24) · standard" in page
    assert "<tr class=pending><td>○</td><td>mvp</td>" in page

    steps = office.scheduler.ensure_steps(job)
    office.store.update_step(steps[0].id, state="done", summary="Repo ready.", links={"issue": "https://github.com/unlaunched/trees/issues/1", "bad": "javascript:alert(1)"})
    office.store.update_step(steps[1].id, state="running", started_at="2026-10-23T10:00:00+00:00")
    office.store.update_job(job.id, state="running")
    page = client.get("/").text
    assert "<tr class=done><td>✓</td><td>set up</td><td>Repo ready.<div class=links><a href='https://github.com/unlaunched/trees/issues/1'>issue</a></div>" in page
    assert "javascript:" not in page
    assert "<tr class=running><td>▶</td><td>spec</td>" in page
    assert "running · spec (2/24)" in page
    job_page = client.get(f"/jobs/{job.id}").text
    assert "bin/watch.sh 1" in job_page and "<td>mvp</td>" in job_page
    assert client.get("/jobs/99").status_code == 404


def test_structured_output_with_a_custom_schema_is_not_a_verdict():
    """The bug that named a project after the first words of its dictation."""
    brief = {"name": "Leaf Peeper", "slug": "leaf-peeper", "summary": "s"}
    r = interpret(result(structured_output=brief), None, 0, verdict_expected=False)
    assert (r.outcome, r.verdict) == ("verdict", brief)
    assert interpret(result(structured_output=brief), None, 0).outcome == "error"


def test_triage_through_the_claude_harness(tmp_path):
    binary = tmp_path / "claude"
    binary.write_text(
        f"#!{sys.executable}\nimport json, sys\nsys.stdin.read()\n"
        "print(json.dumps({'type': 'result', 'subtype': 'success', 'is_error': False, 'structured_output':"
        " {'name': 'Leaf Peeper', 'slug': 'leaf-peeper', 'summary': 'Guess the tree.', 'mode': 'rigorous',"
        " 'model': 'unspecified', 'effort': 'max'}}))\n"
    )
    binary.chmod(binary.stat().st_mode | stat.S_IEXEC)
    brief = asyncio.run(
        intake.triage(
            ClaudeHarness(str(binary)), REPO / "prompts" / "intake.md", "hey i would like a game called leaf peeper",
            cwd=tmp_path / "triage", log_path=tmp_path / "triage.jsonl.gz", env={},
        )
    )  # fmt: skip
    assert brief == intake.Brief("Leaf Peeper", "leaf-peeper", "Guess the tree.", mode="rigorous", effort="max")


def test_older_databases_get_new_columns(tmp_path):
    import sqlite3

    db = sqlite3.connect(tmp_path / "old.db")
    db.execute(
        "CREATE TABLE projects (slug TEXT PRIMARY KEY, name TEXT NOT NULL, summary TEXT NOT NULL,"
        " mode TEXT NOT NULL DEFAULT 'standard', room_id TEXT UNIQUE, paused INTEGER NOT NULL DEFAULT 0,"
        " created_at TEXT NOT NULL)"
    )
    db.execute("INSERT INTO projects (slug, name, summary, created_at) VALUES ('hey', 'Hey', 's', 'now')")
    db.commit()
    db.close()
    project = Store(tmp_path / "old.db").project("hey")
    assert (project.model, project.effort) == ("opus", "high")


# --- office's own git steps, on a real repo ---------------------------------------


@pytest.fixture
def checkout(tmp_path, monkeypatch):
    """A GitHubWorkspace whose `trees` checkout has a local bare repo as origin."""
    import subprocess

    from office.workspace import GitHubWorkspace

    for name in ("AUTHOR", "COMMITTER"):
        monkeypatch.setenv(f"GIT_{name}_NAME", "office")
        monkeypatch.setenv(f"GIT_{name}_EMAIL", "office@example.com")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / "gitconfig"))
    (tmp_path / "gitconfig").write_text("[init]\n\tdefaultBranch = main\n")
    subprocess.run(["git", "init", "--quiet", "--bare", str(tmp_path / "origin.git")], check=True)
    subprocess.run(["git", "clone", "--quiet", str(tmp_path / "origin.git"), str(tmp_path / "ws" / "trees")], check=True)
    return GitHubWorkspace(tmp_path / "ws", "unlaunched")


def git(ws, *args) -> str:
    import subprocess

    return subprocess.run(["git", *args], cwd=ws.path("trees"), capture_output=True, text=True, check=True).stdout.strip()


def test_git_steps_branch_commit_push_and_land(checkout):
    ws = checkout

    async def go():
        await ws.ensure_main("trees", "# Trees\n")
        assert git(ws, "log", "--oneline", "origin/main").endswith("Start the project")
        await ws.ensure_main("trees", "# Trees\n")  # once only
        assert await ws.push_branch("trees", "office/job-1") is None  # nothing new for main

        await ws.start_branch("trees", "office/job-1")
        assert git(ws, "branch", "--show-current") == "office/job-1"
        (ws.path("trees") / "app.js").write_text("hi\n")
        (ws.path("trees") / "node_modules").mkdir()
        (ws.path("trees") / "node_modules" / "dep.js").write_text("x\n")
        assert await ws.commit_all("trees", "Work left uncommitted")
        assert git(ws, "show", "--name-only", "--format=", "HEAD") == "app.js"  # never node_modules
        assert not await ws.commit_all("trees", "again")

        sha = await ws.push_branch("trees", "office/job-1")
        assert sha == git(ws, "rev-parse", "origin/office/job-1")
        await ws.start_branch("trees", "office/job-1")  # already on it: stays

        assert await ws.push_main("trees", "office/job-1") == sha  # reckless: straight to main
        assert git(ws, "branch", "--show-current") == "main" and await ws.main_sha("trees") == sha
        assert "office/job-1" not in git(ws, "branch")  # gone locally, ready for the next work

    asyncio.run(go())


def test_ci_is_read_like_the_deployer_reads_it(checkout, monkeypatch):
    from office import workspace

    runs = {"workflow_runs": [
        {"id": 1, "workflow_id": 9, "name": "build", "status": "completed", "conclusion": "failure", "html_url": "u1"},
        {"id": 2, "workflow_id": 9, "name": "build", "status": "in_progress", "conclusion": None, "html_url": "u2"},
    ]}  # fmt: skip

    async def fake_run(*args, cwd=None):
        if args[:2] == ("gh", "api"):
            return 0, json.dumps(runs)
        return 0, "npm test: 2 failing"

    monkeypatch.setattr(workspace, "run", fake_run)
    ci = asyncio.run(checkout.ci("trees", "abc"))
    assert (ci.state, ci.detail, ci.url) == ("pending", "build: in_progress", "u2")  # the rerun replaces the failure
    runs["workflow_runs"][1].update(status="completed", conclusion="failure")
    ci = asyncio.run(checkout.ci("trees", "abc"))
    assert (ci.state, ci.detail) == ("failed", "build: failure\n\nnpm test: 2 failing")
    runs["workflow_runs"][1].update(conclusion="success")
    assert asyncio.run(checkout.ci("trees", "abc")).state == "passed"
    runs["workflow_runs"].clear()
    assert asyncio.run(checkout.ci("trees", "abc")).state == "none"


def test_deployer_client_deploys_and_says_what_to_fix():
    def handler(request):
        sha = json.loads(request.content)["sha"]
        if sha == "good":
            return httpx.Response(200, json={"ok": True, "url": "https://trees.unlaunched.dev", "sha": sha})
        if sha == "rules":
            return httpx.Response(422, json={"ok": False, "reasons": ["ports: not allowed", "mem_limit missing"]})
        if sha == "crash":
            return httpx.Response(502, json={"ok": False, "error": "didn't answer", "logs": "Error: EADDRINUSE"})
        return httpx.Response(502, json={"ok": False, "error": "GitHub returned 401"})

    d = deployer(handler)
    assert asyncio.run(d.deploy("trees", "good")).ok
    assert asyncio.run(d.deploy("trees", "rules")).problem == "ports: not allowed\nmem_limit missing"
    assert asyncio.run(d.deploy("trees", "crash")).problem == "didn't answer\n\nLogs:\nError: EADDRINUSE"
    with pytest.raises(DeployerError, match="GitHub returned 401"):  # office's setup, not the project's code
        asyncio.run(d.deploy("trees", "config"))
