"""todo_ui.py — Tkinter GUI for todo-cli.

Architecture
------------
The UI is built as a single ``TodoApp(tk.Tk)`` class with clear separation
between three concerns:

  * **Layout** — methods prefixed ``_build_*`` construct widget trees once.
  * **Rendering** — ``_refresh`` and helpers re-draw the scrollable list.
  * **Actions** — methods prefixed ``_`` (``_add_todo``, ``_mark_done``, …)
    handle user events and delegate to ``TodoRepository``.

The app holds a single ``TodoRepository`` instance (``self._repo``).  Every
mutation goes through that object — the UI never touches the JSON file
directly.  When the user opens a different list, ``_switch_list`` replaces the
repository, keeping the rest of the code unchanged.

Undo is implemented as a single-level snapshot: before every mutation
``self._repo.snapshot()`` captures the raw list, and ``_undo`` calls
``self._repo.restore_snapshot(...)`` to roll it back.  No private storage
internals are accessed from here.

Run with:  uv run python todo_ui.py

Keyboard shortcuts
------------------
  Ctrl+N   Focus the "add" entry
  Ctrl+E   Edit the selected todo
  Delete   Delete the selected todo (with confirmation)
  Space    Toggle done on the selected todo
  Ctrl+Z   Undo last action
  Escape   Clear the search box
  F5       Force refresh from disk
"""

from __future__ import annotations

import threading
from datetime import date
from pathlib import Path
from tkinter import filedialog, font as tkfont, messagebox
import tkinter as tk
from tkinter import ttk

from todo.models  import PRIORITIES, RECUR_PERIODS, Todo
from todo.queries import FilterOptions, filter_todos
from todo.storage import DEFAULT_PATH, TodoRepository

# ── Optional system-tray (gracefully omitted if pystray/Pillow not installed)
try:
    import pystray
    from PIL import Image, ImageDraw
    _HAS_TRAY = True
except ImportError:
    _HAS_TRAY = False

# ---------------------------------------------------------------------------
# Colour palette  (one place to change the look of the whole app)
# ---------------------------------------------------------------------------
BG            = "#F7F8FA"   # main background
TOOLBAR_BG    = "#FFFFFF"   # toolbar / filter bar
ACCENT        = "#5B8DEF"   # primary action colour (blue)
ACCENT_DARK   = "#4070D0"   # pressed state of accent buttons
ROW_ODD       = "#FFFFFF"
ROW_EVEN      = "#F3F5F8"
ROW_SEL       = "#E8F0FD"   # selected row highlight
DONE_FG       = "#9CA3AF"   # greyed-out text for completed todos
TEXT_FG       = "#111827"
MUTED_FG      = "#6B7280"
BORDER        = "#E5E7EB"
DANGER        = "#EF4444"   # overdue / delete actions (red)
SUCCESS       = "#10B981"   # done action (green)

# Priority dot colour map — drives the small coloured circles in each row.
PRI_COLORS: dict[str, str] = {
    "high":   "#EF4444",
    "medium": "#F59E0B",
    "low":    "#10B981",
}

# Placeholder text shown in the search box when it is empty.
_SEARCH_PLACEHOLDER = "🔍 Search…"


# ---------------------------------------------------------------------------
# Utility functions
# ---------------------------------------------------------------------------

def _fmt_due(due: str | None) -> str:
    """Format a due-date ISO string into a compact human-readable label.

    Examples: "⚠ Jan 05" (overdue), "Today", "Tomorrow", "Mon", "Dec 15".
    Returns an empty string for ``None``.
    """
    if not due:
        return ""
    try:
        d     = date.fromisoformat(due)
        delta = (d - date.today()).days
        if delta < 0:   return f"⚠ {d.strftime('%b %d')}"
        if delta == 0:  return "Today"
        if delta == 1:  return "Tomorrow"
        if delta < 7:   return d.strftime("%a")
        return d.strftime("%b %d")
    except ValueError:
        return due   # return raw string rather than crashing


def _due_color(todo: Todo) -> str:
    """Return the foreground colour for a todo's due-date badge."""
    if not todo.due_date:  return MUTED_FG
    if todo.is_overdue:    return DANGER
    if todo.is_due_today:  return "#F59E0B"   # amber — due today
    return MUTED_FG


# ---------------------------------------------------------------------------
# PlaceholderEntry widget
# ---------------------------------------------------------------------------

class PlaceholderEntry(tk.Entry):
    """A ``tk.Entry`` that shows placeholder text when empty.

    Why a class?
    ------------
    The previous approach scattered placeholder-related code across three
    methods (``_search_focus_in``, ``_search_focus_out``, ``_clear_search``)
    and a ``StringVar`` trace that fired even when the placeholder was being
    inserted, causing spurious refreshes.  Encapsulating the behaviour here
    keeps the host widget simpler and the logic in one place.

    Usage::

        entry = PlaceholderEntry(parent, placeholder="Search…", width=22)
        value = entry.get_value()   # "" when placeholder is showing
        entry.clear()               # reset to placeholder state
    """

    def __init__(
        self,
        master: tk.Widget,
        *,
        placeholder: str = "",
        active_fg:   str = TEXT_FG,
        **kwargs,
    ) -> None:
        super().__init__(master, **kwargs)
        self._placeholder    = placeholder
        self._active_fg      = active_fg
        self._placeholder_fg = MUTED_FG
        self._showing        = False   # True while placeholder is displayed

        self._show_placeholder()
        self.bind("<FocusIn>",  self._on_focus_in)
        self.bind("<FocusOut>", self._on_focus_out)

    # ── Placeholder state management ──────────────────────────────────────

    def _show_placeholder(self) -> None:
        self.delete(0, tk.END)
        self.insert(0, self._placeholder)
        self.config(fg=self._placeholder_fg)
        self._showing = True

    def _on_focus_in(self, _event: tk.Event) -> None:
        if self._showing:
            self.delete(0, tk.END)
            self.config(fg=self._active_fg)
            self._showing = False

    def _on_focus_out(self, _event: tk.Event) -> None:
        if not self.get():
            self._show_placeholder()

    # ── Public API ────────────────────────────────────────────────────────

    def get_value(self) -> str:
        """Return the user-typed text, or ``""`` when the placeholder is shown."""
        return "" if self._showing else self.get()

    def clear(self) -> None:
        """Erase input and return to the placeholder state."""
        self._show_placeholder()


