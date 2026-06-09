import os
import asyncio
import time
import logging
import shutil
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
MAX_PARALLEL = 2
MAX_FILE_MB  = 49
SHAZAM_SEC   = 30

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(message)s",
    level=logging.INFO,
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("bot.log", encoding="utf-8"),
    ]
)
log = logging.getLogger(__name__)

_executor = ThreadPoolExecutor(max_workers=6)
_user_tasks: dict[int, int] = defaultdict(int)
_ytdlp_last_update: float = 0

# ══════════════════════════════════════════════════════
# YOUTUBE BOT BLOKIDAN QOCHISH UCHUN BAZAVIY SOZLAMALAR
# ══════════════════════════════════════════════════════

# Barcha yt-dlp so'rovlarda ishlatiladi
YDL_BASE = {
    "extractor_args": {
        "youtube": {
            "player_client": ["ios", "web"],
        }
    },
}

# ══════════════════════════════════════════════════════
# FFMPEG YO'LI — bir marta aniqlanadi
# ══════════════════════════════════════════════════════

def _find_ffmpeg() -> str:
    found = shutil.which("ffmpeg")
    if found:
        return found
    for p in ["/usr/bin/ffmpeg", "/usr/local/bin/ffmpeg",
              "/opt/homebrew/bin/ffmpeg", "/snap/bin/ffmpeg"]:
        if os.path.isfile(p):
            return p
    return "ffmpeg"

FFMPEG = _find_ffmpeg()
log.info(f"FFmpeg: {FFMPEG}")

# ══════════════════════════════════════════════════════
# YT-DLP YANGILASH
# ══════════════════════════════════════════════════════

