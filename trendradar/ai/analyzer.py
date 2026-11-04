# coding=utf-8
"""
AI 分析器模块

调用 AI 大模型对热点新闻进行深度分析
基于 LiteLLM 统一接口，支持 100+ AI 提供商
"""

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from trendradar.ai.client import AIClient


@dataclass
class AIAnalysisResult:
    """AI 分析结果"""
    # 新版 5 核心板块
    core_trends: str = ""                # 核心热点与舆情态势
    sentiment_controversy: str = ""      # 舆论风向与争议
    signals: str = ""                    # 异动与弱信号
    rss_insights: str = ""               # RSS 深度洞察
    outlook_strategy: str = ""           # 研判与策略建议
    standalone_summaries: Dict[str, str] = field(default_factory=dict)  # 独立展示区概括 {源ID: 概括}

    # 基础元数据
    raw_response: str = ""               # 原始响应
    success: bool = False                # 是否成功
    error: str = ""                      # 错误信息

    # 新闻数量统计
    total_news: int = 0                  # 总新闻数（热榜+RSS）
    analyzed_news: int = 0               # 实际分析的新闻数
    max_news_limit: int = 0              # 分析上限配置值
    hotlist_count: int = 0               # 热榜新闻数
    rss_count: int = 0                   # RSS 新闻数
    ai_mode: str = ""                    # AI 分析使用的模式 (daily/current/incremental)


