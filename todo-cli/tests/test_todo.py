"""Tests for todo-cli."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from todo import storage
from todo.storage import TodoRepository
from todo.queries import FilterOptions, filter_todos as filter_todos
from todo.models import Todo


@pytest.fixture
def todo_file(tmp_path: Path) -> Path:
    return tmp_path / "todos.json"


# ---------------------------------------------------------------------------
# Model
# ---------------------------------------------------------------------------

class TestTodo:
    def test_to_dict_roundtrip(self):
        t = Todo(id=1, title="Buy milk", done=False, created_at="2024-01-01T00:00:00+00:00")
        assert Todo.from_dict(t.to_dict()) == t

    def test_done_defaults_false(self):
        t = Todo(id=1, title="Task")
        assert t.done is False

    def test_created_at_auto_populated(self):
        t = Todo(id=1, title="Task")
        assert t.created_at != ""


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------

class TestStorage:
    def test_load_empty_when_no_file(self, todo_file: Path):
        assert storage.load(todo_file) == []

    def test_add_creates_file(self, todo_file: Path):
        storage.add("First task", path=todo_file)
        assert todo_file.exists()

    def test_add_increments_id(self, todo_file: Path):
        t1 = storage.add("First", path=todo_file)
        t2 = storage.add("Second", path=todo_file)
        assert t1.id == 1
        assert t2.id == 2

    def test_add_persists(self, todo_file: Path):
        storage.add("Persisted", path=todo_file)
        todos = storage.load(todo_file)
        assert len(todos) == 1
        assert todos[0].title == "Persisted"

    def test_mark_done(self, todo_file: Path):
        todo = storage.add("Do it", path=todo_file)
        result = storage.mark_done(todo.id, path=todo_file)
        assert result is not None
        assert result.done is True
        # Verify persisted
        todos = storage.load(todo_file)
        assert todos[0].done is True

    def test_mark_done_unknown_id_returns_none(self, todo_file: Path):
        assert storage.mark_done(999, path=todo_file) is None

    def test_export_json_empty(self, todo_file: Path):
        result = storage.export_json(path=todo_file)
        assert json.loads(result) == []

    def test_export_json_contains_todos(self, todo_file: Path):
        storage.add("Alpha", path=todo_file)
        storage.add("Beta", path=todo_file)
        data = json.loads(storage.export_json(path=todo_file))
        assert len(data) == 2
        assert data[0]["title"] == "Alpha"
        assert data[1]["title"] == "Beta"

    def test_export_json_reflects_done(self, todo_file: Path):
        t = storage.add("Check me", path=todo_file)
        storage.mark_done(t.id, path=todo_file)
        data = json.loads(storage.export_json(path=todo_file))
        assert data[0]["done"] is True


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

class TestCLI:
    """Integration tests via the argparse layer."""

    def _run(self, args: list[str], todo_file: Path) -> tuple[int, str]:
        """Run CLI with captured stdout, return (exit_code, output)."""
        import io
        from contextlib import redirect_stdout
        from todo.cli import build_parser

        parser = build_parser()
        parsed = parser.parse_args(["--file", str(todo_file)] + args)
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = parsed.func(parsed)
        return code, buf.getvalue()

    def test_add_and_list(self, todo_file: Path):
        code, out = self._run(["add", "Walk the dog"], todo_file)
        assert code == 0
        assert "Walk the dog" in out

        code, out = self._run(["list"], todo_file)
        assert code == 0
        assert "Walk the dog" in out

    def test_list_empty(self, todo_file: Path):
        code, out = self._run(["list"], todo_file)
        assert code == 0
        assert "No todos" in out

    def test_done_marks_item(self, todo_file: Path):
        self._run(["add", "Finish report"], todo_file)
        code, out = self._run(["done", "1"], todo_file)
        assert code == 0
        assert "Finish report" in out

        _, list_out = self._run(["list"], todo_file)
        assert "✓" in list_out

    def test_done_unknown_id_fails(self, todo_file: Path):
        import sys, io
        from contextlib import redirect_stderr
        from todo.cli import build_parser

        parser = build_parser()
        parsed = parser.parse_args(["--file", str(todo_file), "done", "99"])
        buf = io.StringIO()
        with redirect_stderr(buf):
            code = parsed.func(parsed)
        assert code == 1

    def test_export_stdout(self, todo_file: Path):
        self._run(["add", "Export me"], todo_file)
        code, out = self._run(["export"], todo_file)
        assert code == 0
        data = json.loads(out)
        assert data[0]["title"] == "Export me"

    def test_export_to_file(self, todo_file: Path, tmp_path: Path):
        self._run(["add", "Save to file"], todo_file)
        out_file = tmp_path / "out.json"
        code, _ = self._run(["export", "--output", str(out_file)], todo_file)
        assert code == 0
        assert out_file.exists()
        data = json.loads(out_file.read_text())
        assert data[0]["title"] == "Save to file"

    def test_add_empty_title_fails(self, todo_file: Path):
        import sys, io
        from contextlib import redirect_stderr
        from todo.cli import build_parser

        parser = build_parser()
        parsed = parser.parse_args(["--file", str(todo_file), "add", "   "])
        buf = io.StringIO()
        with redirect_stderr(buf):
            code = parsed.func(parsed)
        assert code == 1


# ---------------------------------------------------------------------------
# Delete (storage + CLI)
# ---------------------------------------------------------------------------

class TestDelete:
    def test_delete_removes_todo(self, todo_file: Path):
        t = storage.add("Remove me", path=todo_file)
        result = storage.delete(t.id, path=todo_file)
        assert result is not None
        assert result.title == "Remove me"
        assert storage.load(todo_file) == []

    def test_delete_unknown_id_returns_none(self, todo_file: Path):
        assert storage.delete(999, path=todo_file) is None

    def test_delete_only_removes_target(self, todo_file: Path):
        t1 = storage.add("Keep", path=todo_file)
        t2 = storage.add("Delete", path=todo_file)
        storage.delete(t2.id, path=todo_file)
        todos = storage.load(todo_file)
        assert len(todos) == 1
        assert todos[0].id == t1.id

    def test_cli_delete(self, todo_file: Path):
        from todo.cli import build_parser
        import io
        from contextlib import redirect_stdout

        storage.add("CLI delete me", path=todo_file)
        parser = build_parser()
        parsed = parser.parse_args(["--file", str(todo_file), "delete", "1"])
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = parsed.func(parsed)
        assert code == 0
        assert "CLI delete me" in buf.getvalue()
        assert storage.load(todo_file) == []

    def test_cli_delete_unknown_fails(self, todo_file: Path):
        from todo.cli import build_parser
        import io
        from contextlib import redirect_stderr

        parser = build_parser()
        parsed = parser.parse_args(["--file", str(todo_file), "delete", "99"])
        buf = io.StringIO()
        with redirect_stderr(buf):
            code = parsed.func(parsed)
        assert code == 1


# ---------------------------------------------------------------------------
# New model fields (backward compat + derived properties)
# ---------------------------------------------------------------------------

class TestModelFields:
    def test_new_fields_have_defaults(self):
        t = Todo(id=1, title="Task")
        assert t.due_date is None
        assert t.priority == "medium"
        assert t.notes == ""
        assert t.tags == []
        assert t.recur is None
        assert t.completed_at is None

    def test_from_dict_old_format_still_works(self):
        """Old JSON without new fields must deserialise cleanly."""
        t = Todo.from_dict({"id": 1, "title": "Old", "done": False, "created_at": ""})
        assert t.priority == "medium"
        assert t.tags == []

    def test_is_overdue_false_when_done(self):
        from datetime import date, timedelta
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        t = Todo(id=1, title="T", done=True, due_date=yesterday)
        assert not t.is_overdue

    def test_is_overdue_true_for_past_due(self):
        from datetime import date, timedelta
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        t = Todo(id=1, title="T", due_date=yesterday)
        assert t.is_overdue

    def test_is_due_today(self):
        from datetime import date
        t = Todo(id=1, title="T", due_date=date.today().isoformat())
        assert t.is_due_today

    def test_next_due_date_daily(self):
        from datetime import date, timedelta
        today = date.today()
        t = Todo(id=1, title="T", due_date=today.isoformat(), recur="daily")
        assert t.next_due_date() == (today + timedelta(days=1)).isoformat()

    def test_next_due_date_weekly(self):
        from datetime import date, timedelta
        today = date.today()
        t = Todo(id=1, title="T", due_date=today.isoformat(), recur="weekly")
        assert t.next_due_date() == (today + timedelta(weeks=1)).isoformat()

    def test_next_due_date_monthly(self):
        t = Todo(id=1, title="T", due_date="2024-01-15", recur="monthly")
        assert t.next_due_date() == "2024-02-15"

    def test_next_due_date_monthly_end_of_month(self):
        """Jan 31 + 1 month should not crash (Feb has no 31st)."""
        t = Todo(id=1, title="T", due_date="2024-01-31", recur="monthly")
        result = t.next_due_date()
        assert result is not None
        assert result.startswith("2024-02-")

    def test_next_due_date_none_without_recur(self):
        t = Todo(id=1, title="T", due_date="2024-01-01")
        assert t.next_due_date() is None


# ---------------------------------------------------------------------------
# Storage – new operations
# ---------------------------------------------------------------------------

class TestStorageEdit:
    def test_edit_title(self, todo_file: Path):
        t = storage.add("Old title", path=todo_file)
        result = storage.edit(t.id, title="New title", path=todo_file)
        assert result is not None
        assert result.title == "New title"
        assert storage.load(todo_file)[0].title == "New title"

    def test_edit_due_date(self, todo_file: Path):
        t = storage.add("Task", path=todo_file)
        storage.edit(t.id, due_date="2030-06-01", path=todo_file)
        assert storage.load(todo_file)[0].due_date == "2030-06-01"

    def test_edit_clear_due_date(self, todo_file: Path):
        t = storage.add("Task", due_date="2030-01-01", path=todo_file)
        storage.edit(t.id, due_date=None, path=todo_file)
        assert storage.load(todo_file)[0].due_date is None

    def test_edit_priority(self, todo_file: Path):
        t = storage.add("Task", path=todo_file)
        storage.edit(t.id, priority="high", path=todo_file)
        assert storage.load(todo_file)[0].priority == "high"

    def test_edit_tags(self, todo_file: Path):
        t = storage.add("Task", path=todo_file)
        storage.edit(t.id, tags=["work", "urgent"], path=todo_file)
        assert storage.load(todo_file)[0].tags == ["work", "urgent"]

    def test_edit_notes(self, todo_file: Path):
        t = storage.add("Task", path=todo_file)
        storage.edit(t.id, notes="Detailed notes", path=todo_file)
        assert storage.load(todo_file)[0].notes == "Detailed notes"

    def test_edit_unknown_id_returns_none(self, todo_file: Path):
        assert storage.edit(999, title="X", path=todo_file) is None

    def test_edit_recur_then_clear(self, todo_file: Path):
        t = storage.add("Task", path=todo_file)
        storage.edit(t.id, recur="weekly", path=todo_file)
        assert storage.load(todo_file)[0].recur == "weekly"
        storage.edit(t.id, recur=None, path=todo_file)
        assert storage.load(todo_file)[0].recur is None


class TestStorageMove:
    def test_move_to_front(self, todo_file: Path):
        storage.add("A", path=todo_file)
        t2 = storage.add("B", path=todo_file)
        storage.move(t2.id, 0, path=todo_file)
        assert storage.load(todo_file)[0].title == "B"

    def test_move_to_end(self, todo_file: Path):
        t1 = storage.add("A", path=todo_file)
        storage.add("B", path=todo_file)
        storage.move(t1.id, 99, path=todo_file)   # clamped to last
        assert storage.load(todo_file)[-1].title == "A"

    def test_move_unknown_id_returns_false(self, todo_file: Path):
        assert not storage.move(999, 0, path=todo_file)


class TestStorageBackupRestore:
    def test_backup_creates_file(self, todo_file: Path, tmp_path: Path):
        storage.add("Task", path=todo_file)
        dest = tmp_path / "backup.json"
        assert storage.backup(dest, src_path=todo_file)
        assert dest.exists()

    def test_backup_fails_when_no_source(self, tmp_path: Path):
        missing = tmp_path / "nonexistent.json"
        dest    = tmp_path / "backup.json"
        assert not storage.backup(dest, src_path=missing)

    def test_restore_overwrites(self, todo_file: Path, tmp_path: Path):
        storage.add("Original", path=todo_file)
        backup = tmp_path / "backup.json"
        storage.backup(backup, src_path=todo_file)
        # Now add something and restore
        storage.add("Extra", path=todo_file)
        assert len(storage.load(todo_file)) == 2
        storage.restore(backup, dest_path=todo_file)
        assert len(storage.load(todo_file)) == 1
        assert storage.load(todo_file)[0].title == "Original"

    def test_restore_fails_when_no_source(self, todo_file: Path, tmp_path: Path):
        assert not storage.restore(tmp_path / "missing.json", dest_path=todo_file)


class TestStorageFilter:
    @pytest.fixture
    def populated(self, todo_file: Path) -> Path:
        from datetime import date, timedelta
        storage.add("Pending high",  priority="high",   path=todo_file)
        storage.add("Pending low",   priority="low",    path=todo_file)
        t3 = storage.add("Done task", path=todo_file)
        storage.mark_done(t3.id, path=todo_file)
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        storage.add("Overdue",       due_date=yesterday, path=todo_file)
        storage.add("Tagged work",   tags=["work"],     path=todo_file)
        return todo_file

    def test_filter_pending(self, populated: Path):
        todos = storage.load(populated)
        r = storage.filter_todos(todos, filter_by="pending")
        assert all(not t.done for t in r)

    def test_filter_done(self, populated: Path):
        todos = storage.load(populated)
        r = storage.filter_todos(todos, filter_by="done")
        assert all(t.done for t in r)

    def test_filter_overdue(self, populated: Path):
        todos = storage.load(populated)
        r = storage.filter_todos(todos, filter_by="overdue")
        assert all(t.is_overdue for t in r)

    def test_filter_by_tag(self, populated: Path):
        todos = storage.load(populated)
        r = storage.filter_todos(todos, tag="work")
        assert all("work" in t.tags for t in r)

    def test_filter_by_priority(self, populated: Path):
        todos = storage.load(populated)
        r = storage.filter_todos(todos, priority="high")
        assert all(t.priority == "high" for t in r)

    def test_search_title(self, populated: Path):
        todos = storage.load(populated)
        r = storage.filter_todos(todos, search="overdue")
        assert len(r) == 1
        assert "overdue" in r[0].title.lower()

    def test_sort_alpha(self, populated: Path):
        todos = storage.load(populated)
        r = storage.filter_todos(todos, sort_by="alpha")
        titles = [t.title for t in r]
        assert titles == sorted(titles, key=str.lower)

    def test_sort_priority(self, populated: Path):
        todos = storage.load(populated)
        r = storage.filter_todos(todos, filter_by="pending", sort_by="priority")
        # high should come before low
        priorities = [t.priority for t in r if t.priority in ("high", "low")]
        assert priorities.index("high") < priorities.index("low")


class TestRecurring:
    def test_mark_done_spawns_next(self, todo_file: Path):
        from datetime import date, timedelta
        today = date.today().isoformat()
        t = storage.add("Daily standup", due_date=today, recur="daily", path=todo_file)
        storage.mark_done(t.id, path=todo_file)
        todos = storage.load(todo_file)
        pending = [x for x in todos if not x.done]
        assert len(pending) == 1
        expected = (date.today() + timedelta(days=1)).isoformat()
        assert pending[0].due_date == expected
        assert pending[0].recur == "daily"
        assert pending[0].title == "Daily standup"

    def test_non_recurring_does_not_spawn(self, todo_file: Path):
        t = storage.add("One-off", path=todo_file)
        storage.mark_done(t.id, path=todo_file)
        todos = storage.load(todo_file)
        assert len(todos) == 1


# ---------------------------------------------------------------------------
# CLI – new commands
# ---------------------------------------------------------------------------

class TestCLIEdit:
    def _run(self, args, todo_file):
        import io
        from contextlib import redirect_stdout
        from todo.cli import build_parser
        parser = build_parser()
        parsed = parser.parse_args(["--file", str(todo_file)] + args)
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = parsed.func(parsed)
        return code, buf.getvalue()

    def test_edit_title(self, todo_file: Path):
        storage.add("Old", path=todo_file)
        code, out = self._run(["edit", "1", "--title", "New"], todo_file)
        assert code == 0
        assert "New" in out
        assert storage.load(todo_file)[0].title == "New"

    def test_edit_priority(self, todo_file: Path):
        storage.add("Task", path=todo_file)
        self._run(["edit", "1", "--priority", "high"], todo_file)
        assert storage.load(todo_file)[0].priority == "high"

    def test_edit_due(self, todo_file: Path):
        storage.add("Task", path=todo_file)
        self._run(["edit", "1", "--due", "2030-12-31"], todo_file)
        assert storage.load(todo_file)[0].due_date == "2030-12-31"

    def test_edit_clear_due(self, todo_file: Path):
        storage.add("Task", due_date="2030-01-01", path=todo_file)
        self._run(["edit", "1", "--clear-due"], todo_file)
        assert storage.load(todo_file)[0].due_date is None

    def test_edit_tags(self, todo_file: Path):
        storage.add("Task", path=todo_file)
        self._run(["edit", "1", "--tags", "work,urgent"], todo_file)
        assert storage.load(todo_file)[0].tags == ["work", "urgent"]

    def test_edit_unknown_fails(self, todo_file: Path):
        import io
        from contextlib import redirect_stderr
        from todo.cli import build_parser
        parser = build_parser()
        parsed = parser.parse_args(["--file", str(todo_file), "edit", "99", "--title", "X"])
        buf = io.StringIO()
        with redirect_stderr(buf):
            code = parsed.func(parsed)
        assert code == 1


class TestCLIMove:
    def _run(self, args, todo_file):
        import io
        from contextlib import redirect_stdout
        from todo.cli import build_parser
        parser = build_parser()
        parsed = parser.parse_args(["--file", str(todo_file)] + args)
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = parsed.func(parsed)
        return code, buf.getvalue()

    def test_move_to_position_1(self, todo_file: Path):
        storage.add("A", path=todo_file)
        storage.add("B", path=todo_file)
        code, _ = self._run(["move", "2", "1"], todo_file)
        assert code == 0
        assert storage.load(todo_file)[0].title == "B"


class TestCLIListFilters:
    def _run(self, args, todo_file):
        import io
        from contextlib import redirect_stdout
        from todo.cli import build_parser
        parser = build_parser()
        parsed = parser.parse_args(["--file", str(todo_file)] + args)
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = parsed.func(parsed)
        return code, buf.getvalue()

    def test_list_filter_pending(self, todo_file: Path):
        t = storage.add("A", path=todo_file)
        storage.add("B", path=todo_file)
        storage.mark_done(t.id, path=todo_file)
        code, out = self._run(["list", "--filter", "pending"], todo_file)
        assert code == 0
        assert "B" in out
        assert "✓" not in out

    def test_list_search(self, todo_file: Path):
        storage.add("Buy milk", path=todo_file)
        storage.add("Walk dog", path=todo_file)
        code, out = self._run(["list", "--search", "milk"], todo_file)
        assert code == 0
        assert "milk" in out.lower()
        assert "dog" not in out.lower()

    def test_add_with_priority_and_tags(self, todo_file: Path):
        code, out = self._run(
            ["add", "Important", "--priority", "high", "--tags", "work,urgent"],
            todo_file,
        )
        assert code == 0
        t = storage.load(todo_file)[0]
        assert t.priority == "high"
        assert "work" in t.tags


class TestCLIBackupRestore:
    def _run(self, args, todo_file):
        import io
        from contextlib import redirect_stdout
        from todo.cli import build_parser
        parser = build_parser()
        parsed = parser.parse_args(["--file", str(todo_file)] + args)
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = parsed.func(parsed)
        return code, buf.getvalue()

    def test_backup_and_restore(self, todo_file: Path, tmp_path: Path):
        storage.add("Keep me", path=todo_file)
        backup = str(tmp_path / "backup.json")
        code, _ = self._run(["backup", backup], todo_file)
        assert code == 0

        storage.add("Discard me", path=todo_file)
        assert len(storage.load(todo_file)) == 2

        code, _ = self._run(["restore", backup], todo_file)
        assert code == 0
        assert len(storage.load(todo_file)) == 1
        assert storage.load(todo_file)[0].title == "Keep me"



# ===========================================================================
# TestTodoRepository — tests that use the class-based API directly
# ===========================================================================

class TestTodoRepository:
    """Verify that ``TodoRepository`` provides the same contract as the free
    functions while eliminating the repeated ``path=`` argument.

    These tests run against a fresh temp-file repository (``repo`` fixture) so
    they are fully isolated from the real ``~/.todo-cli/todos.json`` file.
    """

    def test_add_and_load(self, todo_file):
        repo = TodoRepository(todo_file)
        todo = repo.add("Repo task")
        assert todo.id == 1
        loaded = repo.load()
        assert len(loaded) == 1
        assert loaded[0].title == "Repo task"

    def test_add_with_options(self, todo_file):
        repo = TodoRepository(todo_file)
        todo = repo.add("Repo task", priority="high", tags=["work"], due_date="2030-01-01")
        assert todo.priority == "high"
        assert todo.tags == ["work"]
        assert todo.due_date == "2030-01-01"

    def test_mark_done(self, todo_file):
        repo = TodoRepository(todo_file)
        t    = repo.add("Task")
        done = repo.mark_done(t.id)
        assert done.done is True
        assert done.completed_at is not None
        assert repo.load()[0].done is True

    def test_mark_done_unknown_id(self, todo_file):
        repo = TodoRepository(todo_file)
        assert repo.mark_done(999) is None

    def test_delete(self, todo_file):
        repo = TodoRepository(todo_file)
        t    = repo.add("Task")
        deleted = repo.delete(t.id)
        assert deleted.id == t.id
        assert repo.load() == []

    def test_delete_unknown_id(self, todo_file):
        repo = TodoRepository(todo_file)
        assert repo.delete(999) is None

    def test_edit(self, todo_file):
        repo = TodoRepository(todo_file)
        t    = repo.add("Old title")
        updated = repo.edit(t.id, title="New title", priority="high")
        assert updated.title == "New title"
        assert updated.priority == "high"
        assert repo.load()[0].title == "New title"

    def test_edit_clear_due(self, todo_file):
        repo = TodoRepository(todo_file)
        t    = repo.add("Task", due_date="2030-01-01")
        repo.edit(t.id, due_date=None)
        assert repo.load()[0].due_date is None

    def test_edit_unknown_id(self, todo_file):
        repo = TodoRepository(todo_file)
        assert repo.edit(999, title="X") is None

    def test_move(self, todo_file):
        repo = TodoRepository(todo_file)
        a = repo.add("A")
        b = repo.add("B")
        c = repo.add("C")
        repo.move(a.id, 2)   # move A to position index 2 (last)
        titles = [t.title for t in repo.load()]
        assert titles == ["B", "C", "A"]

    def test_move_unknown_id(self, todo_file):
        repo = TodoRepository(todo_file)
        assert repo.move(999, 0) is False

    def test_snapshot_and_restore(self, todo_file):
        repo     = TodoRepository(todo_file)
        repo.add("Before")
        snapshot = repo.snapshot()
        assert len(snapshot) == 1

        repo.add("After")
        assert len(repo.load()) == 2

        repo.restore_snapshot(snapshot)
        assert len(repo.load()) == 1
        assert repo.load()[0].title == "Before"

    def test_export_json(self, todo_file):
        repo = TodoRepository(todo_file)
        repo.add("Export me")
        j    = repo.export_json()
        data = json.loads(j)
        assert len(data) == 1
        assert data[0]["title"] == "Export me"

    def test_backup_and_restore(self, todo_file, tmp_path):
        repo   = TodoRepository(todo_file)
        repo.add("Persisted")
        backup = tmp_path / "backup.json"
        assert repo.backup(backup) is True
        assert backup.exists()

        repo.delete(repo.load()[0].id)
        assert repo.load() == []

        repo.restore(backup)
        assert repo.load()[0].title == "Persisted"

    def test_backup_missing_source(self, tmp_path):
        """backup() returns False when the source file does not exist."""
        repo = TodoRepository(tmp_path / "nonexistent.json")
        assert repo.backup(tmp_path / "out.json") is False

    def test_restore_missing_source(self, todo_file, tmp_path):
        """restore() returns False when the backup file does not exist."""
        repo = TodoRepository(todo_file)
        assert repo.restore(tmp_path / "nonexistent.json") is False

    def test_recurring_spawns_next(self, todo_file):
        """mark_done on a recurring todo should append the next occurrence."""
        repo = TodoRepository(todo_file)
        t    = repo.add("Weekly", recur="weekly", due_date="2030-01-06")
        repo.mark_done(t.id)
        todos = repo.load()
        assert len(todos) == 2
        next_t = todos[1]
        assert next_t.done is False
        assert next_t.due_date == "2030-01-13"


# ===========================================================================
# TestFilterOptions — tests for the queries module
# ===========================================================================

class TestFilterOptions:
    """Verify ``FilterOptions`` and ``filter_todos`` from ``todo.queries``.

    All tests operate on in-memory lists — no filesystem needed.
    """

    def _make(self, **kwargs) -> Todo:
        """Factory shortcut so test bodies stay concise."""
        return storage.add.__wrapped__(**kwargs) if False else None   # placeholder

    def _todos(self) -> list[Todo]:
        """A small fixed dataset covering all filter dimensions."""
        from todo.models import Todo as T
        return [
            T(id=1, title="Alpha",   done=False, priority="high",   tags=["work"],
              due_date="2020-01-01"),   # overdue
            T(id=2, title="Beta",    done=True,  priority="medium",  tags=["home"]),
            T(id=3, title="Gamma",   done=False, priority="low",    tags=["work", "urgent"]),
            T(id=4, title="Delta",   done=False, priority="high",   tags=[],
              due_date="2099-12-31"),   # future
            T(id=5, title="epsilon", done=False, priority="medium",  tags=[]),
        ]

    def test_no_options_returns_all(self):
        todos = self._todos()
        assert filter_todos(todos) == todos

    def test_filter_pending(self):
        result = filter_todos(self._todos(), FilterOptions(filter_by="pending"))
        assert all(not t.done for t in result)
        assert len(result) == 4

    def test_filter_done(self):
        result = filter_todos(self._todos(), FilterOptions(filter_by="done"))
        assert all(t.done for t in result)
        assert len(result) == 1

    def test_filter_overdue(self):
        result = filter_todos(self._todos(), FilterOptions(filter_by="overdue"))
        # Only id=1 has an overdue date (2020-01-01) and is not done.
        assert len(result) == 1
        assert result[0].id == 1

    def test_tag_filter(self):
        result = filter_todos(self._todos(), FilterOptions(tag="work"))
        assert all("work" in t.tags for t in result)
        assert len(result) == 2

    def test_priority_filter(self):
        result = filter_todos(self._todos(), FilterOptions(priority="high"))
        assert all(t.priority == "high" for t in result)
        assert len(result) == 2

    def test_search_title(self):
        result = filter_todos(self._todos(), FilterOptions(search="alpha"))
        assert len(result) == 1
        assert result[0].title == "Alpha"

    def test_search_case_insensitive(self):
        result = filter_todos(self._todos(), FilterOptions(search="EPSILON"))
        assert len(result) == 1

    def test_search_tag(self):
        result = filter_todos(self._todos(), FilterOptions(search="urgent"))
        assert len(result) == 1
        assert result[0].id == 3

    def test_sort_alpha(self):
        titles = [t.title for t in filter_todos(
            self._todos(), FilterOptions(sort_by="alpha")
        )]
        assert titles == sorted(titles, key=str.lower)

    def test_sort_priority(self):
        result = filter_todos(self._todos(), FilterOptions(sort_by="priority"))
        pris   = [t.priority for t in result]
        rank   = {"high": 0, "medium": 1, "low": 2}
        assert pris == sorted(pris, key=lambda p: rank[p])

    def test_sort_due_undated_last(self):
        result = filter_todos(self._todos(), FilterOptions(sort_by="due"))
        # Todos with due dates come before undated ones.
        dated_indices   = [i for i, t in enumerate(result) if t.due_date]
        undated_indices = [i for i, t in enumerate(result) if not t.due_date]
        assert max(dated_indices) < min(undated_indices)

    def test_sort_position_unchanged(self):
        todos  = self._todos()
        result = filter_todos(todos, FilterOptions(sort_by="position"))
        assert [t.id for t in result] == [t.id for t in todos]

    def test_combined_filter_and_sort(self):
        result = filter_todos(
            self._todos(),
            FilterOptions(filter_by="pending", sort_by="alpha"),
        )
        assert all(not t.done for t in result)
        titles = [t.title for t in result]
        assert titles == sorted(titles, key=str.lower)

    def test_no_mutation(self):
        """filter_todos must not mutate the original list."""
        todos    = self._todos()
        original = [t.id for t in todos]
        filter_todos(todos, FilterOptions(sort_by="alpha", filter_by="pending"))
        assert [t.id for t in todos] == original
