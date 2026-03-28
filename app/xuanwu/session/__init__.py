"""

Session management module

Includes:
- context:Session context definitions(SessionKey, SessionScope, SessionMetadata etc.)
- manager:Session manager(CRUD, storage,)
- queue:Session serialization queue(SessionQueue)
- storage:storage adapter
"""

from app.xuanwu.session.context import (
    SessionScope,
    ChatType,
    SessionKey,
    SessionKeyFactory,
    IdentityLinks,
    SessionOrigin,
    SessionMetadata,
    TranscriptEntry,
)
from app.xuanwu.session.queue import SessionQueue, QueueMode
from app.xuanwu.session.router import SessionManagerRouter

__all__ = [
    "SessionScope",
    "ChatType", 
    "SessionKey",
    "SessionKeyFactory",
    "IdentityLinks",
    "SessionOrigin",
    "SessionMetadata",
    "TranscriptEntry",
    "SessionQueue",
    "QueueMode",
    "SessionManagerRouter",
]
