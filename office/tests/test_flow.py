"""End-to-end flows through the bot and scheduler, with Matrix, GitHub and Claude faked."""

from __future__ import annotations

import asyncio
import gzip
import json
from datetime import UTC, datetime, timedelta

from conftest import Office

from office import nuke, plan, scheduler
from office.harness import WRAPUP_SCHEMA, RunResult
from office.workspace import CI

TREES = {"name": "Tree Cards", "slug": "tree-cards", "summary": "A flashcard game for northeastern US trees."}
URL = "https://tree-cards.unlaunched.dev/"
CHECKIN = ["set up", "spec", "scaffold", "push", "PR", "CI", "merge", "deploy", "mvp", "push", "PR", "CI", "merge", "deploy"]


def run(coro):
    return asyncio.run(coro)


def verdict(kind: str, message: str = "ok", link: str = "") -> dict:
    return {"verdict": kind, "message": message, **({"link": link} if link else {})}


async def new_project(office: Office, script_after: list) -> str:
    """Dictate the trees idea in the inbox; returns the project's room."""
    office.harness.script = [TREES, *script_after]
    await office.bot.ensure_rooms()
    await office.say(office.inbox, "a flashcard game for identifying northeastern us trees")
    return office.store.projects()[0].room_id


def current(office: Office, job_id: int):
    return plan.current(office.store.steps(job_id))


# --- new projects -------------------------------------------------------------


def test_inbox_idea_becomes_a_project_with_its_own_room(office):
    async def go():
        room = await new_project(office, [])
        project = office.store.project("tree-cards")
        assert project.name == "Tree Cards"
        created = office.chat.rooms[-1]
        assert created == {
            "id": room,
            "name": "Tree Cards",
            "topic": "https://tree-cards.unlaunched.dev/ · https://github.com/unlaunched/tree-cards",
            "alias": "tree-cards",
        }
        job = office.store.active_job("tree-cards")
        # A minute's grace (none in tests) to change the settings before work starts.
        assert (job.kind, job.playbook, job.state) == ("new", "standard", "parked")
        assert job.request == "a flashcard game for identifying northeastern us trees"
        [reply] = office.chat.texts(office.inbox)[1:]  # after the welcome
        assert reply.startswith("New project: Tree Cards (tree-cards), mode standard, model opus, high effort.")
        assert f"https://matrix.to/#/{room}" in reply
        [intro] = office.chat.texts(room)
        assert "A flashcard game for northeastern US trees." in intro
        assert "https://tree-cards.unlaunched.dev/" in intro
        assert "Mode standard, model opus, high effort. Starting in a minute" in intro
        # Triage is a cheap structured call.
        triage = office.harness.calls[0]
        assert (triage.model, triage.label) == ("haiku", "intake")
        assert "OFFICE_MATRIX_PASSWORD" not in triage.env

    run(go())


def test_slug_clashes_get_a_suffix(office):
    async def go():
        office.workspace.repos.add("tree-cards")  # a repo by that name already exists
        await new_project(office, [])
        assert office.store.project("tree-cards") is None
        assert office.store.project("tree-cards-2").name == "Tree Cards"

    run(go())


def test_a_bad_triage_falls_back_to_the_dictation(office):
    async def go():
        office.harness.script = [RunResult("error", error="boom")]
        await office.bot.ensure_rooms()
        await office.say(office.inbox, "Pomodoro timer with sounds")
        assert office.store.project("pomodoro-timer-with-sounds").name == "Pomodoro Timer With Sounds"

    run(go())


