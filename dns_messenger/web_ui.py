import asyncio
import json
import os
import time
from pathlib import Path
from aiohttp import web
from .config import load_config, save_config, DATA_DIR, RESOLVERS_FILE, load_resolvers, save_resolvers
from .client import DnsTunnelClient
from .crypto import get_message_file_bytes

HTML_INDEX = """<!DOCTYPE html>
<html lang="fa" dir="rtl">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>DNS Messenger | پیام‌رسان امن تونل DNS</title>
    <link href="https://cdn.jsdelivr.net/gh/rastikerdar/vazirmatn@v33.003/Vazirmatn-font-face.css" rel="stylesheet">
    <style>
        :root {
            --bg-dark: #0f141c;
            --bg-panel: #18222d;
            --bg-bubble-self: #2b5278;
            --bg-bubble-peer: #212d3b;
            --accent: #5288c1;
            --accent-green: #4fae67;
            --text-main: #f5f5f5;
            --text-muted: #8e9bb0;
            --border-color: #243242;
            --danger: #e53935;
        }
        * { box-sizing: border-box; margin: 0; padding: 0; font-family: 'Vazirmatn', sans-serif; }
        body { background: var(--bg-dark); color: var(--text-main); height: 100vh; overflow: hidden; display: flex; flex-direction: column; }
        
        /* Top Navigation Header */
        header {
            background: var(--bg-panel);
            border-bottom: 1px solid var(--border-color);
            padding: 8px 16px;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }
        .brand { font-size: 1.15rem; font-weight: bold; display: flex; align-items: center; gap: 8px; }
        .dns-badge {
            background: #113324;
            color: #4cd964;
            font-size: 0.78rem;
            padding: 4px 10px;
            border-radius: 20px;
            border: 1px solid #1f6b3e;
            display: flex;
            align-items: center;
            gap: 6px;
        }
        .dns-dot { width: 8px; height: 8px; background: #4cd964; border-radius: 50%; box-shadow: 0 0 6px #4cd964; }
        .header-actions { display: flex; gap: 8px; align-items: center; }
        button { cursor: pointer; border: none; border-radius: 6px; font-weight: bold; transition: all 0.2s; }
        .btn-primary { background: var(--accent); color: white; padding: 6px 12px; }
        .btn-primary:hover { background: #6199d6; }
        .btn-outline { background: transparent; border: 1px solid var(--border-color); color: var(--text-muted); padding: 6px 12px; }
        .btn-outline:hover { background: var(--bg-dark); color: var(--text-main); }
        
        /* Main Layout */
        .main-container { display: flex; flex: 1; height: calc(100vh - 54px); }
        
        /* Sidebar */
        .sidebar {
            width: 320px;
            background: var(--bg-panel);
            border-left: 1px solid var(--border-color);
            display: flex;
            flex-direction: column;
        }
        .tabs { display: flex; border-bottom: 1px solid var(--border-color); }
        .tab-btn {
            flex: 1; padding: 10px; text-align: center; background: transparent;
            color: var(--text-muted); border-bottom: 2px solid transparent; font-size: 0.9rem;
        }
        .tab-btn.active { color: var(--accent); border-bottom: 2px solid var(--accent); background: rgba(82, 136, 193, 0.08); }
        .chat-list { flex: 1; overflow-y: auto; }
        .chat-item {
            padding: 12px 14px;
            display: flex;
            align-items: center;
            gap: 12px;
            border-bottom: 1px solid rgba(255, 255, 255, 0.03);
            cursor: pointer;
        }
        .chat-item:hover, .chat-item.active { background: rgba(82, 136, 193, 0.15); }
        .chat-avatar {
            width: 42px; height: 42px; border-radius: 50%;
            background: #2a3a4d; display: flex; align-items: center; justify-content: center;
            font-size: 1.2rem; flex-shrink: 0;
        }
        .chat-info { flex: 1; min-width: 0; }
        .chat-name { font-weight: bold; font-size: 0.95rem; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
        .chat-sub { font-size: 0.75rem; color: var(--text-muted); margin-top: 2px; }
        .sidebar-bottom { padding: 12px; border-top: 1px solid var(--border-color); }
        
        /* Chat Area */
        .chat-view { flex: 1; display: flex; flex-direction: column; background: var(--bg-dark); }
        .chat-header {
            padding: 10px 18px;
            background: var(--bg-panel);
            border-bottom: 1px solid var(--border-color);
            display: flex;
            justify-content: space-between;
            align-items: center;
        }
        .crypto-badge {
            background: rgba(79, 174, 103, 0.15);
            color: #62d680;
            padding: 4px 8px;
            border-radius: 6px;
            font-size: 0.75rem;
            display: flex;
            align-items: center;
            gap: 4px;
        }
        .messages-container {
            flex: 1; padding: 20px; overflow-y: auto; display: flex; flex-direction: column; gap: 12px;
        }
        .msg-bubble {
            max-width: 68%;
            padding: 10px 14px;
            border-radius: 12px;
            line-height: 1.5;
            position: relative;
            word-break: break-word;
            font-size: 0.92rem;
        }
        .msg-bubble.self { align-self: flex-start; background: var(--bg-bubble-self); border-bottom-right-radius: 2px; }
        .msg-bubble.peer { align-self: flex-end; background: var(--bg-bubble-peer); border-bottom-left-radius: 2px; }
        .msg-sender { font-size: 0.78rem; font-weight: bold; color: #8ec3eb; margin-bottom: 4px; }
        .msg-time { font-size: 0.7rem; color: rgba(255,255,255,0.45); text-align: left; margin-top: 4px; }
        .msg-file {
            background: rgba(0,0,0,0.25);
            padding: 8px 12px;
            border-radius: 8px;
            margin-top: 6px;
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 12px;
        }
        
        /* Input Bar */
        .chat-input-bar {
            padding: 12px 18px;
            background: var(--bg-panel);
            border-top: 1px solid var(--border-color);
            display: flex;
            gap: 10px;
            align-items: center;
        }
        .input-text {
            flex: 1;
            background: var(--bg-dark);
            border: 1px solid var(--border-color);
            padding: 10px 14px;
            border-radius: 8px;
            color: white;
            font-size: 0.95rem;
            outline: none;
        }
        .input-text:focus { border-color: var(--accent); }
        .btn-file {
            background: transparent; color: var(--text-muted); font-size: 1.4rem; padding: 4px 8px;
        }
        .btn-file:hover { color: var(--text-main); }
        .progress-bar-container {
            display: none;
            height: 4px;
            width: 100%;
            background: var(--bg-dark);
            position: absolute;
            bottom: 60px;
            left: 0;
        }
        .progress-bar { height: 100%; background: var(--accent); width: 0%; transition: width 0.2s; }
        
        /* Modal */
        .modal {
            display: none; position: fixed; inset: 0; background: rgba(0,0,0,0.7);
            align-items: center; justify-content: center; z-index: 100;
        }
        .modal-card {
            background: var(--bg-panel);
            width: 480px;
            border-radius: 10px;
            border: 1px solid var(--border-color);
            padding: 22px;
            display: flex; flex-direction: column; gap: 14px;
        }
        .form-group { display: flex; flex-direction: column; gap: 6px; }
        .form-group label { font-size: 0.85rem; color: var(--text-muted); }
        .form-group input, .form-group textarea {
            background: var(--bg-dark); border: 1px solid var(--border-color);
            padding: 8px 12px; border-radius: 6px; color: white; outline: none;
        }
    </style>
</head>
<body>

    <header>
        <div class="brand">
            <span>🛡️ DNS Messenger</span>
            <div class="dns-badge">
                <div class="dns-dot"></div>
                <span id="resolver-label">DNS: 8.8.8.8</span>
                <span id="latency-label">~0ms</span>
            </div>
        </div>
        <div class="header-actions">
            <button class="btn-outline" onclick="pollMessages()">🔄 همگام‌سازی DNS</button>
            <button class="btn-outline" onclick="openSettings()">⚙️ تنظیمات</button>
        </div>
    </header>

    <div class="main-container">
        <!-- Sidebar -->
        <div class="sidebar">
            <div class="tabs">
                <button class="tab-btn active" id="tab-channels-btn" onclick="switchTab('channels')">📢 کانال‌ها</button>
                <button class="tab-btn" id="tab-direct-btn" onclick="switchTab('direct')">💬 گفتگوی مستقیم</button>
            </div>
            <div class="chat-list" id="chat-list">
                <!-- Dynamically populated -->
            </div>
            <div class="sidebar-bottom">
                <button class="btn-primary" style="width: 100%;" onclick="openAddChatModal()">➕ ایجاد یا عضویت</button>
            </div>
        </div>

        <!-- Chat Area -->
        <div class="chat-view">
            <div class="chat-header">
                <div>
                    <h3 id="current-chat-title">لطفاً یک کانال یا گفتگو را انتخاب کنید</h3>
                    <div id="current-chat-sub" style="font-size: 0.78rem; color: var(--text-muted);">رمزنگاری End-to-End اختصاصی AES-256-GCM</div>
                </div>
                <div class="crypto-badge">
                    <span>🔒</span>
                    <span>رمزنگاری سمت کلاینت فعال است</span>
                </div>
            </div>

            <div class="messages-container" id="messages-box">
                <div style="text-align: center; color: var(--text-muted); margin-top: 50px;">
                    هیچ گفتگویی انتخاب نشده است.
                </div>
            </div>

            <div class="progress-bar-container" id="upload-progress">
                <div class="progress-bar" id="progress-inner"></div>
            </div>

            <div class="chat-input-bar" id="input-bar" style="display: none; position: relative;">
                <input type="file" id="file-selector" style="display: none;" onchange="handleFileSelected()">
                <button class="btn-file" title="ارسال فایل (حداکثر ۲۰ مگابایت)" onclick="document.getElementById('file-selector').click()">📎</button>
                <input type="text" class="input-text" id="message-input" placeholder="پیام خود را بنویسید... (ارسال با Enter)" onkeydown="if(event.key === 'Enter') sendMessage()">
                <button class="btn-primary" id="send-btn" onclick="sendMessage()">ارسال</button>
            </div>
        </div>
    </div>

    <!-- Modal for Create/Join Channel & Chat -->
    <div class="modal" id="add-modal">
        <div class="modal-card">
            <h3 id="modal-title">عضویت یا ساخت کانال</h3>
            <div class="form-group">
                <label id="modal-name-label">نام کانال / شناسه چت:</label>
                <input type="text" id="modal-name-input" placeholder="مثال: news یا dev_group">
            </div>
            <div class="form-group">
                <label id="modal-pass-label">کلید مشترک AES (رمز عبور):</label>
                <input type="password" id="modal-pass-input" placeholder="برای کانال‌های همگانی تلگرام خالی بگذارید">
                <span id="modal-pass-hint" style="font-size: 0.75rem; color: var(--text-muted); margin-top: 2px;">
                    💡 هر فردی که این رمز را وارد کند عضو گروه مشترک شده و می‌تواند پیام بخواند و ارسال کند.
                </span>
            </div>
            <div style="display: flex; gap: 8px; justify-content: flex-end; margin-top: 10px;">
                <button class="btn-outline" onclick="closeModal('add-modal')">انصراف</button>
                <button class="btn-primary" onclick="submitAddChat()">تأیید و ذخیره</button>
            </div>
        </div>
    </div>

    <!-- Modal for Settings -->
    <div class="modal" id="settings-modal">
        <div class="modal-card" style="width: 520px;">
            <h3>تنظیمات کلاینت و تونل DNS</h3>
            <div class="form-group">
                <label>دامنه پایه DNS Tunnel (Base Domain):</label>
                <input type="text" id="cfg-base-domain" placeholder="msg.example.com">
            </div>
            <div class="form-group">
                <div style="display: flex; justify-content: space-between; align-items: center;">
                    <label>فایل اختصاصی رسیورها (resolvers.txt):</label>
                    <button type="button" class="btn-primary" style="font-size: 0.75rem; padding: 3px 8px;" onclick="benchmarkResolvers()">⚡ تست سرعت و رتبه‌بندی</button>
                </div>
                <textarea id="cfg-resolvers-raw" rows="4" style="font-family: monospace; font-size: 0.85rem;" placeholder="1.1.1.1:53&#10;8.8.8.8:53&#10;9.9.9.9:53"></textarea>
                <div id="benchmark-results" style="font-size: 0.78rem; color: #4cd964; display: none; margin-top: 4px;"></div>
            </div>
            <div class="form-group">
                <label>حداکثر حجم مجاز هر فایل ارسالی (مگابایت):</label>
                <input type="number" id="cfg-max-file" value="20" min="1" max="100">
            </div>
            <div class="form-group">
                <label>نام مستعار شما در چت‌ها (Username):</label>
                <input type="text" id="cfg-username" placeholder="Anonymous">
            </div>
            <div style="display: flex; gap: 8px; justify-content: flex-end; margin-top: 10px;">
                <button class="btn-outline" onclick="closeModal('settings-modal')">بستن</button>
                <button class="btn-primary" onclick="saveSettings()">ذخیره تنظیمات</button>
            </div>
        </div>
    </div>

    <script>
        let currentTab = 'channels';
        let currentChat = null;
        let selectedFileBytes = null;
        let selectedFileName = "";

        async function init() {
            await fetchStatus();
            await fetchChats();
            setInterval(fetchStatus, 4000);
            setInterval(pollMessages, 6000);
        }

        async function fetchStatus() {
            try {
                const res = await fetch('/api/status');
                const data = await res.json();
                document.getElementById('resolver-label').innerText = 'DNS: ' + data.current_resolver;
                document.getElementById('latency-label').innerText = '~' + data.last_latency_ms + 'ms';
            } catch(e) {}
        }

        async function fetchChats() {
            try {
                const res = await fetch('/api/chats');
                const data = await res.json();
                const list = document.getElementById('chat-list');
                list.innerHTML = '';
                const items = currentTab === 'channels' ? data.channels : data.direct;
                
                for (const name in items) {
                    const item = items[name];
                    const div = document.createElement('div');
                    div.className = 'chat-item' + (currentChat === name ? ' active' : '');
                    const isPub = currentTab === 'channels' && (item.is_public || !item.password);
                    const icon = currentTab === 'channels' ? (isPub ? '📢' : '🔒') : '👤';
                    const tagLabel = currentTab === 'channels' ? (isPub ? 'همگانی تلگرام' : 'گروه رمزدار') : 'مستقیم';
                    div.innerHTML = `
                        <div class="chat-avatar" onclick="selectChat('${escapeHtml(name)}', {is_public: ${isPub}, password: '${escapeHtml(item.password || '')}'})">${icon}</div>
                        <div class="chat-info" onclick="selectChat('${escapeHtml(name)}', {is_public: ${isPub}, password: '${escapeHtml(item.password || '')}'})">
                            <div class="chat-name">${escapeHtml(item.name)}</div>
                            <div class="chat-sub">${item.messages ? item.messages.length : 0} پیام (${tagLabel})</div>
                        </div>
                        <button class="btn-outline" style="padding:2px 8px; font-size:0.75rem; color:var(--text-muted);" title="حذف" onclick="deleteChat('${currentTab}', '${escapeHtml(name)}', event)">✕</button>
                    `;
                    list.appendChild(div);
                }
            } catch(e) {}
        }

        async function deleteChat(type, name, ev) {
            if (ev) ev.stopPropagation();
            if (!confirm(`آیا از حذف "${name}" مطمئن هستید؟`)) return;
            await fetch('/api/chat/delete', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ type: type, name: name })
            });
            if (currentChat === name) {
                currentChat = null;
                document.getElementById('input-bar').style.display = 'none';
                document.getElementById('current-chat-title').innerText = 'لطفاً یک گفتگو را انتخاب کنید';
                document.getElementById('messages-box').innerHTML = '<div style="text-align: center; color: var(--text-muted); margin-top: 50px;">یک چت یا کانال را انتخاب کنید</div>';
            }
            await fetchChats();
        }

        function switchTab(tab) {
            currentTab = tab;
            document.getElementById('tab-channels-btn').className = 'tab-btn' + (tab === 'channels' ? ' active' : '');
            document.getElementById('tab-direct-btn').className = 'tab-btn' + (tab === 'direct' ? ' active' : '');
            currentChat = null;
            document.getElementById('input-bar').style.display = 'none';
            document.getElementById('current-chat-title').innerText = 'لطفاً یک گفتگو را انتخاب کنید';
            document.getElementById('messages-box').innerHTML = '<div style="text-align: center; color: var(--text-muted); margin-top: 50px;">یک چت یا کانال را انتخاب کنید</div>';
            fetchChats();
        }

        async function selectChat(name, chatInfo) {
            currentChat = name;
            document.getElementById('input-bar').style.display = 'flex';
            const isPub = currentTab === 'channels' && (!chatInfo || chatInfo.is_public || !chatInfo.password);
            const prefix = currentTab === 'channels' ? (isPub ? 'کانال عمومی: ' : 'گروه رمزدار: ') : 'کاربر: ';
            document.getElementById('current-chat-title').innerText = prefix + name;
            document.getElementById('current-chat-sub').innerText = isPub ? 'پخش همگانی از تلگرام (بدون رمز)' : 'رمزنگاری سرتاسری AES-256-GCM';
            await loadMessages();
            fetchChats();
            pollMessages();
        }

        async function loadMessages() {
            if (!currentChat) return;
            const res = await fetch(`/api/messages?type=${currentTab}&target=${encodeURIComponent(currentChat)}`);
            const messages = await res.json();
            const box = document.getElementById('messages-box');
            box.innerHTML = '';

            if (messages.length === 0) {
                box.innerHTML = '<div style="text-align: center; color: var(--text-muted); margin-top: 50px;">هنوز پیامی وجود ندارد یا کلید اشتباه است.</div>';
                return;
            }

            messages.forEach(msg => {
                const bubble = document.createElement('div');
                bubble.className = 'msg-bubble ' + (msg.sender === 'User' ? 'self' : 'peer');
                let fileHtml = '';
                if (msg.has_file && msg.file_name) {
                    const sizeStr = (msg.file_size / 1024).toFixed(1) + ' KB';
                    fileHtml = `
                        <div class="msg-file">
                            <span>📄 ${msg.file_name} (${sizeStr})</span>
                            <a href="/api/file/${currentTab}/${encodeURIComponent(currentChat)}/${msg.msg_id}" download="${msg.file_name}" class="btn-primary" style="text-decoration:none; font-size:0.75rem; padding:4px 8px;">دانلود</a>
                        </div>
                    `;
                }
                const timeStr = msg.timestamp ? new Date(msg.timestamp * 1000).toLocaleTimeString('fa-IR', {hour:'2-digit', minute:'2-digit'}) : '';
                bubble.innerHTML = `
                    <div class="msg-sender">${msg.sender || 'ناشناس'}</div>
                    <div class="msg-text">${escapeHtml(msg.text || '')}</div>
                    ${fileHtml}
                    <div class="msg-time">${timeStr}</div>
                `;
                box.appendChild(bubble);
            });
            box.scrollTop = box.scrollHeight;
        }

        function escapeHtml(text) {
            return String(text).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
        }

        let isPolling = false;
        async function pollMessages() {
            if (isPolling) return;
            isPolling = true;
            try {
                const targetParam = currentChat ? `?target=${encodeURIComponent(currentChat)}` : '';
                await fetch('/api/poll' + targetParam, { method: 'POST' });
                if (currentChat) {
                    await loadMessages();
                }
                fetchChats();
            } catch(e) {}
            finally {
                isPolling = false;
            }
        }

        function handleFileSelected() {
            const input = document.getElementById('file-selector');
            if (input.files.length > 0) {
                const file = input.files[0];
                selectedFileName = file.name;
                const reader = new FileReader();
                reader.onload = function(e) {
                    selectedFileBytes = e.target.result;
                    document.getElementById('message-input').placeholder = `فایل انتخاب شد: ${file.name} (${(file.size/1024).toFixed(1)} KB)`;
                };
                reader.readAsArrayBuffer(file);
            }
        }

        async function sendMessage() {
            if (!currentChat) return;
            const input = document.getElementById('message-input');
            const text = input.value.trim();
            if (!text && !selectedFileBytes) return;

            const sendBtn = document.getElementById('send-btn');
            const progressBar = document.getElementById('upload-progress');
            const progressInner = document.getElementById('progress-inner');
            progressBar.style.display = 'block';
            progressInner.style.width = '30%';

            sendBtn.disabled = true;
            sendBtn.innerText = 'ارسال...';
            input.disabled = true;

            const formData = new FormData();
            formData.append('type', currentTab);
            formData.append('target', currentChat);
            formData.append('text', text);

            if (selectedFileBytes) {
                const blob = new Blob([selectedFileBytes]);
                formData.append('file', blob, selectedFileName);
            }

            input.value = '';
            input.placeholder = 'در حال ارسال بسته‌های DNS...';

            try {
                const res = await fetch('/api/send', { method: 'POST', body: formData });
                const result = await res.json();
                progressInner.style.width = '100%';

                if (!result.ok) {
                    alert('خطا در ارسال: ' + (result.error || 'پکت ارسال نشد'));
                } else {
                    selectedFileBytes = null;
                    selectedFileName = '';
                    await pollMessages();
                }
            } catch(e) {
                alert('خطا در ارسال: ' + e);
            } finally {
                setTimeout(() => { progressBar.style.display = 'none'; progressInner.style.width = '0%'; }, 500);
                sendBtn.disabled = false;
                sendBtn.innerText = 'ارسال';
                input.disabled = false;
                input.placeholder = 'پیام خود را بنویسید... (ارسال با Enter)';
                input.focus();
            }
        }

        function openAddChatModal() {
            document.getElementById('modal-title').innerText = currentTab === 'channels' ? 'عضویت یا ساخت کانال' : 'افزودن گفتگوی مستقیم';
            document.getElementById('modal-name-label').innerText = currentTab === 'channels' ? 'نام کانال:' : 'شناسه کاربر:';
            document.getElementById('modal-pass-label').innerText = currentTab === 'channels' ? 'کلید مشترک AES (رمز عبور):' : 'کلید اشتراکی AES:';
            document.getElementById('modal-name-input').value = '';
            document.getElementById('modal-pass-input').value = '';
            document.getElementById('add-modal').style.display = 'flex';
        }

        async function submitAddChat() {
            const name = document.getElementById('modal-name-input').value.trim();
            const pass = document.getElementById('modal-pass-input').value.trim();
            if (!name) { alert('لطفاً نام کانال یا کاربر را وارد کنید'); return; }
            if (currentTab === 'direct' && !pass) { alert('برای گفتگوی مستقیم، وارد کردن کلید مشترک الزامی است'); return; }

            const endpoint = currentTab === 'channels' ? '/api/channel/join' : '/api/direct/add';
            const res = await fetch(endpoint, {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ name: name, password: pass })
            });
            const data = await res.json();
            if (!data.ok) {
                alert('خطا: ' + (data.error || 'مشکلی رخ داد'));
                return;
            }

            closeModal('add-modal');
            await fetchChats();
            selectChat(name, { name: name, is_public: !pass, password: pass });
        }

        async function openSettings() {
            const res = await fetch('/api/config');
            const cfg = await res.json();
            document.getElementById('cfg-base-domain').value = cfg.base_domain || '';
            document.getElementById('cfg-max-file').value = cfg.max_file_size_mb || 20;

            const resResolvers = await fetch('/api/resolvers');
            const resData = await resResolvers.json();
            document.getElementById('cfg-resolvers-raw').value = resData.raw || '';

            const resChats = await fetch('/api/chats');
            const chatsData = await resChats.json();
            document.getElementById('cfg-username').value = chatsData.username || '';

            document.getElementById('settings-modal').style.display = 'flex';
        }

        async function benchmarkResolvers() {
            const box = document.getElementById('benchmark-results');
            box.style.display = 'block';
            box.innerText = 'در حال ارسال بسته‌های سنجش تأخیر به تمام رسیورها...';
            try {
                const res = await fetch('/api/resolvers/benchmark', { method: 'POST' });
                const data = await res.json();
                let txt = '⚡ سریع‌ترین: ' + data.fastest + ' | ';
                const parts = [];
                for (const r in data.benchmarks) {
                    const lat = data.benchmarks[r];
                    parts.push(r + ' (' + (lat >= 0 ? lat + 'ms' : 'بدون پاسخ') + ')');
                }
                box.innerText = txt + parts.slice(0, 6).join(' - ');
                await fetchStatus();
            } catch(e) {
                box.innerText = 'خطا در بنچمارک: ' + e;
            }
        }

        async function saveSettings() {
            const base_domain = document.getElementById('cfg-base-domain').value.trim();
            const rawResolvers = document.getElementById('cfg-resolvers-raw').value.trim();
            const max_file = parseInt(document.getElementById('cfg-max-file').value) || 20;
            const username = document.getElementById('cfg-username').value.trim();

            // Save resolvers.txt
            await fetch('/api/resolvers', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ raw: rawResolvers })
            });

            const body = {
                base_domain,
                max_file_size_mb: max_file,
                username: username
            };

            await fetch('/api/config', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify(body)
            });

            closeModal('settings-modal');
            alert('تنظیمات با موفقیت ذخیره شد.');
            await fetchStatus();
        }
                body: JSON.stringify({ raw: rawResolvers })
            });

            const body = {
                base_domain,
                max_file_size_mb: max_file,
                kurigram_tracker: {
                    enabled: tg_enabled,
                    api_id: tg_api_id,
                    api_hash: tg_api_hash,
                    channels_map: tg_map
                }
            };

            await fetch('/api/config', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify(body)
            });

            closeModal('settings-modal');
            alert('تنظیمات و لیست رسیورهای DNS با موفقیت ذخیره شد.');
            await fetchStatus();
        }

        function closeModal(id) {
            document.getElementById(id).style.display = 'none';
        }

        window.onload = init;
    </script>
</body>
</html>
"""

