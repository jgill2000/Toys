"""Core domain model for todo-cli.

This module is the innermost layer of the application — it contains
*only* value types and pure logic.  No filesystem I/O, no terminal output,
no Tkinter imports.  Everything else (storage, CLI, UI) depends on this
module; it depends on nothing within the project.

Design decisions
----------------
``Priority`` and ``Recur`` are declared as ``str`` subclasses (via the
``(str, Enum)`` mix-in).  This means:

* Instances compare equal to their raw string values:
  ``Priority.HIGH == "high"`` is ``True``.
* The JSON serialiser writes them as plain strings (``"high"``, not
  ``{"__type__": "Priority", "value": "high"}``).
* Old JSON files that stored bare strings load without any conversion
  code — ``from_dict`` just reads the string and it passes through.

The ``_ADVANCE`` dispatch table replaces a chain of ``if/elif`` checks in
``next_due_date``.  Adding a new recurrence period (e.g. "yearly") requires
only a new entry in that table — no other code needs to change.  This is
the *open/closed principle* in practice: open for extension (add an entry),
closed for modification (don't touch existing code).
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from enum import Enum
from typing import Callable


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------

class Priority(str, Enum):
    """Importance level of a todo.

    Inheriting from ``str`` makes instances usable everywhere a plain string
    is expected (comparisons, f-strings, JSON serialisation) without losing
    the benefits of an enum (autocomplete, typo detection, iteration).

    >>> Priority.HIGH == "high"
    True
    >>> [p.value for p in Priority]
    ['low', 'medium', 'high']
    """
    LOW    = "low"
    MEDIUM = "medium"
    HIGH   = "high"


class Recur(str, Enum):
    """Recurrence cadence for a todo.

    When a recurring todo is marked done, ``storage.mark_done`` automatically
    creates a new sibling todo with the next calculated due date (see
    ``Todo.next_due_date``).  The original todo is still recorded as done —
    completion history is preserved.
    """
    DAILY   = "daily"
    WEEKLY  = "weekly"
    MONTHLY = "monthly"


# Tuples of raw string values, exported so argparse choices lists and the UI's
# combo-boxes never need to import the enum classes themselves.
PRIORITIES:    tuple[str, ...] = tuple(p.value for p in Priority)
RECUR_PERIODS: tuple[str, ...] = tuple(r.value for r in Recur)


# ---------------------------------------------------------------------------
# Recurrence dispatch table
# ---------------------------------------------------------------------------

def _advance_month(d: date) -> date:
    """Return the same day-of-month one calendar month later.

    The day is clamped to the last day of the target month so that, e.g.,
    "Jan 31 → Feb 28/29" works correctly rather than raising a ValueError.
    """
    month    = d.month + 1
    year     = d.year + (month - 1) // 12
    month    = ((month - 1) % 12) + 1
    last_day = calendar.monthrange(year, month)[1]
    return d.replace(year=year, month=month, day=min(d.day, last_day))


# Maps each Recur value to a pure function ``date → date``.
# ``next_due_date`` looks up the appropriate function and calls it.
# To add a new recurrence period: insert one entry here. Nothing else changes.
_ADVANCE: dict[str, Callable[[date], date]] = {
    Recur.DAILY:   lambda d: d + timedelta(days=1),
    Recur.WEEKLY:  lambda d: d + timedelta(weeks=1),
    Recur.MONTHLY: _advance_month,
}


# ---------------------------------------------------------------------------
# Domain model
# ---------------------------------------------------------------------------

@dataclass
class Todo:
    """A single todo item.

    This is a plain data container (dataclass) with a few derived properties
    and helpers.  It carries no references to files, widgets, or other
    infrastructure — that keeps it testable in isolation and reusable across
    all layers.

    Fields
    ------
    id           : Stable integer identifier; monotonically increasing, never
                   reused within a list even after deletions.
    title        : Short human-readable summary shown in lists.
    done         : ``True`` once the user has marked the item complete.
    created_at   : ISO-8601 UTC timestamp recorded at creation.
    due_date     : Optional ``"YYYY-MM-DD"`` string.  Drives the ``is_overdue``
                   and ``is_due_today`` properties and coloured UI badges.
    priority     : Importance level (``Priority.LOW/MEDIUM/HIGH``), stored as
                   its underlying string for JSON round-trip compatibility.
    notes        : Free-form longer description or context clues.
    tags         : Flat list of label strings (no ``#`` prefix stored here;
                   the UI may render them with a ``#`` for visual flair).
    recur        : If set, completing this todo spawns a new one via
                   ``next_due_date``.  Value is a ``Recur`` string.
    completed_at : ISO-8601 UTC timestamp recorded when marked done.
    """
    id:           int
    title:        str
    done:         bool           = False
    created_at:   str            = field(
        # Evaluated at instance-creation time, not class-definition time.
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    due_date:     str | None     = None
    priority:     str            = Priority.MEDIUM   # str for JSON compat
    notes:        str            = ""
    tags:         list[str]      = field(default_factory=list)
    recur:        str | None     = None
    completed_at: str | None     = None

    # ── Derived (read-only) properties ────────────────────────────────────

    @property
    def is_overdue(self) -> bool:
        """``True`` when the todo has a past due date and is not yet done.

        Returns ``False`` for completed todos so overdue filters only surface
        actionable items.
        """
        if self.done or not self.due_date:
            return False
        return date.fromisoformat(self.due_date) < date.today()

    @property
    def is_due_today(self) -> bool:
        """``True`` when the due date is today and the todo is still pending."""
        if self.done or not self.due_date:
            return False
        return date.fromisoformat(self.due_date) == date.today()

    # ── Recurrence ────────────────────────────────────────────────────────

    def next_due_date(self) -> str | None:
        """Return the ISO-8601 date string of the next occurrence.

        Returns ``None`` when the todo has no recurrence cadence, no due date,
        or an unrecognised period (safe no-op in that case).

        The calculation is entirely driven by the ``_ADVANCE`` dispatch table,
        so adding new periods requires no changes here.
        """
        if not self.recur or not self.due_date:
            return None
        advance = _ADVANCE.get(self.recur)
        if advance is None:
            # Unknown period string — return None rather than crashing.
            return None
        return advance(date.fromisoformat(self.due_date)).isoformat()

    # ── Serialisation ─────────────────────────────────────────────────────

    def to_dict(self) -> dict:
        """Return a plain ``dict`` suitable for ``json.dump``.

        Priority and recur are stored as their underlying string values (e.g.
        ``"high"`` not ``<Priority.HIGH: 'high'>``), which keeps the JSON file
        human-readable and compatible with older app versions.
        """
        return {
            "id":           self.id,
            "title":        self.title,
            "done":         self.done,
            "created_at":   self.created_at,
            "due_date":     self.due_date,
            "priority":     self.priority,
            "notes":        self.notes,
            "tags":         self.tags,
            "recur":        self.recur,
            "completed_at": self.completed_at,
        }

    @staticmethod
    def from_dict(data: dict) -> "Todo":
        """Reconstruct a ``Todo`` from a plain dict.

        Missing keys fall back to safe defaults so that JSON files written by
        an older version of the app (which lacked some fields) load without
        errors.  This is a simple form of forward-compatibility.
        """
        return Todo(
            id=           data["id"],
            title=        data["title"],
            done=         data.get("done",         False),
            created_at=   data.get("created_at",   ""),
            due_date=     data.get("due_date"),
            priority=     data.get("priority",     Priority.MEDIUM),
            notes=        data.get("notes",        ""),
            tags=         data.get("tags",         []),
            recur=        data.get("recur"),
            completed_at= data.get("completed_at"),
        )
