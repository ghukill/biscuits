"""The order of a project's sections, kept in biscuits' own database.

A section is an OpenProject work package, but OpenProject's API has no manual
position on this build, so the order can't live there. Sections with no stored
position come after those with one, in id order. That means a project nobody has
reordered keeps its import order, and a new section starts at the end.
"""

from datetime import datetime, timezone

from sqlalchemy import delete, select

from . import db


def order() -> list[int]:
    """Every positioned section id, grouped by project and in order within each."""
    with db.session() as s:
        return list(s.scalars(select(db.SectionPosition.section_id)
                              .order_by(db.SectionPosition.project_id, db.SectionPosition.position)))


def set_order(project_id: int, section_ids: list[int], by: int | None = None) -> None:
    """Replace a project's order. Any section left out goes back to the end."""
    now = datetime.now(timezone.utc)
    with db.session() as s:
        s.execute(delete(db.SectionPosition).where(
            (db.SectionPosition.project_id == project_id) | db.SectionPosition.section_id.in_(section_ids)))
        s.add_all(db.SectionPosition(section_id=sid, project_id=project_id, position=n, updated_at=now, updated_by=by)
                  for n, sid in enumerate(section_ids))
