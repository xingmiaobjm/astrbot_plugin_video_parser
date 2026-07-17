"""视频解析器基类"""
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class PlatformInfo:
    """平台信息"""
    key: str
    name: str
    name_cn: str
    icon_url: str = ""


@dataclass
class ParserResult:
    """解析结果"""
    success: bool
    platform: str          # 平台标识: douyin/bilibili/xiaohongshu/kuaishou/twitter
    title: str = ""        # 视频/内容标题
    author: str = ""       # 作者
    cover_url: str = ""    # 封面图链接
    video_url: str = ""    # 视频直链（无水印）
    image_urls: list = field(default_factory=list)  # 图片链接列表
    desc: str = ""         # 描述
    error: str = ""        # 错误信息
    raw_url: str = ""      # 原始链接
    extra: dict = field(default_factory=dict)       # 额外数据（如音频URL、下载请求头等）
    download_headers: dict = field(default_factory=dict)  # 下载视频时需要的请求头


# URL 匹配规则
URL_PATTERNS = {
    "douyin": [
        r"https?://(?:www\.)?(?:v\.douyin\.com/[\w-]+/?\S*|(?:www\.)?douyin\.com/video/\d+)",
        r"https?://(?:www\.)?(?:v\.douyin\.com/\w+|(?:www\.)?douyin\.com/(?:video|note)/\d+)",
    ],
    "bilibili": [
        r"https?://(?:www\.)?bilibili\.com/video/(?:BV\w+|av\d+)",
        r"https?://(?:www\.)?b23\.tv/\w+",
    ],
    "xiaohongshu": [
        r"https?://(?:www\.)?xhslink\.com/\w+\S*",
        r"https?://(?:www\.)?xiaohongshu\.com/(?:discovery/item|explore)/\w+\S*",
    ],
    "kuaishou": [
        r"https?://(?:www\.)?v\.kuaishou\.com/\w+",
        r"https?://(?:www\.)?kuaishou\.com/(?:short-video|fw)/\w+",
    ],
    "twitter": [
        r"https?://(?:www\.)?(?:twitter\.com|x\.com)/\w+/status/\d+",
    ],
}


def detect_platform(url: str) -> Optional[str]:
    """检测 URL 属于哪个平台"""
    for platform, patterns in URL_PATTERNS.items():
        for pattern in patterns:
            if re.search(pattern, url, re.IGNORECASE):
                return platform
    return None


def extract_urls(text: str) -> list[str]:
    """从文本中提取所有可能的平台链接"""
    urls = []
    for patterns in URL_PATTERNS.values():
        for pattern in patterns:
            matches = re.findall(pattern, text, re.IGNORECASE)
            urls.extend(matches)
    # 去重
    seen = set()
    result = []
    for url in urls:
        if url not in seen:
            seen.add(url)
            result.append(url)
    return result


class BaseParser(ABC):
    """解析器基类"""

    def __init__(self, config: dict = None):
        self.config = config or {}
        self.timeout = self.config.get("request_timeout", 30)

    @property
    @abstractmethod
    def platform_info(self) -> PlatformInfo:
        """返回平台信息"""
        ...

    @abstractmethod
    async def parse(self, url: str) -> ParserResult:
        """解析视频链接"""
        ...

    def format_result(self, result: ParserResult) -> str:
        """将解析结果格式化为文本消息（仅标题+作者）"""
        if not result.success:
            return f"[{self.platform_info.name_cn}] 解析失败: {result.error}"

        lines = []
        if result.title:
            lines.append(f"标题: {result.title}")
        if result.author:
            lines.append(f"作者: {result.author}")

        return "\n".join(lines)

    @staticmethod
    def _get_platform_name(key: str) -> str:
        names = {
            "douyin": "抖音",
            "bilibili": "Bilibili",
            "xiaohongshu": "小红书",
            "kuaishou": "快手",
            "twitter": "X(Twitter)",
        }
        return names.get(key, key)
