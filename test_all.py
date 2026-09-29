import asyncio
import os
import sqlite3
from dns_messenger.server import DnsTunnelServerProtocol, GLOBAL_DISK_STORE
from dns_messenger.client import DnsTunnelClient
from dns_messenger.crypto import get_message_file_bytes, get_target_tag, pack_message, encrypt_payload

async def run_full_suite():
    print("[1/6] Starting Disk-backed DNS Server (4-Hour Retention) on 127.0.0.1:5399...")
    cfg = {
        "base_domain": "tunnel.msg.local",
        "server_listen_host": "127.0.0.1",
        "server_listen_port": 5399,
        "max_file_size_mb": 20
    }
    loop = asyncio.get_running_loop()
    transport, protocol = await loop.create_datagram_endpoint(
        lambda: DnsTunnelServerProtocol(cfg, GLOBAL_DISK_STORE),
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
    new_id = GLOBAL_DISK_STORE.get_next_msg_id(pub_tag)
    GLOBAL_DISK_STORE.save_message(pub_tag, new_id, pub_enc)

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

    print("[4/6] Testing Private Encrypted Direct Chat (AES-256-GCM)...")
    chan_priv = f"secret_{os.getpid()}"
    secret_pass = "UltraSecretGroupPassword123"
    client1.add_direct_chat(chan_priv, secret_pass)

    # Verify clients CANNOT send messages in channels (Read-Only)
    cant_send_chan, chan_err = await client1.send_message("channel", chan_priv, text="Unauthorized channel message")
    assert not cant_send_chan, "Client should NOT be able to send message in channel!"
    print("  -> Channel read-only check passed successfully!")

    # Send in direct chat
    ok, err = await client1.send_message("direct", chan_priv, text="Confidential Group Message")
    assert ok, f"Send failed: {err}"

    # Verify server holds ZERO plaintext on disk
    sec_tag = get_target_tag(chan_priv, prefix="direct")
    server_blob = GLOBAL_DISK_STORE.get_message(sec_tag, 1)
    assert server_blob is not None
    assert b"Confidential Group Message" not in server_blob, "Plaintext leaked in server storage!"
    print(f"  -> Server disk ciphertext: {server_blob[:12].hex()}... (Plaintext nowhere found)")

    # Client2 joins with correct password
    client2.add_direct_chat(chan_priv, secret_pass)
    fetched_sec = await client2.poll_target("direct", chan_priv)
    assert fetched_sec == 1, "Failed to fetch private message"
    assert client2.state["direct"][chan_priv]["messages"][-1]["text"] == "Confidential Group Message"
    print("  -> Client2 decrypted group message successfully!")

    # Client3 joins with WRONG password -> cannot read
    client3 = DnsTunnelClient()
    client3.resolvers = ["127.0.0.1:5399"]
    client3.base_domain = "tunnel.msg.local"
    client3.add_direct_chat(chan_priv, "WrongPassword")
    fetched_wrong = await client3.poll_target("direct", chan_priv)
    assert fetched_wrong == 0, "Wrong password must not decrypt message"
    print("  -> Wrong password client rejected correctly!")

    print("[5/6] Testing Binary File Transfer Over DNS...")
    test_file_bytes = b"BINARY_DATA_TEST_FILE_CONTENT_" * 60
    ok, msg = await client1.send_message(
        chat_type="direct",
        target_name=chan_priv,
        text="Sending attachment",
        file_name="secret_doc.bin",
        file_bytes=test_file_bytes
    )
    assert ok, f"File send failed: {msg}"

    fetched_file = await client2.poll_target("direct", chan_priv)
    assert fetched_file == 1, "File message pull failed"
    file_msg = client2.state["direct"][chan_priv]["messages"][-1]
    recovered_bytes = get_message_file_bytes(file_msg)
    assert recovered_bytes == test_file_bytes, "File byte mismatch!"
    print(f"  -> File '{file_msg['file_name']}' received and verified successfully!")

    print("[6/7] Checking Disk Persistence (SQLite WAL & 100-Message Limit)...")
    assert GLOBAL_DISK_STORE.db_path.exists(), "server_storage.db must exist on disk!"
    with sqlite3.connect(GLOBAL_DISK_STORE.db_path) as conn:
        row_cnt = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
        assert row_cnt > 0, "Messages must be persisted on disk!"
        print(f"  -> Total messages persisted on disk in SQLite: {row_cnt}")

    # Test limit: insert messages past history limit (e.g. limit=100)
    chan_limit = f"limittest_{os.getpid()}"
    lim_tag = get_target_tag(chan_limit, prefix="chan")
    for i in range(105):
        m_id = GLOBAL_DISK_STORE.get_next_msg_id(lim_tag)
        GLOBAL_DISK_STORE.save_message(lim_tag, m_id, b"chunk_payload")
    with sqlite3.connect(GLOBAL_DISK_STORE.db_path) as conn:
        stored_cnt = conn.execute("SELECT COUNT(*) FROM messages WHERE target_tag = ?", (lim_tag,)).fetchone()[0]
        assert stored_cnt == 100, f"Expected exactly 100 messages retained, got {stored_cnt}"
        print(f"  -> 100-Message Limit Verified! (105 inserted -> exactly {stored_cnt} retained)")
    print("  -> Disk Persistence & Storage Architecture Verified!")

    print("[7/7] Testing Channel Auto-Discovery & On-Demand Media Pull (Autodownload OFF)...")
    chan_auto = f"autonews_{os.getpid()}"
    GLOBAL_DISK_STORE.register_public_channel(chan_auto)
    discovered = await client1.discover_public_channels()
    assert chan_auto in discovered
    print(f"  -> Client auto-discovered channels: {discovered}")

    # Test on-demand media
    m_tag = get_target_tag(chan_auto, prefix="chan")
    m_id = GLOBAL_DISK_STORE.get_next_msg_id(m_tag)
    sample_photo = b"JPEG_IMAGE_BYTES_DEMO_" * 40
    GLOBAL_DISK_STORE.save_media(m_tag, m_id, sample_photo)

    # Save message with has_media=True, file_bytes=b""
    p_packed = pack_message(sender="Telegram", text="Breaking Photo", file_name="photo.jpg", is_public=True, has_media=True, media_type="photo", media_size=len(sample_photo))
    p_enc = encrypt_payload(p_packed, password=None)
    GLOBAL_DISK_STORE.save_message(m_tag, m_id, p_enc)

    # Client polls: only text arrives!
    await client1.poll_target("channel", chan_auto)
    msg_obj = client1.state["channels"][chan_auto]["messages"][-1]
    assert msg_obj["has_media"] is True
    assert msg_obj.get("file_data") == "", "Media should NOT be auto-downloaded!"
    print("  -> Text arrived without media (Autodownload OFF confirmed).")

    # Client on-demand downloads media:
    dl_media = await client1.fetch_media_for_message("channel", chan_auto, m_id)
    assert dl_media == sample_photo, "On-demand media download mismatch!"
    print("  -> On-demand media pull over DNS verified successfully!")

    transport.close()
    print("\n=======================================================")
    print("ALL TESTS PASSED SUCCESSFULLY! 100% OPERATIONAL.")
    print("=======================================================\n")

if __name__ == "__main__":
    asyncio.run(run_full_suite())
