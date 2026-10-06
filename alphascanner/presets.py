import re
from dataclasses import dataclass
from datetime import UTC, datetime

from .db import connect
from .scanner import ScreenQuery

# Restricted so names are safe in URLs, CLI args, and HTML without escaping concerns.
_NAME_RE = re.compile(r"[A-Za-z0-9_-]{1,64}")


class InvalidPresetName(ValueError):
    pass


@dataclass
class Preset:
    name: str
    query: ScreenQuery
    created_at: str
    updated_at: str


def is_valid_name(name: str) -> bool:
    return _NAME_RE.fullmatch(name) is not None


def _check_name(name: str) -> None:
    if not is_valid_name(name):
        raise InvalidPresetName(
            "Preset names must be 1-64 characters: letters, digits, '-' or '_'."
        )


def save_preset(name: str, query: ScreenQuery) -> None:
    """Create or overwrite a preset."""
    _check_name(name)
    now = datetime.now(UTC).isoformat(timespec="seconds")
    with connect() as conn:
        conn.execute(
            "INSERT INTO presets (name, params, created_at, updated_at) VALUES (?,?,?,?) "
            "ON CONFLICT(name) DO UPDATE SET params = excluded.params, "
            "updated_at = excluded.updated_at",
            (name, query.model_dump_json(), now, now),
        )


def _from_row(row) -> Preset:
    return Preset(
        name=row["name"],
        query=ScreenQuery.model_validate_json(row["params"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


# Lookups and deletes allowlist-check the name before it reaches SQL, like saves
# do. The queries are parameterized regardless; this keeps untrusted input (e.g.
# the ?preset= query param) from ever touching the database layer. A name that
# fails the check can't exist, since save_preset only stores valid names.
def get_preset(name: str) -> Preset | None:
    if not is_valid_name(name):
        return None
    with connect() as conn:
        row = conn.execute("SELECT * FROM presets WHERE name = ?", (name,)).fetchone()
    return _from_row(row) if row else None


def list_presets() -> list[Preset]:
    with connect() as conn:
        rows = conn.execute("SELECT * FROM presets ORDER BY name").fetchall()
    return [_from_row(r) for r in rows]


def delete_preset(name: str) -> bool:
    if not is_valid_name(name):
        return False
    with connect() as conn:
        cur = conn.execute("DELETE FROM presets WHERE name = ?", (name,))
    return cur.rowcount > 0