def test_standard_steps_walk_to_the_mvp_checkin_and_on_to_a_deployed_done(office):
    async def go():
        room = await new_project(
            office,
            [
                verdict("done", "MVP: pick the tree from four photos."),
                verdict("done", "A styled placeholder page."),
                verdict("done", "Try the quiz: ten trees per round."),
            ],
        )
        await office.finish()  # until the check-in, which waits on Riggy
        job = office.store.active_job("tree-cards")
        assert job.state == "waiting"
        assert office.labels(job.id) == CHECKIN
        assert (plan.label(current(office, job.id)), current(office, job.id).state) == ("your check-in", "waiting")
        assert job.issue == 1 and office.workspace.issues == [("tree-cards", "Tree Cards")]

        # office's own code pushed, opened PRs, waited for CI, merged and deployed: twice by now.
        did = [a for a in office.workspace.actions if not a.startswith("CI on")]
        assert did == [
            "push office/job-1", "open PR #1: Tree Cards: spec + scaffold", "merge PR #1",
            "push office/job-1", "open PR #2: Tree Cards: mvp", "merge PR #2",
        ]  # fmt: skip
        assert len(office.deployer.deploys) == 2
        assert office.deployer.deploys[-1] == ("tree-cards", office.workspace.main["tree-cards"])  # main, after the merge
        assert office.workspace.bodies[0].startswith("Part of #1\n\n- **spec:** MVP: pick the tree from four photos.")

        texts = office.chat.texts(room)
        assert "✓ spec: MVP: pick the tree from four photos.\nNext: scaffold." in texts
        assert "✓ scaffold: A styled placeholder page.\nNext: PR, CI, merge, deploy, then mvp." in texts
        assert "✓ PR: PR #1 is open.\nhttps://github.com/unlaunched/tree-cards/pull/1\nNext: CI, merge, deploy, then mvp." in texts
        deployed = [t for t in texts if t.startswith("✓ deploy:")]
        assert deployed[0].endswith(f" is live.\n{URL}\nNext: mvp.")
        assert deployed[1].endswith(f" is live.\n{URL}\nNext: your check-in, then buildout.")
        assert texts[-1] == f"Try the quiz: ten trees per round.\n{URL}\n\nTry it and reply here: say it looks good, or what to change."
        # Progress is posted as notices (no push); the check-in as a message (push).
        assert [n for r, _, n in office.chat.sent if r == room][-1] is False

        # Nothing runs while the job waits on Riggy.
        assert office.store.next_job(at="9999") is None

        await office.say(room, "looks good but make the photos bigger")
        assert office.chat.texts(room)[-1] == "Thanks, picking it back up. Starting now."
        office.harness.script = [verdict("done"), verdict("done"), verdict("done", "Ten trees a round, with streaks.")]
        await office.finish()

        buildout, review, docs = office.harness.calls[-3:]
        assert "# Your step: buildout" in buildout.prompt
        assert "> looks good but make the photos bigger" in buildout.prompt
        assert "- your check-in: You said: “looks good but make the photos bigger”" in buildout.prompt
        assert "OFFICE_MATRIX_PASSWORD" not in buildout.env
        assert buildout.env["OFFICE_SLUG"] == "tree-cards"
        # The reply was delivered once; the next step doesn't see it again.
        assert "make the photos bigger\n" not in review.prompt.split("# Riggy's feedback")[1]

        job = office.store.job(job.id)
        assert job.state == "done"
        steps = office.store.steps(job.id)
        assert [s.state for s in steps] == ["done"] * 24
        assert [plan.label(s) for s in steps[-2:]] == ["deploy", "done"]
        assert len(office.deployer.deploys) == 3
        assert office.workspace.bodies[-1].startswith("Closes #1")
        assert office.chat.texts(room)[-1] == f"Done: Ten trees a round, with streaks.\n{URL}"
        assert [r["phase"] for r in office.store.runs(job.id)] == ["spec", "scaffold", "mvp", "buildout", "review", "docs"]
        assert "Finished job" in office.chat.texts(office.store.get("status_room"))[-1]

    run(go())


def test_reckless_goes_straight_to_main(tmp_path):
    office = Office(tmp_path, default_mode="reckless")

    async def go():
        room = await new_project(office, [verdict("done", "Flip cards to learn twenty trees.")])
        await office.finish()
        job = office.store.jobs()[0]
        assert job.state == "done"
        assert office.labels(job.id) == ["set up", "build", "push to main", "deploy", "done"]
        assert [a for a in office.workspace.actions if not a.startswith("CI on")] == ["push main"]
        assert office.chat.texts(room)[-1] == f"Done: Flip cards to learn twenty trees.\n{URL}"

    run(go())


def test_a_worker_has_no_say_in_what_comes_next(office):
    """Whatever a worker says, office runs the next step; only its own last steps finish a job."""

    async def go():
        room = await new_project(office, [])
        await office.until("spec")
        office.harness.script = [verdict("done", "All done, nothing more to do!")]
        await office.step()
        job = office.store.jobs()[0]
        assert job.state == "queued" and plan.label(current(office, job.id)) == "scaffold"
        assert office.harness.calls[-1].schema is None  # the verdict schema: done or blocked, nothing else
        # The old verdicts are gone: a worker can't skip ahead or stop for a review.
        office.harness.script = [verdict("checkin", "Please review my PR")]
        await office.step()
        assert office.store.job(job.id).state == "parked"  # not a verdict: retried
        assert (current(office, job.id).name, current(office, job.id).error) == ("scaffold", "unknown verdict 'checkin'")
        assert not any("Please review my PR" in t for t in office.chat.texts(room))

    run(go())


def test_workers_never_get_office_secrets(office, monkeypatch):
    monkeypatch.setenv("OFFICE_MATRIX_PASSWORD", "pw")
    monkeypatch.setenv("DEPLOYER_ADMIN_SECRET", "admin")
    monkeypatch.setenv("DEPLOYER_SECRET", "deploy")

    async def go():
        await new_project(office, [verdict("done")])
        await office.until("spec")
        await office.step()
        triage, step = office.harness.calls
        for call in (triage, step):
            assert "OFFICE_MATRIX_PASSWORD" not in call.env and "DEPLOYER_ADMIN_SECRET" not in call.env
        assert step.env["DEPLOYER_SECRET"] == "deploy"  # they can read their own project's status and logs

    run(go())


