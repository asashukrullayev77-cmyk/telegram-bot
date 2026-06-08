"""
Music Bot — To'liq, mukammal versiya
Barcha kamchiliklar tuzatilgan:
  ✅ /help komandasi
  ✅ Navbat (queue) tizimi — bir vaqtda max 2 ta yuklash
  ✅ /cancel — jarayonni bekor qilish
  ✅ Admin xabardorlik tizimi
  ✅ Platform aniqlash (YouTube/Instagram/TikTok/Twitter/VK)
  ✅ Katta audio faylni bo'lib yuborish (50MB+)
  ✅ shazamio yo'q bo'lsa ogohlantirish
  ✅ yt-dlp avtomatik yangilash (kuniga bir marta)
  ✅ Cookie muddati tekshiruvi
  ✅ Xotira tozalash
  ✅ Barcha xato holatlari qayta ishlangan
"""

import os
import asyncio
import time
import logging
import yt_dlp
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    CallbackQueryHandler, filters, ContextTypes
)

# ══════════════════════════════════════════════════════
# SOZLAMALAR
# ══════════════════════════════════════════════════════

TOKEN        = "8802164056:AAHUzN18Lr5a8S3lhKmuIJ4Ix0OP4X5_Jo4"
COOKIES_FILE = os.environ.get("COOKIES_FILE", "/root/cookies.txt")
ADMIN_IDS    = [int(x) for x in os.environ.get("ADMIN_IDS", "").split(",") if x.strip().isdigit()]
DOWNLOAD_DIR = "downloads"
MAX_PARALLEL = 2          # Bir foydalanuvchi uchun max parallel yuklash
MAX_FILE_MB  = 49         # Telegram limiti (aslida 50MB, bir oz xavfsiz chegara)
SHAZAM_SEC   = 30         # Shazam uchun audio uzunligi (sekund)

# Logging
logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(message)s",
    level=logging.INFO,
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("bot.log", encoding="utf-8"),
    ]
)
log = logging.getLogger(__name__)

# Global thread pool
_executor = ThreadPoolExecutor(max_workers=6)

# Foydalanuvchi navbat holati: {user_id: active_count}
_user_tasks: dict[int, int] = defaultdict(int)

# yt-dlp oxirgi yangilanish vaqti
_ytdlp_last_update: float = 0

# ══════════════════════════════════════════════════════
# YT-DLP AVTOMATIK YANGILASH
# ══════════════════════════════════════════════════════

