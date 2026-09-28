import asyncio
import os
import sqlite3
from dns_messenger.server import DnsTunnelServerProtocol
from dns_messenger.client import DnsTunnelClient
from dns_messenger.crypto import get_message_file_bytes
from dns_messenger.config import DATA_DIR

async def run_full_suite():
    print("[1/6] Starting In-Memory DNS Server on 127.0.0.1:5399...")
    cfg = {
        "base_domain": "tunnel.msg.local",
        "server_listen_host": "127.0.0.1",
        "server_listen_port": 5399,
        "max_file_size_mb": 20
    }
    loop = asyncio.get_running_loop()
    transport, protocol = await loop.create_datagram_endpoint(
        lambda: DnsTunnelServerProtocol(cfg),
        local_addr=("127.0.0.1", 5399)
    )

    print("[2/6] Initializing DNS Messenger Client...")
    client1 = DnsTunnelClient()
    client1.resolvers = ["127.0.0.1:5399"]
    client1.base_domain = "tunnel.msg.local"

    # Ping test
    assert await client1.ping(), "Ping test failed"
    print("  -> Ping OK!")

    print("[3/6] Testing Channel Creation & AES-256-GCM Encryption...")
    channel_name = "test_channel"
    channel_pass = "SecurePassphrase123!#"
    client1.join_channel(channel_name, channel_pass)

    ok, msg = await client1.send_message("channel", channel_name, text="Hello DNS Tunnel World!")
    assert ok, f"Send failed: {msg}"
    print("  -> Message sent over DNS packets successfully!")

    print("[4/6] Verifying Server-Side Zero Knowledge...")
    db_file = DATA_DIR / "server_messages.db"
    with sqlite3.connect(db_file) as conn:
        rows = conn.execute("SELECT target_tag, encrypted_data FROM messages").fetchall()
        assert len(rows) > 0, "No messages found in server DB"
        tag, enc = rows[-1]
        print(f"  -> Server raw tag: {tag} (16 hex chars)")
        print(f"  -> Server ciphertext starts with: {enc[:16].hex()}... (Plaintext nowhere found)")
        assert b"Hello DNS Tunnel World!" not in enc, "Plaintext leaked to server!"
    print("  -> Zero Knowledge Verified!")

    print("[5/6] Testing Channel Message Sync & Decryption...")
    client2 = DnsTunnelClient()
    client2.resolvers = ["127.0.0.1:5399"]
    client2.base_domain = "tunnel.msg.local"
    client2.join_channel(channel_name, channel_pass)

    fetched = await client2.poll_target("channel", channel_name)
    assert fetched == 1, f"Expected 1 fetched message, got {fetched}"
    last_msg = client2.state["channels"][channel_name]["messages"][-1]
    assert last_msg["text"] == "Hello DNS Tunnel World!"
    print(f"  -> Client2 Decrypted: '{last_msg['text']}'")

    print("[6/6] Testing Binary File Transfer Over DNS...")
    test_file_bytes = b"BINARY_DATA_TEST_FILE_CONTENT_" * 60  # ~1.8KB chunked
    ok, msg = await client1.send_message(
        chat_type="channel",
        target_name=channel_name,
        text="Sending attachment",
        file_name="secret_doc.bin",
        file_bytes=test_file_bytes
    )
    assert ok, f"File send failed: {msg}"

    fetched_file = await client2.poll_target("channel", channel_name)
    assert fetched_file == 1, "File message pull failed"
    file_msg = client2.state["channels"][channel_name]["messages"][-1]
    recovered_bytes = get_message_file_bytes(file_msg)
    assert recovered_bytes == test_file_bytes, "File byte mismatch!"
    print(f"  -> File '{file_msg['file_name']}' received and verified successfully!")

    transport.close()
    print("\n=======================================================")
    print("ALL TESTS PASSED SUCCESSFULLY! 100% OPERATIONAL.")
    print("=======================================================\n")

if __name__ == "__main__":
    asyncio.run(run_full_suite())
