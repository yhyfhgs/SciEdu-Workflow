"""提示词文件加载器：从 .txt 文件加载 Markdown 格式的提示词模板。"""

from functools import lru_cache
from pathlib import Path

_TEMPLATE_DIR = Path(__file__).parent / "templates"


@lru_cache(maxsize=64)
def load_prompt(relative_path: str) -> str:
    """从 templates/ 目录加载 .txt 提示词文件。

    Args:
        relative_path: 相对于 templates/ 目录的文件路径，
                       如 "generation/variant_generation.txt"

    Returns:
        提示词文本内容（保留首尾空白的 strip 处理）

    Raises:
        FileNotFoundError: 文件不存在时抛出
    """
    path = _TEMPLATE_DIR / relative_path
    if not path.exists():
        msg = f"提示词模板文件不存在: {path}"
        raise FileNotFoundError(msg)
    return path.read_text(encoding="utf-8").strip()
