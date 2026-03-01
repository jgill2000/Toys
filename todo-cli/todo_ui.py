"""todo_ui.py — Tkinter GUI for todo-cli.

Run with:  uv run python todo_ui.py
Keyboard shortcuts:
  Ctrl+N   Focus the add entry
  Ctrl+E   Edit selected todo
  Delete   Delete selected todo (with confirmation)
  Space    Toggle done on selected todo
  Ctrl+Z   Undo last action
  Escape   Clear search
  F5       Refresh
"""

from __future__ import annotations

import threading
from datetime import date
from pathlib import Path
from tkinter import filedialog, font as tkfont, messagebox
import tkinter as tk
from tkinter import ttk

from todo import storage
from todo.models import PRIORITIES, RECUR_PERIODS, Todo

# ── Optional system-tray (requires: pip install pystray pillow) ──────────────
try:
    import pystray
    from PIL import Image, ImageDraw
    _HAS_TRAY = True
except ImportError:
    _HAS_TRAY = False

# ── Palette ───────────────────────────────────────────────────────────────────
BG            = "#F7F8FA"
TOOLBAR_BG    = "#FFFFFF"
ACCENT        = "#5B8DEF"
ACCENT_DARK   = "#4070D0"
ROW_ODD       = "#FFFFFF"
ROW_EVEN      = "#F3F5F8"
ROW_SEL       = "#E8F0FD"
DONE_FG       = "#9CA3AF"
TEXT_FG       = "#111827"
MUTED_FG      = "#6B7280"
BORDER        = "#E5E7EB"
DANGER        = "#EF4444"
SUCCESS       = "#10B981"
PRI_COLORS    = {"high": "#EF4444", "medium": "#F59E0B", "low": "#10B981"}

# ── Small utilities ───────────────────────────────────────────────────────────

def _fmt_due(due: str | None) -> str:
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
        return due


def _due_color(todo: Todo) -> str:
    if not todo.due_date:  return MUTED_FG
    if todo.is_overdue:    return DANGER
    if todo.is_due_today:  return "#F59E0B"
    return MUTED_FG


class _Tooltip:
    """Simple hover tooltip for any widget."""
    def __init__(self, widget: tk.Widget, text: str) -> None:
        self._widget = widget
        self._text   = text
        self._win: tk.Toplevel | None = None
        widget.bind("<Enter>", self._show)
        widget.bind("<Leave>", self._hide)

    def _show(self, _event: tk.Event) -> None:
        if self._win:
            return
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


# ── Edit / New dialog ─────────────────────────────────────────────────────────

