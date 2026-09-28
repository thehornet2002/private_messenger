import base64
import struct
from typing import Optional, Tuple, List
import dns.message
import dns.rdatatype
import dns.rdataclass
import dns.rrset

MAGIC = 0xD5

# Packet Types
PKT_PUSH_CHUNK = 1   # Client sending encrypted chunk
PKT_PUSH_ACK   = 2   # Server ACK chunk
PKT_POLL_META  = 3   # Client asking for message info for a target tag
PKT_META_RESP  = 4   # Server returning message metadata
PKT_PULL_CHUNK = 5   # Client fetching a specific chunk
PKT_CHUNK_RESP = 6   # Server returning chunk data
PKT_PING       = 7   # Connectivity check
PKT_PONG       = 8   # Pong

HEADER_LEN = 22
HEADER_STRUCT = ">BBH8sIHHH"

class DnsPacket:
    def __init__(
        self,
        pkt_type: int,
        session_id: int = 0,
        target_tag: str = "0000000000000000",  # 16 hex chars
        msg_id: int = 0,
        chunk_idx: int = 0,
        total_chunks: int = 1,
        payload: bytes = b""
    ):
        self.pkt_type = pkt_type
        self.session_id = session_id
        self.target_tag = target_tag.lower().ljust(16, "0")[:16]
        self.msg_id = msg_id
        self.chunk_idx = chunk_idx
        self.total_chunks = total_chunks
        self.payload = payload

    def serialize(self) -> bytes:
        tag_bytes = bytes.fromhex(self.target_tag)
        hdr = struct.pack(
            HEADER_STRUCT,
            MAGIC,
            self.pkt_type,
            self.session_id & 0xFFFF,
            tag_bytes,
            self.msg_id & 0xFFFFFFFF,
            self.chunk_idx & 0xFFFF,
            self.total_chunks & 0xFFFF,
            len(self.payload) & 0xFFFF
        )
        return hdr + self.payload

    @classmethod
    def deserialize(cls, data: bytes) -> Optional["DnsPacket"]:
        if len(data) < HEADER_LEN:
            return None
        magic, p_type, sess_id, tag_bytes, m_id, c_idx, tot_c, p_len = struct.unpack(
            HEADER_STRUCT, data[:HEADER_LEN]
        )
        if magic != MAGIC:
            return None
        payload = data[HEADER_LEN:HEADER_LEN + p_len]
        return cls(
            pkt_type=p_type,
            session_id=sess_id,
            target_tag=tag_bytes.hex(),
            msg_id=m_id,
            chunk_idx=c_idx,
            total_chunks=tot_c,
            payload=payload
        )

def bytes_to_domain(data: bytes, base_domain: str) -> str:
    """Encode binary packet into DNS labels under base_domain."""
    b32 = base64.b32encode(data).decode("ascii").rstrip("=").lower()
    labels = [b32[i:i + 58] for i in range(0, len(b32), 58)]
    clean_base = base_domain.strip(".")
    return ".".join(labels) + "." + clean_base

def domain_to_bytes(domain_name: str, base_domain: str) -> Optional[bytes]:
    """Decode DNS labels back into raw packet bytes."""
    clean_d = domain_name.strip(".").lower()
    clean_base = base_domain.strip(".").lower()
    if not clean_d.endswith(clean_base):
        return None
    sub = clean_d[:-len(clean_base)].rstrip(".")
    if not sub:
        return None
    b32 = sub.replace(".", "").upper()
    pad = (8 - len(b32) % 8) % 8
    b32 += "=" * pad
    try:
        return base64.b32decode(b32)
    except Exception:
        return None

def build_txt_response(query_msg: dns.message.Message, payload_bytes: bytes) -> dns.message.Message:
    """Build DNS TXT response carrying base64-encoded binary payload."""
    resp = dns.message.make_response(query_msg)
    b64_str = base64.b64encode(payload_bytes).decode("ascii")
    # Split into 250-character strings to respect DNS TXT character-string limit
    chunks = [b64_str[i:i + 250] for i in range(0, len(b64_str), 250)]
    if not chunks:
        chunks = [""]
    quoted_chunks = [f'"{c}"' for c in chunks]
    txt_text = " ".join(quoted_chunks)
    rrset = dns.rrset.from_text(
        query_msg.question[0].name,
        0,
        dns.rdataclass.IN,
        dns.rdatatype.TXT,
        txt_text
    )
    resp.answer.append(rrset)
    return resp

def parse_txt_response(resp_msg: dns.message.Message) -> Optional[bytes]:
    """Extract and base64-decode binary payload from DNS TXT response."""
    for rrset in resp_msg.answer:
        if rrset.rdtype == dns.rdatatype.TXT:
            full_b64 = ""
            for item in rrset:
                for text_part in item.strings:
                    full_b64 += text_part.decode("ascii", errors="ignore")
            if full_b64:
                try:
                    return base64.b64decode(full_b64)
                except Exception:
                    pass
    return None
