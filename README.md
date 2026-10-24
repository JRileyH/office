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
  - `!model fable|opus|sonnet|haiku|default` and
    `!effort low|medium|high|xhigh|max|default`: for all the project's agent
    steps, from the next one; `default` goes back to each step's default.

  The model and effort are chosen per step, the most specific choice winning:
  one step's (set in its sheet on the status page), then the project's, then
  the default for that kind of step (set on the status page's Insights), then
  `OFFICE_MODEL` and `OFFICE_EFFORT` in `.env`. The default mode is
  `OFFICE_DEFAULT_MODE`.
- **Commands,** in any room (in a project room they apply to that project;
  elsewhere put its slug first): `!help`, `!queue`, `!status`, `!mode`,
  `!model`, `!effort`, `!rename <name>`, `!slug <new-slug>` (moves the repo and
  address; the old repo stays for you to delete), `!pause`, `!resume`, `!kill`.
- **Deleting a project:** `!delete` in its room says what will go, then
  `!delete <slug>` takes it all: the site and its data, the checkout, the job
  history and logs, and the room. The repo stays on GitHub for you to delete
  (office's token can't). To delete every project at once, run `bin/nuke.sh`
  on the box ("Starting over" in `docs/runbook.md`).
- **Projects page:** https://projects.unlaunched.dev, public: a card for each
  live project with a screenshot, a tagline, a description, tags ("Game",
  "Flashcards") and a button that says what you'll do there (Play, Open, Use,
  Visit). Cards are written by a quick haiku call from the project's README and
  brief, with a screenshot of the live site, when a project first goes live and
  again at the end of every job. Projects live before this get theirs when
  office starts.
- **Status page:** https://office.unlaunched.dev, made for a phone. At the top,
  what needs you and what's coming up when, in words, across projects. Then a
  lane per project: its job drawn as a line through its steps, like a git graph
  (work on the branch above, merges dropping to main and deploys below), solid
  as far as it's got and dashed after, with how long each step took or when it
  should start. Below it, where the job is and when it goes live, and what the
  running step is doing (what the worker says, its ▸ notes and a live tail).
  Tap any step for its sheet: what it did, with its runs, usage and transcript;
  what it's doing now; or, before it starts, when it should and what it will run
  on. It refreshes itself. **Insights** shows where the time and Claude usage go
  by kind of step, project and model, the slowest and costliest steps, and the
  default model and effort for each kind of step. Anyone can look; sign in with
  `OFFICE_WEB_PASSWORD` (default `supi`; change it in `.env`) for 24 hours of
  controls: skip optional steps (reviews, docs, the MVP check-in) or undo that,
  give one upcoming step its own model, effort or context, run a job next or
  last, pause a project or all work, stop a job, set a project's or a kind of
  step's model and effort, and send a job a note or answer.
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