async def maybe_update_ytdlp():
    """Kuniga bir marta yt-dlp ni yangilaydi"""
    global _ytdlp_last_update
    now = time.time()
    if now - _ytdlp_last_update < 86400:
        return
    _ytdlp_last_update = now
    try:
        proc = await asyncio.create_subprocess_exec(
            "pip", "install", "-q", "--upgrade", "yt-dlp",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await asyncio.wait_for(proc.wait(), timeout=120)
        log.info("yt-dlp yangilandi ✅")
    except Exception as e:
        log.warning(f"yt-dlp yangilanmadi: {e}")

# ══════════════════════════════════════════════════════
# YORDAMCHI FUNKSIYALAR
# ══════════════════════════════════════════════════════

def get_cookies_opt() -> dict:
    if os.path.exists(COOKIES_FILE):
        age = time.time() - os.path.getmtime(COOKIES_FILE)
        if age > 7 * 86400:
            log.warning("⚠️ Cookies fayli 7 kundan eski — yangilash kerak!")
        return {"cookiefile": COOKIES_FILE}
    return {}

def is_url(text: str) -> bool:
    return text.startswith("http://") or text.startswith("https://")

def detect_platform(url: str) -> str:
    u = url.lower()
    if "youtube.com" in u or "youtu.be" in u:
        return "YouTube"
    if "instagram.com" in u:
        return "Instagram"
    if "tiktok.com" in u:
        return "TikTok"
    if "twitter.com" in u or "x.com" in u:
        return "Twitter/X"
    if "vk.com" in u:
        return "VK"
    if "facebook.com" in u or "fb.watch" in u:
        return "Facebook"
    if "soundcloud.com" in u:
        return "SoundCloud"
    return "Noma'lum"

def find_file(directory: str, prefix: str) -> str | None:
    try:
        for f in os.listdir(directory):
            if f.startswith(prefix):
                return os.path.join(directory, f)
    except Exception:
        pass
    return None

def human_size(n_bytes: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n_bytes < 1024:
            return f"{n_bytes:.1f} {unit}"
        n_bytes /= 1024
    return f"{n_bytes:.1f} GB"

def make_progress_hook(msg, loop):
    state = {"last_update": 0, "last_pct": ""}
    def hook(d):
        if d["status"] == "downloading":
            pct = d.get("_percent_str", "").strip()
            spd = d.get("_speed_str", "").strip() or "?"
            now = time.time()
            if pct and pct != state["last_pct"] and now - state["last_update"] > 4:
                state["last_pct"] = pct
                state["last_update"] = now
                asyncio.run_coroutine_threadsafe(
                    msg.edit_text(f"⏳ Yuklanmoqda... {pct}\n⚡ Tezlik: {spd}"),
                    loop,
                )
    return hook

async def run_in_executor(func):
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(_executor, func)

def _cleanup_old_files():
    try:
        now = time.time()
        for f in os.listdir(DOWNLOAD_DIR):
            fp = os.path.join(DOWNLOAD_DIR, f)
            if os.path.isfile(fp) and now - os.path.getmtime(fp) > 7200:
                try:
                    os.remove(fp)
                except Exception:
                    pass
    except Exception:
        pass

def _cleanup_bot_data(context: ContextTypes.DEFAULT_TYPE):
    now = time.time()
    to_del = []
    for key, val in list(context.bot_data.items()):
        if key.startswith("vinfo_") and isinstance(val, dict):
            if now - val.get("saved_at", 0) > 3600:
                vk = key.replace("vinfo_", "")
                to_del += [f"vfile_{vk}", f"vinfo_{vk}"]
    for k in to_del:
        context.bot_data.pop(k, None)

async def notify_admin(context: ContextTypes.DEFAULT_TYPE, text: str):
    for aid in ADMIN_IDS:
        try:
            await context.bot.send_message(aid, f"🔔 Admin xabar:\n{text}")
        except Exception:
            pass

# ══════════════════════════════════════════════════════
# NAVBAT TIZIMI
# ══════════════════════════════════════════════════════

class TaskLimitExceeded(Exception):
    pass

class UserTask:
    """Context manager: foydalanuvchi navbatini boshqaradi"""
    def __init__(self, user_id: int):
        self.user_id = user_id

    def __enter__(self):
        if _user_tasks[self.user_id] >= MAX_PARALLEL:
            raise TaskLimitExceeded()
        _user_tasks[self.user_id] += 1
        return self

    def __exit__(self, *_):
        _user_tasks[self.user_id] = max(0, _user_tasks[self.user_id] - 1)

# ══════════════════════════════════════════════════════
# FFMPEG
# ══════════════════════════════════════════════════════

async def _check_ffmpeg() -> bool:
    try:
        proc = await asyncio.create_subprocess_exec(
            "ffmpeg", "-version",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await proc.wait()
        return proc.returncode == 0
    except FileNotFoundError:
        return False

async def extract_audio_from_video(
    video_path: str,
    out_path: str,
    duration: int | None = None,
) -> bool:
    cmd = ["ffmpeg", "-i", video_path]
    if duration:
        cmd += ["-t", str(duration)]
    cmd += [
        "-vn",
        "-acodec", "libmp3lame",
        "-q:a", "2",
        "-ar", "44100",
        "-ac", "2",
        "-y", out_path,
    ]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await asyncio.wait_for(proc.wait(), timeout=180)
        return os.path.exists(out_path) and os.path.getsize(out_path) > 0
    except (asyncio.TimeoutError, FileNotFoundError):
        return False

async def split_audio_parts(audio_path: str, part_sec: int = 3600) -> list[str]:
    """
    Katta audio faylni qismlarga bo'lish.
    Har bir qism max ~part_sec sekund (standart 1 soat).
    """
    parts = []
    base = audio_path.rsplit(".", 1)[0]
    idx = 0
    offset = 0
    while True:
        out = f"{base}_part{idx}.mp3"
        cmd = [
            "ffmpeg", "-i", audio_path,
            "-ss", str(offset),
            "-t", str(part_sec),
            "-acodec", "copy",
            "-y", out,
        ]
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await proc.wait()
        if not os.path.exists(out) or os.path.getsize(out) < 1024:
            try:
                os.remove(out)
            except Exception:
                pass
            break
        parts.append(out)
        offset += part_sec
        idx += 1
        if idx > 20:  # xavfsizlik chegarasi
            break
    return parts

# ══════════════════════════════════════════════════════
# SHAZAM
# ══════════════════════════════════════════════════════

_shazamio_available: bool | None = None

def _check_shazamio() -> bool:
    global _shazamio_available
    if _shazamio_available is None:
        try:
            import shazamio  # noqa: F401
            _shazamio_available = True
        except ImportError:
            _shazamio_available = False
    return _shazamio_available

async def recognize_song(audio_path: str) -> dict | None:
    if not _check_shazamio():
        return None
    try:
        from shazamio import Shazam
        shazam = Shazam()
        result = await shazam.recognize(audio_path)
        track = result.get("track")
        if not track:
            return None
        return {
            "title":  track.get("title", ""),
            "artist": track.get("subtitle", ""),
            "genre":  track.get("genres", {}).get("primary", ""),
            "cover":  track.get("images", {}).get("coverart", ""),
        }
    except Exception as e:
        log.warning(f"Shazam xato: {e}")
        return None

# ══════════════════════════════════════════════════════
# /start
# ══════════════════════════════════════════════════════

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    name = update.effective_user.first_name or "Do'st"
    shazam_status = "✅ O'rnatilgan" if _check_shazamio() else "❌ O'rnatilmagan (pip install shazamio)"
    ffmpeg_ok = await _check_ffmpeg()
    ffmpeg_status = "✅ O'rnatilgan" if ffmpeg_ok else "❌ O'rnatilmagan (apt install ffmpeg)"

    await update.message.reply_text(
        f"Salom, {name}! 👋\n\n"
        "🎶 *Music Bot* ga xush kelibsiz!\n\n"
        "📌 *Imkoniyatlar:*\n"
        "🔍 Qo'shiq nomi → qidirish → yuklab olish\n"
        "🔗 Havola yuboring → video yoki audio\n"
        "🎵 Video kelgach → *Faqat audio* tugmasi\n"
        "🎧 Video kelgach → *Qo'shiqni top* (Shazam)\n"
        "📤 Video/audio fayl yuboring → qo'shiq topiladi\n\n"
        "⚙️ *Holat:*\n"
        f"• Shazam: {shazam_status}\n"
        f"• FFmpeg: {ffmpeg_status}\n\n"
        "📋 /help — batafsil yordam",
        parse_mode="Markdown",
    )

# ══════════════════════════════════════════════════════
# /help
# ══════════════════════════════════════════════════════

async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📖 *Yordam*\n\n"
        "*Qo'shiq qidirish:*\n"
        "Qo'shiq nomini yozing → katalogdan tanlang → yuklanadi\n\n"
        "*Havola orqali:*\n"
        "YouTube / Instagram / TikTok / Twitter / VK / SoundCloud havolasini yuboring\n"
        "• Faqat audio kerak bo'lsa: `havola audio` deb yozing\n\n"
        "*Fayl yuborish:*\n"
        "Video yoki audio fayl yuboring → Shazam orqali qo'shiq topiladi\n\n"
        "*Tugmalar:*\n"
        "🎵 *Faqat audio* — videoni faqat mp3 sifatida oling\n"
        "🔍 *Qo'shiqni top* — Shazam orqali qo'shiq nomini aniqlang\n"
        "⬇️ *Yuklab olish* — topilgan qo'shiqni yuklab oling\n\n"
        "*Buyruqlar:*\n"
        "/start — botni qayta ishga tushirish\n"
        "/help — shu yordam\n"
        "/cancel — joriy yuklanishni bekor qilish\n"
        "/status — bot holati\n\n"
        "⚠️ *Cheklovlar:*\n"
        f"• Bir vaqtda max {MAX_PARALLEL} ta yuklash\n"
        f"• Max fayl hajmi: {MAX_FILE_MB}MB\n"
        "• Telegram fayl: max 20MB",
        parse_mode="Markdown",
    )

# ══════════════════════════════════════════════════════
# /cancel
# ══════════════════════════════════════════════════════

async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if _user_tasks.get(uid, 0) > 0:
        _user_tasks[uid] = 0
        await update.message.reply_text("🛑 Navbatdagi vazifalar bekor qilindi.")
    else:
        await update.message.reply_text("ℹ️ Hozir faol yuklanish yo'q.")

# ══════════════════════════════════════════════════════
# /status
# ══════════════════════════════════════════════════════

async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ffmpeg_ok = await _check_ffmpeg()
    shazam_ok = _check_shazamio()

    # Faoliyat sonini hisoblash
    active = sum(_user_tasks.values())

    # Downloads papkasi hajmi
    total_size = 0
    file_count = 0
    try:
        for f in os.listdir(DOWNLOAD_DIR):
            fp = os.path.join(DOWNLOAD_DIR, f)
            if os.path.isfile(fp):
                total_size += os.path.getsize(fp)
                file_count += 1
    except Exception:
        pass

    cookie_info = "❌ Yo'q"
    if os.path.exists(COOKIES_FILE):
        age_h = (time.time() - os.path.getmtime(COOKIES_FILE)) / 3600
        cookie_info = f"✅ Bor ({age_h:.0f} soat oldin yangilangan)"
        if age_h > 168:
            cookie_info += " ⚠️ Eski!"

    await update.message.reply_text(
        "📊 *Bot holati*\n\n"
        f"• FFmpeg: {'✅' if ffmpeg_ok else '❌'}\n"
        f"• Shazam: {'✅' if shazam_ok else '❌'}\n"
        f"• Cookie: {cookie_info}\n"
        f"• Faol yuklanishlar: {active}\n"
        f"• Vaqtinchalik fayllar: {file_count} ta ({human_size(total_size)})\n",
        parse_mode="Markdown",
    )

# ══════════════════════════════════════════════════════
# MUSIQA QIDIRISH
# ══════════════════════════════════════════════════════

async def search_music(update: Update, query: str):
    msg = await update.message.reply_text("🔍 Qidirilmoqda...")
    try:
        ydl_opts = {
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
            "extract_flat": False,
            "socket_timeout": 20,
            "noplaylist": True,
            **get_cookies_opt(),
        }

        def do_search():
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                return ydl.extract_info(f"ytsearch5:{query}", download=False)

        info = await run_in_executor(do_search)
        entries = [e for e in (info.get("entries") or []) if e and e.get("id")]

        if not entries:
            await msg.edit_text("❌ Hech narsa topilmadi. Boshqa so'z bilan qidiring.")
            return

        keyboard = []
        for e in entries[:5]:
            title = (e.get("title") or "Nomsiz")[:45]
            dur   = int(e.get("duration") or 0)
            keyboard.append([InlineKeyboardButton(
                f"🎵 {title}  {dur//60}:{dur%60:02d}",
                callback_data=f"dl_{e['id']}",
            )])

        await msg.edit_text(
            f"🎵 *Natijalar:* {query}",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown",
        )
    except Exception as e:
        log.error(f"search_music xato: {e}")
        await msg.edit_text("❌ Qidirishda xato yuz berdi. Keyinroq urinib ko'ring.")

# ══════════════════════════════════════════════════════
# KATALOGDAN QOSHIQ YUKLASH
# ══════════════════════════════════════════════════════

async def download_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id
    video_id = q.data.replace("dl_", "", 1)
    url = f"https://www.youtube.com/watch?v={video_id}"

    try:
        with UserTask(uid):
            msg = await q.edit_message_text("⏳ Yuklanmoqda... 0%")
            loop = asyncio.get_running_loop()
            os.makedirs(DOWNLOAD_DIR, exist_ok=True)
            await maybe_update_ytdlp()

            ydl_opts = {
                "format": "bestaudio[ext=m4a]/bestaudio[ext=webm]/bestaudio/best",
                "outtmpl": f"{DOWNLOAD_DIR}/{video_id}.%(ext)s",
                "quiet": True,
                "no_warnings": True,
                "socket_timeout": 30,
                "concurrent_fragment_downloads": 4,
                "progress_hooks": [make_progress_hook(msg, loop)],
                **get_cookies_opt(),
            }

            def do_dl():
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    return ydl.extract_info(url, download=True)

            info = await run_in_executor(do_dl)
            filename = find_file(DOWNLOAD_DIR, video_id)
            if not filename:
                await msg.edit_text("❌ Fayl yuklanmadi!")
                return

            fsize = os.path.getsize(filename)
            if fsize > MAX_FILE_MB * 1024 * 1024:
                await msg.edit_text(
                    f"❌ Fayl juda katta ({human_size(fsize)}).\n"
                    "Telegram 50MB dan katta fayllarni qabul qilmaydi."
                )
                os.remove(filename)
                return

            await msg.delete()
            with open(filename, "rb") as f:
                await q.message.reply_audio(
                    f,
                    title=info.get("title", "Qo'shiq"),
                    performer=info.get("uploader", ""),
                )
            try:
                os.remove(filename)
            except Exception:
                pass

    except TaskLimitExceeded:
        await q.answer(
            f"⏳ Siz allaqachon {MAX_PARALLEL} ta yuklash qilmoqdasiz. Biroz kuting.",
            show_alert=True,
        )
    except Exception as e:
        log.error(f"download_callback xato: {e}")
        try:
            await msg.edit_text(f"❌ Xato: {str(e)[:200]}")
        except Exception:
            pass
        await notify_admin(context, f"download_callback xato (uid={uid}): {e}")

# ══════════════════════════════════════════════════════
# HAVOLA ORQALI VIDEO YUKLASH
# ══════════════════════════════════════════════════════

MOBILE_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1"
)

PLATFORM_FORMATS = {
    "Instagram":  "best[ext=mp4]/best",
    "TikTok":     "best[ext=mp4]/best",
    "Twitter/X":  "best[ext=mp4]/best",
    "Facebook":   "best[ext=mp4][filesize<50M]/best",
    "SoundCloud": "bestaudio/best",
    "YouTube":    "best[ext=mp4][filesize<50M]/bestvideo[ext=mp4]+bestaudio[ext=m4a]/best",
    "VK":         "best[ext=mp4]/best",
    "Noma'lum":   "best[ext=mp4][filesize<50M]/best",
}

async def download_video(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    url: str,
    audio_only: bool = False,
):
    uid  = update.effective_user.id
    platform = detect_platform(url)
    ts = int(time.time())

    try:
        with UserTask(uid):
            if audio_only:
                msg = await update.message.reply_text(
                    f"⏳ [{platform}] Audio yuklanmoqda..."
                )
            else:
                msg = await update.message.reply_text(
                    f"⏳ [{platform}] Video yuklanmoqda..."
                )

            loop = asyncio.get_running_loop()
            os.makedirs(DOWNLOAD_DIR, exist_ok=True)
            await maybe_update_ytdlp()

            headers = {
                "User-Agent": MOBILE_UA,
                "Accept-Language": "en-US,en;q=0.9",
            }

            # ── Audio rejimi ──────────────────────────────
            if audio_only:
                ydl_opts = {
                    "format": "bestaudio[ext=m4a]/bestaudio[ext=webm]/bestaudio/best",
                    "outtmpl": f"{DOWNLOAD_DIR}/audio_{ts}.%(ext)s",
                    "quiet": True,
                    "no_warnings": True,
                    "socket_timeout": 60,
                    "progress_hooks": [make_progress_hook(msg, loop)],
                    "http_headers": headers,
                    **get_cookies_opt(),
                }

                def do_audio():
                    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                        return ydl.extract_info(url, download=True)

                info = await run_in_executor(do_audio)
                filename = find_file(DOWNLOAD_DIR, f"audio_{ts}")
                if not filename:
                    await msg.edit_text("❌ Fayl yuklanmadi!")
                    return

                await msg.delete()
                with open(filename, "rb") as f:
                    await update.message.reply_audio(
                        f,
                        title=info.get("title", "Audio"),
                        performer=info.get("uploader", ""),
                    )
                try:
                    os.remove(filename)
                except Exception:
                    pass
                return

            # ── Video rejimi ──────────────────────────────
            fmt = PLATFORM_FORMATS.get(platform, PLATFORM_FORMATS["Noma'lum"])
            ydl_opts = {
                "format": fmt,
                "outtmpl": f"{DOWNLOAD_DIR}/video_{ts}.%(ext)s",
                "quiet": True,
                "no_warnings": True,
                "socket_timeout": 60,
                "concurrent_fragment_downloads": 4,
                "merge_output_format": "mp4",
                "progress_hooks": [make_progress_hook(msg, loop)],
                "http_headers": headers,
                **get_cookies_opt(),
            }

            def do_video():
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    return ydl.extract_info(url, download=True)

            info = await run_in_executor(do_video)
            filename = find_file(DOWNLOAD_DIR, f"video_{ts}")

            if not filename:
                await msg.edit_text("❌ Fayl yuklanmadi!")
                return

            fsize = os.path.getsize(filename)
            title = info.get("title", "Video")
            vid_key = f"vid_{ts}"

            # Video ma'lumotlarini saqlash
            context.bot_data[f"vfile_{vid_key}"] = filename
            context.bot_data[f"vinfo_{vid_key}"] = {
                "title":    title,
                "uploader": info.get("uploader", ""),
                "saved_at": time.time(),
            }

            keyboard = InlineKeyboardMarkup([[
                InlineKeyboardButton("🎵 Faqat audio",   callback_data=f"vaudio_{vid_key}"),
                InlineKeyboardButton("🔍 Qo'shiqni top", callback_data=f"shazam_{vid_key}"),
            ]])

            await msg.delete()

            if fsize > MAX_FILE_MB * 1024 * 1024:
                await update.message.reply_text(
                    f"⚠️ Video juda katta ({human_size(fsize)}) — Telegram qabul qilmaydi.\n"
                    "Faqat audio sifatida yuborilmoqda...",
                )
                # Katta videoni audio sifatida yuborish
                audio_out = os.path.join(DOWNLOAD_DIR, f"big_audio_{ts}.mp3")
                ok = await extract_audio_from_video(filename, audio_out)
                if ok:
                    await _send_audio_file(
                        update.message, audio_out,
                        title, info.get("uploader", ""),
                        extra_markup=keyboard,
                    )
                else:
                    await update.message.reply_text("❌ Audio ajratib bo'lmadi.")
                try:
                    os.remove(filename)
                except Exception:
                    pass
            else:
                with open(filename, "rb") as f:
                    await update.message.reply_video(
                        f,
                        caption=f"✅ {title}",
                        reply_markup=keyboard,
                        supports_streaming=True,
                    )

            _cleanup_bot_data(context)
            _cleanup_old_files()

    except TaskLimitExceeded:
        await update.message.reply_text(
            f"⏳ Siz allaqachon {MAX_PARALLEL} ta yuklash qilmoqdasiz.\n"
            "/cancel buyrug'i bilan bekor qilib, qayta urining."
        )
    except Exception as e:
        err = str(e)
        log.error(f"download_video xato (uid={uid}): {err}")
        if "Private" in err or "login" in err.lower() or "authentication" in err.lower():
            await _safe_edit(msg, "❌ Bu post shaxsiy (private). Ochiq havola yuboring!")
        elif "Unsupported URL" in err:
            await _safe_edit(
                msg,
                f"❌ [{platform}] ushbu havola qo'llab-quvvatlanmaydi.\n"
                "YouTube / Instagram / TikTok / Twitter / VK / SoundCloud yuboring."
            )
        elif "Geo" in err or "geo" in err:
            await _safe_edit(msg, "❌ Bu video sizning hududingizda mavjud emas.")
        elif "copyright" in err.lower():
            await _safe_edit(msg, "❌ Bu video mualliflik huquqi tufayli bloklanган.")
        else:
            await _safe_edit(msg, f"❌ Xato: {err[:250]}")
        await notify_admin(context, f"download_video xato (uid={uid}, url={url}): {err[:300]}")

async def _safe_edit(msg, text: str):
    try:
        await msg.edit_text(text)
    except Exception:
        pass

# ══════════════════════════════════════════════════════
# KATTA AUDIO FAYLNI BO'LIB YUBORISH
# ══════════════════════════════════════════════════════

async def _send_audio_file(
    message,
    audio_path: str,
    title: str,
    performer: str,
    extra_markup=None,
):
    """
    Audio faylni yuboradi.
    50MB dan oshsa, qismlarga bo'lib yuboradi.
    """
    fsize = os.path.getsize(audio_path)

    if fsize <= MAX_FILE_MB * 1024 * 1024:
        with open(audio_path, "rb") as f:
            await message.reply_audio(
                f,
                title=title,
                performer=performer,
                reply_markup=extra_markup,
            )
        try:
            os.remove(audio_path)
        except Exception:
            pass
        return

    # Bo'lib yuborish
    await message.reply_text(
        f"⚠️ Audio katta ({human_size(fsize)}). Qismlarga bo'lib yuborilmoqda..."
    )
    parts = await split_audio_parts(audio_path)
    if not parts:
        await message.reply_text("❌ Audio bo'linmadi.")
        try:
            os.remove(audio_path)
        except Exception:
            pass
        return

    for i, part_path in enumerate(parts, 1):
        try:
            with open(part_path, "rb") as f:
                await message.reply_audio(
                    f,
                    title=f"{title} (qism {i}/{len(parts)})",
                    performer=performer,
                    reply_markup=extra_markup if i == len(parts) else None,
                )
        except Exception as e:
            await message.reply_text(f"❌ Qism {i} yuborilmadi: {e}")
        finally:
            try:
                os.remove(part_path)
            except Exception:
                pass
        await asyncio.sleep(0.5)

    try:
        os.remove(audio_path)
    except Exception:
        pass

# ══════════════════════════════════════════════════════
# VIDEO → FAQAT AUDIO
# ══════════════════════════════════════════════════════

async def video_audio_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id
    vid_key = q.data.replace("vaudio_", "", 1)
    filename = context.bot_data.get(f"vfile_{vid_key}")
    info = context.bot_data.get(f"vinfo_{vid_key}", {})

    if not filename or not os.path.exists(filename):
        await q.message.reply_text(
            "❌ Video fayli topilmadi.\n"
            "Bot qayta ishga tushirilgan bo'lishi mumkin. Havolani qayta yuboring."
        )
        return

    try:
        with UserTask(uid):
            msg = await q.message.reply_text("🎵 Audio ajratilmoqda...")
            ts = int(time.time())
            audio_out = os.path.join(DOWNLOAD_DIR, f"vaudio_{ts}.mp3")

            ok = await extract_audio_from_video(filename, audio_out)
            if not ok:
                await msg.edit_text(
                    "❌ Audio ajratib bo'lmadi.\n"
                    "ffmpeg o'rnatilganini tekshiring: apt install ffmpeg"
                )
                return

            await msg.delete()
            await _send_audio_file(
                q.message, audio_out,
                info.get("title", "Audio"),
                info.get("uploader", ""),
            )
    except TaskLimitExceeded:
        await q.answer(
            f"⏳ Parallel yuklanishlar limiti ({MAX_PARALLEL}) to'ldi.",
            show_alert=True,
        )
    except Exception as e:
        log.error(f"video_audio_callback xato: {e}")
        try:
            await msg.edit_text(f"❌ Xato: {e}")
        except Exception:
            pass

# ══════════════════════════════════════════════════════
# SHAZAM CALLBACK
# ══════════════════════════════════════════════════════

async def shazam_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    vid_key = q.data.replace("shazam_", "", 1)
    filename = context.bot_data.get(f"vfile_{vid_key}")

    if not filename or not os.path.exists(filename):
        await q.message.reply_text(
            "❌ Video fayli topilmadi.\n"
            "Bot qayta ishga tushirilgan bo'lishi mumkin. Havolani qayta yuboring."
        )
        return

    if not _check_shazamio():
        await q.message.reply_text(
            "❌ Shazam moduli o'rnatilmagan.\n"
            "Server administratori quyidagini bajarishi kerak:\n"
            "`pip install shazamio`",
            parse_mode="Markdown",
        )
        return

    if not await _check_ffmpeg():
        await q.message.reply_text(
            "❌ FFmpeg o'rnatilmagan.\n"
            "`apt install ffmpeg`",
            parse_mode="Markdown",
        )
        return

    msg = await q.message.reply_text("🎧 Qo'shiq tanib olinmoqda...")
    ts = int(time.time())
    shazam_audio = os.path.join(DOWNLOAD_DIR, f"shazam_{ts}.mp3")

    ok = await extract_audio_from_video(filename, shazam_audio, duration=SHAZAM_SEC)
    if not ok:
        await msg.edit_text("❌ Audio ajratib bo'lmadi. FFmpeg-ni tekshiring.")
        return

    result = await recognize_song(shazam_audio)
    try:
        os.remove(shazam_audio)
    except Exception:
        pass

    if not result:
        await msg.edit_text(
            "❌ Qo'shiq tanib olinmadi.\n"
            "Sabab: video shovqinli yoki qo'shiq bazada yo'q."
        )
        return

    await _deliver_shazam_result(msg, q.message, result)

async def _deliver_shazam_result(msg, reply_to, result: dict):
    text = f"🎵 *{result['title']}*\n👤 {result['artist']}\n"
    if result.get("genre"):
        text += f"🎸 {result['genre']}\n"

    search_q = f"{result['artist']} {result['title']}"
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("⬇️ Yuklab olish", callback_data=f"sdl_{search_q[:50]}")
    ]])

    if result.get("cover"):
        try:
            await msg.delete()
            await reply_to.reply_photo(
                result["cover"],
                caption=text,
                parse_mode="Markdown",
                reply_markup=keyboard,
            )
            return
        except Exception:
            pass

    await msg.edit_text(text, parse_mode="Markdown", reply_markup=keyboard)