async def maybe_update_ytdlp():
    global _ytdlp_last_update
    if time.time() - _ytdlp_last_update < 86400:
        return
    _ytdlp_last_update = time.time()
    try:
        proc = await asyncio.create_subprocess_exec(
            "pip", "install", "-q", "--upgrade", "yt-dlp",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await asyncio.wait_for(proc.wait(), timeout=120)
        log.info("yt-dlp yangilandi")
    except Exception as e:
        log.warning(f"yt-dlp yangilanmadi: {e}")

# ══════════════════════════════════════════════════════
# YORDAMCHILAR
# ══════════════════════════════════════════════════════

def get_cookies_opt() -> dict:
    if os.path.exists(COOKIES_FILE):
        return {"cookiefile": COOKIES_FILE}
    return {}

def is_url(text: str) -> bool:
    return text.startswith("http://") or text.startswith("https://")

def detect_platform(url: str) -> str:
    u = url.lower()
    if "youtube.com" in u or "youtu.be" in u: return "YouTube"
    if "instagram.com" in u:                   return "Instagram"
    if "tiktok.com" in u:                      return "TikTok"
    if "twitter.com" in u or "x.com" in u:     return "Twitter/X"
    if "vk.com" in u:                          return "VK"
    if "facebook.com" in u or "fb.watch" in u: return "Facebook"
    if "soundcloud.com" in u:                  return "SoundCloud"
    return "Video"

def find_file(directory: str, prefix: str) -> str | None:
    try:
        for f in os.listdir(directory):
            if f.startswith(prefix):
                return os.path.join(directory, f)
    except Exception:
        pass
    return None

def human_size(n: int) -> str:
    for u in ("B", "KB", "MB", "GB"):
        if n < 1024: return f"{n:.1f} {u}"
        n /= 1024
    return f"{n:.1f} GB"

def make_progress_hook(msg, loop):
    state = {"t": 0, "p": ""}
    def hook(d):
        if d["status"] == "downloading":
            pct = d.get("_percent_str", "").strip()
            spd = d.get("_speed_str", "").strip() or "?"
            now = time.time()
            if pct and pct != state["p"] and now - state["t"] > 4:
                state["p"] = pct
                state["t"] = now
                asyncio.run_coroutine_threadsafe(
                    msg.edit_text(f"⏳ Yuklanmoqda... {pct}\n⚡ Tezlik: {spd}"), loop
                )
    return hook

async def run_in_executor(func):
    return await asyncio.get_running_loop().run_in_executor(_executor, func)

def cleanup_files():
    try:
        now = time.time()
        for f in os.listdir(DOWNLOAD_DIR):
            fp = os.path.join(DOWNLOAD_DIR, f)
            if os.path.isfile(fp) and now - os.path.getmtime(fp) > 7200:
                try: os.remove(fp)
                except: pass
    except: pass

def cleanup_bot_data(context):
    now = time.time()
    for key in [k for k in context.bot_data if k.startswith("vinfo_")]:
        if now - context.bot_data[key].get("saved_at", 0) > 3600:
            vk = key[6:]
            context.bot_data.pop(f"vfile_{vk}", None)
            context.bot_data.pop(f"vinfo_{vk}", None)

async def notify_admin(context, text: str):
    for aid in ADMIN_IDS:
        try: await context.bot.send_message(aid, f"🔔 {text}")
        except: pass

async def safe_edit(msg, text: str):
    try: await msg.edit_text(text)
    except: pass

# ══════════════════════════════════════════════════════
# NAVBAT
# ══════════════════════════════════════════════════════

class TaskLimitExceeded(Exception):
    pass

class UserTask:
    def __init__(self, uid: int):
        self.uid = uid
    def __enter__(self):
        if _user_tasks[self.uid] >= MAX_PARALLEL:
            raise TaskLimitExceeded()
        _user_tasks[self.uid] += 1
        return self
    def __exit__(self, *_):
        _user_tasks[self.uid] = max(0, _user_tasks[self.uid] - 1)

# ══════════════════════════════════════════════════════
# FFMPEG FUNKSIYALARI
# ══════════════════════════════════════════════════════

async def extract_audio(video_path: str, out_path: str, duration: int = None) -> bool:
    """Videodan audio ajratib olish"""
    cmd = [FFMPEG, "-i", video_path]
    if duration:
        cmd += ["-t", str(duration)]
    cmd += ["-vn", "-acodec", "libmp3lame", "-q:a", "2",
            "-ar", "44100", "-ac", "2", "-y", out_path]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await asyncio.wait_for(proc.wait(), timeout=300)
        return os.path.exists(out_path) and os.path.getsize(out_path) > 1024
    except Exception as e:
        log.error(f"extract_audio xato: {e}")
        return False

async def split_and_send_audio(message, audio_path: str, title: str, performer: str, markup=None):
    """50MB dan katta audioni bo'lib yuborish"""
    fsize = os.path.getsize(audio_path)
    if fsize <= MAX_FILE_MB * 1024 * 1024:
        with open(audio_path, "rb") as f:
            await message.reply_audio(f, title=title, performer=performer, reply_markup=markup)
        try: os.remove(audio_path)
        except: pass
        return

    await message.reply_text(f"⚠️ Audio katta ({human_size(fsize)}), qismlarga bo'lib yuborilmoqda...")
    base = audio_path.rsplit(".", 1)[0]
    offset, idx = 0, 0
    parts = []

    while True:
        part = f"{base}_part{idx}.mp3"
        cmd = [FFMPEG, "-i", audio_path, "-ss", str(offset), "-t", "3600",
               "-acodec", "copy", "-y", part]
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL
        )
        await proc.wait()
        if not os.path.exists(part) or os.path.getsize(part) < 1024:
            try: os.remove(part)
            except: pass
            break
        parts.append(part)
        offset += 3600
        idx += 1
        if idx > 20: break

    for i, part in enumerate(parts, 1):
        try:
            with open(part, "rb") as f:
                await message.reply_audio(
                    f,
                    title=f"{title} ({i}/{len(parts)})",
                    performer=performer,
                    reply_markup=markup if i == len(parts) else None,
                )
        except Exception as e:
            await message.reply_text(f"❌ Qism {i} yuborilmadi: {e}")
        finally:
            try: os.remove(part)
            except: pass
        await asyncio.sleep(0.5)

    try: os.remove(audio_path)
    except: pass

# ══════════════════════════════════════════════════════
# SHAZAM
# ══════════════════════════════════════════════════════