class EditDialog(tk.Toplevel):
    """Modal dialog for editing or creating a todo."""

    def __init__(self, parent: tk.Tk, todo: Todo | None = None) -> None:
        super().__init__(parent)
        self.title("Edit Todo" if todo else "New Todo")
        self.resizable(False, False)
        self.configure(bg=BG)
        self.grab_set()
        self._todo   = todo
        self.result: dict | None = None
        self._build()
        self._center(parent)

    def _center(self, parent: tk.Tk) -> None:
        self.update_idletasks()
        pw, ph = parent.winfo_width(), parent.winfo_height()
        px, py = parent.winfo_x(), parent.winfo_y()
        w, h   = self.winfo_width(), self.winfo_height()
        self.geometry(f"+{px+(pw-w)//2}+{py+(ph-h)//2}")

    def _lbl(self, text: str) -> None:
        tk.Label(self, text=text, bg=BG, fg=MUTED_FG,
                 font=("Segoe UI", 9)).pack(anchor="w", padx=14, pady=(6, 0))

    def _entry_widget(self, var: tk.StringVar, width: int = 44) -> tk.Entry:
        e = tk.Entry(self, textvariable=var, width=width,
                     font=("Segoe UI", 11), relief=tk.FLAT,
                     bg="white", fg=TEXT_FG,
                     highlightthickness=1,
                     highlightbackground=BORDER, highlightcolor=ACCENT)
        e.pack(fill=tk.X, padx=14, pady=(0, 2))
        return e

    def _build(self) -> None:
        t = self._todo

        # Title
        self._lbl("Title")
        self._title_var = tk.StringVar(value=t.title if t else "")
        title_e = self._entry_widget(self._title_var)
        title_e.focus_set()

        # Due + Priority on one row
        row = tk.Frame(self, bg=BG)
        row.pack(fill=tk.X, padx=14, pady=(6, 0))

        due_f = tk.Frame(row, bg=BG)
        due_f.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 10))
        tk.Label(due_f, text="Due date (YYYY-MM-DD)", bg=BG, fg=MUTED_FG,
                 font=("Segoe UI", 9)).pack(anchor="w")
        self._due_var = tk.StringVar(value=t.due_date or "" if t else "")
        tk.Entry(due_f, textvariable=self._due_var, width=16,
                 font=("Segoe UI", 11), relief=tk.FLAT, bg="white", fg=TEXT_FG,
                 highlightthickness=1, highlightbackground=BORDER,
                 highlightcolor=ACCENT).pack(fill=tk.X)

        pri_f = tk.Frame(row, bg=BG)
        pri_f.pack(side=tk.LEFT)
        tk.Label(pri_f, text="Priority", bg=BG, fg=MUTED_FG,
                 font=("Segoe UI", 9)).pack(anchor="w")
        self._priority_var = tk.StringVar(value=t.priority if t else "medium")
        ttk.Combobox(pri_f, textvariable=self._priority_var,
                     values=list(PRIORITIES), state="readonly", width=10).pack()

        # Tags
        self._lbl("Tags (comma-separated)")
        self._tags_var = tk.StringVar(value=", ".join(t.tags) if t else "")
        self._entry_widget(self._tags_var)

        # Recurrence + clear-due on one row
        row2 = tk.Frame(self, bg=BG)
        row2.pack(fill=tk.X, padx=14, pady=(6, 0))
        rec_f = tk.Frame(row2, bg=BG)
        rec_f.pack(side=tk.LEFT, padx=(0, 10))
        tk.Label(rec_f, text="Recurrence", bg=BG, fg=MUTED_FG,
                 font=("Segoe UI", 9)).pack(anchor="w")
        self._recur_var = tk.StringVar(value=t.recur or "" if t else "")
        ttk.Combobox(rec_f, textvariable=self._recur_var,
                     values=["", *RECUR_PERIODS], state="readonly", width=12).pack()

        self._clear_due_var = tk.BooleanVar()
        tk.Checkbutton(row2, text="Clear due date", variable=self._clear_due_var,
                       bg=BG, fg=MUTED_FG, font=("Segoe UI", 9),
                       activebackground=BG).pack(side=tk.LEFT, pady=(14, 0))

        # Notes
        self._lbl("Notes")
        self._notes = tk.Text(self, height=4, width=44,
                              font=("Segoe UI", 11), relief=tk.FLAT,
                              bg="white", fg=TEXT_FG,
                              highlightthickness=1,
                              highlightbackground=BORDER, highlightcolor=ACCENT)
        self._notes.pack(fill=tk.X, padx=14, pady=(0, 2))
        if t and t.notes:
            self._notes.insert("1.0", t.notes)

        # Buttons
        btn_row = tk.Frame(self, bg=BG)
        btn_row.pack(fill=tk.X, padx=14, pady=10)
        tk.Button(btn_row, text="Cancel", command=self.destroy,
                  bg=BORDER, fg=TEXT_FG, relief=tk.FLAT,
                  font=("Segoe UI", 10), padx=16, pady=6,
                  cursor="hand2").pack(side=tk.RIGHT, padx=(4, 0))
        tk.Button(btn_row, text="Save", command=self._save,
                  bg=ACCENT, fg="white", relief=tk.FLAT,
                  font=("Segoe UI", 10, "bold"), padx=16, pady=6,
                  cursor="hand2", activebackground=ACCENT_DARK).pack(side=tk.RIGHT)

        self.bind("<Return>", lambda _: self._save())
        self.bind("<Escape>", lambda _: self.destroy())

    def _save(self) -> None:
        title = self._title_var.get().strip()
        if not title:
            messagebox.showwarning("Validation", "Title cannot be empty.", parent=self)
            return
        due = self._due_var.get().strip() or None
        if due:
            try:
                date.fromisoformat(due)
            except ValueError:
                messagebox.showwarning("Validation",
                                       "Invalid date. Use YYYY-MM-DD.", parent=self)
                return
        if self._clear_due_var.get():
            due = None
        self.result = {
            "title":    title,
            "due_date": due,
            "priority": self._priority_var.get(),
            "tags":     [t.strip() for t in self._tags_var.get().split(",") if t.strip()],
            "recur":    self._recur_var.get() or None,
            "notes":    self._notes.get("1.0", tk.END).strip(),
        }
        self.destroy()


