"""Persistence layer for todo-cli.

The primary public API is ``TodoRepository``.  It wraps a single JSON file
path and exposes all mutating operations as methods, eliminating the repeated
``load → mutate → save`` boilerplate that existed when storage was a bag of
free functions.

Backward-compatible free functions
-----------------------------------
The module-level functions (``add``, ``load``, ``mark_done``, etc.) are kept
as thin wrappers that create a temporary ``TodoRepository`` instance.  Existing
callers — including the test suite — continue to work without changes.
New code should prefer ``TodoRepository`` directly.

Undo / snapshot
---------------
``TodoRepository.snapshot()`` captures the current state as a raw list of
dicts.  ``TodoRepository.restore_snapshot(data)`` applies a previously captured
snapshot.  The UI uses these two methods to implement single-level undo without
reaching into private internals.
"""

from __future__ import annotations

import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path

from .models import Priority, Todo
from .queries import FilterOptions
from .queries import filter_todos as _query_filter_todos


# The default storage location.  Override via the ``$TODO_FILE`` environment
# variable (useful for testing with a real file outside tmp_path).
DEFAULT_PATH = Path(
    os.environ.get("TODO_FILE", Path.home() / ".todo-cli" / "todos.json")
)

# Sentinel that distinguishes "caller did not supply this argument" from
# ``None`` (which means "clear the field").  Using a dedicated sentinel avoids
# the ambiguity you would get with ``None`` as the default.
_UNSET = object()


# ---------------------------------------------------------------------------
# Low-level file helpers  (module-private)
# ---------------------------------------------------------------------------

def _load_raw(path: Path) -> list[dict]:
    """Read and return the raw JSON array from *path*.

    Returns an empty list if the file does not exist yet, so callers never
    need to guard against a missing file on first run.
    """
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