async def recognize_song(audio_path: str) -> dict | None:
    try:
        from shazamio import Shazam
        result = await Shazam().recognize(audio_path)
        track = result.get("track")
        if not track:
            return None
        return {
            "title":  track.get("title", ""),
            "artist": track.get("subtitle", ""),
            "genre":  track.get("genres", {}).get("primary", ""),
            "cover":  track.get("images", {}).get("coverart", ""),
        }
    except ImportError:
        log.error("shazamio o'rnatilmagan! pip install shazamio")
        return None
    except Exception as e:
        log.error(f"Shazam xato: {e}")
        return None

async def send_shazam_result(msg, reply_to, result: dict):
    text = f"🎵 *{result['title']}*\n👤 {result['artist']}\n"
    if result.get("genre"):
        text += f"🎸 {result['genre']}\n"
    sq = f"{result['artist']} {result['title']}"
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("⬇️ Yuklab olish", callback_data=f"sdl_{sq[:50]}")
    ]])
    if result.get("cover"):
        try:
            await msg.delete()
            await reply_to.reply_photo(result["cover"], caption=text,
                                       parse_mode="Markdown", reply_markup=kb)
            return
        except: pass
    await msg.edit_text(text, parse_mode="Markdown", reply_markup=kb)

# ══════════════════════════════════════════════════════
# BUYRUQLAR
# ══════════════════════════════════════════════════════

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    name = update.effective_user.first_name or "Do'st"
    await update.message.reply_text(
        f"Salom, {name}! 👋\n\n"
        "🎶 *Music Bot* ga xush kelibsiz!\n\n"
        "📌 *Imkoniyatlar:*\n"
        "🔍 Qo'shiq nomi yozing → qidirish → yuklab olish\n"
        "🔗 Havola yuboring → video yuklanadi\n"
        "🎵 Video kelgach → *Faqat audio* tugmasi\n"
        "🎧 Video kelgach → *Qo'shiqni top* (Shazam)\n"
        "📤 Video/audio fayl yuboring → qo'shiq topiladi\n\n"
        "/help — batafsil\n"
        "/status — bot holati",
        parse_mode="Markdown",
    )

async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📖 *Yordam*\n\n"
        "*Qo'shiq qidirish:*\n"
        "Qo'shiq yoki artist nomini yozing\n\n"
        "*Havola orqali video:*\n"
        "YouTube / Instagram / TikTok / Twitter havolasini yuboring\n"
        "Faqat audio: `havola audio` deb yozing\n\n"
        "*Fayl yuborish:*\n"
        "Video yoki audio → Shazam orqali qo'shiq topiladi\n\n"
        "*Tugmalar:*\n"
        "🎵 *Faqat audio* — videoni mp3 ga aylantiradi\n"
        "🔍 *Qo'shiqni top* — Shazam orqali aniqlaydi\n"
        "⬇️ *Yuklab olish* — topilgan qo'shiqni yuklab oladi\n\n"
        "/cancel — yuklanishni bekor qilish\n"
        "/status — bot holati",
        parse_mode="Markdown",
    )

async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if _user_tasks.get(uid, 0) > 0:
        _user_tasks[uid] = 0
        await update.message.reply_text("🛑 Bekor qilindi.")
    else:
        await update.message.reply_text("ℹ️ Hozir faol yuklanish yo'q.")

async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # shazamio tekshirish
    try:
        import shazamio
        shazam_st = f"✅ {shazamio.__version__}"
    except ImportError:
        shazam_st = "❌ O'rnatilmagan (pip install shazamio)"

    # ffmpeg tekshirish
    ffmpeg_st = f"✅ {FFMPEG}" if os.path.isfile(FFMPEG) or shutil.which("ffmpeg") else "❌ Topilmadi"

    # cookie
    if os.path.exists(COOKIES_FILE):
        age_h = (time.time() - os.path.getmtime(COOKIES_FILE)) / 3600
        cookie_st = f"✅ Bor ({age_h:.0f} soat oldin)" + (" ⚠️ Eski!" if age_h > 168 else "")
    else:
        cookie_st = "❌ Yo'q"

    # fayllar
    total, count = 0, 0
    try:
        for f in os.listdir(DOWNLOAD_DIR):
            fp = os.path.join(DOWNLOAD_DIR, f)
            if os.path.isfile(fp):
                total += os.path.getsize(fp)
                count += 1
    except: pass

    await update.message.reply_text(
        "📊 *Bot holati*\n\n"
        f"• FFmpeg: {ffmpeg_st}\n"
        f"• Shazam: {shazam_st}\n"
        f"• Cookie: {cookie_st}\n"
        f"• Faol yuklanishlar: {sum(_user_tasks.values())}\n"
        f"• Vaqtinchalik fayllar: {count} ta ({human_size(total)})",
        parse_mode="Markdown",
    )

