"""存储后端协议 — FileStorageBackend 和 SQLiteStorageBackend 的统一接口"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from mathforge.core.models import (
        DocumentTree,
        ItemRelation,
        KnowledgePoint,
        Question,
        Solution,
        TaxonomyNode,
    )


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


class BatchStorageBackend(StorageBackend, Protocol):
    """支持批量操作的存储后端"""

    def save_batch(
        self,
        kps: list[KnowledgePoint],
        questions: list[tuple[Question, Solution | None]],
    ) -> int: ...

    def update_relations_batch(
        self,
        items: list[tuple[str, str, list[ItemRelation]]],
    ) -> None: ...

    def find_duplicates(self, content: str, item_type: str = "all") -> list[str]: ...

    # ── 树结构持久化 ──

    def save_tree_node(self, node: TaxonomyNode) -> None: ...

    def save_item_placement(
        self, item_id: str, item_type: str, node_id: str, position: int
    ) -> None: ...

    def load_tree(self, source_file: str) -> DocumentTree: ...

    def get_subtree_items(self, node_id: str) -> list[str]: ...