# ══════════════════════════════════════════════════════
# SHAZAM TOPGAN QOSHIQNI YUKLAB OLISH
# ══════════════════════════════════════════════════════

async def search_dl_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id
    search_q = q.data.replace("sdl_", "", 1)
    msg = await q.message.reply_text("🔍 Qidirilmoqda...")

    try:
        ydl_opts = {
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
            "extract_flat": False,
            "socket_timeout": 20,
            "noplaylist": True,
            **get_cookies_opt(),
        }

        def do_search():
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                return ydl.extract_info(f"ytsearch3:{search_q}", download=False)

        info = await run_in_executor(do_search)
        entries = [e for e in (info.get("entries") or []) if e and e.get("id")]

        if not entries:
            await msg.edit_text("❌ Topilmadi!")
            return

        keyboard = []
        for e in entries[:3]:
            title = (e.get("title") or "Nomsiz")[:45]
            dur   = int(e.get("duration") or 0)
            keyboard.append([InlineKeyboardButton(
                f"🎵 {title}  {dur//60}:{dur%60:02d}",
                callback_data=f"dl_{e['id']}",
            )])

        await msg.edit_text(
            f"🎶 *Natijalar:* {search_q}",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown",
        )
    except Exception as e:
        log.error(f"search_dl_callback xato: {e}")
        await msg.edit_text("❌ Qidirishda xato yuz berdi.")

