"""三阶段 OCR 错误修复引擎"""

from __future__ import annotations

import re

import structlog

logger = structlog.get_logger()

# ── Stage 1: 确定性替换表 ─────────────────────────────

KNOWN_SUBSTITUTIONS: dict[str, str] = {
    # 设计文档中的基础替换
    "肴求": "要求",
    "清哳": "清晰",
    "盐德金": "戴德金",
    "觔城": "邻域",
    "板限": "极限",
    "砽": "群",
    "酝": "群",
    "媨": "群",
    "迪元": "逆元",
    "迎元": "逆元",
    "沿元": "逆元",
    "唄元": "逆元",
    "通元": "逆元",
    "晃": "是",
    "舊": "意",
    "霄": "素",
    "硧界": "确界",
    "没小": "最小",
    "昌": "界",
    "笲": "算",
    "眻": "群",
    "玫": "敛",
    "致": "敛",
    "孝感": "考虑",
    "丁解": "了解",
    # 实际数据中发现的额外模式
    "低德企分到定理": "戴德金分割定理",
    "低德企分割": "戴德金分割",
    "矤列极限": "数列极限",
    "矤列": "数列",
    "收玫": "收敛",
    "收致": "收敛",
    "运笲": "运算",
    "定罣": "定理",
    "上硧界": "上确界",
    "下硧界": "下确界",
    "无㝖": "无穷",
    "极限定人": "极限定义",
    "央逼收敛": "夹逼收敛",
    "封闭运負": "封闭运算",
    "半畔": "半群",
    "交换半解": "交换半群",
    "聠": "群",
    "畔": "群",
    "㽚": "群",
    "羘": "群",
    "样": "群",
    "坏": "环",
    "含么环": "含幺环",
    "喪示": "表示",
    "喍": "界",
    "忨": "无",
}

# ── Stage 2: 数学术语白名单 ──────────────────────────

MATH_TERMS_WHITELIST: set[str] = {
    # 基础概念
    "定义",
    "定理",
    "引理",
    "推论",
    "命题",
    "性质",
    "证明",
    "例",
    "注",
    "公式",
    "习题",
    # 数学分析
    "极限",
    "收敛",
    "发散",
    "连续",
    "可微",
    "可积",
    "导数",
    "积分",
    "微分",
    "级数",
    "数列",
    "函数",
    "序列",
    "上确界",
    "下确界",
    "最大值",
    "最小值",
    "上界",
    "下界",
    "有界",
    "无界",
    "单调",
    "递增",
    "递减",
    "邻域",
    "开集",
    "闭集",
    "紧集",
    "连通",
    "一致收敛",
    "逐点收敛",
    "绝对收敛",
    "条件收敛",
    "戴德金分割",
    "柯西序列",
    "波尔查诺",
    "魏尔斯特拉斯",
    "黎曼积分",
    "勒贝格积分",
    "泰勒展开",
    "傅里叶级数",
    # 高等代数
    "线性空间",
    "向量空间",
    "基",
    "维数",
    "子空间",
    "线性映射",
    "线性变换",
    "矩阵",
    "行列式",
    "秩",
    "特征值",
    "特征向量",
    "对角化",
    "约当标准形",
    "内积",
    "正交",
    "正交基",
    "施密特正交化",
    "核",
    "像",
    "零空间",
    "值域",
    # 群论
    "群",
    "子群",
    "正规子群",
    "商群",
    "同态",
    "同构",
    "阶",
    "拉格朗日",
    "西罗",
    "置换群",
    "循环群",
    "阿贝尔群",
    "交换群",
    "群作用",
    "轨道",
    "稳定子",
    "逆元",
    "单位元",
    "幺元",
    "生成元",
    "环",
    "域",
    "理想",
    "整环",
    "多项式环",
    "含幺环",
    "交换环",
    "欧几里得整环",
}

# 图片 URL 提取
IMAGE_URL_PATTERN = re.compile(r"!\[.*?\]\((https?://cdn\.mathpix\.com/[^)]+)\)")


class OcrRepairEngine:
    """三阶段 OCR 修复引擎"""

    def __init__(self, extra_substitutions: dict[str, str] | None = None):
        self.substitutions = dict(KNOWN_SUBSTITUTIONS)
        if extra_substitutions:
            self.substitutions.update(extra_substitutions)

    def repair_known_patterns(self, text: str) -> tuple[str, list[dict]]:
        """Stage 1: 确定性替换。返回 (修复后文本, 变更日志)"""
        changes: list[dict] = []
        # 按长度降序排列，优先匹配长词（避免子串冲突）
        sorted_subs = sorted(self.substitutions.items(), key=lambda x: -len(x[0]))
        for wrong, correct in sorted_subs:
            if wrong in text:
                count = text.count(wrong)
                text = text.replace(wrong, correct)
                changes.append({"from": wrong, "to": correct, "count": count})

        if changes:
            logger.debug("ocr_stage1_repairs", total_changes=len(changes))

        return text, changes

    def validate_math_terms(self, text: str) -> list[dict]:
        """Stage 2: 数学术语校验。返回可疑词列表"""
        suspicious: list[dict] = []
        # 检查块起始位置的关键词
        block_pattern = re.compile(
            r"^(定[义理]|引理|推论|命题|性质|例|证明)\s*(\S{1,20})",
            re.MULTILINE,
        )
        for m in block_pattern.finditer(text):
            term = m.group(2).strip("．.。，,：: ")
            # 去除编号部分
            term_clean = re.sub(r"^[\d．.]+", "", term).strip()
            if term_clean and len(term_clean) >= 2:
                # 检查前几个字是否在白名单中
                first_chars = term_clean[:4]
                if not any(w in first_chars for w in MATH_TERMS_WHITELIST):
                    # 可能是 OCR 错误
                    suspicious.append(
                        {
                            "position": m.start(),
                            "context": m.group(0)[:50],
                            "suspicious_term": term_clean[:10],
                        }
                    )

        return suspicious

    def catalog_images(self, text: str) -> list[str]:
        """提取所有 Mathpix CDN 图片 URL"""
        return IMAGE_URL_PATTERN.findall(text)

    def full_repair(self, text: str) -> tuple[str, dict]:
        """执行完整修复流程（Stage 1 + Stage 2），返回 (修复后文本, 报告)"""
        # Stage 1
        text, changes = self.repair_known_patterns(text)

        # Stage 2
        suspicious = self.validate_math_terms(text)

        # 图片编目
        images = self.catalog_images(text)

        report = {
            "stage1_changes": changes,
            "stage2_suspicious": suspicious,
            "images_found": len(images),
            "image_urls": images,
        }

        logger.info(
            "ocr_repair_complete",
            stage1_fixes=len(changes),
            stage2_suspicious=len(suspicious),
            images=len(images),
        )

        return text, report

    async def repair_with_llm(self, text: str, context: str = "") -> tuple[str, list[dict]]:
        """Stage 3: LLM 辅助修复（需要 LLM 客户端）"""
        raise NotImplementedError("LLM 辅助 OCR 修复需要配置 LLM 客户端")
