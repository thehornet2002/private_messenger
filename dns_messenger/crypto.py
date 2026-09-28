import os
import zlib
import json
import hashlib
from typing import Optional, Tuple
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes

# Header flag byte in wire payload:
FLAG_ENCRYPTED = 0x01
FLAG_PUBLIC    = 0x02

def get_target_tag(name: str, prefix: str = "chan") -> str:
    """Generate 16-hex-character zero-knowledge routing tag."""
    raw = f"{prefix}:{name.strip().lower()}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:16]

def derive_key(password: str, salt: bytes) -> bytes:
    """Derive 256-bit AES key from password and salt using PBKDF2."""
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=100000,
    )
    return kdf.derive(password.encode("utf-8"))

def encrypt_payload(data: bytes, password: Optional[str] = None) -> bytes:
    """
    If password is provided: compresses with zlib and encrypts with AES-256-GCM.
    Returns: FLAG_ENCRYPTED (1B) + salt (16B) + nonce (12B) + ciphertext_with_tag
    
    If password is empty/None (Public Broadcast):
    Returns: FLAG_PUBLIC (1B) + compressed_data
    """
    compressed = zlib.compress(data, level=9)
    if not password:
        # Public unencrypted broadcast
        return bytes([FLAG_PUBLIC]) + compressed

    salt = os.urandom(16)
    nonce = os.urandom(12)
    key = derive_key(password, salt)
    aesgcm = AESGCM(key)
    ciphertext = aesgcm.encrypt(nonce, compressed, None)
    return bytes([FLAG_ENCRYPTED]) + salt + nonce + ciphertext

def decrypt_payload(encrypted_blob: bytes, password: Optional[str] = None) -> Optional[Tuple[bytes, bool]]:
    """
    Decrypts blob. Returns (raw_bytes, is_public).
    Returns None if key is wrong or blob is corrupted.
    """
    if len(encrypted_blob) < 2:
        return None

    flag = encrypted_blob[0]
    body = encrypted_blob[1:]

    if flag == FLAG_PUBLIC:
        # Public broadcast - no password needed
        try:
            return zlib.decompress(body), True
        except Exception:
            return None

    if flag == FLAG_ENCRYPTED:
        if not password:
            return None  # Needs password
        if len(body) < 16 + 12 + 16:
            return None
        salt = body[:16]
        nonce = body[16:28]
        ciphertext = body[28:]
        try:
            key = derive_key(password, salt)
            aesgcm = AESGCM(key)
            decompressed = aesgcm.decrypt(nonce, ciphertext, None)
            return zlib.decompress(decompressed), False
        except Exception:
            return None

    return None

def pack_message(sender: str, text: str = "", file_name: str = "", file_bytes: bytes = b"", is_public: bool = False) -> bytes:
    """Serialize chat message / file into JSON bytes before transmission."""
    obj = {
        "sender": sender,
        "text": text,
        "is_public": is_public,
        "has_file": bool(file_bytes),
        "file_name": file_name,
        "file_size": len(file_bytes),
        "file_data": file_bytes.hex() if file_bytes else ""
    }
    return json.dumps(obj, ensure_ascii=False).encode("utf-8")

def unpack_message(raw_bytes: bytes) -> dict:
    """Deserialize JSON message."""
    return json.loads(raw_bytes.decode("utf-8"))

def get_message_file_bytes(data: dict) -> bytes:
    """Extract raw bytes of file if present."""
    if data.get("file_data"):
        return bytes.fromhex(data["file_data"])
    return b""
