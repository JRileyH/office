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
- **Settings,** per project, shown when it's created (work starts a minute later,
  so there's time to change them). Say them while dictating ("…called Leaf
  Peeper, rigorous, opus, max effort") or change them later in the room:
  - `!mode reckless|standard|rigorous`: one quick pass; spec, MVP check-in,
    review; or a reviewed spec, thorough tests and two reviews.
  - `!model fable|opus|sonnet|haiku` and `!effort low|medium|high|xhigh|max`:
    take effect from the next phase.

  Defaults are `OFFICE_DEFAULT_MODE`, `OFFICE_MODEL` and `OFFICE_EFFORT` in `.env`.
- **Commands,** in any room (in a project room they apply to that project;
  elsewhere put its slug first): `!help`, `!queue`, `!status`, `!mode`,
  `!model`, `!effort`, `!rename <name>`, `!slug <new-slug>` (moves the repo and
  address; the old repo stays for you to delete), `!pause`, `!resume`, `!kill`.
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
