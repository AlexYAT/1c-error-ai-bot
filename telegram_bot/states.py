"""
Conversation states for /new flow.
"""
from enum import IntEnum


class NewErrorState(IntEnum):
    """Состояния диалога добавления новой ошибки."""
    ASK_BASE = 0
    ASK_ZIP = 1
    DONE = 2


class StatusState(IntEnum):
    """Состояния при смене статуса на resolved (ожидание комментария)."""
    IDLE = 0
    AWAIT_COMMENT = 1


class QuickIngestState(IntEnum):
    """Сценарий: пользователь отправил zip без /new, ждём название базы."""
    WAITING_FOR_BASE_AFTER_FILE = 0