# ══════════════════════════════════════════════════════
# MUSIQA QIDIRISH
# ══════════════════════════════════════════════════════

async def search_music(update: Update, query: str):
    msg = await update.message.reply_text("🔍 Qidirilmoqda...")
    try:
        def do_search():
            ydl_opts = {
                "quiet": True,
                "no_warnings": True,
                "skip_download": True,
                "extract_flat": "in_playlist",
                "socket_timeout": 20,
                "noplaylist": True,
                "ignoreerrors": True,
                "extractor_args": {"youtube": {"player_client": ["android", "web"]}},
            }
            ydl_opts.update(YDL_BASE)
            ydl_opts.update(get_cookies_opt())
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                return ydl.extract_info(f"ytsearch8:{query}", download=False)

        info = await run_in_executor(do_search)
        if not info:
            await msg.edit_text("❌ Hech narsa topilmadi.")
            return

        entries = info.get("entries") or []
        valid = []
        for e in entries:
            if not e:
                continue
            vid_id = e.get("id")
            if not vid_id:
                continue
            title = (e.get("title") or "Nomsiz").strip()
            dur = int(e.get("duration") or 0)
            valid.append({"id": vid_id, "title": title, "dur": dur})

        if not valid:
            await msg.edit_text("❌ Hech narsa topilmadi. Boshqacha yozing.")
            return

        kb = []
        for e in valid[:5]:
            t = e["title"][:45]
            d = e["dur"]
            dur_str = f"{d//60}:{d%60:02d}" if d else "--:--"
            kb.append([InlineKeyboardButton(
                f"🎵 {t}  {dur_str}",
                callback_data=f"dl_{e['id']}"
            )])

        await msg.edit_text(
            f"🎵 *Natijalar:* {query}",
            reply_markup=InlineKeyboardMarkup(kb),
            parse_mode="Markdown",
        )
    except Exception as e:
        log.error(f"search_music xato: {e}")
        await msg.edit_text("❌ Qidirishda xato yuz berdi. Qayta urinib ko'ring.")

# ══════════════════════════════════════════════════════
# KATALOGDAN AUDIO YUKLASH
# ══════════════════════════════════════════════════════

async def download_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id
    video_id = q.data[3:]  # "dl_" ni olib tashlash
    url = f"https://www.youtube.com/watch?v={video_id}"

    try:
        with UserTask(uid):
            msg = await q.edit_message_text("⏳ Yuklanmoqda... 0%")
            loop = asyncio.get_running_loop()
            os.makedirs(DOWNLOAD_DIR, exist_ok=True)
            await maybe_update_ytdlp()

            def do_dl():
                with yt_dlp.YoutubeDL({
                    "format": "bestaudio[ext=m4a]/bestaudio[ext=webm]/bestaudio/best",
                    "outtmpl": f"{DOWNLOAD_DIR}/{video_id}.%(ext)s",
                    "quiet": True, "no_warnings": True,
                    "socket_timeout": 60,
                    "concurrent_fragment_downloads": 4,
                    "progress_hooks": [make_progress_hook(msg, loop)],
                    "extractor_args": {"youtube": {"player_client": ["ios"]}},
                    **get_cookies_opt(),
                }) as ydl:
                    return ydl.extract_info(url, download=True)

            info = await run_in_executor(do_dl)
            filename = find_file(DOWNLOAD_DIR, video_id)
            if not filename:
                await msg.edit_text("❌ Fayl yuklanmadi!")
                return

            fsize = os.path.getsize(filename)
            if fsize > MAX_FILE_MB * 1024 * 1024:
                await msg.edit_text(f"❌ Fayl juda katta ({human_size(fsize)}).")
                try: os.remove(filename)
                except: pass
                return

            await msg.delete()
            with open(filename, "rb") as f:
                await q.message.reply_audio(
                    f,
                    title=info.get("title", "Qo'shiq"),
                    performer=info.get("uploader", ""),
                )
            try: os.remove(filename)
            except: pass

    except TaskLimitExceeded:
        await q.answer(f"⏳ Max {MAX_PARALLEL} ta parallel yuklash. Kuting.", show_alert=True)
    except Exception as e:
        log.error(f"download_callback: {e}")
        err = str(e)
        if "Sign in" in err or "confirm" in err.lower():
            await safe_edit(msg, "❌ YouTube bot ekanligimizni aniqladi.\n"
                                 "Muammo yt-dlp versiyasida. Qayta urinib ko'ring.")
        else:
            await safe_edit(msg, f"❌ Xato: {err[:200]}")

