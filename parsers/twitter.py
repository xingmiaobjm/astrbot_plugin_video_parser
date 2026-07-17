"""X(Twitter) 视频/图片解析器"""
import re
import httpx
from .base import BaseParser, ParserResult, PlatformInfo


class TwitterParser(BaseParser):
    """X(Twitter) 内容解析 - 支持视频和图片推文"""

    HEADERS = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9,zh-CN;q=0.8",
    }

    @property
    def platform_info(self) -> PlatformInfo:
        return PlatformInfo(
            key="twitter",
            name="X(Twitter)",
            name_cn="X",
        )

    def _get_headers(self) -> dict:
        """获取请求头，包含 Cookie（如果配置了）"""
        headers = self.HEADERS.copy()
        cookies = self.config.get("twitter_cookies", "")
        if cookies:
            headers["Cookie"] = cookies
        return headers

    def _extract_tweet_id(self, url: str) -> str:
        """提取推文 ID"""
        match = re.search(r'/status/(\d+)', url)
        return match.group(1) if match else ""

    def _extract_from_html(self, html: str, tweet_id: str) -> dict:
        """从 Twitter 页面提取内容信息"""
        info = {}

        try:
            # 从 meta 标签提取
            title_match = re.search(r'<meta[^>]+property="og:title"[^>]+content="([^"]+)"', html)
            if title_match:
                info["title"] = title_match.group(1)

            desc_match = re.search(r'<meta[^>]+property="og:description"[^>]+content="([^"]+)"', html)
            if desc_match:
                info["desc"] = desc_match.group(1)

            # 提取作者
            author_match = re.search(r'<meta[^>]+name="twitter:creator"[^>]+content="@(\w+)"', html)
            if author_match:
                info["author"] = f"@{author_match.group(1)}"

            # 提取视频 URL
            video_match = re.search(r'<meta[^>]+property="og:video:url"[^>]+content="(.+?)"', html)
            if video_match:
                info["video_url"] = video_match.group(1)
            else:
                # 尝试从 video 标签提取
                video_match = re.search(r'<video[^>]+src="(.+?)"', html)
                if video_match:
                    info["video_url"] = video_match.group(1)

            # 提取视频类型
            if info.get("video_url"):
                video_type_match = re.search(r'<meta[^>]+property="og:video:type"[^>]+content="(.+?)"', html)
                if video_type_match:
                    info["video_type"] = video_type_match.group(1)

            # 图片 URL
            img_match = re.search(r'<meta[^>]+property="og:image"[^>]+content="(.+?)"', html)
            if img_match:
                info["cover_url"] = img_match.group(1)
                info["image_urls"] = [img_match.group(1)]

            # 尝试提取所有媒体图片
            img_urls = re.findall(r'https://pbs\.twimg\.com/media/[^"\s?]+(?:\?format=\w+&name=\w+)?', html)
            if img_urls:
                seen = set()
                unique_urls = []
                for u in img_urls:
                    if u not in seen:
                        seen.add(u)
                        unique_urls.append(u)
                info["image_urls"] = unique_urls

        except Exception:
            pass

        return info

    async def parse(self, url: str) -> ParserResult:
        result = ParserResult(
            success=False,
            platform="twitter",
            raw_url=url,
        )

        try:
            tweet_id = self._extract_tweet_id(url)
            if not tweet_id:
                result.error = "无法提取推文ID"
                return result

            # 使用 fx.twitter.com 来提高兼容性
            # 或者使用 twitter.com 的 embed 端点
            embed_url = f"https://platform.twitter.com/embed/Tweet.html?id={tweet_id}"

            async with httpx.AsyncClient(timeout=self.timeout) as client:
                headers = self._get_headers()
                resp = await client.get(url, headers=headers)

                # 如果是登录/验证页面
                if resp.status_code in (302, 303):
                    result.error = "需要登录X账号，请在配置中设置Cookie"
                    return result

                info = self._extract_from_html(resp.text, tweet_id)

                result.success = True
                result.title = info.get("title", "X 推文")
                result.author = info.get("author", "")
                result.desc = info.get("desc", "")
                result.cover_url = info.get("cover_url", "")
                result.video_url = info.get("video_url", "")
                result.image_urls = info.get("image_urls", [])
                result.download_headers = {
                    "User-Agent": self.HEADERS["User-Agent"],
                    "Referer": "https://x.com/",
                }
                cookies = self.config.get("twitter_cookies", "")
                if cookies:
                    result.download_headers["Cookie"] = cookies

        except httpx.TimeoutException:
            result.error = "请求超时"
        except Exception as e:
            result.error = f"解析异常: {str(e)}"

        return result
