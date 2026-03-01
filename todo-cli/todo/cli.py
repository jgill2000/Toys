"""CLI entry point for todo-cli."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import storage
from .models import PRIORITIES, RECUR_PERIODS


def _get_path(args: argparse.Namespace) -> Path:
    return Path(args.file) if args.file else storage.DEFAULT_PATH


# ── Commands ──────────────────────────────────────────────────────────────────

def cmd_add(args: argparse.Namespace) -> int:
    title = " ".join(args.title)
    if not title.strip():
        print("Error: title cannot be empty.", file=sys.stderr)
        return 1
    tags = [t.strip() for t in args.tags.split(",")] if args.tags else []
    todo = storage.add(
        title,
        due_date=args.due,
        priority=args.priority,
        notes=args.notes or "",
        tags=tags,
        recur=args.recur,
        path=_get_path(args),
    )
    print(f"Added [{todo.id}] {todo.title}")
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    todos = storage.load(path=_get_path(args))
    filtered = storage.filter_todos(
        todos,
        filter_by=args.filter,
        tag=args.tag,
        priority=getattr(args, "priority", None),
        search=args.search,
        sort_by=args.sort,
    )
    if not filtered:
        print("No todos match the current filter." if todos else
              "No todos yet. Use `todo add <title>` to create one.")
        return 0
    for todo in filtered:
        status  = "✓" if todo.done else ("⚠" if todo.is_overdue else "○")
        pri     = f" [{todo.priority}]" if todo.priority != "medium" else ""
        due     = f"  📅 {todo.due_date}" if todo.due_date else ""
        tags    = ("  " + " ".join(f"#{t}" for t in todo.tags)) if todo.tags else ""
        recur   = f"  ↻{todo.recur}" if todo.recur else ""
        notes   = "  📝" if todo.notes else ""
        print(f"  {status} [{todo.id}] {todo.title}{pri}{due}{tags}{recur}{notes}")
    return 0


def cmd_done(args: argparse.Namespace) -> int:
    todo = storage.mark_done(args.id, path=_get_path(args))
    if todo is None:
        print(f"Error: no todo with id {args.id}.", file=sys.stderr)
        return 1
    msg = f"Done  [{todo.id}] {todo.title}"
    if todo.recur:
        msg += f"  (next: {todo.next_due_date() or 'N/A'})"
    print(msg)
    return 0


def cmd_edit(args: argparse.Namespace) -> int:
    kwargs: dict = {"path": _get_path(args)}
    if args.title:
        kwargs["title"] = " ".join(args.title)
    if args.clear_due:
        kwargs["due_date"] = None
    elif args.due is not None:
        kwargs["due_date"] = args.due
    if args.priority:
        kwargs["priority"] = args.priority
    if args.notes is not None:
        kwargs["notes"] = args.notes
    if args.tags is not None:
        kwargs["tags"] = [t.strip() for t in args.tags.split(",") if t.strip()]
    if args.clear_recur:
        kwargs["recur"] = None
    elif args.recur is not None:
        kwargs["recur"] = args.recur or None

    todo = storage.edit(args.id, **kwargs)
    if todo is None:
        print(f"Error: no todo with id {args.id}.", file=sys.stderr)
        return 1
    print(f"Updated [{todo.id}] {todo.title}")
    return 0


def cmd_delete(args: argparse.Namespace) -> int:
    todo = storage.delete(args.id, path=_get_path(args))
    if todo is None:
        print(f"Error: no todo with id {args.id}.", file=sys.stderr)
        return 1
    print(f"Deleted [{todo.id}] {todo.title}")
    return 0


def cmd_move(args: argparse.Namespace) -> int:
    ok = storage.move(args.id, args.position - 1, path=_get_path(args))
    if not ok:
        print(f"Error: no todo with id {args.id}.", file=sys.stderr)
        return 1
    print(f"Moved [{args.id}] to position {args.position}.")
    return 0


def cmd_export(args: argparse.Namespace) -> int:
    json_str = storage.export_json(path=_get_path(args))
    if args.output:
        Path(args.output).write_text(json_str)
        print(f"Exported to {args.output}")
    else:
        print(json_str)
    return 0


def cmd_backup(args: argparse.Namespace) -> int:
    src  = _get_path(args)
    dest = Path(args.dest) if args.dest else src.with_suffix(".backup.json")
    if not storage.backup(dest, src_path=src):
        print(f"Error: no todos file at {src}.", file=sys.stderr)
        return 1
    print(f"Backed up to {dest}")
    return 0


def cmd_restore(args: argparse.Namespace) -> int:
    src  = Path(args.src)
    dest = _get_path(args)
    if not storage.restore(src, dest_path=dest):
        print(f"Error: backup not found at {src}.", file=sys.stderr)
        return 1
    print(f"Restored from {src}")
    return 0


# ── Parser ────────────────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="todo",
        description="A tiny CLI todo manager.",
    )
    parser.add_argument(
        "--file", "-f", metavar="PATH",
        help="Path to the todos JSON file (overrides $TODO_FILE).",
    )

    sub = parser.add_subparsers(dest="command", metavar="<command>")
    sub.required = True

    # add
    p_add = sub.add_parser("add", help="Add a new todo.")
    p_add.add_argument("title", nargs="+")
    p_add.add_argument("--due",      metavar="YYYY-MM-DD")
    p_add.add_argument("--priority", choices=PRIORITIES, default="medium")
    p_add.add_argument("--notes")
    p_add.add_argument("--tags",     metavar="TAG1,TAG2")
    p_add.add_argument("--recur",    choices=RECUR_PERIODS)
    p_add.set_defaults(func=cmd_add)

    # list
    p_list = sub.add_parser("list", help="List todos.")
    p_list.add_argument("--filter",   choices=["all","pending","done","overdue"], default="all")
    p_list.add_argument("--tag")
    p_list.add_argument("--priority", choices=PRIORITIES)
    p_list.add_argument("--sort",     choices=["position","due","priority","alpha","created"],
                        default="position")
    p_list.add_argument("--search",   metavar="TEXT")
    p_list.set_defaults(func=cmd_list)

    # done
    p_done = sub.add_parser("done", help="Mark a todo as done.")
    p_done.add_argument("id", type=int)
    p_done.set_defaults(func=cmd_done)

    # edit
    p_edit = sub.add_parser("edit", help="Edit a todo.")
    p_edit.add_argument("id", type=int)
    p_edit.add_argument("--title",      nargs="+")
    p_edit.add_argument("--due",        metavar="YYYY-MM-DD")
    p_edit.add_argument("--clear-due",  action="store_true")
    p_edit.add_argument("--priority",   choices=PRIORITIES)
    p_edit.add_argument("--notes")
    p_edit.add_argument("--tags",       metavar="TAG1,TAG2")
    p_edit.add_argument("--recur",      choices=[*RECUR_PERIODS, ""])
    p_edit.add_argument("--clear-recur", action="store_true")
    p_edit.set_defaults(func=cmd_edit)

    # delete
    p_delete = sub.add_parser("delete", help="Delete a todo.")
    p_delete.add_argument("id", type=int)
    p_delete.set_defaults(func=cmd_delete)

    # move
    p_move = sub.add_parser("move", help="Move a todo to a new position (1-indexed).")
    p_move.add_argument("id",       type=int)
    p_move.add_argument("position", type=int)
    p_move.set_defaults(func=cmd_move)

    # export
    p_export = sub.add_parser("export", help="Export todos to JSON.")
    p_export.add_argument("--output", "-o", metavar="FILE")
    p_export.set_defaults(func=cmd_export)

    # backup
    p_backup = sub.add_parser("backup", help="Backup todos file.")
    p_backup.add_argument("dest", nargs="?",
                          help="Destination path (default: <todos>.backup.json).")
    p_backup.set_defaults(func=cmd_backup)

    # restore
    p_restore = sub.add_parser("restore", help="Restore todos from a backup file.")
    p_restore.add_argument("src")
    p_restore.set_defaults(func=cmd_restore)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
