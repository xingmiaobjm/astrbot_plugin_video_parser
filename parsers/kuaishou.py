"""快手视频解析器 — 第三方API + 官方接口双路"""
import re
import json
import httpx
from .base import BaseParser, ParserResult, PlatformInfo

try:
    from astrbot.api import logger
except ImportError:
    import logging
    logger = logging.getLogger("kuaishou_parser")


class KuaishouParser(BaseParser):
    """快手视频解析 — 优先第三方API，兜底官方接口"""

    MOBILE_HEADERS = {
        "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) "
                       "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9",
    }

    @property
    def platform_info(self) -> PlatformInfo:
        return PlatformInfo(key="kuaishou", name="Kuaishou", name_cn="快手")

    # ---- 第三方 API ----

    async def _parse_via_api(self, client: httpx.AsyncClient, original_url: str) -> dict:
        api_template = self.config.get("kuaishou_api_url", "")
        if not api_template:
            return {}

        api_url = api_template.replace("{url}", original_url)
        logger.info(f"[快手] 调用第三方API: {api_url[:100]}...")

        try:
            resp = await client.get(api_url, headers={
                "User-Agent": self.MOBILE_HEADERS["User-Agent"],
                "Accept": "application/json",
            })
            if resp.status_code != 200:
                return {}

            data = resp.json()

            info = {}
            if "data" in data and isinstance(data["data"], dict):
                d = data["data"]
                info["title"] = d.get("title", d.get("desc", ""))
                info["author"] = d.get("author", d.get("nickname", ""))
                info["video_url"] = d.get("video_url", d.get("url", ""))
                info["cover_url"] = d.get("cover_url", d.get("cover", ""))
            else:
                info["title"] = data.get("title", data.get("desc", ""))
                info["author"] = data.get("author", data.get("nickname", ""))
                info["video_url"] = data.get("video_url", data.get("url", ""))

            if info.get("title") or info.get("video_url"):
                logger.info(f"[快手] 第三方API成功: {info.get('title','')[:30]}")
                return info
        except Exception as e:
            logger.info(f"[快手] 第三方API异常: {e}")

        return {}

    # ---- 官方接口 ----

    async def _resolve_short_link(self, client: httpx.AsyncClient, url: str) -> str:
        try:
            resp = await client.get(url, headers=self.MOBILE_HEADERS)
            final_url = str(resp.url)
            if "kuaishou.com" in final_url:
                return final_url
            m = re.search(r'https?://(?:www\.)?kuaishou\.com/\S+?(?=["\'\\s])', resp.text)
            if m:
                return m.group(0)
            return url
        except Exception:
            return url

    def _extract_from_html(self, html: str) -> dict:
        info = {}
        try:
            # __APOLLO_STATE__
            state = re.search(r'window\.__APOLLO_STATE__\s*=\s*({.+?});', html, re.DOTALL)
            if state:
                try:
                    data = json.loads(state.group(1))
                    for key, value in data.items():
                        if isinstance(value, dict) and ("photoId" in value or "photo_id" in str(value).lower()):
                            info["title"] = value.get("caption", value.get("shareTitle", ""))
                            author = value.get("userName", value.get("authorName", ""))
                            info["author"] = author
                            info["cover_url"] = value.get("coverUrl", value.get("poster", ""))
                            info["video_url"] = value.get("photoUrl", value.get("srcNoMark", "")).replace("\\u002F", "/")
                            break
                except Exception:
                    pass

            # og meta
            if not info.get("title"):
                m = re.search(r'<meta[^>]+property="og:title"[^>]+content="([^"]+)"', html)
                if m:
                    info["title"] = m.group(1)
            if not info.get("cover_url"):
                m = re.search(r'<meta[^>]+property="og:image"[^>]+content="([^"]+)"', html)
                if m:
                    info["cover_url"] = m.group(1)

            # video URL patterns
            for pat in [r'"srcNoMark":"([^"]+)"', r'"photoUrl":"([^"]+)"', r'"playUrl":"([^"]+)"']:
                m = re.search(pat, html)
                if m:
                    info["video_url"] = m.group(1).replace("\\u002F", "/")
                    break
        except Exception:
            pass
        return info

    # ---- 主入口 ----

    async def parse(self, url: str) -> ParserResult:
        result = ParserResult(success=False, platform="kuaishou", raw_url=url)

        try:
            async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=True) as client:
                info = {}

                # 优先: 第三方 API
                info = await self._parse_via_api(client, url)

                if not (info.get("title") or info.get("video_url")):
                    # 兜底: 官方页面解析
                    if "v.kuaishou.com" in url:
                        url = await self._resolve_short_link(client, url)
                    resp = await client.get(url, headers=self.MOBILE_HEADERS)
                    info = self._extract_from_html(resp.text)

                video_url = info.get("video_url", "")
                if not info.get("title") and not video_url:
                    api_configured = bool(self.config.get("kuaishou_api_url", ""))
                    hint = ""
                    if not api_configured:
                        hint = "（提示: 建议在WebUI配置 kuaishou_api_url 使用第三方解析API）"
                    result.error = "未能提取视频信息" + hint
                    return result

                result.success = True
                result.title = info.get("title", "快手视频")
                result.author = info.get("author", "")
                result.cover_url = info.get("cover_url", "")
                result.video_url = video_url
                result.download_headers = {
                    "User-Agent": self.MOBILE_HEADERS["User-Agent"],
                    "Referer": "https://www.kuaishou.com/",
                }

        except httpx.TimeoutException:
            result.error = "请求超时"
        except Exception as e:
            result.error = f"解析异常: {str(e)}"

        return result
