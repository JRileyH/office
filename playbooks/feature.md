---
phases:
  - {name: change, max_turns: 400}
  - {name: review, max_turns: 150}
  - {name: ship, max_turns: 150}
---
A change to an existing, live project: a feature, a fix, or "I don't like how X
works, make it do Y". Riggy asked for it in the project's room; his words are the
request. Read `AGENTS.md`, `README.md` and `docs/` before touching code: they say
how this project is built, tested and deployed.

Don't check in unless the request is genuinely ambiguous in a way that changes what
gets built, or would destroy something irreversible (deleting data). Otherwise
decide, record the decision in `docs/decisions.md`, and carry on.

The project's mode sets the bar: in reckless mode keep tests light; in standard,
test the paths you touch; in rigorous, test thoroughly (including browser tests
for changed flows) and check the fixes again after review.

## change

1. Work out what Riggy wants. If it's a bug, reproduce it first.
2. For anything bigger than a small fix, write a short mini-spec in
   `docs/specs/` (what changes, what doesn't, how you'll know it works).
3. Branch, make the change, and add or update tests for the paths it touches.
   Check the result in a real browser (skills/browser.md) at phone size.
4. Open a PR that closes the job's issue and wait for checks to pass.

Verdict: `continue`, linking the PR. Use `blocked` with a specific question only
if you can't proceed on a reasonable assumption.

## review

You are a fresh pair of eyes. Review the open PR with skills/review.md against
Riggy's request, the mini-spec if there is one, and `AGENTS.md`. Fix what you
find on the PR's branch, push, and wait for checks to pass.

Verdict: `continue`, linking the PR.

## ship

1. Merge the PR once checks pass, deploy the merge commit (skills/deploy.md),
   and check on the live site that the change does what Riggy asked.
2. Update `README.md` and `AGENTS.md` if they're now wrong. Add anything a
   future worker should know to `docs/lessons.md`, and lessons for every
   project to the office lessons file named in this prompt. Commit docs
   straight to `main`.

Verdict: `done`, linking the live URL. The message says what changed in Riggy's
terms ("the quiz now shows the answer after each question"), not the code's.
