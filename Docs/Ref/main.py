#!/usr/bin/env python3
"""
MathForge CLI — 核心功能命令行入口

用法:
    python -m cli.main generate --subject 数学分析 --topic 数列极限 --difficulty 5
    python -m cli.main variant --source q-a03 --strategy A4_escalate --difficulty 7
    python -m cli.main solve --question q-a06
    python -m cli.main list-questions --subject 数学分析
    python -m cli.main list-kps --subject 群论
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import os

# 添加项目根目录到路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.models import (
    GenerationRequest, QuestionType, Subject, VariantStrategy,
)
from core.seed_data import ALL_KNOWLEDGE_POINTS, ALL_QUESTIONS
from agents.generation_engine import (
    ContextRetriever, GenerationEngine,
    QuestionGenerator, SolveEngine, VerificationPipeline, DifficultyAnnotator,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(message)s",
)
logger = logging.getLogger(__name__)


# ── 科目映射 ──

SUBJECT_MAP = {
    "数学分析": Subject.ANALYSIS,
    "analysis": Subject.ANALYSIS,
    "高等代数": Subject.LINEAR_ALGEBRA,
    "linear_algebra": Subject.LINEAR_ALGEBRA,
    "群论": Subject.GROUP_THEORY,
    "group_theory": Subject.GROUP_THEORY,
}

TYPE_MAP = {
    "proof": QuestionType.PROOF,
    "calculation": QuestionType.CALCULATION,
    "construction": QuestionType.CONSTRUCTION,
    "true_false": QuestionType.TRUE_FALSE,
    "choice": QuestionType.CHOICE,
    "证明": QuestionType.PROOF,
    "计算": QuestionType.CALCULATION,
    "构造": QuestionType.CONSTRUCTION,
}


# ── 命令实现 ──

def cmd_generate(args):
    """自由模式生成新题"""
    subject = SUBJECT_MAP.get(args.subject)
    if not subject:
        print(f"未知科目: {args.subject}. 可选: {list(SUBJECT_MAP.keys())}")
        return

    topics = [t.strip() for t in args.topic.split(",")]
    qtype = TYPE_MAP.get(args.type, QuestionType.PROOF)
    strategy = VariantStrategy.TOPIC_GENERATION
    if len(topics) > 1:
        strategy = VariantStrategy.CROSS_TOPIC

    request = GenerationRequest(
        subject=subject,
        topics=topics,
        difficulty_target=args.difficulty,
        question_type=qtype,
        strategy=strategy,
    )

    retriever = ContextRetriever(ALL_KNOWLEDGE_POINTS, ALL_QUESTIONS)
    engine = GenerationEngine(retriever=retriever)

    result = engine.generate(request)

    if result:
        _print_result(result)
    else:
        print("\n❌ 未能生成合格题目。请尝试调整参数。")


def cmd_variant(args):
    """变式模式: 基于已有题目生成变式"""
    # 查找源题
    source = None
    for q in ALL_QUESTIONS:
        if q.id == args.source:
            source = q
            break

    if not source:
        print(f"未找到源题 ID: {args.source}")
        print(f"可用 ID: {[q.id for q in ALL_QUESTIONS]}")
        return

    strategy = VariantStrategy(args.strategy) if args.strategy else VariantStrategy.PARAMETER_SHIFT
    difficulty = args.difficulty or source.difficulty

    request = GenerationRequest(
        subject=source.subject,
        topics=source.tags[:3],  # 取前 3 个标签作为 topics
        difficulty_target=difficulty,
        question_type=source.question_type,
        strategy=strategy,
        source_question=source,
    )

    retriever = ContextRetriever(ALL_KNOWLEDGE_POINTS, ALL_QUESTIONS)
    engine = GenerationEngine(retriever=retriever)

    result = engine.generate(request)

    if result:
        print(f"\n{'='*60}")
        print(f"📌 源题 ({source.id}):")
        print(f"{source.content_latex.strip()}")
        print(f"{'='*60}")
        _print_result(result)
    else:
        print("\n❌ 未能生成合格变式。")


def cmd_solve(args):
    """对指定题目生成详细解答"""
    target = None
    for q in ALL_QUESTIONS:
        if q.id == args.question:
            target = q
            break

    if not target:
        print(f"未找到题目 ID: {args.question}")
        return

    print(f"\n📝 题目 ({target.id}, 难度 {target.difficulty}):")
    print(target.content_latex.strip())
    print(f"\n{'─'*50}")
    print("🔍 求解中...")

    retriever = ContextRetriever(ALL_KNOWLEDGE_POINTS, ALL_QUESTIONS)
    context = retriever.build_context(GenerationRequest(
        subject=target.subject,
        topics=target.tags[:3],
        difficulty_target=target.difficulty,
        question_type=target.question_type,
        strategy=VariantStrategy.TOPIC_GENERATION,
    ))

    solver = SolveEngine()
    solution = solver.solve(target, context)

    if solution:
        print(f"\n✅ 解答 (置信度: {solution.confidence}):")
        print(f"{'─'*50}")
        print(solution.solution_latex)
        print(f"\n方法: {', '.join(solution.methods_used)}")
        print(f"步数: {solution.step_count}")
    else:
        print("\n❌ 求解失败。")


def cmd_list_questions(args):
    """列出题库中的题目"""
    questions = ALL_QUESTIONS
    if args.subject:
        subject = SUBJECT_MAP.get(args.subject)
        if subject:
            questions = [q for q in questions if q.subject == subject]

    print(f"\n{'ID':<10} {'难度':<6} {'类型':<14} {'标签':<30} {'预览'}")
    print(f"{'─'*90}")
    for q in questions:
        preview = q.content_latex.strip().replace('\n', ' ')[:50]
        tags = ", ".join(q.tags[:3])
        print(f"{q.id:<10} {q.difficulty:<6} {q.question_type.value:<14} {tags:<30} {preview}...")


def cmd_list_kps(args):
    """列出知识库中的知识点"""
    kps = ALL_KNOWLEDGE_POINTS
    if args.subject:
        kps = [kp for kp in kps if args.subject in str(kp.tags)]

    print(f"\n{'ID':<10} {'类型':<16} {'难度':<6} {'标题'}")
    print(f"{'─'*70}")
    for kp in kps:
        print(f"{kp.id:<10} {kp.kp_type.value:<16} {kp.difficulty:<6} {kp.title}")


def cmd_batch(args):
    """批量生成"""
    subject = SUBJECT_MAP.get(args.subject)
    if not subject:
        print(f"未知科目: {args.subject}")
        return

    topics = [t.strip() for t in args.topic.split(",")]
    qtype = TYPE_MAP.get(args.type, QuestionType.PROOF)

    request = GenerationRequest(
        subject=subject,
        topics=topics,
        difficulty_target=args.difficulty,
        question_type=qtype,
        strategy=VariantStrategy.TOPIC_GENERATION,
    )

    retriever = ContextRetriever(ALL_KNOWLEDGE_POINTS, ALL_QUESTIONS)
    engine = GenerationEngine(retriever=retriever)

    results = engine.generate_batch(request, count=args.count)

    print(f"\n{'='*60}")
    print(f"批量生成结果: {len(results)}/{args.count} 成功")
    print(f"{'='*60}")
    for i, r in enumerate(results, 1):
        print(f"\n--- 题目 {i} (难度 {r.difficulty_score.overall}) ---")
        print(r.question.content_latex.strip())


# ── 辅助 ──

def _print_result(result):
    """美化输出生成结果"""
    q = result.question
    s = result.solution
    d = result.difficulty_score

    print(f"\n{'='*60}")
    print(f"✅ 生成成功!")
    print(f"{'='*60}")
    print(f"\n📝 题目:")
    print(q.content_latex.strip())
    print(f"\n📊 难度评分: {d.overall}/10")
    print(f"   理由: {d.reasoning}")
    print(f"   维度明细:")
    for dim in ['concept_count', 'step_count', 'abstraction_level',
                'technique_novelty', 'proof_structure', 'prerequisite_depth']:
        val = getattr(d, dim, '?')
        print(f"     {dim}: {val}/10")
    print(f"\n📋 标签: {', '.join(q.tags)}")
    print(f"   策略: {q.generation_meta.get('strategy', '?')}")
    print(f"   设计理由: {q.generation_meta.get('design_rationale', '?')}")
    print(f"\n{'─'*60}")
    print(f"📖 解答 (置信度: {s.confidence}):")
    print(s.solution_latex[:500])
    if len(s.solution_latex) > 500:
        print("  ... (省略)")
    print(f"\n   方法: {', '.join(s.methods_used)}")
    print(f"   步数: {s.step_count}")
    print(f"\n{'─'*60}")
    print(f"✓ 验证: {result.verification.checks}")
    print(f"  轮次: {result.generation_meta.get('round', '?')}")


# ── 主入口 ──

def main():
    parser = argparse.ArgumentParser(
        prog="mathforge",
        description="MathForge — 大学数学题目生成引擎",
    )
    subparsers = parser.add_subparsers(dest="command", help="可用命令")

    # generate
    p_gen = subparsers.add_parser("generate", help="自由模式生成新题")
    p_gen.add_argument("--subject", "-s", required=True, help="科目 (数学分析/高等代数/群论)")
    p_gen.add_argument("--topic", "-t", required=True, help="专题 (逗号分隔)")
    p_gen.add_argument("--difficulty", "-d", type=int, default=5, help="目标难度 1-10")
    p_gen.add_argument("--type", default="proof", help="题型 (proof/calculation/construction)")

    # variant
    p_var = subparsers.add_parser("variant", help="变式模式: 基于已有题生成变式")
    p_var.add_argument("--source", required=True, help="源题 ID")
    p_var.add_argument("--strategy", help="变式策略 (A1-A6, B1-B2)")
    p_var.add_argument("--difficulty", "-d", type=int, help="目标难度 (默认同源题)")

    # solve
    p_solve = subparsers.add_parser("solve", help="对指定题目生成解答")
    p_solve.add_argument("--question", "-q", required=True, help="题目 ID")

    # batch
    p_batch = subparsers.add_parser("batch", help="批量生成")
    p_batch.add_argument("--subject", "-s", required=True)
    p_batch.add_argument("--topic", "-t", required=True)
    p_batch.add_argument("--difficulty", "-d", type=int, default=5)
    p_batch.add_argument("--type", default="proof")
    p_batch.add_argument("--count", "-n", type=int, default=3)

    # list
    p_lq = subparsers.add_parser("list-questions", help="列出题库")
    p_lq.add_argument("--subject", "-s", help="按科目过滤")

    p_lk = subparsers.add_parser("list-kps", help="列出知识点")
    p_lk.add_argument("--subject", "-s", help="按科目过滤")

    args = parser.parse_args()

    if args.command == "generate":
        cmd_generate(args)
    elif args.command == "variant":
        cmd_variant(args)
    elif args.command == "solve":
        cmd_solve(args)
    elif args.command == "batch":
        cmd_batch(args)
    elif args.command == "list-questions":
        cmd_list_questions(args)
    elif args.command == "list-kps":
        cmd_list_kps(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
