# Playbooks

The steps of a job, read by the scheduler and written into its database when the
job starts. Front matter lists the steps; each `## <name>` section is an agent
step's instructions. Every other step is office's own code: `setup`, `push`,
`pr`, `checks`, `merge`, `push-main`, `deploy`, `checkin`, `alert`. Every
playbook starts with `setup` and ends with `deploy, alert` (the loader refuses
anything else). The model and effort come from the project. Baked read-only into
the office image.

- `reckless.md`, `standard.md`, `rigorous.md`: new projects, by mode.
- `feature.md`: changes Riggy asks for in a project's room (steps can be limited
  to some modes).

When CI fails or the deployer refuses a commit, office inserts a `fix` agent
step (instructions in `prompts/fix.md`) and repeats the steps from the last push.
