"""
Task dispatch phase for validators.

Handles sending task specifications to miners via dendrite and collecting responses.
"""

from .mixin import TaskDispatchMixin

__all__ = ["TaskDispatchMixin"]
