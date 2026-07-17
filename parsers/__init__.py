"""视频平台解析器模块"""

from .base import (
    ParserResult,
    PlatformInfo,
    BaseParser,
    detect_platform,
    extract_urls,
)
from .douyin import DouyinParser
from .bilibili import BilibiliParser
from .xiaohongshu import XiaohongshuParser
from .kuaishou import KuaishouParser
from .twitter import TwitterParser

PLATFORM_PARSERS = {
    "douyin": DouyinParser,
    "bilibili": BilibiliParser,
    "xiaohongshu": XiaohongshuParser,
    "kuaishou": KuaishouParser,
    "twitter": TwitterParser,
}

__all__ = [
    "ParserResult",
    "PlatformInfo",
    "BaseParser",
    "detect_platform",
    "extract_urls",
    "PLATFORM_PARSERS",
]