def test_step_prompts_are_fully_filled_in(office):
    async def go():
        await new_project(office, [verdict("done")])
        await office.until("spec")
        await office.step()
        prompt = office.harness.calls[-1].prompt
        assert '>> "$OFFICE_PROGRESS"' in prompt  # an environment variable, not a placeholder
        assert "$" not in prompt.replace("$(", "").replace("$OFFICE_PROGRESS", "")
        assert "# Your step: spec" in prompt
        assert "on the branch `office/job-1`" in prompt
        assert "Don't push, open or merge pull requests, or deploy" in prompt
        assert "> a flashcard game for identifying northeastern us trees" in prompt
        assert "https://github.com/unlaunched/tree-cards/issues/1" in prompt
        assert office.workspace.branch == "office/job-1"

    run(go())


# --- when something fails ------------------------------------------------------


def test_failed_ci_gets_a_fix_step_and_the_delivery_again(office):
    async def go():
        room = await new_project(office, [verdict("done"), verdict("done")])
        await office.until("CI")
        failure = CI("failed", "build: failure\n\nnpm test: 2 failing", "https://github.com/unlaunched/tree-cards/actions/runs/7")
        office.workspace.ci_results = [failure]
        await office.step()
        job = office.store.jobs()[0]
        assert office.chat.texts(room)[-1] == "✗ CI: CI failed on PR #1. Fixing it."
        failed = [s for s in office.store.steps(job.id) if s.state == "failed"]
        assert [(plan.label(s), s.links) for s in failed] == [("CI", {"ci": failure.url})]
        upcoming = [plan.label(s) for s in office.store.steps(job.id) if s.state == "pending"]
        assert upcoming[:6] == ["fix", "push", "PR", "CI", "merge", "deploy"]

        office.harness.script = [verdict("done", "The tests expected the old deck size.")]
        await office.step()
        fix = office.harness.calls[-1]
        assert "# Your step: fix" in fix.prompt and "npm test: 2 failing" in fix.prompt
        await office.until("mvp")
        assert office.labels(job.id)[3:] == ["push", "PR", "CI", "fix", "push", "PR", "CI", "merge", "deploy"]
        assert len(office.workspace.bodies) == 1  # the same PR, fixed

    run(go())


def test_a_refused_deploy_is_fixed_and_merged_again(office):
    async def go():
        await new_project(office, [verdict("done"), verdict("done")])
        await office.until("deploy")
        office.deployer.refuse = ["ports: not allowed; the deployer routes traffic itself"]
        await office.step()
        job = office.store.jobs()[0]
        upcoming = [plan.label(s) for s in office.store.steps(job.id) if s.state == "pending"]
        assert upcoming[:7] == ["fix", "push", "PR", "CI", "merge", "deploy", "mvp"]
        office.harness.script = [verdict("done")]
        await office.step()
        assert "ports: not allowed" in office.harness.calls[-1].prompt
        await office.until("mvp")
        assert len(office.deployer.deploys) == 2 and len(office.workspace.bodies) == 2

    run(go())


def test_waiting_for_ci_frees_the_worker_and_checks_again(office):
    async def go():
        await new_project(office, [verdict("done"), verdict("done")])
        await office.until("CI")
        office.workspace.ci_results = [CI("pending", "build: in_progress")]
        await office.step()
        job = office.store.jobs()[0]
        step = current(office, job.id)
        assert (job.state, plan.label(step), step.summary) == ("parked", "CI", "Waiting for CI on PR #1: build: in_progress")
        assert office.store.next_job() is None  # checked again in 20 seconds
        assert "waiting for CI, CI (6/24)" in office.bot.job_line(job)
        await office.step()
        assert office.store.step(step.id).state == "done"

    run(go())


def test_no_ci_at_all_is_something_to_fix(office, monkeypatch):
    monkeypatch.setattr(scheduler, "CI_START_TIMEOUT", timedelta(0))

    async def go():
        await new_project(office, [verdict("done"), verdict("done")])
        await office.until("CI")
        office.workspace.ci_results = [CI("none")]
        await office.step()
        fix = current(office, office.store.jobs()[0].id)
        assert fix.name == "fix" and ".github/workflows/build.yml" in fix.problem

    run(go())


def test_fixing_in_circles_asks_riggy_for_a_hint(office, monkeypatch):
    monkeypatch.setattr(scheduler, "MAX_FIXES", 1)

    async def go():
        room = await new_project(office, [verdict("done"), verdict("done"), verdict("done")])
        await office.until("deploy")
        office.deployer.refuse = ["bad compose", "still bad compose"]
        await office.step()
        await office.until("deploy")
        await office.step()
        job = office.store.jobs()[0]
        assert job.state == "waiting"
        assert office.chat.texts(room)[-1].startswith("I've tried to fix deploy 1 times and it still fails")
        await office.say(room, "use port 8080")
        office.harness.script = [verdict("done")]
        await office.step()
        assert "> use port 8080" in office.harness.calls[-1].prompt

    run(go())


