"""JSON 索引管理"""

from __future__ import annotations

import json
from pathlib import Path


class IndexManager:
    """管理 JSON 索引文件，加速查找"""

    def __init__(self, index_path: Path):
        self.index_path = index_path
        self._cache: dict | None = None

    def _load(self) -> dict:
        if self._cache is None:
            if self.index_path.exists():
                self._cache = json.loads(self.index_path.read_text(encoding="utf-8"))
            else:
                self._cache = {}
        return self._cache

    def _save(self) -> None:
        self.index_path.parent.mkdir(parents=True, exist_ok=True)
        self.index_path.write_text(
            json.dumps(self._cache, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def add(self, id: str, metadata: dict) -> None:
        data = self._load()
        data[id] = metadata
        self._save()

    def get(self, id: str) -> dict | None:
        return self._load().get(id)

    def remove(self, id: str) -> bool:
        data = self._load()
        if id in data:
            del data[id]
            self._save()
            return True
        return False

    def query(self, filters: dict) -> list[str]:
        """按过滤条件查询，返回匹配的 ID 列表"""
        data = self._load()
        results = []
        for id, meta in data.items():
            if self._matches(meta, filters):
                results.append(id)
        return results

    def count(self) -> int:
        return len(self._load())

    def all_ids(self) -> list[str]:
        return list(self._load().keys())

    def _matches(self, meta: dict, filters: dict) -> bool:
        for key, value in filters.items():
            if key == "tags" and isinstance(value, list):
                if not set(value) & set(meta.get("tags", [])):
                    return False
            elif key == "difficulty_range":
                d = meta.get("difficulty", 5)
                if not (value[0] <= d <= value[1]):
                    return False
            elif key == "exclude_ids":
                continue  # handled externally
            elif meta.get(key) != value:
                return False
        return True
