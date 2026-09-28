import asyncio
import os
import logging
from typing import Dict, List, Optional
from pyrogram import Client, filters
from pyrogram.types import Message

from .config import load_config
from .client import DnsTunnelClient

logger = logging.getLogger("KurigramTracker")

class TelegramTracker:
    def __init__(self, dns_client: DnsTunnelClient):
        self.dns_client = dns_client
        self.client: Optional[Client] = None
        self.is_running = False
        self._task = None

    async def start(self):
        if self.is_running:
            return
        cfg = load_config()
        tg_cfg = cfg.get("kurigram_tracker", {})
        if not tg_cfg.get("enabled", False):
            return

        api_id = tg_cfg.get("api_id")
        api_hash = tg_cfg.get("api_hash")
        session_str = tg_cfg.get("session_string", "")
        if not api_id or not api_hash:
            logger.warning("Telegram Tracker: Missing api_id or api_hash")
            return

        channels_map = tg_cfg.get("channels_map", [])
        if not channels_map:
            logger.info("Telegram Tracker: No channels configured in map")
            return

        # Initialize Kurigram client
        session_name = session_str if session_str else "dns_messenger_selfbot"
        self.client = Client(
            session_name,
            api_id=api_id,
            api_hash=api_hash,
            session_string=session_str if session_str else None,
            workdir=str(self.dns_client.config.get("data_dir", "data"))
        )

        @self.client.on_message(~filters.me)
        async def on_new_channel_post(bot_client: Client, message: Message):
            try:
                chat_username = f"@{message.chat.username}".lower() if message.chat.username else ""
                chat_id = str(message.chat.id)

                for mapping in channels_map:
                    target_tg = str(mapping.get("tg_channel", "")).strip().lower()
                    if target_tg in (chat_username, chat_id):
                        m_chan = mapping.get("messenger_channel")
                        m_pass = mapping.get("password")
                        if not m_chan or not m_pass:
                            continue

                        # Extract text or caption
                        text = message.text or message.caption or ""
                        sender_label = f"📢 Telegram [{message.chat.title or target_tg}]"
                        full_text = f"{sender_label}\n\n{text}".strip()

                        # Check media
                        file_name = ""
                        file_bytes = b""
                        max_bytes = self.dns_client.max_file_size_mb * 1024 * 1024

                        if message.document and message.document.file_size <= max_bytes:
                            file_name = message.document.file_name or "document"
                            dl_path = await message.download()
                            if dl_path and os.path.exists(dl_path):
                                with open(dl_path, "rb") as f:
                                    file_bytes = f.read()
                                os.remove(dl_path)
                        elif message.photo:
                            file_name = "photo.jpg"
                            dl_path = await message.download()
                            if dl_path and os.path.exists(dl_path):
                                with open(dl_path, "rb") as f:
                                    file_bytes = f.read()
                                os.remove(dl_path)

                        # Join channel locally and send over DNS
                        self.dns_client.join_channel(m_chan, m_pass)
                        await self.dns_client.send_message(
                            chat_type="channel",
                            target_name=m_chan,
                            text=full_text,
                            file_name=file_name,
                            file_bytes=file_bytes
                        )
                        logger.info(f"Forwarded Telegram post from {target_tg} to channel '{m_chan}'")
            except Exception as e:
                logger.error(f"Error handling Telegram message: {e}")

        try:
            await self.client.start()
            self.is_running = True
            logger.info("Kurigram Telegram Self-Bot Tracker started successfully.")
        except Exception as e:
            logger.error(f"Failed to start Kurigram client: {e}")
            self.is_running = False

    async def stop(self):
        if self.client and self.is_running:
            try:
                await self.client.stop()
            except Exception:
                pass
            self.is_running = False
