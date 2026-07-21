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
        # douyin.com/note/{id} 图文链接
        for m in re.finditer(r'https?://(?:www\.)?douyin\.com/note/(\d{15,20})', text):
            urls.append(m.group(0))
        # iesdouyin.com 分享链接
        for m in re.finditer(r'https?://(?:www\.)?iesdouyin\.com/share/video/(\d{15,20})', text):
            urls.append(m.group(0))
        # iesdouyin.com 图文分享链接
        for m in re.finditer(r'https?://(?:www\.)?iesdouyin\.com/share/note/(\d{15,20})', text):
            urls.append(m.group(0))
        return urls

    def _extract_video_id(self, url: str) -> str:
        for pat in [r'/video/(\d+)', r'/note/(\d+)', r'share/video/(\d+)', r'share/note/(\d+)']:
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

    async def _fetch_from_share_page(self, client: httpx.AsyncClient, video_id: str, is_note: bool = False) -> dict:
        """从 iesdouyin.com 分享页提取视频/图文信息（统一用 /share/video/ 端点）"""
        url = f"https://www.iesdouyin.com/share/video/{video_id}/"
        logger.info(f"[抖音] 请求分享页 (is_note={is_note}): {url}")

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
                for alt_key in ["serverRouter", "default", "video_info", "aweme"]:
                    loader = data.get(alt_key, {})
                    if isinstance(loader, dict):
                        break
                if not isinstance(loader, dict):
                    return {}

            # 检查顶层 errors
            errors = data.get("errors")
            if errors:
                logger.info(f"[抖音] 分享页返回 errors: {json.dumps(errors, ensure_ascii=False)[:200]}")

            video_info_res = None
            stack_items = [(k, v) for k, v in loader.items()]
            while stack_items:
                k, v = stack_items.pop()
                if isinstance(v, dict):
                    if "videoInfoRes" in v:
                        video_info_res = v["videoInfoRes"]
                        logger.info(f"[抖音] 找到 videoInfoRes, keys={list(video_info_res.keys())[:15] if isinstance(video_info_res, dict) else type(video_info_res).__name__}")
                        break
                    if "item_list" in v:
                        video_info_res = v
                        break
                    stack_items.extend(v.items())

            # 如果 videoInfoRes 找到了但 item_list 无效，尝试宽泛搜索
            item_list = None
            if isinstance(video_info_res, dict):
                item_list = video_info_res.get("item_list")

            if not isinstance(item_list, list) or not item_list:
                # 宽泛搜索：在整个 loaderData 中找包含有效 item 的列表
                stack_items = [(k, v) for k, v in loader.items()]
                while stack_items:
                    k, v = stack_items.pop()
                    if isinstance(v, list) and v and isinstance(v[0], dict):
                        # 优先找有 desc/video/images 字段的 item 列表
                        if any(isinstance(d, dict) and (d.get("desc") or d.get("video") or d.get("images")) for d in v):
                            video_info_res = {"item_list": v}
                            item_list = v
                            logger.info(f"[抖音] 宽泛搜索找到 item_list (key={k}, len={len(v)})")
                            break
                    if isinstance(v, dict):
                        stack_items.extend(v.items())

            if not isinstance(item_list, list) or not item_list:
                logger.info("[抖音] loaderData 中未找到有效的 videoInfoRes/item_list")
                return {}

            item = item_list[0]
            if not isinstance(item, dict):
                logger.info(f"[抖音] item 不是 dict: {type(item).__name__}")
                return {}

            # 提取字段
            title = item.get("desc", "")
            author = item.get("author", {}).get("nickname", "")
            create_time = item.get("create_time", 0)
            if create_time:
                create_time_str = datetime.fromtimestamp(create_time).strftime("%Y-%m-%d")
            else:
                create_time_str = ""

            # 图集（图文和视频都可能有多张图）
            images = [img.get("url_list", [""])[0]
                      for img in (item.get("images") or []) if img and img.get("url_list")]

            video_data = item.get("video")
            has_video = isinstance(video_data, dict) and bool(video_data.get("play_addr"))

            # 数据自检：有图无视频 → 强制按图文处理
            if images and not has_video:
                if not is_note:
                    logger.info(f"[抖音] 数据自检：有{len(images)}张图但无视频，改为图文模式")
                is_note = True

            # 默认标题
            if not title:
                title = "抖音图文" if is_note else "抖音视频"

            video_url = ""
            cover_url = ""

            if not is_note:
                if not isinstance(video_data, dict):
                    video_data = {}

                cover_list = video_data.get("cover", {}).get("url_list", [])
                cover_url = cover_list[0] if cover_list else ""

                # 优先使用带 token 的 CDN 直链，避免 aweme/v1/play API 的 404
                play_addr = video_data.get("play_addr", {})
                url_list = play_addr.get("url_list", [])
                if url_list:
                    video_url = url_list[0]  # CDN 直链，带 token，可直接下载
                else:
                    play_uri = play_addr.get("uri", "")
                    if play_uri:
                        if play_uri.endswith(".mp3"):
                            video_url = play_uri
                        elif play_uri.startswith("https://"):
                            video_url = play_uri
                        else:
                            video_url = f"https://www.douyin.com/aweme/v1/play/?video_id={play_uri}"

                # download_addr 的 CDN 直链品质最高，覆盖前面的结果
                download_addr = video_data.get("download_addr", {})
                dw_list = download_addr.get("url_list", [])
                if dw_list:
                    video_url = dw_list[0]

                logger.info(f"[抖音] video_url 前80字符: {video_url[:80] if video_url else '(空)'}")
            else:
                # 图文模式：封面取第一张图
                if images:
                    cover_url = images[0]

            info = {
                "title": title,
                "author": author,
                "create_time": create_time_str,
                "cover_url": cover_url,
                "video_url": video_url,
                "images": images,
                "is_gallery": len(images) > 0,
                "is_note": is_note,
            }
            logger.info(f"[抖音] 解析成功: title={title[:30]}, note={is_note}, "
                       f"video={'有' if video_url else '无'}, images={len(images)}张")
            return info

        except Exception as e:
            import traceback
            tb_lines = traceback.format_exc().splitlines()
            for line in tb_lines[-5:]:
                logger.info(f"[抖音] TB: {line}")
            logger.info(f"[抖音] 分享页解析异常: {e}")
            return {}

    # ---- 短链解析 ----

    async def _resolve_short_url(self, client: httpx.AsyncClient, url: str) -> tuple[str, bool]:
        """通过 HEAD 请求重定向解析 v.douyin.com 短链，返回 (video_id, is_note)"""
        final_url = ""
        try:
            resp = await client.head(url, headers=self.MOBILE_HEADERS)
            final_url = str(resp.url)
        except Exception:
            pass

        # HEAD 失败尝试 GET
        if not final_url:
            try:
                resp = await client.get(url, headers=self.MOBILE_HEADERS, follow_redirects=False)
                if resp.status_code in (301, 302):
                    final_url = resp.headers.get("Location", "")
            except Exception:
                pass

        if final_url:
            vid = self._extract_video_id(final_url)
            is_note = "/note/" in final_url
            if vid:
                return vid, is_note
        return "", False

    # ---- 主入口 ----

    async def parse(self, url: str) -> ParserResult:
        result = ParserResult(success=False, platform="douyin", raw_url=url)

        try:
            clean_url = re.sub(r'\?.*$', '', url).strip()
            video_id = self._extract_video_id(clean_url)
            is_note = "/note/" in clean_url

            async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=True) as client:
                # 短链 → 解析出 video_id 和 is_note
                if not video_id and "v.douyin.com" in clean_url:
                    video_id, is_note = await self._resolve_short_url(client, clean_url)

                if not video_id:
                    result.error = "无法提取视频ID"
                    return result

                logger.info(f"[抖音] video_id={video_id}, is_note={is_note}")

                # 从分享页解析
                info = await self._fetch_from_share_page(client, video_id, is_note=is_note)

                # 图文：有标题且有图就算成功
                if is_note:
                    title_ok = bool(info.get("title"))
                    images_ok = bool(info.get("images"))
                    if not title_ok and not images_ok:
                        result.error = "未能提取图文信息"
                        return result

                if not info.get("title") and not info.get("video_url") and not is_note:
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
                result.title = info.get("title", "抖音视频" if not is_note else "抖音图文")
                result.author = info.get("author", "")
                result.cover_url = info.get("cover_url", "")
                result.video_url = info.get("video_url", "")
                result.image_urls = info.get("images", [])
                result.extra = {
                    "is_gallery": info.get("is_gallery", False),
                    "is_note": is_note,
                }
                result.download_headers = self.DOWNLOAD_HEADERS

        except httpx.TimeoutException:
            result.error = "请求超时"
        except Exception as e:
            result.error = f"解析异常: {str(e)}"

        return result
