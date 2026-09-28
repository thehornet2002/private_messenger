import asyncio
import time
import math
import random
import json
from pathlib import Path
from typing import List, Dict, Optional, Tuple
import dns.message
import dns.query
import dns.rdatatype

from .config import load_config, DATA_DIR, load_resolvers, save_resolvers
from .crypto import (
    get_target_tag,
    encrypt_payload,
    decrypt_payload,
    pack_message,
    unpack_message
)
from .dns_proto import (
    DnsPacket,
    bytes_to_domain,
    parse_txt_response,
    PKT_PUSH_CHUNK,
    PKT_PUSH_ACK,
    PKT_POLL_META,
    PKT_META_RESP,
    PKT_PULL_CHUNK,
    PKT_CHUNK_RESP,
    PKT_PING,
    PKT_PONG
)

CLIENT_STATE_FILE = DATA_DIR / "client_state.json"

class DnsTunnelClient:
    def __init__(self):
        self.config = load_config()
        self.base_domain = self.config.get("base_domain", "msg.example.com")
        self.resolvers: List[str] = load_resolvers()
        self.resolver_benchmarks: Dict[str, int] = {}
        self.active_resolver_idx = 0
        self.session_id = random.randint(1000, 65000)
        self.query_timeout = self.config.get("dns_query_timeout", 2.5)
        self.max_retries = self.config.get("max_retries", 3)
        self.max_file_size_mb = self.config.get("max_file_size_mb", 20)
        self.chunk_size = self.config.get("chunk_size_bytes", 110)
        
        # State: channels and direct chats
        self.state = self._load_state()
        self.stats = {
            "queries_sent": 0,
            "queries_success": 0,
            "queries_failed": 0,
            "current_resolver": self.get_current_resolver(),
            "last_latency_ms": 0,
            "is_polling": False
        }
        self.progress_callbacks = {}

    def _load_state(self) -> dict:
        if CLIENT_STATE_FILE.exists():
            try:
                with open(CLIENT_STATE_FILE, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return {"channels": {}, "direct": {}, "username": "User" + str(random.randint(100, 999))}

    def save_state(self):
        with open(CLIENT_STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(self.state, f, indent=2, ensure_ascii=False)

    def reload_resolvers(self):
        """Reload resolvers list from resolvers.txt."""
        self.resolvers = load_resolvers()
        self.active_resolver_idx = 0
        self.stats["current_resolver"] = self.get_current_resolver()

    def get_current_resolver(self) -> str:
        if not self.resolvers:
            return "8.8.8.8:53"
        return self.resolvers[self.active_resolver_idx % len(self.resolvers)]

    def rotate_resolver(self):
        if self.resolvers:
            self.active_resolver_idx = (self.active_resolver_idx + 1) % len(self.resolvers)
            self.stats["current_resolver"] = self.get_current_resolver()

    async def benchmark_resolvers(self) -> Dict[str, int]:
        """Test latency to all resolvers in parallel and sort by speed."""
        results = {}
        loop = asyncio.get_running_loop()

        async def _test_one(res_str: str):
            parts = res_str.split(":")
            ip = parts[0]
            port = int(parts[1]) if len(parts) > 1 else 53
            pkt = DnsPacket(PKT_PING, session_id=random.randint(100, 9999))
            wire_data = pkt.serialize()
            qname = bytes_to_domain(wire_data, self.base_domain)
            query = dns.message.make_query(qname, dns.rdatatype.TXT)
            query.id = random.randint(1, 65535)

            start = time.time()
            def _run():
                try:
                    return dns.query.udp(query, ip, port=port, timeout=1.8)
                except Exception:
                    return None

            resp = await loop.run_in_executor(None, _run)
            if resp and parse_txt_response(resp):
                lat = int((time.time() - start) * 1000)
                results[res_str] = lat
            else:
                results[res_str] = -1

        tasks = [_test_one(r) for r in self.resolvers]
        await asyncio.gather(*tasks, return_exceptions=True)

        self.resolver_benchmarks = results

        working = sorted([r for r, l in results.items() if l >= 0], key=lambda r: results[r])
        failed = [r for r, l in results.items() if l < 0]
        if working:
            self.resolvers = working + failed
            self.active_resolver_idx = 0
            self.stats["current_resolver"] = self.resolvers[0]

        return results

    async def send_dns_packet(self, pkt: DnsPacket) -> Optional[DnsPacket]:
        """Send a single DnsPacket over DNS TXT query, retrying and rotating resolvers on failure."""
        pkt.session_id = self.session_id
        wire_data = pkt.serialize()
        qname = bytes_to_domain(wire_data, self.base_domain)
        loop = asyncio.get_running_loop()

        for attempt in range(self.max_retries):
            # On retry with multiple resolvers, race top 2 resolvers in parallel
            if attempt > 0 and len(self.resolvers) > 1:
                res_candidates = [
                    self.get_current_resolver(),
                    self.resolvers[(self.active_resolver_idx + 1) % len(self.resolvers)]
                ]
                async def _try_res(r_str):
                    parts = r_str.split(":")
                    rip = parts[0]
                    rport = int(parts[1]) if len(parts) > 1 else 53
                    q = dns.message.make_query(qname, dns.rdatatype.TXT)
                    q.id = random.randint(1, 65535)
                    def _sync():
                        try:
                            return dns.query.udp(q, rip, port=rport, timeout=self.query_timeout)
                        except Exception:
                            return None
                    t0 = time.time()
                    resp = await loop.run_in_executor(None, _sync)
                    lat = int((time.time() - t0) * 1000)
                    if resp:
                        ext = parse_txt_response(resp)
                        if ext:
                            p = DnsPacket.deserialize(ext)
                            if p:
                                return p, lat, r_str
                    return None, 0, r_str

                tasks = [asyncio.create_task(_try_res(r)) for r in res_candidates]
                done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                for t in done:
                    p, lat, winning_r = t.result()
                    if p:
                        for pt in pending:
                            pt.cancel()
                        self.stats["queries_success"] += 1
                        self.stats["last_latency_ms"] = lat
                        self.stats["current_resolver"] = winning_r
                        return p
                for pt in pending:
                    try:
                        p, lat, winning_r = await pt
                        if p:
                            self.stats["queries_success"] += 1
                            self.stats["last_latency_ms"] = lat
                            self.stats["current_resolver"] = winning_r
                            return p
                    except Exception:
                        pass
                self.stats["queries_failed"] += 1
                self.rotate_resolver()
                await asyncio.sleep(0.2)
                continue

            resolver = self.get_current_resolver()
            parts = resolver.split(":")
            res_ip = parts[0]
            res_port = int(parts[1]) if len(parts) > 1 else 53

            query = dns.message.make_query(qname, dns.rdatatype.TXT)
            query.id = random.randint(1, 65535)

            self.stats["queries_sent"] += 1
            start_t = time.time()

            def _query_sync():
                try:
                    return dns.query.udp(query, res_ip, port=res_port, timeout=self.query_timeout)
                except Exception:
                    return None

            resp_msg = await loop.run_in_executor(None, _query_sync)
            latency = int((time.time() - start_t) * 1000)

            if resp_msg:
                extracted = parse_txt_response(resp_msg)
                if extracted:
                    resp_pkt = DnsPacket.deserialize(extracted)
                    if resp_pkt:
                        self.stats["queries_success"] += 1
                        self.stats["last_latency_ms"] = latency
                        return resp_pkt

            self.stats["queries_failed"] += 1
            self.rotate_resolver()
            await asyncio.sleep(0.2)

        return None

    async def ping(self) -> bool:
        pkt = DnsPacket(PKT_PING)
        resp = await self.send_dns_packet(pkt)
        return resp is not None and resp.pkt_type == PKT_PONG

    # Channel & Chat Management
    def join_channel(self, channel_name: str, password: str):
        chan_name = channel_name.strip().lower()
        tag = get_target_tag(chan_name, prefix="chan")
        if chan_name not in self.state["channels"]:
            self.state["channels"][chan_name] = {
                "name": chan_name,
                "password": password,
                "tag": tag,
                "last_seen_id": 0,
                "messages": []
            }
        else:
            self.state["channels"][chan_name]["password"] = password
            self.state["channels"][chan_name]["tag"] = tag
        self.save_state()

    def add_direct_chat(self, peer_name: str, shared_key: str):
        peer = peer_name.strip().lower()
        tag = get_target_tag(peer, prefix="direct")
        if peer not in self.state["direct"]:
            self.state["direct"][peer] = {
                "name": peer,
                "password": shared_key,
                "tag": tag,
                "last_seen_id": 0,
                "messages": []
            }
        else:
            self.state["direct"][peer]["password"] = shared_key
            self.state["direct"][peer]["tag"] = tag
        self.save_state()

    # Sending Messages and Files
    async def send_message(
        self,
        chat_type: str,  # "channel" or "direct"
        target_name: str,
        text: str = "",
        file_name: str = "",
        file_bytes: bytes = b"",
        on_progress = None
    ) -> Tuple[bool, str]:
        store = self.state["channels"] if chat_type == "channel" else self.state["direct"]
        target = target_name.strip().lower()
        if target not in store:
            return False, "Target not found in chats"

        chat_info = store[target]
        password = chat_info["password"]
        tag = chat_info["tag"]

        if file_bytes:
            max_bytes = self.max_file_size_mb * 1024 * 1024
            if len(file_bytes) > max_bytes:
                return False, f"File size exceeds limit of {self.max_file_size_mb} MB"

        sender = self.state.get("username", "Anonymous")
        packed = pack_message(sender=sender, text=text, file_name=file_name, file_bytes=file_bytes)
        encrypted_blob = encrypt_payload(packed, password)

        # Split encrypted blob into DNS chunks
        total_chunks = max(1, math.ceil(len(encrypted_blob) / self.chunk_size))
        temp_msg_id = random.randint(10000, 999999)

        for chunk_idx in range(total_chunks):
            start = chunk_idx * self.chunk_size
            chunk_data = encrypted_blob[start:start + self.chunk_size]
            pkt = DnsPacket(
                PKT_PUSH_CHUNK,
                target_tag=tag,
                msg_id=temp_msg_id,
                chunk_idx=chunk_idx,
                total_chunks=total_chunks,
                payload=chunk_data
            )
            resp = await self.send_dns_packet(pkt)
            if not resp or resp.pkt_type != PKT_PUSH_ACK:
                return False, f"Packet lost on chunk {chunk_idx + 1}/{total_chunks}"

            if on_progress:
                progress = int(((chunk_idx + 1) / total_chunks) * 100)
                await on_progress(progress)

        return True, "Message sent successfully"

    # Polling & Pulling Messages
    async def poll_target(self, chat_type: str, target_name: str) -> int:
        """Poll server for new messages on a specific target. Returns count of new messages fetched."""
        store = self.state["channels"] if chat_type == "channel" else self.state["direct"]
        if target_name not in store:
            return 0

        chat_info = store[target_name]
        tag = chat_info["tag"]
        password = chat_info["password"]
        last_seen = chat_info.get("last_seen_id", 0)

        # 1. Ask server for metadata of next message
        poll_pkt = DnsPacket(PKT_POLL_META, target_tag=tag, msg_id=last_seen)
        meta_resp = await self.send_dns_packet(poll_pkt)
        if not meta_resp or meta_resp.pkt_type != PKT_META_RESP or meta_resp.msg_id == 0:
            return 0

        next_msg_id = meta_resp.msg_id
        total_chunks = meta_resp.total_chunks

        # 2. Pull all chunks of this message
        chunks = {}
        for c_idx in range(total_chunks):
            pull_pkt = DnsPacket(
                PKT_PULL_CHUNK,
                target_tag=tag,
                msg_id=next_msg_id,
                chunk_idx=c_idx,
                total_chunks=total_chunks
            )
            resp = await self.send_dns_packet(pull_pkt)
            if not resp or resp.pkt_type != PKT_CHUNK_RESP:
                return 0  # retry next cycle
            chunks[c_idx] = resp.payload

        if len(chunks) != total_chunks:
            return 0

        full_encrypted = b"".join(chunks[i] for i in range(total_chunks))
        decrypted = decrypt_payload(full_encrypted, password)
        if not decrypted:
            # Password mismatch or corrupted
            chat_info["last_seen_id"] = next_msg_id
            self.save_state()
            return 0

        try:
            msg_obj = unpack_message(decrypted)
            msg_obj["msg_id"] = next_msg_id
            msg_obj["timestamp"] = int(time.time())
            chat_info["messages"].append(msg_obj)
            chat_info["last_seen_id"] = next_msg_id
            self.save_state()
            return 1
        except Exception:
            return 0

    async def poll_all(self):
        """Poll all active channels and direct chats."""
        self.stats["is_polling"] = True
        try:
            for chan_name in list(self.state["channels"].keys()):
                await self.poll_target("channel", chan_name)
            for user_name in list(self.state["direct"].keys()):
                await self.poll_target("direct", user_name)
        finally:
            self.stats["is_polling"] = False
