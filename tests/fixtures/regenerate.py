"""Regenerate synthetic fixture databases for tests.

The full reference fixture will be expanded as readers and writers are
implemented. This script is intentionally synthetic-only.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path


def main() -> None:
    fixtures = Path(__file__).parent
    db_path = fixtures / "empty.obsdb"
    if db_path.exists():
        db_path.unlink()
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("create table Main (prop_id text primary key, rsv_cat text not null)")
        conn.execute("create table WellAttributes (prop_id text primary key)")
        conn.commit()
    finally:
        conn.close()


if __name__ == "__main__":
    main()
