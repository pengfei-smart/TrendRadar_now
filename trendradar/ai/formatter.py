# coding=utf-8
"""
AI 分析结果格式化模块

将 AI 分析结果格式化为各推送渠道的样式
"""

import html as html_lib
import re
from .analyzer import AIAnalysisResult


def _escape_html(text: str) -> str:
    """转义 HTML 特殊字符，防止 XSS 攻击"""
    return html_lib.escape(text) if text else ""


def _format_list_content(text: str) -> str:
    """规范化 AI 分析内容的标题、语义标签和编号列表，避免长段落挤在一起。"""
    if not text:
        return ""

    # 统一换行符并清理行尾空格，不对普通句号强制断行。
    result = str(text).replace("\r\n", "\n").replace("\r", "\n")
    result = re.sub(r"[ \t]+\n", "\n", result).strip()

    # 将模型偶尔写成“核心判断：内容”的行内小标题，规范为独立结构标题。
    # 仅匹配明确带冒号的固定标签，避免拆开普通叙述句。
    semantic_headings = (
        "核心判断", "国际主线", "国内主线", "宏观主线", "关键佐证",
        "微观佐证", "短线含义", "A股短线含义", "情绪温度", "主流声音",
        "潜在分歧", "主要分歧", "跨平台共振/温差", "轨迹突变", "弱信号捕捉",
        "认知纠偏", "硬核增量", "数据状态", "专业增量", "A股短线影响",
        "资产逻辑映射",
    )
    for label in sorted(semantic_headings, key=len, reverse=True):
        pattern = re.compile(
            rf"(?<!\n)(?<!【)({re.escape(label)})[ \t]*[:：][ \t]*"
        )
        result = pattern.sub(
            lambda match: f"\n\n【{match.group(1)}】\n", result
        )

    # 兼容原有规则：标题标签前留出段落间距，标签后另起一行放正文。
    result = re.sub(r"(?<!\n)[ \t]*(【[^】\n]+】)", r"\n\n\1", result)
    result = re.sub(r"(【[^】\n]+】)\n+([:：])", r"\1\2", result)
    result = re.sub(r"(【[^】\n]+】[:：]?)[ \t]*(?=\S)", r"\1\n", result)

    # 天时、地利、人和及弱信号四项字段必须处于行首；值仍留在标签同一行。
    row_labels = (
        "天时", "地利", "人和", "风险等级", "建议总体仓位",
        "反向利空路径", "验证点", "今日核心观察点", "盘中触发器",
        "离场触发器", "进攻方向", "风险红线", "信号", "来源", "对A股短线",
    )
    for label in row_labels:
        label_pattern = re.escape(label) + r"[ \t]*[:：]"
        result = re.sub(
            rf"(?<!\n)[ \t]+({label_pattern})", r"\n\1", result
        )
        result = re.sub(
            rf"([。！？；，、])\s*({label_pattern})", r"\1\n\2", result
        )

    # 处理“XX方面：”“XX领域：”等子标题，保留原格式化器的兼容行为。
    result = re.sub(
        r"([。！？；，、])\s*([a-zA-Z0-9\u4e00-\u9fa5]+(方面|领域)[:：])",
        r"\1\n\2",
        result,
    )

    # 列表编号另起一行；排除 2.0、3.5 等小数或版本号。
    result = re.sub(r"(?<=[^\n])[ \t]+(\d+\.)(?!\d)", r"\n\1", result)
    result = re.sub(r"([：:;,。；，])\s*(\d+\.)(?!\d)", r"\1\n\2", result)
    result = re.sub(r"(?<=[^\n])(\d+\.\*\*)", r"\n\1", result)
    result = re.sub(r"(?m)^(\d+\.)(?=\S)", r"\1 ", result)

    # 项目符号仅在明显的列表分隔符后换行，避免破坏英文连字符或普通短语。
    result = re.sub(r"([。！？；;])\s*[-•]\s+", r"\1\n- ", result)

    # 连续列表项之间保留一个空行；不拆分普通句子。
    result = re.sub(r"(?<![:：】])\n(\d+\.)(?!\d)", r"\n\n\1", result)
    result = re.sub(r"\n{3,}", "\n\n", result)
    return result.strip()


def _format_standalone_summaries(summaries: dict) -> str:
    """格式化独立展示区概括为纯文本行，每个源名称单独一行"""
    if not summaries:
        return ""
    lines = []
    for source_name, summary in summaries.items():
        if summary:
            lines.append(f"[{source_name}]:\n{summary}")
    return "\n\n".join(lines)


