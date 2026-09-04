"""确定性 Concept Linker（v11 子系统 IV / M5）。

把已入库条目的受控 ``taxonomy_path`` 提升为稳定 concept identity，并将 ``tags``
降为别名/展示层。全流程不调用 LLM：

1. 合法 taxonomy 完整路径直接作为 concept id；
2. 为路径祖先建立 ``part_of`` 层级边；
3. tag 中英文注册为分支作用域别名，跨书可复用；
4. 未命中 taxonomy/既有别名的 tag 建 ``pending`` 结点，保留信息但不进入默认生成池。

这样既不让模型随意创造的 tag 污染权威图，又不会为了“干净”而静默丢掉未知概念。
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from collections.abc import Mapping
from typing import Any

from sciforge.core.models import (
    ConceptAlias,
    ConceptLinkBatch,
    ConceptNode,
    ConceptRelation,
    ConceptRole,
    ConceptStatus,
    Discipline,
    ItemConceptLink,
    RelationType,
)
from sciforge.disciplines.registry import DISCIPLINE_REGISTRY, DisciplineSpec
from sciforge.pipeline.taxonomy_validator import TaxonomyValidator

_SLUG_RE = re.compile(r"[^a-z0-9_]+")
_UNDERSCORE_RE = re.compile(r"_+")
_DEFINITION_LIKE = frozenset({"definition", "law", "principle", "model"})
_METHOD_LIKE = frozenset({"method", "proof_technique", "formula"})
_THEOREM_LIKE = frozenset({"theorem", "lemma", "corollary", "proposition"})


def normalize_concept_alias(value: str) -> str:
    """NFKC + casefold 的稳定别名键；英文分隔符统一为下划线。"""
    normalized = unicodedata.normalize("NFKC", value).strip().casefold()
    normalized = normalized.replace("-", "_")
    normalized = "_".join(normalized.split())
    return _UNDERSCORE_RE.sub("_", normalized).strip("_")


def _slug_segment(value: str) -> str:
    normalized = normalize_concept_alias(value)
    slug = _UNDERSCORE_RE.sub("_", _SLUG_RE.sub("_", normalized)).strip("_")
    if slug:
        return slug
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]
    return f"unknown_{digest}"


def _split_tag(segment: str) -> tuple[str, str]:
    if ":" not in segment:
        return _slug_segment(segment), segment.strip()
    english, chinese = segment.split(":", 1)
    return _slug_segment(english), chinese.strip()


def _item_role(item_kind: str, item_type: str) -> ConceptRole:
    if item_kind == "question":
        return ConceptRole.TESTS
    if item_type in _DEFINITION_LIKE:
        return ConceptRole.DEFINES
    return ConceptRole.PRIMARY


def _concept_kind(item_type: str, *, is_branch: bool = False) -> str:
    if is_branch:
        return "domain"
    if item_type in _METHOD_LIKE:
        return "method"
    if item_type in _THEOREM_LIKE:
        return "theorem_named"
    return "concept"


def _spec_for(discipline: Discipline) -> DisciplineSpec | None:
    try:
        return DISCIPLINE_REGISTRY.get(discipline)
    except KeyError:
        return None


def _branch_for_row(
    row: Mapping[str, Any],
    *,
    discipline: Discipline,
    taxonomy_path: str,
    tags: list[str],
    spec: DisciplineSpec | None,
) -> str:
    parts = taxonomy_path.split(".") if taxonomy_path else []
    if len(parts) >= 2 and parts[0] == discipline.value:
        candidate = _slug_segment(parts[1])
        if spec is None or not spec.allowed_branches or candidate in spec.allowed_branches:
            return candidate

    for segment in tags[1:]:
        candidate, _ = _split_tag(segment)
        if spec is not None and candidate in spec.allowed_branches:
            return candidate

    if spec is not None:
        inferred = spec.infer_branch(
            str(row.get("subject") or ""),
            " ".join(_split_tag(segment)[1] for segment in tags),
        )
        if inferred:
            return _slug_segment(inferred)
    return "unclassified"


def _tag_label_for_token(tags: list[str], token: str) -> str:
    for segment in reversed(tags):
        english, chinese = _split_tag(segment)
        if english == token and chinese:
            return chinese
    return ""


def link_items_to_concepts(
    rows: list[dict[str, Any]],
    *,
    discipline: Discipline | str,
    taxonomy: TaxonomyValidator | None = None,
    existing_aliases: Mapping[tuple[str, str], str] | None = None,
) -> ConceptLinkBatch:
    """把一批已入库条目确定性映射到 concept graph。

    ``existing_aliases`` 的 key 为 ``(branch, normalized_alias)``。它来自持久化
    ``concept_aliases`` 表，因此同义 tag 在下一本书中会直接复用已有 concept id。
    """
    disc = Discipline(discipline)
    validator = taxonomy or TaxonomyValidator()
    spec = _spec_for(disc)
    known_aliases = dict(existing_aliases or {})

    concept_by_id: dict[str, ConceptNode] = {}
    alias_by_key: dict[tuple[str, str], ConceptAlias] = {}
    conflicted_aliases: set[tuple[str, str]] = set()
    link_by_key: dict[tuple[str, str, str], ItemConceptLink] = {}
    relation_by_key: dict[tuple[str, str, str], ConceptRelation] = {}
    pending_ids: set[str] = set()

    def add_concept(node: ConceptNode) -> None:
        previous = concept_by_id.get(node.id)
        if previous is None or (
            previous.status == ConceptStatus.PENDING and node.status == ConceptStatus.ACTIVE
        ):
            concept_by_id[node.id] = node

    def add_alias(alias: ConceptAlias) -> None:
        normalized = normalize_concept_alias(alias.alias)
        if not normalized:
            return
        key = (alias.branch, normalized)
        if key in conflicted_aliases:
            return
        normalized_alias = alias.model_copy(update={"alias": normalized})
        previous = alias_by_key.get(key)
        if previous is not None and previous.concept_id != normalized_alias.concept_id:
            # 同一分支内出现歧义时宁可不自动归并，交给治理层处理。
            alias_by_key.pop(key, None)
            conflicted_aliases.add(key)
            return
        alias_by_key[key] = normalized_alias

    def add_relation(edge: ConceptRelation) -> None:
        if edge.src_concept == edge.dst_concept:
            return
        key = (edge.src_concept, edge.dst_concept, edge.relation_type.value)
        relation_by_key[key] = edge

    for row in rows:
        item_id = str(row.get("id") or "")
        if not item_id:
            continue
        item_kind = "question" if row.get("kind") == "question" else "knowledge"
        item_type = str(row.get("type") or "")
        raw_tags = row.get("tags")
        tags = [str(tag) for tag in raw_tags] if isinstance(raw_tags, list) else []

        raw_taxonomy = str(row.get("taxonomy_path") or "")
        corrected_path, _ = validator.validate(raw_taxonomy, disc.value)
        normalized_raw_path = validator.normalize_path(raw_taxonomy)
        # TaxonomyValidator 的“最长合法前缀”适合 Stage3 兜底展示，但 concept identity
        # 不能把 ``math.analysis.unknown`` 静默折叠为宽泛的 ``math.analysis``。
        # 精确命中或同深度 typo/fuzzy 修正可以 active；深度缩短则进入 pending 治理。
        same_depth = len(normalized_raw_path.split(".")) == len(corrected_path.split("."))
        controlled_path = (
            validator.normalize_path(corrected_path)
            if corrected_path and validator.has_path(corrected_path) and same_depth
            else ""
        )
        branch = _branch_for_row(
            row,
            discipline=disc,
            taxonomy_path=controlled_path,
            tags=tags,
            spec=spec,
        )

        leaf_en, leaf_zh = _split_tag(tags[-1]) if tags else ("unclassified", "未分类")
        primary_concept_id = ""

        if controlled_path:
            ancestors = validator.ancestor_paths(controlled_path)
            previous_id = ""
            for path in ancestors:
                parts = path.split(".")
                canonical_en = _slug_segment(parts[-1])
                canonical_zh = validator.label_for_path(path)
                if path == controlled_path and leaf_zh:
                    canonical_zh = canonical_zh or leaf_zh
                canonical_zh = canonical_zh or _tag_label_for_token(tags, canonical_en)
                node = ConceptNode(
                    id=path,
                    discipline=disc,
                    branch=branch,
                    canonical_en=canonical_en,
                    canonical_zh=canonical_zh or canonical_en,
                    tag_path="/".join(tags) if path == controlled_path else path,
                    kind=_concept_kind(item_type, is_branch=len(parts) == 2),
                    status=ConceptStatus.ACTIVE,
                    depth=max(1, len(parts) - 1),
                )
                add_concept(node)
                add_alias(
                    ConceptAlias(
                        discipline=disc,
                        branch=branch,
                        alias=canonical_en,
                        concept_id=path,
                        locale="en",
                        source="taxonomy",
                    )
                )
                if node.canonical_zh:
                    add_alias(
                        ConceptAlias(
                            discipline=disc,
                            branch=branch,
                            alias=node.canonical_zh,
                            concept_id=path,
                            locale="zh",
                            source="taxonomy",
                        )
                    )
                if previous_id:
                    add_relation(
                        ConceptRelation(
                            src_concept=path,
                            dst_concept=previous_id,
                            relation_type=RelationType.PART_OF,
                            confidence=1.0,
                            source="structural",
                            note="taxonomy 层级",
                        )
                    )
                previous_id = path
            primary_concept_id = controlled_path

            # 当前 tag 叶段是 taxonomy leaf 的展示别名，而不是另造一个 concept。
            for alias_value, locale in ((leaf_en, "en"), (leaf_zh, "zh")):
                if alias_value:
                    add_alias(
                        ConceptAlias(
                            discipline=disc,
                            branch=branch,
                            alias=alias_value,
                            concept_id=primary_concept_id,
                            locale=locale,  # type: ignore[arg-type]
                            source="pipeline",
                            confidence=0.9,
                        )
                    )
        else:
            # 先尝试复用跨书持久化别名；只在同一 branch 内匹配，避免同名概念串线。
            primary_concept_id = known_aliases.get(
                (branch, normalize_concept_alias(leaf_en)), ""
            ) or known_aliases.get((branch, normalize_concept_alias(leaf_zh)), "")

            if not primary_concept_id:
                primary_concept_id = f"{disc.value}.{branch}.{_slug_segment(leaf_en)}"
                pending = ConceptNode(
                    id=primary_concept_id,
                    discipline=disc,
                    branch=branch,
                    canonical_en=_slug_segment(leaf_en),
                    canonical_zh=leaf_zh or leaf_en,
                    tag_path="/".join(tags),
                    kind=_concept_kind(item_type),
                    status=ConceptStatus.PENDING,
                    depth=max(1, len(tags) - 1),
                )
                add_concept(pending)
                pending_ids.add(primary_concept_id)
                for alias_value, locale in ((leaf_en, "en"), (leaf_zh, "zh")):
                    if alias_value:
                        add_alias(
                            ConceptAlias(
                                discipline=disc,
                                branch=branch,
                                alias=alias_value,
                                concept_id=primary_concept_id,
                                locale=locale,  # type: ignore[arg-type]
                                source="pipeline",
                                confidence=0.6,
                            )
                        )

        if not primary_concept_id:
            continue
        role = _item_role(item_kind, item_type)
        link = ItemConceptLink(
            item_id=item_id,
            item_kind=item_kind,  # type: ignore[arg-type]
            concept_id=primary_concept_id,
            role=role,
            weight=1.0,
        )
        link_by_key[(link.item_id, link.concept_id, link.role.value)] = link

    return ConceptLinkBatch(
        concepts=sorted(concept_by_id.values(), key=lambda node: node.id),
        aliases=sorted(
            alias_by_key.values(), key=lambda alias: (alias.branch, alias.alias, alias.concept_id)
        ),
        item_links=sorted(
            link_by_key.values(), key=lambda link: (link.item_id, link.concept_id, link.role.value)
        ),
        relations=sorted(
            relation_by_key.values(),
            key=lambda edge: (
                edge.src_concept,
                edge.dst_concept,
                edge.relation_type.value,
            ),
        ),
        pending_concept_ids=sorted(pending_ids),
    )


def aggregate_item_relations_to_concepts(
    rows: list[dict[str, Any]],
) -> list[ConceptRelation]:
    """把 item-level 关系证据聚合成 concept-level 边。

    输入行含 ``src_concept/dst_concept/relation_type/confidence``。相同概念对的
    多条跨书证据以平均置信度 + evidence_count 合并，幂等写入时由存储层继续累计。
    """
    grouped: dict[tuple[str, str, str], list[float]] = {}
    for row in rows:
        src = str(row.get("src_concept") or "")
        dst = str(row.get("dst_concept") or "")
        relation = str(row.get("relation_type") or RelationType.RELATED.value)
        if not src or not dst or src == dst:
            continue
        try:
            relation_type = RelationType(relation)
        except ValueError:
            relation_type = RelationType.RELATED
        key = (src, dst, relation_type.value)
        grouped.setdefault(key, []).append(float(row.get("confidence") or 0.0))

    result: list[ConceptRelation] = []
    for (src, dst, relation), scores in sorted(grouped.items()):
        result.append(
            ConceptRelation(
                src_concept=src,
                dst_concept=dst,
                relation_type=RelationType(relation),
                confidence=round(sum(scores) / len(scores), 4),
                source="structural",
                evidence_count=len(scores),
                note="由 item_relations 聚合",
            )
        )
    return result
