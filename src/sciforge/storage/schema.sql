-- SciForge SQLite Schema v4
-- v4 变更：新增 discipline 列（math/physics/chemistry/biology/other）作为学科路由
--         分层的第一级字段；subject 继续作为二级字符串分类。
-- v3 变更：精简字段、proof/solution 嵌入主表、统一 tag 格式、删除
--         taxonomy_nodes/item_tree_placement/relations 表、新增
--         start_line/end_line/order_index/page_start/page_end 列
--
-- 升级路径：旧 DB 不做 ALTER 迁移；重新 `parse run --restart` 即可。

PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

-- ── 知识点 ──

CREATE TABLE IF NOT EXISTS knowledge_points (
    id TEXT PRIMARY KEY,
    discipline TEXT NOT NULL DEFAULT 'math',
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
    -- 位置/顺序（pipeline 提取时填充）
    start_line INTEGER NOT NULL DEFAULT 0,
    end_line INTEGER NOT NULL DEFAULT 0,
    order_index INTEGER NOT NULL DEFAULT 0,
    page_start INTEGER,
    page_end INTEGER,
    -- 质量
    quality_grade TEXT DEFAULT 'B',
    content_hash TEXT DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ── 题目（solution 直接嵌入）──

CREATE TABLE IF NOT EXISTS questions (
    id TEXT PRIMARY KEY,
    discipline TEXT NOT NULL DEFAULT 'math',
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
    -- 位置/顺序
    start_line INTEGER NOT NULL DEFAULT 0,
    end_line INTEGER NOT NULL DEFAULT 0,
    order_index INTEGER NOT NULL DEFAULT 0,
    page_start INTEGER,
    page_end INTEGER,
    -- 质量
    quality_grade TEXT DEFAULT 'B',
    content_hash TEXT DEFAULT '',
    -- 生成
    is_generated INTEGER NOT NULL DEFAULT 0,
    parent_id TEXT,
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

-- ── 索引 ──

CREATE INDEX IF NOT EXISTS idx_kp_discipline ON knowledge_points(discipline);
CREATE INDEX IF NOT EXISTS idx_kp_type ON knowledge_points(kp_type);
CREATE INDEX IF NOT EXISTS idx_kp_subject ON knowledge_points(subject);
CREATE INDEX IF NOT EXISTS idx_kp_taxonomy ON knowledge_points(taxonomy_path);
CREATE INDEX IF NOT EXISTS idx_kp_hash ON knowledge_points(content_hash);
CREATE INDEX IF NOT EXISTS idx_kp_order ON knowledge_points(source_file, order_index);
CREATE INDEX IF NOT EXISTS idx_kp_page ON knowledge_points(source_file, page_start);

CREATE INDEX IF NOT EXISTS idx_q_discipline ON questions(discipline);
CREATE INDEX IF NOT EXISTS idx_q_type ON questions(question_type);
CREATE INDEX IF NOT EXISTS idx_q_subject ON questions(subject);
CREATE INDEX IF NOT EXISTS idx_q_taxonomy ON questions(taxonomy_path);
CREATE INDEX IF NOT EXISTS idx_q_exam ON questions(exam_type, exam_year);
CREATE INDEX IF NOT EXISTS idx_q_hash ON questions(content_hash);
CREATE INDEX IF NOT EXISTS idx_q_order ON questions(source_file, order_index);
CREATE INDEX IF NOT EXISTS idx_q_page ON questions(source_file, page_start);

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