def _save_raw(data: list[dict], path: Path) -> None:
    """Atomically-ish write *data* as a JSON array to *path*.

    Creates parent directories as needed.  Indented output keeps the file
    human-readable and produces clean diffs in version control.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)


# ---------------------------------------------------------------------------
# TodoRepository
# ---------------------------------------------------------------------------

class TodoRepository:
    """All persistence operations for a single todo list file.

    Each instance is bound to one JSON file (``path``).  Methods follow a
    consistent pattern:

    1. ``_load()``  — read todos from disk into memory
    2. mutate the in-memory list
    3. ``_save()``  — write the updated list back to disk

    This eliminates the duplicated ``path`` parameter that appeared on every
    free function and makes the class easy to mock in unit tests (subclass and
    override ``_load`` / ``_save``).

    Usage::

        repo = TodoRepository(Path("~/my-todos.json"))
        todo = repo.add("Buy milk", priority="high")
        repo.mark_done(todo.id)
        repo.delete(todo.id)
    """

    def __init__(self, path: Path = DEFAULT_PATH) -> None:
        self._path = path

    # ── Internal helpers ──────────────────────────────────────────────────

    def _load(self) -> list[Todo]:
        """Read the todo list from disk.  Called at the top of every operation."""
        return [Todo.from_dict(d) for d in _load_raw(self._path)]

    def _save(self, todos: list[Todo]) -> None:
        """Persist the todo list to disk."""
        _save_raw([t.to_dict() for t in todos], self._path)

    # ── Read operations ───────────────────────────────────────────────────

    def load(self) -> list[Todo]:
        """Return the current list of todos (fresh read from disk)."""
        return self._load()

    def export_json(self) -> str:
        """Return the full todo list serialised as a JSON string."""
        return json.dumps([t.to_dict() for t in self._load()], indent=2)

    def snapshot(self) -> list[dict]:
        """Capture the current state as a raw list of dicts.

        The snapshot can be passed back to ``restore_snapshot`` to implement
        undo.  Raw dicts (not Todo objects) are used so the snapshot is
        immediately serialisable and completely independent of the live list.
        """
        return [t.to_dict() for t in self._load()]

    def restore_snapshot(self, data: list[dict]) -> None:
        """Overwrite the file with a previously captured snapshot.

        This is the *only* sanctioned way to write raw dicts to storage.
        The UI's undo action uses this instead of calling ``_save_raw``
        directly, which would break encapsulation.
        """
        _save_raw(data, self._path)

    # ── Write operations ──────────────────────────────────────────────────

    def add(
        self,
        title: str,
        *,
        due_date:  str | None      = None,
        priority:  str             = Priority.MEDIUM,
        notes:     str             = "",
        tags:      list[str] | None = None,
        recur:     str | None      = None,
    ) -> Todo:
        """Create a new todo and persist it.

        The new todo receives an ``id`` one greater than the current maximum,
        so ids are always increasing even after deletions.

        Returns the newly created ``Todo``.
        """
        todos   = self._load()
        next_id = max((t.id for t in todos), default=0) + 1
        todo    = Todo(
            id=       next_id,
            title=    title,
            due_date= due_date,
            priority= priority,
            notes=    notes,
            tags=     tags or [],
            recur=    recur,
        )
        todos.append(todo)
        self._save(todos)
        return todo

    def edit(
        self,
        todo_id: int,
        *,
        title:     str | None = None,
        due_date:  object     = _UNSET,   # pass None to clear the date
        priority:  str | None = None,
        notes:     str | None = None,
        tags:      object     = _UNSET,   # pass [] to clear all tags
        recur:     object     = _UNSET,   # pass None to clear recurrence
    ) -> Todo | None:
        """Update one or more fields of an existing todo.

        Only the fields you supply are changed; others are left as-is.
        Uses the ``_UNSET`` sentinel to distinguish "not given" (leave field
        alone) from ``None`` (clear the field).

        Returns the updated ``Todo``, or ``None`` if ``todo_id`` was not found.
        """
        todos = self._load()
        for todo in todos:
            if todo.id != todo_id:
                continue
            if title is not None:
                todo.title = title
            if due_date is not _UNSET:
                todo.due_date = due_date          # type: ignore[assignment]
            if priority is not None:
                todo.priority = priority
            if notes is not None:
                todo.notes = notes
            if tags is not _UNSET:
                todo.tags = list(tags)            # type: ignore[arg-type]
            if recur is not _UNSET:
                todo.recur = recur                # type: ignore[assignment]
            self._save(todos)
            return todo
        return None

    def mark_done(self, todo_id: int) -> Todo | None:
        """Mark a todo as complete and record the completion timestamp.

        **Recurring todos**: if the todo has a ``recur`` field, a new sibling
        todo is automatically appended with the next calculated due date.  The
        original todo is still marked done — it is *not* mutated into the next
        occurrence.  This preserves the completion history.

        Returns the completed ``Todo``, or ``None`` if not found.
        """
        todos = self._load()
        for todo in todos:
            if todo.id != todo_id:
                continue
            todo.done         = True
            todo.completed_at = datetime.now(timezone.utc).isoformat()

            # Spawn the next occurrence for recurring todos.
            if todo.recur:
                self._spawn_recurrence(todos, todo)

            self._save(todos)
            return todo
        return None

    def _spawn_recurrence(self, todos: list[Todo], source: Todo) -> None:
        """Append a new pending todo based on a completed recurring one.

        Extracted from ``mark_done`` so that the spawning logic is isolated
        and testable on its own.  The new todo inherits title, priority, notes,
        tags, and recur from the source; its due date is advanced by one period.
        """
        next_id = max(t.id for t in todos) + 1
        todos.append(Todo(
            id=       next_id,
            title=    source.title,
            priority= source.priority,
            notes=    source.notes,
            tags=     source.tags[:],   # defensive copy — lists are mutable
            recur=    source.recur,
            due_date= source.next_due_date(),
        ))

    def delete(self, todo_id: int) -> Todo | None:
        """Remove a todo by id.

        Returns the deleted ``Todo`` so callers can show confirmation text,
        or ``None`` if the id was not found.
        """
        todos = self._load()
        for i, todo in enumerate(todos):
            if todo.id == todo_id:
                todos.pop(i)
                self._save(todos)
                return todo
        return None

    def move(self, todo_id: int, new_index: int) -> bool:
        """Move a todo to a new 0-based position in the list.

        The position is clamped so it can never go out of bounds.
        Returns ``True`` on success, ``False`` if ``todo_id`` was not found.
        """
        todos = self._load()
        for i, todo in enumerate(todos):
            if todo.id == todo_id:
                todos.pop(i)
                todos.insert(max(0, min(new_index, len(todos))), todo)
                self._save(todos)
                return True
        return False

    def backup(self, dest: Path) -> bool:
        """Copy the todos file to *dest*.

        Returns ``False`` (and does nothing) if the source file does not yet
        exist (i.e. no todos have been saved yet).
        """
        if not self._path.exists():
            return False
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(self._path, dest)
        return True

    def restore(self, src: Path) -> bool:
        """Overwrite the todos file with a backup from *src*.

        Returns ``False`` if *src* does not exist.
        """
        if not src.exists():
            return False
        self._path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, self._path)
        return True


# ---------------------------------------------------------------------------
# Backward-compatible module-level free functions
# ---------------------------------------------------------------------------
# These thin wrappers exist so that the extensive test suite (which calls
# ``storage.add(..., path=x)``) continues to work without modification.
# New code should use ``TodoRepository`` directly.

def load(path: Path = DEFAULT_PATH) -> list[Todo]:
    """Read todos from *path*.  Prefer ``TodoRepository.load()`` in new code."""
    return TodoRepository(path).load()


def save(todos: list[Todo], path: Path = DEFAULT_PATH) -> None:
    """Persist *todos* to *path*.  Prefer ``TodoRepository`` in new code."""
    _save_raw([t.to_dict() for t in todos], path)


def add(title: str, *, path: Path = DEFAULT_PATH, **kwargs) -> Todo:
    """Create and persist a todo.  Prefer ``TodoRepository.add()`` in new code."""
    return TodoRepository(path).add(title, **kwargs)


def edit(todo_id: int, *, path: Path = DEFAULT_PATH, **kwargs) -> Todo | None:
    """Edit a todo.  Prefer ``TodoRepository.edit()`` in new code."""
    return TodoRepository(path).edit(todo_id, **kwargs)


def mark_done(todo_id: int, path: Path = DEFAULT_PATH) -> Todo | None:
    """Mark a todo done.  Prefer ``TodoRepository.mark_done()`` in new code."""
    return TodoRepository(path).mark_done(todo_id)


def delete(todo_id: int, path: Path = DEFAULT_PATH) -> Todo | None:
    """Delete a todo.  Prefer ``TodoRepository.delete()`` in new code."""
    return TodoRepository(path).delete(todo_id)


def move(todo_id: int, new_index: int, path: Path = DEFAULT_PATH) -> bool:
    """Move a todo.  Prefer ``TodoRepository.move()`` in new code."""
    return TodoRepository(path).move(todo_id, new_index)


def backup(dest_path: Path, src_path: Path = DEFAULT_PATH) -> bool:
    """Back up the todos file.  Prefer ``TodoRepository.backup()`` in new code."""
    return TodoRepository(src_path).backup(dest_path)


def restore(src_path: Path, dest_path: Path = DEFAULT_PATH) -> bool:
    """Restore from backup.  Prefer ``TodoRepository.restore()`` in new code."""
    return TodoRepository(dest_path).restore(src_path)


def export_json(path: Path = DEFAULT_PATH) -> str:
    """Export todos as JSON string.  Prefer ``TodoRepository.export_json()``."""
    return TodoRepository(path).export_json()


def filter_todos(
    todos: list[Todo],
    *,
    filter_by: str        = "all",
    tag:       str | None = None,
    priority:  str | None = None,
    search:    str | None = None,
    sort_by:   str        = "position",
) -> list[Todo]:
    """Filter and sort todos.  Prefer ``queries.filter_todos()`` in new code.

    This wrapper translates the old keyword-argument signature into the new
    ``FilterOptions`` parameter object, keeping all existing call sites working.
    """
    return _query_filter_todos(
        todos,
        FilterOptions(
            filter_by=filter_by,
            tag=tag,
            priority=priority,
            search=search,
            sort_by=sort_by,
        ),
    )
