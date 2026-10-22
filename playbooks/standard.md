---
phases:
  - {name: spec, max_turns: 80}
  - {name: scaffold, max_turns: 200}
  - {name: mvp, max_turns: 400}
  - {name: buildout, max_turns: 400}
  - {name: review, max_turns: 150}
  - {name: ship, max_turns: 150}
---
Standard mode, new project: spec it, prove hosting with a first deploy, build to an
MVP and check in with Riggy, build it out, review it with fresh eyes, ship it.

Riggy is the product manager, not a reviewer of details. Decide colors, names,
libraries, layouts and similar yourself and record the choice in
`docs/decisions.md` (one line each: decision, why).

## spec

Turn the request into a spec. The repo is new and empty (or nearly).

1. Write `docs/brief.md`: Riggy's request word for word, then the brief (name,
   one-paragraph summary, who it's for).
2. Write `docs/specs/mvp.md` with skills/spec-writing.md: the MVP, what comes
   after it, what's out of scope, and the bar for "good" (see the quality bar in
   this prompt).
3. Choose the stack and record it in `docs/decisions.md` (skills/new-repo.md).
4. Commit and push to `main`.

Verdict: `continue`, with a one-sentence summary of the MVP. Use `blocked` only
if the request could mean two very different products.

## scaffold

Prove hosting before building features: get a minimal version of the project live.

1. Follow skills/new-repo.md: app skeleton, `Dockerfile`, `deploy/compose.yml`,
   the build workflow, `AGENTS.md`, `CLAUDE.md`, `README.md`. A styled page with
   the project's name and a line about it is enough for the app itself.
2. Commit and push straight to `main` (this is the one phase that skips the PR).
3. Deploy with skills/deploy.md and check the live URL with skills/browser.md.

Verdict: `continue`, linking the live URL.

## mvp

Build the MVP from `docs/specs/mvp.md`, then show it to Riggy.

1. Branch, build the MVP, and add tests for its core paths.
2. Check it in a real browser as you go (skills/browser.md), at phone and
   desktop sizes. Look at the screenshots. Fix what looks wrong or feels clumsy
   before moving on: this is what Riggy sees first.
3. Open a PR that closes the job's issue, wait for checks, merge it, and deploy
   the merge commit (skills/deploy.md). Check the live site in the browser.

Verdict: `checkin`, linking the live URL. The message says what to try and,
briefly, what's deliberately not there yet. This is the one planned check-in.

## buildout

Riggy has seen the MVP. His reply is under "Riggy's feedback" in this prompt.

1. If he asked for changes, those come first, ahead of the rest of the spec.
   "Looks good" means carry on with the spec as written. If he said to stop or
   shelve it, finish nothing new: return `done` saying it's shelved.
2. Build out the rest of the spec's "after the MVP" list that's worth doing,
   with tests for core paths. Update the spec if his feedback changed it.
3. Polish: go through every screen in the browser at phone size and fix rough
   edges (spacing, empty states, loading, feedback on every action).
4. Open a PR (closing the issue) and wait for checks. Don't merge: review is next.

Verdict: `continue`, linking the PR.

## review

You are a fresh pair of eyes. Review the open PR with skills/review.md, against
`docs/specs/mvp.md` and `AGENTS.md` only: don't trust earlier reasoning, read the
diff and use the app.

Fix what you find on the PR's branch, push, and wait for checks to pass.

Verdict: `continue`, linking the PR, with the most important thing you fixed.

## ship

1. Merge the PR once checks pass, then deploy the merge commit
   (skills/deploy.md) and check the live site end to end in the browser.
2. Docs: `README.md` leads with the live URL and what the project does;
   `AGENTS.md` is accurate (stack, run, test, deploy, gotchas).
3. Retro: add anything a future worker on this project should know to
   `docs/lessons.md`. Add lessons that apply to every project (a deploy
   gotcha, a CI trick) to the office lessons file named in this prompt.
4. Commit docs straight to `main` and deploy again if anything served changed.

Verdict: `done`, linking the live URL, with one or two sentences on what Riggy
can do there now.