# ══════════════════════════════════════════════════════
# HAVOLA ORQALI VIDEO YUKLASH
# ══════════════════════════════════════════════════════

PLATFORM_FMT = {
    "YouTube":   "best[ext=mp4][filesize<50M]/bestvideo[ext=mp4]+bestaudio[ext=m4a]/best",
    "Instagram": "best[ext=mp4]/best",
    "TikTok":    "best[ext=mp4]/best",
    "Twitter/X": "best[ext=mp4]/best",
    "Facebook":  "best[ext=mp4][filesize<50M]/best",
    "VK":        "best[ext=mp4]/best",
    "SoundCloud":"bestaudio/best",
}
MOBILE_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1"
)

async def download_video(update: Update, context: ContextTypes.DEFAULT_TYPE,
                         url: str, audio_only: bool = False):
    uid = update.effective_user.id
    platform = detect_platform(url)
    ts = int(time.time())

    try:
        with UserTask(uid):
            msg = await update.message.reply_text(
                f"⏳ [{platform}] {'Audio' if audio_only else 'Video'} yuklanmoqda..."
            )
            loop = asyncio.get_running_loop()
            os.makedirs(DOWNLOAD_DIR, exist_ok=True)
            await maybe_update_ytdlp()

            headers = {"User-Agent": MOBILE_UA, "Accept-Language": "en-US,en;q=0.9"}

            # ── Audio rejimi ──
            if audio_only:
                def do_audio():
                    with yt_dlp.YoutubeDL({
                        "format": "bestaudio[ext=m4a]/bestaudio/best",
                        "outtmpl": f"{DOWNLOAD_DIR}/audio_{ts}.%(ext)s",
                        "quiet": True, "no_warnings": True, "socket_timeout": 60,
                        "progress_hooks": [make_progress_hook(msg, loop)],
                        "http_headers": headers, **YDL_BASE, **get_cookies_opt(),
                    }) as ydl:
                        return ydl.extract_info(url, download=True)

                info = await run_in_executor(do_audio)
                filename = find_file(DOWNLOAD_DIR, f"audio_{ts}")
                if not filename:
                    await msg.edit_text("❌ Fayl yuklanmadi!")
                    return
                await msg.delete()
                with open(filename, "rb") as f:
                    await update.message.reply_audio(
                        f, title=info.get("title", "Audio"),
                        performer=info.get("uploader", "")
                    )
                try: os.remove(filename)
                except: pass
                return

            # ── Video rejimi ──
            fmt = PLATFORM_FMT.get(platform, "best[ext=mp4][filesize<50M]/best")

            def do_video():
                with yt_dlp.YoutubeDL({
                    "format": fmt,
                    "outtmpl": f"{DOWNLOAD_DIR}/video_{ts}.%(ext)s",
                    "quiet": True, "no_warnings": True, "socket_timeout": 60,
                    "concurrent_fragment_downloads": 4,
                    "merge_output_format": "mp4",
                    "progress_hooks": [make_progress_hook(msg, loop)],
                    "http_headers": headers, **YDL_BASE, **get_cookies_opt(),
                }) as ydl:
                    return ydl.extract_info(url, download=True)

            info = await run_in_executor(do_video)
            filename = find_file(DOWNLOAD_DIR, f"video_{ts}")
            if not filename:
                await msg.edit_text("❌ Fayl yuklanmadi!")
                return

            fsize = os.path.getsize(filename)
            title = info.get("title", "Video")
            vid_key = f"vid_{ts}"

            # Keyingi amallar uchun saqlab qo'yamiz
            context.bot_data[f"vfile_{vid_key}"] = filename
            context.bot_data[f"vinfo_{vid_key}"] = {
                "title": title,
                "uploader": info.get("uploader", ""),
                "saved_at": time.time(),
            }

            kb = InlineKeyboardMarkup([[
                InlineKeyboardButton("🎵 Faqat audio",   callback_data=f"vaudio_{vid_key}"),
                InlineKeyboardButton("🔍 Qo'shiqni top", callback_data=f"shazam_{vid_key}"),
            ]])

            await msg.delete()

            if fsize > MAX_FILE_MB * 1024 * 1024:
                # Video juda katta — audio sifatida yuboramiz
                await update.message.reply_text(
                    f"⚠️ Video katta ({human_size(fsize)}). Audio sifatida yuborilmoqda..."
                )
                audio_out = os.path.join(DOWNLOAD_DIR, f"big_{ts}.mp3")
                if await extract_audio(filename, audio_out):
                    await split_and_send_audio(
                        update.message, audio_out, title,
                        info.get("uploader", ""), markup=kb
                    )
                else:
                    await update.message.reply_text("❌ Audio ajratib bo'lmadi.")
                try: os.remove(filename)
                except: pass
            else:
                with open(filename, "rb") as f:
                    await update.message.reply_video(
                        f, caption=f"✅ {title}",
                        reply_markup=kb, supports_streaming=True,
                    )

            cleanup_bot_data(context)
            cleanup_files()

    except TaskLimitExceeded:
        await update.message.reply_text(
            f"⏳ Max {MAX_PARALLEL} ta parallel yuklash.\n/cancel bilan bekor qiling."
        )
    except Exception as e:
        err = str(e)
        log.error(f"download_video uid={uid}: {err}")
        if "private" in err.lower() or "login" in err.lower():
            await safe_edit(msg, "❌ Bu post shaxsiy (private). Ochiq havola yuboring!")
        elif "Unsupported URL" in err:
            await safe_edit(msg, f"❌ [{platform}] qo'llab-quvvatlanmaydi.")
        elif "geo" in err.lower():
            await safe_edit(msg, "❌ Bu video sizning hududingizda mavjud emas.")
        elif "copyright" in err.lower():
            await safe_edit(msg, "❌ Mualliflik huquqi tufayli bloklanган.")
        else:
            await safe_edit(msg, f"❌ Xato: {err[:200]}")
        await notify_admin(context, f"download_video xato (uid={uid}): {err[:300]}")

