# Reviewing a PR

You're reviewing work you didn't write, with only the spec, `AGENTS.md` and the
diff. That's deliberate: judge the result, not the reasoning that produced it.

```sh
gh pr list --state open
gh pr view <n>
gh pr diff <n>
gh pr checkout <n>
```

Check, in this order:

1. **Does it do what was asked?** Compare against the spec or Riggy's request.
   Run it: start the app or open the built files and walk the main flow.
   Missing behavior is the most important finding.
2. **Is it broken?** Errors on load, unhandled empty states, things that fail on
   a phone-sized screen, data lost on restart (state must live in a named volume
   or the browser).
3. **Will it deploy?** `deploy/compose.yml` follows skills/deploy.md, the app
   listens on `0.0.0.0` at `office.port`, the workflow builds and pushes the
   image, and checks are green.
4. **Are the core paths tested?** Tests that would fail if the main flow broke.
5. **Is it maintainable?** Docs accurate, no dead code, no secrets committed.

Fix what you find on the PR's branch in small commits, push, and wait for checks.
Don't rewrite working code to suit your taste, and don't add scope.
