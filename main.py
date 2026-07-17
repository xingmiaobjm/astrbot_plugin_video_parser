"""多平台视频解析插件 - AstrBot 插件入口"""
import json
import re
import os
import tempfile
import time
import hashlib
from collections import defaultdict
from pathlib import Path
from typing import Optional

import httpx
from astrbot.api.event import filter, AstrMessageEvent, MessageEventResult
from astrbot.api.star import Context, Star
from astrbot.api import logger, AstrBotConfig
from astrbot.api.message_components import Plain, Image, At, Video
from astrbot.api.web import json_response, error_response, request


class _MessageChain:
    """AstrBot消息链包装（event.send 需要 .chain 属性）"""
    def __init__(self, chain: list):
        self.chain = chain

# 尝试导入 Reply（部分 AstrBot 版本可能没有此组件）
try:
    from astrbot.api.message_components import Reply
except ImportError:
    Reply = None

from .parsers import (
    ParserResult,
    BaseParser,
    detect_platform,
    extract_urls,
    PLATFORM_PARSERS,
)

PLUGIN_NAME = "astrbot_plugin_video_parser"
TEMP_DIR = Path(tempfile.gettempdir()) / PLUGIN_NAME


class VideoParserPlugin(Star):
    """视频解析插件主类"""

    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config

        self.stats = defaultdict(int)
        self.stats["total"] = 0
        self.stats["errors"] = 0

        self._cache: dict[str, tuple[ParserResult, float]] = {}

        self.parsers: dict[str, BaseParser] = {}
        parser_config = {
            "request_timeout": self.config.get("request_timeout", 30),
            "twitter_cookies": self.config.get("twitter_cookies", ""),
            "xiaohongshu_cookies": self.config.get("xiaohongshu_cookies", ""),
            "douyin_api_url": self.config.get("douyin_api_url", ""),
        }
        for platform, parser_cls in PLATFORM_PARSERS.items():
            self.parsers[platform] = parser_cls(parser_config)

        TEMP_DIR.mkdir(parents=True, exist_ok=True)
        self._register_web_apis()

    def _register_web_apis(self):
        self.context.register_web_api(f"/{PLUGIN_NAME}/config", self._api_get_config, ["GET"], "获取插件配置")
        self.context.register_web_api(f"/{PLUGIN_NAME}/config", self._api_save_config, ["POST"], "保存插件配置")
        self.context.register_web_api(f"/{PLUGIN_NAME}/stats", self._api_get_stats, ["GET"], "获取解析统计信息")
        self.context.register_web_api(f"/{PLUGIN_NAME}/clear_cache", self._api_clear_cache, ["POST"], "清除解析缓存")
        self.context.register_web_api(f"/{PLUGIN_NAME}/reset_stats", self._api_reset_stats, ["POST"], "重置统计数据")
        self.context.register_web_api(f"/{PLUGIN_NAME}/cache_status", self._api_cache_status, ["GET"], "获取缓存状态")
        self.context.register_web_api(f"/{PLUGIN_NAME}/parse", self._api_manual_parse, ["POST"], "手动解析视频链接")
        self.context.register_web_api(f"/{PLUGIN_NAME}/palette_colors", self._api_palette_colors, ["GET"], "读取调色盘主题色")

    # ========== WebUI API ==========

    def _get_public_config(self) -> dict:
        """获取公开配置项（返回纯 dict）"""
        public_config = {}
        for key in (
            "max_video_size_mb", "request_timeout",
            "twitter_cookies", "xiaohongshu_cookies",
            "douyin_api_url",
        ):
            public_config[key] = self.config.get(key, None)
        ep = self.config.get("enabled_platforms", {})
        public_config["enabled_platforms"] = ep if isinstance(ep, dict) else {}
        return public_config

    async def _api_get_config(self):
        """返回当前插件配置"""
        return json_response(self._get_public_config())

    async def _api_save_config(self):
        """保存插件配置"""
        payload = await request.json(default={})
        if not isinstance(payload, dict):
            return error_response("配置格式不正确", status_code=400)

        for key in (
            "max_video_size_mb", "request_timeout",
            "twitter_cookies", "xiaohongshu_cookies",
            "douyin_api_url",
        ):
            if key in payload:
                self.config[key] = payload[key]

        if "enabled_platforms" in payload:
            ep = payload["enabled_platforms"]
            if isinstance(ep, dict):
                self.config["enabled_platforms"] = ep

        if hasattr(self.config, "save_config"):
            self.config.save_config(self.config)
        logger.info("[VideoParser] 配置已保存")

        # 更新解析器配置
        parser_config = {
            "request_timeout": self.config.get("request_timeout", 30),
            "twitter_cookies": self.config.get("twitter_cookies", ""),
            "xiaohongshu_cookies": self.config.get("xiaohongshu_cookies", ""),
            "douyin_api_url": self.config.get("douyin_api_url", ""),
        }
        for parser in self.parsers.values():
            parser.config = parser_config

        return json_response({"message": "设置已保存", "config": self._get_public_config()})

    async def _api_get_stats(self):
        platform_names = {
            "douyin": "抖音", "bilibili": "B站", "xiaohongshu": "小红书",
            "twitter": "X(Twitter)",
        }
        platform_stats = {}
        for k, v in self.stats.items():
            if k in ("total", "errors"):
                continue
            platform_stats[platform_names.get(k, k)] = v
        return json_response({
            "total": self.stats["total"], "errors": self.stats["errors"],
            "platforms": platform_stats, "cache_size": len(self._cache),
        })

    async def _api_clear_cache(self):
        size = len(self._cache)
        self._cache.clear()
        return json_response({"cleared": size, "message": f"已清除 {size} 条缓存"})

    async def _api_reset_stats(self):
        self.stats.clear()
        self.stats["total"] = 0
        self.stats["errors"] = 0
        return json_response({"message": "统计已重置"})

    async def _api_cache_status(self):
        now = time.time()
        active = sum(1 for _, (_, exp) in self._cache.items() if exp > now)
        return json_response({
            "active": active, "total": len(self._cache),
            "cache_enabled": True,
            "ttl_minutes": 30,
        })

    async def _api_manual_parse(self):
        payload = await request.json(default={})
        url = payload.get("url", "")
        platform = payload.get("platform", "")
        if not url:
            return error_response("缺少 url 参数", status_code=400)
        detected = platform or detect_platform(url)
        if not detected or detected not in self.parsers:
            return error_response("不支持的链接格式", status_code=400)
        result = await self.parsers[detected].parse(url)
        return json_response({
            "success": result.success, "platform": result.platform,
            "title": result.title, "author": result.author,
            "cover_url": result.cover_url, "video_url": result.video_url,
            "image_urls": result.image_urls, "desc": result.desc, "error": result.error,
        })

    async def _api_palette_colors(self):
        """读取调色盘插件的主题色配置"""
        try:
            palette_path = Path(self.config.config_path).parent / "astrbot_plugin_palette_config.json"
            if not palette_path.exists():
                return json_response({"primary": "", "secondary": "", "found": False})

            raw = palette_path.read_text(encoding="utf-8")
            data = json.loads(raw)
            primary = data.get("theme_primary", "") or ""
            secondary = data.get("theme_secondary", "") or ""
            return json_response({
                "primary": primary, "secondary": secondary,
                "found": True,
            })
        except Exception as e:
            logger.debug(f"[VideoParser] 读取调色盘配置失败: {e}")
            return json_response({"primary": "", "secondary": "", "found": False})

    # ========== 指令处理 ==========

    @filter.command("parse_video", alias=["解析视频", "parse", "视频解析"])
    async def cmd_parse_video(self, event: AstrMessageEvent):
        """手动解析: /parse_video <url>"""
        message = event.message_str.strip()
        urls = extract_urls(message)
        if not urls:
            yield event.plain_result("请在指令后提供视频链接，例如: /parse_video https://v.douyin.com/xxx/")
            return

        url = urls[0]
        platform = detect_platform(url)
        if not platform or platform not in self.parsers:
            yield event.plain_result("不支持的链接格式，目前支持: 抖音、B站、小红书、X(Twitter)")
            return

        enabled_platforms = self.config.get("enabled_platforms", {})
        if not enabled_platforms.get(platform, True):
            yield event.plain_result(f"已禁用 {platform} 平台的解析")
            return

        yield event.plain_result(f"正在解析链接... [平台: {self.parsers[platform].platform_info.name_cn}]")
        chain = await self._parse_and_build_chain(event, url, platform)
        yield event.chain_result(chain)

    @filter.command("parse_status", alias=["解析状态"])
    async def cmd_parse_status(self, event: AstrMessageEvent):
        platform_names = {
            "douyin": "抖音", "bilibili": "B站", "xiaohongshu": "小红书",
            "twitter": "X(Twitter)",
        }
        lines = [
            "📊 视频解析统计",
            f"总解析次数: {self.stats['total']}",
            f"失败次数: {self.stats['errors']}",
            f"缓存条目: {len(self._cache)}",
        ]
        for k, name in platform_names.items():
            count = self.stats.get(k, 0)
            if count > 0:
                lines.append(f"  {name}: {count}")
        yield event.plain_result("\n".join(lines))

    # ========== 自动解析 ==========

    @filter.regex(r'https?://')
    async def on_message(self, event: AstrMessageEvent):
        # 获取消息纯文本
        message = event.message_str

        # 兜底：从消息链组件中提取文本
        if not message:
            try:
                parts = []
                for comp in event.message_obj.message:
                    text = getattr(comp, 'text', None)
                    if text:
                        parts.append(text)
                message = "".join(parts)
            except Exception:
                pass

        logger.debug(f"[VideoParser] 收到消息: {message[:200] if message else '(空)'}")
        if not message:
            return

        urls = extract_urls(message)
        if not urls:
            return

        enabled_platforms = self.config.get("enabled_platforms", {})

        for url in urls[:3]:
            platform = detect_platform(url)
            if not platform or platform not in self.parsers:
                continue
            if not enabled_platforms.get(platform, True):
                continue

            logger.info(f"[VideoParser] 检测到 {platform} 链接: {url}")
            chain = await self._parse_and_build_chain(event, url, platform)
            await event.send(_MessageChain(chain))

    # ========== 核心: 解析 + 下载 + 合并发送 ==========

    async def _parse_and_build_chain(self, event: AstrMessageEvent, url: str, platform: str) -> list:
        """解析视频 → 下载 → 构建消息链 → 返回链列表"""
        result = await self._parse_with_cache(url, platform)
        parser = self.parsers[platform]

        if not result.success:
            return [Plain(f"[{parser.platform_info.name_cn}] 解析失败: {result.error}")]

        # 下载视频
        video_path = None
        if result.video_url:
            logger.info(f"[VideoParser] 准备下载视频: platform={platform}, title={result.title}, "
                       f"url前80字符={result.video_url[:80]}")
            video_path = await self._download_video(result)
            logger.info(f"[VideoParser] 下载结果: path={video_path}")
        elif not result.video_url:
            logger.info(f"[VideoParser] 无video_url, platform={platform}, title={result.title}")

        # 构建消息链: 引用回复 + 文本 + 视频
        text = parser.format_result(result)
        chain = self._build_message_chain(event, text, video_path)

        # 图片（无视频但有图片的情况，如小红书图文）
        if not video_path and not result.video_url and result.image_urls:
            for img_url in result.image_urls[:3]:
                try:
                    chain.append(Image(file=img_url))
                except Exception as e:
                    logger.warning(f"[VideoParser] 添加图片到链失败: {e}")

        return chain

    def _build_message_chain(self, event: AstrMessageEvent, text: str, video_path: Optional[Path]) -> list:
        """构建消息链: 引用回复 + 文本 + 视频"""
        chain = []

        # 引用用户消息
        try:
            message_id = event.message_obj.message_id
            if Reply is not None and message_id:
                chain.append(Reply(message_id=message_id))
            else:
                # fallback: @用户
                sender_id = event.get_sender_id()
                if sender_id:
                    chain.append(At(qq=sender_id))
                    chain.append(Plain(" "))
        except Exception:
            pass

        chain.append(Plain(text))

        if video_path and video_path.exists():
            try:
                chain.append(Video.fromFileSystem(path=str(video_path)))
            except Exception as e:
                logger.warning(f"[VideoParser] Video组件构建失败: {e}")

        return chain

    # ========== 视频下载 ==========

    async def _download_video(self, result: ParserResult) -> Optional[Path]:
        """下载视频到临时目录，返回文件路径"""
        max_size = self.config.get("max_video_size_mb", 50) * 1024 * 1024
        timeout = self.config.get("request_timeout", 30)

        safe_title = re.sub(r'[\\/:*?"<>|]', '', result.title)[:30] if result.title else "video"
        temp_path = TEMP_DIR / f"{result.platform}_{safe_title}_{int(time.time())}.mp4"

        try:
            logger.info(f"[VideoParser] 开始下载视频: {result.video_url[:80]}...")

            headers = dict(result.download_headers) if result.download_headers else {}
            headers.setdefault("User-Agent",
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36")

            async with httpx.AsyncClient(timeout=timeout * 2, follow_redirects=True) as client:
                # HEAD 检查大小
                try:
                    head_resp = await client.head(result.video_url, headers=headers)
                    content_length = int(head_resp.headers.get("content-length", 0))
                    if content_length > max_size:
                        logger.info(f"[VideoParser] 视频过大 ({content_length/1024/1024:.1f}MB > {max_size/1024/1024:.0f}MB)，跳过下载")
                        return None
                except Exception:
                    pass

                # 流式下载
                async with client.stream("GET", result.video_url, headers=headers) as resp:
                    if resp.status_code not in (200, 206):
                        logger.error(f"[VideoParser] 下载失败 HTTP {resp.status_code}")
                        return None

                    total = 0
                    with open(temp_path, "wb") as f:
                        async for chunk in resp.aiter_bytes(chunk_size=65536):
                            f.write(chunk)
                            total += len(chunk)
                            if total > max_size:
                                f.close()
                                temp_path.unlink(missing_ok=True)
                                logger.info("[VideoParser] 下载中超出大小限制，已取消")
                                return None

                logger.info(f"[VideoParser] 下载完成: {total/1024/1024:.1f}MB -> {temp_path.name}")
                return temp_path

        except httpx.TimeoutException:
            logger.error("[VideoParser] 下载超时")
            temp_path.unlink(missing_ok=True)
        except Exception as e:
            logger.error(f"[VideoParser] 下载异常: {e}")
            temp_path.unlink(missing_ok=True)

        return None

    # ========== 解析核心 ==========

    async def _parse_with_cache(self, url: str, platform: str) -> ParserResult:
        cache_ttl = 30 * 60  # 固定30分钟缓存
        cache_key = hashlib.md5(url.encode()).hexdigest()

        now = time.time()
        cached = self._cache.get(cache_key)
        if cached:
            cached_result, expire_time = cached
            if expire_time > now:
                logger.debug(f"[VideoParser] 缓存命中: {url}")
                self._update_stats(platform, cached_result.success)
                return cached_result

        parser = self.parsers.get(platform)
        if not parser:
            result = ParserResult(success=False, platform=platform, error="不支持的平台", raw_url=url)
            self._update_stats(platform, False)
            return result

        try:
            result = await parser.parse(url)
        except Exception as e:
            logger.error(f"[VideoParser] 解析异常: {e}")
            result = ParserResult(success=False, platform=platform, error=f"解析异常: {str(e)}", raw_url=url)

        self._update_stats(platform, result.success)
        self._cache[cache_key] = (result, time.time() + cache_ttl)

        return result

    def _update_stats(self, platform: str, success: bool):
        self.stats["total"] += 1
        if not success:
            self.stats["errors"] += 1
        else:
            self.stats[platform] += 1

    async def terminate(self):
        self._cache.clear()
        try:
            import shutil
            if TEMP_DIR.exists():
                shutil.rmtree(TEMP_DIR, ignore_errors=True)
        except Exception:
            pass
        logger.info("[VideoParser] 插件已卸载")
