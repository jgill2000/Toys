# Running todo-cli

## Prerequisites

Install [uv](https://docs.astral.sh/uv/getting-started/installation/) — the only tool you need:

```powershell
# Windows (PowerShell)
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

---

## Start the UI

```powershell
cd todo-cli
uv run python todo_ui.py
```

That's it. `uv` will automatically create a virtual environment and install dependencies the first time.

---

## UI quick-reference

| Action | How |
|---|---|
| Add a todo | Type in the top box → **Enter** or **Add** button |
| Mark done | Click **Done** on a row, or select it and press **Space** |
| Edit a todo | Double-click the row (or select + **Ctrl+E**) |
| Delete a todo | Click 🗑, or select + **Delete** (confirms first) |
| Reorder | Use **↑ ↓** buttons on each row |
| Search | Type in the 🔍 Search box (live) |
| Filter | Use the **Filter** dropdown (all / pending / done / overdue) |
| Sort | Use the **Sort** dropdown (position / due / priority / alpha) |
| Export JSON | **⬇ Export JSON** in the status bar |
| Undo | **↩ Undo** in the status bar, or **Ctrl+Z** |
| New / open list | Toolbar buttons (each list is a separate `.json` file) |
| Backup / Restore | Toolbar buttons |
| Stats | Toolbar button |

### Keyboard shortcuts

| Key | Action |
|---|---|
| `Ctrl+N` | Focus the add box |
| `Ctrl+E` | Edit selected todo |
| `Ctrl+Z` | Undo last action |
| `Delete` | Delete selected todo |
| `Space` | Toggle done on selected todo |
| `Escape` | Clear search |
| `F5` | Refresh |

---

## Use the CLI instead

```powershell
cd todo-cli

uv run todo add "Buy milk"
uv run todo add "Ship feature" --due 2026-03-10 --priority high --tags work
uv run todo list
uv run todo list --filter pending --sort priority
uv run todo list --search "milk"
uv run todo done 1
uv run todo edit 2 --title "Ship the feature" --tags "work,urgent"
uv run todo delete 3
uv run todo move 2 1          # move id 2 to position 1
uv run todo export            # print JSON
uv run todo export -o out.json
uv run todo backup            # saves todos.backup.json next to todos.json
uv run todo restore backup.json
```

Todos are stored at `~/.todo-cli/todos.json` by default.  
Override with `--file PATH` or the `$TODO_FILE` environment variable.

---

## Run the tests

```powershell
cd todo-cli
uv run pytest -v
```