# ══════════════════════════════════════════════════════
# VIDEO → FAQAT AUDIO
# ══════════════════════════════════════════════════════

async def video_audio_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id
    vid_key = q.data[7:]  # "vaudio_" ni olib tashlash
    filename = context.bot_data.get(f"vfile_{vid_key}")
    info = context.bot_data.get(f"vinfo_{vid_key}", {})

    if not filename or not os.path.exists(filename):
        await q.message.reply_text(
            "❌ Video fayli topilmadi.\n"
            "Bot qayta ishga tushgan bo'lishi mumkin. Havolani qayta yuboring."
        )
        return

    try:
        with UserTask(uid):
            msg = await q.message.reply_text("🎵 Audio ajratilmoqda...")
            ts = int(time.time())
            audio_out = os.path.join(DOWNLOAD_DIR, f"vaudio_{ts}.mp3")

            if not await extract_audio(filename, audio_out):
                await msg.edit_text(
                    "❌ Audio ajratib bo'lmadi.\n"
                    f"FFmpeg yo'li: {FFMPEG}\n"
                    "Serverni tekshiring: apt install ffmpeg"
                )
                return

            await msg.delete()
            await split_and_send_audio(
                q.message, audio_out,
                info.get("title", "Audio"),
                info.get("uploader", ""),
            )
    except TaskLimitExceeded:
        await q.answer(f"⏳ Max {MAX_PARALLEL} ta parallel.", show_alert=True)
    except Exception as e:
        log.error(f"video_audio_callback: {e}")
        try: await msg.edit_text(f"❌ Xato: {e}")
        except: pass

# ══════════════════════════════════════════════════════
# SHAZAM — VIDEO DAN QOSHIQ TOPISH
# ══════════════════════════════════════════════════════