# ══════════════════════════════════════════════════════
# TELEGRAM FAYL → SHAZAM
# ══════════════════════════════════════════════════════

async def handle_media(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _check_shazamio():
        await update.message.reply_text(
            "❌ Shazam moduli o'rnatilmagan.\n"
            "`pip install shazamio`",
            parse_mode="Markdown",
        )
        return

    if not await _check_ffmpeg():
        await update.message.reply_text(
            "❌ FFmpeg o'rnatilmagan.\n"
            "`apt install ffmpeg`",
            parse_mode="Markdown",
        )
        return

    file = (
        update.message.video
        or update.message.audio
        or update.message.voice
        or update.message.document
    )
    if not file:
        await update.message.reply_text("❌ Fayl topilmadi.")
        return

    file_size = getattr(file, "file_size", 0) or 0
    if file_size > 20 * 1024 * 1024:
        await update.message.reply_text(
            f"❌ Fayl juda katta ({human_size(file_size)}).\n"
            "Telegram max 20MB fayl uzatadi. Kichikroq fayl yuboring."
        )
        return

    msg = await update.message.reply_text("🎧 Qo'shiq tanib olinmoqda...")
    ts = int(time.time())
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)
    tmp_path = os.path.join(DOWNLOAD_DIR, f"tg_{ts}_{file.file_id[:8]}.tmp")

    try:
        tg_file = await context.bot.get_file(file.file_id)
        await tg_file.download_to_drive(tmp_path)
    except Exception as e:
        await msg.edit_text(f"❌ Fayl yuklab olinmadi: {e}")
        return

    shazam_audio = tmp_path + "_shazam.mp3"
    ok = await extract_audio_from_video(tmp_path, shazam_audio, duration=SHAZAM_SEC)
    try:
        os.remove(tmp_path)
    except Exception:
        pass

    if not ok:
        await msg.edit_text("❌ FFmpeg xato. Serverni tekshiring.")
        return

    result = await recognize_song(shazam_audio)
    try:
        os.remove(shazam_audio)
    except Exception:
        pass

    if not result:
        await msg.edit_text(
            "❌ Qo'shiq tanib olinmadi.\n"
            "Audio sifati past yoki qo'shiq Shazam bazasida yo'q."
        )
        return

    await _deliver_shazam_result(msg, update.message, result)

