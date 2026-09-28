import asyncio
import socket
import sqlite3
import time
import math
from pathlib import Path
from typing import Dict, Tuple, Optional
import dns.message
import dns.query
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
)

CHUNK_PULL_SIZE = 380  # Max safe bytes in TXT record response without IP fragmentation

class MessageStore:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._init_db()

    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
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

    def get_next_msg_id(self, target_tag: str) -> int:
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT MAX(msg_id) FROM messages WHERE target_tag = ?", (target_tag,))
            row = cursor.fetchone()
            return (row[0] or 0) + 1

    def save_message(self, target_tag: str, msg_id: int, encrypted_data: bytes):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT INTO messages (target_tag, msg_id, timestamp, encrypted_data) VALUES (?, ?, ?, ?)",
                (target_tag, msg_id, time.time(), encrypted_data)
            )

    def get_messages_after(self, target_tag: str, last_seen_id: int, limit: int = 1):
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT msg_id, encrypted_data FROM messages WHERE target_tag = ? AND msg_id > ? ORDER BY msg_id ASC LIMIT ?",
                (target_tag, last_seen_id, limit)
            )
            return cursor.fetchall()

    def get_message(self, target_tag: str, msg_id: int) -> Optional[bytes]:
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT encrypted_data FROM messages WHERE target_tag = ? AND msg_id = ?",
                (target_tag, msg_id)
            )
            row = cursor.fetchone()
            return row[0] if row else None


class DnsTunnelServerProtocol(asyncio.DatagramProtocol):
    def __init__(self, config: dict):
        self.config = config
        self.base_domain = config.get("base_domain", "msg.example.com").lower()
        self.max_file_size = config.get("max_file_size_mb", 20) * 1024 * 1024
        self.forward_upstream = config.get("forward_dns_upstream", "")
        self.transport = None
        self.store = MessageStore(DATA_DIR / "server_messages.db")
        # In-flight incoming chunks: {(target_tag, msg_id): {chunk_idx: bytes, 'total': int, 'time': float}}
        self.incoming_assembly: Dict[Tuple[str, int], dict] = {}

    def connection_made(self, transport):
        self.transport = transport

    def datagram_received(self, data: bytes, addr: Tuple[str, int]):
        asyncio.create_task(self.handle_datagram(data, addr))

    async def handle_datagram(self, data: bytes, addr: Tuple[str, int]):
        try:
            query = dns.message.from_wire(data)
        except Exception:
            return

        if not query.question:
            return

        qname = str(query.question[0].name).strip(".").lower()

        # Check if query is for our tunnel domain
        if not qname.endswith(self.base_domain):
            # Transparent forward to MasterDNS / Upstream DNS if configured
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
        # Clean stale incoming assemblies older than 10 minutes
        now = time.time()
        stale = [k for k, v in self.incoming_assembly.items() if now - v["time"] > 600]
        for k in stale:
            del self.incoming_assembly[k]

        if pkt.pkt_type == PKT_PING:
            return DnsPacket(PKT_PONG, session_id=pkt.session_id, target_tag=pkt.target_tag)

        elif pkt.pkt_type == PKT_PUSH_CHUNK:
            # Check maximum size
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

            # If all chunks arrived, assemble and store message
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
            # pkt.msg_id is client's last seen msg_id
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
    cfg = load_config()
    host = cfg.get("server_listen_host", "0.0.0.0")
    port = cfg.get("server_listen_port", 5354)
    loop = asyncio.get_running_loop()
    transport, protocol = await loop.create_datagram_endpoint(
        lambda: DnsTunnelServerProtocol(cfg),
        local_addr=(host, port)
    )
    print(f"[DNS Messenger Server] Listening on {host}:{port}")
    print(f"[DNS Messenger Server] Base domain: {cfg.get('base_domain')}")
    if cfg.get("forward_dns_upstream"):
        print(f"[DNS Messenger Server] Upstream fallback (MasterDNS co-exist): {cfg.get('forward_dns_upstream')}")
    try:
        while True:
            await asyncio.sleep(3600)
    finally:
        transport.close()

if __name__ == "__main__":
    asyncio.run(run_server())
