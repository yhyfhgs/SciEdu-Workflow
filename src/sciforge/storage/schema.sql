-- SciForge SQLite Schema v7
-- v7 变更：恢复 source_file 字段用于追溯来源
--   knowledge_points / questions 新增 source_file 列（原 logical_document 的文件路径）
--   idx_kp_order / idx_q_order 改回 (source_file, order_index)，便于按文件查询
-- v6 变更（历史）：删除 source_file/source_chapter/exam_type/exam_year
-- v5 变更（历史）：激进精简 quality_grade/created_at/page_* 等
-- v4 变更：新增 discipline 列
-- v3 变更：proof/solution 嵌入主表、统一 tag 格式
-- 本版本不提供自动迁移：检测到 user_version < 7 → 启动时报错，要求用户 --restart 清库重跑

PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;
PRAGMA user_version = 7;

-- ── 知识点 ──

CREATE TABLE IF NOT EXISTS knowledge_points (
    id TEXT PRIMARY KEY,
    discipline TEXT NOT NULL DEFAULT 'math',
    kp_type TEXT NOT NULL DEFAULT 'theorem',
    number TEXT DEFAULT '',
    content TEXT NOT NULL DEFAULT '',
    proof_content TEXT,
    difficulty INTEGER NOT NULL DEFAULT 5 CHECK(difficulty BETWEEN 1 AND 10),
    subject TEXT NOT NULL DEFAULT '',
    taxonomy_path TEXT DEFAULT '',
    tags TEXT DEFAULT '[]',
    source_file TEXT NOT NULL DEFAULT '',
    start_line INTEGER NOT NULL DEFAULT 0,
    end_line INTEGER NOT NULL DEFAULT 0,
    order_index INTEGER NOT NULL DEFAULT 0,
    content_hash TEXT DEFAULT ''
);

-- ── 题目（solution 直接嵌入）──

CREATE TABLE IF NOT EXISTS questions (
    id TEXT PRIMARY KEY,
    discipline TEXT NOT NULL DEFAULT 'math',
    question_type TEXT NOT NULL DEFAULT 'proof',
    number TEXT DEFAULT '',
    content TEXT NOT NULL DEFAULT '',
    solution_content TEXT,
    difficulty INTEGER NOT NULL DEFAULT 5 CHECK(difficulty BETWEEN 1 AND 10),
    subject TEXT NOT NULL DEFAULT '',
    taxonomy_path TEXT DEFAULT '',
    tags TEXT DEFAULT '[]',
    source_file TEXT NOT NULL DEFAULT '',
    start_line INTEGER NOT NULL DEFAULT 0,
    end_line INTEGER NOT NULL DEFAULT 0,
    order_index INTEGER NOT NULL DEFAULT 0,
    content_hash TEXT DEFAULT ''
);

-- ── 学科全局 Tag Tree ──

CREATE TABLE IF NOT EXISTS discipline_tag_trees (
    discipline TEXT PRIMARY KEY,
    tree_json  TEXT NOT NULL DEFAULT '{}',
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

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
CREATE INDEX IF NOT EXISTS idx_kp_hash ON knowledge_points(content_hash);
CREATE INDEX IF NOT EXISTS idx_kp_source ON knowledge_points(source_file);
CREATE INDEX IF NOT EXISTS idx_kp_order ON knowledge_points(source_file, order_index);

CREATE INDEX IF NOT EXISTS idx_q_discipline ON questions(discipline);
CREATE INDEX IF NOT EXISTS idx_q_type ON questions(question_type);
CREATE INDEX IF NOT EXISTS idx_q_subject ON questions(subject);
CREATE INDEX IF NOT EXISTS idx_q_taxonomy ON questions(taxonomy_path);
CREATE INDEX IF NOT EXISTS idx_q_hash ON questions(content_hash);
CREATE INDEX IF NOT EXISTS idx_q_source ON questions(source_file);
CREATE INDEX IF NOT EXISTS idx_q_order ON questions(source_file, order_index);

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
