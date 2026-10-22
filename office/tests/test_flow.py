"""End-to-end flows through the bot and scheduler, with Matrix, GitHub and Claude faked."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from conftest import Office

from office.harness import RunResult

TREES = {"name": "Tree Cards", "slug": "tree-cards", "summary": "A flashcard game for northeastern US trees."}


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
        assert (job.kind, job.playbook, job.state) == ("new", "standard", "queued")
        assert job.request == "a flashcard game for identifying northeastern us trees"
        [reply] = office.chat.texts(office.inbox)[1:]  # after the welcome
        assert reply.startswith("New project: Tree Cards (tree-cards). Starting now.")
        assert f"https://matrix.to/#/{room}" in reply
        [intro] = office.chat.texts(room)
        assert "A flashcard game for northeastern US trees." in intro
        assert "https://tree-cards.unlaunched.dev/" in intro
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


def test_standard_playbook_walks_to_the_mvp_checkin_and_on_to_done(office):
    async def go():
        room = await new_project(
            office,
            [
                verdict("continue", "MVP: pick the tree from four photos."),
                verdict("continue", "Live with a placeholder page.", "https://tree-cards.unlaunched.dev/"),
                verdict("checkin", "Try the quiz: ten trees per round.", "https://tree-cards.unlaunched.dev/"),
            ],
        )
        for _ in range(3):
            await office.step()
        job = office.store.active_job("tree-cards")
        assert (job.state, job.phase) == ("waiting", 3)  # buildout comes next, after Riggy replies
        assert job.issue == 1 and office.workspace.issues == [("tree-cards", "Tree Cards")]
        checkin = office.chat.texts(room)[-1]
        assert checkin.startswith("Try the quiz: ten trees per round.\nhttps://tree-cards.unlaunched.dev/")
        assert "Reply here" in checkin
        # Progress is posted as notices (no push); the check-in as a message (push).
        assert [n for r, _, n in office.chat.sent if r == room][-1] is False

        # Nothing runs while the job waits on Riggy.
        assert office.store.next_job() is None

        await office.say(room, "looks good but make the photos bigger")
        assert office.chat.texts(room)[-1] == "Thanks, picking it back up. Starting now."
        office.harness.script = [verdict("continue"), verdict("continue"), verdict("done", "Ready to play.")]
        await office.step()
        buildout = office.harness.calls[-1]
        assert "# This phase: buildout (4 of 6)" in buildout.prompt
        assert "> looks good but make the photos bigger" in buildout.prompt
        assert "OFFICE_MATRIX_PASSWORD" not in buildout.env
        assert buildout.env["OFFICE_SLUG"] == "tree-cards"
        # The reply was delivered once; the next phase doesn't see it again.
        await office.step()
        assert "make the photos bigger" not in office.harness.calls[-1].prompt
        await office.step()

        job = office.store.job(job.id)
        assert job.state == "done"
        assert office.chat.texts(room)[-1] == "Done: Ready to play.\nhttps://tree-cards.unlaunched.dev/"
        assert [r["phase"] for r in office.store.runs(job.id)] == ["spec", "scaffold", "mvp", "buildout", "review", "ship"]
        assert "Finished job" in office.chat.texts(office.store.get("status_room"))[-1]

    run(go())


def test_phase_prompts_are_fully_filled_in(office):
    async def go():
        await new_project(office, [verdict("continue")])
        await office.step()
        prompt = office.harness.calls[-1].prompt
        assert "$" not in prompt.replace("$(", "")
        assert "# This phase: spec (1 of 6)" in prompt
        assert "> a flashcard game for identifying northeastern us trees" in prompt
        assert "https://github.com/unlaunched/tree-cards/issues/1" in prompt
        assert "the last phase? no)" in prompt

    run(go())


# --- project rooms ------------------------------------------------------------


def test_a_message_in_an_idle_project_room_is_a_change_request(office):
    async def go():
        room = await new_project(office, [verdict("done", "Live.")])
        office.store.update_job(office.store.active_job("tree-cards").id, state="done")

        await office.say(room, "i dont like how the score works, count streaks instead")
        job = office.store.active_job("tree-cards")
        assert (job.kind, job.playbook) == ("change", "feature")
        assert office.chat.texts(room)[-1] == "Got it. Starting now."

        office.harness.script = [verdict("continue", "PR up."), verdict("continue"), verdict("done", "Scores now count streaks.")]
        await office.step()
        texts = office.chat.texts(room)
        assert "Working on it: i dont like how the score works, count streaks instead" in texts
        assert "# This phase: change (1 of 3)" in office.harness.calls[-1].prompt
        await office.step()
        await office.step()
        assert office.chat.texts(room)[-1] == "Done: Scores now count streaks.\nhttps://tree-cards.unlaunched.dev/"
        assert office.workspace.issues[-1] == ("tree-cards", "i dont like how the score works, count streaks instead")

    run(go())


def test_messages_while_a_job_runs_are_notes_for_its_next_phase(office):
    async def go():
        room = await new_project(office, [])
        job = office.store.active_job("tree-cards")
        office.store.update_job(job.id, state="running")
        await office.say(room, "use oak and maple first")
        assert office.chat.texts(room)[-1] == "Noted. I'll work that in at the next step of the current job."
        assert office.store.active_job("tree-cards").id == job.id  # no second job
        office.store.update_job(job.id, state="queued")
        office.harness.script = [verdict("continue")]
        await office.step()
        assert "> use oak and maple first" in office.harness.calls[-1].prompt

    run(go())


def test_blocked_resumes_the_same_session_with_the_answer(office):
    async def go():
        room = await new_project(office, [RunResult("verdict", verdict("blocked", "Photos or drawings?"), session_id="s-1")])
        await office.step()
        job = office.store.active_job("tree-cards")
        assert (job.state, job.phase, job.resume_session) == ("waiting", 0, "s-1")
        assert office.chat.texts(room)[-1].startswith("Question: Photos or drawings?")

        await office.say(room, "photos")
        office.harness.script = [verdict("continue")]
        await office.step()
        call = office.harness.calls[-1]
        assert call.resume == "s-1"
        assert call.prompt.startswith("Riggy has replied to your question:\n\n> photos")
        job = office.store.job(job.id)
        assert (job.phase, job.resume_session) == (1, None)

    run(go())


# --- failures -----------------------------------------------------------------


def test_usage_limit_parks_the_job_and_says_so_once(office):
    async def go():
        await new_project(office, [RunResult("limit", error="usage limit reached"), RunResult("limit")])
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
        room = await new_project(office, [RunResult("error", error="npm exploded")] * 3)
        job = office.store.active_job("tree-cards")
        for attempt in (1, 2):
            await office.scheduler.step(office.store.job(job.id))
            assert (office.store.job(job.id).state, office.store.job(job.id).attempts) == ("parked", attempt)
        await office.scheduler.step(office.store.job(job.id))
        assert office.store.job(job.id).state == "waiting"
        assert office.chat.texts(room)[-1].startswith("I'm stuck in spec after 3 tries: npm exploded")

    run(go())


def test_running_out_of_turns_gets_a_wrapup_in_the_same_session(office):
    async def go():
        await new_project(office, [RunResult("max_turns", session_id="s-9"), verdict("continue")])
        await office.step()
        wrapup = office.harness.calls[-1]
        assert wrapup.resume == "s-9" and wrapup.max_turns == 15
        assert wrapup.prompt.startswith("You've used up this phase's turns.")
        job = office.store.active_job("tree-cards")
        assert job.phase == 1
        assert [r["phase"] for r in office.store.runs(job.id)] == ["spec", "spec-wrapup"]

    run(go())


def test_interrupted_jobs_rerun_their_phase(office):
    async def go():
        await new_project(office, [])
        job = office.store.active_job("tree-cards")
        office.store.update_job(job.id, state="running", phase=2)
        assert [j.id for j in office.store.requeue_interrupted()] == [job.id]
        assert office.store.next_job().phase == 2

    run(go())


def test_kill_stops_the_running_phase(office):
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


# --- commands -----------------------------------------------------------------


def test_commands(office):
    async def go():
        room = await new_project(office, [])
        inbox = office.inbox

        await office.say(inbox, "!queue")
        job = office.store.active_job("tree-cards")
        assert office.chat.sent[-1] == (inbox, f"#{job.id} Tree Cards: new project, queued, spec", True)

        await office.say(room, "!status")
        assert office.chat.texts(room)[-1].startswith("Tree Cards (tree-cards)\n#")

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
