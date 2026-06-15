"""个性化推荐（P4）—— 基于用户练习行为的难度/标签推荐引擎。

`RecommendEngine` 是**纯算法**（无 LLM、无外部依赖）：
- `build_profile(behaviors)` 把行为流水聚合成 `UserProfile`（掌握难度 + 薄弱标签）
- `recommend(profile, candidates)` 按"最近发展区"（略高于掌握难度）+ 薄弱标签命中排序

行为数据由 `storage.saas.SaasStore`（user_behavior 表）提供，候选题由题库
`StorageBackend` 提供 —— 引擎只做排序，两端均可替换。
"""

from __future__ import annotations

from sciforge.agents.recommend.engine import RecommendEngine

__all__ = ["RecommendEngine"]
