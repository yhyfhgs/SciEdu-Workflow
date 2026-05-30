#!/bin/bash
# SciForge 5 阶段 Pipeline 全量执行脚本
# 架构: Task0 文档识别 → Stage1Extract → Stage1Review → Stage2Annotate → Stage2Review → Stage3Persist
# 直接递归处理整个 Raw Data 目录

set -uo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$PROJECT_ROOT"

DB="data/sciforge.db"
RAW_DATA_DIR="Raw Data"
LOG_DIR="data/logs"
LOG_LEVEL="INFO"
LLM_CONFIG="config/sciforge.toml"
MAX_FILES="${SCIFORGE_MAX_FILES:-10}"
MAX_LLM_CALLS="${SCIFORGE_MAX_CONCURRENT_LLM_CALLS:-30}"
VLM_MAX_CONCURRENT="${SCIFORGE_VLM_MAX_CONCURRENT:-15}"
RESTART_FLAG="${SCIFORGE_RESTART:-0}"

mkdir -p "$LOG_DIR" data/llm_logs

export PYTHONPATH="$PROJECT_ROOT/src${PYTHONPATH:+:$PYTHONPATH}"
export SCIFORGE_MAX_CONCURRENT_LLM_CALLS="$MAX_LLM_CALLS"
export SCIFORGE_VLM_MAX_CONCURRENT="$VLM_MAX_CONCURRENT"

if [ -x "$PROJECT_ROOT/.venv/bin/python" ]; then
  SCIFORGE_CMD=("$PROJECT_ROOT/.venv/bin/python" -m sciforge)
elif command -v uv >/dev/null 2>&1; then
  SCIFORGE_CMD=(uv run python -m sciforge)
else
  echo "错误: 未找到可用的 Python 运行环境（缺少 .venv/bin/python 和 uv）" >&2
  exit 1
fi

RUN_MODE="resume"
if [ "$RESTART_FLAG" = "1" ]; then
  RUN_MODE="restart"
fi

if [ ! -d "$RAW_DATA_DIR" ]; then
  echo "错误: 原始数据目录不存在: $RAW_DATA_DIR" >&2
  exit 1
fi

echo "========================================="
echo "SciForge 5 阶段 Pipeline 全量执行"
echo "DB: $DB"
echo "Raw Data: $RAW_DATA_DIR"
echo "模式: $RUN_MODE"
echo "Python: ${SCIFORGE_CMD[*]}"
echo "LLM Config: ${LLM_CONFIG:-(环境变量默认)}"
echo "目录并发: $MAX_FILES"
echo "LLM 并发上限: $MAX_LLM_CALLS"
echo "VLM 并发上限: $VLM_MAX_CONCURRENT"
echo "开始时间: $(date '+%Y-%m-%d %H:%M:%S')"
echo "========================================="

echo ""
echo "[1/1] 处理整个 Raw Data 目录"
echo "开始: $(date '+%H:%M:%S')"
set -- \
  --log-level "$LOG_LEVEL" \
  --log-dir "$LOG_DIR" \
  parse run "$RAW_DATA_DIR" \
  --db "$DB" \
  -D math \
  --backend vlm \
  -j "$MAX_FILES"

if [ -f "$LLM_CONFIG" ]; then
  set -- "$@" --llm-config "$LLM_CONFIG"
fi

if [ "$RESTART_FLAG" = "1" ]; then
  set -- "$@" --restart
fi

if ! "${SCIFORGE_CMD[@]}" "$@"; then
  echo "[1/1] 以非零码退出"
fi
echo "[1/1] 完成: $(date '+%H:%M:%S')"

echo ""
echo "========================================="
echo "Pipeline 全量执行完成"
echo "结束时间: $(date '+%Y-%m-%d %H:%M:%S')"
echo "========================================="

echo ""
echo "数据库统计:"
if ! "${SCIFORGE_CMD[@]}" parse db-stats "$DB"; then
  echo "无法读取数据库统计"
fi
