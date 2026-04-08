-- MathForge SQLite Schema
-- 核心表 + FTS5 全文检索 + 索引 + 触发器

PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

-- ── 知识点 ──

CREATE TABLE IF NOT EXISTS knowledge_points (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL DEFAULT '',
    content_latex TEXT NOT NULL DEFAULT '',
    content_plain TEXT NOT NULL DEFAULT '',
    kp_type TEXT NOT NULL DEFAULT 'theorem',
    difficulty INTEGER NOT NULL DEFAULT 5 CHECK(difficulty BETWEEN 1 AND 10),
    subject TEXT NOT NULL DEFAULT '数学分析',
    domain TEXT NOT NULL DEFAULT 'math',
    education_level TEXT NOT NULL DEFAULT 'undergraduate',
    -- 结构化内容
    proof_json TEXT,
    solution_json TEXT,
    -- 来源
    source_file TEXT DEFAULT '',
    source_chapter TEXT,
    source_section TEXT,
    source_line_start INTEGER,
    source_line_end INTEGER,
    extraction_strategy TEXT DEFAULT '',
    extraction_score REAL DEFAULT 0.0,
    -- 质量
    quality_grade TEXT DEFAULT 'B',
    -- 分类
    taxonomy_path TEXT DEFAULT '',
    tags_json TEXT DEFAULT '[]',
    prerequisites_json TEXT DEFAULT '[]',
    -- 去重
    content_hash TEXT DEFAULT '',
    -- 时间
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ── 题目 ──

CREATE TABLE IF NOT EXISTS questions (
    id TEXT PRIMARY KEY,
    content_latex TEXT NOT NULL DEFAULT '',
    content_plain TEXT NOT NULL DEFAULT '',
    question_type TEXT NOT NULL DEFAULT 'proof',
    difficulty INTEGER NOT NULL DEFAULT 5 CHECK(difficulty BETWEEN 1 AND 10),
    difficulty_score_json TEXT,
    subject TEXT NOT NULL DEFAULT '数学分析',
    domain TEXT NOT NULL DEFAULT 'math',
    education_level TEXT NOT NULL DEFAULT 'undergraduate',
    answer TEXT DEFAULT '',
    -- 来源
    source_file TEXT DEFAULT '',
    source_chapter TEXT,
    exam_type TEXT,
    exam_year INTEGER,
    book_title TEXT,
    is_generated INTEGER NOT NULL DEFAULT 0,
    parent_id TEXT,
    extraction_strategy TEXT DEFAULT '',
    extraction_score REAL DEFAULT 0.0,
    -- 质量
    quality_grade TEXT DEFAULT 'B',
    -- 分类
    taxonomy_path TEXT DEFAULT '',
    tags_json TEXT DEFAULT '[]',
    -- 去重
    content_hash TEXT DEFAULT '',
    -- 时间
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ── 解答 ──

CREATE TABLE IF NOT EXISTS solutions (
    id TEXT PRIMARY KEY,
    question_id TEXT NOT NULL REFERENCES questions(id) ON DELETE CASCADE,
    solution_latex TEXT NOT NULL DEFAULT '',
    solution_plain TEXT NOT NULL DEFAULT '',
    methods_used_json TEXT DEFAULT '[]',
    step_count INTEGER DEFAULT 0,
    key_steps_json TEXT DEFAULT '[]',
    confidence TEXT DEFAULT 'medium',
    is_verified INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ── 关系 ──

CREATE TABLE IF NOT EXISTS relations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id TEXT NOT NULL,
    source_type TEXT NOT NULL CHECK(source_type IN ('kp', 'question')),
    target_id TEXT NOT NULL,
    target_type TEXT NOT NULL CHECK(target_type IN ('kp', 'question')),
    relation_type TEXT NOT NULL,
    confidence REAL NOT NULL DEFAULT 0.7,
    source_method TEXT NOT NULL DEFAULT 'structural',
    note TEXT DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ── 标签 ──

CREATE TABLE IF NOT EXISTS tags (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id TEXT NOT NULL,
    item_type TEXT NOT NULL CHECK(item_type IN ('kp', 'question')),
    tag TEXT NOT NULL,
    tag_type TEXT NOT NULL DEFAULT 'custom'
);

-- ── FTS5 全文检索 ──

CREATE VIRTUAL TABLE IF NOT EXISTS kp_fts USING fts5(
    title, content_plain, taxonomy_path,
    content='knowledge_points',
    content_rowid='rowid'
);

CREATE VIRTUAL TABLE IF NOT EXISTS question_fts USING fts5(
    content_plain, taxonomy_path,
    content='questions',
    content_rowid='rowid'
);

-- ── 索引 ──

CREATE INDEX IF NOT EXISTS idx_kp_type ON knowledge_points(kp_type);
CREATE INDEX IF NOT EXISTS idx_kp_subject ON knowledge_points(subject);
CREATE INDEX IF NOT EXISTS idx_kp_difficulty ON knowledge_points(difficulty);
CREATE INDEX IF NOT EXISTS idx_kp_taxonomy ON knowledge_points(taxonomy_path);
CREATE INDEX IF NOT EXISTS idx_kp_hash ON knowledge_points(content_hash);

CREATE INDEX IF NOT EXISTS idx_q_type ON questions(question_type);
CREATE INDEX IF NOT EXISTS idx_q_subject ON questions(subject);
CREATE INDEX IF NOT EXISTS idx_q_difficulty ON questions(difficulty);
CREATE INDEX IF NOT EXISTS idx_q_taxonomy ON questions(taxonomy_path);
CREATE INDEX IF NOT EXISTS idx_q_exam ON questions(exam_type, exam_year);
CREATE INDEX IF NOT EXISTS idx_q_hash ON questions(content_hash);
CREATE INDEX IF NOT EXISTS idx_q_generated ON questions(is_generated);

CREATE INDEX IF NOT EXISTS idx_rel_source ON relations(source_id, source_type);
CREATE INDEX IF NOT EXISTS idx_rel_target ON relations(target_id, target_type);
CREATE INDEX IF NOT EXISTS idx_rel_type ON relations(relation_type);

CREATE INDEX IF NOT EXISTS idx_tags_item ON tags(item_id, item_type);
CREATE INDEX IF NOT EXISTS idx_tags_tag ON tags(tag);

CREATE INDEX IF NOT EXISTS idx_sol_question ON solutions(question_id);

-- ── FTS 同步触发器 ──

CREATE TRIGGER IF NOT EXISTS kp_fts_insert AFTER INSERT ON knowledge_points BEGIN
    INSERT INTO kp_fts(rowid, title, content_plain, taxonomy_path)
    VALUES (NEW.rowid, NEW.title, NEW.content_plain, NEW.taxonomy_path);
END;

CREATE TRIGGER IF NOT EXISTS kp_fts_delete AFTER DELETE ON knowledge_points BEGIN
    INSERT INTO kp_fts(kp_fts, rowid, title, content_plain, taxonomy_path)
    VALUES ('delete', OLD.rowid, OLD.title, OLD.content_plain, OLD.taxonomy_path);
END;

CREATE TRIGGER IF NOT EXISTS kp_fts_update AFTER UPDATE ON knowledge_points BEGIN
    INSERT INTO kp_fts(kp_fts, rowid, title, content_plain, taxonomy_path)
    VALUES ('delete', OLD.rowid, OLD.title, OLD.content_plain, OLD.taxonomy_path);
    INSERT INTO kp_fts(rowid, title, content_plain, taxonomy_path)
    VALUES (NEW.rowid, NEW.title, NEW.content_plain, NEW.taxonomy_path);
END;

CREATE TRIGGER IF NOT EXISTS q_fts_insert AFTER INSERT ON questions BEGIN
    INSERT INTO question_fts(rowid, content_plain, taxonomy_path)
    VALUES (NEW.rowid, NEW.content_plain, NEW.taxonomy_path);
END;

CREATE TRIGGER IF NOT EXISTS q_fts_delete AFTER DELETE ON questions BEGIN
    INSERT INTO question_fts(question_fts, rowid, content_plain, taxonomy_path)
    VALUES ('delete', OLD.rowid, OLD.content_plain, OLD.taxonomy_path);
END;

CREATE TRIGGER IF NOT EXISTS q_fts_update AFTER UPDATE ON questions BEGIN
    INSERT INTO question_fts(question_fts, rowid, content_plain, taxonomy_path)
    VALUES ('delete', OLD.rowid, OLD.content_plain, OLD.taxonomy_path);
    INSERT INTO question_fts(rowid, content_plain, taxonomy_path)
    VALUES (NEW.rowid, NEW.content_plain, NEW.taxonomy_path);
END;
