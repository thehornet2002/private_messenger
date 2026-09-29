import asyncio
import json
import time
from aiohttp import web
from .config import load_config, save_config
from .server import GLOBAL_DISK_STORE
from .telegram_server_bot import GLOBAL_TELEGRAM_BOT

SERVER_ADMIN_HTML = """<!DOCTYPE html>
<html lang="fa" dir="rtl">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>DNS Messenger | پنل مدیریت سرور</title>
    <link href="https://cdn.jsdelivr.net/gh/rastikerdar/vazirmatn@v33.003/Vazirmatn-font-face.css" rel="stylesheet">
    <style>
        :root {
            --bg-dark: #0a0e14;
            --bg-card: #131b26;
            --accent: #3b82f6;
            --green: #10b981;
            --yellow: #f59e0b;
            --red: #ef4444;
            --text-main: #f3f4f6;
            --text-muted: #9ca3af;
            --border: #1f2937;
        }
        * { box-sizing: border-box; margin: 0; padding: 0; font-family: 'Vazirmatn', sans-serif; }
        body { background: var(--bg-dark); color: var(--text-main); min-height: 100vh; padding: 24px; }
        .container { max-width: 1000px; margin: 0 auto; display: flex; flex-direction: column; gap: 20px; }
        
        /* Header */
        header { display: flex; justify-content: space-between; align-items: center; border-bottom: 1px solid var(--border); padding-bottom: 16px; }
        .title { font-size: 1.4rem; font-weight: bold; display: flex; align-items: center; gap: 10px; }
        .badge-live { background: rgba(16, 185, 129, 0.15); color: var(--green); border: 1px solid var(--green); padding: 4px 10px; border-radius: 20px; font-size: 0.8rem; display: flex; align-items: center; gap: 6px; }
        .dot { width: 8px; height: 8px; background: var(--green); border-radius: 50%; box-shadow: 0 0 8px var(--green); }
        
        /* Stats Grid */
        .stats-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 14px; }
        .stat-card { background: var(--bg-card); border: 1px solid var(--border); border-radius: 10px; padding: 16px; display: flex; flex-direction: column; gap: 6px; }
        .stat-label { font-size: 0.82rem; color: var(--text-muted); }
        .stat-val { font-size: 1.6rem; font-weight: bold; color: white; }
        
        /* Sections */
        .card { background: var(--bg-card); border: 1px solid var(--border); border-radius: 10px; padding: 20px; display: flex; flex-direction: column; gap: 14px; }
        h3 { font-size: 1.1rem; color: #93c5fd; border-bottom: 1px solid var(--border); padding-bottom: 8px; }
        
        .form-row { display: flex; gap: 10px; flex-wrap: wrap; }
        .form-group { display: flex; flex-direction: column; gap: 6px; flex: 1; min-width: 220px; }
        label { font-size: 0.82rem; color: var(--text-muted); }
        input, select, textarea { background: #070b10; border: 1px solid var(--border); border-radius: 6px; color: white; padding: 9px 12px; outline: none; font-size: 0.9rem; }
        input:focus { border-color: var(--accent); }
        
        button { cursor: pointer; border: none; border-radius: 6px; font-weight: bold; padding: 9px 16px; font-size: 0.88rem; transition: all 0.2s; }
        .btn-primary { background: var(--accent); color: white; }
        .btn-primary:hover { background: #2563eb; }
        .btn-green { background: var(--green); color: white; }
        .btn-danger { background: var(--red); color: white; }
        
        /* Table */
        table { width: 100%; border-collapse: collapse; margin-top: 10px; font-size: 0.88rem; }
        th, td { padding: 10px; text-align: right; border-bottom: 1px solid var(--border); }
        th { color: var(--text-muted); }
        
        .notice { background: rgba(59, 130, 246, 0.1); border-right: 4px solid var(--accent); padding: 10px 14px; font-size: 0.85rem; border-radius: 4px; }
        .step-box { background: #0d1520; border: 1px dashed var(--accent); border-radius: 8px; padding: 14px; display: flex; flex-direction: column; gap: 10px; }
    </style>
</head>
<body>
    <div class="container">
        <header>
            <div class="title">
                <span>🛡️ DNS Tunnel Messenger | سرور اختصاصی</span>
            </div>
            <div class="badge-live">
                <div class="dot"></div>
                <span>سرور فعال (ذخیره‌سازی دیسک با انقضای ۴ ساعته)</span>
            </div>
        </header>

        <!-- Stats Overview -->
        <div class="stats-grid">
            <div class="stat-card">
                <span class="stat-label">تعداد کوئری‌های پردازش‌شده DNS</span>
                <span class="stat-val" id="stat-queries">0</span>
            </div>
            <div class="stat-card">
                <span class="stat-label">پیام‌های ذخیره‌شده (بازه ۴ ساعت اخیر)</span>
                <span class="stat-val" id="stat-msgs">0</span>
            </div>
            <div class="stat-card">
                <span class="stat-label">حجم کل ترافیک منتقل‌شده</span>
                <span class="stat-val" id="stat-traffic">0 KB</span>
            </div>
            <div class="stat-card">
                <span class="stat-label">کانال‌های فعال روی سرور</span>
                <span class="stat-val" id="stat-tags">0</span>
            </div>
            <div class="stat-card">
                <span class="stat-label">وضعیت سلف‌بات تلگرام (Kurigram)</span>
                <span class="stat-val" id="stat-tg-status" style="font-size: 1.1rem; color: var(--yellow);">در حال بررسی...</span>
            </div>
        </div>

        <div class="notice">
            💾 <strong>پایداری روی دیسک با پاک‌سازی خودکار:</strong> پیام‌ها و فایل‌های ارسالی تا سقف حجم تعیین‌شده به مدت ۴ ساعت روی دیسک سرور در پایگاه‌داده امن ذخیره می‌شوند و بسته‌های قدیمی‌تر از ۴ ساعت به طور مداوم و خودکار حذف می‌گردند.
        </div>

        <!-- Telegram Kurigram Setup Wizard -->
        <div class="card">
            <h3>🤖 راه‌اندازی و ورود به سلف‌بات تلگرام (Kurigram Server Bot)</h3>
            <p style="font-size: 0.85rem; color: var(--text-muted);">
                این بخش به صورت مستقیم روی سرور اجرا می‌شود و کانال‌های دلخواه شما در تلگرام را رصد کرده و بدون نیاز به رمزنگاری برای همه کلاینت‌ها پخش (Broadcast) می‌کند.
            </p>

            <div class="step-box" id="step-1">
                <strong>مرحله ۱: مشخصات تلگرام و شماره تماس</strong>
                <div class="form-row">
                    <div class="form-group" style="max-width: 140px;">
                        <label>Telegram API ID:</label>
                        <input type="text" id="tg-api-id" placeholder="مثال: 1234567">
                    </div>
                    <div class="form-group">
                        <label>Telegram API Hash:</label>
                        <input type="text" id="tg-api-hash" placeholder="مثال: 0123456789abcdef0123456789abcdef">
                    </div>
                    <div class="form-group">
                        <label>شماره تماس (با کد کشور):</label>
                        <input type="text" id="tg-phone" placeholder="+989123456789">
                    </div>
                    <div class="form-group" style="align-self: flex-end;">
                        <button class="btn-primary" onclick="requestTgCode()">دریافت کد تایید 📩</button>
                    </div>
                </div>
            </div>

            <div class="step-box" id="step-2" style="display: none;">
                <strong>مرحله ۲: وارد کردن کد تایید تلگرام</strong>
                <div class="form-row">
                    <div class="form-group">
                        <label>کد تایید دریافت‌شده در تلگرام یا SMS:</label>
                        <input type="text" id="tg-code" placeholder="کد ۵ رقمی">
                    </div>
                    <div class="form-group" style="align-self: flex-end;">
                        <button class="btn-green" onclick="submitTgCode()">تایید کد و ورود ✅</button>
                    </div>
                </div>
            </div>

            <div class="step-box" id="step-3" style="display: none; border-color: var(--yellow);">
                <strong style="color: var(--yellow);">مرحله ۳: تایید دو مرحله‌ای (2FA Cloud Password)</strong>
                <div class="form-row">
                    <div class="form-group">
                        <label>رمز دو مرحله‌ای اکانت تلگرام:</label>
                        <input type="password" id="tg-2fa" placeholder="رمز ۲FA">
                    </div>
                    <div class="form-group" style="align-self: flex-end;">
                        <button class="btn-green" onclick="submitTg2fa()">تایید رمز 2FA 🔑</button>
                    </div>
                </div>
            </div>

            <div id="auth-status-msg" style="font-size: 0.85rem; margin-top: 4px;"></div>
        </div>

        <!-- Tracked Channels Management -->
        <div class="card">
            <h3>📢 مدیریت کانال‌های رصدشده تلگرام جهت پخش همگانی</h3>
            <div class="form-row">
                <div class="form-group">
                    <label>کانال تلگرام (آیدی یا یوزرنیم):</label>
                    <input type="text" id="map-tg" placeholder="@durov یا -1001234567">
                </div>
                <div class="form-group">
                    <label>نام کانال در پیام‌رسان:</label>
                    <input type="text" id="map-msg" placeholder="news">
                </div>
                <div class="form-group">
                    <label>رمز عبور کانال (خالی بگذارید تا همگانی و بدون رمز باشد):</label>
                    <input type="text" id="map-pass" placeholder="اختیاری (خالی = برای همه)">
                </div>
                <div class="form-group" style="align-self: flex-end; max-width: 140px;">
                    <button class="btn-primary" onclick="addChannelMapping()">➕ افزودن به ردیاب</button>
                </div>
            </div>

            <table>
                <thead>
                    <tr>
                        <th>کانال تلگرام</th>
                        <th>کانال پیام‌رسان</th>
                        <th>نوع انتشار</th>
                        <th>عملیات</th>
                    </tr>
                </thead>
                <tbody id="mapping-table-body">
                    <!-- Populated dynamically -->
                </tbody>
            </table>
        </div>
    </div>

    <script>
        async function fetchStats() {
            try {
                const res = await fetch('/api/admin/stats');
                const data = await res.json();
                document.getElementById('stat-queries').innerText = data.total_queries;
                document.getElementById('stat-msgs').innerText = data.total_messages_received;
                document.getElementById('stat-tags').innerText = data.active_tags_count;
                document.getElementById('stat-traffic').innerText = (data.total_bytes_transferred / 1024).toFixed(1) + ' KB';
                
                const tgEl = document.getElementById('stat-tg-status');
                if (data.tg_connected) {
                    tgEl.innerText = 'متصل و فعال ✅';
                    tgEl.style.color = 'var(--green)';
                } else {
                    tgEl.innerText = 'غیرفعال / خارج از حساب ❌';
                    tgEl.style.color = 'var(--red)';
                }
            } catch(e) {}
        }

        async function fetchMappings() {
            try {
                const res = await fetch('/api/admin/mappings');
                const list = await res.json();
                const tbody = document.getElementById('mapping-table-body');
                tbody.innerHTML = '';
                if (list.length === 0) {
                    tbody.innerHTML = '<tr><td colspan="4" style="text-align: center; color: var(--text-muted);">هنوز کانالی برای ردیابی ثبت نشده است.</td></tr>';
                    return;
                }
                list.forEach((item, idx) => {
                    const tr = document.createElement('tr');
                    const isPub = !item.password;
                    tr.innerHTML = `
                        <td><strong>${item.tg_channel}</strong></td>
                        <td>${item.messenger_channel}</td>
                        <td><span style="color:${isPub ? 'var(--green)' : 'var(--yellow)'};">${isPub ? '📢 همگانی (بدون رمز)' : '🔒 رمزنگاری‌شده AES'}</span></td>
                        <td><button class="btn-danger" style="padding: 4px 8px; font-size: 0.75rem;" onclick="deleteMapping(${idx})">حذف</button></td>
                    `;
                    tbody.appendChild(tr);
                });
            } catch(e) {}
        }

        async function requestTgCode() {
            const api_id = parseInt(document.getElementById('tg-api-id').value);
            const api_hash = document.getElementById('tg-api-hash').value.trim();
            const phone = document.getElementById('tg-phone').value.trim();
            if (!api_id || !api_hash || !phone) { alert('لطفاً تمام فیلدهای مرحله ۱ را پر کنید'); return; }

            const msgEl = document.getElementById('auth-status-msg');
            msgEl.innerText = 'در حال ارسال کد تایید به تلگرام...';

            const res = await fetch('/api/admin/tg/request_code', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ api_id, api_hash, phone })
            });
            const data = await res.json();
            if (data.ok) {
                msgEl.innerText = data.message;
                msgEl.style.color = 'var(--green)';
                document.getElementById('step-2').style.display = 'block';
            } else {
                msgEl.innerText = 'خطا: ' + data.error;
                msgEl.style.color = 'var(--red)';
            }
        }

        async function submitTgCode() {
            const code = document.getElementById('tg-code').value.trim();
            if (!code) return;
            const msgEl = document.getElementById('auth-status-msg');
            msgEl.innerText = 'در حال بررسی کد...';

            const res = await fetch('/api/admin/tg/submit_code', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ code })
            });
            const data = await res.json();
            if (data.ok) {
                if (data.need_2fa) {
                    msgEl.innerText = data.message;
                    msgEl.style.color = 'var(--yellow)';
                    document.getElementById('step-3').style.display = 'block';
                } else {
                    msgEl.innerText = data.message;
                    msgEl.style.color = 'var(--green)';
                    fetchStats();
                }
            } else {
                msgEl.innerText = 'خطا: ' + data.error;
                msgEl.style.color = 'var(--red)';
            }
        }

        async function submitTg2fa() {
            const password = document.getElementById('tg-2fa').value.trim();
            if (!password) return;
            const msgEl = document.getElementById('auth-status-msg');
            msgEl.innerText = 'در حال اعتبارسنجی رمز ۲FA...';

            const res = await fetch('/api/admin/tg/submit_2fa', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ password })
            });
            const data = await res.json();
            if (data.ok) {
                msgEl.innerText = data.message;
                msgEl.style.color = 'var(--green)';
                fetchStats();
            } else {
                msgEl.innerText = 'خطا: ' + data.error;
                msgEl.style.color = 'var(--red)';
            }
        }

        async function addChannelMapping() {
            const tg = document.getElementById('map-tg').value.trim();
            const msg = document.getElementById('map-msg').value.trim();
            const pass = document.getElementById('map-pass').value.trim();
            if (!tg || !msg) { alert('کانال تلگرام و نام پیام‌رسان الزامی است'); return; }

            await fetch('/api/admin/mappings/add', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ tg_channel: tg, messenger_channel: msg, password: pass })
            });
            document.getElementById('map-tg').value = '';
            document.getElementById('map-msg').value = '';
            document.getElementById('map-pass').value = '';
            fetchMappings();
        }

        async function deleteMapping(idx) {
            await fetch('/api/admin/mappings/delete', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ index: idx })
            });
            fetchMappings();
        }

        window.onload = () => {
            fetchStats();
            fetchMappings();
            setInterval(fetchStats, 3000);
        };
    </script>
</body>
</html>
"""

