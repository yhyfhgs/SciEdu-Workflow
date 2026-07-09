-- SciForge SQLite Schema v13
-- v13 变更（Knowledge Evaluation / M6）：
--   * item_evaluations：版本化 10 维难度 + 质量/教学价值量规
--   * review_queue：低质量、答案不完整、pending concept 的人工治理队列
-- v12 变更（Concept Graph / M5）：
--   * concepts：受控 taxonomy 驱动的稳定概念身份
--   * concept_aliases：分支作用域别名持久化，避免跨分支同名误归并
--   * item_concepts：条目到概念的多对多角色关联
--   * concept_relations：跨书聚合后的概念粒度关系图
-- v10 变更（本版本）：
--   * 结构化题型（REC-19）：questions 新增 options(JSON) / correct_option / blanks
--   * 检索优化（REC-16）：knowledge_points / questions 新增 order_index 单列索引
--     （max_order_index 不再全表扫描；非 source 过滤检索排序提速）
--   迁移：提供 v9→v10 增量 ALTER + CREATE INDEX（见 sqlite_backend._MIGRATIONS）
-- v9 变更：questions 新增生成元数据列
--   is_generated / generator_model / generated_at / generation_session_id / tenant_id
--   （区分生成题 vs 手动题，支撑版权归属、计费、统计；tenant_id 支撑多租户隔离）
--   迁移：提供 v8→v9 增量 ALTER（见 sqlite_backend._MIGRATIONS），不再要求删库
-- v8 变更：
--   * 字段规范化（#4）：knowledge_points / questions 新增
--       number_canonical（规范化题号，如 "3.2"）+ seq_chapter/seq_section/seq_index（结构化序号，编号连续性 audit 用）
--   * 题目类型双维度（#5）：questions 新增 question_form（题目形式：choice/fill_blank/...，与 question_type 认知类别正交）
--   * 写入去重（#8）：content_hash 升级为 UNIQUE 部分索引（非空时唯一），save_batch 用 INSERT OR IGNORE
--   * 约束强化（#4）：discipline 加 CHECK；difficulty 保留 CHECK
--   * 知识图谱（#6/#15）：新增 item_relations 边表
-- v7 变更（历史）：恢复 source_file 字段用于追溯来源
-- 本版本不提供自动迁移：检测到 user_version < 8 → 启动时报错，要求用户 --restart 清库重跑

PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;
PRAGMA user_version = 13;

-- ── 知识点 ──

CREATE TABLE IF NOT EXISTS knowledge_points (
    id TEXT PRIMARY KEY,
    discipline TEXT NOT NULL DEFAULT 'math'
        CHECK(discipline IN ('math','physics','chemistry','biology','other')),
    kp_type TEXT NOT NULL DEFAULT 'theorem',
    number TEXT DEFAULT '',
    number_canonical TEXT DEFAULT '',
    seq_chapter INTEGER,
    seq_section INTEGER,
    seq_index INTEGER,
    content TEXT NOT NULL DEFAULT '',
    proof_content TEXT,
    difficulty INTEGER NOT NULL DEFAULT 5 CHECK(difficulty BETWEEN 1 AND 10),
    subject TEXT NOT NULL DEFAULT '',
    taxonomy_path TEXT DEFAULT '',
    tags TEXT DEFAULT '[]',
    source_file TEXT NOT NULL DEFAULT '',
    start_line INTEGER NOT NULL DEFAULT 0,
    end_line INTEGER NOT NULL DEFAULT 0,
    -- 字符级溯源（v11 §F M1）：行内偏移，0 = 行级；page 为源 PDF 页码
    start_char INTEGER NOT NULL DEFAULT 0,
    end_char INTEGER NOT NULL DEFAULT 0,
    page INTEGER,
    order_index INTEGER NOT NULL DEFAULT 0,
    content_hash TEXT DEFAULT ''
);

-- ── 题目（solution 直接嵌入）──

