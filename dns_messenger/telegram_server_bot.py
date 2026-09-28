import asyncio
import os
import logging
from typing import Dict, List, Optional
from pyrogram import Client, filters
from pyrogram.errors import SessionPasswordNeeded, PhoneCodeInvalid, PasswordHashInvalid
from pyrogram.types import Message

from .config import load_config, save_config, DATA_DIR
from .crypto import get_target_tag, pack_message, encrypt_payload
from .server import GLOBAL_MEMORY_STORE

logger = logging.getLogger("TelegramServerBot")

class TelegramServerBot:
    def __init__(self):
        self.client: Optional[Client] = None
        self.is_connected = False
        self.is_running = False
        self.pending_auth = {
            "api_id": None,
            "api_hash": None,
            "phone_number": None,
            "phone_code_hash": None,
            "temp_client": None
        }

    async def start_from_config(self):
        cfg = load_config()
        tg_cfg = cfg.get("kurigram_tracker", {})
        if not tg_cfg.get("enabled", False):
            return

        api_id = tg_cfg.get("api_id")
        api_hash = tg_cfg.get("api_hash")
        session_str = tg_cfg.get("session_string", "")
        if not api_id or not api_hash or not session_str:
            logger.info("Telegram Server Bot: Inactive (not logged in)")
            return

        try:
            self.client = Client(
                "server_tg_session",
                api_id=api_id,
                api_hash=api_hash,
                session_string=session_str,
                in_memory=True
            )
            self._register_handlers(cfg)
            await self.client.start()
            self.is_connected = True
            self.is_running = True
            logger.info("Telegram Server Bot: Successfully connected and tracking channels.")
        except Exception as e:
            logger.error(f"Telegram Server Bot start error: {e}")
            self.is_connected = False

    def _register_handlers(self, cfg: dict):
        tg_cfg = cfg.get("kurigram_tracker", {})
        channels_map = tg_cfg.get("channels_map", [])

        @self.client.on_message(~filters.me)
        async def on_post(bot: Client, message: Message):
            chat_user = f"@{message.chat.username}".lower() if message.chat.username else ""
            chat_id = str(message.chat.id)

            for item in channels_map:
                target_tg = str(item.get("tg_channel", "")).strip().lower()
                if target_tg in (chat_user, chat_id):
                    m_chan = item.get("messenger_channel", "general").strip().lower()
                    m_pass = item.get("password", "").strip() or None  # None = Public unencrypted

                    text = message.text or message.caption or ""
                    sender_title = f"📢 Telegram [{message.chat.title or target_tg}]"
                    full_text = f"{sender_title}\n\n{text}".strip()

                    file_name = ""
                    file_bytes = b""
                    max_bytes = cfg.get("max_file_size_mb", 20) * 1024 * 1024

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

                    is_public = (m_pass is None)
                    packed = pack_message(
                        sender=sender_title,
                        text=full_text,
                        file_name=file_name,
                        file_bytes=file_bytes,
                        is_public=is_public
                    )
                    encrypted_data = encrypt_payload(packed, password=m_pass)

                    # Inject directly into server's In-Memory RAM buffer
                    tag = get_target_tag(m_chan, prefix="chan")
                    new_id = GLOBAL_MEMORY_STORE.get_next_msg_id(tag)
                    GLOBAL_MEMORY_STORE.save_message(tag, new_id, encrypted_data)
                    logger.info(f"Telegram Server Bot: Forwarded post from {target_tg} to '{m_chan}' (Public: {is_public})")

    # Interactive Authentication API
    async def request_code(self, api_id: int, api_hash: str, phone_number: str) -> dict:
        if self.client and self.is_running:
            await self.client.stop()

        temp_client = Client(
            "temp_auth_session",
            api_id=api_id,
            api_hash=api_hash,
            in_memory=True
        )
        await temp_client.connect()
        try:
            sent_code = await temp_client.send_code(phone_number)
            self.pending_auth = {
                "api_id": api_id,
                "api_hash": api_hash,
                "phone_number": phone_number,
                "phone_code_hash": sent_code.phone_code_hash,
                "temp_client": temp_client
            }
            return {"ok": True, "message": "Verification code sent to Telegram app / SMS"}
        except Exception as e:
            await temp_client.disconnect()
            return {"ok": False, "error": str(e)}

    async def submit_code(self, code: str) -> dict:
        temp_client: Client = self.pending_auth.get("temp_client")
        if not temp_client:
            return {"ok": False, "error": "No active authentication session. Request code first."}

        try:
            await temp_client.sign_in(
                self.pending_auth["phone_number"],
                self.pending_auth["phone_code_hash"],
                code
            )
            return await self._finalize_login(temp_client)
        except SessionPasswordNeeded:
            return {"ok": True, "need_2fa": True, "message": "Two-factor authentication (2FA) required"}
        except PhoneCodeInvalid:
            return {"ok": False, "error": "Invalid verification code"}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    async def submit_2fa(self, password: str) -> dict:
        temp_client: Client = self.pending_auth.get("temp_client")
        if not temp_client:
            return {"ok": False, "error": "No active authentication session"}
        try:
            await temp_client.check_password(password)
            return await self._finalize_login(temp_client)
        except PasswordHashInvalid:
            return {"ok": False, "error": "Invalid 2FA password"}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    async def _finalize_login(self, temp_client: Client) -> dict:
        session_str = await temp_client.export_session_string()
        await temp_client.disconnect()

        cfg = load_config()
        if "kurigram_tracker" not in cfg:
            cfg["kurigram_tracker"] = {}
        cfg["kurigram_tracker"]["enabled"] = True
        cfg["kurigram_tracker"]["api_id"] = self.pending_auth["api_id"]
        cfg["kurigram_tracker"]["api_hash"] = self.pending_auth["api_hash"]
        cfg["kurigram_tracker"]["session_string"] = session_str
        save_config(cfg)

        self.pending_auth = {}
        # Start bot with new credentials
        await self.start_from_config()
        return {"ok": True, "message": "Successfully logged in and activated Kurigram Bot!"}

    async def stop(self):
        if self.client and self.is_running:
            try:
                await self.client.stop()
            except Exception:
                pass
            self.is_running = False
            self.is_connected = False

GLOBAL_TELEGRAM_BOT = TelegramServerBot()
