"""Check the database connection configured in backend/.env (DATABASE_URL).

    python -m scripts.check_db

Prints the target (password hidden), server version, Alembic revision and table count.
Exit code 0 = connected and migrated, 1 = connection failed, 2 = connected but not migrated.
"""

from __future__ import annotations

import sys

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url

from app.config import get_settings
from app.database import Base
import app.models  # noqa: F401  (registers tables on Base.metadata)


def main() -> int:
    url = make_url(get_settings().database_url)
    print(f"target   : {url.render_as_string(hide_password=True)}")
    try:
        engine = create_engine(url, pool_pre_ping=True)
        with engine.connect() as conn:
            version = conn.execute(text("select version()")).scalar() if url.get_backend_name() == "postgresql" \
                else conn.execute(text("select sqlite_version()")).scalar()
            tables = set(inspect(conn).get_table_names())
            revision = (
                conn.execute(text("select version_num from alembic_version")).scalar()
                if "alembic_version" in tables else None
            )
    except Exception as exc:  # report, don't hide
        print(f"status   : CONNECTION FAILED - {type(exc).__name__}: {str(exc).splitlines()[0]}")
        print("hint     : is the PostgreSQL service running, and are user/password/database in DATABASE_URL correct?")
        return 1

    expected = set(Base.metadata.tables)
    missing = sorted(expected - tables)
    print(f"server   : {version}")
    print(f"alembic  : {revision or 'not migrated'}")
    print(f"tables   : {len(expected & tables)}/{len(expected)} application tables present")
    if missing:
        print(f"missing  : {', '.join(missing)}")
        print("hint     : run `alembic upgrade head`")
        return 2
    print("status   : OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
