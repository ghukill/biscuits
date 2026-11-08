"""The MCP tools' names, descriptions and arguments, shared by the biscuits MCP
endpoint (mcp_tools.py) and the direct server (mcp_server.py), so the two can't
drift apart. Data only, and standard library only: mcp_server.py imports it.
"""

TOOLS = [
    {
        "name": "list_projects",
        "description": "List OpenProject projects (id, identifier, name).",
        "inputSchema": {"type": "object", "properties": {"active_only": {"type": "boolean", "default": True},
                                                         "limit": {"type": "integer", "default": 200}}},
    },
    {
        "name": "search_work_packages",
        "description": "Search/filter work packages. Returns id, subject, project, type, status, assignee, author, dates, pct_done, lockVersion.",
        "inputSchema": {"type": "object", "properties": {
            "project_id": {"type": "integer", "description": "restrict to a project id"},
            "subject_contains": {"type": "string", "description": "substring match on subject"},
            "query": {"type": "string", "description": "full-text search on subject OR work-package id (Cribbed from AndyEverything repo; verified on this build)"},
            "status": {"type": "string", "description": "status name(s) or id(s), comma-separated (e.g. 'new,in progress')"},
            "wp_type": {"type": "string", "description": "type name(s) or id(s), comma-separated"},
            "open_only": {"type": "boolean", "default": True, "description": "if no explicit status given, only open statuses"},
            "limit": {"type": "integer", "default": 25}}},
    },
    {
        "name": "get_work_package",
        "description": "Get full details (incl. description and attachments) of one work package by id.",
        "inputSchema": {"type": "object", "properties": {"work_package_id": {"type": "integer"}}, "required": ["work_package_id"]},
    },
    {
        "name": "create_work_package",
        "description": "Create a work package in a project. wp_type: Task, Milestone, Summary task, Feature, Epic, User story, Bug (or type id). status: status name or id.",
        "inputSchema": {"type": "object", "properties": {
            "project_id": {"type": "integer"}, "subject": {"type": "string"},
            "description": {"type": "string", "description": "markdown body"}, "wp_type": {"type": "string", "default": "Task"},
            "status": {"type": "string"}, "assignee_id": {"type": "integer"},
            "priority": {"type": "string", "description": "priority name or id (Low/Normal/High/Immediate)"},
            "start_on": {"type": "string", "description": "YYYY-MM-DD"}, "due_on": {"type": "string", "description": "YYYY-MM-DD"},
            "parent_id": {"type": "integer"}},
            "required": ["project_id", "subject"]},
    },
    {
        "name": "update_work_package",
        "description": "Update a work package (handles lockVersion automatically). Optionally attach a comment in the same call.",
        "inputSchema": {"type": "object", "properties": {
            "work_package_id": {"type": "integer"}, "subject": {"type": "string"}, "description": {"type": "string"},
            "status": {"type": "string", "description": "status name or id"}, "assignee_id": {"type": "integer"},
            "priority": {"type": "string", "description": "priority name or id (Low/Normal/High/Immediate)"},
            "start_on": {"type": "string"}, "due_on": {"type": "string"}, "parent_id": {"type": "integer"},
            "clear_parent": {"type": "boolean"}, "comment": {"type": "string", "description": "markdown comment to add"}},
            "required": ["work_package_id"]},
    },
    {
        "name": "add_comment",
        "description": "Add a markdown comment/note to a work package.",
        "inputSchema": {"type": "object", "properties": {"work_package_id": {"type": "integer"}, "comment": {"type": "string"}},
                        "required": ["work_package_id", "comment"]},
    },
    {
        "name": "add_attachment",
        "description": ("Attach a file (e.g. a screenshot or mockup) to a work package. Give file_path "
                        "(a local file) or content_base64 plus file_name. Max 10 MB. Returns the "
                        "attachment's id and a `markdown` snippet that shows it inline when put in a "
                        "description or comment. Attachments can't be removed through this server."),
        "inputSchema": {"type": "object", "properties": {
            "work_package_id": {"type": "integer"},
            "file_path": {"type": "string", "description": "path to a local file to upload"},
            "content_base64": {"type": "string", "description": "file contents, base64-encoded (instead of file_path)"},
            "file_name": {"type": "string", "description": "name to give the file (default: file_path's name)"},
            "content_type": {"type": "string", "description": "MIME type (default: guessed from the name)"}},
            "required": ["work_package_id"]},
    },
    {
        "name": "count_work_packages",
        "description": "Count work packages per project: total (all statuses) and open, plus sums across active projects. Archived projects are excluded (API returns 403).",
        "inputSchema": {"type": "object", "properties": {"project_id": {"type": "integer", "description": "count a single project"}}},
    },
    {
        "name": "get_comments",
        "description": "Read the comments (comment activities) on a work package, oldest-first.",
        "inputSchema": {"type": "object", "properties": {"work_package_id": {"type": "integer"}}, "required": ["work_package_id"]},
    },
    {
        "name": "list_statuses",
        "description": "List valid work-package statuses (id, name, isClosed).",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "list_types",
        "description": "List work-package types (id, name).",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "list_priorities",
        "description": "List work-package priorities (id, name).",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "list_users",
        "description": "List users/principals (id, name) for assignee_id fields.",
        "inputSchema": {"type": "object", "properties": {}},
    },
]


# Only on the biscuits endpoint, which knows about biscuits' own data.
LIST_SECTIONS = {
    "name": "list_sections",
    "description": ("List a project's sections (id, name), in the order biscuits shows them. A section "
                    "is a top-level task; give its id as parent_id to put a task in that section."),
    "inputSchema": {"type": "object", "properties": {"project_id": {"type": "integer"}},
                    "required": ["project_id"]},
}