def render_ai_analysis_markdown(result: AIAnalysisResult) -> str:
    """渲染为通用 Markdown 格式（Telegram、企业微信、ntfy、Bark、Slack）"""
    if not result.success:
        return f"⚠️ AI 分析失败: {result.error}"

    lines = ["**✨ AI 热点分析**", ""]

    if result.core_trends:
        lines.extend(["**核心热点态势**", _format_list_content(result.core_trends), ""])

    if result.sentiment_controversy:
        lines.extend(
            ["**舆论风向争议**", _format_list_content(result.sentiment_controversy), ""]
        )

    if result.signals:
        lines.extend(["**异动与弱信号**", _format_list_content(result.signals), ""])

    if result.rss_insights:
        lines.extend(
            ["**RSS 深度洞察**", _format_list_content(result.rss_insights), ""]
        )

    if result.outlook_strategy:
        lines.extend(
            ["**研判策略建议**", _format_list_content(result.outlook_strategy), ""]
        )

    if result.standalone_summaries:
        summaries_text = _format_standalone_summaries(result.standalone_summaries)
        if summaries_text:
            lines.extend(["**独立源点速览**", summaries_text])

    return "\n".join(lines)


def render_ai_analysis_feishu(result: AIAnalysisResult) -> str:
    """渲染为飞书卡片 Markdown 格式"""
    if not result.success:
        return f"⚠️ AI 分析失败: {result.error}"

    lines = ["**✨ AI 热点分析**", ""]

    if result.core_trends:
        lines.extend(["**核心热点态势**", _format_list_content(result.core_trends), ""])

    if result.sentiment_controversy:
        lines.extend(
            ["**舆论风向争议**", _format_list_content(result.sentiment_controversy), ""]
        )

    if result.signals:
        lines.extend(["**异动与弱信号**", _format_list_content(result.signals), ""])

    if result.rss_insights:
        lines.extend(
            ["**RSS 深度洞察**", _format_list_content(result.rss_insights), ""]
        )

    if result.outlook_strategy:
        lines.extend(
            ["**研判策略建议**", _format_list_content(result.outlook_strategy), ""]
        )

    if result.standalone_summaries:
        summaries_text = _format_standalone_summaries(result.standalone_summaries)
        if summaries_text:
            lines.extend(["**独立源点速览**", summaries_text])

    return "\n".join(lines)


def render_ai_analysis_dingtalk(result: AIAnalysisResult) -> str:
    """渲染为钉钉 Markdown 格式"""
    if not result.success:
        return f"⚠️ AI 分析失败: {result.error}"

    lines = ["### ✨ AI 热点分析", ""]

    if result.core_trends:
        lines.extend(
            ["#### 核心热点态势", _format_list_content(result.core_trends), ""]
        )

    if result.sentiment_controversy:
        lines.extend(
            [
                "#### 舆论风向争议",
                _format_list_content(result.sentiment_controversy),
                "",
            ]
        )

    if result.signals:
        lines.extend(["#### 异动与弱信号", _format_list_content(result.signals), ""])

    if result.rss_insights:
        lines.extend(
            ["#### RSS 深度洞察", _format_list_content(result.rss_insights), ""]
        )

    if result.outlook_strategy:
        lines.extend(
            ["#### 研判策略建议", _format_list_content(result.outlook_strategy), ""]
        )

    if result.standalone_summaries:
        summaries_text = _format_standalone_summaries(result.standalone_summaries)
        if summaries_text:
            lines.extend(["#### 独立源点速览", summaries_text])

    return "\n".join(lines)


def render_ai_analysis_html(result: AIAnalysisResult) -> str:
    """渲染为 HTML 格式（邮件）"""
    if not result.success:
        return (
            f'<div class="ai-error">⚠️ AI 分析失败: {_escape_html(result.error)}</div>'
        )

    html_parts = ['<div class="ai-analysis">', "<h3>✨ AI 热点分析</h3>"]

    if result.core_trends:
        content = _format_list_content(result.core_trends)
        content_html = _escape_html(content).replace("\n", "<br>")
        html_parts.extend(
            [
                '<div class="ai-section">',
                "<h4>核心热点态势</h4>",
                f'<div class="ai-content">{content_html}</div>',
                "</div>",
            ]
        )

    if result.sentiment_controversy:
        content = _format_list_content(result.sentiment_controversy)
        content_html = _escape_html(content).replace("\n", "<br>")
        html_parts.extend(
            [
                '<div class="ai-section">',
                "<h4>舆论风向争议</h4>",
                f'<div class="ai-content">{content_html}</div>',
                "</div>",
            ]
        )

    if result.signals:
        content = _format_list_content(result.signals)
        content_html = _escape_html(content).replace("\n", "<br>")
        html_parts.extend(
            [
                '<div class="ai-section">',
                "<h4>异动与弱信号</h4>",
                f'<div class="ai-content">{content_html}</div>',
                "</div>",
            ]
        )

    if result.rss_insights:
        content = _format_list_content(result.rss_insights)
        content_html = _escape_html(content).replace("\n", "<br>")
        html_parts.extend(
            [
                '<div class="ai-section">',
                "<h4>RSS 深度洞察</h4>",
                f'<div class="ai-content">{content_html}</div>',
                "</div>",
            ]
        )

    if result.outlook_strategy:
        content = _format_list_content(result.outlook_strategy)
        content_html = _escape_html(content).replace("\n", "<br>")
        html_parts.extend(
            [
                '<div class="ai-section ai-conclusion">',
                "<h4>研判策略建议</h4>",
                f'<div class="ai-content">{content_html}</div>',
                "</div>",
            ]
        )

    if result.standalone_summaries:
        summaries_text = _format_standalone_summaries(result.standalone_summaries)
        if summaries_text:
            summaries_html = _escape_html(summaries_text).replace("\n", "<br>")
            html_parts.extend(
                [
                    '<div class="ai-section">',
                    "<h4>独立源点速览</h4>",
                    f'<div class="ai-content">{summaries_html}</div>',
                    "</div>",
                ]
            )

    html_parts.append("</div>")
    return "\n".join(html_parts)