CREATE TABLE IF NOT EXISTS questions (
    id TEXT PRIMARY KEY,
    discipline TEXT NOT NULL DEFAULT 'math'
        CHECK(discipline IN ('math','physics','chemistry','biology','other')),
    question_type TEXT NOT NULL DEFAULT 'proof',
    question_form TEXT DEFAULT '',
    number TEXT DEFAULT '',
    number_canonical TEXT DEFAULT '',
    seq_chapter INTEGER,
    seq_section INTEGER,
    seq_index INTEGER,
    content TEXT NOT NULL DEFAULT '',
    solution_content TEXT,
    -- 结构化题型（v10 / REC-19）
    options TEXT DEFAULT '[]',
    correct_option TEXT,
    blanks INTEGER,
    difficulty INTEGER NOT NULL DEFAULT 5 CHECK(difficulty BETWEEN 1 AND 10),
    subject TEXT NOT NULL DEFAULT '',
    taxonomy_path TEXT DEFAULT '',
    tags TEXT DEFAULT '[]',
    source_file TEXT NOT NULL DEFAULT '',
    start_line INTEGER NOT NULL DEFAULT 0,
    end_line INTEGER NOT NULL DEFAULT 0,
    -- 字符级溯源（v11 §F M1）
    start_char INTEGER NOT NULL DEFAULT 0,
    end_char INTEGER NOT NULL DEFAULT 0,
    page INTEGER,
    order_index INTEGER NOT NULL DEFAULT 0,
    content_hash TEXT DEFAULT '',
    -- 生成元数据（v9）
    is_generated INTEGER NOT NULL DEFAULT 0,
    generator_model TEXT NOT NULL DEFAULT '',
    generated_at TEXT,
    generation_session_id TEXT NOT NULL DEFAULT '',
    tenant_id TEXT NOT NULL DEFAULT ''
);

-- ── 学科全局 Tag Tree ──

CREATE TABLE IF NOT EXISTS discipline_tag_trees (
    discipline TEXT PRIMARY KEY,
    tree_json  TEXT NOT NULL DEFAULT '{}',
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ── 条目间关系边（知识图谱）──
-- src_id/dst_id 引用 knowledge_points.id 或 questions.id（跨表，不设 FK）
-- relation_type 见 core.models.RelationType；source ∈ {llm, structural, manual}

CREATE TABLE IF NOT EXISTS item_relations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    src_id TEXT NOT NULL,
    dst_id TEXT NOT NULL,
    relation_type TEXT NOT NULL DEFAULT 'related',
    confidence REAL NOT NULL DEFAULT 1.0,
    source TEXT NOT NULL DEFAULT 'structural',
    discipline TEXT NOT NULL DEFAULT 'math',
    note TEXT DEFAULT '',
    UNIQUE(src_id, dst_id, relation_type)
);

-- ── 图表（v10 / REC-18）──
-- 理工科带图条目的图片元数据。item_id 引用 knowledge_points.id 或 questions.id（跨表，
-- 不设 FK）；image_path 指向裁剪落盘的图片。content 里仍保留 ![](path) 链接，本表供结构化检索。

CREATE TABLE IF NOT EXISTS figures (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id TEXT NOT NULL,
    item_kind TEXT NOT NULL DEFAULT 'question',
    source_file TEXT NOT NULL DEFAULT '',
    image_path TEXT NOT NULL DEFAULT '',
    caption TEXT DEFAULT '',
    page INTEGER,
    UNIQUE(item_id, image_path)
);

CREATE INDEX IF NOT EXISTS idx_fig_item ON figures(item_id);

-- ── Concept Graph（v12 / M5）──
-- taxonomy_path 是权威概念身份来源；tags 仅作为别名与展示路径。
-- 未命中受控 taxonomy 的 tag 仍可入 concepts，但 status=pending，默认不进入生成池。

