"""抖音视频解析器 — 基于 iesdouyin.com 分享页的 _ROUTER_DATA 提取
参考: https://github.com/drdon1234/astrbot_plugin_douyin_bot
"""
import re
import json
import httpx
from datetime import datetime
from .base import BaseParser, ParserResult, PlatformInfo

try:
    from astrbot.api import logger
except ImportError:
    import logging
    logger = logging.getLogger("douyin_parser")


class DouyinParser(BaseParser):
    """抖音视频解析 — iesdouyin 分享页解析，无 API 依赖"""

    # Android 手机 UA，模拟移动端访问分享页
    MOBILE_HEADERS = {
        "User-Agent": "Mozilla/5.0 (Linux; Android 8.0.0; SM-G955U Build/R16NW) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/116.0.0.0 Mobile Safari/537.36",
        "Referer": "https://www.douyin.com/?is_from_mobile_home=1&recommend=1",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }

    # 下载视频时用的 header
    DOWNLOAD_HEADERS = {
        "User-Agent": "Mozilla/5.0 (Linux; Android 8.0.0; SM-G955U Build/R16NW) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/116.0.0.0 Mobile Safari/537.36",
        "Referer": "https://www.douyin.com/",
    }

    @property
    def platform_info(self) -> PlatformInfo:
        return PlatformInfo(key="douyin", name="Douyin", name_cn="抖音")

    # ---- URL 提取 ----

    @staticmethod
    def extract_urls(text: str) -> list:
        """从消息文本中提取所有抖音链接"""
        urls = []
        # v.douyin.com 短链
        for m in re.finditer(r'https?://v\.douyin\.com/[^\s]+', text):
            urls.append(m.group(0))
        # douyin.com/video/{id} 标准链接
        for m in re.finditer(r'https?://(?:www\.)?douyin\.com/video/(\d{15,20})', text):
            urls.append(m.group(0))
        # iesdouyin.com 分享链接
        for m in re.finditer(r'https?://(?:www\.)?iesdouyin\.com/share/video/(\d{15,20})', text):
            urls.append(m.group(0))
        return urls

    def _extract_video_id(self, url: str) -> str:
        for pat in [r'/video/(\d+)', r'/note/(\d+)', r'share/video/(\d+)']:
            m = re.search(pat, url)
            if m:
                return m.group(1)
        return ""

    # ---- _ROUTER_DATA 提取 ----

    @staticmethod
    def _extract_router_data(html: str) -> str | None:
        """用括号计数法精确提取 window._ROUTER_DATA 的 JSON 字符串"""
        marker = "window._ROUTER_DATA = "
        start = html.find(marker)
        if start == -1:
            return None
        brace_start = html.find("{", start)
        if brace_start == -1:
            return None
        stack = []
        i = brace_start
        while i < len(html):
            if html[i] == "{":
                stack.append("{")
            elif html[i] == "}":
                stack.pop()
                if not stack:
                    return html[brace_start:i + 1]
            i += 1
        return None

    # ---- 分享页解析 ----

    async def _fetch_from_share_page(self, client: httpx.AsyncClient, video_id: str) -> dict:
        """从 iesdouyin.com 分享页提取视频信息"""
        url = f"https://www.iesdouyin.com/share/video/{video_id}/"
        logger.info(f"[抖音] 请求分享页: {url}")

        try:
            resp = await client.get(url, headers=self.MOBILE_HEADERS)
            html = resp.text

            json_str = self._extract_router_data(html)
            if not json_str:
                logger.info("[抖音] 未找到 _ROUTER_DATA")
                return {}

            # 处理转义
            json_str = json_str.replace("\\u002F", "/").replace("\\/", "/")

            try:
                data = json.loads(json_str)
            except json.JSONDecodeError as e:
                logger.info(f"[抖音] _ROUTER_DATA JSON 解析失败: {e}")
                # 尝试截取部分重新解析
                data = None

            if not data:
                return {}

            if not isinstance(data, dict):
                logger.info(f"[抖音] data 不是 dict, type={type(data).__name__}")
                return {}

            # 输出顶层 keys 帮助调试
            top_keys = list(data.keys())[:20]
            logger.info(f"[抖音] data 顶层 keys: {top_keys}")

            # 遍历 loaderData，找到包含 videoInfoRes 的项
            loader = data.get("loaderData")
            if not isinstance(loader, dict):
                logger.info(f"[抖音] loaderData 类型异常: {type(loader).__name__}, keys={top_keys}")
                # 尝试其他可能的顶层 key
                for alt_key in ["serverRouter", "default", "video_info", "aweme"]:
                    loader = data.get(alt_key, {})
                    if isinstance(loader, dict):
                        break
                if not isinstance(loader, dict):
                    return {}

            video_info_res = None
            # 递归查找 videoInfoRes
            stack_items = [(k, v) for k, v in loader.items()]
            while stack_items:
                k, v = stack_items.pop()
                if isinstance(v, dict):
                    if "videoInfoRes" in v:
                        video_info_res = v["videoInfoRes"]
                        break
                    if "item_list" in v:
                        video_info_res = v
                        break
                    stack_items.extend(v.items())

            if not video_info_res:
                logger.info("[抖音] loaderData 中未找到 videoInfoRes/item_list")
                return {}

            item_list = video_info_res.get("item_list")
            if not isinstance(item_list, list) or not item_list:
                logger.info("[抖音] videoInfoRes.item_list 为空或非 list")
                return {}

            item = item_list[0]
            if not isinstance(item, dict):
                logger.info(f"[抖音] item 不是 dict: {type(item).__name__}")
                return {}

            video_data = item.get("video")
            if not isinstance(video_data, dict):
                video_data = {}

            # 提取字段
            title = item.get("desc", "抖音视频")
            author = item.get("author", {}).get("nickname", "")
            create_time = item.get("create_time", 0)
            if create_time:
                create_time_str = datetime.fromtimestamp(create_time).strftime("%Y-%m-%d")
            else:
                create_time_str = ""

            # 封面
            cover_list = video_data.get("cover", {}).get("url_list", [])
            cover_url = cover_list[0] if cover_list else ""

            # 视频地址：从 play_addr.uri 构造
            play_addr = video_data.get("play_addr", {})
            play_uri = play_addr.get("uri", "")
            if play_uri:
                if play_uri.endswith(".mp3"):
                    video_url = play_uri
                elif play_uri.startswith("https://"):
                    video_url = play_uri
                else:
                    video_url = f"https://www.douyin.com/aweme/v1/play/?video_id={play_uri}"
            else:
                # 兜底: 直接用 url_list
                url_list = play_addr.get("url_list", [])
                video_url = url_list[0] if url_list else ""

            # 无水印地址
            download_addr = video_data.get("download_addr", {})
            dw_list = download_addr.get("url_list", [])
            if dw_list:
                video_url = dw_list[0]  # 无水印链接优先

            # 图集
            images = [img.get("url_list", [""])[0]
                      for img in (item.get("images") or []) if img and img.get("url_list")]

            info = {
                "title": title,
                "author": author,
                "create_time": create_time_str,
                "cover_url": cover_url,
                "video_url": video_url,
                "images": images,
                "is_gallery": len(images) > 0,
            }
            logger.info(f"[抖音] 分享页解析成功: title={title[:30]}, video_url={'有' if video_url else '无'}, images={len(images)}张")
            return info

        except Exception as e:
            import traceback
            tb_lines = traceback.format_exc().splitlines()
            for line in tb_lines[-5:]:
                logger.info(f"[抖音] TB: {line}")
            logger.info(f"[抖音] 分享页解析异常: {e}")
            return {}

    # ---- 短链解析 ----

    async def _resolve_short_url(self, client: httpx.AsyncClient, url: str) -> str:
        """通过 HEAD 请求重定向解析 v.douyin.com 短链"""
        try:
            resp = await client.head(url, headers=self.MOBILE_HEADERS)
            final = str(resp.url)
            vid = self._extract_video_id(final)
            if vid:
                return vid
        except Exception:
            pass

        # HEAD 失败尝试 GET
        try:
            resp = await client.get(url, headers=self.MOBILE_HEADERS, follow_redirects=False)
            if resp.status_code in (301, 302):
                loc = resp.headers.get("Location", "")
                vid = self._extract_video_id(loc)
                if vid:
                    return vid
        except Exception:
            pass

        return ""

    # ---- 主入口 ----

    async def parse(self, url: str) -> ParserResult:
        result = ParserResult(success=False, platform="douyin", raw_url=url)

        try:
            clean_url = re.sub(r'\?.*$', '', url).strip()
            video_id = self._extract_video_id(clean_url)

            async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=True) as client:
                # 短链 → 解析出 video_id
                if not video_id and "v.douyin.com" in clean_url:
                    video_id = await self._resolve_short_url(client, clean_url)

                if not video_id:
                    result.error = "无法提取视频ID"
                    return result

                logger.info(f"[抖音] video_id={video_id}")

                # 从分享页解析
                info = await self._fetch_from_share_page(client, video_id)

                if not info.get("title") and not info.get("video_url"):
                    # 分享页失败，尝试第三方 API（如果配置了）
                    api_template = self.config.get("douyin_api_url", "")
                    if api_template:
                        try:
                            api_url = api_template.replace("{url}", url)
                            api_resp = await client.get(api_url, headers={
                                "User-Agent": self.MOBILE_HEADERS["User-Agent"],
                                "Accept": "application/json",
                            })
                            data = api_resp.json()
                            d = data.get("data", data)
                            info["title"] = d.get("title", d.get("desc", ""))
                            info["author"] = d.get("author", d.get("nickname", ""))
                            info["video_url"] = d.get("video_url", d.get("url", ""))
                        except Exception:
                            pass

                if not info.get("title") and not info.get("video_url"):
                    result.error = "未能提取视频信息"
                    return result

                result.success = True
                result.title = info.get("title", "抖音视频")
                result.author = info.get("author", "")
                result.cover_url = info.get("cover_url", "")
                result.video_url = info.get("video_url", "")
                result.image_urls = info.get("images", [])
                result.extra = {"is_gallery": info.get("is_gallery", False)}
                result.download_headers = self.DOWNLOAD_HEADERS

        except httpx.TimeoutException:
            result.error = "请求超时"
        except Exception as e:
            result.error = f"解析异常: {str(e)}"

        return result
