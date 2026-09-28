import os
import zlib
import json
import hashlib
from typing import Optional, Tuple
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes

def get_target_tag(name: str, prefix: str = "chan") -> str:
    """Generate 16-hex-character zero-knowledge tag for server-side routing."""
    raw = f"{prefix}:{name.strip().lower()}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:16]

def derive_key(password: str, salt: bytes) -> bytes:
    """Derive 256-bit AES key from password and salt."""
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=100000,
    )
    return kdf.derive(password.encode("utf-8"))

def encrypt_payload(data: bytes, password: str) -> bytes:
    """
    Compress with zlib, then encrypt with AES-256-GCM.
    Returns: salt (16B) + nonce (12B) + ciphertext_with_tag
    """
    compressed = zlib.compress(data, level=9)
    salt = os.urandom(16)
    nonce = os.urandom(12)
    key = derive_key(password, salt)
    aesgcm = AESGCM(key)
    ciphertext = aesgcm.encrypt(nonce, compressed, None)
    return salt + nonce + ciphertext

def decrypt_payload(encrypted_blob: bytes, password: str) -> Optional[bytes]:
    """
    Decrypts AES-256-GCM blob and decompresses zlib payload.
    Returns None if key is wrong or blob is corrupted.
    """
    if len(encrypted_blob) < 16 + 12 + 16:
        return None
    salt = encrypted_blob[:16]
    nonce = encrypted_blob[16:28]
    ciphertext = encrypted_blob[28:]
    try:
        key = derive_key(password, salt)
        aesgcm = AESGCM(key)
        decompressed = aesgcm.decrypt(nonce, ciphertext, None)
        return zlib.decompress(decompressed)
    except Exception:
        return None

def pack_message(sender: str, text: str = "", file_name: str = "", file_bytes: bytes = b"") -> bytes:
    """Serialize chat message / file into JSON bytes before encryption."""
    obj = {
        "sender": sender,
        "text": text,
        "has_file": bool(file_bytes),
        "file_name": file_name,
        "file_size": len(file_bytes),
        "file_data": file_bytes.hex() if file_bytes else ""
    }
    return json.dumps(obj, ensure_ascii=False).encode("utf-8")

def unpack_message(raw_bytes: bytes) -> dict:
    """Deserialize decrypted JSON message."""
    return json.loads(raw_bytes.decode("utf-8"))

def get_message_file_bytes(data: dict) -> bytes:
    """Extract raw bytes of file if present."""
    if data.get("file_data"):
        return bytes.fromhex(data["file_data"])
    return b""
