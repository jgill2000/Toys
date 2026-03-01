# todo-cli

[![CI](https://github.com/jonathanmv/Toys/actions/workflows/todo-ci.yml/badge.svg)](https://github.com/jonathanmv/Toys/actions/workflows/todo-ci.yml)

A tiny, zero-dependency CLI todo manager. Todos are stored as a JSON file — no database required.

## Features

- **add** — create a new todo
- **list** — display all todos with status indicators
- **done** — mark a todo complete by ID
- **export** — dump todos to JSON (stdout or file)

## Installation

Requires Python 3.10+.

```bash
# with pip
pip install -e ./todo-cli

# with uv (recommended)
uv pip install -e ./todo-cli
```

Once installed the `todo` command is available globally.

## Usage

```bash
# Add todos
todo add Buy milk
todo add "Write the quarterly report"

# List todos
todo list
#   ○ [1] Buy milk
#   ○ [2] Write the quarterly report

# Mark done
todo done 1
# Done  [1] Buy milk

todo list
#   ✓ [1] Buy milk
#   ○ [2] Write the quarterly report

# Export to stdout
todo export

# Export to a file
todo export --output todos.json
```

### Custom storage path

By default todos are stored at `~/.todo-cli/todos.json`.  
Override per-command with `--file` or globally with the `$TODO_FILE` env var:

```bash
TODO_FILE=./my-todos.json todo list
todo --file ./my-todos.json add "Custom path"
```

## Running tests

```bash
# with uv (creates a venv automatically)
cd todo-cli
uv run pytest -v
```

## Project structure

```
todo-cli/
├── todo/
│   ├── __init__.py
│   ├── models.py    # Todo dataclass
│   ├── storage.py   # JSON file persistence
│   └── cli.py       # argparse CLI entry point
├── tests/
│   └── test_todo.py # 19 tests (model, storage, CLI)
└── pyproject.toml
```