async def shazam_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    vid_key = q.data[7:]  # "shazam_" ni olib tashlash
    filename = context.bot_data.get(f"vfile_{vid_key}")

    if not filename or not os.path.exists(filename):
        await q.message.reply_text(
            "❌ Video fayli topilmadi.\n"
            "Bot qayta ishga tushgan bo'lishi mumkin. Havolani qayta yuboring."
        )
        return

    msg = await q.message.reply_text("🎧 Qo'shiq aniqlanmoqda...")
    ts = int(time.time())
    shazam_mp3 = os.path.join(DOWNLOAD_DIR, f"shazam_{ts}.mp3")

    # Videoning birinchi 30 soniyasidan audio olamiz
    if not await extract_audio(filename, shazam_mp3, duration=SHAZAM_SEC):
        await msg.edit_text(
            "❌ Audio ajratib bo'lmadi.\n"
            f"FFmpeg: {FFMPEG}\n"
            "apt install ffmpeg"
        )
        return

    result = await recognize_song(shazam_mp3)
    try: os.remove(shazam_mp3)
    except: pass

    if not result:
        await msg.edit_text(
            "❌ Qo'shiq tanib olinmadi.\n"
            "Sabab: video shovqinli, instrumental yoki Shazam bazasida yo'q."
        )
        return

    await send_shazam_result(msg, q.message, result)

# ══════════════════════════════════════════════════════
# SHAZAM TOPGAN QOSHIQNI YOUTUBE DAN YUKLAB OLISH
# ══════════════════════════════════════════════════════

async def search_dl_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id
    search_q = q.data[4:]  # "sdl_" ni olib tashlash
    msg = await q.message.reply_text("🔍 Qidirilmoqda...")

    try:
        def do_search():
            with yt_dlp.YoutubeDL({
                "quiet": True, "no_warnings": True,
                "skip_download": True, "socket_timeout": 20,
                "noplaylist": True, **YDL_BASE, **get_cookies_opt(),
            }) as ydl:
                return ydl.extract_info(f"ytsearch3:{search_q}", download=False)

        info = await run_in_executor(do_search)
        entries = [e for e in (info.get("entries") or []) if e and e.get("id")]
        if not entries:
            await msg.edit_text("❌ Topilmadi!")
            return

        kb = []
        for e in entries[:3]:
            title = (e.get("title") or "Nomsiz")[:45]
            dur = int(e.get("duration") or 0)
            kb.append([InlineKeyboardButton(
                f"🎵 {title}  {dur//60}:{dur%60:02d}",
                callback_data=f"dl_{e['id']}"
            )])
        await msg.edit_text(
            f"🎶 *Natijalar:* {search_q}",
            reply_markup=InlineKeyboardMarkup(kb),
            parse_mode="Markdown",
        )
    except Exception as e:
        log.error(f"search_dl_callback: {e}")
        await msg.edit_text("❌ Qidirishda xato yuz berdi.")

# ══════════════════════════════════════════════════════
# TELEGRAM FAYL → SHAZAM
# ══════════════════════════════════════════════════════

async def handle_media(update: Update, context: ContextTypes.DEFAULT_TYPE):
    file = (
        update.message.video or update.message.audio or
        update.message.voice or update.message.document
    )
    if not file:
        await update.message.reply_text("❌ Fayl topilmadi.")
        return

    fsize = getattr(file, "file_size", 0) or 0
    if fsize > 20 * 1024 * 1024:
        await update.message.reply_text(
            f"❌ Fayl juda katta ({human_size(fsize)}).\n"
            "Telegram max 20MB fayl uzatadi."
        )
        return

    msg = await update.message.reply_text("🎧 Qo'shiq aniqlanmoqda...")
    ts = int(time.time())
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)
    tmp = os.path.join(DOWNLOAD_DIR, f"tg_{ts}.tmp")

    try:
        tg_file = await context.bot.get_file(file.file_id)
        await tg_file.download_to_drive(tmp)
    except Exception as e:
        await msg.edit_text(f"❌ Fayl yuklab olinmadi: {e}")
        return

    shazam_mp3 = tmp + ".mp3"
    if not await extract_audio(tmp, shazam_mp3, duration=SHAZAM_SEC):
        await msg.edit_text("❌ Audio ajratib bo'lmadi. FFmpeg tekshiring.")
        try: os.remove(tmp)
        except: pass
        return

    try: os.remove(tmp)
    except: pass

    result = await recognize_song(shazam_mp3)
    try: os.remove(shazam_mp3)
    except: pass

    if not result:
        await msg.edit_text(
            "❌ Qo'shiq tanib olinmadi.\n"
            "Audio sifati past yoki Shazam bazasida yo'q."
        )
        return

    await send_shazam_result(msg, update.message, result)

