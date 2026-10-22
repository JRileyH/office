---
phases:
  - {name: build, max_turns: 400}
---
Reckless mode, new project: one pass from idea to live site. No PRs, no review,
no check-in: build it, push to `main`, deploy it, and report. Riggy chose speed;
still make it work well and look good.

Decide colors, names, libraries, layouts and similar yourself.

## build

1. Write a short spec paragraph at the top of `README.md` (what it is, what it
   does), under the live URL. Put Riggy's request word for word in
   `docs/brief.md`.
2. Set the repo up with skills/new-repo.md. Tests are optional: the image build
   is the only required check.
3. Build the whole thing. Check it in a real browser (skills/browser.md) at phone
   size and fix what's broken or ugly.
4. Commit and push to `main`, deploy with skills/deploy.md, and check the live site
   in the browser.

Verdict: `done`, linking the live URL, with one or two sentences on what Riggy
can do there now.
