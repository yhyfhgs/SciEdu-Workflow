"""存储后端协议 — FileStorageBackend 和 SQLiteStorageBackend 的统一接口"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from mathforge.core.models import KnowledgePoint, Question, Solution


@runtime_checkable
class StorageBackend(Protocol):
    """存储后端统一协议"""

    def save_question(self, question: Question, solution: Solution | None = None) -> str: ...

    def load_question(self, id: str) -> Question: ...

    def search_questions(self, filters: dict) -> list[str]: ...

    def question_count(self) -> int: ...

    def save_knowledge_point(self, kp: KnowledgePoint) -> str: ...

    def load_knowledge_point(self, id: str) -> KnowledgePoint: ...

    def search_knowledge(self, filters: dict) -> list[str]: ...

    def knowledge_count(self) -> int: ...
