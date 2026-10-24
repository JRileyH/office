# Prompts

Templates the office service fills in (`$name` placeholders, Python
`string.Template`; write a literal dollar sign as `$$`). Baked read-only into
the office image.

- `intake.md`: triage of a dictated idea into a brief.
- `step.md`: the wrapper around every agent step of a playbook.
- `fix.md`: the instructions of a `fix` step, which office inserts when CI fails
  or the deployer refuses a commit.
- `resume.md`: Riggy answered a `blocked` question; the same session carries on.
- `wrapup.md`: a step ran out of turns; commit and say whether it finished.
- `card.md`: a live project's card on the projects page (tagline, description,
  kind, tags), from its README and brief. Filled in by plain replacement, not
  `string.Template`.