# ══════════════════════════════════════════════════════
# MATN HANDLER
# ══════════════════════════════════════════════════════

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    parts = text.split()

    if len(parts) >= 2 and is_url(parts[0]) and parts[-1].lower() == "audio":
        await download_video(update, context, parts[0], audio_only=True)
    elif is_url(text):
        await download_video(update, context, text)
    else:
        await search_music(update, text)


async def cmd_debug(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Qidiruvni test qilish — Railway logs da ko'rinadi"""
    msg = await update.message.reply_text("🔍 Test qidiruv boshlandi...")
    results = []

    # Test 1: eng oddiy sozlama
    try:
        def t1():
            with yt_dlp.YoutubeDL({
                "quiet": True, "no_warnings": True,
                "skip_download": True, "extract_flat": "in_playlist",
                "noplaylist": True, "ignoreerrors": True,
            }) as ydl:
                return ydl.extract_info("ytsearch3:Dua Lipa", download=False)
        info = await run_in_executor(t1)
        entries = [e for e in (info.get("entries") or []) if e and e.get("id")]
        results.append(f"Test1 (oddiy): {len(entries)} ta natija")
        if entries:
            results.append(f"  1-natija: {entries[0].get('title','?')[:40]}")
    except Exception as e:
        results.append(f"Test1 xato: {str(e)[:100]}")

    # Test 2: android client
    try:
        def t2():
            with yt_dlp.YoutubeDL({
                "quiet": True, "no_warnings": True,
                "skip_download": True, "extract_flat": "in_playlist",
                "noplaylist": True, "ignoreerrors": True,
                "extractor_args": {"youtube": {"player_client": ["android"]}},
            }) as ydl:
                return ydl.extract_info("ytsearch3:Dua Lipa", download=False)
        info = await run_in_executor(t2)
        entries = [e for e in (info.get("entries") or []) if e and e.get("id")]
        results.append(f"Test2 (android): {len(entries)} ta natija")
        if entries:
            results.append(f"  1-natija: {entries[0].get('title','?')[:40]}")
    except Exception as e:
        results.append(f"Test2 xato: {str(e)[:100]}")

    # Test 3: mweb client
    try:
        def t3():
            with yt_dlp.YoutubeDL({
                "quiet": True, "no_warnings": True,
                "skip_download": True, "extract_flat": "in_playlist",
                "noplaylist": True, "ignoreerrors": True,
                "extractor_args": {"youtube": {"player_client": ["mweb"]}},
            }) as ydl:
                return ydl.extract_info("ytsearch3:Dua Lipa", download=False)
        info = await run_in_executor(t3)
        entries = [e for e in (info.get("entries") or []) if e and e.get("id")]
        results.append(f"Test3 (mweb): {len(entries)} ta natija")
        if entries:
            results.append(f"  1-natija: {entries[0].get('title','?')[:40]}")
    except Exception as e:
        results.append(f"Test3 xato: {str(e)[:100]}")

    text = "📊 Debug natijalar:\n\n" + "\n".join(results)
    await msg.edit_text(text)

# ══════════════════════════════════════════════════════
# ISHGA TUSHIRISH
# ══════════════════════════════════════════════════════

os.makedirs(DOWNLOAD_DIR, exist_ok=True)

app = ApplicationBuilder().token(TOKEN).build()

app.add_handler(CommandHandler("start",  cmd_start))
app.add_handler(CommandHandler("help",   cmd_help))
app.add_handler(CommandHandler("cancel", cmd_cancel))
app.add_handler(CommandHandler("status", cmd_status))
app.add_handler(CommandHandler("debug",  cmd_debug))

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
print("✅ Bot ishlamoqda!")
app.run_polling(poll_interval=0.3, timeout=30, drop_pending_updates=True)
