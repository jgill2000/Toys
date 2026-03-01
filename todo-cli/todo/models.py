from __future__ import annotations

import calendar
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

PRIORITIES = ("low", "medium", "high")
RECUR_PERIODS = ("daily", "weekly", "monthly")


@dataclass
class Todo:
    id: int
    title: str
    done: bool = False
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    due_date: str | None = None       # "YYYY-MM-DD"
    priority: str = "medium"          # "low" | "medium" | "high"
    notes: str = ""
    tags: list[str] = field(default_factory=list)
    recur: str | None = None          # "daily" | "weekly" | "monthly"
    completed_at: str | None = None

    # ------------------------------------------------------------------
    # Derived properties
    # ------------------------------------------------------------------

    @property
    def is_overdue(self) -> bool:
        if self.done or not self.due_date:
            return False
        return date.fromisoformat(self.due_date) < date.today()

    @property
    def is_due_today(self) -> bool:
        if self.done or not self.due_date:
            return False
        return date.fromisoformat(self.due_date) == date.today()

    def next_due_date(self) -> str | None:
        """Return the next occurrence date string for a recurring todo."""
        if not self.recur or not self.due_date:
            return None
        d = date.fromisoformat(self.due_date)
        if self.recur == "daily":
            d += timedelta(days=1)
        elif self.recur == "weekly":
            d += timedelta(weeks=1)
        elif self.recur == "monthly":
            month = d.month + 1
            year = d.year + (month - 1) // 12
            month = ((month - 1) % 12) + 1
            last_day = calendar.monthrange(year, month)[1]
            d = d.replace(year=year, month=month, day=min(d.day, last_day))
        return d.isoformat()

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "done": self.done,
            "created_at": self.created_at,
            "due_date": self.due_date,
            "priority": self.priority,
            "notes": self.notes,
            "tags": self.tags,
            "recur": self.recur,
            "completed_at": self.completed_at,
        }

    @staticmethod
    def from_dict(data: dict) -> "Todo":
        return Todo(
            id=data["id"],
            title=data["title"],
            done=data.get("done", False),
            created_at=data.get("created_at", ""),
            due_date=data.get("due_date"),
            priority=data.get("priority", "medium"),
            notes=data.get("notes", ""),
            tags=data.get("tags", []),
            recur=data.get("recur"),
            completed_at=data.get("completed_at"),
        )