# ---------------------------------------------------------------------------
# _Tooltip helper
# ---------------------------------------------------------------------------

class _Tooltip:
    """A lightweight hover tooltip attached to any Tkinter widget.

    Shown on ``<Enter>``, hidden on ``<Leave>``.  The window uses
    ``wm_overrideredirect`` to appear without a title bar.
    """
    def __init__(self, widget: tk.Widget, text: str) -> None:
        self._widget = widget
        self._text   = text
        self._win: tk.Toplevel | None = None
        widget.bind("<Enter>", self._show)
        widget.bind("<Leave>", self._hide)

    def _show(self, _event: tk.Event) -> None:
        if self._win:
            return   # already visible
        x = self._widget.winfo_rootx() + 20
        y = self._widget.winfo_rooty() + self._widget.winfo_height() + 4
        self._win = tk.Toplevel(self._widget)
        self._win.wm_overrideredirect(True)
        self._win.wm_geometry(f"+{x}+{y}")
        tk.Label(
            self._win, text=self._text, justify=tk.LEFT,
            bg="#FFFDE7", fg=TEXT_FG, relief=tk.SOLID, borderwidth=1,
            font=("Segoe UI", 9), padx=6, pady=4, wraplength=300,
        ).pack()

    def _hide(self, _event: tk.Event) -> None:
        if self._win:
            self._win.destroy()
            self._win = None


# ---------------------------------------------------------------------------
# EditDialog — modal form for creating or editing a todo
# ---------------------------------------------------------------------------