class WebApp:
    def __init__(self, dns_client: DnsTunnelClient):
        self.client = dns_client
        self.app = web.Application()
        self._setup_routes()

    def _setup_routes(self):
        self.app.router.add_get("/", self.index_handler)
        self.app.router.add_get("/api/status", self.status_handler)
        self.app.router.add_get("/api/chats", self.chats_handler)
        self.app.router.add_post("/api/channel/join", self.join_channel_handler)
        self.app.router.add_post("/api/direct/add", self.add_direct_handler)
        self.app.router.add_post("/api/chat/delete", self.delete_chat_handler)
        self.app.router.add_get("/api/messages", self.messages_handler)
        self.app.router.add_post("/api/send", self.send_handler)
        self.app.router.add_post("/api/poll", self.poll_handler)
        self.app.router.add_get("/api/file/{chat_type}/{target_name}/{msg_id}", self.file_download_handler)
        self.app.router.add_get("/api/config", self.get_config_handler)
        self.app.router.add_post("/api/config", self.save_config_handler)
        self.app.router.add_get("/api/resolvers", self.get_resolvers_handler)
        self.app.router.add_post("/api/resolvers", self.save_resolvers_handler)
        self.app.router.add_post("/api/resolvers/benchmark", self.benchmark_resolvers_handler)

    async def index_handler(self, request):
        return web.Response(text=HTML_INDEX, content_type="text/html")

    async def status_handler(self, request):
        return web.json_response(self.client.stats)

    async def chats_handler(self, request):
        return web.json_response(self.client.state)

    async def join_channel_handler(self, request):
        data = await request.json()
        name = str(data.get("name", "")).strip().lower()
        password = str(data.get("password", "")).strip()
        if name:
            self.client.join_channel(name, password)
            return web.json_response({"ok": True})
        return web.json_response({"ok": False, "error": "نام کانال نمی‌تواند خالی باشد"}, status=400)

    async def add_direct_handler(self, request):
        data = await request.json()
        name = str(data.get("name", "")).strip().lower()
        password = str(data.get("password", "")).strip()
        if name and password:
            self.client.add_direct_chat(name, password)
            return web.json_response({"ok": True})
        return web.json_response({"ok": False, "error": "نام کاربر و کلید رمزگذاری الزامی است"}, status=400)

    async def delete_chat_handler(self, request):
        data = await request.json()
        chat_type = data.get("type", "channels")
        name = str(data.get("name", "")).strip().lower()
        key = "channels" if chat_type in ("channel", "channels") else "direct"
        if name in self.client.state.get(key, {}):
            del self.client.state[key][name]
            self.client.save_state()
            return web.json_response({"ok": True})
        return web.json_response({"ok": False, "error": "چت یافت نشد"}, status=404)

    async def messages_handler(self, request):
        chat_type = request.query.get("type", "channels")
        target = request.query.get("target", "").strip().lower()
        key = "channels" if chat_type == "channels" else "direct"
        chat_info = self.client.state.get(key, {}).get(target, {})
        messages = chat_info.get("messages", [])
        return web.json_response(messages)

    async def send_handler(self, request):
        reader = await request.multipart()
        chat_type = "channel"
        target = ""
        text = ""
        file_name = ""
        file_bytes = b""

        while True:
            part = await reader.next()
            if part is None:
                break
            if part.name == "type":
                chat_type = (await part.text()).strip()
            elif part.name == "target":
                target = (await part.text()).strip()
            elif part.name == "text":
                text = (await part.text()).strip()
            elif part.name == "file":
                file_name = part.filename or "file.bin"
                file_bytes = await part.read()

        c_type = "channel" if chat_type in ("channel", "channels") else "direct"
        ok, msg = await self.client.send_message(
            chat_type=c_type,
            target_name=target,
            text=text,
            file_name=file_name,
            file_bytes=file_bytes
        )
        return web.json_response({"ok": ok, "error": msg if not ok else ""})

    async def poll_handler(self, request):
        target = request.query.get("target")
        await self.client.poll_all(target_priority=target)
        return web.json_response({"ok": True})

    async def file_download_handler(self, request):
        chat_type = request.match_info["chat_type"]
        target = request.match_info["target_name"].strip().lower()
        msg_id = int(request.match_info["msg_id"])
        key = "channels" if chat_type in ("channel", "channels") else "direct"
        chat_info = self.client.state.get(key, {}).get(target, {})
        for msg in chat_info.get("messages", []):
            if msg.get("msg_id") == msg_id:
                raw_bytes = get_message_file_bytes(msg)
                if raw_bytes:
                    headers = {
                        "Content-Disposition": f'attachment; filename="{msg.get("file_name", "download.bin")}"'
                    }
                    return web.Response(body=raw_bytes, headers=headers, content_type="application/octet-stream")
        return web.Response(status=404, text="File not found")

    async def get_config_handler(self, request):
        return web.json_response(load_config())

    async def save_config_handler(self, request):
        data = await request.json()
        cfg = load_config()
        cfg.update(data)
        save_config(cfg)
        self.client.base_domain = cfg.get("base_domain", self.client.base_domain)
        self.client.max_file_size_mb = cfg.get("max_file_size_mb", self.client.max_file_size_mb)
        return web.json_response({"ok": True})

    async def get_resolvers_handler(self, request):
        content = ""
        target_path = RESOLVERS_FILE if RESOLVERS_FILE.exists() else (DATA_DIR / "resolvers.txt")
        if target_path.exists():
            with open(target_path, "r", encoding="utf-8") as f:
                content = f.read()
        return web.json_response({
            "raw": content,
            "parsed": self.client.resolvers,
            "benchmarks": self.client.resolver_benchmarks
        })

    async def save_resolvers_handler(self, request):
        data = await request.json()
        raw_text = data.get("raw", "")
        with open(RESOLVERS_FILE, "w", encoding="utf-8") as f:
            f.write(raw_text.strip() + "\n")
        self.client.reload_resolvers()
        return web.json_response({"ok": True, "parsed": self.client.resolvers})

    async def benchmark_resolvers_handler(self, request):
        benchmarks = await self.client.benchmark_resolvers()
        return web.json_response({
            "ok": True,
            "benchmarks": benchmarks,
            "fastest": self.client.get_current_resolver()
        })