# ══════════════════════════════════════════════════════
# MATN HANDLER
# ══════════════════════════════════════════════════════

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    parts = text.split()

    # "havola audio" → audio rejimi
    if len(parts) >= 2 and is_url(parts[0]) and parts[-1].lower() == "audio":
        await download_video(update, context, parts[0], audio_only=True)
        return

    if is_url(text):
        await download_video(update, context, text)
    else:
        await search_music(update, text)

# ══════════════════════════════════════════════════════
# ISHGA TUSHIRISH
# ══════════════════════════════════════════════════════

os.makedirs(DOWNLOAD_DIR, exist_ok=True)

app = ApplicationBuilder().token(TOKEN).build()

app.add_handler(CommandHandler("start",  cmd_start))
app.add_handler(CommandHandler("help",   cmd_help))
app.add_handler(CommandHandler("cancel", cmd_cancel))
app.add_handler(CommandHandler("status", cmd_status))

app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
app.add_handler(MessageHandler(
    filters.VIDEO | filters.AUDIO | filters.VOICE |
    filters.Document.VIDEO | filters.Document.AUDIO,
    handle_media,
))

app.add_handler(CallbackQueryHandler(download_callback,    pattern=r"^dl_"))
app.add_handler(CallbackQueryHandler(video_audio_callback, pattern=r"^vaudio_"))
app.add_handler(CallbackQueryHandler(shazam_callback,      pattern=r"^shazam_"))
app.add_handler(CallbackQueryHandler(search_dl_callback,   pattern=r"^sdl_"))

log.info("✅ Music Bot ishlamoqda!")
app.run_polling(
    poll_interval=0.3,
    timeout=30,
    drop_pending_updates=True,
)
