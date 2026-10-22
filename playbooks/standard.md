---
phases:
  - {name: spec, model: sonnet, max_turns: 60}
  - {name: scaffold, model: sonnet, max_turns: 150}
  - {name: mvp, model: sonnet, max_turns: 250}
  - {name: buildout, model: sonnet, max_turns: 250}
  - {name: review, model: sonnet, max_turns: 100}
  - {name: ship, model: sonnet, max_turns: 120}
---
Standard mode, new project: spec it, prove hosting with a first deploy, build to an
MVP and check in with Riggy, build it out, review it with fresh eyes, ship it.

Riggy is the product manager, not a reviewer of details. Decide colors, names,
libraries, layouts and similar yourself and record the choice in
`docs/decisions.md` (one line each: decision, why). Projects are personal tools
with public, disposable data and no customers: favor the simplest thing that
works well, and a small stack you can finish.

## spec

Turn the request into a short spec. The repo is new and empty (or nearly).

1. Write `docs/brief.md`: Riggy's request word for word, then the brief (name,
   one-paragraph summary, who it's for).
2. Write `docs/specs/mvp.md` with skills/spec-writing.md: the MVP (the smallest
   version worth looking at), what comes after it, and what's out of scope.
3. Choose the stack and record it in `docs/decisions.md`. Default to a static
   site or a single small container. Add a database only if the idea needs data
   to survive restarts, and then prefer SQLite in a named volume.
4. Commit and push to `main`.

Verdict: `continue`, with a one-sentence summary of the MVP. Use `blocked` only
if the request could mean two very different products.

## scaffold

Prove hosting before building features: get a minimal version of the project live.

1. Follow skills/new-repo.md: app skeleton, `Dockerfile`, `deploy/compose.yml`,
   the build workflow, `AGENTS.md`, `CLAUDE.md`, `README.md`. A page with the
   project's name and a line about it is enough for the app itself.
2. Commit and push straight to `main` (this is the one phase that skips the PR).
3. Deploy with skills/deploy.md and check the live URL serves the page.

Verdict: `continue`, linking the live URL.

## mvp

Build the MVP from `docs/specs/mvp.md`, then show it to Riggy.

1. Branch, build the MVP, and add tests for its core paths (skills/new-repo.md
   says how tests run in CI).
2. Open a PR that closes the job's issue, wait for checks, merge it, and deploy
   the merge commit (skills/deploy.md).
3. Check the live site yourself: load it, click through the main flow with curl
   or a script, and fix what's broken before checking in.

Verdict: `checkin`, linking the live URL. The message says what to try and,
briefly, what's deliberately not there yet. This is the one planned check-in.

## buildout

Riggy has seen the MVP. His reply is under "Riggy's feedback" in this prompt.

1. If he asked for changes, those come first, ahead of the rest of the spec.
   "Looks good" means carry on with the spec as written. If he said to stop or
   shelve it, finish nothing new: return `done` saying it's shelved.
2. Build out the rest of `docs/specs/mvp.md`'s "after the MVP" list that's worth
   doing, with tests for core paths. Update the spec if his feedback changed it.
3. Open a PR (closing the issue) and wait for checks. Don't merge: review is next.

Verdict: `continue`, linking the PR.

## review

You are a fresh pair of eyes. Review the open PR with skills/review.md, against
`docs/specs/mvp.md` and `AGENTS.md` only: don't trust earlier reasoning, read the
diff.

Fix what you find on the PR's branch, push, and wait for checks to pass.

Verdict: `continue`, linking the PR, with the most important thing you fixed.

## ship

1. Merge the PR once checks pass, then deploy the merge commit
   (skills/deploy.md) and check the live site works end to end.
2. Docs: `README.md` leads with the live URL and what the project does;
   `AGENTS.md` is accurate (stack, run, test, deploy, gotchas).
3. Retro: add anything a future worker on this project should know to
   `docs/lessons.md`. Add lessons that apply to every project (a deploy
   gotcha, a CI trick) to the office lessons file named in this prompt.
4. Commit docs straight to `main` and deploy again if anything served changed.

Verdict: `done`, linking the live URL, with one or two sentences on what Riggy
can do there now.