class ServerAdminApp:
    def __init__(self):
        self.app = web.Application()
        self._setup_routes()

    def _setup_routes(self):
        self.app.router.add_get("/", self.index_handler)
        self.app.router.add_get("/api/admin/stats", self.stats_handler)
        self.app.router.add_get("/api/admin/mappings", self.get_mappings_handler)
        self.app.router.add_post("/api/admin/mappings/add", self.add_mapping_handler)
        self.app.router.add_post("/api/admin/mappings/delete", self.delete_mapping_handler)
        self.app.router.add_post("/api/admin/tg/request_code", self.tg_request_code_handler)
        self.app.router.add_post("/api/admin/tg/submit_code", self.tg_submit_code_handler)
        self.app.router.add_post("/api/admin/tg/submit_2fa", self.tg_submit_2fa_handler)

    async def index_handler(self, request):
        return web.Response(text=SERVER_ADMIN_HTML, content_type="text/html")

    async def stats_handler(self, request):
        stats = GLOBAL_DISK_STORE.stats.copy()
        stats["tg_connected"] = GLOBAL_TELEGRAM_BOT.is_connected
        return web.json_response(stats)

    async def get_mappings_handler(self, request):
        cfg = load_config()
        return web.json_response(cfg.get("kurigram_tracker", {}).get("channels_map", []))

    async def add_mapping_handler(self, request):
        data = await request.json()
        cfg = load_config()
        if "kurigram_tracker" not in cfg:
            cfg["kurigram_tracker"] = {}
        if "channels_map" not in cfg["kurigram_tracker"]:
            cfg["kurigram_tracker"]["channels_map"] = []
        cfg["kurigram_tracker"]["channels_map"].append(data)
        save_config(cfg)
        
        # Register public channel in server store
        m_chan = str(data.get("messenger_channel", "")).strip().lower()
        if m_chan and not data.get("password"):
            GLOBAL_DISK_STORE.register_public_channel(m_chan)

        return web.json_response({"ok": True})

    async def delete_mapping_handler(self, request):
        data = await request.json()
        idx = data.get("index")
        cfg = load_config()
        m_list = cfg.get("kurigram_tracker", {}).get("channels_map", [])
        if 0 <= idx < len(m_list):
            m_list.pop(idx)
            save_config(cfg)
            GLOBAL_DISK_STORE.sync_from_config(cfg)
        return web.json_response({"ok": True})

    async def tg_request_code_handler(self, request):
        data = await request.json()
        api_id = int(data.get("api_id"))
        api_hash = str(data.get("api_hash")).strip()
        phone = str(data.get("phone")).strip()
        res = await GLOBAL_TELEGRAM_BOT.request_code(api_id, api_hash, phone)
        return web.json_response(res)

    async def tg_submit_code_handler(self, request):
        data = await request.json()
        code = str(data.get("code")).strip()
        res = await GLOBAL_TELEGRAM_BOT.submit_code(code)
        return web.json_response(res)

    async def tg_submit_2fa_handler(self, request):
        data = await request.json()
        password = str(data.get("password")).strip()
        res = await GLOBAL_TELEGRAM_BOT.submit_2fa(password)
        return web.json_response(res)
