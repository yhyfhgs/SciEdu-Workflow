"""共享测试 fixtures"""

from pathlib import Path

import pytest


@pytest.fixture
def project_root() -> Path:
    return Path(__file__).parent.parent


@pytest.fixture
def sample_tex_dir(project_root: Path) -> Path:
    return project_root / "Raw Data" / "新领军真题整理"


@pytest.fixture
def sample_md_lecture_dir(project_root: Path) -> Path:
    return project_root / "Raw Data" / "新领军讲义md"


@pytest.fixture
def sample_md_textbook_dir(project_root: Path) -> Path:
    return project_root / "Raw Data" / "数学分析课本md"


@pytest.fixture
def sample_pdf_dir(project_root: Path) -> Path:
    return project_root / "Raw Data" / "抽代教材PPT习题"


@pytest.fixture
def tmp_data_dir(tmp_path: Path) -> Path:
    d = tmp_path / "data"
    d.mkdir()
    return d


@pytest.fixture
def sample_tex_content() -> str:
    return r"""
\section{2021年3月新领军零试}

\begin{problem}[第1题]
若 $a, b$ 为正整数，求 $a+b$。
\end{problem}
\begin{solution}
$a+b = \boxed{3}$。
\end{solution}

\begin{problem}[第2题]
证明 $1+1=2$。
\end{problem}
\begin{solution}
显然成立。
\end{solution}
"""


@pytest.fixture
def sample_md_lecture_content() -> str:
    return """## 序列极限

## 1．1．预备知识

定义 1．1．1．｜分划｜设 $S$ 是一个有大小顺序的非空数集，若将 $S$ 分为两个非空子集 $A$ 和 $B$。

定理1．1．2．［戴德金分割定理］对 $\\mathbb{R}$ 的任一分划 $(A \\mid B)$, $B$ 中必有最小数。

证明：由实数的完备性可知，$A$ 有上确界。

例 1．2．1．证明 $\\lim_{n \\to \\infty} \\sqrt[n]{n} = 1$。
"""