def test_nothing_to_merge_still_ends_deployed(office):
    async def go():
        await new_project(office, [verdict("done"), verdict("done")])
        office.workspace.has_work = False
        await office.until("mvp")
        job = office.store.jobs()[0]
        states = [(plan.label(s), s.state) for s in office.store.steps(job.id)][3:8]
        assert states == [("push", "skipped"), ("PR", "skipped"), ("CI", "skipped"), ("merge", "skipped"), ("deploy", "done")]
        assert len(office.deployer.deploys) == 1

    run(go())


# --- project rooms ------------------------------------------------------------


def test_a_message_in_an_idle_project_room_is_a_change_request(office):
    async def go():
        room = await new_project(office, [])
        office.store.update_job(office.store.active_job("tree-cards").id, state="done")

        said = len(office.chat.texts(room))
        await office.say(room, "i dont like how the score works, count streaks instead")
        job = office.store.active_job("tree-cards")
        assert (job.kind, job.playbook) == ("change", "feature")
        assert len(office.chat.texts(room)) == said  # no "Got it": "Working on it" follows at once

        office.harness.script = [verdict("done", "Scores count streaks."), verdict("done"), verdict("done", "Scores now count streaks.")]
        await office.step()
        assert office.chat.texts(room)[-1] == "Working on it: i dont like how the score works, count streaks instead"
        await office.finish()
        assert office.labels(job.id) == ["set up", "change", "review", "docs", "push", "PR", "CI", "merge", "deploy", "done"]
        assert "✓ change: Scores count streaks.\nNext: review." in office.chat.texts(room)
        assert office.chat.texts(room)[-1] == f"Done: Scores now count streaks.\n{URL}"
        assert office.workspace.issues[-1] == ("tree-cards", "i dont like how the score works, count streaks instead")
        assert office.workspace.actions[-2:] == ["merge PR #1", "CI on " + office.workspace.main["tree-cards"][-2:] + ": passed"]

    run(go())


def test_a_change_in_reckless_mode_skips_the_review(office):
    async def go():
        await new_project(office, [])
        office.store.update_project("tree-cards", mode="reckless")
        office.store.update_job(office.store.active_job("tree-cards").id, state="done")
        await office.say(office.store.project("tree-cards").room_id, "bigger photos")
        await office.finish()
        job = office.store.jobs()[0]
        assert office.labels(job.id) == ["set up", "change", "docs", "push", "PR", "CI", "merge", "deploy", "done"]

    run(go())


def test_messages_while_a_job_runs_are_notes_for_its_next_step(office):
    async def go():
        room = await new_project(office, [])
        await office.until("spec")
        job = office.store.active_job("tree-cards")
        office.store.update_job(job.id, state="running")
        await office.say(room, "use oak and maple first")
        assert office.chat.texts(room)[-1] == "Noted. I'll work that in at the next step of the current job."
        assert office.store.active_job("tree-cards").id == job.id  # no second job
        office.store.update_job(job.id, state="queued")
        office.harness.script = [verdict("done")]
        await office.step()
        assert "> use oak and maple first" in office.harness.calls[-1].prompt

    run(go())


def test_blocked_resumes_the_same_session_with_the_answer(office):
    async def go():
        room = await new_project(office, [])
        await office.until("spec")
        office.harness.script = [RunResult("verdict", verdict("blocked", "Photos or drawings?"), session_id="s-1")]
        await office.step()
        job = office.store.active_job("tree-cards")
        assert (job.state, job.resume_session) == ("waiting", "s-1")
        assert current(office, job.id).summary == "Asked you: Photos or drawings?"
        assert office.chat.texts(room)[-1].startswith("Question: Photos or drawings?")

        await office.say(room, "photos")
        office.harness.script = [verdict("done")]
        await office.step()
        call = office.harness.calls[-1]
        assert call.resume == "s-1"
        assert call.prompt.startswith("Riggy has replied to your question:\n\n> photos")
        job = office.store.job(job.id)
        assert (plan.label(current(office, job.id)), job.resume_session) == ("scaffold", None)

    run(go())


# --- failures -----------------------------------------------------------------


def test_usage_limit_parks_the_job_and_says_so_once(office):
    async def go():
        await new_project(office, [])
        await office.until("spec")
        office.harness.script = [RunResult("limit", error="usage limit reached"), RunResult("limit")]
        status = office.store.get("status_room")
        await office.step()
        job = office.store.active_job("tree-cards")
        assert job.state == "parked" and job.attempts == 0
        assert office.store.next_job() is None
        soon = (datetime.now(UTC) + timedelta(minutes=31)).isoformat(timespec="seconds")
        assert office.store.next_job(at=soon).id == job.id
        await office.scheduler.step(office.store.job(job.id))
        notices = [t for t in office.chat.texts(status) if "usage limit" in t]
        assert len(notices) == 1

    run(go())


def test_repeated_errors_ask_riggy_for_help(office):
    async def go():
        room = await new_project(office, [])
        await office.until("spec")
        office.harness.script = [RunResult("error", error="npm exploded")] * 3
        job = office.store.active_job("tree-cards")
        for attempt in (1, 2):
            await office.scheduler.step(office.store.job(job.id))
            assert (office.store.job(job.id).state, office.store.job(job.id).attempts) == ("parked", attempt)
        await office.scheduler.step(office.store.job(job.id))
        assert office.store.job(job.id).state == "waiting"
        assert office.chat.texts(room)[-1].startswith("I'm stuck on spec after 3 tries: npm exploded")
        assert current(office, job.id).error == "npm exploded"

    run(go())


