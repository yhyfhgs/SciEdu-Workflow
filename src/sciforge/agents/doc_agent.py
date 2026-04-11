"""DocAssemblyAgent (lite) — 讲义组装 Agent (Agent 5)

MVP 版：
- 按 topic_tags 从 DB 检索知识点和例题
- 组合成 Lecture 结构（分节：引入 + 知识点 + 例题 + 练习）
- 输出 markdown 文本（不做 LaTeX 编译）

后续扩展: LaTeX 编译、模板体系（见 Docs/10_文档生成系统.md）
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, Field

from sciforge.agents.base import BaseAgent
from sciforge.core.models import (
    Discipline,
    KnowledgePoint,
    Lecture,
    LectureSection,
    Question,
)

if TYPE_CHECKING:
    from sciforge.llm.client import LLMClient
    from sciforge.storage.sqlite_backend import SQLiteStorageBackend


class DocInput(BaseModel):
    topic_tags: list[str]
    title: str = ""
    target_audience: str = "大学一年级"
    max_kps_per_section: int = 3
    max_examples_per_section: int = 2
    max_exercises_per_section: int = 3
    output_format: Literal["markdown", "latex"] = "markdown"


class DocOutput(BaseModel):
    lecture: Lecture
    markdown_text: str = ""


class DocAssemblyAgent(BaseAgent[DocInput, DocOutput]):
    """Lite 版讲义组装：按 tag 聚合条目并生成 markdown"""

    name = "doc_assembly"
    prompt_file = ""

    def __init__(
        self,
        llm: LLMClient,
        storage: SQLiteStorageBackend,
        discipline: Discipline = Discipline.MATH,
    ) -> None:
        super().__init__(llm=llm, discipline=discipline)
        self.storage = storage

    async def run(self, input: DocInput) -> DocOutput:
        self._log_start(tags=input.topic_tags)

        if input.output_format == "latex":
            # lite 版暂不做 LaTeX 编译，提醒后退到 markdown
            self._log_end(note="latex output not yet supported in lite version")

        # 从 DB 检索
        kps = self._retrieve_kps(input.topic_tags)
        qs = self._retrieve_questions(input.topic_tags)

        # 按首个子主题分组（tag[2] 作为节的划分依据）
        sections = self._group_by_topic(
            kps,
            qs,
            max_kps=input.max_kps_per_section,
            max_examples=input.max_examples_per_section,
            max_exercises=input.max_exercises_per_section,
        )

        lecture = Lecture(
            title=input.title or self._default_title(input.topic_tags),
            discipline=self.discipline,
            topic_tags=input.topic_tags,
            sections=sections,
            target_audience=input.target_audience,
            generation_meta={
                "kps_used": len(kps),
                "questions_used": len(qs),
            },
        )

        # 生成 markdown
        md_text = self._render_markdown(lecture, kps, qs)

        self._log_end(sections=len(sections), md_length=len(md_text))
        return DocOutput(lecture=lecture, markdown_text=md_text)

    # ── 检索 ─────────────────────────────────────────────

    def _retrieve_kps(self, topic_tags: list[str]) -> list[KnowledgePoint]:
        """按 discipline 粗筛，再按 tag 重叠度排序"""
        ids = self.storage.search_knowledge({"discipline": self.discipline.value})
        kps = self.storage.load_knowledge_by_ids(ids[:100])
        tag_set = set(topic_tags)
        return sorted(
            kps,
            key=lambda kp: len(set(kp.tags) & tag_set),
            reverse=True,
        )[:50]

    def _retrieve_questions(self, topic_tags: list[str]) -> list[Question]:
        ids = self.storage.search_questions({"discipline": self.discipline.value})
        qs = self.storage.load_questions_by_ids(ids[:100])
        tag_set = set(topic_tags)
        return sorted(
            qs,
            key=lambda q: len(set(q.tags) & tag_set),
            reverse=True,
        )[:50]

    # ── 分组 ─────────────────────────────────────────────

    def _group_by_topic(
        self,
        kps: list[KnowledgePoint],
        qs: list[Question],
        *,
        max_kps: int,
        max_examples: int,
        max_exercises: int,
    ) -> list[LectureSection]:
        """按 tag 的第 3 段（子主题）分组"""
        section_map: dict[str, LectureSection] = {}

        def topic_key(tags: list[str]) -> str:
            """取 tag 列表的第 3 段作为主题 key（顶层学科→大类→主题）"""
            if len(tags) >= 3:
                return tags[2]
            if len(tags) >= 2:
                return tags[1]
            return tags[0] if tags else "其他"

        for kp in kps:
            key = topic_key(kp.tags)
            if key not in section_map:
                section_map[key] = LectureSection(title=self._friendly_title(key))
            sec = section_map[key]
            if len(sec.knowledge_point_ids) < max_kps:
                sec.knowledge_point_ids.append(kp.id)

        for q in qs:
            key = topic_key(q.tags)
            if key not in section_map:
                section_map[key] = LectureSection(title=self._friendly_title(key))
            sec = section_map[key]
            # 前 max_examples 作例题，其余作练习
            if len(sec.worked_example_ids) < max_examples:
                sec.worked_example_ids.append(q.id)
            elif len(sec.exercise_ids) < max_exercises:
                sec.exercise_ids.append(q.id)

        # 删除空节
        return [s for s in section_map.values() if s.knowledge_point_ids or s.worked_example_ids]

    @staticmethod
    def _friendly_title(tag_segment: str) -> str:
        """'normal_subgroup:正规子群' → '正规子群'"""
        if ":" in tag_segment:
            return tag_segment.split(":", 1)[1]
        return tag_segment

    def _default_title(self, topic_tags: list[str]) -> str:
        parts = [self._friendly_title(t) for t in topic_tags]
        return " · ".join(parts) if parts else f"{self.discipline_spec.display_name}讲义"

    # ── markdown 渲染 ────────────────────────────────────

    def _render_markdown(
        self,
        lecture: Lecture,
        kps: list[KnowledgePoint],
        qs: list[Question],
    ) -> str:
        kps_by_id = {kp.id: kp for kp in kps}
        qs_by_id = {q.id: q for q in qs}

        lines: list[str] = [f"# {lecture.title}", ""]
        lines.append(f"- **学科**: {self.discipline_spec.display_name}")
        lines.append(f"- **对象**: {lecture.target_audience}")
        lines.append(f"- **预计时长**: {lecture.estimated_duration_min} 分钟")
        lines.append("")

        for i, sec in enumerate(lecture.sections, 1):
            lines.append(f"## {i}. {sec.title}")
            lines.append("")
            if sec.intro:
                lines.append(sec.intro)
                lines.append("")

            if sec.knowledge_point_ids:
                lines.append("### 核心知识点")
                lines.append("")
                for kp_id in sec.knowledge_point_ids:
                    kp = kps_by_id.get(kp_id)
                    if kp:
                        title = f"{kp.number} " if kp.number else ""
                        lines.append(f"**{title}({kp.kp_type})** {kp.content}")
                        if kp.proof_content:
                            lines.append("")
                            lines.append(f"*证明：* {kp.proof_content}")
                        lines.append("")

            if sec.worked_example_ids:
                lines.append("### 例题")
                lines.append("")
                for q_id in sec.worked_example_ids:
                    q = qs_by_id.get(q_id)
                    if q:
                        number = f"例 {q.number}" if q.number else "例题"
                        lines.append(f"**{number}.** {q.content}")
                        if q.solution_content:
                            lines.append("")
                            lines.append(f"*解答：* {q.solution_content}")
                        lines.append("")

            if sec.exercise_ids:
                lines.append("### 练习")
                lines.append("")
                for j, q_id in enumerate(sec.exercise_ids, 1):
                    q = qs_by_id.get(q_id)
                    if q:
                        lines.append(f"{j}. {q.content}")
                lines.append("")

        return "\n".join(lines)