class EditDialog(tk.Toplevel):
    """Modal dialog that collects todo fields from the user.

    Set ``todo=None`` to create a new todo; pass an existing ``Todo`` to
    pre-populate the fields for editing.  After ``wait_window`` returns,
    inspect ``dlg.result`` — it is a plain dict with all field values, or
    ``None`` if the user cancelled.
    """

    def __init__(self, parent: tk.Tk, todo: Todo | None = None) -> None:
        super().__init__(parent)
        self.title("Edit Todo" if todo else "New Todo")
        self.resizable(False, False)
        self.configure(bg=BG)
        self.grab_set()   # modal: block the parent window
        self._todo   = todo
        self.result: dict | None = None
        self._build()
        self._center(parent)

    def _center(self, parent: tk.Tk) -> None:
        """Position the dialog in the centre of its parent window."""
        self.update_idletasks()
        pw, ph = parent.winfo_width(), parent.winfo_height()
        px, py = parent.winfo_x(),     parent.winfo_y()
        w,  h  = self.winfo_width(),   self.winfo_height()
        self.geometry(f"+{px + (pw - w) // 2}+{py + (ph - h) // 2}")

    # ── Build helpers ─────────────────────────────────────────────────────

    def _lbl(self, text: str) -> None:
        """Append a small muted section label to the dialog."""
        tk.Label(self, text=text, bg=BG, fg=MUTED_FG,
                 font=("Segoe UI", 9)).pack(anchor="w", padx=14, pady=(6, 0))

    def _entry_widget(self, var: tk.StringVar, width: int = 44) -> tk.Entry:
        """Create and pack a styled Entry bound to ``var``."""
        e = tk.Entry(
            self, textvariable=var, width=width,
            font=("Segoe UI", 11), relief=tk.FLAT,
            bg="white", fg=TEXT_FG,
            highlightthickness=1,
            highlightbackground=BORDER, highlightcolor=ACCENT,
        )
        e.pack(fill=tk.X, padx=14, pady=(0, 2))
        return e

    def _build(self) -> None:
        """Construct all form fields from the todo data (or blank for new)."""
        t = self._todo

        # ── Title ─────────────────────────────────────────────────────────
        self._lbl("Title")
        self._title_var = tk.StringVar(value=t.title if t else "")
        title_e = self._entry_widget(self._title_var)
        title_e.focus_set()   # cursor starts in the title field

        # ── Due date + Priority (side-by-side) ────────────────────────────
        row = tk.Frame(self, bg=BG)
        row.pack(fill=tk.X, padx=14, pady=(6, 0))

        due_f = tk.Frame(row, bg=BG)
        due_f.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 10))
        tk.Label(due_f, text="Due date (YYYY-MM-DD)", bg=BG, fg=MUTED_FG,
                 font=("Segoe UI", 9)).pack(anchor="w")
        self._due_var = tk.StringVar(value=t.due_date or "" if t else "")
        tk.Entry(
            due_f, textvariable=self._due_var, width=16,
            font=("Segoe UI", 11), relief=tk.FLAT, bg="white", fg=TEXT_FG,
            highlightthickness=1, highlightbackground=BORDER,
            highlightcolor=ACCENT,
        ).pack(fill=tk.X)

        pri_f = tk.Frame(row, bg=BG)
        pri_f.pack(side=tk.LEFT)
        tk.Label(pri_f, text="Priority", bg=BG, fg=MUTED_FG,
                 font=("Segoe UI", 9)).pack(anchor="w")
        self._priority_var = tk.StringVar(value=t.priority if t else "medium")
        ttk.Combobox(
            pri_f, textvariable=self._priority_var,
            values=list(PRIORITIES), state="readonly", width=10,
        ).pack()

        # ── Tags ──────────────────────────────────────────────────────────
        self._lbl("Tags (comma-separated)")
        self._tags_var = tk.StringVar(value=", ".join(t.tags) if t else "")
        self._entry_widget(self._tags_var)

        # ── Recurrence + clear-due (side-by-side) ─────────────────────────
        row2 = tk.Frame(self, bg=BG)
        row2.pack(fill=tk.X, padx=14, pady=(6, 0))

        rec_f = tk.Frame(row2, bg=BG)
        rec_f.pack(side=tk.LEFT, padx=(0, 10))
        tk.Label(rec_f, text="Recurrence", bg=BG, fg=MUTED_FG,
                 font=("Segoe UI", 9)).pack(anchor="w")
        self._recur_var = tk.StringVar(value=t.recur or "" if t else "")
        ttk.Combobox(
            rec_f, textvariable=self._recur_var,
            values=["", *RECUR_PERIODS], state="readonly", width=12,
        ).pack()

        # Checkbox to explicitly clear the due date (sets it to None).
        self._clear_due_var = tk.BooleanVar()
        tk.Checkbutton(
            row2, text="Clear due date", variable=self._clear_due_var,
            bg=BG, fg=MUTED_FG, font=("Segoe UI", 9), activebackground=BG,
        ).pack(side=tk.LEFT, pady=(14, 0))

        # ── Notes ─────────────────────────────────────────────────────────
        self._lbl("Notes")
        self._notes = tk.Text(
            self, height=4, width=44,
            font=("Segoe UI", 11), relief=tk.FLAT,
            bg="white", fg=TEXT_FG,
            highlightthickness=1,
            highlightbackground=BORDER, highlightcolor=ACCENT,
        )
        self._notes.pack(fill=tk.X, padx=14, pady=(0, 2))
        if t and t.notes:
            self._notes.insert("1.0", t.notes)

        # ── Cancel / Save buttons ─────────────────────────────────────────
        btn_row = tk.Frame(self, bg=BG)
        btn_row.pack(fill=tk.X, padx=14, pady=10)
        tk.Button(
            btn_row, text="Cancel", command=self.destroy,
            bg=BORDER, fg=TEXT_FG, relief=tk.FLAT,
            font=("Segoe UI", 10), padx=16, pady=6, cursor="hand2",
        ).pack(side=tk.RIGHT, padx=(4, 0))
        tk.Button(
            btn_row, text="Save", command=self._save,
            bg=ACCENT, fg="white", relief=tk.FLAT,
            font=("Segoe UI", 10, "bold"), padx=16, pady=6,
            cursor="hand2", activebackground=ACCENT_DARK,
        ).pack(side=tk.RIGHT)

        self.bind("<Return>", lambda _: self._save())
        self.bind("<Escape>", lambda _: self.destroy())

    def _save(self) -> None:
        """Validate inputs and store results; close dialog on success."""
        title = self._title_var.get().strip()
        if not title:
            messagebox.showwarning("Validation", "Title cannot be empty.", parent=self)
            return
        due = self._due_var.get().strip() or None
        if due:
            try:
                date.fromisoformat(due)
            except ValueError:
                messagebox.showwarning(
                    "Validation", "Invalid date. Use YYYY-MM-DD.", parent=self
                )
                return
        if self._clear_due_var.get():
            due = None   # user explicitly cleared it
        self.result = {
            "title":    title,
            "due_date": due,
            "priority": self._priority_var.get(),
            "tags":     [t.strip() for t in self._tags_var.get().split(",") if t.strip()],
            "recur":    self._recur_var.get() or None,
            "notes":    self._notes.get("1.0", tk.END).strip(),
        }
        self.destroy()


# ---------------------------------------------------------------------------
# StatsDialog
# ---------------------------------------------------------------------------

