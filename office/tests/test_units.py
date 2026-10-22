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
from office.harness import ClaudeHarness, interpret
from office.matrix import Matrix
from office.store import Store

# --- playbooks ----------------------------------------------------------------


@pytest.mark.parametrize(
    "name, phases",
    [
        ("reckless", ["build"]),
        ("standard", ["spec", "scaffold", "mvp", "buildout", "review", "ship"]),
        ("rigorous", ["spec", "spec-review", "scaffold", "mvp", "buildout", "review", "second-pass", "ship"]),
        ("feature", ["change", "review", "ship"]),
    ],
)
def test_playbooks_load(name, phases):
    book = playbooks.load(REPO / "playbooks" / f"{name}.md")
    assert [p.name for p in book.phases] == phases
    assert all(p.instructions and p.max_turns > 0 for p in book.phases)
    assert book.intro


def test_playbook_sections_must_match_phases(tmp_path):
    path = tmp_path / "bad.md"
    path.write_text("---\nphases:\n  - {name: a, max_turns: 5}\n---\nintro\n\n## b\nstuff\n")
    with pytest.raises(ValueError, match="phase a has no '## a' section"):
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
                  "structured_output": {{"verdict": "continue", "message": "argv=" + " ".join(sys.argv[1:]) + " prompt=" + prompt}}}}))
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


# --- office-deploy ------------------------------------------------------------


def test_deploy_cli_is_scoped_to_the_job_project(monkeypatch):
    calls = []
    monkeypatch.setattr(deploy_cli, "call", lambda method, path, body=None: calls.append((method, path, body)) or 0)
    monkeypatch.setenv("OFFICE_SLUG", "trees")
    assert deploy_cli.main(["deploy", "abc"]) == 0
    assert deploy_cli.main(["status"]) == 0
    assert deploy_cli.main(["logs", "web", "50"]) == 0
    assert deploy_cli.main(["stop"]) == 2
    assert calls == [
        ("POST", "/deploy", {"slug": "trees", "sha": "abc"}),
        ("GET", "/status?slug=trees", None),
        ("GET", "/logs?slug=trees&service=web&tail=50", None),
    ]
    monkeypatch.delenv("OFFICE_SLUG")
    assert deploy_cli.main(["status"]) == 2


# --- status page --------------------------------------------------------------


def test_status_page_lists_projects_and_jobs(office):
    from starlette.testclient import TestClient

    from office.web import create_app

    office.store.add_project("trees", "Trees <b>", "Flashcards")
    office.store.add_job("trees", "new", "idea", "standard")
    client = TestClient(create_app(office.settings, office.store, office.scheduler))
    assert client.get("/healthz").json() == {"ok": True, "harness": "claude"}
    page = client.get("/").text
    assert "Trees &lt;b&gt;" in page and "https://trees.unlaunched.dev/" in page
    assert "<td>spec</td><td>queued</td>" in page


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
