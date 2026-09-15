"""Check that Astrocade's SQLite database is ready for use."""

import os
import sqlite3
import sys
from pathlib import Path
from typing import Final

DEFAULT_DATABASE_PATH: Final = "/astrocade/astrocade.db"
REQUIRED_TABLES: Final = frozenset({"players", "wordle_puzzles", "wordle_results"})


def main() -> int:
    """Confirm the application database contains Astrocade's required tables."""
    database = Path(os.environ.get("DATABASE_PATH", DEFAULT_DATABASE_PATH)).resolve()
    if not database.is_file():
        print(f"healthcheck: database does not exist: {database}", file=sys.stderr)
        return 1

    try:
        uri = f"{database.as_uri()}?mode=ro"
        with sqlite3.connect(uri, uri=True, timeout=3) as connection:
            tables = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_schema WHERE type = 'table'"
                )
            }
    except sqlite3.Error as error:
        print(f"healthcheck: database check failed: {error}", file=sys.stderr)
        return 1

    if missing := REQUIRED_TABLES - tables:
        print(
            f"healthcheck: database is missing required tables: {', '.join(sorted(missing))}",
            file=sys.stderr,
        )
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
