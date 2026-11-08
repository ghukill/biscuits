# Changelog

Each release of biscuits, newest first. A release is the only thing that changes
the version, and every release goes to prod. See "Releases" in `README.md`.

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
