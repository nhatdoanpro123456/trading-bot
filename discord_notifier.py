import asyncio
import logging
import aiohttp

from config import DISCORD_WEBHOOK_URL, DISCORD_STATUS_INTERVAL_MINUTES

logger = logging.getLogger("DiscordNotifier")

DISCORD_MAX_LEN = 2000  # Giới hạn ký tự / tin nhắn của Discord


def _telegram_html_to_discord_md(text: str) -> str:
    """Chuyển định dạng HTML kiểu Telegram (<b>, <i>, <code>, <pre>) sang Markdown Discord."""
    replacements = [
        ("<pre>", "```\n"), ("</pre>", "\n```"),
        ("<b>", "**"), ("</b>", "**"),
        ("<i>", "*"), ("</i>", "*"),
        ("<code>", "`"), ("</code>", "`"),
        ("&lt;", "<"), ("&gt;", ">"), ("&amp;", "&"),
    ]
    for old, new in replacements:
        text = text.replace(old, new)
    return text


class DiscordNotifier:
    """
    Gửi cảnh báo qua Discord Webhook - thay thế TelegramNotifier khi kênh Telegram
    bị chặn. Giữ nguyên interface (session, send_message, start_polling_commands,
    close) để bot.py không cần đổi logic gọi.
    """

    def __init__(self):
        self.webhook_url = DISCORD_WEBHOOK_URL
        self.session: aiohttp.ClientSession | None = None
        self._owns_session = True
        if not self.webhook_url:
            logger.warning("⚠️ DISCORD_WEBHOOK_URL chưa được cấu hình trong .env!")

    async def _get_session(self) -> aiohttp.ClientSession:
        """Tái sử dụng session được bot.py gán vào (self.session), tự tạo nếu chưa có."""
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10))
            self._owns_session = True
        return self.session

    async def send_message(self, text: str):
        """Gửi 1 tin nhắn qua Webhook (tự tách nếu vượt quá 2000 ký tự)."""
        if not self.webhook_url:
            logger.error("Không thể gửi tin: chưa cấu hình DISCORD_WEBHOOK_URL trong .env")
            return

        content = _telegram_html_to_discord_md(text)
        session = await self._get_session()

        chunks = [content[i:i + DISCORD_MAX_LEN] for i in range(0, len(content), DISCORD_MAX_LEN)] or [""]

        for chunk in chunks:
            await self._post_with_retry(session, chunk)

    async def _post_with_retry(self, session: aiohttp.ClientSession, chunk: str, retried: bool = False):
        payload = {"content": chunk}
        try:
            async with session.post(self.webhook_url, json=payload) as resp:
                if resp.status == 429 and not retried:
                    data = await resp.json()
                    retry_after = float(data.get("retry_after", 1.0))
                    logger.warning(f"Discord rate-limit, chờ {retry_after:.1f}s rồi gửi lại...")
                    await asyncio.sleep(retry_after)
                    await self._post_with_retry(session, chunk, retried=True)
                elif resp.status not in (200, 204):
                    body = await resp.text()
                    logger.error(f"Gửi Discord thất bại (status {resp.status}): {body}")
        except Exception as e:
            logger.error(f"Lỗi kết nối tới Discord Webhook: {e}")

    async def start_polling_commands(self, dashboard_callback):
        """
        Discord Webhook chỉ GỬI được, KHÔNG nhận lệnh như /status của Telegram Bot.
        Thay thế: tự động gửi Dashboard định kỳ mỗi DISCORD_STATUS_INTERVAL_MINUTES phút
        (đặt = 0 trong .env để tắt hẳn tính năng này).

        Muốn có lệnh bấm /status theo yêu cầu thật sự trên Discord thì cần tạo hẳn
        1 Discord Bot (bot token + kết nối Gateway, dùng thư viện discord.py) - phức tạp
        hơn nhiều so với Webhook. Nói mình nếu bạn muốn nâng cấp lên hướng đó.
        """
        if DISCORD_STATUS_INTERVAL_MINUTES <= 0:
            logger.info("Đã tắt gửi Dashboard định kỳ qua Discord (DISCORD_STATUS_INTERVAL_MINUTES=0).")
            return

        while True:
            await asyncio.sleep(DISCORD_STATUS_INTERVAL_MINUTES * 60)
            try:
                dashboard_text = dashboard_callback()
                await self.send_message(dashboard_text)
            except Exception as e:
                logger.error(f"Lỗi khi gửi Dashboard định kỳ qua Discord: {e}")

    async def close(self):
        if self.session and not self.session.closed and self._owns_session:
            await self.session.close()
