import sys
import asyncio
import logging
from aiohttp import web

from dns_messenger.config import load_config
from dns_messenger.server import run_server
from dns_messenger.client import DnsTunnelClient
from dns_messenger.web_ui import WebApp

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

async def start_client_app():
    cfg = load_config()
    client = DnsTunnelClient()
    web_app = WebApp(client)
    runner = web.AppRunner(web_app.app)
    await runner.setup()
    
    host = cfg.get("web_host", "127.0.0.1")
    port = cfg.get("web_port", 8080)
    site = web.TCPSite(runner, host, port)
    await site.start()
    
    print("\n=======================================================")
    print(f"DNS Messenger Web GUI running at: http://{host}:{port}")
    print(f"DNS Tunnel Resolvers: {', '.join(client.resolvers)}")
    print(f"Base Domain: {client.base_domain}")
    print(f"Encryption: Client-Side AES-256-GCM Zero-Knowledge")
    print("=======================================================\n")
    
    try:
        while True:
            await asyncio.sleep(3600)
    finally:
        await runner.cleanup()

async def main():
    mode = sys.argv[1].lower() if len(sys.argv) > 1 else "client"
    if mode == "server":
        await run_server()
    elif mode == "client":
        await start_client_app()
    elif mode in ("both", "all"):
        await asyncio.gather(
            run_server(),
            start_client_app()
        )
    else:
        print("Usage:")
        print("  python run.py server  -> Run authoritative DNS server (VPS)")
        print("  python run.py client  -> Run Web GUI & DNS messenger client")
        print("  python run.py both    -> Run both server and client on same machine")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nExiting DNS Messenger.")