class StatsDialog(tk.Toplevel):
    """Read-only statistics summary for the current todo list."""

    def __init__(self, parent: tk.Tk, todos: list[Todo]) -> None:
        super().__init__(parent)
        self.title("Stats")
        self.resizable(False, False)
        self.configure(bg=BG, padx=20, pady=16)
        self.grab_set()
        self._build(todos)
        self.bind("<Escape>", lambda _: self.destroy())

    def _build(self, todos: list[Todo]) -> None:
        from collections import Counter

        total      = len(todos)
        done       = sum(1 for t in todos if t.done)
        pending    = total - done
        overdue    = sum(1 for t in todos if t.is_overdue)
        today_str  = date.today().isoformat()
        done_today = sum(
            1 for t in todos
            if t.completed_at and t.completed_at[:10] == today_str
        )
        rate     = f"{done / total * 100:.0f}%" if total else "—"
        top_tags = Counter(tg for t in todos for tg in t.tags).most_common(5)

        for label, value in [
            ("Total",           str(total)),
            ("Pending",         str(pending)),
            ("Done",            str(done)),
            ("Overdue",         str(overdue)),
            ("Done today",      str(done_today)),
            ("Completion rate", rate),
        ]:
            row = tk.Frame(self, bg=BG)
            row.pack(fill=tk.X, pady=2)
            tk.Label(row, text=label, bg=BG, fg=MUTED_FG,
                     font=("Segoe UI", 10), width=18, anchor="w").pack(side=tk.LEFT)
            tk.Label(row, text=value, bg=BG, fg=TEXT_FG,
                     font=("Segoe UI", 10, "bold")).pack(side=tk.LEFT)

        if top_tags:
            tk.Label(self, text="Top tags", bg=BG, fg=MUTED_FG,
                     font=("Segoe UI", 9)).pack(anchor="w", pady=(10, 2))
            for tag, count in top_tags:
                row = tk.Frame(self, bg=BG)
                row.pack(fill=tk.X, pady=1)
                tk.Label(row, text=f"#{tag}", bg=BG, fg=ACCENT,
                         font=("Segoe UI", 10), width=18, anchor="w").pack(side=tk.LEFT)
                tk.Label(row, text=str(count), bg=BG, fg=TEXT_FG,
                         font=("Segoe UI", 10)).pack(side=tk.LEFT)

        tk.Button(
            self, text="Close", command=self.destroy,
            bg=ACCENT, fg="white", relief=tk.FLAT,
            font=("Segoe UI", 10), padx=16, pady=6,
        ).pack(pady=(14, 0))


# ---------------------------------------------------------------------------
# TodoApp — main window
# ---------------------------------------------------------------------------

