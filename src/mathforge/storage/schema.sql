-- MathForge SQLite Schema v2
-- 精简字段、proof/solution 嵌入主表、统一 tag 格式

PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

-- ── 知识点 ──

CREATE TABLE IF NOT EXISTS knowledge_points (
    id TEXT PRIMARY KEY,
    kp_type TEXT NOT NULL DEFAULT 'theorem',
    number TEXT DEFAULT '',
    content TEXT NOT NULL DEFAULT '',
    proof_content TEXT,
    proof_methods TEXT DEFAULT '[]',
    difficulty INTEGER NOT NULL DEFAULT 5 CHECK(difficulty BETWEEN 1 AND 10),
    subject TEXT NOT NULL DEFAULT '',
    taxonomy_path TEXT DEFAULT '',
    tags TEXT DEFAULT '[]',
    -- 来源
    source_file TEXT DEFAULT '',
    source_chapter TEXT DEFAULT '',
    source_section TEXT DEFAULT '',
    -- 质量
    quality_grade TEXT DEFAULT 'B',
    content_hash TEXT DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ── 题目（solution 直接嵌入）──

CREATE TABLE IF NOT EXISTS questions (
    id TEXT PRIMARY KEY,
    question_type TEXT NOT NULL DEFAULT 'proof',
    number TEXT DEFAULT '',
    content TEXT NOT NULL DEFAULT '',
    solution_content TEXT,
    solution_methods TEXT DEFAULT '[]',
    answer TEXT DEFAULT '',
    difficulty INTEGER NOT NULL DEFAULT 5 CHECK(difficulty BETWEEN 1 AND 10),
    subject TEXT NOT NULL DEFAULT '',
    taxonomy_path TEXT DEFAULT '',
    tags TEXT DEFAULT '[]',
    -- 来源
    source_file TEXT DEFAULT '',
    source_chapter TEXT DEFAULT '',
    exam_type TEXT,
    exam_year INTEGER,
    -- 质量
    quality_grade TEXT DEFAULT 'B',
    content_hash TEXT DEFAULT '',
    -- 生成
    is_generated INTEGER NOT NULL DEFAULT 0,
    parent_id TEXT,
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

-- ── 文档结构树 ──

CREATE TABLE IF NOT EXISTS taxonomy_nodes (
    node_id TEXT PRIMARY KEY,
    level TEXT NOT NULL CHECK(level IN ('book', 'chapter', 'section', 'subsection')),
    title TEXT NOT NULL DEFAULT '',
    number TEXT DEFAULT '',
    parent_id TEXT REFERENCES taxonomy_nodes(node_id),
    source_file TEXT NOT NULL DEFAULT '',
    position INTEGER DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS item_tree_placement (
    item_id TEXT NOT NULL,
    item_type TEXT NOT NULL CHECK(item_type IN ('kp', 'question')),
    node_id TEXT NOT NULL REFERENCES taxonomy_nodes(node_id),
    position_in_node INTEGER DEFAULT 0,
    PRIMARY KEY (item_id, item_type)
);

-- ── 索引 ──

CREATE INDEX IF NOT EXISTS idx_kp_type ON knowledge_points(kp_type);
CREATE INDEX IF NOT EXISTS idx_kp_subject ON knowledge_points(subject);
CREATE INDEX IF NOT EXISTS idx_kp_taxonomy ON knowledge_points(taxonomy_path);
CREATE INDEX IF NOT EXISTS idx_kp_hash ON knowledge_points(content_hash);

CREATE INDEX IF NOT EXISTS idx_q_type ON questions(question_type);
CREATE INDEX IF NOT EXISTS idx_q_subject ON questions(subject);
CREATE INDEX IF NOT EXISTS idx_q_taxonomy ON questions(taxonomy_path);
CREATE INDEX IF NOT EXISTS idx_q_exam ON questions(exam_type, exam_year);
CREATE INDEX IF NOT EXISTS idx_q_hash ON questions(content_hash);

CREATE INDEX IF NOT EXISTS idx_rel_source ON relations(source_id, source_type);
CREATE INDEX IF NOT EXISTS idx_rel_target ON relations(target_id, target_type);
CREATE INDEX IF NOT EXISTS idx_rel_type ON relations(relation_type);

CREATE INDEX IF NOT EXISTS idx_tree_parent ON taxonomy_nodes(parent_id);
CREATE INDEX IF NOT EXISTS idx_tree_source ON taxonomy_nodes(source_file);
CREATE INDEX IF NOT EXISTS idx_placement_node ON item_tree_placement(node_id);

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
