"""
用户输入清洗 · 防 Prompt 注入。从 oriself-core 移植。
"""
from __future__ import annotations

import re

_ROLE_TAGS_RE = re.compile(
    r"<\s*/?\s*(system|assistant|user|prompt|instruction)\b[^>]*>", re.IGNORECASE
)
_INST_RE = re.compile(r"\[/?INST\]", re.IGNORECASE)
_SYS_RE = re.compile(r"<</?SYS>>", re.IGNORECASE)
_NEWLINE_ROLE_RE = re.compile(
    r"\n\s*(System|Assistant|Human|User)\s*:", re.IGNORECASE
)
_XML_DANGEROUS_RE = re.compile(
    r"<\s*/?\s*(reasoning|conversation|end|result|report|check|next_mode|"
    r"quiz|scenario|question|questions|option|options|answer|answers|"
    r"history_review|primary_analysis|hypothesis_stack|"
    r"think|cognitive_observation|evidence_table|final_label|"
    r"detective_lens|strategy_check|response_drafting|action|evidence|"
    r"converge_output|card|insight_paragraphs)\b[^>]*>",
    re.IGNORECASE,
)


def sanitize_user_input(text: str, max_length: int | None = 4000) -> str:
    """清洗用户自由文本输入。

    Args:
        text: 原文
        max_length: 长度上限，超限抛 ValueError；None 不限制
    Returns:
        清洗后文本
    """
    if not text:
        return ""
    if max_length is not None:
        if max_length <= 0:
            raise ValueError("max_length 必须为正整数")
        if len(text) > max_length:
            raise ValueError(f"输入长度超限：{len(text)} > {max_length}")
    # 1. 伪造的角色标签
    text = _ROLE_TAGS_RE.sub("", text)
    # 2. LLM 特定标记
    text = _INST_RE.sub("", text)
    text = _SYS_RE.sub("", text)
    # 3. 换行角色伪造
    text = _NEWLINE_ROLE_RE.sub("\n", text)
    # 4. 危险 XML 标签（防止注入 action / evidence 等干扰解析）
    text = _XML_DANGEROUS_RE.sub("", text)
    # 5. 全角尖括号
    text = text.replace("\uff1c", "<").replace("\uff1e", ">")
    # 二次过滤
    text = _ROLE_TAGS_RE.sub("", text)
    text = _XML_DANGEROUS_RE.sub("", text)
    # 6. 压缩连续分隔符
    text = re.sub(r"---+", "-", text)
    text = re.sub(r"===+", "=", text)
    # 7. 压缩多余空白行
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()