class TodoApp(tk.Tk):
    """The main application window.

    Responsibilities
    ----------------
    * Hold a single ``TodoRepository`` instance (``self._repo``).
    * Build the widget tree once (``_build_ui``).
    * Re-render the list on every state change (``_refresh``).
    * Delegate all data mutations to ``self._repo``.

    The repository is replaced (not mutated) when the user switches lists
    (see ``_switch_list``), keeping list-switching simple and side-effect-free.
    """

    def __init__(self, path: Path = DEFAULT_PATH) -> None:
        super().__init__()
        # The repository is the single point of contact with storage.
        self._repo            = TodoRepository(path)
        self._todos_path      = path
        self._selected_id: int | None          = None
        self._undo_snapshot:  list[dict] | None = None

        self.geometry("700x560")
        self.minsize(500, 360)
        self.configure(bg=BG)

        self._build_ui()
        self._bind_shortcuts()
        self._setup_tray()
        self._refresh()

    # ── Construction (called once) ─────────────────────────────────────────

    def _build_ui(self) -> None:
        self._build_toolbar()
        self._build_filter_bar()
        self._build_add_bar()
        self._build_list_area()
        self._build_status_bar()

    def _build_toolbar(self) -> None:
        """Top bar with app title, current list name, and utility buttons."""
        bar = tk.Frame(self, bg=TOOLBAR_BG, pady=6, padx=12)
        bar.pack(fill=tk.X)

        tk.Label(bar, text="📋 Todo", bg=TOOLBAR_BG, fg=TEXT_FG,
                 font=("Segoe UI", 13, "bold")).pack(side=tk.LEFT)
        self._path_label = tk.Label(bar, text="", bg=TOOLBAR_BG, fg=MUTED_FG,
                                    font=("Segoe UI", 9))
        self._path_label.pack(side=tk.LEFT, padx=(8, 0))

        # Utility buttons — right-aligned, in reverse order.
        for label, cmd in [
            ("New List",  self._new_list),
            ("Open List", self._open_list),
            ("Backup",    self._backup),
            ("Restore",   self._restore),
            ("Stats",     self._show_stats),
        ]:
            tk.Button(
                bar, text=label, command=cmd,
                bg=TOOLBAR_BG, fg=MUTED_FG, relief=tk.FLAT,
                font=("Segoe UI", 9), padx=8, pady=4, cursor="hand2",
                activebackground="#F3F4F6",
            ).pack(side=tk.RIGHT)

        tk.Frame(self, bg=BORDER, height=1).pack(fill=tk.X)

    def _build_filter_bar(self) -> None:
        """Filter / search bar below the toolbar.

        Uses ``PlaceholderEntry`` for the search field so placeholder state
        management is fully encapsulated in that class.  A ``<KeyRelease>``
        binding triggers refresh; this avoids the spurious refreshes that
        occurred when a ``StringVar`` trace fired on placeholder insertion.
        """
        bar = tk.Frame(self, bg=TOOLBAR_BG, pady=6, padx=12)
        bar.pack(fill=tk.X)

        # Search field  (PlaceholderEntry manages placeholder text internally)
        self._search_entry = PlaceholderEntry(
            bar,
            placeholder=_SEARCH_PLACEHOLDER,
            width=22,
            font=("Segoe UI", 10),
            relief=tk.FLAT,
            bg="#F3F4F6",
            highlightthickness=1,
            highlightbackground=BORDER,
            highlightcolor=ACCENT,
        )
        self._search_entry.pack(side=tk.LEFT, ipady=4, padx=(0, 10))
        # Refresh after each keystroke (not on trace, which fires for placeholder too).
        self._search_entry.bind("<KeyRelease>", lambda _: self._safe_refresh())

        # Filter drop-down
        tk.Label(bar, text="Filter:", bg=TOOLBAR_BG, fg=MUTED_FG,
                 font=("Segoe UI", 9)).pack(side=tk.LEFT)
        self._filter_var = tk.StringVar(value="all")
        self._filter_var.trace_add("write", lambda *_: self._safe_refresh())
        ttk.Combobox(
            bar, textvariable=self._filter_var,
            values=["all", "pending", "done", "overdue"],
            state="readonly", width=10,
        ).pack(side=tk.LEFT, padx=(4, 12))

        # Sort drop-down
        tk.Label(bar, text="Sort:", bg=TOOLBAR_BG, fg=MUTED_FG,
                 font=("Segoe UI", 9)).pack(side=tk.LEFT)
        self._sort_var = tk.StringVar(value="position")
        self._sort_var.trace_add("write", lambda *_: self._safe_refresh())
        ttk.Combobox(
            bar, textvariable=self._sort_var,
            values=["position", "due", "priority", "alpha", "created"],
            state="readonly", width=12,
        ).pack(side=tk.LEFT, padx=(4, 0))

        tk.Frame(self, bg=BORDER, height=1).pack(fill=tk.X)

    def _build_add_bar(self) -> None:
        """Quick-add bar: single-line entry + "Add" button."""
        bar = tk.Frame(self, bg=BG, pady=8, padx=12)
        bar.pack(fill=tk.X)

        self._entry = tk.Entry(
            bar, font=("Segoe UI", 12), relief=tk.FLAT,
            bg="white", fg=TEXT_FG,
            highlightthickness=1, highlightbackground=BORDER,
            highlightcolor=ACCENT,
        )
        self._entry.pack(side=tk.LEFT, fill=tk.X, expand=True, ipady=6, padx=(0, 8))
        self._entry.bind("<Return>", lambda _: self._add_todo())

        tk.Button(
            bar, text="Add", command=self._add_todo,
            bg=ACCENT, fg="white", relief=tk.FLAT,
            font=("Segoe UI", 11, "bold"), padx=16, pady=6, cursor="hand2",
            activebackground=ACCENT_DARK,
        ).pack(side=tk.LEFT)

    def _build_list_area(self) -> None:
        """Scrollable todo list with a fixed column-header row."""
        frame = tk.Frame(self, bg=BG, padx=12)
        frame.pack(fill=tk.BOTH, expand=True)

        # Static column header row.
        hdr = tk.Frame(frame, bg="#EEF2FF")
        hdr.pack(fill=tk.X)
        for text, lbl_opts, pack_opts in [
            ("",     dict(width=12), dict()),
            ("Task", dict(),         dict(fill=tk.X, expand=True)),
            ("Due",  dict(width=10), dict()),
            ("",     dict(width=18), dict()),
        ]:
            tk.Label(
                hdr, text=text, bg="#EEF2FF", fg=MUTED_FG,
                font=("Segoe UI", 8, "bold"), anchor="w", **lbl_opts,
            ).pack(side=tk.LEFT, pady=3, **pack_opts)

        # Canvas + scrollbar — the list rows are placed inside _list_inner.
        self._canvas = tk.Canvas(frame, bg=BG, highlightthickness=0)
        sb = ttk.Scrollbar(frame, orient=tk.VERTICAL, command=self._canvas.yview)
        self._canvas.configure(yscrollcommand=sb.set)
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        self._canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self._list_inner = tk.Frame(self._canvas, bg=BG)
        self._canvas_win = self._canvas.create_window(
            (0, 0), window=self._list_inner, anchor="nw"
        )
        # Keep scroll region in sync with the inner frame's size.
        self._list_inner.bind(
            "<Configure>",
            lambda _e: self._canvas.configure(scrollregion=self._canvas.bbox("all")),
        )
        # Keep the inner frame width equal to the canvas width on resize.
        self._canvas.bind(
            "<Configure>",
            lambda e: self._canvas.itemconfig(self._canvas_win, width=e.width),
        )
        # Mouse-wheel scrolling (Windows/macOS send delta in multiples of 120).
        self._canvas.bind_all(
            "<MouseWheel>",
            lambda e: self._canvas.yview_scroll(-1 * (e.delta // 120), "units"),
        )

    def _build_status_bar(self) -> None:
        """Bottom bar: status counts + Export and Undo buttons."""
        tk.Frame(self, bg=BORDER, height=1).pack(fill=tk.X)
        bar = tk.Frame(self, bg=TOOLBAR_BG, pady=5, padx=12)
        bar.pack(fill=tk.X, side=tk.BOTTOM)

        self._status_var = tk.StringVar()
        tk.Label(
            bar, textvariable=self._status_var, bg=TOOLBAR_BG,
            fg=MUTED_FG, font=("Segoe UI", 9),
        ).pack(side=tk.LEFT)

        for label, cmd in [("⬇ Export JSON", self._export),
                            ("↩ Undo",        self._undo)]:
            tk.Button(
                bar, text=label, command=cmd,
                bg=TOOLBAR_BG, fg=MUTED_FG, relief=tk.FLAT,
                font=("Segoe UI", 9), padx=8, pady=3, cursor="hand2",
                activebackground="#F3F4F6",
            ).pack(side=tk.RIGHT, padx=(4, 0))

    # ── Keyboard shortcuts ─────────────────────────────────────────────────

    def _bind_shortcuts(self) -> None:
        self.bind("<Control-n>", lambda _: (
            self._entry.focus_set(), self._entry.select_range(0, tk.END)))
        self.bind("<Control-z>", lambda _: self._undo())
        self.bind("<Control-e>", lambda _: self._edit_selected())
        self.bind("<Delete>",    lambda _: self._delete_selected())
        self.bind("<space>",     lambda _: self._done_selected())
        self.bind("<Escape>",    lambda _: self._clear_search())
        self.bind("<F5>",        lambda _: self._refresh())

    # ── Search helpers ────────────────────────────────────────────────────

    def _clear_search(self) -> None:
        """Reset the search field to its placeholder and return focus to the add entry."""
        self._search_entry.clear()
        self._entry.focus_set()

    # ── Rendering ─────────────────────────────────────────────────────────

    def _safe_refresh(self) -> None:
        """Refresh only once ``_list_inner`` exists (guards against trace firing early)."""
        if hasattr(self, "_list_inner"):
            self._refresh()

    def _refresh(self) -> None:
        """Rebuild the visible todo list and update all status indicators.

        Called after every mutation and on every filter/search/sort change.
        Destroys all existing row widgets and recreates them; this is simple
        and correct — Tkinter row creation is fast enough for typical list sizes.
        """
        # Clear the list area.
        for w in self._list_inner.winfo_children():
            w.destroy()

        all_todos = self._repo.load()

        # Build a FilterOptions object from the current UI state.
        options = FilterOptions(
            filter_by=self._filter_var.get(),
            search=   self._search_entry.get_value(),
            sort_by=  self._sort_var.get(),
        )
        visible = filter_todos(all_todos, options)

        if not visible:
            msg = ("No todos yet — type above and press Enter."
                   if not all_todos else "No todos match the filter.")
            tk.Label(self._list_inner, text=msg, bg=BG, fg=MUTED_FG,
                     font=("Segoe UI", 11), pady=30).pack()
        else:
            for idx, todo in enumerate(visible):
                bg = (ROW_SEL  if todo.id == self._selected_id else
                      ROW_ODD  if idx % 2 == 0 else ROW_EVEN)
                self._build_row(todo, bg, idx, len(visible))

        # Update the status bar counts.
        pending = sum(1 for t in all_todos if not t.done)
        done    = sum(1 for t in all_todos if t.done)
        overdue = sum(1 for t in all_todos if t.is_overdue)
        parts   = [f"{pending} pending", f"{done} done"]
        if overdue:
            parts.append(f"{overdue} overdue ⚠")
        self._status_var.set("  |  ".join(parts))

        # Update title bar and path label with the active list name.
        stem = self._todos_path.stem
        self._path_label.config(text=f"— {stem}")
        self.title(f"Todo — {stem}")

    # ── Row rendering (decomposed) ─────────────────────────────────────────

    def _build_row(self, todo: Todo, bg: str, idx: int, total: int) -> None:
        """Build one todo row by composing the smaller render helpers.

        This method was previously ~90 lines.  Breaking it into focused helpers
        makes each piece independently readable and testable.
        """
        row = tk.Frame(self._list_inner, bg=bg, cursor="hand2")
        row.pack(fill=tk.X)
        # Click anywhere on the row to select; double-click to open editor.
        row.bind("<Button-1>",        lambda _: self._select(todo.id))
        row.bind("<Double-Button-1>", lambda _: self._open_edit(todo))

        self._render_status_indicator(row, todo, bg)
        self._render_title(row, todo, bg)
        self._render_badges(row, todo, bg)
        self._render_due_date(row, todo, bg)
        self._render_action_buttons(row, todo, bg, idx, total)

        # Separator line between rows.
        tk.Frame(self._list_inner, bg=BORDER, height=1).pack(fill=tk.X)

    def _render_status_indicator(self, row: tk.Frame, todo: Todo, bg: str) -> None:
        """Priority colour dot + numeric id + status glyph (◯ / ⚠ / ✓)."""
        # Small coloured circle whose fill colour reflects priority.
        dot = tk.Canvas(row, width=10, height=10, bg=bg, highlightthickness=0)
        dot.create_oval(
            1, 1, 9, 9,
            fill=(PRI_COLORS.get(todo.priority, ACCENT) if not todo.done else BORDER),
            outline="",
        )
        dot.pack(side=tk.LEFT, padx=(8, 2), pady=10)
        dot.bind("<Button-1>", lambda _: self._select(todo.id))

        # Numeric id label.
        tk.Label(
            row, text=str(todo.id), bg=bg,
            fg=DONE_FG if todo.done else MUTED_FG,
            font=("Segoe UI", 9), width=3, anchor="e",
        ).pack(side=tk.LEFT)

        # Status glyph — indicates completion or overdue state at a glance.
        glyph    = "✓" if todo.done else ("⚠" if todo.is_overdue else "○")
        glyph_fg = (DONE_FG if todo.done else
                    DANGER  if todo.is_overdue else ACCENT)
        tk.Label(row, text=glyph, bg=bg, fg=glyph_fg,
                 font=("Segoe UI", 12), width=2).pack(side=tk.LEFT)

    def _render_title(self, row: tk.Frame, todo: Todo, bg: str) -> None:
        """Title label with strikethrough font for completed todos."""
        # ``tkfont.Font`` is created per-row so the strikethrough can differ
        # per todo without sharing a mutable font object.
        title_f = tkfont.Font(
            family="Segoe UI", size=11,
            overstrike=1 if todo.done else 0,
        )
        lbl = tk.Label(
            row, text=todo.title, bg=bg,
            fg=DONE_FG if todo.done else TEXT_FG,
            font=title_f, anchor="w", cursor="hand2",
        )
        lbl.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(4, 6), pady=8)
        lbl.bind("<Button-1>",        lambda _: self._select(todo.id))
        lbl.bind("<Double-Button-1>", lambda _: self._open_edit(todo))

    def _render_badges(self, row: tk.Frame, todo: Todo, bg: str) -> None:
        """Notes tooltip icon, recurrence indicator, and tag chips (max 3)."""
        # 📝 icon with a hover tooltip showing the first 200 chars of notes.
        if todo.notes:
            nl = tk.Label(row, text="📝", bg=bg, font=("Segoe UI", 9))
            nl.pack(side=tk.LEFT, padx=2)
            excerpt = todo.notes[:200] + ("…" if len(todo.notes) > 200 else "")
            _Tooltip(nl, excerpt)

        # Recurrence indicator (e.g. "↻d" for daily).
        if todo.recur:
            tk.Label(
                row, text=f"↻{todo.recur[0]}", bg=bg, fg=MUTED_FG,
                font=("Segoe UI", 8),
            ).pack(side=tk.LEFT, padx=2)

        # Tag chips — show at most 3 to avoid overflow on narrow windows.
        for tag in todo.tags[:3]:
            tk.Label(
                row, text=f"#{tag}", bg="#EEF2FF", fg=ACCENT,
                font=("Segoe UI", 8), padx=4, pady=1,
            ).pack(side=tk.LEFT, padx=(2, 0), pady=6)

    def _render_due_date(self, row: tk.Frame, todo: Todo, bg: str) -> None:
        """Compact due-date label with colour coding (red = overdue, amber = today)."""
        if todo.due_date:
            tk.Label(
                row, text=_fmt_due(todo.due_date), bg=bg,
                fg=(_due_color(todo) if not todo.done else DONE_FG),
                font=("Segoe UI", 9), width=10, anchor="e",
            ).pack(side=tk.LEFT, padx=4)

    def _render_action_buttons(
        self, row: tk.Frame, todo: Todo, bg: str, idx: int, total: int
    ) -> None:
        """Move-up, move-down, delete, and done buttons (right-aligned)."""
        btn_kw = dict(bg=bg, relief=tk.FLAT, font=("Segoe UI", 9),
                      padx=3, pady=2, cursor="hand2", activebackground=ROW_EVEN)

        # Move down — only shown when not already at the bottom.
        if idx < total - 1:
            tk.Button(
                row, text="↓", fg=MUTED_FG,
                command=lambda tid=todo.id, i=idx: self._move(tid, i + 1),
                **btn_kw,
            ).pack(side=tk.RIGHT)

        # Move up — only shown when not already at the top.
        if idx > 0:
            tk.Button(
                row, text="↑", fg=MUTED_FG,
                command=lambda tid=todo.id, i=idx: self._move(tid, i - 1),
                **btn_kw,
            ).pack(side=tk.RIGHT)

        # Delete button (always visible).
        tk.Button(
            row, text="🗑", fg=DANGER,
            command=lambda tid=todo.id: self._delete_todo(tid),
            **{**btn_kw, "font": ("Segoe UI", 10), "padx": 4},
        ).pack(side=tk.RIGHT, padx=(0, 2), pady=4)

        # "Done" button — hidden once the todo is complete.
        if not todo.done:
            tk.Button(
                row, text="Done", fg=SUCCESS,
                font=("Segoe UI", 9, "bold"), padx=6, pady=2,
                bg=bg, relief=tk.FLAT, cursor="hand2",
                activebackground=ROW_EVEN,
                command=lambda tid=todo.id: self._mark_done(tid),
            ).pack(side=tk.RIGHT, padx=(0, 2), pady=4)

    # ── Mutation actions ───────────────────────────────────────────────────

    def _add_todo(self) -> None:
        """Read the add-bar entry and create a new todo."""
        title = self._entry.get().strip()
        if not title:
            self._flash("⚠ Please enter a task title.")
            return
        self._undo_snapshot = self._repo.snapshot()
        self._repo.add(title)
        self._entry.delete(0, tk.END)
        self._refresh()

    def _mark_done(self, todo_id: int) -> None:
        self._undo_snapshot = self._repo.snapshot()
        self._repo.mark_done(todo_id)
        self._refresh()

    def _delete_todo(self, todo_id: int) -> None:
        """Ask for confirmation before deleting."""
        todos = self._repo.load()
        todo  = next((t for t in todos if t.id == todo_id), None)
        if todo and not messagebox.askyesno(
            "Delete", f'Delete "{todo.title}"?', parent=self
        ):
            return
        self._undo_snapshot = self._repo.snapshot()
        self._repo.delete(todo_id)
        if self._selected_id == todo_id:
            self._selected_id = None
        self._refresh()

    def _open_edit(self, todo: Todo) -> None:
        """Open the edit dialog and apply changes if the user saved."""
        dlg = EditDialog(self, todo)
        self.wait_window(dlg)
        if dlg.result:
            self._undo_snapshot = self._repo.snapshot()
            self._repo.edit(todo.id, **dlg.result)
            self._refresh()

    def _move(self, todo_id: int, new_idx: int) -> None:
        self._undo_snapshot = self._repo.snapshot()
        self._repo.move(todo_id, new_idx)
        self._refresh()

    def _undo(self) -> None:
        """Restore the pre-mutation snapshot.

        ``restore_snapshot`` is the public API on ``TodoRepository`` that
        writes raw dict data back to disk.  This replaces the previous
        ``storage._save_raw(...)`` call which violated encapsulation by
        reaching into a private module-level function.
        """
        if self._undo_snapshot is None:
            self._flash("Nothing to undo.")
            return
        self._repo.restore_snapshot(self._undo_snapshot)
        self._undo_snapshot = None
        self._refresh()
        self._flash("↩ Undone.")

    # ── List management ───────────────────────────────────────────────────

    def _switch_list(self, path: Path) -> None:
        """Replace the active list with a new path and recreate the repository.

        Centralising list switching here ensures that ``self._repo`` and
        ``self._todos_path`` are always in sync.
        """
        self._todos_path      = path
        self._repo            = TodoRepository(path)
        self._selected_id     = None
        self._undo_snapshot   = None
        self._refresh()

    def _new_list(self) -> None:
        path = filedialog.asksaveasfilename(
            title="New List", defaultextension=".json",
            filetypes=[("JSON", "*.json")],
        )
        if path:
            self._switch_list(Path(path))

    def _open_list(self) -> None:
        path = filedialog.askopenfilename(
            title="Open List", filetypes=[("JSON", "*.json")],
        )
        if path:
            self._switch_list(Path(path))

    def _backup(self) -> None:
        dest = filedialog.asksaveasfilename(
            title="Save Backup", defaultextension=".json",
            filetypes=[("JSON", "*.json")],
        )
        if dest:
            ok = self._repo.backup(Path(dest))
            if ok:
                self._flash(f"Backed up to {Path(dest).name}")
            else:
                messagebox.showwarning("Backup", "No todos file to back up.", parent=self)

    def _restore(self) -> None:
        src = filedialog.askopenfilename(
            title="Restore Backup", filetypes=[("JSON", "*.json")],
        )
        if src:
            self._undo_snapshot = self._repo.snapshot()
            ok = self._repo.restore(Path(src))
            if ok:
                self._refresh()
                self._flash(f"Restored from {Path(src).name}")
            else:
                messagebox.showwarning("Restore", "Backup file not found.", parent=self)

    def _export(self) -> None:
        path = filedialog.asksaveasfilename(
            defaultextension=".json",
            filetypes=[("JSON", "*.json")],
            title="Export todos",
        )
        if path:
            Path(path).write_text(self._repo.export_json(), encoding="utf-8")
            self._flash(f"Exported to {Path(path).name}")

    def _show_stats(self) -> None:
        StatsDialog(self, self._repo.load())

    # ── Selection helpers ──────────────────────────────────────────────────

    def _select(self, todo_id: int) -> None:
        """Highlight the clicked row (and re-render to show the new colour)."""
        self._selected_id = todo_id
        self._refresh()

    def _edit_selected(self) -> None:
        if not self._selected_id:
            return
        todos = self._repo.load()
        todo  = next((t for t in todos if t.id == self._selected_id), None)
        if todo:
            self._open_edit(todo)

    def _delete_selected(self) -> None:
        if self._selected_id:
            self._delete_todo(self._selected_id)

    def _done_selected(self) -> None:
        if self._selected_id:
            self._mark_done(self._selected_id)

    # ── Flash message ──────────────────────────────────────────────────────

    def _flash(self, msg: str) -> None:
        """Display a temporary status message that auto-clears after 3 seconds."""
        self._status_var.set(msg)
        self.after(3000, lambda: (
            self._refresh() if self._status_var.get() == msg else None
        ))

    # ── System tray ────────────────────────────────────────────────────────

    def _setup_tray(self) -> None:
        """Attach a system-tray icon if pystray/Pillow are available.

        The tray icon is created in a daemon thread so it does not block the
        Tkinter main loop.  If the optional packages are absent, this method
        does nothing — the app runs perfectly without a tray icon.
        """
        if not _HAS_TRAY:
            return

        def _make_icon() -> "Image.Image":
            # Simple blue circle with a white checkmark glyph.
            img  = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
            draw = ImageDraw.Draw(img)
            draw.ellipse([2, 2, 62, 62], fill="#5B8DEF")
            draw.line([(18, 32), (28, 44)], fill="white", width=5)
            draw.line([(28, 44), (48, 20)], fill="white", width=5)
            return img

        menu = pystray.Menu(
            pystray.MenuItem("Show", self._show_from_tray, default=True),
            pystray.MenuItem("Quit", self._quit_app),
        )
        self._tray = pystray.Icon("todo-cli", _make_icon(), "Todo", menu)
        threading.Thread(target=self._tray.run, daemon=True).start()
        # Override the close button: hide to tray rather than quitting.
        self.protocol("WM_DELETE_WINDOW", self._hide_to_tray)

    def _hide_to_tray(self) -> None:
        self.withdraw()

    def _show_from_tray(self, *_) -> None:
        self.after(0, self.deiconify)

    def _quit_app(self, *_) -> None:
        if _HAS_TRAY and hasattr(self, "_tray"):
            self._tray.stop()
        self.after(0, self.destroy)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    app = TodoApp()
    app.mainloop()


if __name__ == "__main__":
    main()
