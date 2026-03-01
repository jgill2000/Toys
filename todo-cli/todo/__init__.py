"""todo-cli package.

Exports the most commonly used symbols so consumers can write::

    from todo import Todo, Priority, FilterOptions, TodoRepository

rather than drilling into sub-modules.
"""

from .models  import Priority, Recur, Todo, PRIORITIES, RECUR_PERIODS
from .queries import FilterOptions, filter_todos
from .storage import DEFAULT_PATH, TodoRepository

__all__ = [
    # Domain model
    "Todo", "Priority", "Recur", "PRIORITIES", "RECUR_PERIODS",
    # Query layer
    "FilterOptions", "filter_todos",
    # Storage
    "TodoRepository", "DEFAULT_PATH",
]
