"""The status page: what anyone sees, signing in, and the changes Riggy can make there."""

from __future__ import annotations

import asyncio
import gzip
import json
from dataclasses import replace

import pytest
from starlette.testclient import TestClient

from office import plan, web
from office.control import Control
from office.live import LiveRuns, view
from office.web import COOKIE, Auth, create_app

SECRET = "ghp_supersecrettokenvalue"


@pytest.fixture
def page(office, monkeypatch):
    monkeypatch.setenv("GH_TOKEN", SECRET)
    office.store.add_project("trees", "Trees <b>", "Flashcards")
    job = office.store.add_job("trees", "new", "a flashcard game", "standard")
    client = TestClient(create_app(office.settings, office.store, office.scheduler), base_url="https://office.unlaunched.dev")
    client.job = job
    return client


def sign_in(client) -> None:
    r = client.post("/login", data={"password": "supi", "back": "/"}, follow_redirects=False)
    assert r.status_code == 303 and COOKIE in r.cookies


def act(client, **form):
    r = client.post("/act", data={"back": "/", **form}, follow_redirects=False)
    assert r.status_code == 303
    return r.headers["location"]


def start(office, job, at: str):
    """Give the job its steps and work them as far as the step labelled `at`."""
    steps = office.scheduler.ensure_steps(job)
    for s in steps:
        if plan.label(s) == at:
            break
        office.store.update_step(s.id, state="done", summary=f"{s.name} done", started_at="2026-10-23T10:00:00+00:00",
                                 ended_at="2026-10-23T10:05:00+00:00")  # fmt: skip
    return office.store.steps(job.id)


def by_name(office, job, name):
    return next(s for s in office.store.steps(job.id) if s.name == name)


def test_anyone_sees_each_project_as_a_lane(office, page):
    assert page.get("/healthz").json() == {"ok": True, "harness": "claude"}
    html = page.get("/").text
    assert "Trees &lt;b&gt;" in html and "https://trees.unlaunched.dev/" in html
    assert "queued</span> 0 of 24 steps" in html
    assert html.count("<li class='pending") == 24  # the strip: the steps it will get
    assert "Sign in" in html and "/act" not in html  # no controls signed out
    assert page.get("/?part=lanes").text.startswith("<section class=queue>")  # what the page refreshes with


def test_the_running_step_shows_its_live_tail_without_secrets(office, page):
    job = page.job
    steps = start(office, job, "spec")
    spec = plan.current(steps)
    office.store.update_step(spec.id, state="running", started_at="2026-10-23T10:00:00+00:00")
    office.store.update_job(job.id, state="running")
    log = office.settings.job_logs(job.id) / "001-spec.jsonl.gz"
    log.parent.mkdir(parents=True)
    events = [
        {"type": "assistant", "timestamp": "2026-10-23T10:01:00Z", "message": {"content": [{"type": "text", "text": "Reading the brief."}]}},
        {"type": "assistant", "timestamp": "2026-10-23T10:02:00Z", "message": {"content": [
            {"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": f"curl -H 'token {SECRET}' x", "description": "Check the API"}}]}},
        {"type": "user", "timestamp": "2026-10-23T10:03:00Z", "message": {"content": [{"type": "tool_result", "tool_use_id": "t1", "content": f"echo {SECRET}"}]}},
    ]  # fmt: skip
    with gzip.open(log, "wt") as f:
        f.writelines(json.dumps(e) + "\n" for e in events)
    office.store.start_run(job.id, "spec", "opus/high", str(log), spec.id)
    office.store.db.execute("UPDATE runs SET started_at = '2026-10-23T10:00:00+00:00'")
    (office.settings.job_logs(job.id) / "progress.txt").write_text("The brief makes sense.\n")

    html = page.get("/").text
    assert "<ul class=notes><li>The brief makes sense.</li></ul>" in html
    assert "1:00</time><span>💬 Reading the brief." in html
    assert "→ Bash Check the API" in html  # what it's doing, but not the command or its output
    assert "curl" not in html and SECRET not in html and "echo" not in html

    sign_in(page)
    html = page.get("/").text
    assert "→ Bash curl -H &#x27;token •••&#x27; x" in html and "← echo •••" in html  # in full, secrets masked
    assert SECRET not in html