def test_running_out_of_turns_gets_a_wrapup_in_the_same_session(office):
    async def go():
        await new_project(office, [])
        await office.until("spec")
        office.harness.script = [
            RunResult("max_turns", session_id="s-9"),
            {"finished": False, "message": "The spec has no out-of-scope list yet."},
        ]
        await office.step()
        wrapup = office.harness.calls[-1]
        assert wrapup.resume == "s-9" and wrapup.max_turns == 15 and wrapup.schema == WRAPUP_SCHEMA
        assert wrapup.prompt.startswith("You've used up this step's turns.")
        job = office.store.active_job("tree-cards")
        step = current(office, job.id)
        assert (step.name, step.error) == ("spec", "ran out of turns: The spec has no out-of-scope list yet.")

        # The next attempt is told, and carries on from the commits.
        office.harness.script = [RunResult("max_turns", session_id="s-10"), {"finished": True, "message": "Spec written."}]
        await office.step()
        assert "didn't finish: ran out of turns: The spec has no out-of-scope list yet." in office.harness.calls[-2].prompt
        assert plan.label(current(office, job.id)) == "scaffold"
        assert [r["phase"] for r in office.store.runs(job.id)] == ["spec", "spec-wrapup", "spec", "spec-wrapup"]

    run(go())


def test_interrupted_jobs_carry_on_from_their_step(office):
    async def go():
        await new_project(office, [])
        await office.until("mvp")
        job = office.store.active_job("tree-cards")
        office.store.update_job(job.id, state="running")
        assert [j.id for j in office.store.requeue_interrupted()] == [job.id]
        assert plan.label(current(office, office.store.next_job().id)) == "mvp"

    run(go())


def test_jobs_from_before_steps_pick_up_where_they_were(office):
    async def go():
        await new_project(office, [])
        job = office.store.active_job("tree-cards")
        # Phase 3 of the old standard playbook was buildout; it had run before.
        office.store.update_job(job.id, phase=3, state="queued")
        office.store.start_run(job.id, "buildout", "opus/high", "log")
        steps = office.scheduler.ensure_steps(office.store.job(job.id))
        assert [s.state for s in steps[:15]] == ["done"] * 15 and steps[0].summary == plan.LEGACY_DONE
        assert plan.label(current(office, job.id)) == "buildout"

    run(go())


def test_kill_stops_the_running_step(office):
    started = asyncio.Event()

    class Hangs:
        calls = []

        async def run(self, **kwargs):
            started.set()
            await asyncio.sleep(3600)

    async def go():
        await new_project(office, [])
        office.scheduler.harness = Hangs()
        loop = asyncio.create_task(office.scheduler.run_forever())
        await asyncio.wait_for(started.wait(), 5)
        job = office.store.active_job("tree-cards")
        await office.say(office.store.project("tree-cards").room_id, "!kill")
        assert office.chat.sent[-1][1] == f"Killed job {job.id} (Tree Cards)."
        for _ in range(50):
            if office.scheduler.current is None:
                break
            await asyncio.sleep(0.01)
        assert office.store.job(job.id).state == "killed"
        assert office.store.runs(job.id)[-1]["outcome"] == "killed"
        loop.cancel()

    run(go())


class Worker:
    """An agent step run by `act(log_path, env)`, which writes what a worker would."""

    def __init__(self, act):
        self.act = act

    async def run(self, *, log_path, env, **kwargs):
        log_path.parent.mkdir(parents=True, exist_ok=True)
        return await self.act(log_path, env)


def log_event(log, event: dict) -> None:
    log.write(json.dumps(event) + "\n")
    log.flush()


