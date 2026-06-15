"""存储后端协议 — SQLiteStorageBackend 的统一接口"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from sciforge.core.models import Figure, KnowledgePoint, Question


@runtime_checkable
class StorageBackend(Protocol):
    """存储后端统一协议"""

    def save_question(self, question: Question) -> str: ...

    def load_question(self, id: str) -> Question: ...

    def search_questions(self, filters: dict[str, object]) -> list[str]: ...

    def question_count(self) -> int: ...

    def save_knowledge_point(self, kp: KnowledgePoint) -> str: ...

    def load_knowledge_point(self, id: str) -> KnowledgePoint: ...

    def search_knowledge(self, filters: dict[str, object]) -> list[str]: ...

    def knowledge_count(self) -> int: ...


class BatchStorageBackend(StorageBackend, Protocol):
    """支持批量操作的存储后端"""

    def save_batch(
        self,
        kps: list[KnowledgePoint],
        questions: list[Question],
    ) -> int: ...

    def find_duplicates(self, content: str, item_type: str = "all") -> list[str]: ...

    def count_existing_content(self, contents: list[str], scope: str) -> int: ...

    def save_figures(self, figures: list[Figure]) -> int: ...