def render_ai_analysis_plain(result: AIAnalysisResult) -> str:
    """渲染为纯文本格式"""
    if not result.success:
        return f"AI 分析失败: {result.error}"

    lines = ["【✨ AI 热点分析】", ""]

    if result.core_trends:
        lines.extend(["[核心热点态势]", _format_list_content(result.core_trends), ""])

    if result.sentiment_controversy:
        lines.extend(
            ["[舆论风向争议]", _format_list_content(result.sentiment_controversy), ""]
        )

    if result.signals:
        lines.extend(["[异动与弱信号]", _format_list_content(result.signals), ""])

    if result.rss_insights:
        lines.extend(["[RSS 深度洞察]", _format_list_content(result.rss_insights), ""])

    if result.outlook_strategy:
        lines.extend(["[研判策略建议]", _format_list_content(result.outlook_strategy), ""])

    if result.standalone_summaries:
        summaries_text = _format_standalone_summaries(result.standalone_summaries)
        if summaries_text:
            lines.extend(["[独立源点速览]", summaries_text])

    return "\n".join(lines)


def get_ai_analysis_renderer(channel: str):
    """根据渠道获取对应的渲染函数"""
    renderers = {
        "feishu": render_ai_analysis_feishu,
        "dingtalk": render_ai_analysis_dingtalk,
        "wework": render_ai_analysis_markdown,
        "telegram": render_ai_analysis_markdown,
        "email": render_ai_analysis_html_rich,  # 邮件使用丰富样式，配合 HTML 报告的 CSS
        "ntfy": render_ai_analysis_markdown,
        "bark": render_ai_analysis_plain,
        "slack": render_ai_analysis_markdown,
    }
    return renderers.get(channel, render_ai_analysis_markdown)


def render_ai_analysis_html_rich(result: AIAnalysisResult) -> str:
    """渲染为丰富样式的 HTML 格式（HTML 报告用）"""
    if not result:
        return ""

    # 检查是否成功
    if not result.success:
        error_msg = result.error or "未知错误"
        return f"""
                <div class="ai-section">
                    <div class="ai-error">⚠️ AI 分析失败: {_escape_html(str(error_msg))}</div>
                </div>"""

    ai_html = """
                <div class="ai-section">
                    <div class="ai-section-header">
                        <div class="ai-section-title">✨ AI 热点分析</div>
                        <span class="ai-section-badge">AI</span>
                    </div>"""

    if result.core_trends:
        content = _format_list_content(result.core_trends)
        content_html = _escape_html(content).replace("\n", "<br>")
        ai_html += f"""
                    <div class="ai-block">
                        <div class="ai-block-title">核心热点态势</div>
                        <div class="ai-block-content">{content_html}</div>
                    </div>"""

    if result.sentiment_controversy:
        content = _format_list_content(result.sentiment_controversy)
        content_html = _escape_html(content).replace("\n", "<br>")
        ai_html += f"""
                    <div class="ai-block">
                        <div class="ai-block-title">舆论风向争议</div>
                        <div class="ai-block-content">{content_html}</div>
                    </div>"""

    if result.signals:
        content = _format_list_content(result.signals)
        content_html = _escape_html(content).replace("\n", "<br>")
        ai_html += f"""
                    <div class="ai-block">
                        <div class="ai-block-title">异动与弱信号</div>
                        <div class="ai-block-content">{content_html}</div>
                    </div>"""

    if result.rss_insights:
        content = _format_list_content(result.rss_insights)
        content_html = _escape_html(content).replace("\n", "<br>")
        ai_html += f"""
                    <div class="ai-block">
                        <div class="ai-block-title">RSS 深度洞察</div>
                        <div class="ai-block-content">{content_html}</div>
                    </div>"""

    if result.outlook_strategy:
        content = _format_list_content(result.outlook_strategy)
        content_html = _escape_html(content).replace("\n", "<br>")
        ai_html += f"""
                    <div class="ai-block">
                        <div class="ai-block-title">研判策略建议</div>
                        <div class="ai-block-content">{content_html}</div>
                    </div>"""

    if result.standalone_summaries:
        summaries_text = _format_standalone_summaries(result.standalone_summaries)
        if summaries_text:
            summaries_html = _escape_html(summaries_text).replace("\n", "<br>")
            ai_html += f"""
                    <div class="ai-block">
                        <div class="ai-block-title">独立源点速览</div>
                        <div class="ai-block-content">{summaries_html}</div>
                    </div>"""

    ai_html += """
                </div>"""
    return ai_html
