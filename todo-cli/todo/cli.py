"""Command-line interface for todo-cli.

This module is the *outermost* layer of the application that touches the
terminal.  It is responsible for:

* Parsing command-line arguments (via ``argparse``).
* Translating parsed arguments into calls on ``TodoRepository``.
* Formatting and printing results to stdout.
* Returning an exit code (0 = success, non-zero = error).

Architecture note
-----------------
Each command is a small function that follows the same pattern:

    1. Resolve the todos file path.
    2. Create a ``TodoRepository`` for that path.
    3. Delegate all business logic to the repository (or ``filter_todos``).
    4. Print a human-readable result.
    5. Return an integer exit code.

This keeps the functions thin and easy to test: callers can pass a
``tmp_path`` via ``--file`` and inspect the exit code and captured output.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .models  import PRIORITIES, RECUR_PERIODS
from .queries import FilterOptions, filter_todos
from .storage import DEFAULT_PATH, TodoRepository


# ---------------------------------------------------------------------------
# Path helper
# ---------------------------------------------------------------------------

def _get_path(args: argparse.Namespace) -> Path:
    """Return the todos file path from args, falling back to ``DEFAULT_PATH``.

    The ``--file``/``-f`` flag lets users maintain multiple independent lists
    (work, personal, etc.) without changing environment variables.
    """
    return Path(args.file) if args.file else DEFAULT_PATH


# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------

def cmd_add(args: argparse.Namespace) -> int:
    """Handle the ``todo add`` command.

    Joins multi-word title tokens (argparse splits on spaces when ``nargs="+"``
    is used) and passes all optional fields straight through to the repository.
    """
    title = " ".join(args.title)
    if not title.strip():
        print("Error: title cannot be empty.", file=sys.stderr)
        return 1

    # Split the comma-separated tag string into a clean list, stripping
    # whitespace from each tag so "work, home" and "work,home" are equivalent.
    tags = [t.strip() for t in args.tags.split(",")] if args.tags else []

    repo = TodoRepository(_get_path(args))
    todo = repo.add(
        title,
        due_date= args.due,
        priority= args.priority,
        notes=    args.notes or "",
        tags=     tags,
        recur=    args.recur,
    )
    print(f"Added [{todo.id}] {todo.title}")
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    """Handle the ``todo list`` command.

    Builds a ``FilterOptions`` parameter object from the parsed flags and
    delegates filtering + sorting to the pure ``filter_todos`` function in the
    query layer.  This keeps the I/O (loading from disk) separate from the
    filtering logic (pure in-memory transformation).
    """
    repo = TodoRepository(_get_path(args))
    todos = repo.load()

    # Construct a FilterOptions value object rather than threading five
    # keyword arguments through multiple function calls.
    options = FilterOptions(
        filter_by= args.filter,
        tag=       args.tag,
        priority=  getattr(args, "priority", None),
        search=    args.search,
        sort_by=   args.sort,
    )
    filtered = filter_todos(todos, options)

    if not filtered:
        print(
            "No todos match the current filter." if todos else
            "No todos yet. Use `todo add <title>` to create one."
        )
        return 0

    for todo in filtered:
        # Compose a single-line display string from the todo's fields.
        status = "✓" if todo.done else ("⚠" if todo.is_overdue else "○")
        pri    = f" [{todo.priority}]" if todo.priority != "medium" else ""
        due    = f"  📅 {todo.due_date}"                    if todo.due_date else ""
        tags   = ("  " + " ".join(f"#{t}" for t in todo.tags)) if todo.tags  else ""
        recur  = f"  ↻{todo.recur}"                         if todo.recur    else ""
        notes  = "  📝"                                      if todo.notes   else ""
        print(f"  {status} [{todo.id}] {todo.title}{pri}{due}{tags}{recur}{notes}")
    return 0


def cmd_done(args: argparse.Namespace) -> int:
    """Handle ``todo done <id>``.

    If the todo has a recurrence cadence, the repository automatically spawns
    the next occurrence.  We report the next due date here so the user knows
    when to expect the task again.
    """
    repo = TodoRepository(_get_path(args))
    todo = repo.mark_done(args.id)
    if todo is None:
        print(f"Error: no todo with id {args.id}.", file=sys.stderr)
        return 1
    msg = f"Done  [{todo.id}] {todo.title}"
    if todo.recur:
        msg += f"  (next: {todo.next_due_date() or 'N/A'})"
    print(msg)
    return 0


def cmd_edit(args: argparse.Namespace) -> int:
    """Handle ``todo edit <id> [--field value ...]``.

    Only explicitly supplied flags are forwarded so that omitted flags leave
    the existing field values untouched.  ``--clear-due`` and ``--clear-recur``
    are sentinel flags that set the field to ``None`` (distinguishing "not
    supplied" from "explicitly cleared").
    """
    repo = TodoRepository(_get_path(args))

    # Build a dict of only the fields the user actually asked to change.
    # We cannot just pass all args because omitted optional flags have the
    # value None, which would overwrite good data with null.
    kwargs: dict = {}
    if args.title:
        kwargs["title"] = " ".join(args.title)
    if args.clear_due:
        kwargs["due_date"] = None          # explicitly clear the field
    elif args.due is not None:
        kwargs["due_date"] = args.due
    if args.priority:
        kwargs["priority"] = args.priority
    if args.notes is not None:
        kwargs["notes"] = args.notes
    if args.tags is not None:
        kwargs["tags"] = [t.strip() for t in args.tags.split(",") if t.strip()]
    if args.clear_recur:
        kwargs["recur"] = None             # explicitly clear the field
    elif args.recur is not None:
        kwargs["recur"] = args.recur or None

    todo = repo.edit(args.id, **kwargs)
    if todo is None:
        print(f"Error: no todo with id {args.id}.", file=sys.stderr)
        return 1
    print(f"Updated [{todo.id}] {todo.title}")
    return 0


def cmd_delete(args: argparse.Namespace) -> int:
    """Handle ``todo delete <id>``."""
    repo = TodoRepository(_get_path(args))
    todo = repo.delete(args.id)
    if todo is None:
        print(f"Error: no todo with id {args.id}.", file=sys.stderr)
        return 1
    print(f"Deleted [{todo.id}] {todo.title}")
    return 0


def cmd_move(args: argparse.Namespace) -> int:
    """Handle ``todo move <id> <position>``.

    The user supplies a 1-based position; we convert to 0-based before
    passing to the repository (Python lists are 0-indexed).
    """
    repo = TodoRepository(_get_path(args))
    ok   = repo.move(args.id, args.position - 1)
    if not ok:
        print(f"Error: no todo with id {args.id}.", file=sys.stderr)
        return 1
    print(f"Moved [{args.id}] to position {args.position}.")
    return 0


def cmd_export(args: argparse.Namespace) -> int:
    """Handle ``todo export [--output FILE]``.

    Without ``--output`` the JSON is written to stdout so it can be piped to
    other tools.  With ``--output`` it is written to a file.
    """
    repo     = TodoRepository(_get_path(args))
    json_str = repo.export_json()
    if args.output:
        Path(args.output).write_text(json_str, encoding="utf-8")
        print(f"Exported to {args.output}")
    else:
        print(json_str)
    return 0


def cmd_backup(args: argparse.Namespace) -> int:
    """Handle ``todo backup [dest]``.

    Defaults to placing the backup next to the source file with a
    ``.backup.json`` suffix if no destination is supplied.
    """
    src  = _get_path(args)
    dest = Path(args.dest) if args.dest else src.with_suffix(".backup.json")
    if not TodoRepository(src).backup(dest):
        print(f"Error: no todos file at {src}.", file=sys.stderr)
        return 1
    print(f"Backed up to {dest}")
    return 0


def cmd_restore(args: argparse.Namespace) -> int:
    """Handle ``todo restore <src>``."""
    src  = Path(args.src)
    dest = _get_path(args)
    if not TodoRepository(dest).restore(src):
        print(f"Error: backup not found at {src}.", file=sys.stderr)
        return 1
    print(f"Restored from {src}")
    return 0


# ---------------------------------------------------------------------------
# Argument parser
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    """Construct and return the root ``argparse.ArgumentParser``.

    Each sub-command has its own parser (``sub.add_parser(...)``).  The
    ``set_defaults(func=cmd_*)`` pattern lets ``main()`` dispatch without a
    long if/elif chain.
    """
    parser = argparse.ArgumentParser(
        prog="todo",
        description="A tiny CLI todo manager.",
    )
    # Global flag: lets the user point at an alternative todos file.
    parser.add_argument(
        "--file", "-f", metavar="PATH",
        help="Path to the todos JSON file (overrides $TODO_FILE).",
    )

    sub          = parser.add_subparsers(dest="command", metavar="<command>")
    sub.required = True

    # ── add ──────────────────────────────────────────────────────────────
    p_add = sub.add_parser("add", help="Add a new todo.")
    p_add.add_argument("title", nargs="+",
                       help="Todo title (multi-word, no quotes needed).")
    p_add.add_argument("--due",      metavar="YYYY-MM-DD", help="Due date.")
    p_add.add_argument("--priority", choices=PRIORITIES, default="medium")
    p_add.add_argument("--notes",    help="Longer description.")
    p_add.add_argument("--tags",     metavar="TAG1,TAG2", help="Comma-separated tags.")
    p_add.add_argument("--recur",    choices=RECUR_PERIODS, help="Recurrence cadence.")
    p_add.set_defaults(func=cmd_add)

    # ── list ─────────────────────────────────────────────────────────────
    p_list = sub.add_parser("list", help="List todos.")
    p_list.add_argument("--filter",
                        choices=["all", "pending", "done", "overdue"],
                        default="all")
    p_list.add_argument("--tag",      help="Show only todos with this tag.")
    p_list.add_argument("--priority", choices=PRIORITIES)
    p_list.add_argument("--sort",
                        choices=["position", "due", "priority", "alpha", "created"],
                        default="position")
    p_list.add_argument("--search",   metavar="TEXT",
                        help="Case-insensitive text search across title/notes/tags.")
    p_list.set_defaults(func=cmd_list)

    # ── done ─────────────────────────────────────────────────────────────
    p_done = sub.add_parser("done", help="Mark a todo as done.")
    p_done.add_argument("id", type=int)
    p_done.set_defaults(func=cmd_done)

    # ── edit ─────────────────────────────────────────────────────────────
    p_edit = sub.add_parser("edit", help="Edit a todo.")
    p_edit.add_argument("id", type=int)
    p_edit.add_argument("--title",       nargs="+")
    p_edit.add_argument("--due",         metavar="YYYY-MM-DD")
    p_edit.add_argument("--clear-due",   action="store_true",
                        help="Remove the due date entirely.")
    p_edit.add_argument("--priority",    choices=PRIORITIES)
    p_edit.add_argument("--notes")
    p_edit.add_argument("--tags",        metavar="TAG1,TAG2")
    p_edit.add_argument("--recur",       choices=[*RECUR_PERIODS, ""])
    p_edit.add_argument("--clear-recur", action="store_true",
                        help="Remove the recurrence setting entirely.")
    p_edit.set_defaults(func=cmd_edit)

    # ── delete ────────────────────────────────────────────────────────────
    p_delete = sub.add_parser("delete", help="Delete a todo.")
    p_delete.add_argument("id", type=int)
    p_delete.set_defaults(func=cmd_delete)

    # ── move ──────────────────────────────────────────────────────────────
    p_move = sub.add_parser("move", help="Move a todo to a new position (1-indexed).")
    p_move.add_argument("id",       type=int)
    p_move.add_argument("position", type=int)
    p_move.set_defaults(func=cmd_move)

    # ── export ────────────────────────────────────────────────────────────
    p_export = sub.add_parser("export", help="Export todos to JSON.")
    p_export.add_argument("--output", "-o", metavar="FILE",
                          help="File to write; defaults to stdout.")
    p_export.set_defaults(func=cmd_export)

    # ── backup ────────────────────────────────────────────────────────────
    p_backup = sub.add_parser("backup", help="Backup todos file.")
    p_backup.add_argument("dest", nargs="?",
                          help="Destination path (default: <todos>.backup.json).")
    p_backup.set_defaults(func=cmd_backup)

    # ── restore ───────────────────────────────────────────────────────────
    p_restore = sub.add_parser("restore", help="Restore todos from a backup file.")
    p_restore.add_argument("src")
    p_restore.set_defaults(func=cmd_restore)

    return parser


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    """Parse arguments, dispatch to the appropriate command handler, and exit."""
    parser = build_parser()
    args   = parser.parse_args()
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
