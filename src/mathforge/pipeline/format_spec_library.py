"""FormatSpec 缓存库 — 跨文档复用已推断的 FormatSpec

按文档指纹精确匹配，或按结构相似度模糊匹配。
持久化为 JSON 文件。
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import structlog

from mathforge.core.models import DocumentFormat, FormatSpec

logger = structlog.get_logger()


def _fingerprint(lines: list[str], source_format: DocumentFormat) -> str:
    """计算文档指纹：source_format + 前50非空行 + 行数的 hash

    使用更多行减少不同文档间的指纹碰撞。
    """
    non_blank = [ln.strip() for ln in lines if ln.strip()][:50]
    raw = f"{source_format.value}|{len(lines)}|" + "\n".join(non_blank)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _block_type_set(spec: FormatSpec) -> set[str]:
    """提取 FormatSpec 中所有 block_type"""
    return {bs.block_type for bs in spec.content_blocks}


class FormatSpecLibrary:
    """FormatSpec 缓存库"""

    def __init__(self, persist_path: Path | None = None) -> None:
        self._persist_path = persist_path
        self._entries: dict[str, dict] = {}  # fingerprint → {spec_data, score, block_types}
        if persist_path and persist_path.exists():
            self._load()

    def get(self, lines: list[str], source_format: DocumentFormat) -> FormatSpec | None:
        """精确指纹匹配"""
        fp = _fingerprint(lines, source_format)
        entry = self._entries.get(fp)
        if entry:
            logger.info("format_spec_library_hit", fingerprint=fp[:8])
            return self._entry_to_spec(entry)
        return None

    def find_similar(
        self,
        lines: list[str],
        source_format: DocumentFormat,
        threshold: float = 0.6,
    ) -> FormatSpec | None:
        """模糊匹配：按 block_type 集合的 Jaccard 相似度

        仅在相同 source_format 的条目中搜索。
        """
        if not self._entries:
            return None

        # 用启发式扫描当前文档的结构类型
        target_types = _detect_structure_types(lines)
        if not target_types:
            return None

        best_score = 0.0
        best_entry = None

        for entry in self._entries.values():
            # 仅匹配相同格式
            if entry.get("source_format") != source_format.value:
                continue
            entry_types = set(entry.get("block_types", []))
            if not entry_types:
                continue
            jaccard = len(target_types & entry_types) / len(target_types | entry_types)
            if jaccard > best_score:
                best_score = jaccard
                best_entry = entry

        if best_score >= threshold and best_entry:
            logger.info(
                "format_spec_library_similar",
                similarity=round(best_score, 2),
            )
            return self._entry_to_spec(best_entry)

        return None

    def put(
        self,
        lines: list[str],
        source_format: DocumentFormat,
        spec: FormatSpec,
        validation_score: float = 0.0,
    ) -> None:
        """存入缓存"""
        fp = _fingerprint(lines, source_format)
        self._entries[fp] = {
            "spec_data": json.loads(spec.model_dump_json()),
            "score": validation_score,
            "block_types": list(_block_type_set(spec)),
            "source_format": source_format.value,
        }
        self._save()
        logger.info("format_spec_library_stored", fingerprint=fp[:8])

    def _entry_to_spec(self, entry: dict) -> FormatSpec:
        return FormatSpec.model_validate(entry["spec_data"])

    def _load(self) -> None:
        if not self._persist_path:
            return
        try:
            data = json.loads(self._persist_path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                logger.warning("format_spec_library_invalid_data", type=type(data).__name__)
                return
            # 验证每个 entry 有必须的 spec_data 字段
            self._entries = {
                k: v for k, v in data.items() if isinstance(v, dict) and "spec_data" in v
            }
        except Exception:
            logger.warning("format_spec_library_load_failed", exc_info=True)

    def _save(self) -> None:
        if not self._persist_path:
            return
        try:
            self._persist_path.parent.mkdir(parents=True, exist_ok=True)
            self._persist_path.write_text(
                json.dumps(self._entries, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception:
            logger.warning("format_spec_library_save_failed", exc_info=True)


# 预编译结构检测正则，避免每次调用重新编译
import re  # noqa: E402

_IM = re.IGNORECASE | re.MULTILINE
_STRUCTURE_PATTERNS: dict[str, re.Pattern[str]] = {
    "definition": re.compile(r"^\s*(?:定义|Definition)\s*[\d.]", _IM),
    "theorem": re.compile(r"^\s*(?:定理|Theorem)\s*[\d.]", _IM),
    "lemma": re.compile(r"^\s*(?:引理|Lemma)\s*[\d.]", _IM),
    "corollary": re.compile(r"^\s*(?:推论|Corollary)\s*[\d.]", _IM),
    "proposition": re.compile(r"^\s*(?:命题|Proposition)\s*[\d.]", _IM),
    "example": re.compile(r"^\s*(?:例\s*[\d.]|例题|Example)", _IM),
    "proof": re.compile(r"^\s*(?:证明|Proof)[.\s:：]", _IM),
    "exercise": re.compile(r"^\s*(?:习题|练习|Exercise|Problem)\s*[\d.]", _IM),
    "remark": re.compile(r"^\s*(?:注\s*[\d.]|注记|Remark)", _IM),
}


def _detect_structure_types(lines: list[str]) -> set[str]:
    """快速扫描文档行，检测可能存在的结构类型

    使用行首锚定匹配 (^) 减少误匹配。
    """
    found: set[str] = set()
    # 只扫描前 500 行以提高性能
    for line in lines[:500]:
        for block_type, pattern in _STRUCTURE_PATTERNS.items():
            if block_type not in found and pattern.match(line):
                found.add(block_type)
    return found
