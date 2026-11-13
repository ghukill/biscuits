# Changelog

Each release of biscuits, newest first. A release is the only thing that changes
the version, and every release goes to prod. See "Releases" in `README.md`.

## 0.7.0 (2026-11-13)

- **New tasks start today** (#3512). When you add a task or subtask, its start
  date is filled in with today's date, which you can change or clear. If the
  task's due date has already passed, the start date is set to the due date.

## 0.6.0 (2026-11-08)

- **Assistants and bots go through biscuits** (#3490, #3476). Programs such as the
  Mattermost assistant and agent sessions now reach OpenProject through biscuits
  instead of directly. Each one signs in as a biscuits account, so its changes
  follow biscuits' write settings, appear in the write log, and show who really
  made them. Only reachable from this machine and the tailnet for now.
- **Assistants know about sections and archived projects** (#3490): a new
  `list_sections` tool, the section a task is in, and archived projects left out
  of the project list.

Behind the scenes:

- `biscuits tokens add/list/revoke` makes API tokens for programs; `biscuits mcp`
  is now a small client for the server's `/mcp` endpoint, and the old direct
  server is `biscuits mcp-direct`, for emergencies. New `[mcp]` and `[agent]
  mcp_url`/`mcp_token_file` settings, and a new database table (#3490).
- The release steps no longer try the public health check, which is behind
  exe.dev's login (#3495).

## 0.5.0 (2026-11-08)

- **Pictures and files on tasks** (#3494). Pictures show as previews you can
  click to enlarge, and other files as downloads. Add them with "+ Add files", by
  dragging them onto a task, or by pasting a screenshot. Paste or drop one while
  writing a description or comment and it appears right there in the text. Up to
  10 MB each. You only see files you can already see in OpenProject.
- **The assistant can attach files too** (#3494): the MCP server has a new
  `add_attachment` tool, and `get_work_package` lists a task's attachments.

Behind the scenes:

- Releases are now tagged and deployed to prod with `deploy_release`, and the dev
  server shows how far it is past the last release (#3495).

Versions before 0.5.0 weren't tagged. The git history has their details.