# ── Stats dialog ──────────────────────────────────────────────────────────────

class StatsDialog(tk.Toplevel):
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
        total     = len(todos)
        done      = sum(1 for t in todos if t.done)
        pending   = total - done
        overdue   = sum(1 for t in todos if t.is_overdue)
        today_str = date.today().isoformat()
        done_today = sum(
            1 for t in todos
            if t.completed_at and t.completed_at[:10] == today_str
        )
        rate = f"{done/total*100:.0f}%" if total else "—"
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

        tk.Button(self, text="Close", command=self.destroy,
                  bg=ACCENT, fg="white", relief=tk.FLAT,
                  font=("Segoe UI", 10), padx=16, pady=6).pack(pady=(14, 0))


# ── Main application ──────────────────────────────────────────────────────────

class TodoApp(tk.Tk):
    def __init__(self, path: Path = storage.DEFAULT_PATH) -> None:
        super().__init__()
        self._todos_path: Path            = path
        self._selected_id: int | None     = None
        self._undo_snapshot: list[dict] | None = None

        self.geometry("700x560")
        self.minsize(500, 360)
        self.configure(bg=BG)

        self._build_ui()
        self._bind_shortcuts()
        self._setup_tray()
        self._refresh()

    # ── Construction ──────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        self._build_toolbar()
        self._build_filter_bar()
        self._build_add_bar()
        self._build_list_area()
        self._build_status_bar()

    def _build_toolbar(self) -> None:
        bar = tk.Frame(self, bg=TOOLBAR_BG, pady=6, padx=12)
        bar.pack(fill=tk.X)

        tk.Label(bar, text="📋 Todo", bg=TOOLBAR_BG, fg=TEXT_FG,
                 font=("Segoe UI", 13, "bold")).pack(side=tk.LEFT)
        self._path_label = tk.Label(bar, text="", bg=TOOLBAR_BG, fg=MUTED_FG,
                                    font=("Segoe UI", 9))
        self._path_label.pack(side=tk.LEFT, padx=(8, 0))

        for label, cmd in [
            ("New List",  self._new_list),
            ("Open List", self._open_list),
            ("Backup",    self._backup),
            ("Restore",   self._restore),
            ("Stats",     self._show_stats),
        ]:
            tk.Button(bar, text=label, command=cmd,
                      bg=TOOLBAR_BG, fg=MUTED_FG, relief=tk.FLAT,
                      font=("Segoe UI", 9), padx=8, pady=4,
                      cursor="hand2",
                      activebackground="#F3F4F6").pack(side=tk.RIGHT)

        tk.Frame(self, bg=BORDER, height=1).pack(fill=tk.X)

    def _build_filter_bar(self) -> None:
        bar = tk.Frame(self, bg=TOOLBAR_BG, pady=6, padx=12)
        bar.pack(fill=tk.X)

        # Search
        self._search_var = tk.StringVar()
        self._search_var.trace_add("write", lambda *_: self._safe_refresh())
        self._search_entry = tk.Entry(
            bar, textvariable=self._search_var, width=22,
            font=("Segoe UI", 10), relief=tk.FLAT,
            bg="#F3F4F6", fg=MUTED_FG,
            highlightthickness=1, highlightbackground=BORDER,
            highlightcolor=ACCENT,
        )
        self._search_entry.pack(side=tk.LEFT, ipady=4, padx=(0, 10))
        self._search_entry.insert(0, "🔍 Search…")
        self._search_entry.bind("<FocusIn>",  self._search_focus_in)
        self._search_entry.bind("<FocusOut>", self._search_focus_out)

        # Filter
        tk.Label(bar, text="Filter:", bg=TOOLBAR_BG, fg=MUTED_FG,
                 font=("Segoe UI", 9)).pack(side=tk.LEFT)
        self._filter_var = tk.StringVar(value="all")
        self._filter_var.trace_add("write", lambda *_: self._safe_refresh())
        ttk.Combobox(bar, textvariable=self._filter_var,
                     values=["all", "pending", "done", "overdue"],
                     state="readonly", width=10).pack(side=tk.LEFT, padx=(4, 12))

        # Sort
        tk.Label(bar, text="Sort:", bg=TOOLBAR_BG, fg=MUTED_FG,
                 font=("Segoe UI", 9)).pack(side=tk.LEFT)
        self._sort_var = tk.StringVar(value="position")
        self._sort_var.trace_add("write", lambda *_: self._safe_refresh())
        ttk.Combobox(bar, textvariable=self._sort_var,
                     values=["position", "due", "priority", "alpha", "created"],
                     state="readonly", width=12).pack(side=tk.LEFT, padx=(4, 0))

        tk.Frame(self, bg=BORDER, height=1).pack(fill=tk.X)

    def _build_add_bar(self) -> None:
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

        tk.Button(bar, text="Add", command=self._add_todo,
                  bg=ACCENT, fg="white", relief=tk.FLAT,
                  font=("Segoe UI", 11, "bold"), padx=16, pady=6,
                  cursor="hand2",
                  activebackground=ACCENT_DARK).pack(side=tk.LEFT)

    def _build_list_area(self) -> None:
        frame = tk.Frame(self, bg=BG, padx=12)
        frame.pack(fill=tk.BOTH, expand=True)

        # Column headers
        hdr = tk.Frame(frame, bg="#EEF2FF")
        hdr.pack(fill=tk.X)
        for text, lbl_opts, pack_opts in [
            ("",     dict(width=12),                    dict()),
            ("Task", dict(),                             dict(fill=tk.X, expand=True)),
            ("Due",  dict(width=10),                    dict()),
            ("",     dict(width=18),                    dict()),
        ]:
            tk.Label(hdr, text=text, bg="#EEF2FF", fg=MUTED_FG,
                     font=("Segoe UI", 8, "bold"),
                     anchor="w", **lbl_opts).pack(side=tk.LEFT, pady=3, **pack_opts)

        # Scrollable canvas
        self._canvas = tk.Canvas(frame, bg=BG, highlightthickness=0)
        sb = ttk.Scrollbar(frame, orient=tk.VERTICAL, command=self._canvas.yview)
        self._canvas.configure(yscrollcommand=sb.set)
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        self._canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self._list_inner = tk.Frame(self._canvas, bg=BG)
        self._canvas_win = self._canvas.create_window(
            (0, 0), window=self._list_inner, anchor="nw")
        self._list_inner.bind("<Configure>", lambda _e: self._canvas.configure(
            scrollregion=self._canvas.bbox("all")))
        self._canvas.bind("<Configure>", lambda e: self._canvas.itemconfig(
            self._canvas_win, width=e.width))
        self._canvas.bind_all("<MouseWheel>", lambda e: self._canvas.yview_scroll(
            -1 * (e.delta // 120), "units"))

    def _build_status_bar(self) -> None:
        tk.Frame(self, bg=BORDER, height=1).pack(fill=tk.X)
        bar = tk.Frame(self, bg=TOOLBAR_BG, pady=5, padx=12)
        bar.pack(fill=tk.X, side=tk.BOTTOM)

        self._status_var = tk.StringVar()
        tk.Label(bar, textvariable=self._status_var, bg=TOOLBAR_BG,
                 fg=MUTED_FG, font=("Segoe UI", 9)).pack(side=tk.LEFT)

        for label, cmd in [("⬇ Export JSON", self._export),
                            ("↩ Undo",        self._undo)]:
            tk.Button(bar, text=label, command=cmd,
                      bg=TOOLBAR_BG, fg=MUTED_FG, relief=tk.FLAT,
                      font=("Segoe UI", 9), padx=8, pady=3,
                      cursor="hand2",
                      activebackground="#F3F4F6").pack(side=tk.RIGHT, padx=(4, 0))

    # ── Keyboard shortcuts ─────────────────────────────────────────────────────

    def _bind_shortcuts(self) -> None:
        self.bind("<Control-n>", lambda _: (
            self._entry.focus_set(), self._entry.select_range(0, tk.END)))
        self.bind("<Control-z>", lambda _: self._undo())
        self.bind("<Control-e>", lambda _: self._edit_selected())
        self.bind("<Delete>",    lambda _: self._delete_selected())
        self.bind("<space>",     lambda _: self._done_selected())
        self.bind("<Escape>",    lambda _: self._clear_search())
        self.bind("<F5>",        lambda _: self._refresh())

    # ── Search bar helpers ─────────────────────────────────────────────────────

    def _search_focus_in(self, _: tk.Event) -> None:
        if self._search_entry.get() == "🔍 Search…":
            self._search_entry.delete(0, tk.END)
            self._search_entry.config(fg=TEXT_FG)

    def _search_focus_out(self, _: tk.Event) -> None:
        if not self._search_entry.get():
            self._search_entry.insert(0, "🔍 Search…")
            self._search_entry.config(fg=MUTED_FG)

    def _clear_search(self) -> None:
        self._search_entry.delete(0, tk.END)
        self._search_entry.insert(0, "🔍 Search…")
        self._search_entry.config(fg=MUTED_FG)
        self._entry.focus_set()

    def _get_search(self) -> str:
        v = self._search_var.get()
        return "" if v == "🔍 Search…" else v

    # ── Rendering ─────────────────────────────────────────────────────────────

    def _safe_refresh(self) -> None:
        """Refresh only once the list area has been fully constructed."""
        if hasattr(self, "_list_inner"):
            self._refresh()

    def _refresh(self) -> None:
        for w in self._list_inner.winfo_children():
            w.destroy()

        all_todos = storage.load(self._todos_path)
        visible   = storage.filter_todos(
            all_todos,
            filter_by=self._filter_var.get(),
            search=self._get_search(),
            sort_by=self._sort_var.get(),
        )

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

        # Status counts
        pending = sum(1 for t in all_todos if not t.done)
        done    = sum(1 for t in all_todos if t.done)
        overdue = sum(1 for t in all_todos if t.is_overdue)
        parts   = [f"{pending} pending", f"{done} done"]
        if overdue:
            parts.append(f"{overdue} overdue ⚠")
        self._status_var.set("  |  ".join(parts))

        # Window / path label
        stem = self._todos_path.stem
        self._path_label.config(text=f"— {stem}")
        self.title(f"Todo — {stem}")

    def _build_row(self, todo: Todo, bg: str, idx: int, total: int) -> None:
        row = tk.Frame(self._list_inner, bg=bg, cursor="hand2")
        row.pack(fill=tk.X)
        row.bind("<Button-1>",        lambda _: self._select(todo.id))
        row.bind("<Double-Button-1>", lambda _: self._open_edit(todo))

        # Priority dot
        dot = tk.Canvas(row, width=10, height=10, bg=bg, highlightthickness=0)
        dot.create_oval(
            1, 1, 9, 9,
            fill=(PRI_COLORS.get(todo.priority, ACCENT) if not todo.done else BORDER),
            outline="",
        )
        dot.pack(side=tk.LEFT, padx=(8, 2), pady=10)
        dot.bind("<Button-1>", lambda _: self._select(todo.id))

        # ID
        tk.Label(row, text=str(todo.id), bg=bg,
                 fg=DONE_FG if todo.done else MUTED_FG,
                 font=("Segoe UI", 9), width=3, anchor="e").pack(side=tk.LEFT)

        # Status glyph
        glyph = "✓" if todo.done else ("⚠" if todo.is_overdue else "○")
        glyph_fg = (DONE_FG if todo.done else
                    DANGER  if todo.is_overdue else ACCENT)
        tk.Label(row, text=glyph, bg=bg, fg=glyph_fg,
                 font=("Segoe UI", 12), width=2).pack(side=tk.LEFT)

        # Title (with strikethrough when done)
        title_f = tkfont.Font(family="Segoe UI", size=11,
                              overstrike=1 if todo.done else 0)
        title_lbl = tk.Label(row, text=todo.title, bg=bg,
                             fg=DONE_FG if todo.done else TEXT_FG,
                             font=title_f, anchor="w", cursor="hand2")
        title_lbl.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(4, 6), pady=8)
        title_lbl.bind("<Button-1>",        lambda _: self._select(todo.id))
        title_lbl.bind("<Double-Button-1>", lambda _: self._open_edit(todo))

        # Notes indicator with tooltip
        if todo.notes:
            nl = tk.Label(row, text="📝", bg=bg, font=("Segoe UI", 9))
            nl.pack(side=tk.LEFT, padx=2)
            _Tooltip(nl, todo.notes[:200] + ("…" if len(todo.notes) > 200 else ""))

        # Recurrence indicator
        if todo.recur:
            tk.Label(row, text=f"↻{todo.recur[0]}", bg=bg, fg=MUTED_FG,
                     font=("Segoe UI", 8)).pack(side=tk.LEFT, padx=2)

        # Tags (max 3)
        for tag in todo.tags[:3]:
            tk.Label(row, text=f"#{tag}", bg="#EEF2FF", fg=ACCENT,
                     font=("Segoe UI", 8), padx=4, pady=1).pack(
                side=tk.LEFT, padx=(2, 0), pady=6)

        # Due date
        if todo.due_date:
            tk.Label(row, text=_fmt_due(todo.due_date), bg=bg,
                     fg=(_due_color(todo) if not todo.done else DONE_FG),
                     font=("Segoe UI", 9), width=10, anchor="e").pack(
                side=tk.LEFT, padx=4)

        # ── Action buttons ──────────────────────────────────────────────
        # Move down
        if idx < total - 1:
            tk.Button(row, text="↓", bg=bg, fg=MUTED_FG, relief=tk.FLAT,
                      font=("Segoe UI", 9), padx=3, pady=2, cursor="hand2",
                      activebackground=ROW_EVEN,
                      command=lambda tid=todo.id, i=idx: self._move(tid, i + 1)
                      ).pack(side=tk.RIGHT)
        # Move up
        if idx > 0:
            tk.Button(row, text="↑", bg=bg, fg=MUTED_FG, relief=tk.FLAT,
                      font=("Segoe UI", 9), padx=3, pady=2, cursor="hand2",
                      activebackground=ROW_EVEN,
                      command=lambda tid=todo.id, i=idx: self._move(tid, i - 1)
                      ).pack(side=tk.RIGHT)

        # Delete
        tk.Button(row, text="🗑", bg=bg, fg=DANGER, relief=tk.FLAT,
                  font=("Segoe UI", 10), padx=4, pady=2, cursor="hand2",
                  activebackground=ROW_EVEN,
                  command=lambda tid=todo.id: self._delete_todo(tid)
                  ).pack(side=tk.RIGHT, padx=(0, 2), pady=4)

        # Done (only for pending)
        if not todo.done:
            tk.Button(row, text="Done", bg=bg, fg=SUCCESS, relief=tk.FLAT,
                      font=("Segoe UI", 9, "bold"), padx=6, pady=2,
                      cursor="hand2", activebackground=ROW_EVEN,
                      command=lambda tid=todo.id: self._mark_done(tid)
                      ).pack(side=tk.RIGHT, padx=(0, 2), pady=4)

        tk.Frame(self._list_inner, bg=BORDER, height=1).pack(fill=tk.X)

    # ── Actions ───────────────────────────────────────────────────────────────

    def _snapshot(self) -> list[dict]:
        return [t.to_dict() for t in storage.load(self._todos_path)]

    def _add_todo(self) -> None:
        title = self._entry.get().strip()
        if not title:
            self._flash("⚠ Please enter a task title.")
            return
        self._undo_snapshot = self._snapshot()
        storage.add(title, path=self._todos_path)
        self._entry.delete(0, tk.END)
        self._refresh()

    def _mark_done(self, todo_id: int) -> None:
        self._undo_snapshot = self._snapshot()
        storage.mark_done(todo_id, path=self._todos_path)
        self._refresh()

    def _delete_todo(self, todo_id: int) -> None:
        todos = storage.load(self._todos_path)
        todo  = next((t for t in todos if t.id == todo_id), None)
        if todo and not messagebox.askyesno(
            "Delete", f'Delete "{todo.title}"?', parent=self
        ):
            return
        self._undo_snapshot = self._snapshot()
        storage.delete(todo_id, path=self._todos_path)
        if self._selected_id == todo_id:
            self._selected_id = None
        self._refresh()

    def _open_edit(self, todo: Todo) -> None:
        dlg = EditDialog(self, todo)
        self.wait_window(dlg)
        if dlg.result:
            self._undo_snapshot = self._snapshot()
            storage.edit(todo.id, **dlg.result, path=self._todos_path)
            self._refresh()

    def _move(self, todo_id: int, new_idx: int) -> None:
        self._undo_snapshot = self._snapshot()
        storage.move(todo_id, new_idx, path=self._todos_path)
        self._refresh()

    def _undo(self) -> None:
        if self._undo_snapshot is None:
            self._flash("Nothing to undo.")
            return
        storage._save_raw(self._undo_snapshot, self._todos_path)
        self._undo_snapshot = None
        self._refresh()
        self._flash("↩ Undone.")

    def _export(self) -> None:
        path = filedialog.asksaveasfilename(
            defaultextension=".json",
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
            initialfile="todos.json", title="Export todos",
        )
        if path:
            Path(path).write_text(storage.export_json(self._todos_path))
            self._flash(f"✓ Exported to {path}")

    def _new_list(self) -> None:
        path = filedialog.asksaveasfilename(
            defaultextension=".json",
            filetypes=[("JSON files", "*.json")],
            title="New todo list",
        )
        if path:
            self._todos_path      = Path(path)
            self._selected_id     = None
            self._undo_snapshot   = None
            self._refresh()

    def _open_list(self) -> None:
        path = filedialog.askopenfilename(
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
            title="Open todo list",
        )
        if path:
            self._todos_path      = Path(path)
            self._selected_id     = None
            self._undo_snapshot   = None
            self._refresh()

    def _backup(self) -> None:
        path = filedialog.asksaveasfilename(
            defaultextension=".json",
            filetypes=[("JSON files", "*.json")],
            initialfile="todos.backup.json", title="Save backup",
        )
        if path:
            ok = storage.backup(Path(path), src_path=self._todos_path)
            self._flash(f"✓ Backed up to {path}" if ok else "⚠ Nothing to back up.")

    def _restore(self) -> None:
        path = filedialog.askopenfilename(
            filetypes=[("JSON files", "*.json")], title="Restore from backup",
        )
        if path and messagebox.askyesno(
            "Restore", "Overwrite current todos with backup?", parent=self
        ):
            self._undo_snapshot = self._snapshot()
            storage.restore(Path(path), dest_path=self._todos_path)
            self._refresh()
            self._flash("✓ Restored.")

    def _show_stats(self) -> None:
        StatsDialog(self, storage.load(self._todos_path))

    # ── Selection helpers ──────────────────────────────────────────────────────

    def _select(self, todo_id: int) -> None:
        self._selected_id = todo_id
        self._refresh()

    def _edit_selected(self) -> None:
        if not self._selected_id:
            return
        todos = storage.load(self._todos_path)
        todo  = next((t for t in todos if t.id == self._selected_id), None)
        if todo:
            self._open_edit(todo)

    def _delete_selected(self) -> None:
        if self._selected_id:
            self._delete_todo(self._selected_id)

    def _done_selected(self) -> None:
        if self._selected_id:
            self._mark_done(self._selected_id)

    def _flash(self, msg: str) -> None:
        self._status_var.set(msg)
        self.after(3000, lambda: (
            self._refresh() if self._status_var.get() == msg else None
        ))

    # ── System tray ────────────────────────────────────────────────────────────

    def _setup_tray(self) -> None:
        if not _HAS_TRAY:
            return

        def _make_icon() -> "Image.Image":
            img  = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
            draw = ImageDraw.Draw(img)
            draw.ellipse([2, 2, 62, 62], fill="#5B8DEF")
            draw.line([(18, 32), (28, 44)], fill="white", width=5)
            draw.line([(28, 44), (48, 20)], fill="white", width=5)
            return img

        menu = pystray.Menu(
            pystray.MenuItem("Show",  self._show_from_tray, default=True),
            pystray.MenuItem("Quit",  self._quit_app),
        )
        self._tray = pystray.Icon("todo-cli", _make_icon(), "Todo", menu)
        threading.Thread(target=self._tray.run, daemon=True).start()
        self.protocol("WM_DELETE_WINDOW", self._hide_to_tray)

    def _hide_to_tray(self) -> None:
        self.withdraw()

    def _show_from_tray(self, *_) -> None:
        self.after(0, self.deiconify)

    def _quit_app(self, *_) -> None:
        if _HAS_TRAY and hasattr(self, "_tray"):
            self._tray.stop()
        self.after(0, self.destroy)


def main() -> None:
    app = TodoApp()
    app.mainloop()


if __name__ == "__main__":
    main()
