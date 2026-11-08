---
name: release
description: Cut a biscuits release and put it on prod. It bumps the version, adds a CHANGELOG entry, tags, pushes to GitHub, then runs deploy_release on the prod VM. Use when admin says they're ready for a new release ("let's release", "ship it to prod", "I'm ready for a new release"). Never use it on your own initiative.
---

# Releasing biscuits

A release is the **only** thing that changes the version, and **every release goes
to prod**. It ships whatever is on `main`. Task branches reach `main` only when admin
has approved them, so if work is still waiting on a branch, ask about it first.

Read "Releases" in `README.md` for the background. Approvals:

- Admin asking for a release in this session approves steps 1 to 4.
- **Stop at step 5 and get an explicit go-ahead** before pushing or touching prod,
  unless admin has already said in this session to go all the way to prod.

## 1. What's going out

```sh
git checkout main && git status --short     # must be clean
last=$(git describe --tags --abbrev=0 --match 'v[0-9]*' 2>/dev/null || echo "")
git log --oneline --first-parent ${last:+$last..}main
```

- If nothing has changed since `$last`, say so and stop.
- List the tasks (the `(#NNNN)` numbers) and read each one's subject in OpenProject
  if a commit doesn't make the change clear.

## 2. Choose the version

Semver, while we're below 1.0:

- **Minor** (`0.5.0` → `0.6.0`) if anything new is visible to people: a feature,
  a new MCP tool, a new setting.
- **Patch** (`0.5.0` → `0.5.1`) if it's only fixes and internal changes.

Tell admin which you chose and why. They can overrule it.

## 3. Write the release

On `main`:

1. Bump `version` in `pyproject.toml`, `__version__` in `src/biscuits/__init__.py`,
   and the `biscuits` entry in `uv.lock`. Then `uv lock --check`.
2. Add a section at the top of `CHANGELOG.md`: `## X.Y.Z (YYYY-MM-DD)`, then one
   plain-language line per task, with its number, written for the people who use
   biscuits. Put the developer-only changes under a short "Behind the scenes" list.
3. Run `uv run pytest`. Everything must pass.
4. Commit as `Release X.Y.Z`, with the CHANGELOG section as the body. Tag it
   `git tag -a vX.Y.Z -m "biscuits X.Y.Z"`.

## 4. Try it on dev

```sh
git checkout dev && git reset --hard main     # dev becomes exactly the release
uv tool install --force --reinstall . && biscuits restart
curl -s http://127.0.0.1:9173/healthz          # "version" and "build" both X.Y.Z
```

Then re-merge into `dev` any task branches that are still waiting for review (they're
listed in project 56 as "waiting on correspondence"), and deploy again.

## 5. Ask for the go-ahead

Show admin the version, the CHANGELOG section and the dev check. Stop here unless they
have already approved going to prod in this session.

## 6. Push and deploy to prod

```sh
git push origin main vX.Y.Z
# Keep the VM's script in step with the repo's (it lives outside any checkout):
ssh franklab-biscuits.exe.xyz 'sha256sum ~/.local/bin/deploy_release' ; sha256sum deploy/deploy_release
# If they differ or it's missing, say so, then:
scp deploy/deploy_release franklab-biscuits.exe.xyz:.local/bin/deploy_release
ssh franklab-biscuits.exe.xyz 'chmod 755 ~/.local/bin/deploy_release'
ssh franklab-biscuits.exe.xyz '~/.local/bin/deploy_release vX.Y.Z'
```

`deploy_release` installs the tag, restarts, and waits until `/healthz` reports the
new version. It exits 0 when that has worked, or exits 1 after putting the previous
build back. Show admin its whole output, not a summary. If it rolled back, the
release is still tagged and pushed but **not live**. Say so plainly, and don't
re-tag. Fix the problem in a patch release.

Use SSH on the VM only to run these commands. Don't read its config or data.

## 7. Afterwards

- `deploy_release`'s own `/healthz` check is the one that counts. The public URL sits
  behind exe.dev's login, so `curl` from outside gets a redirect, not biscuits.
- `ssh franklab-biscuits.exe.xyz 'tail -3 ~/.biscuits/deploys.log'` shows the result.
- Comment on each task that went out: "Released in X.Y.Z, now on prod."
- Planning docs: the Status row in the planning `README.md`, and the roadmap entry
  for each task.
- MCP sessions that are already running (e.g. the Mattermost assistant) keep the old
  code until they restart. Mention this if the release changed `biscuits mcp`.
