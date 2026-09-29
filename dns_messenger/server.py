import asyncio
import socket
import sqlite3
import time
import math
from pathlib import Path
from typing import Dict, Tuple, Optional, List
import dns.message
import dns.query
from aiohttp import web
from .config import load_config, DATA_DIR
from .dns_proto import (
    DnsPacket,
    domain_to_bytes,
    build_txt_response,
    PKT_PUSH_CHUNK,
    PKT_PUSH_ACK,
    PKT_POLL_META,
    PKT_META_RESP,
    PKT_PULL_CHUNK,
    PKT_CHUNK_RESP,
    PKT_PING,
    PKT_PONG,
    PKT_DISCOVER_CHANNELS,
    PKT_CHANNELS_RESP,
    PKT_PULL_MEDIA,
    PKT_MEDIA_RESP,
)

CHUNK_PULL_SIZE = 380

class DiskMessageStore:
    """
    Disk-backed SQLite Store with 100-message history limit per channel.
    Messages and media are stored on disk in data/server_storage.db and automatically
    pruned to keep the last 100 messages per channel/tag.
    """
    def __init__(self, db_path: Path, history_limit: int = 100):
        self.db_path = db_path
        self.history_limit = history_limit
        self.public_channels: set = set()
        self._init_db()
        self.stats = {
            "start_time": time.time(),
            "total_queries": 0,
            "total_messages_received": 0,
            "total_messages_delivered": 0,
            "total_bytes_transferred": 0,
            "active_tags_count": 0
        }

    def _init_db(self):
        self.db_path.parent.mkdir(exist_ok=True)
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    target_tag TEXT NOT NULL,
                    msg_id INTEGER NOT NULL,
                    timestamp REAL NOT NULL,
                    encrypted_data BLOB NOT NULL
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_tag_msg ON messages(target_tag, msg_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_msg_time ON messages(timestamp)")

            conn.execute("""
                CREATE TABLE IF NOT EXISTS media (
                    target_tag TEXT NOT NULL,
                    msg_id INTEGER NOT NULL,
                    timestamp REAL NOT NULL,
                    media_data BLOB NOT NULL,
                    PRIMARY KEY (target_tag, msg_id)
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_media_time ON media(timestamp)")

            conn.execute("""
                CREATE TABLE IF NOT EXISTS public_channels (
                    channel_name TEXT PRIMARY KEY,
                    updated_at REAL NOT NULL
                )
            """)
            cur = conn.cursor()
            cur.execute("SELECT channel_name FROM public_channels")
            for row in cur.fetchall():
                self.public_channels.add(row[0])

        self.sync_from_config()

    def sync_from_config(self, cfg: Optional[dict] = None):
        """Sync public channels from config.json into database and memory."""
        try:
            if cfg is None:
                cfg = load_config()
            tg_map = cfg.get("kurigram_tracker", {}).get("channels_map", [])
            with sqlite3.connect(self.db_path) as conn:
                for item in tg_map:
                    if not item.get("password"):
                        cname = str(item.get("messenger_channel", "")).strip().lower()
                        if cname:
                            self.public_channels.add(cname)
                            conn.execute(
                                "INSERT OR REPLACE INTO public_channels (channel_name, updated_at) VALUES (?, ?)",
                                (cname, time.time())
                            )
        except Exception:
            pass

    def _purge_limit(self, target_tag: str):
        """Retain only the newest self.history_limit messages for this target_tag."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                DELETE FROM messages
                WHERE target_tag = ?
                  AND id NOT IN (
                      SELECT id FROM messages
                      WHERE target_tag = ?
                      ORDER BY msg_id DESC
                      LIMIT ?
                  )
            """, (target_tag, target_tag, self.history_limit))

            # Prune orphaned media records
            conn.execute("""
                DELETE FROM media
                WHERE (target_tag, msg_id) NOT IN (
                    SELECT target_tag, msg_id FROM messages
                )
            """)

            cur = conn.cursor()
            cur.execute("SELECT COUNT(DISTINCT target_tag) FROM messages")
            row = cur.fetchone()
            self.stats["active_tags_count"] = row[0] if row else 0

    def register_public_channel(self, channel_name: str):
        cname = channel_name.strip().lower()
        if not cname:
            return
        self.public_channels.add(cname)
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO public_channels (channel_name, updated_at) VALUES (?, ?)",
                (cname, time.time())
            )

    def get_public_channels(self) -> List[str]:
        self.sync_from_config()
        return sorted(list(self.public_channels))

    def get_next_msg_id(self, target_tag: str) -> int:
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.cursor()
            cur.execute("SELECT MAX(msg_id) FROM messages WHERE target_tag = ?", (target_tag,))
            row = cur.fetchone()
            return (row[0] or 0) + 1

    def save_message(self, target_tag: str, msg_id: int, data: bytes):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT INTO messages (target_tag, msg_id, timestamp, encrypted_data) VALUES (?, ?, ?, ?)",
                (target_tag, msg_id, time.time(), data)
            )
        self._purge_limit(target_tag)
        self.stats["total_messages_received"] += 1
        self.stats["total_bytes_transferred"] += len(data)

    def save_media(self, target_tag: str, msg_id: int, data: bytes):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO media (target_tag, msg_id, timestamp, media_data) VALUES (?, ?, ?, ?)",
                (target_tag, msg_id, time.time(), data)
            )
        self.stats["total_bytes_transferred"] += len(data)

    def get_media(self, target_tag: str, msg_id: int) -> Optional[bytes]:
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.cursor()
            cur.execute("SELECT media_data FROM media WHERE target_tag = ? AND msg_id = ?", (target_tag, msg_id))
            row = cur.fetchone()
            return row[0] if row else None

    def get_messages_after(self, target_tag: str, last_seen_id: int, limit: int = 1) -> List[Tuple[int, bytes]]:
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT msg_id, encrypted_data FROM messages WHERE target_tag = ? AND msg_id > ? ORDER BY msg_id ASC LIMIT ?",
                (target_tag, last_seen_id, limit)
            )
            return cur.fetchall()

    def get_message(self, target_tag: str, msg_id: int) -> Optional[bytes]:
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT encrypted_data FROM messages WHERE target_tag = ? AND msg_id = ?",
                (target_tag, msg_id)
            )
            row = cur.fetchone()
            if row:
                self.stats["total_messages_delivered"] += 1
                return row[0]
            return None

# Global disk store for server runtime
GLOBAL_DISK_STORE = DiskMessageStore(
    DATA_DIR / "server_storage.db",
    history_limit=int(load_config().get("channel_history_limit", 100))
)

class DnsTunnelServerProtocol(asyncio.DatagramProtocol):
    def __init__(self, config: dict, store: Optional[DiskMessageStore] = None):
        self.config = config
        self.base_domain = config.get("base_domain", "msg.example.com").lower()
        self.max_file_size = config.get("max_file_size_mb", 20) * 1024 * 1024
        self.forward_upstream = config.get("forward_dns_upstream", "")
        self.transport = None
        self.store = store or GLOBAL_DISK_STORE
        self.incoming_assembly: Dict[Tuple[str, int], dict] = {}

    def connection_made(self, transport):
        self.transport = transport

    def datagram_received(self, data: bytes, addr: Tuple[str, int]):
        asyncio.create_task(self.handle_datagram(data, addr))

    async def handle_datagram(self, data: bytes, addr: Tuple[str, int]):
        self.store.stats["total_queries"] += 1
        try:
            query = dns.message.from_wire(data)
        except Exception:
            return

        if not query.question:
            return

        qname = str(query.question[0].name).strip(".").lower()

        # Check if query is for our tunnel domain
        if not qname.endswith(self.base_domain):
            if self.forward_upstream:
                await self.forward_to_upstream(data, addr)
            return

        raw_packet_bytes = domain_to_bytes(qname, self.base_domain)
        if not raw_packet_bytes:
            return

        pkt = DnsPacket.deserialize(raw_packet_bytes)
        if not pkt:
            return

        resp_pkt = self.process_tunnel_packet(pkt)
        if resp_pkt:
            resp_wire = build_txt_response(query, resp_pkt.serialize())
            self.transport.sendto(resp_wire.to_wire(), addr)

    def process_tunnel_packet(self, pkt: DnsPacket) -> Optional[DnsPacket]:
        now = time.time()
        stale = [k for k, v in self.incoming_assembly.items() if now - v["time"] > 600]
        for k in stale:
            del self.incoming_assembly[k]

        if pkt.pkt_type == PKT_PING:
            return DnsPacket(PKT_PONG, session_id=pkt.session_id, target_tag=pkt.target_tag)

        elif pkt.pkt_type == PKT_PUSH_CHUNK:
            if pkt.total_chunks * len(pkt.payload) > self.max_file_size:
                return None

            key = (pkt.target_tag, pkt.msg_id)
            if key not in self.incoming_assembly:
                self.incoming_assembly[key] = {
                    "chunks": {},
                    "total": pkt.total_chunks,
                    "time": now
                }

            assembly = self.incoming_assembly[key]
            assembly["chunks"][pkt.chunk_idx] = pkt.payload
            assembly["time"] = now

            if len(assembly["chunks"]) == assembly["total"]:
                full_bytes = b"".join(assembly["chunks"][i] for i in range(assembly["total"]))
                new_id = self.store.get_next_msg_id(pkt.target_tag)
                self.store.save_message(pkt.target_tag, new_id, full_bytes)
                del self.incoming_assembly[key]
                return DnsPacket(
                    PKT_PUSH_ACK,
                    session_id=pkt.session_id,
                    target_tag=pkt.target_tag,
                    msg_id=new_id,
                    chunk_idx=pkt.chunk_idx,
                    total_chunks=pkt.total_chunks
                )

            return DnsPacket(
                PKT_PUSH_ACK,
                session_id=pkt.session_id,
                target_tag=pkt.target_tag,
                msg_id=pkt.msg_id,
                chunk_idx=pkt.chunk_idx,
                total_chunks=pkt.total_chunks
            )

        elif pkt.pkt_type == PKT_POLL_META:
            rows = self.store.get_messages_after(pkt.target_tag, pkt.msg_id, limit=1)
            if not rows:
                return DnsPacket(
                    PKT_META_RESP,
                    session_id=pkt.session_id,
                    target_tag=pkt.target_tag,
                    msg_id=0,
                    chunk_idx=0,
                    total_chunks=0
                )
            next_msg_id, enc_data = rows[0]
            total_chunks = max(1, math.ceil(len(enc_data) / CHUNK_PULL_SIZE))
            return DnsPacket(
                PKT_META_RESP,
                session_id=pkt.session_id,
                target_tag=pkt.target_tag,
                msg_id=next_msg_id,
                chunk_idx=0,
                total_chunks=total_chunks,
                payload=str(len(enc_data)).encode("ascii")
            )

        elif pkt.pkt_type == PKT_PULL_CHUNK:
            enc_data = self.store.get_message(pkt.target_tag, pkt.msg_id)
            if not enc_data:
                return None
            total_chunks = max(1, math.ceil(len(enc_data) / CHUNK_PULL_SIZE))
            start = pkt.chunk_idx * CHUNK_PULL_SIZE
            chunk_data = enc_data[start:start + CHUNK_PULL_SIZE]
            return DnsPacket(
                PKT_CHUNK_RESP,
                session_id=pkt.session_id,
                target_tag=pkt.target_tag,
                msg_id=pkt.msg_id,
                chunk_idx=pkt.chunk_idx,
                total_chunks=total_chunks,
                payload=chunk_data
            )

        elif pkt.pkt_type == PKT_DISCOVER_CHANNELS:
            import json
            chans = list(self.store.public_channels)
            return DnsPacket(
                PKT_CHANNELS_RESP,
                session_id=pkt.session_id,
                payload=json.dumps(chans).encode("utf-8")
            )

        elif pkt.pkt_type == PKT_PULL_MEDIA:
            media_data = self.store.get_media(pkt.target_tag, pkt.msg_id)
            if not media_data:
                return None
            total_chunks = max(1, math.ceil(len(media_data) / CHUNK_PULL_SIZE))
            start = pkt.chunk_idx * CHUNK_PULL_SIZE
            chunk_data = media_data[start:start + CHUNK_PULL_SIZE]
            return DnsPacket(
                PKT_MEDIA_RESP,
                session_id=pkt.session_id,
                target_tag=pkt.target_tag,
                msg_id=pkt.msg_id,
                chunk_idx=pkt.chunk_idx,
                total_chunks=total_chunks,
                payload=chunk_data
            )

        return None

    async def forward_to_upstream(self, raw_data: bytes, client_addr: Tuple[str, int]):
        if not self.forward_upstream:
            return
        parts = self.forward_upstream.split(":")
        up_host = parts[0]
        up_port = int(parts[1]) if len(parts) > 1 else 53
        loop = asyncio.get_running_loop()

        def _forward():
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                    s.settimeout(2.0)
                    s.sendto(raw_data, (up_host, up_port))
                    resp, _ = s.recvfrom(4096)
                    return resp
            except Exception:
                return None

        resp_data = await loop.run_in_executor(None, _forward)
        if resp_data and self.transport:
            self.transport.sendto(resp_data, client_addr)


async def run_server():
    from .server_admin_ui import ServerAdminApp
    from .telegram_server_bot import GLOBAL_TELEGRAM_BOT

    cfg = load_config()
    host = cfg.get("server_listen_host", "0.0.0.0")
    port = cfg.get("server_listen_port", 5354)
    loop = asyncio.get_running_loop()

    # 1. Start DNS Tunnel UDP Server
    transport, protocol = await loop.create_datagram_endpoint(
        lambda: DnsTunnelServerProtocol(cfg, GLOBAL_DISK_STORE),
        local_addr=(host, port)
    )

    # 2. Start Server Web Admin Dashboard
    admin_app = ServerAdminApp()
    admin_runner = web.AppRunner(admin_app.app)
    await admin_runner.setup()
    admin_host = cfg.get("server_admin_host", "0.0.0.0")
    admin_port = cfg.get("server_admin_port", 8081)
    admin_site = web.TCPSite(admin_runner, admin_host, admin_port)
    await admin_site.start()

    # 3. Start Telegram Kurigram Server Bot if configured
    asyncio.create_task(GLOBAL_TELEGRAM_BOT.start_from_config())

    print("\n=======================================================")
    print(f"DNS Messenger Server active on UDP {host}:{port}")
    print(f"Server Web Admin Panel: http://{admin_host}:{admin_port}")
    print(f"Base Domain: {cfg.get('base_domain')}")
    print(f"Storage Architecture: Disk-backed SQLite (data/server_storage.db with 100-message channel history)")
    print("=======================================================\n")

    try:
        while True:
            await asyncio.sleep(3600)
    finally:
        await GLOBAL_TELEGRAM_BOT.stop()
        await admin_runner.cleanup()
        transport.close()

if __name__ == "__main__":
    asyncio.run(run_server())
