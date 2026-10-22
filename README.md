# office

A self-hosted system that turns ideas dictated from a phone into deployed web
projects at `<slug>.unlaunched.dev`, built by background Claude Code workers.

See `BRIEF.md` for the design and `AGENTS.md` for how to work on the repo.

## Using it

Everything happens in Element X, signed in as `@riggy:matrix.unlaunched.dev`.

- **office inbox:** dictate an idea ("a flashcard game for northeastern US
  trees"). office names it, picks a slug, creates `unlaunched/<slug>`, and
  invites you to a new room for that project.
- **The project's room:** progress shows up here as quiet notices; questions,
  the MVP check-in and "Done" arrive as messages that notify your phone.
  - When office is waiting on you, your reply is the answer ("looks good",
    "make the photos bigger").
  - While it's working, what you say is passed to the next step of the job.
  - Otherwise, anything you say is a change request ("I don't like how the
    score works, count streaks instead"): it says when it starts, and again when
    it's live or if it needs an answer.
- **office status:** usage-limit notices and finished jobs.
- **Commands,** in any room (in a project room they apply to that project):
  `!help`, `!queue`, `!status [project]`, `!pause [project]`,
  `!resume [project]`, `!kill [job]`.
- **Status page:** https://office.unlaunched.dev lists projects, jobs and runs.

## Local mode

Needs Docker (OrbStack) and [just](https://github.com/casey/just).

```sh
just bootstrap local     # writes local settings into .env; add CLAUDE_CODE_OAUTH_TOKEN by hand
just up
just hello-local         # deploys examples/hello -> http://hello.localhost:8800
just claude-check        # Claude Code headless in the office image
just test                # both test suites
```

Status page: http://office.localhost:8800. Matrix: http://matrix.localhost:8800
(accounts: `tuwunel/README.md`). Set `HARNESS=fake` in `.env` to exercise the
bot without spending Claude usage: every phase then answers "continue".

## VPS

See `docs/runbook.md`.
