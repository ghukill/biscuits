from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext

from biscuits import archive, config, db


def test_database_is_private_and_created_on_first_use(write_config):
    write_config()
    assert archive.ids() == []
    assert config.load().db_file.stat().st_mode & 0o077 == 0


def test_migrations_match_the_models(write_config):
    write_config()
    with db.engine().connect() as conn:
        assert compare_metadata(MigrationContext.configure(conn), db.Base.metadata) == []
