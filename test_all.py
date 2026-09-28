import asyncio
import os
from dns_messenger.server import DnsTunnelServerProtocol, GLOBAL_MEMORY_STORE
from dns_messenger.client import DnsTunnelClient
from dns_messenger.crypto import get_message_file_bytes, get_target_tag, pack_message, encrypt_payload

async def run_full_suite():
    print("[1/6] Starting In-Memory Ephemeral DNS Server on 127.0.0.1:5399...")
    cfg = {
        "base_domain": "tunnel.msg.local",
        "server_listen_host": "127.0.0.1",
        "server_listen_port": 5399,
        "max_file_size_mb": 20
    }
    loop = asyncio.get_running_loop()
    transport, protocol = await loop.create_datagram_endpoint(
        lambda: DnsTunnelServerProtocol(cfg, GLOBAL_MEMORY_STORE),
        local_addr=("127.0.0.1", 5399)
    )

    print("[2/6] Initializing DNS Messenger Client...")
    client1 = DnsTunnelClient()
    client1.resolvers = ["127.0.0.1:5399"]
    client1.base_domain = "tunnel.msg.local"

    # Ping test
    assert await client1.ping(), "Ping test failed"
    print("  -> Ping OK!")

    print("[3/6] Testing Public / Telegram Broadcast (No password)...")
    chan_pub = f"news_{os.getpid()}"
    pub_tag = get_target_tag(chan_pub, prefix="chan")
    pub_packed = pack_message(sender="📢 Telegram [@breaking]", text="Public News Alert!", is_public=True)
    pub_enc = encrypt_payload(pub_packed, password=None)
    new_id = GLOBAL_MEMORY_STORE.get_next_msg_id(pub_tag)
    GLOBAL_MEMORY_STORE.save_message(pub_tag, new_id, pub_enc)

    # Client joins public channel with NO password
    client2 = DnsTunnelClient()
    client2.resolvers = ["127.0.0.1:5399"]
    client2.base_domain = "tunnel.msg.local"
    client2.join_channel(chan_pub, password="")  # Public

    fetched = await client2.poll_target("channel", chan_pub)
    assert fetched == 1, f"Expected 1 fetched public message, got {fetched}"
    pub_msg = client2.state["channels"][chan_pub]["messages"][-1]
    assert pub_msg["is_public"] is True
    assert "Public News Alert!" in pub_msg["text"]
    print(f"  -> Client received public broadcast: '{pub_msg['text']}'")

    print("[4/6] Testing Private Encrypted Group Chat (AES-256-GCM)...")
    chan_priv = f"secret_{os.getpid()}"
    secret_pass = "UltraSecretGroupPassword123"
    client1.join_channel(chan_priv, secret_pass)
    ok, err = await client1.send_message("channel", chan_priv, text="Confidential Group Message")
    assert ok, f"Send failed: {err}"

    # Verify server holds ZERO plaintext in RAM
    sec_tag = get_target_tag(chan_priv, prefix="chan")
    server_blob = GLOBAL_MEMORY_STORE.get_message(sec_tag, 1)
    assert server_blob is not None
    assert b"Confidential Group Message" not in server_blob, "Plaintext leaked in server RAM!"
    print(f"  -> Server RAM payload: {server_blob[:12].hex()}... (Plaintext nowhere found)")

    # Client2 joins with correct password
    client2.join_channel(chan_priv, secret_pass)
    fetched_sec = await client2.poll_target("channel", chan_priv)
    assert fetched_sec == 1, "Failed to fetch private message"
    assert client2.state["channels"][chan_priv]["messages"][-1]["text"] == "Confidential Group Message"
    print("  -> Client2 decrypted group message successfully!")

    # Client3 joins with WRONG password -> cannot read
    client3 = DnsTunnelClient()
    client3.resolvers = ["127.0.0.1:5399"]
    client3.base_domain = "tunnel.msg.local"
    client3.join_channel(chan_priv, "WrongPassword")
    fetched_wrong = await client3.poll_target("channel", chan_priv)
    assert fetched_wrong == 0, "Wrong password must not decrypt message"
    print("  -> Wrong password client rejected correctly!")

    print("[5/6] Testing Binary File Transfer Over DNS...")
    test_file_bytes = b"BINARY_DATA_TEST_FILE_CONTENT_" * 60
    ok, msg = await client1.send_message(
        chat_type="channel",
        target_name=chan_priv,
        text="Sending attachment",
        file_name="secret_doc.bin",
        file_bytes=test_file_bytes
    )
    assert ok, f"File send failed: {msg}"

    fetched_file = await client2.poll_target("channel", chan_priv)
    assert fetched_file == 1, "File message pull failed"
    file_msg = client2.state["channels"][chan_priv]["messages"][-1]
    recovered_bytes = get_message_file_bytes(file_msg)
    assert recovered_bytes == test_file_bytes, "File byte mismatch!"
    print(f"  -> File '{file_msg['file_name']}' received and verified successfully!")

    print("[6/6] Checking In-Memory Stats & Zero Disk Writes...")
    stats = GLOBAL_MEMORY_STORE.stats
    print(f"  -> Total queries handled: {stats['total_queries']}")
    print(f"  -> Messages received in RAM: {stats['total_messages_received']}")
    print(f"  -> Bytes transferred: {stats['total_bytes_transferred']}")
    assert stats["total_queries"] > 5
    assert not os.path.exists("data/server_messages.db"), "Server must NOT create any sqlite database file!"
    print("  -> Zero-Knowledge In-Memory Architecture Verified!")

    transport.close()
    print("\n=======================================================")
    print("ALL TESTS PASSED SUCCESSFULLY! 100% OPERATIONAL.")
    print("=======================================================\n")

if __name__ == "__main__":
    asyncio.run(run_full_suite())
