"""Pure query layer for todo-cli.

All functions and classes in this module operate exclusively on in-memory
``list[Todo]`` values — no filesystem I/O happens here.  This makes them
fast, trivially testable (no tmp_path fixtures needed), and safe to call
from any layer (storage, CLI, UI, tests) without side effects.

Why a separate module?
----------------------
Previously ``filter_todos`` lived in ``storage.py``.  That was a wrong home:
it took no ``Path``, did no I/O, and was a pure transformation of domain
objects.  Moving it here respects the Single Responsibility Principle and
makes the dependency graph cleaner — UI and CLI can import queries without
importing storage.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .models import Priority, Todo


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Numeric rank used when sorting by priority (lower = shown first).
_PRIORITY_RANK: dict[str, int] = {
    Priority.HIGH:   0,
    Priority.MEDIUM: 1,
    Priority.LOW:    2,
}

# Sentinel sort key for todos that have no due date.
# ISO-8601 date strings sort lexicographically, so this value ensures that
# undated todos sort after all real dates.
_NO_DUE_DATE = "9999-99-99"


# ---------------------------------------------------------------------------
# Parameter object  (replaces the long keyword-argument list)
# ---------------------------------------------------------------------------

@dataclass
class FilterOptions:
    """Encapsulates all filter + sort parameters as a single value object.

    Passing a ``FilterOptions`` instead of five separate keyword arguments
    avoids the "long parameter list" smell, allows the object to be stored
    and compared, and makes it easy to add new options in the future without
    changing every call site.

    Attributes
    ----------
    filter_by : Restrict visible todos by status.
                ``"all"``     — show everything (default)
                ``"pending"`` — only incomplete todos
                ``"done"``    — only completed todos
                ``"overdue"`` — only overdue todos (implies pending)
    tag       : If set, keep only todos that carry this exact tag string.
    priority  : If set, keep only todos at this priority level.
    search    : Case-insensitive substring matched against title, notes, and
                tag labels.  A todo is kept if *any* field matches.
    sort_by   : Ordering applied after all filters.
                ``"position"`` — preserve JSON file order (default)
                ``"due"``      — ascending due date, undated last
                ``"priority"`` — high → medium → low
                ``"alpha"``    — case-insensitive alphabetical by title
                ``"created"``  — ascending creation timestamp
    """
    filter_by: str        = "all"
    tag:       str | None = None
    priority:  str | None = None
    search:    str | None = None
    sort_by:   str        = "position"


# ---------------------------------------------------------------------------
# Main query function
# ---------------------------------------------------------------------------

def filter_todos(
    todos: list[Todo],
    options: FilterOptions | None = None,
) -> list[Todo]:
    """Return a filtered and sorted *copy* of ``todos``.

    The original list is never mutated.  Pass a ``FilterOptions`` to control
    which todos are kept and in what order; omit it (or pass ``None``) to
    receive a plain copy in the original file order.

    Filter pipeline
    ---------------
    Each step operates on the output of the previous one, so they compose:

    1. **Status filter**   — keep only todos matching the requested status
    2. **Tag filter**      — narrow to todos carrying a specific tag
    3. **Priority filter** — narrow to todos at a specific priority level
    4. **Text search**     — free-text match across title, notes, and tags
    5. **Sort**            — reorder the surviving todos

    Examples::

        # All pending todos sorted by due date
        opts = FilterOptions(filter_by="pending", sort_by="due")
        visible = filter_todos(all_todos, opts)

        # Full-text search, no other filtering
        opts = FilterOptions(search="milk")
        matches = filter_todos(all_todos, opts)
    """
    if options is None:
        options = FilterOptions()

    result = list(todos)   # work on a copy; never mutate the input

    # ── Step 1: status filter ──────────────────────────────────────────────
    if options.filter_by == "pending":
        result = [t for t in result if not t.done]
    elif options.filter_by == "done":
        result = [t for t in result if t.done]
    elif options.filter_by == "overdue":
        # ``is_overdue`` already returns False for done todos, so no extra
        # "not done" guard is needed here.
        result = [t for t in result if t.is_overdue]

    # ── Step 2: tag filter ─────────────────────────────────────────────────
    if options.tag:
        result = [t for t in result if options.tag in t.tags]

    # ── Step 3: priority filter ────────────────────────────────────────────
    if options.priority:
        result = [t for t in result if t.priority == options.priority]

    # ── Step 4: full-text search ───────────────────────────────────────────
    if options.search:
        needle = options.search.lower()
        result = [
            t for t in result
            if needle in t.title.lower()
            or needle in t.notes.lower()
            or any(needle in tag.lower() for tag in t.tags)
        ]

    # ── Step 5: sort ──────────────────────────────────────────────────────
    _apply_sort(result, options.sort_by)

    return result


def _apply_sort(todos: list[Todo], sort_by: str) -> None:
    """Sort ``todos`` **in-place** by the requested criterion.

    ``"position"`` is deliberately a no-op — it preserves whatever order the
    caller provided, which is typically the JSON file order.
    """
    if sort_by == "due":
        # Stable secondary key (id) keeps equal-due todos in their original
        # relative order, which is less surprising than a random shuffle.
        todos.sort(key=lambda t: (t.due_date or _NO_DUE_DATE, t.id))
    elif sort_by == "priority":
        todos.sort(key=lambda t: (_PRIORITY_RANK.get(t.priority, 1), t.id))
    elif sort_by == "alpha":
        todos.sort(key=lambda t: t.title.lower())
    elif sort_by == "created":
        todos.sort(key=lambda t: t.created_at)
    # "position" → nothing to do; list is already in file order.
