"""小红书视频/图文解析器 — 基于 __INITIAL_STATE__ 提取"""
import re
import json
import httpx
from astrbot.api import logger
from .base import BaseParser, ParserResult, PlatformInfo


class XiaohongshuParser(BaseParser):
    """小红书内容解析 - 支持视频和图文笔记"""

    HEADERS = {
        "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) "
                       "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 Mobile/15E148 Safari/604.1",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Referer": "https://www.xiaohongshu.com/",
    }

    def __init__(self, config: dict):
        super().__init__(config)
        self.cookies = config.get("xiaohongshu_cookies", "")
        self._req_headers = dict(self.HEADERS)
        if self.cookies:
            self._req_headers["Cookie"] = self.cookies

    @property
    def platform_info(self) -> PlatformInfo:
        return PlatformInfo(
            key="xiaohongshu",
            name="Xiaohongshu",
            name_cn="小红书",
        )

    async def _resolve_short_link(self, client: httpx.AsyncClient, url: str) -> str:
        """解析小红书短链接 xhslink.com → 重定向后的完整 URL"""
        try:
            resp = await client.head(url, headers=self._req_headers, follow_redirects=False)
            loc = resp.headers.get("location", "")
            if resp.status_code in (301, 302, 307, 308) and loc:
                if "xhslink.com" not in loc:
                    logger.info(f"[小红书] 短链重定向: {loc[:120]}")
                    return loc
        except Exception:
            pass

        try:
            resp2 = await client.get(url, headers=self._req_headers, follow_redirects=True)
            final_url = str(resp2.url)
            if "xiaohongshu.com" in final_url and "xhslink.com" not in final_url:
                return final_url
        except Exception as e:
            logger.info(f"[小红书] 短链 GET 失败: {e}")

        return url

    def _extract_note_id(self, url: str) -> str:
        """从小红书链接中提取笔记ID"""
        for pattern in [r'/explore/([a-zA-Z0-9]+)', r'/discovery/item/([a-zA-Z0-9]+)', r'/a/([a-zA-Z0-9]+)']:
            match = re.search(pattern, url)
            if match:
                return match.group(1)
        return ""

    def _extract_initial_state(self, html: str) -> dict:
        """括号计数法提取 window.__INITIAL_STATE__ 数据"""
        marker = "window.__INITIAL_STATE__="
        idx = html.find(marker)
        if idx == -1:
            return {}

        idx += len(marker)
        while idx < len(html) and html[idx] in ' \t\r\n':
            idx += 1

        if idx >= len(html) or html[idx] not in '{[':
            return {}

        closer = '}' if html[idx] == '{' else ']'
        start = idx
        depth = 0
        in_string = False
        escape = False

        for i in range(start, len(html)):
            ch = html[i]
            if escape:
                escape = False
                continue
            if ch == '\\':
                escape = True
                continue
            if ch == '"':
                in_string = not in_string
                continue
            if in_string:
                continue
            if ch in '{[':
                depth += 1
            elif ch in '}]':
                depth -= 1
                if depth == 0:
                    json_str = html[start:i + 1]
                    json_str = json_str.replace("undefined", "null")
                    try:
                        return json.loads(json_str)
                    except json.JSONDecodeError as e:
                        logger.info(f"[小红书] __INITIAL_STATE__ JSON 解析失败: {e}")
                        return {}
        return {}

    def _extract_from_html(self, html: str) -> dict:
        """从小红书页面 HTML 提取内容"""
        info = {}
        try:
            data = self._extract_initial_state(html)
            if not data:
                return self._extract_from_meta(html)

            if not isinstance(data, dict):
                logger.info(f"[小红书] __INITIAL_STATE__ 不是 dict, type={type(data).__name__}")
                return self._extract_from_meta(html)

            top_keys = list(data.keys())[:20]
            logger.info(f"[小红书] __INITIAL_STATE__ 顶层 keys: {top_keys}")

            # 小红书的 __INITIAL_STATE__ 结构:
            # 路径1: note.noteDetailMap.{noteId}.note
            # 路径2: note.noteDetail.{noteId}.note
            # 路径3: noteData.noteDetailMap.{noteId}.note (新版结构)
            # 路径4: noteData 直接为笔记数据 (新版简化结构)
            note_data = None

            note_root = data.get("note", {})
            if not isinstance(note_root, dict):
                note_root = {}
            
            # 路径1/2: 从 note 下取
            note_map = note_root.get("noteDetailMap", {})
            if isinstance(note_map, dict) and note_map:
                for nid, nd in note_map.items():
                    if isinstance(nd, dict):
                        note_data = nd.get("note", {})
                    break

            if not note_data:
                note_detail = note_root.get("noteDetail", {})
                if isinstance(note_detail, dict) and note_detail:
                    for nid, nd in note_detail.items():
                        note_data = nd if isinstance(nd, dict) else {}
                        break

            # 路径3/4: 从 noteData 下取 (新版)
            if not note_data:
                note_data_root = data.get("noteData", {})
                if isinstance(note_data_root, dict):
                    nd_keys = list(note_data_root.keys())[:20]
                    logger.info(f"[小红书] noteData keys: {nd_keys}")
                    
                    # 路径3a: noteData.data (新版最外层数据)
                    inner_data = note_data_root.get("data")
                    if isinstance(inner_data, dict):
                        inner_keys = list(inner_data.keys())[:15]
                        logger.info(f"[小红书] noteData.data keys: {inner_keys}")
                        # data 下可能有多层
                        for sub_key in ("noteDetail", "noteDetailMap", "note", "notes", "noteList", "firstNote", "noteData"):
                            sub = inner_data.get(sub_key)
                            if isinstance(sub, dict):
                                if sub.get("noteId") or sub.get("title"):
                                    note_data = sub
                                    break
                                # 可能又是 {noteId: {...}} 结构
                                if any(isinstance(v, dict) and (v.get("noteId") or v.get("title")) for v in sub.values()):
                                    note_data = list(sub.values())[0]
                                    break
                            elif isinstance(sub, list) and sub:
                                first = sub[0]
                                if isinstance(first, dict):
                                    note_data = first.get("note", first)
                                    break
                        if not note_data and isinstance(inner_data, dict):
                            if inner_data.get("noteId") or inner_data.get("title"):
                                note_data = inner_data

                    # 路径3b: noteData.normalNotePreloadData
                    if not note_data:
                        preload = note_data_root.get("normalNotePreloadData") or note_data_root.get("errorNoteData")
                        if isinstance(preload, dict):
                            pre_keys = list(preload.keys())[:10]
                            logger.info(f"[小红书] normalNotePreloadData keys: {pre_keys}")
                            # preload 自身可能是扁平 note（新版：title/desc 直接在顶层）
                            if preload.get("title") or preload.get("noteId"):
                                logger.info("[小红书] preload 自身就是扁平 note，直接使用")
                                note_data = preload
                            # 查 userNotes 数组
                            if not note_data:
                                user_notes = preload.get("userNotes", [])
                                logger.info(f"[小红书] userNotes type={type(user_notes).__name__}, len={len(user_notes) if isinstance(user_notes, list) else 'N/A'}")
                                if isinstance(user_notes, list) and user_notes:
                                    first_note = user_notes[0]
                                    if isinstance(first_note, dict):
                                        fk = list(first_note.keys())[:15]
                                        logger.info(f"[小红书] userNotes[0] keys: {fk}")
                                        if "noteId" in first_note or "title" in first_note:
                                            note_data = first_note
                                        elif "note" in first_note:
                                            note_data = first_note["note"]
                            # 查 noteData
                            if not note_data:
                                nd = preload.get("noteData", {})
                                logger.info(f"[小红书] preload.noteData type={type(nd).__name__}")
                                if isinstance(nd, dict) and nd:
                                    nd_sub_keys = list(nd.keys())[:15]
                                    logger.info(f"[小红书] preload.noteData keys: {nd_sub_keys}")
                                    if nd.get("noteId") or nd.get("title"):
                                        note_data = nd
                                    else:
                                        for nid, nv in nd.items():
                                            if isinstance(nv, dict):
                                                note_data = nv.get("note", nv)
                                            break
                            # 直接遍历（跳过 imagesList/desc 等非 note 键）
                            if not note_data:
                                logger.info("[小红书] 进入直接遍历 preload...")
                                skip_keys = {"imagesList", "desc", "title", "userNotes", "noteData",
                                             "showLaunchAppModal", "show", "routeQuery", "launchAppModalParams"}
                                for nid, nd_val in preload.items():
                                    if nid in skip_keys:
                                        continue
                                    logger.info(f"[小红书] 遍历 preload[{nid}] type={type(nd_val).__name__}")
                                    if isinstance(nd_val, dict):
                                        vk = list(nd_val.keys())[:10]
                                        logger.info(f"[小红书] preload[{nid}] keys: {vk}")
                                        if nd_val.get("noteId") or nd_val.get("title"):
                                            note_data = nd_val
                                            break
                                        nv = nd_val.get("note", {})
                                        if isinstance(nv, dict) and (nv.get("noteId") or nv.get("title")):
                                            note_data = nv
                                            break

                    # 路径3c: noteDetailMap 老结构
                    if not note_data:
                        nd_map = note_data_root.get("noteDetailMap", {})
                        if isinstance(nd_map, dict) and nd_map:
                            for nid, nd in nd_map.items():
                                if isinstance(nd, dict):
                                    note_data = nd.get("note", {})
                                break
                        
                    # 路径3d: 自身就是 note
                    if not note_data and isinstance(note_data_root, dict):
                        if note_data_root.get("noteId") or note_data_root.get("title"):
                            note_data = note_data_root

            if not note_data:
                logger.info("[小红书] 未找到 note_data")
                return self._extract_from_meta(html)

            note_type = note_data.get("type", "")
            logger.info(f"[小红书] 笔记类型: {note_type}")

            info["title"] = note_data.get("title", "")
            info["desc"] = note_data.get("desc", "")

            author = note_data.get("user", {})
            info["author"] = (author.get("nickname") or author.get("name", ""))

            # 图片
            image_list = note_data.get("imageList") or note_data.get("image_list") or note_data.get("imagesList") or []
            if image_list:
                info["image_urls"] = []
                for img in image_list:
                    if not isinstance(img, dict):
                        continue
                    img_url = img.get("urlDefault", "") or img.get("url", "") or img.get("original", "")
                    if img_url and not img_url.startswith("http"):
                        img_url = "https://ci.xiaohongshu.com/" + img_url.lstrip("/")
                    if img_url:
                        info["image_urls"].append(img_url)
                info["cover_url"] = info["image_urls"][0] if info["image_urls"] else ""

            # 视频
            video = note_data.get("video") if isinstance(note_data.get("video"), dict) else {}
            if video:
                media = video.get("media", {})
                stream = media.get("stream", {}) if isinstance(media, dict) else {}

                for quality in ["h264", "h265", "h264Avc", "av1"]:
                    stream_data = stream.get(quality, [])
                    if not isinstance(stream_data, list) or not stream_data:
                        continue
                    for s in stream_data:
                        if not isinstance(s, dict):
                            continue
                        master_url = s.get("masterUrl", "")
                        if master_url:
                            info["video_url"] = master_url
                            break
                    if info.get("video_url"):
                        break

                # 备用: 直接字段
                if not info.get("video_url"):
                    for k in ("masterUrl", "url", "h264", "streamUrl"):
                        val = video.get(k, "")
                        if val:
                            info["video_url"] = val
                            break

                cover = (video.get("image") or {}).get("urlDefault", "") or (video.get("image") or {}).get("url", "")
                if cover:
                    info["cover_url"] = cover

            logger.info(
                f"[小红书] title={info.get('title')[:30] if info.get('title') else 'None'}, "
                f"author={info.get('author')}, "
                f"video={bool(info.get('video_url'))}, "
                f"images={len(info.get('image_urls') or [])}"
            )

        except Exception as e:
            logger.info(f"[小红书] HTML 解析异常: {e}")
            return self._extract_from_meta(html)

        return info

    def _extract_from_meta(self, html: str) -> dict:
        """降级：从 og meta 标签提取"""
        info = {}
        for prop, key in [("og:title", "title"), ("og:description", "desc"), ("og:image", "cover_url")]:
            m = re.search(rf'<meta[^>]+(?:property|name)="{prop}"[^>]+content="([^"]*)"', html, re.I)
            if m:
                info[key] = m.group(1)
        return info

    async def parse(self, url: str) -> ParserResult:
        result = ParserResult(
            success=False,
            platform="xiaohongshu",
            raw_url=url,
        )

        try:
            note_id = self._extract_note_id(url)
            logger.info(f"[小红书] note_id={note_id or '未提取到'}")

            async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=True) as client:
                if "xhslink.com" in url:
                    url = await self._resolve_short_link(client, url)

                logger.info(f"[小红书] 请求: {url[:120]}")
                resp = await client.get(url, headers=self._req_headers)
                logger.info(f"[小红书] 响应: status={resp.status_code}, len={len(resp.text)}")

                info = self._extract_from_html(resp.text)

                result.success = True
                result.title = info.get("title") or "小红书笔记"
                result.author = info.get("author") or ""
                result.desc = info.get("desc", "")
                result.cover_url = info.get("cover_url", "")
                result.video_url = info.get("video_url", "")
                result.image_urls = info.get("image_urls") or []
                result.download_headers = {
                    "User-Agent": self.HEADERS["User-Agent"],
                    "Referer": "https://www.xiaohongshu.com/",
                }
                if self.cookies:
                    result.download_headers["Cookie"] = self.cookies

                if result.video_url:
                    logger.info(f"[小红书] 获取到 video_url")
                elif result.image_urls:
                    logger.info(f"[小红书] 获取到 {len(result.image_urls)} 张图片")

                if not any([result.image_urls, result.video_url, result.author]):
                    result.error = "未提取到有效内容（可能需要登录或 cookie）"

        except httpx.TimeoutException:
            result.error = "请求超时"
        except Exception as e:
            result.error = f"解析异常: {str(e)}"
            logger.info(f"[小红书] parse 异常: {e}")

        return result
