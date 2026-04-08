"""JSON 文件 → SQLite 迁移工具"""

from __future__ import annotations

import json
from pathlib import Path

import structlog

from mathforge.core.models import KnowledgePoint, Question, Solution
from mathforge.storage.sqlite_backend import SQLiteStorageBackend

logger = structlog.get_logger()


class JsonToSqliteMigrator:
    """从 JSON 文件存储迁移到 SQLite"""

    def __init__(self, source_dir: Path, target_db: Path) -> None:
        self.source_dir = source_dir
        self.backend = SQLiteStorageBackend(target_db)
        self.stats = {"kp": 0, "questions": 0, "errors": 0, "skipped": 0}

    def migrate(self) -> dict:
        """执行迁移，返回统计信息"""
        # 迁移知识点
        kp_dir = self.source_dir / "knowledge"
        if kp_dir.exists():
            for json_file in kp_dir.rglob("*.json"):
                if json_file.name == "index.json":
                    continue
                self._migrate_kp(json_file)

        # 迁移题目
        q_dir = self.source_dir / "questions"
        if q_dir.exists():
            for json_file in q_dir.rglob("*.json"):
                if json_file.name == "index.json":
                    continue
                self._migrate_question(json_file)

        logger.info("migration_complete", **self.stats)
        return self.stats

    def _migrate_kp(self, json_path: Path) -> None:
        try:
            data = json.loads(json_path.read_text(encoding="utf-8"))
            kp = KnowledgePoint.model_validate(data)

            # 检查重复
            duplicates = self.backend.find_duplicates(kp.content_latex, "kp")
            if duplicates:
                self.stats["skipped"] += 1
                return

            self.backend.save_knowledge_point(kp)
            self.stats["kp"] += 1
        except Exception:
            logger.error("migrate_kp_failed", path=str(json_path), exc_info=True)
            self.stats["errors"] += 1

    def _migrate_question(self, json_path: Path) -> None:
        try:
            data = json.loads(json_path.read_text(encoding="utf-8"))
            sol_data = data.pop("solution", None)

            q = Question.model_validate(data)

            duplicates = self.backend.find_duplicates(q.content_latex, "question")
            if duplicates:
                self.stats["skipped"] += 1
                return

            sol = Solution.model_validate(sol_data) if sol_data else None
            self.backend.save_question(q, sol)
            self.stats["questions"] += 1
        except Exception:
            logger.error("migrate_q_failed", path=str(json_path), exc_info=True)
            self.stats["errors"] += 1

    def close(self) -> None:
        self.backend.close()