def test_a_running_step_passes_on_progress_and_says_what_it_is_on(office, monkeypatch):
    monkeypatch.setattr(scheduler, "MONITOR_SECONDS", 0.01)
    monkeypatch.setattr(scheduler, "HEARTBEAT_SECONDS", 0.15)

    async def act(log_path, env):
        with gzip.open(log_path, "wt") as log:
            text = {"type": "text", "text": "Reading the brief."}
            log_event(log, {"type": "assistant", "message": {"content": [text]}})
            with open(env["OFFICE_PROGRESS"], "a") as notes:
                notes.write("The spec is drafted; checking it against the idea.\n")
            command = {"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "npm test"}}
            log_event(log, {"type": "assistant", "message": {"content": [command]}})
            await asyncio.sleep(0.4)
        return RunResult("verdict", verdict=verdict("done", "Spec written."))

    async def go():
        room = await new_project(office, [])
        await office.until("spec")
        office.scheduler.harness = Worker(act)
        await office.step()
        texts = office.chat.texts(room)
        assert "▸ The spec is drafted; checking it against the idea." in texts
        still = [t for t in texts if t.startswith("Still on")]
        assert still[0] == 'Still on spec (step 2/24), 0s in. Last said: "Reading the brief." Now running Bash npm test.'
        assert texts[-1].startswith("✓ spec: Spec written.")

    run(go())


def test_a_step_that_stops_doing_anything_is_restarted(office, monkeypatch):
    monkeypatch.setattr(scheduler, "MONITOR_SECONDS", 0.01)
    monkeypatch.setattr(scheduler, "STUCK_SECONDS", 0.2)

    async def act(log_path, env):
        with gzip.open(log_path, "wt") as log:
            log_event(log, {"type": "system", "subtype": "init"})
            await asyncio.sleep(3600)

    async def go():
        room = await new_project(office, [])
        await office.until("spec")
        office.scheduler.harness = Worker(act)
        job = office.store.active_job("tree-cards")
        await office.step()
        assert office.chat.texts(room)[-1] == "No sign of life in spec for 0s, so I'm restarting it."
        assert (office.store.job(job.id).state, office.store.job(job.id).attempts) == ("parked", 1)
        assert office.store.runs(job.id)[-1]["outcome"] == "error"

    run(go())


# --- commands -----------------------------------------------------------------


def test_commands(office):
    async def go():
        room = await new_project(office, [])
        inbox = office.inbox

        await office.say(inbox, "!queue")
        job = office.store.active_job("tree-cards")
        assert office.chat.sent[-1] == (inbox, f"#{job.id} Tree Cards: new project, starting shortly, set up (1/24)", True)

        await office.say(room, "!status")
        assert office.chat.texts(room)[-1].startswith("Tree Cards (tree-cards)\nMode standard, model opus, high effort\n#")

        await office.say(room, "!pause")
        assert office.store.project("tree-cards").paused
        assert office.store.next_job() is None
        await office.say(inbox, "!resume tree-cards")
        assert office.store.next_job().id == job.id

        await office.say(inbox, "!pause")
        assert office.scheduler.paused
        await office.say(inbox, "!queue")
        assert office.chat.texts(inbox)[-1].startswith("Work is paused.")
        await office.say(inbox, "!resume")
        assert not office.scheduler.paused

        await office.say(inbox, "!kill")
        assert office.chat.texts(inbox)[-1] == "Which job? !kill <number> (see !queue)."
        await office.say(inbox, "!nope")
        assert office.chat.texts(inbox)[-1].startswith("Unknown command !nope.")

    run(go())


def test_the_status_room_is_not_an_inbox(office):
    async def go():
        await office.bot.ensure_rooms()
        status = office.store.get("status_room")
        await office.say(status, "a todo app")
        assert office.store.projects() == []
        assert office.chat.texts(status)[-1] == "This room is for updates. Dictate ideas in the inbox."

    run(go())


def test_rooms_are_created_once(office):
    async def go():
        await office.bot.ensure_rooms()
        await office.bot.ensure_rooms()
        assert [r["alias"] for r in office.chat.rooms] == ["inbox", "status"]

    run(go())


# --- settings, names and slugs ------------------------------------------------


def test_dictated_settings_pick_the_playbook_model_and_effort(office):
    async def go():
        office.harness.script = [{**TREES, "name": "Leaf Peeper", "slug": "leaf-peeper", "mode": "rigorous", "model": "fable", "effort": "max"}]
        await office.bot.ensure_rooms()
        await office.say(office.inbox, "make a game called Leaf Peeper, rigorous, fable, max effort")
        project = office.store.project("leaf-peeper")
        assert (project.name, project.mode, project.model, project.effort) == ("Leaf Peeper", "rigorous", "fable", "max")
        assert office.store.active_job("leaf-peeper").playbook == "rigorous"
        await office.until("spec")
        office.harness.script = [verdict("done")]
        await office.step()
        call = office.harness.calls[-1]
        assert (call.model, call.effort) == ("fable", "max")
        assert "# Your step: spec" in call.prompt and "**rigorous mode**" in call.prompt
        assert len(office.store.steps(office.store.active_job("leaf-peeper").id)) == 26
        assert office.store.runs()[0]["model"] == "fable/max"

    run(go())


def test_unspecified_settings_use_the_defaults(tmp_path):
    office = Office(tmp_path, default_mode="reckless", default_model="sonnet", default_effort="xhigh")

    async def go():
        office.harness.script = [{**TREES, "mode": "unspecified", "model": "unspecified", "effort": "nonsense"}]
        await office.bot.ensure_rooms()
        await office.say(office.inbox, "trees")
        project = office.store.project("tree-cards")
        assert (project.mode, project.model, project.effort) == ("reckless", "sonnet", "xhigh")
        assert office.store.active_job("tree-cards").playbook == "reckless"

    run(go())


def test_settings_commands(office):
    async def go():
        room = await new_project(office, [])
        job = office.store.active_job("tree-cards")

        await office.say(room, "!mode rigorous")
        assert office.chat.texts(room)[-1] == (
            "Tree Cards: mode rigorous, model opus, high effort. The new-project job will follow the rigorous playbook."
        )
        assert office.store.job(job.id).playbook == "rigorous"

        await office.say(office.inbox, "!effort tree-cards max")
        assert office.store.project("tree-cards").effort == "max"
        await office.say(room, "!model sonnet")
        await office.say(room, "!model gpt")
        assert office.chat.texts(room)[-1] == "Usage: !model fable|opus|sonnet|haiku"
        await office.say(office.inbox, "!mode rigorous")
        assert office.chat.texts(office.inbox)[-1] == "Usage: !mode <project> reckless|standard|rigorous"

        await office.until("spec")
        office.harness.script = [verdict("done")]
        await office.step()
        assert (office.harness.calls[-1].model, office.harness.calls[-1].effort) == ("sonnet", "max")

        # Once the job has started, its steps stay.
        await office.say(room, "!mode reckless")
        assert office.store.job(job.id).playbook == "rigorous"
        assert "keeps its steps" in office.chat.texts(room)[-1]

        await office.say(room, "!status")
        assert "Mode reckless, model sonnet, max effort" in office.chat.texts(room)[-1]

    run(go())


def test_rename(office):
    async def go():
        room = await new_project(office, [])
        await office.say(room, "!rename Leaf Peeper")
        assert office.store.project("tree-cards").name == "Leaf Peeper"
        assert office.chat.names[room] == "Leaf Peeper"
        await office.say(office.inbox, "!rename tree-cards Canopy Quiz")
        assert office.store.project("tree-cards").name == "Canopy Quiz"

    run(go())


def test_slug_moves_the_project_and_queues_the_reference_fixes(office):
    async def go():
        room = await new_project(office, [])
        old_job = office.store.jobs()[0]
        office.store.update_job(old_job.id, state="done")

        await office.say(room, "!slug leaf-peeper")
        reply = office.chat.texts(room)[-1]
        assert reply.startswith("Moved Tree Cards to leaf-peeper: https://github.com/unlaunched/leaf-peeper")
        assert "https://github.com/unlaunched/tree-cards is still there" in reply
        assert office.store.project("tree-cards") is None
        project = office.store.project("leaf-peeper")
        assert project.room_id == room and project.name == "Tree Cards"
        assert office.store.job(old_job.id).slug == "leaf-peeper"  # history moves with it
        assert office.deployer.stopped == ["tree-cards"]
        assert (room, "leaf-peeper") in office.chat.aliases
        assert office.chat.topics[room] == "https://leaf-peeper.unlaunched.dev/ · https://github.com/unlaunched/leaf-peeper"
        assert office.workspace.path("leaf-peeper").is_dir() and not office.workspace.path("tree-cards").exists()

        fix = office.store.active_job("leaf-peeper")
        assert (fix.kind, fix.playbook) == ("change", "feature")
        assert "moved from `tree-cards` to `leaf-peeper`" in fix.request

    run(go())


async def a_run(office: Office) -> None:
    """Work the new project's job as far as its first agent run."""
    await office.until("spec")
    office.harness.script = [verdict("done")]
    await office.step()


def test_delete_asks_first_then_takes_everything_but_the_repo(office):
    async def go():
        room = await new_project(office, [])
        await a_run(office)  # a log and a checkout
        job = office.store.jobs()[0]
        office.store.add_note(job.id, "use oak first")
        logs = office.settings.job_logs(job.id)
        assert logs.is_dir() and office.workspace.path("tree-cards").is_dir()

        await office.say(room, "!delete")
        assert office.chat.texts(room)[-1] == (
            "This deletes Tree Cards for good: the site and its data, the code checkout, every job and its logs, "
            "and this room. The repo stays on GitHub for you to delete. To go ahead, say !delete tree-cards"
        )
        assert office.store.project("tree-cards") is not None and office.deployer.deleted == []

        await office.say(room, "!delete tree-cards")
        assert office.deployer.deleted == ["tree-cards"]
        assert not office.workspace.path("tree-cards").exists() and not logs.exists()
        assert office.store.project("tree-cards") is None
        assert office.store.jobs() == [] and office.store.runs() == [] and office.store.pending_notes(job.id) == []
        assert office.store.steps(job.id) == []
        assert office.chat.closed == [(room, "Tree Cards was deleted.")]
        # The room is gone, so the answer comes in the inbox.
        assert office.chat.texts(office.inbox)[-1] == (
            "Deleted Tree Cards: its site and data, checkout, jobs, logs and room are gone. Delete the repo yourself "
            "on GitHub (Settings, at the bottom): https://github.com/unlaunched/tree-cards/settings"
        )
        assert office.store.next_job() is None

    run(go())


def test_delete_from_the_inbox_needs_the_slug(office):
    async def go():
        await new_project(office, [])
        await office.say(office.inbox, "!delete")
        assert office.chat.texts(office.inbox)[-1] == "Usage: !delete <slug>. It deletes the project for good."
        await office.say(office.inbox, "!delete trees")
        assert office.chat.texts(office.inbox)[-1] == "No project called trees."
        assert office.store.project("tree-cards") is not None
        await office.say(office.inbox, "!delete tree-cards")
        assert office.store.project("tree-cards") is None
        assert office.chat.texts(office.inbox)[-1].startswith("Deleted Tree Cards:")

    run(go())


def test_delete_stops_a_running_step_first(office):
    started = asyncio.Event()

    class Hangs:
        async def run(self, **kwargs):
            started.set()
            await asyncio.sleep(3600)

    async def go():
        room = await new_project(office, [])
        office.scheduler.harness = Hangs()
        loop = asyncio.create_task(office.scheduler.run_forever())
        await asyncio.wait_for(started.wait(), 5)
        task = office.scheduler.current[1]
        await office.say(room, "!delete tree-cards")
        assert task.done()  # stopped before anything was deleted
        assert office.store.project("tree-cards") is None and office.store.runs() == []
        assert office.deployer.deleted == ["tree-cards"]
        loop.cancel()

    run(go())


def test_a_failed_delete_keeps_the_project_and_can_be_retried(office):
    async def go():
        room = await new_project(office, [])
        office.deployer.fail = "deleting is off: DEPLOYER_ADMIN_SECRET is not set"
        await office.say(room, "!delete tree-cards")
        assert office.chat.texts(room)[-1] == (
            "Couldn't delete all of Tree Cards: deleting is off: DEPLOYER_ADMIN_SECRET is not set. "
            "Its jobs are stopped; say !delete tree-cards to try again."
        )
        assert office.store.project("tree-cards") is not None
        assert office.store.jobs()[0].state == "killed" and office.chat.closed == []

        office.deployer.fail = None
        await office.say(room, "!delete tree-cards")
        assert office.store.project("tree-cards") is None

    run(go())


def test_nuke_says_what_will_go_then_deletes_it_all(office):
    async def go():
        room = await new_project(office, [])
        await a_run(office)
        office.deployer.deployed = ["hello", "tree-cards", "zz-old"]
        lessons = office.settings.state_dir / "lessons.md"
        lessons.write_text("- a lesson\n")
        office.workspace.path("zz-old").mkdir()

        assert await nuke.plan(office.settings, office.store, office.deployer) == (
            "This deletes, for good:\n"
            "- 1 project(s), each with its site and data, checkout, jobs, run logs and Matrix room:\n"
            "    tree-cards (Tree Cards)\n"
            "- 2 more site(s) the deployer has, with their data: hello, zz-old\n"
            f"- everything in {office.settings.workspace_dir}, office's run logs, triage log, lessons.md and proposals\n"
            "Kept: the stack, the Matrix accounts, the inbox and status rooms, and .env.\n"
            "Not touched: the GitHub repos. Delete them at https://github.com/orgs/unlaunched/repositories"
        )
        assert office.store.project("tree-cards") and office.deployer.deleted == []  # planning deletes nothing

        said = []
        problems = await nuke.wipe(office.settings, office.store, office.chat, office.workspace, office.deployer, said.append)
        assert problems == []
        assert said == ["deleted project tree-cards", "deleted site hello", "deleted site zz-old"]
        assert office.deployer.deleted == ["tree-cards", "hello", "zz-old"]
        assert office.store.projects() == [] and office.store.jobs() == [] and office.store.runs() == []
        assert list(office.settings.workspace_dir.iterdir()) == []
        assert not lessons.exists() and not (office.settings.state_dir / "jobs").exists()
        assert office.chat.closed == [(room, "Tree Cards was deleted.")]
        # The bot itself carries on as before.
        assert office.store.get("inbox_room") == office.inbox and (office.settings.state_dir / "office.db").exists()
        assert await nuke.plan(office.settings, office.store, office.deployer) == nuke.NOTHING

    run(go())


def test_nuke_reports_what_it_could_not_delete(office):
    async def go():
        await new_project(office, [])
        office.deployer.deployed = ["hello"]
        office.deployer.fail = "deleting is off: DEPLOYER_ADMIN_SECRET is not set"
        problems = await nuke.wipe(office.settings, office.store, office.chat, office.workspace, office.deployer, print)
        assert problems == [
            "tree-cards: deleting is off: DEPLOYER_ADMIN_SECRET is not set",
            "hello: deleting is off: DEPLOYER_ADMIN_SECRET is not set",
        ]
        assert office.store.project("tree-cards") is not None  # a second run retries it

    run(go())


def test_slug_refuses_bad_or_taken_slugs_and_running_jobs(office):
    async def go():
        room = await new_project(office, [])
        office.workspace.repos.add("taken")
        await office.say(room, "!slug Office")
        assert "won't work as an address" in office.chat.texts(room)[-1]
        await office.say(room, "!slug taken")
        assert office.chat.texts(room)[-1] == "taken is taken (by a project or an existing repo)."
        office.store.update_job(office.store.active_job("tree-cards").id, state="running")
        await office.say(room, "!slug oaks")
        assert office.chat.texts(room)[-1].startswith("A step is running.")
        assert office.store.project("tree-cards") is not None

    run(go())
