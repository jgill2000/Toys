from __future__ import annotations

import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path

from .models import Todo

DEFAULT_PATH = Path(os.environ.get("TODO_FILE", Path.home() / ".todo-cli" / "todos.json"))

# Sentinel – distinguishes "not supplied" from None (which clears a field).
_UNSET = object()


def _load_raw(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open() as f:
        return json.load(f)


def _save_raw(todos: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as f:
        json.dump(todos, f, indent=2)


def load(path: Path = DEFAULT_PATH) -> list[Todo]:
    return [Todo.from_dict(d) for d in _load_raw(path)]


def save(todos: list[Todo], path: Path = DEFAULT_PATH) -> None:
    _save_raw([t.to_dict() for t in todos], path)


def add(
    title: str,
    *,
    due_date: str | None = None,
    priority: str = "medium",
    notes: str = "",
    tags: list[str] | None = None,
    recur: str | None = None,
    path: Path = DEFAULT_PATH,
) -> Todo:
    todos = load(path)
    next_id = max((t.id for t in todos), default=0) + 1
    todo = Todo(
        id=next_id,
        title=title,
        due_date=due_date,
        priority=priority,
        notes=notes,
        tags=tags or [],
        recur=recur,
    )
    todos.append(todo)
    save(todos, path)
    return todo


def edit(
    todo_id: int,
    *,
    title: str | None = None,
    due_date: object = _UNSET,   # pass None to clear
    priority: str | None = None,
    notes: str | None = None,
    tags: object = _UNSET,       # pass [] to clear
    recur: object = _UNSET,      # pass None to clear
    path: Path = DEFAULT_PATH,
) -> Todo | None:
    todos = load(path)
    for todo in todos:
        if todo.id == todo_id:
            if title is not None:
                todo.title = title
            if due_date is not _UNSET:
                todo.due_date = due_date  # type: ignore[assignment]
            if priority is not None:
                todo.priority = priority
            if notes is not None:
                todo.notes = notes
            if tags is not _UNSET:
                todo.tags = list(tags)  # type: ignore[arg-type]
            if recur is not _UNSET:
                todo.recur = recur  # type: ignore[assignment]
            save(todos, path)
            return todo
    return None


def mark_done(todo_id: int, path: Path = DEFAULT_PATH) -> Todo | None:
    todos = load(path)
    for todo in todos:
        if todo.id == todo_id:
            todo.done = True
            todo.completed_at = datetime.now(timezone.utc).isoformat()
            # Recurring: spawn next occurrence automatically.
            if todo.recur:
                next_id = max(t.id for t in todos) + 1
                todos.append(Todo(
                    id=next_id,
                    title=todo.title,
                    priority=todo.priority,
                    notes=todo.notes,
                    tags=todo.tags[:],
                    recur=todo.recur,
                    due_date=todo.next_due_date(),
                ))
            save(todos, path)
            return todo
    return None


def delete(todo_id: int, path: Path = DEFAULT_PATH) -> Todo | None:
    todos = load(path)
    for i, todo in enumerate(todos):
        if todo.id == todo_id:
            todos.pop(i)
            save(todos, path)
            return todo
    return None


def move(todo_id: int, new_index: int, path: Path = DEFAULT_PATH) -> bool:
    """Move a todo to new_index (0-based) in the list."""
    todos = load(path)
    for i, todo in enumerate(todos):
        if todo.id == todo_id:
            todos.pop(i)
            todos.insert(max(0, min(new_index, len(todos))), todo)
            save(todos, path)
            return True
    return False


def backup(dest_path: Path, src_path: Path = DEFAULT_PATH) -> bool:
    if not src_path.exists():
        return False
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src_path, dest_path)
    return True


def restore(src_path: Path, dest_path: Path = DEFAULT_PATH) -> bool:
    if not src_path.exists():
        return False
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src_path, dest_path)
    return True


def filter_todos(
    todos: list[Todo],
    *,
    filter_by: str = "all",    # "all" | "pending" | "done" | "overdue"
    tag: str | None = None,
    priority: str | None = None,
    search: str | None = None,
    sort_by: str = "position", # "position" | "due" | "priority" | "alpha" | "created"
) -> list[Todo]:
    result = list(todos)

    if filter_by == "pending":
        result = [t for t in result if not t.done]
    elif filter_by == "done":
        result = [t for t in result if t.done]
    elif filter_by == "overdue":
        result = [t for t in result if t.is_overdue]

    if tag:
        result = [t for t in result if tag in t.tags]
    if priority:
        result = [t for t in result if t.priority == priority]
    if search:
        s = search.lower()
        result = [
            t for t in result
            if s in t.title.lower()
            or s in t.notes.lower()
            or any(s in tg.lower() for tg in t.tags)
        ]

    _PRI = {"high": 0, "medium": 1, "low": 2}
    if sort_by == "due":
        result.sort(key=lambda t: (t.due_date or "9999-99-99", t.id))
    elif sort_by == "priority":
        result.sort(key=lambda t: (_PRI.get(t.priority, 1), t.id))
    elif sort_by == "alpha":
        result.sort(key=lambda t: t.title.lower())
    elif sort_by == "created":
        result.sort(key=lambda t: t.created_at)
    # "position" → preserve JSON array order

    return result


def export_json(path: Path = DEFAULT_PATH) -> str:
    todos = load(path)
    return json.dumps([t.to_dict() for t in todos], indent=2)
