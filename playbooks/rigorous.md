---
phases:
  - {name: spec, max_turns: 120}
  - {name: spec-review, max_turns: 80}
  - {name: scaffold, max_turns: 200}
  - {name: mvp, max_turns: 500}
  - {name: buildout, max_turns: 500}
  - {name: review, max_turns: 200}
  - {name: second-pass, max_turns: 150}
  - {name: ship, max_turns: 200}
---
Rigorous mode, new project: a reviewed spec, an MVP check-in, thorough tests, two
review passes, and a smoke test before every deploy. Take the time to make it
excellent: Riggy chose this mode because he wants the best version of the idea,
not the fastest.

Riggy is the product manager, not a reviewer of details. Decide colors, names,
libraries, layouts and similar yourself and record the choice in
`docs/decisions.md` (one line each: decision, why).

## spec

Turn the request into a thorough spec. The repo is new and empty (or nearly).

1. Write `docs/brief.md`: Riggy's request word for word, then the brief (name,
   one-paragraph summary, who it's for).
2. Research what makes this kind of thing good: think about the best existing
   apps or games like it and what makes them satisfying. Write the findings
   that matter into the spec.
3. Write `docs/specs/mvp.md` with skills/spec-writing.md: the MVP, what comes
   after it, what's out of scope, and a concrete bar for "good" (see the
   quality bar in this prompt): the details that make it delightful.
4. Choose the stack and record it in `docs/decisions.md` (skills/new-repo.md).
5. Commit and push to `main`.

Verdict: `continue`, with a one-sentence summary of the MVP. Use `blocked` only
if the request could mean two very different products.

## spec-review

You are a fresh pair of eyes on `docs/specs/mvp.md`, with only `docs/brief.md`
for context. Check it against Riggy's request: does the MVP deliver what he
asked for, is anything important missing, is anything there he didn't want, is
the quality bar specific enough to build against, is each "after the MVP" item
small enough for one PR? Edit the spec directly to fix what you find, note the
significant changes at the end under "Review notes", commit and push to `main`.

Verdict: `continue`, with the most important change you made.

## scaffold

Prove hosting before building features: get a minimal version of the project live.

1. Follow skills/new-repo.md: app skeleton, test setup, `Dockerfile`,
   `deploy/compose.yml`, the build workflow (tests must run in CI), `AGENTS.md`,
   `CLAUDE.md`, `README.md`. A styled page with the project's name and a line
   about it is enough for the app itself.
2. Commit and push straight to `main` (this is the one phase that skips the PR).
3. Deploy with skills/deploy.md and check the live URL with skills/browser.md.

Verdict: `continue`, linking the live URL.

## mvp

Build the MVP from `docs/specs/mvp.md`, then show it to Riggy.

1. Branch and build the MVP with thorough tests: unit tests for the logic, and
   browser tests (Playwright) for the main flows, running in CI.
2. Check it in a real browser as you go (skills/browser.md), at phone and
   desktop sizes. Look at the screenshots and hold them to the spec's quality
   bar; fix what falls short before moving on.
3. Smoke test the production build locally (build it, serve it, run the main
   flow in the browser). Then open a PR that closes the job's issue, wait for
   checks, merge it, deploy the merge commit (skills/deploy.md), and run the
   main flow again on the live site.

Verdict: `checkin`, linking the live URL. The message says what to try and,
briefly, what's deliberately not there yet. This is the one planned check-in.

## buildout

Riggy has seen the MVP. His reply is under "Riggy's feedback" in this prompt.

1. If he asked for changes, those come first, ahead of the rest of the spec.
   "Looks good" means carry on with the spec as written. If he said to stop or
   shelve it, finish nothing new: return `done` saying it's shelved.
2. Build out the spec's "after the MVP" list, with thorough tests. Update the
   spec if his feedback changed it.
3. Polish: go through every screen in the browser at phone and desktop sizes and
   fix rough edges (spacing, empty states, loading, feedback on every action,
   keyboard and touch, accessibility basics).
4. Open a PR (closing the issue) and wait for checks. Don't merge: review is next.

Verdict: `continue`, linking the PR.

## review

You are a fresh pair of eyes. Review the open PR with skills/review.md, against
`docs/specs/mvp.md` and `AGENTS.md` only: don't trust earlier reasoning, read the
diff and use the app. Be demanding: compare it to the spec's quality bar.

Fix what you find on the PR's branch, push, and wait for checks to pass. List
what you fixed in a PR comment (`gh pr comment`).

Verdict: `continue`, linking the PR, with the most important thing you fixed.

## second-pass

A second review, of the fixes. Read the first review's PR comment and the
commits after it: are the fixes right, complete, and tested, and did they break
anything else? Then use the whole app once more in the browser as a first-time
visitor on a phone. Fix what you find, push, and wait for checks.

Verdict: `continue`, linking the PR.

## ship

1. Smoke test the PR's production build locally in the browser. Merge the PR
   once checks pass, deploy the merge commit (skills/deploy.md), and run the
   main flows on the live site in the browser.
2. Docs: `README.md` leads with the live URL and what the project does;
   `AGENTS.md` is accurate (stack, run, test, deploy, gotchas).
3. Retro: add anything a future worker on this project should know to
   `docs/lessons.md`. Add lessons that apply to every project to the office
   lessons file named in this prompt. If a playbook or skill step was wrong or
   missing, write the edit you'd propose to a new file in the office proposals
   directory named in this prompt, for Riggy to apply by hand.
4. Commit docs straight to `main` and deploy again if anything served changed.

Verdict: `done`, linking the live URL, with one or two sentences on what Riggy
can do there now.
