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


@runtime_checkable
class MultiTenantStorageBackend(BatchStorageBackend, Protocol):
    """多租户题库存储协议（产品化 / 共享云端后端用）。

    在 BatchStorageBackend 基础上加三类能力，**不破坏单租户实现**：

    1. **tenant_id 行级隔离检索**：租户仅可见自己的题（``tenant_id == 租户``）
       + 公共题库（``tenant_id == ''``，全局只读）。跨租户查询一律拒绝。
    2. **语义向量检索**：给定 query embedding 返回 (item_id, 相似度)；
       SQLite 实现可线性 cosine 扫描，PostgreSQL/pgvector 实现下推到 DB。
    3. **embedding 写入**：将题目/知识点的向量持久化，供语义检索。

    领域约定：**题目（Question）是租户私有产物**（生成题归属租户），按 tenant_id 隔离；
    **知识点（KnowledgePoint）是共享公共语料**（教材抽取），无租户归属，经基类
    ``search_knowledge`` 全局访问，其向量恒为公共（tenant_id=''）。

    用户 / 租户 / API Key / 计费等**账户元数据由 SaasStore 独立管理**，
    不在本协议内（题库与 SaaS 元数据解耦，契合私有化交付）。
    """

    def search_questions_for_tenant(
        self,
        filters: dict[str, object],
        *,
        tenant_id: str,
        include_public: bool = True,
    ) -> list[str]:
        """租户作用域内的题目检索（含公共题库，除非 include_public=False）。"""
        ...

    def count_questions_for_tenant(self, tenant_id: str, *, include_public: bool = True) -> int:
        """租户可见题目计数（计费 / 统计用）。"""
        ...

    def save_embedding(
        self, item_id: str, vector: list[float], *, kind: str = "question"
    ) -> None:
        """持久化某题目/知识点的向量（kind ∈ {question, knowledge_point}）。"""
        ...

    def semantic_search(
        self,
        query_embedding: list[float],
        *,
        tenant_id: str = "",
        kind: str = "question",
        limit: int = 10,
        include_public: bool = True,
    ) -> list[tuple[str, float]]:
        """向量语义检索，返回按相似度降序的 (item_id, cosine) 列表。"""
        ...
