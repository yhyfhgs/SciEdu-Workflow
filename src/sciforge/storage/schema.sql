-- SciForge SQLite Schema v10
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
PRAGMA user_version = 11;

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
