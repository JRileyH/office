# office

A self-hosted system that turns ideas dictated from a phone into deployed web
projects at `<slug>.unlaunched.dev`, built by background Claude Code workers.

See `BRIEF.md` for the design and `AGENTS.md` for how to work on the repo.

## Using it

Everything happens in Element X, signed in as `@riggy:matrix.unlaunched.dev`.

- **office inbox:** dictate an idea ("a flashcard game for northeastern US
  trees"). office names it, picks a slug, creates `unlaunched/<slug>`, and
  invites you to a new room for that project.
- **How a job runs:** as a list of steps, written into office's database from
  the mode's playbook when it starts (`playbooks/`). Agent steps (spec, mvp,
  review…) are Claude Code runs that each do one piece of work and commit it.
  Everything else is office's own code, the same every time: pushing, opening a
  PR, waiting for CI, merging, deploying, the MVP check-in, and telling you it's
  done. Every playbook ends with deploy then alert, so "Done" means it's live.
  When CI fails or the deployer refuses a commit, office adds a fix step and
  tries again; after four fixes it asks you for a hint.
- **The project's room:** progress shows up here as quiet notices; questions,
  the MVP check-in and "Done" arrive as messages that notify your phone.
  - Each agent step posts "✓ step: what was done" and what's next; so do PRs
    and deploys. Failures post "✗ …. Fixing it."
  - During an agent step, the worker posts a "▸" note at each milestone. After
    20 minutes without news, office says what the worker is on ("Still on mvp
    (step 9/24), 45m in…"). A run that logs nothing for 30 minutes is stopped
    and retried.
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
    take effect from the next step.

  Defaults are `OFFICE_DEFAULT_MODE`, `OFFICE_MODEL` and `OFFICE_EFFORT` in `.env`.
- **Commands,** in any room (in a project room they apply to that project;
  elsewhere put its slug first): `!help`, `!queue`, `!status`, `!mode`,
  `!model`, `!effort`, `!rename <name>`, `!slug <new-slug>` (moves the repo and
  address; the old repo stays for you to delete), `!pause`, `!resume`, `!kill`.
- **Deleting a project:** `!delete` in its room says what will go, then
  `!delete <slug>` takes it all: the site and its data, the checkout, the job
  history and logs, and the room. The repo stays on GitHub for you to delete
  (office's token can't). To delete every project at once, run `bin/nuke.sh`
  on the box ("Starting over" in `docs/runbook.md`).
- **Status page:** https://office.unlaunched.dev, made for a phone. One lane per
  project: its job's steps as a strip of dots, what the running step is doing
  (what the worker says, its ▸ notes and a live tail), what's next, and what's
  done with links to PRs, commits, CI runs and the site. It refreshes itself.
  Anyone can look; sign in with `OFFICE_WEB_PASSWORD` (default `supi`; change it
  in `.env`) for 24 hours of controls: skip optional steps (reviews, docs, the
  MVP check-in) or undo that, run a job next or last, pause a project or all
  work, stop a job, change model and effort, and send a job a note or answer.
  For everything an agent step does, run `bin/watch.sh` on the box ("Checking on
  a job" in `docs/runbook.md`).

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
bot without spending Claude usage: every agent step then answers "done", and
GitHub and deploys are pretended.

## VPS

See `docs/runbook.md`.
