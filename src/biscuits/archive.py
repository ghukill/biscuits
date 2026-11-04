"""Archiving projects, as in Asana: out of everyday views, still fully readable.

This is a flag in biscuits' own database. OpenProject's own archive isn't used
because it makes a project's work packages unreadable through the API.
"""

from datetime import datetime, timezone

from sqlalchemy import select

from . import db


def ids() -> list[int]:
    with db.session() as s:
        return sorted(s.scalars(select(db.ArchivedProject.project_id)))


def set_archived(project_id: int, archived: bool) -> None:
    with db.session() as s:
        row = s.get(db.ArchivedProject, project_id)
        if archived and row is None:
            s.add(db.ArchivedProject(project_id=project_id, archived_at=datetime.now(timezone.utc)))
        elif not archived and row is not None:
            s.delete(row)
