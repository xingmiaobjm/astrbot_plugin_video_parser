"""B站视频解析器"""
import re
import httpx
from .base import BaseParser, ParserResult, PlatformInfo

# 尝试使用 AstrBot logger，回退到标准 logging
try:
    from astrbot.api import logger
except ImportError:
    import logging
    logger = logging.getLogger("bilibili_parser")


class BilibiliParser(BaseParser):
    """B站视频解析 - 使用 B站公开 API"""

    VIDEO_INFO_API = "https://api.bilibili.com/x/web-interface/view"
    VIDEO_PLAYURL_API = "https://api.bilibili.com/x/player/playurl"

    HEADERS = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
        "Referer": "https://www.bilibili.com/",
        "Origin": "https://www.bilibili.com",
    }

    VIDEO_DOWNLOAD_HEADERS = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
        "Referer": "https://www.bilibili.com/",
        "Origin": "https://www.bilibili.com",
        "Accept": "*/*",
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Range": "bytes=0-",  # 支持断点续传
    }

    @property
    def platform_info(self) -> PlatformInfo:
        return PlatformInfo(
            key="bilibili",
            name="Bilibili",
            name_cn="B站",
            icon_url="https://www.bilibili.com/favicon.ico",
        )

    def _extract_bv_av(self, url: str) -> tuple:
        """从 URL 提取 BV 号或 AV 号"""
        if "b23.tv" in url:
            return "b23", url
        bv_match = re.search(r'(?:BV|bv)([a-zA-Z0-9]{10})', url)
        if bv_match:
            return "bvid", f"BV{bv_match.group(1)}"
        av_match = re.search(r'av(\d+)', url, re.IGNORECASE)
        if av_match:
            return "aid", av_match.group(1)
        return None, None

    async def _resolve_b23(self, url: str) -> str:
        """解析 b23.tv 短链接"""
        try:
            async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=True) as client:
                resp = await client.get(url, headers=self.HEADERS)
                final_url = str(resp.url)
                bv_match = re.search(r'(?:BV|bv)([a-zA-Z0-9]{10})', final_url)
                if bv_match:
                    return f"BV{bv_match.group(1)}"
                bv_match2 = re.search(r'(?:BV|bv)([a-zA-Z0-9]{10})', resp.text)
                if bv_match2:
                    return f"BV{bv_match2.group(1)}"
                return ""
        except Exception:
            return ""

    def _format_duration(self, seconds: int) -> str:
        """格式化时长"""
        if seconds < 60:
            return f"{seconds}秒"
        m, s = divmod(seconds, 60)
        if m < 60:
            return f"{m}分{s}秒" if s else f"{m}分钟"
        h, m = divmod(m, 60)
        return f"{h}小时{m}分{s}秒" if m or s else f"{h}小时"

    def _format_count(self, count: int) -> str:
        """格式化数量"""
        if count >= 10000:
            return f"{count / 10000:.1f}万"
        return str(count)

    async def parse(self, url: str) -> ParserResult:
        result = ParserResult(
            success=False,
            platform="bilibili",
            raw_url=url,
        )

        try:
            bv_type, bv_value = self._extract_bv_av(url)
            if not bv_type:
                result.error = "无法从链接中提取视频ID"
                return result

            if bv_type == "b23":
                bvid = await self._resolve_b23(bv_value)
                if not bvid:
                    result.error = "短链接解析失败"
                    return result
                bv_type, bv_value = "bvid", bvid

            async with httpx.AsyncClient(timeout=self.timeout) as client:
                # 获取视频基本信息
                if bv_type == "bvid":
                    params = {"bvid": bv_value}
                else:
                    params = {"aid": bv_value}

                resp = await client.get(
                    self.VIDEO_INFO_API,
                    params=params,
                    headers=self.HEADERS,
                )
                info_data = resp.json()

                if info_data.get("code") != 0:
                    result.error = info_data.get("message", "获取视频信息失败")
                    return result

                video_info = info_data["data"]
                stat = video_info.get("stat", {})
                cid = video_info.get("cid", 0)       # 视频分P的cid，playurl必须
                aid = video_info.get("aid", 0)

                # 构建描述信息
                duration = video_info.get("duration", 0)
                desc_parts = []
                if duration:
                    desc_parts.append(f"时长: {self._format_duration(duration)}")
                if stat.get("view"):
                    desc_parts.append(f"播放: {self._format_count(stat['view'])}")
                if stat.get("like"):
                    desc_parts.append(f"点赞: {self._format_count(stat['like'])}")
                if stat.get("danmaku"):
                    desc_parts.append(f"弹幕: {stat['danmaku']}")
                if video_info.get("pubdate"):
                    import datetime
                    pubdate = datetime.datetime.fromtimestamp(video_info["pubdate"])
                    desc_parts.append(f"发布: {pubdate.strftime('%Y-%m-%d')}")

                extra_info = " | ".join(desc_parts)

                result.success = True
                result.title = video_info.get("title", "")
                result.author = video_info.get("owner", {}).get("name", "")
                result.cover_url = video_info.get("pic", "")
                result.desc = f"{video_info.get('desc', '')}\n{extra_info}"
                # 保存原始 B站链接，而不是 CDN 链接
                result.raw_url = f"https://www.bilibili.com/video/{bv_value}"

                # 获取视频流 URL
                play_params = {
                    "bvid": bv_value,
                    "cid": cid,
                    "qn": 80,
                    "fnval": 1,      # FLV 带音频
                    "fourk": 1,
                    "fnver": 0,
                    "platform": "web",
                } if bv_type == "bvid" else {
                    "aid": bv_value,
                    "cid": cid,
                    "qn": 80,
                    "fnval": 1,
                    "fourk": 1,
                    "fnver": 0,
                    "platform": "web",
                }
                try:
                    play_resp = await client.get(
                        self.VIDEO_PLAYURL_API,
                        params=play_params,
                        headers=self.HEADERS,
                    )
                    play_data = play_resp.json()
                    if play_data.get("code") == 0:
                        durl = play_data["data"].get("durl", [])
                        dash = play_data["data"].get("dash", {})
                        # 优先使用 durl（FLV 带音频），回退 dash
                        if durl:
                            result.video_url = durl[0].get("url", "")
                            result.download_headers = dict(self.VIDEO_DOWNLOAD_HEADERS)
                        elif dash.get("video"):
                            result.video_url = dash["video"][0].get("baseUrl", "")
                            result.download_headers = dict(self.VIDEO_DOWNLOAD_HEADERS)
                            if dash.get("audio"):
                                result.extra["audio_url"] = dash["audio"][0].get("baseUrl", "")
                        else:
                            logger.info(f"[B站解析] playurl成功但durl/dash均为空")
                    else:
                        logger.info(f"[B站解析] playurl返回错误: code={play_data.get('code')}, msg={play_data.get('message')}")
                except Exception as e:
                    logger.info(f"[B站解析] playurl请求异常: {e}")

                logger.info(f"[B站解析] 标题={result.title}, 作者={result.author}, "
                           f"video_url={'有' if result.video_url else '无'}, "
                           f"len={len(result.video_url) if result.video_url else 0}")

        except httpx.TimeoutException:
            result.error = "请求超时"
        except Exception as e:
            result.error = f"解析异常: {str(e)}"

        return result

    def format_result(self, result: ParserResult) -> str:
        """将B站解析结果格式化为文本消息（仅标题+作者）"""
        if not result.success:
            return f"[B站] 解析失败: {result.error}"

        lines = [
            f"标题: {result.title}",
            f"UP主: {result.author}",
        ]
        return "\n".join(lines)