def test_signing_in(office, page):
    r = page.post("/login", data={"password": "nope"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/?msg=Wrong%20password." and COOKIE not in r.cookies
    sign_in(page)
    html = page.get("/").text
    assert "Sign out" in html and "Pause all work" in html and "Run next" in html
    cookie = page.cookies[COOKIE]
    page.post("/logout", follow_redirects=False)
    assert "Sign in" in page.get("/").text
    # A changed password signs everyone out.
    page.cookies.set(COOKIE, cookie, domain="office.unlaunched.dev")
    assert "Sign out" in page.get("/").text
    assert not Auth(replace(office.settings, web_password="new"), office.store).valid(cookie)


def test_tokens_expire_and_guessing_is_slowed():
    class Store(dict):
        get = dict.get

        def put(self, k, v):
            self[k] = v

    now = [1000.0]

    class S:
        web_password = "supi"

    auth = Auth(S(), Store(), clock=lambda: now[0])
    token = auth.token()
    assert auth.valid(token) and not auth.valid(token + "0") and not auth.valid("123.abc") and not auth.valid(None)
    now[0] += 24 * 3600 + 1
    assert not auth.valid(token)
    for _ in range(web.MAX_FAILURES):
        assert auth.check_password("guess") is False
    assert auth.check_password("supi") is None  # even the right one, for a while
    now[0] += web.FAILURE_WINDOW + 1
    assert auth.check_password("supi") is True


def test_changes_need_signing_in_and_this_site(office, page):
    start(office, page.job, "spec")
    review = by_name(office, page.job, "review")
    assert "Sign%20in%20first" in act(page, action="skip", job=page.job.id, step=review.id)
    sign_in(page)
    r = page.post("/act", data={"action": "skip", "job": page.job.id, "step": review.id}, headers={"origin": "https://evil.example"}, follow_redirects=False)
    assert "Sign%20in%20first" in r.headers["location"]
    assert office.store.step(review.id).state == "pending"
    assert act(page, action="skip", job=page.job.id, step=review.id, back="https://evil.example/").startswith("/?msg=")


def test_skipping_only_optional_steps_that_have_not_started(office, page):
    sign_in(page)
    job = page.job
    start(office, job, "buildout")
    review, buildout = by_name(office, job, "review"), by_name(office, job, "buildout")
    html = page.get("/").text
    assert html.count(">Skip</button>") == 2  # review and docs; not buildout or office's shipping
    assert "Skipped%20review." in act(page, action="skip", job=job.id, step=review.id)
    assert office.store.step(review.id).state == "skipped"
    assert office.chat.sent == []  # the project has no room in this test
    assert "can%27t%20be%20skipped" in act(page, action="skip", job=job.id, step=buildout.id)
    deploy = [s for s in office.store.steps(job.id) if s.kind == "deploy"][-1]
    assert "can%27t%20be%20skipped" in act(page, action="skip", job=job.id, step=deploy.id)
    assert "Undo skip" in page.get("/").text
    assert "Put%20review%20back." in act(page, action="restore", job=job.id, step=review.id)
    assert office.store.step(review.id).state == "pending"


def test_skipped_steps_are_passed_over(office):
    async def go():
        from test_flow import new_project, verdict

        room = await new_project(office, [verdict("done")] * 6)
        await office.until("your check-in")
        job = office.store.jobs()[0]
        control = Control(office.store, office.scheduler)
        await control.skip(job.id, by_name(office, job, "review").id)
        assert office.chat.texts(room)[-1] == "Skipped review, from the status page."
        await control.skip(job.id, by_name(office, job, "checkin").id)
        await office.finish()
        assert [c.label for c in office.harness.calls[1:]] == ["spec", "scaffold", "mvp", "buildout", "docs"]
        assert office.store.job(job.id).state == "done"
        assert [plan.label(s) for s in office.store.steps(job.id)][-2:] == ["deploy", "done"]

    asyncio.run(go())


def test_skipping_the_checkin_carries_on(office):
    async def go():
        from test_flow import new_project, verdict

        room = await new_project(office, [verdict("done")] * 3)
        await office.finish()
        job = office.store.jobs()[0]
        assert job.state == "waiting"
        checkin = plan.current(office.store.steps(job.id))
        await Control(office.store, office.scheduler).skip(job.id, checkin.id)
        assert office.store.job(job.id).state == "queued"
        assert "as if you'd said it looks good" in office.chat.texts(room)[-1]
        await office.step()
        assert office.harness.calls[-1].label == "buildout"

    asyncio.run(go())


def test_queue_order_settings_notes_pause_and_stop(office, page):
    sign_in(page)
    office.store.add_project("birds", "Birds", "s")
    birds = office.store.add_job("birds", "new", "birds", "standard")
    trees = page.job
    html = page.get("/").text
    assert html.index("<b>1</b> Trees") < html.index("<b>2</b> Birds")
    act(page, action="move", job=birds.id, value="first")
    assert office.store.next_job(at="9999").id == birds.id
    html = page.get("/").text
    assert html.index("<b>1</b> Birds") < html.index("<b>2</b> Trees")
    act(page, action="move", job=birds.id, value="last")
    assert office.store.next_job(at="9999").id == trees.id

    act(page, action="setting", slug="trees", name="model", value="sonnet")
    assert office.store.project("trees").model == "sonnet"
    assert "can%27t%20be%20gpt" in act(page, action="setting", slug="trees", name="model", value="gpt")

    office.store.update_job(trees.id, state="waiting")
    assert "Picking%20the%20job%20back%20up" in act(page, action="note", job=trees.id, value="use oak first")
    assert office.store.job(trees.id).state == "queued" and office.store.pending_notes(trees.id)[-1][1] == "use oak first"

    act(page, action="pause", slug="trees")
    assert office.store.project("trees").paused and "Resume project" in page.get("/").text
    act(page, action="resume", slug="trees")
    act(page, action="pause-all")
    assert office.scheduler.paused and "Work is paused" in page.get("/").text
    act(page, action="resume-all")
    act(page, action="kill", job=birds.id)
    assert office.store.job(birds.id).state == "killed"
    assert "That%20job%20has%20finished." in act(page, action="kill", job=birds.id)


def test_job_page(office, page):
    start(office, page.job, "mvp")
    html = page.get(f"/jobs/{page.job.id}").text
    assert "bin/watch.sh 1" in html and "8 steps done" in html and " open>" in html
    assert page.get("/jobs/99").status_code == 404


def test_public_view_keeps_what_it_is_doing_but_not_how():
    live = LiveRuns(env={"DEPLOYER_SECRET": "abcdefgh123", "HOME": "/home/office"})
    lines = live.describe(
        {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "1", "name": "Read", "input": {"file_path": "src/a.ts"}},
                                                      {"type": "tool_use", "id": "2", "name": "Bash", "input": {"command": "echo abcdefgh123"}}]}},
        None,
    )  # fmt: skip
    assert [line.text for line in view(lines, signed_in=False)] == ["Read src/a.ts", "Bash"]
    assert [line.text for line in view(lines, signed_in=True)] == ["Read src/a.ts", "Bash echo •••"]