CREATE TABLE IF NOT EXISTS concepts (
    id TEXT PRIMARY KEY,
    discipline TEXT NOT NULL
        CHECK(discipline IN ('math','physics','chemistry','biology','other')),
    branch TEXT NOT NULL,
    canonical_en TEXT NOT NULL,
    canonical_zh TEXT NOT NULL DEFAULT '',
    tag_path TEXT NOT NULL DEFAULT '',
    kind TEXT NOT NULL DEFAULT 'concept',
    status TEXT NOT NULL DEFAULT 'active'
        CHECK(status IN ('active','pending','deprecated')),
    depth INTEGER NOT NULL DEFAULT 1 CHECK(depth >= 1),
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- alias 必须在 discipline+branch 内唯一；例如不同分支的 "definition" 不会误合并。
CREATE TABLE IF NOT EXISTS concept_aliases (
    discipline TEXT NOT NULL,
    branch TEXT NOT NULL,
    alias TEXT NOT NULL,
    concept_id TEXT NOT NULL REFERENCES concepts(id) ON DELETE CASCADE,
    locale TEXT NOT NULL DEFAULT 'mixed'
        CHECK(locale IN ('en','zh','mixed')),
    source TEXT NOT NULL DEFAULT 'pipeline'
        CHECK(source IN ('taxonomy','pipeline','spec','manual')),
    confidence REAL NOT NULL DEFAULT 1.0 CHECK(confidence BETWEEN 0.0 AND 1.0),
    PRIMARY KEY(discipline, branch, alias)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS item_concepts (
    item_id TEXT NOT NULL,
    item_kind TEXT NOT NULL CHECK(item_kind IN ('knowledge','question')),
    concept_id TEXT NOT NULL REFERENCES concepts(id) ON DELETE CASCADE,
    role TEXT NOT NULL DEFAULT 'primary'
        CHECK(role IN ('primary','defines','uses','prerequisite','tests')),
    weight REAL NOT NULL DEFAULT 1.0 CHECK(weight BETWEEN 0.0 AND 1.0),
    PRIMARY KEY(item_id, concept_id, role)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS concept_relations (
    src_concept TEXT NOT NULL REFERENCES concepts(id) ON DELETE CASCADE,
    dst_concept TEXT NOT NULL REFERENCES concepts(id) ON DELETE CASCADE,
    relation_type TEXT NOT NULL,
    confidence REAL NOT NULL DEFAULT 1.0 CHECK(confidence BETWEEN 0.0 AND 1.0),
    source TEXT NOT NULL DEFAULT 'structural'
        CHECK(source IN ('structural','llm','manual')),
    evidence_count INTEGER NOT NULL DEFAULT 1 CHECK(evidence_count >= 1),
    note TEXT NOT NULL DEFAULT '',
    PRIMARY KEY(src_concept, dst_concept, relation_type)
) WITHOUT ROWID;

CREATE INDEX IF NOT EXISTS idx_concept_discipline_branch
    ON concepts(discipline, branch, status);
CREATE INDEX IF NOT EXISTS idx_concept_canonical
    ON concepts(discipline, branch, canonical_en);
CREATE INDEX IF NOT EXISTS idx_concept_alias_target ON concept_aliases(concept_id);
CREATE INDEX IF NOT EXISTS idx_item_concept_concept ON item_concepts(concept_id, role);
CREATE INDEX IF NOT EXISTS idx_concept_rel_dst ON concept_relations(dst_concept, relation_type);

-- ── Knowledge Evaluation（v13 / M6）──

CREATE TABLE IF NOT EXISTS item_evaluations (
    item_id TEXT NOT NULL,
    item_kind TEXT NOT NULL CHECK(item_kind IN ('knowledge','question')),
    rubric_version TEXT NOT NULL,
    extraction_quality REAL NOT NULL CHECK(extraction_quality BETWEEN 0.0 AND 1.0),
    solution_completeness REAL NOT NULL CHECK(solution_completeness BETWEEN 0.0 AND 1.0),
    difficulty_overall REAL NOT NULL CHECK(difficulty_overall BETWEEN 1.0 AND 10.0),
    difficulty_dims TEXT NOT NULL DEFAULT '{}',
    cognitive_level TEXT NOT NULL
        CHECK(cognitive_level IN ('remember','understand','apply','analyze','evaluate','create')),
    prerequisite_depth INTEGER NOT NULL DEFAULT 0 CHECK(prerequisite_depth >= 0),
    proof_complexity REAL NOT NULL DEFAULT 0.0 CHECK(proof_complexity BETWEEN 0.0 AND 1.0),
    lecture_suitability REAL NOT NULL DEFAULT 0.0
        CHECK(lecture_suitability BETWEEN 0.0 AND 1.0),
    cross_book_generality REAL NOT NULL DEFAULT 0.0
        CHECK(cross_book_generality BETWEEN 0.0 AND 1.0),
    source TEXT NOT NULL
        CHECK(source IN ('pipeline_auto','llm_judge','human')),
    confidence REAL NOT NULL DEFAULT 0.65 CHECK(confidence BETWEEN 0.0 AND 1.0),
    evaluated_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY(item_id, rubric_version, source)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS review_queue (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id TEXT NOT NULL,
    item_kind TEXT NOT NULL CHECK(item_kind IN ('knowledge','question')),
    tenant_id TEXT NOT NULL DEFAULT '',
    rubric_version TEXT NOT NULL,
    reason TEXT NOT NULL,
    priority INTEGER NOT NULL DEFAULT 3 CHECK(priority BETWEEN 1 AND 5),
    status TEXT NOT NULL DEFAULT 'open'
        CHECK(status IN ('open','in_progress','resolved','dismissed')),
    details TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(item_id, reason, rubric_version)
);

CREATE INDEX IF NOT EXISTS idx_eval_item ON item_evaluations(item_id);
CREATE INDEX IF NOT EXISTS idx_eval_quality_gate
    ON item_evaluations(rubric_version, source, extraction_quality,
                        solution_completeness, difficulty_overall);
CREATE INDEX IF NOT EXISTS idx_review_queue_work
    ON review_queue(tenant_id, status, priority, created_at);

-- ── FTS5 全文检索 ──

CREATE VIRTUAL TABLE IF NOT EXISTS kp_fts USING fts5(
    content, taxonomy_path,
    content='knowledge_points',
    content_rowid='rowid'
);

CREATE VIRTUAL TABLE IF NOT EXISTS question_fts USING fts5(
    content, taxonomy_path,
    content='questions',
    content_rowid='rowid'
);

-- ── 索引 ──

CREATE INDEX IF NOT EXISTS idx_kp_discipline ON knowledge_points(discipline);
CREATE INDEX IF NOT EXISTS idx_kp_type ON knowledge_points(kp_type);
CREATE INDEX IF NOT EXISTS idx_kp_subject ON knowledge_points(subject);
CREATE INDEX IF NOT EXISTS idx_kp_taxonomy ON knowledge_points(taxonomy_path);
CREATE INDEX IF NOT EXISTS idx_kp_source ON knowledge_points(source_file);
CREATE INDEX IF NOT EXISTS idx_kp_order ON knowledge_points(source_file, order_index);
-- REC-16：order_index 单列索引（max_order_index / 非 source 过滤检索排序，避免全表扫描）
CREATE INDEX IF NOT EXISTS idx_kp_order_only ON knowledge_points(order_index);
-- content_hash 非空时唯一 → 写入去重（#8）
CREATE UNIQUE INDEX IF NOT EXISTS idx_kp_hash_uniq ON knowledge_points(content_hash)
    WHERE content_hash != '';

CREATE INDEX IF NOT EXISTS idx_q_discipline ON questions(discipline);
CREATE INDEX IF NOT EXISTS idx_q_type ON questions(question_type);
CREATE INDEX IF NOT EXISTS idx_q_form ON questions(question_form);
CREATE INDEX IF NOT EXISTS idx_q_subject ON questions(subject);
CREATE INDEX IF NOT EXISTS idx_q_taxonomy ON questions(taxonomy_path);
CREATE INDEX IF NOT EXISTS idx_q_source ON questions(source_file);
CREATE INDEX IF NOT EXISTS idx_q_order ON questions(source_file, order_index);
-- REC-16：order_index 单列索引
CREATE INDEX IF NOT EXISTS idx_q_order_only ON questions(order_index);
CREATE UNIQUE INDEX IF NOT EXISTS idx_q_hash_uniq ON questions(content_hash)
    WHERE content_hash != '';
-- 生成题 / 租户筛选（v9）
CREATE INDEX IF NOT EXISTS idx_q_tenant ON questions(tenant_id);
CREATE INDEX IF NOT EXISTS idx_q_generated ON questions(is_generated);

CREATE INDEX IF NOT EXISTS idx_rel_src ON item_relations(src_id);
CREATE INDEX IF NOT EXISTS idx_rel_dst ON item_relations(dst_id);
CREATE INDEX IF NOT EXISTS idx_rel_type ON item_relations(relation_type);

-- ── FTS 同步触发器 ──

CREATE TRIGGER IF NOT EXISTS kp_fts_insert AFTER INSERT ON knowledge_points BEGIN
    INSERT INTO kp_fts(rowid, content, taxonomy_path)
    VALUES (NEW.rowid, NEW.content, NEW.taxonomy_path);
END;

CREATE TRIGGER IF NOT EXISTS kp_fts_delete AFTER DELETE ON knowledge_points BEGIN
    INSERT INTO kp_fts(kp_fts, rowid, content, taxonomy_path)
    VALUES ('delete', OLD.rowid, OLD.content, OLD.taxonomy_path);
END;

CREATE TRIGGER IF NOT EXISTS q_fts_insert AFTER INSERT ON questions BEGIN
    INSERT INTO question_fts(rowid, content, taxonomy_path)
    VALUES (NEW.rowid, NEW.content, NEW.taxonomy_path);
END;

CREATE TRIGGER IF NOT EXISTS q_fts_delete AFTER DELETE ON questions BEGIN
    INSERT INTO question_fts(question_fts, rowid, content, taxonomy_path)
    VALUES ('delete', OLD.rowid, OLD.content, OLD.taxonomy_path);
END;
