"""Alembic environment. biscuits always passes in a connection; see db._upgrade."""

from alembic import context

from biscuits.db import Base

context.configure(
    connection=context.config.attributes["connection"],
    target_metadata=Base.metadata,
    render_as_batch=True,  # SQLite can't alter most things in place
)
with context.begin_transaction():
    context.run_migrations()
