"""基于 JSON 文件的存储后端"""

from __future__ import annotations

import json
from pathlib import Path

import structlog

from mathforge.core.models import KnowledgePoint, Question, Solution
from mathforge.storage.index import IndexManager

logger = structlog.get_logger()


class FileStorageBackend:
    """基于 JSON 文件的存储后端"""

    def __init__(self, base_path: Path):
        self.base_path = base_path
        self._question_index = IndexManager(base_path / "questions" / "index.json")
        self._knowledge_index = IndexManager(base_path / "knowledge" / "index.json")
        self._ensure_directories()

    def _ensure_directories(self) -> None:
        (self.base_path / "questions").mkdir(parents=True, exist_ok=True)
        (self.base_path / "knowledge").mkdir(parents=True, exist_ok=True)

    # ── Question ──────────────────────────────────────

    def save_question(self, question: Question, solution: Solution | None = None) -> str:
        path = self._question_dir(question)
        path.mkdir(parents=True, exist_ok=True)

        # 写 JSON
        json_path = path / f"{question.id}.json"
        data = json.loads(question.model_dump_json(exclude={"embedding"}))
        if solution:
            data["solution"] = json.loads(solution.model_dump_json())
        json_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

        # 写 LaTeX
        tex_path = path / f"{question.id}.tex"
        tex_path.write_text(question.content_latex, encoding="utf-8")

        if solution and solution.solution_latex:
            sol_path = path / f"{question.id}_sol.tex"
            sol_path.write_text(solution.solution_latex, encoding="utf-8")

        # 更新索引
        self._question_index.add(
            question.id,
            {
                "subject": question.subject.value,
                "difficulty": question.difficulty,
                "tags": question.tags,
                "type": question.question_type.value,
                "is_generated": question.is_generated,
                "path": str(json_path.relative_to(self.base_path)),
            },
        )
        return question.id

    def load_question(self, id: str) -> Question:
        entry = self._question_index.get(id)
        if not entry:
            raise FileNotFoundError(f"Question {id} not found")
        json_path = self.base_path / entry["path"]
        return Question.model_validate_json(json_path.read_text(encoding="utf-8"))

    def search_questions(self, filters: dict) -> list[str]:
        return self._question_index.query(filters)

    def question_count(self) -> int:
        return self._question_index.count()

    # ── KnowledgePoint ────────────────────────────────

    def save_knowledge_point(self, kp: KnowledgePoint) -> str:
        subject_dir = self._knowledge_dir(kp)
        subject_dir.mkdir(parents=True, exist_ok=True)

        json_path = subject_dir / f"{kp.id}.json"
        json_path.write_text(
            kp.model_dump_json(indent=2, exclude={"embedding"}, exclude_none=True),
            encoding="utf-8",
        )

        self._knowledge_index.add(
            kp.id,
            {
                "title": kp.title,
                "kp_type": kp.kp_type.value,
                "difficulty": kp.difficulty,
                "tags": kp.tags,
                "path": str(json_path.relative_to(self.base_path)),
            },
        )
        return kp.id

    def load_knowledge_point(self, id: str) -> KnowledgePoint:
        entry = self._knowledge_index.get(id)
        if not entry:
            raise FileNotFoundError(f"KnowledgePoint {id} not found")
        json_path = self.base_path / entry["path"]
        return KnowledgePoint.model_validate_json(json_path.read_text(encoding="utf-8"))

    def search_knowledge(self, filters: dict) -> list[str]:
        return self._knowledge_index.query(filters)

    def knowledge_count(self) -> int:
        return self._knowledge_index.count()

    # ── 路径 ──────────────────────────────────────────

    def _question_dir(self, q: Question) -> Path:
        if q.source and q.source.exam_name:
            exam_type = q.source.exam_type.value if q.source.exam_type else "other"
            if q.source.month:
                year_month = f"{q.source.year}-{q.source.month:02d}"
            else:
                year_month = str(q.source.year)
            return self.base_path / "questions" / "xinlingjun" / exam_type / year_month
        elif q.is_generated:
            return self.base_path / "questions" / "generated"
        else:
            return self.base_path / "questions" / "manual"

    def _knowledge_dir(self, kp: KnowledgePoint) -> Path:
        # 从 source 推断科目目录
        source_file = ""
        if kp.source and kp.source.source_file:
            source_file = kp.source.source_file

        if "Analysis" in source_file or "分析" in source_file:
            subdir = "analysis"
        elif "Algebra" in source_file or "代数" in source_file:
            subdir = "linear_algebra"
        elif "群" in source_file or "group" in source_file.lower():
            subdir = "group_theory"
        else:
            subdir = "other"

        return self.base_path / "knowledge" / subdir

    # ── 批量加载 ─────────────────────────────────────

    def load_questions_by_ids(self, ids: list[str]) -> list[Question]:
        """批量加载题目，跳过不存在的"""
        results = []
        for qid in ids:
            try:
                results.append(self.load_question(qid))
            except (FileNotFoundError, Exception) as e:
                logger.warning("load_question_skip", id=qid, error=str(e))
        return results

    def load_knowledge_by_ids(self, ids: list[str]) -> list[KnowledgePoint]:
        """批量加载知识点，跳过不存在的"""
        results = []
        for kp_id in ids:
            try:
                results.append(self.load_knowledge_point(kp_id))
            except (FileNotFoundError, Exception) as e:
                logger.warning("load_kp_skip", id=kp_id, error=str(e))
        return results

    # ── Solution ─────────────────────────────────────

    def load_solution(self, question_id: str) -> Solution | None:
        """从 question JSON 中提取内嵌的 solution"""
        entry = self._question_index.get(question_id)
        if not entry:
            return None
        json_path = self.base_path / entry["path"]
        try:
            data = json.loads(json_path.read_text(encoding="utf-8"))
            sol_data = data.get("solution")
            if sol_data:
                return Solution(**sol_data)
        except Exception as e:
            logger.warning("load_solution_fail", id=question_id, error=str(e))
        return None

    def save_solution(self, sol: Solution) -> str:
        """将 solution 合并回 question JSON"""
        entry = self._question_index.get(sol.question_id)
        if not entry:
            raise FileNotFoundError(f"Question {sol.question_id} not found")
        json_path = self.base_path / entry["path"]
        data = json.loads(json_path.read_text(encoding="utf-8"))
        data["solution"] = json.loads(sol.model_dump_json())
        json_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return sol.id