class AIAnalyzer:
    """AI 分析器"""

    def __init__(
        self,
        ai_config: Dict[str, Any],
        analysis_config: Dict[str, Any],
        get_time_func: Callable,
        debug: bool = False,
    ):
        """
        初始化 AI 分析器

        Args:
            ai_config: AI 模型配置（LiteLLM 格式）
            analysis_config: AI 分析功能配置（language, prompt_file 等）
            get_time_func: 获取当前时间的函数
            debug: 是否开启调试模式
        """
        self.ai_config = ai_config
        self.analysis_config = analysis_config
        self.get_time_func = get_time_func
        self.debug = debug

        # 创建 AI 客户端（基于 LiteLLM）
        self.client = AIClient(ai_config)

        # 验证配置
        valid, error = self.client.validate_config()
        if not valid:
            print(f"[AI] 配置警告: {error}")

        # 从分析配置获取功能参数
        self.max_news = analysis_config.get("MAX_NEWS_FOR_ANALYSIS", 50)
        self.include_rss = analysis_config.get("INCLUDE_RSS", True)
        self.include_rank_timeline = analysis_config.get("INCLUDE_RANK_TIMELINE", False)
        self.include_standalone = analysis_config.get("INCLUDE_STANDALONE", False)
        self.language = analysis_config.get("LANGUAGE", "Chinese")

        # 加载提示词模板
        self.system_prompt, self.user_prompt_template = self._load_prompt_template(
            analysis_config.get("PROMPT_FILE", "ai_analysis_prompt.txt")
        )

    def _load_prompt_template(self, prompt_file: str) -> tuple:
        """加载提示词模板"""
        config_dir = Path(__file__).parent.parent.parent / "config"
        prompt_path = config_dir / prompt_file

        if not prompt_path.exists():
            print(f"[AI] 提示词文件不存在: {prompt_path}")
            return "", ""

        content = prompt_path.read_text(encoding="utf-8")

        # 解析 [system] 和 [user] 部分
        system_prompt = ""
        user_prompt = ""

        if "[system]" in content and "[user]" in content:
            parts = content.split("[user]")
            system_part = parts[0]
            user_part = parts[1] if len(parts) > 1 else ""

            # 提取 system 内容
            if "[system]" in system_part:
                system_prompt = system_part.split("[system]")[1].strip()

            user_prompt = user_part.strip()
        else:
            # 整个文件作为 user prompt
            user_prompt = content

        return system_prompt, user_prompt

    def analyze(
        self,
        stats: List[Dict],
        rss_stats: Optional[List[Dict]] = None,
        report_mode: str = "daily",
        report_type: str = "当日汇总",
        platforms: Optional[List[str]] = None,
        keywords: Optional[List[str]] = None,
        standalone_data: Optional[Dict] = None,
    ) -> AIAnalysisResult:
        """
        执行 AI 分析

        Args:
            stats: 热榜统计数据
            rss_stats: RSS 统计数据
            report_mode: 报告模式
            report_type: 报告类型
            platforms: 平台列表
            keywords: 关键词列表

        Returns:
            AIAnalysisResult: 分析结果
        """
        
        # 打印配置信息方便调试
        model = self.ai_config.get("MODEL", "unknown")
        api_key = self.client.api_key or ""
        api_base = self.ai_config.get("API_BASE", "")
        masked_key = f"{api_key[:5]}******" if len(api_key) >= 5 else "******"
        model_display = model.replace("/", "/\u200b") if model else "unknown"

        print(f"[AI] 模型: {model_display}")
        print(f"[AI] Key : {masked_key}")

        if api_base:
            print(f"[AI] 接口: 存在自定义 API 端点")

        timeout = self.ai_config.get("TIMEOUT", 120)
        max_tokens = self.ai_config.get("MAX_TOKENS", 5000)
        print(f"[AI] 参数: timeout={timeout}, max_tokens={max_tokens}")

        if not self.client.api_key:
            return AIAnalysisResult(
                success=False,
                error="未配置 AI API Key，请在 config.yaml 或环境变量 AI_API_KEY 中设置"
            )

        # 准备新闻内容并获取统计数据
        news_content, rss_content, hotlist_total, rss_total, analyzed_count = self._prepare_news_content(stats, rss_stats)
        total_news = hotlist_total + rss_total

        if not news_content and not rss_content:
            return AIAnalysisResult(
                success=False,
                error="没有可分析的新闻内容",
                total_news=total_news,
                hotlist_count=hotlist_total,
                rss_count=rss_total,
                analyzed_news=0,
                max_news_limit=self.max_news
            )

        # 构建提示词
        current_time = self.get_time_func().strftime("%Y-%m-%d %H:%M:%S")

        # 提取关键词
        if not keywords:
            keywords = [s.get("word", "") for s in stats if s.get("word")] if stats else []

        # 使用安全的字符串替换，避免模板中其他花括号（如 JSON 示例）被误解析
        user_prompt = self.user_prompt_template
        user_prompt = user_prompt.replace("{report_mode}", report_mode)
        user_prompt = user_prompt.replace("{report_type}", report_type)
        user_prompt = user_prompt.replace("{current_time}", current_time)
        user_prompt = user_prompt.replace("{news_count}", str(hotlist_total))
        user_prompt = user_prompt.replace("{rss_count}", str(rss_total))
        user_prompt = user_prompt.replace("{platforms}", ", ".join(platforms) if platforms else "多平台")
        user_prompt = user_prompt.replace("{keywords}", ", ".join(keywords[:20]) if keywords else "无")
        user_prompt = user_prompt.replace("{news_content}", news_content)
        user_prompt = user_prompt.replace("{rss_content}", rss_content)
        user_prompt = user_prompt.replace("{language}", self.language)

        # 构建独立展示区内容
        standalone_content = ""
        if self.include_standalone and standalone_data:
            standalone_content = self._prepare_standalone_content(standalone_data)
        user_prompt = user_prompt.replace("{standalone_content}", standalone_content)

        if self.debug:
            print("\n" + "=" * 80)
            print("[AI 调试] 发送给 AI 的完整提示词")
            print("=" * 80)
            if self.system_prompt:
                print("\n--- System Prompt ---")
                print(self.system_prompt)
            print("\n--- User Prompt ---")
            print(user_prompt)
            print("=" * 80 + "\n")

        # 调用 AI API（使用 LiteLLM）
        try:
            response = self._call_ai(user_prompt)
            result = self._parse_response(response)

            # 首次响应必须同时通过 JSON 解析和业务字段校验，否则尝试修复一次
            if not result.success:
                first_error = result.error or "AI 响应未通过校验"
                print("[AI] 首次响应未通过 JSON/字段校验，尝试修复...")

                retry_result = self._retry_fix_json(response, first_error)
                if retry_result and retry_result.success and not retry_result.error:
                    print("[AI] JSON 修复及字段校验成功")
                    # 保留首次模型响应，便于后续排查
                    retry_result.raw_response = response
                    result = retry_result
                else:
                    retry_error = (
                        retry_result.error
                        if retry_result and retry_result.error
                        else "修复请求未返回有效结果"
                    )
                    result.success = False
                    result.error = (
                        f"首次响应校验失败：{first_error}；"
                        f"二次修复也未通过校验：{retry_error}"
                    )
                    print("[AI] JSON 修复未通过字段校验，按分析失败处理")

            # 如果配置未启用 standalone 分析，强制清空
            if not self.include_standalone:
                result.standalone_summaries = {}

            # 填充统计数据
            result.total_news = total_news
            result.hotlist_count = hotlist_total
            result.rss_count = rss_total
            result.analyzed_news = analyzed_count
            result.max_news_limit = self.max_news
            return result
        except Exception as e:
            error_type = type(e).__name__
            error_msg = str(e)

            # 截断过长的错误消息
            if len(error_msg) > 200:
                error_msg = error_msg[:200] + "..."
            friendly_msg = f"AI 分析失败 ({error_type}): {error_msg}"

            return AIAnalysisResult(
                success=False,
                error=friendly_msg
            )

    def _format_time_range(self, first_time: str, last_time: str) -> str:
        """格式化时间范围（简化显示，只保留时分）"""

        def extract_time(time_str: str) -> str:
            if not time_str:
                return "-"
            if " " in time_str:
                parts = time_str.split(" ")
                if len(parts) >= 2:
                    time_part = parts[1]
                    if ":" in time_part:
                        return time_part[:5]
            elif ":" in time_str:
                return time_str[:5]

            result = time_str[:5] if len(time_str) >= 5 else time_str
            if len(result) == 5 and result[2] == "-":
                result = result.replace("-", ":")
            return result

        first = extract_time(first_time)
        last = extract_time(last_time)

        if first == last or last == "-":
            return first
        return f"{first}~{last}"

    def _call_ai(self, user_prompt: str) -> str:
        """调用 AI API（使用 LiteLLM），对“空响应”做重试兜底"""
        messages = []
        if self.system_prompt:
            messages.append({"role": "system", "content": self.system_prompt})
        messages.append({"role": "user", "content": user_prompt})

        max_attempts = 3
        last_content = ""

        for attempt in range(1, max_attempts + 1):
            content = self.client.chat(messages)
            last_content = content or ""

            if last_content.strip():
                return last_content

            print(f"[AI] 警告: AI 返回空响应，重试 {attempt}/{max_attempts}")

            if attempt < max_attempts:
                import time
                time.sleep(attempt)

        raise RuntimeError("AI 返回空响应（重试后仍为空）")
    
    def _prepare_news_content(
        self,
        stats: List[Dict],
        rss_stats: Optional[List[Dict]] = None,
    ) -> tuple:
        """
        准备新闻内容文本（增强版）

        热榜新闻包含：来源、标题、排名范围、时间范围、出现次数
        RSS 包含：来源、标题、发布时间

        Returns:
            tuple: (news_content, rss_content, hotlist_total, rss_total, analyzed_count)
        """
        news_lines = []
        rss_lines = []
        news_count = 0
        rss_count = 0

        # 计算总新闻数
        hotlist_total = sum(len(s.get("titles", [])) for s in stats) if stats else 0
        rss_total = sum(len(s.get("titles", [])) for s in rss_stats) if rss_stats else 0

        # 热榜内容
        if stats:
            for stat in stats:
                word = stat.get("word", "")
                titles = stat.get("titles", [])
                if word and titles:
                    news_lines.append(f"\n**{word}** ({len(titles)}条)")
                    for t in titles:
                        if not isinstance(t, dict):
                            continue
                        title = t.get("title", "")
                        if not title:
                            continue

                        # 来源
                        source = t.get("source_name", t.get("source", ""))

                        # 构建行
                        if source:
                            line = f"- [{source}] {title}"
                        else:
                            line = f"- {title}"

                        # 始终显示简化格式：排名范围 + 时间范围 + 出现次数
                        ranks = t.get("ranks", [])
                        if ranks:
                            min_rank = min(ranks)
                            max_rank = max(ranks)
                            rank_str = f"{min_rank}" if min_rank == max_rank else f"{min_rank}-{max_rank}"
                        else:
                            rank_str = "-"

                        first_time = t.get("first_time", "")
                        last_time = t.get("last_time", "")
                        time_str = self._format_time_range(first_time, last_time)

                        appear_count = t.get("count", 1)

                        line += f" | 排名:{rank_str} | 时间:{time_str} | 出现:{appear_count}次"

                        # 开启完整时间线时，额外添加轨迹
                        if self.include_rank_timeline:
                            rank_timeline = t.get("rank_timeline", [])
                            timeline_str = self._format_rank_timeline(rank_timeline)
                            line += f" | 轨迹:{timeline_str}"

                        news_lines.append(line)

                        news_count += 1
                        if news_count >= self.max_news:
                            break
                if news_count >= self.max_news:
                    break

        # RSS 内容（仅在启用时构建）
        if self.include_rss and rss_stats:
            remaining = self.max_news - news_count
            for stat in rss_stats:
                if rss_count >= remaining:
                    break
                word = stat.get("word", "")
                titles = stat.get("titles", [])
                if word and titles:
                    rss_lines.append(f"\n**{word}** ({len(titles)}条)")
                    for t in titles:
                        if not isinstance(t, dict):
                            continue
                        title = t.get("title", "")
                        if not title:
                            continue

                        # 来源
                        source = t.get("source_name", t.get("feed_name", ""))

                        # 发布时间
                        time_display = t.get("time_display", "")

                        # 构建行：[来源] 标题 | 发布时间
                        if source:
                            line = f"- [{source}] {title}"
                        else:
                            line = f"- {title}"
                        if time_display:
                            line += f" | {time_display}"
                        rss_lines.append(line)

                        rss_count += 1
                        if rss_count >= remaining:
                            break

        news_content = "\n".join(news_lines) if news_lines else ""
        rss_content = "\n".join(rss_lines) if rss_lines else ""
        total_count = news_count + rss_count

        return news_content, rss_content, hotlist_total, rss_total, total_count

    def _retry_fix_json(self, original_response: str, error_msg: str) -> Optional[AIAnalysisResult]:
        """修复 AI 响应，并验证六字段结构及超短交易作战卡格式。"""
        messages = [
            {
                "role": "system",
                "content": (
                    "你是一个严格的 JSON 响应修复助手。\n"
                    "你必须返回一个合法 JSON 对象，不能返回 Markdown 代码块或解释文字。\n"
                    "JSON 必须包含以下六个字段，字段名不得改变：\n"
                    "core_trends、sentiment_controversy、signals、rss_insights、"
                    "outlook_strategy、standalone_summaries。\n"
                    "前五个字段必须是非空字符串；standalone_summaries 必须是对象，"
                    "其中 key 和 value 都必须是字符串，没有独立展示数据时使用 {}。\n"
                    "特别注意：outlook_strategy 必须是一份多行的中国 A 股超短交易作战卡。\n"
                    "第一项标题必须以‘1. 中国A股短线交易者：’开头。\n"
                    "作战卡必须逐行包含以下 12 个固定标签，每个标签必须从新的一行开始，"
                    "名称不得改写，不得将两个或多个标签合并到同一行：\n"
                    "天时：、地利：、人和：、风险等级：、建议总体仓位：、反向利空路径：、"
                    "验证点：、今日核心观察点：、盘中触发器：、离场触发器：、进攻方向：、风险红线：。\n"
                    "即使缺少指数、成交额、板块行情或个股资料，也必须保留对应标签，"
                    "并填写‘未知/无法确定’或‘需结合实时行情确认’，不得删除标签或编造数据。\n"
                    "如果原始响应缺少标签，只能依据原始响应已有信息补全；没有依据时填写‘未知/无法确定’。\n"
                    "如果原始响应缺少字段或字段为空，只能根据原始响应已有信息补全；"
                    "无法从原始响应确定的内容填写‘未知/无法确定’，不得编造事实。\n"
                    "保留原始响应中已有的有效分析内容，不要擅自改变结论。\n"
                    "只返回完整、可解析的纯 JSON。"
                ),
            },
            {
                "role": "user",
                "content": (
                    f"原始响应校验失败，错误如下：\n{error_msg}\n\n"
                    f"原始 AI 响应如下：\n{original_response}\n\n"
                    "请修复 JSON 语法、六个字段结构，以及 outlook_strategy 的作战卡结构。"
                    "必须补齐全部六个字段；outlook_strategy 必须补齐全部 12 个标签，"
                    "并确保每个标签各自独占一行，第一项标题以‘1. 中国A股短线交易者：’开头。"
                    "缺少可靠依据时填写‘未知/无法确定’，需要行情确认时写‘需结合实时行情确认’。"
                    "前五个字段必须是非空字符串；standalone_summaries 没有数据时返回空对象 {}。"
                    "不要输出任何 JSON 以外的内容。"
                ),
            },
        ]

        try:
            response = self.client.chat(messages)
            repaired_result = self._parse_response(response)
            if not repaired_result.success:
                print(f"[AI] 修复响应仍未通过校验：{repaired_result.error}")
                return repaired_result
            return repaired_result
        except Exception as e:
            print(f"[AI] 重试修复 JSON 异常: {type(e).__name__}: {e}")
            return None



    def _format_rank_timeline(self, rank_timeline: List[Dict]) -> str:
        """格式化排名时间线"""
        if not rank_timeline:
            return "-"

        parts = []
        for item in rank_timeline:
            time_str = item.get("time", "")
            if len(time_str) == 5 and time_str[2] == '-':
                time_str = time_str.replace('-', ':')
            rank = item.get("rank")
            if rank is None:
                parts.append(f"0({time_str})")
            else:
                parts.append(f"{rank}({time_str})")

        return "→".join(parts)

    def _prepare_standalone_content(self, standalone_data: Dict) -> str:
        """
        将独立展示区数据转为文本，注入 AI 分析 prompt

        Args:
            standalone_data: 独立展示区数据 {"platforms": [...], "rss_feeds": [...]}

        Returns:
            格式化的文本内容
        """
        lines = []

        # 热榜平台
        for platform in standalone_data.get("platforms", []):
            platform_id = platform.get("id", "")
            platform_name = platform.get("name", platform_id)
            items = platform.get("items", [])
            if not items:
                continue

            lines.append(f"### [{platform_name}]")
            for item in items:
                title = item.get("title", "")
                if not title:
                    continue

                line = f"- {title}"

                # 排名信息
                ranks = item.get("ranks", [])
                if ranks:
                    min_rank = min(ranks)
                    max_rank = max(ranks)
                    rank_str = f"{min_rank}" if min_rank == max_rank else f"{min_rank}-{max_rank}"
                    line += f" | 排名:{rank_str}"

                # 时间范围
                first_time = item.get("first_time", "")
                last_time = item.get("last_time", "")
                if first_time:
                    time_str = self._format_time_range(first_time, last_time)
                    line += f" | 时间:{time_str}"

                # 出现次数
                count = item.get("count", 1)
                if count > 1:
                    line += f" | 出现:{count}次"

                # 排名轨迹（如果启用）
                if self.include_rank_timeline:
                    rank_timeline = item.get("rank_timeline", [])
                    if rank_timeline:
                        timeline_str = self._format_rank_timeline(rank_timeline)
                        line += f" | 轨迹:{timeline_str}"

                lines.append(line)
            lines.append("")

        # RSS 源
        for feed in standalone_data.get("rss_feeds", []):
            feed_id = feed.get("id", "")
            feed_name = feed.get("name", feed_id)
            items = feed.get("items", [])
            if not items:
                continue

            lines.append(f"### [{feed_name}]")
            for item in items:
                title = item.get("title", "")
                if not title:
                    continue

                line = f"- {title}"
                published_at = item.get("published_at", "")
                if published_at:
                    line += f" | {published_at}"

                lines.append(line)
            lines.append("")

        return "\n".join(lines)

    def _parse_response(self, response: str) -> AIAnalysisResult:
        """解析并严格校验 JSON、业务字段及超短交易作战卡结构。"""
        result = AIAnalysisResult(raw_response=response or "")

        if not isinstance(response, str) or not response.strip():
            result.error = "AI 返回空响应"
            return result

        response_text = response.strip().lstrip("\ufeff")
        json_str = response_text

        # 兼容 ```json ... ``` 或普通 Markdown 代码块
        if "```" in response_text:
            parts = response_text.split("```")
            for block in parts[1::2]:
                candidate = block.strip()
                if candidate[:4].lower() == "json":
                    candidate = candidate[4:].strip()
                if candidate:
                    json_str = candidate
                    break

        # 兼容 JSON 前后意外出现的说明文字，只截取最外层 JSON 对象
        if not json_str.lstrip().startswith("{"):
            left = json_str.find("{")
            right = json_str.rfind("}")
            if left >= 0 and right > left:
                json_str = json_str[left:right + 1]

        json_str = json_str.strip()
        if not json_str:
            result.error = "提取的 JSON 内容为空"
            return result

        # 第一步：标准 JSON 解析
        data = None
        parse_error = None
        try:
            data = json.loads(json_str)
        except json.JSONDecodeError as e:
            parse_error = e

        # 第二步：尝试本地 JSON 修复；本地修复后仍须通过下面的字段校验
        if data is None:
            try:
                from json_repair import repair_json
                repaired = repair_json(json_str, return_objects=True)
                if isinstance(repaired, dict):
                    data = repaired
                    print("[AI] JSON 本地语法修复成功，继续校验字段")
            except Exception as e:
                if self.debug:
                    print(f"[AI] JSON 本地修复不可用或失败：{type(e).__name__}: {e}")

        if data is None:
            if parse_error:
                pos = parse_error.pos
                error_context = json_str[max(0, pos - 30):pos + 30]
                result.error = f"JSON 解析错误 (位置 {pos}): {parse_error.msg}"
                if error_context:
                    result.error += f"，上下文: ...{error_context}..."
            else:
                result.error = "JSON 解析失败，且本地修复未能生成 JSON 对象"
            return result

        # 顶层必须是 JSON 对象
        if not isinstance(data, dict):
            result.error = f"JSON 顶层类型错误：要求 object，实际为 {type(data).__name__}"
            return result

        # 六个字段必须全部存在
        required_fields = [
            "core_trends",
            "sentiment_controversy",
            "signals",
            "rss_insights",
            "outlook_strategy",
            "standalone_summaries",
        ]
        missing_fields = [field for field in required_fields if field not in data]
        if missing_fields:
            result.error = "AI 响应缺少必需字段：" + ", ".join(missing_fields)
            return result

        # 前五个字段必须是非空字符串
        text_fields = [
            "core_trends",
            "sentiment_controversy",
            "signals",
            "rss_insights",
            "outlook_strategy",
        ]
        invalid_types = [
            field for field in text_fields if not isinstance(data.get(field), str)
        ]
        if invalid_types:
            result.error = "AI 分析字段类型错误（必须是字符串）：" + ", ".join(invalid_types)
            return result

        empty_fields = [
            field for field in text_fields if not data[field].strip()
        ]
        if empty_fields:
            result.error = "AI 分析字段为空：" + ", ".join(empty_fields)
            return result

        # 第二层业务校验：outlook_strategy 必须包含完整的多行交易作战卡
        strategy = data["outlook_strategy"].replace("\r\n", "\n").replace("\r", "\n")
        strategy_lines = strategy.splitlines()

        # 第一项标题必须明确标识为中国 A 股短线交易者
        trader_heading = re.compile(
            r"^[ \t]*1\.[ \t]*中国[ \t]*A[ \t]*股短线交易者[ \t]*[:：]"
        )
        if not any(trader_heading.match(line) for line in strategy_lines):
            result.error = (
                "outlook_strategy 作战卡格式错误：缺少独立标题行"
                "‘1. 中国A股短线交易者：’"
            )
            return result

        required_strategy_labels = [
            "天时",
            "地利",
            "人和",
            "风险等级",
            "建议总体仓位",
            "反向利空路径",
            "验证点",
            "今日核心观察点",
            "盘中触发器",
            "离场触发器",
            "进攻方向",
            "风险红线",
        ]
        missing_labels = []
        merged_labels = []
        duplicated_labels = []

        for label in required_strategy_labels:
            label_pattern = re.compile(
                rf"^[ \t]*{re.escape(label)}[ \t]*[:：]"
            )
            matching_lines = [
                line for line in strategy_lines if label_pattern.match(line)
            ]

            if not matching_lines:
                # 标签出现在句子中间，通常表示模型把多个标签合并到了一行
                if any(label in line for line in strategy_lines):
                    merged_labels.append(label)
                else:
                    missing_labels.append(label)
            elif len(matching_lines) > 1:
                duplicated_labels.append(label)

        validation_errors = []
        if missing_labels:
            validation_errors.append("缺少标签：" + "、".join(missing_labels))
        if merged_labels:
            validation_errors.append("标签未独占一行或与其他内容合并：" + "、".join(merged_labels))
        if duplicated_labels:
            validation_errors.append("标签重复出现：" + "、".join(duplicated_labels))

        if validation_errors:
            result.error = "outlook_strategy 作战卡格式校验失败；" + "；".join(validation_errors)
            return result

        # 独立展示区必须是 {来源名称: 概括} 的字符串字典；无数据时允许 {}
        summaries = data.get("standalone_summaries")
        if not isinstance(summaries, dict):
            result.error = "AI 响应字段 standalone_summaries 类型错误（必须是对象）"
            return result

        invalid_summary_items = [
            str(key) for key, value in summaries.items()
            if not isinstance(key, str) or not isinstance(value, str)
        ]
        if invalid_summary_items:
            result.error = (
                "standalone_summaries 中存在非字符串 key/value："
                + ", ".join(invalid_summary_items[:10])
            )
            return result

        # 所有校验通过后，才写入结果并标记成功
        result.core_trends = data["core_trends"].strip()
        result.sentiment_controversy = data["sentiment_controversy"].strip()
        result.signals = data["signals"].strip()
        result.rss_insights = data["rss_insights"].strip()
        result.outlook_strategy = strategy.strip()
        result.standalone_summaries = {
            key: value.strip() for key, value in summaries.items()
        }
        result.success = True
        result.error = ""
        return result
