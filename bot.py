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

TOKEN = "8802164056:AAHUzN18Lr5a8S3lhKmuIJ4Ix0OP4X5_Jo4"
DOWNLOAD_DIR = "downloads"
MAX_PARALLEL = 2
MAX_FILE_MB = 49
SHAZAM_SEC = 30

# Agar shu fayl mavjud bo'lsa, bot uni avtomatik ishlatadi
COOKIE_PATH = "cookies.txt"   # Shu papkadagi fayl, yoki to‘liq yo‘l: "/home/user/cookies.txt"

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
# COOKIE FUNKSIYASI (agar fayl mavjud bo'lsa)
# ══════════════════════════════════════════════════════

def get_cookie_opt() -> dict:
    """Agar cookies.txt fayli mavjud bo'lsa, uni ishlat, aks holda bo'sh dict qaytar."""
    if os.path.exists(COOKIE_PATH):
        log.info(f"Cookie fayli topildi: {COOKIE_PATH}")
        return {"cookiefile": COOKIE_PATH}
    else:
        log.info("Cookie fayli topilmadi, cookie ishlatilmaydi.")
        return {}

# ══════════════════════════════════════════════════════
# FFMPEG
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
            "yt-dlp", "-U",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await asyncio.wait_for(proc.wait(), timeout=120)
        log.info("yt-dlp yangilandi")
    except FileNotFoundError:
        try:
            proc = await asyncio.create_subprocess_exec(
                "pip", "install", "-q", "--upgrade", "yt-dlp",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await asyncio.wait_for(proc.wait(), timeout=120)
            log.info("yt-dlp pip orqali yangilandi")
        except Exception as e:
            log.warning(f"yt-dlp yangilanmadi: {e}")
    except Exception as e:
        log.warning(f"yt-dlp yangilanmadi: {e}")

# ══════════════════════════════════════════════════════
# YORDAMCHILAR
# ══════════════════════════════════════════════════════

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
                try:
                    asyncio.run_coroutine_threadsafe(
                        msg.edit_text(f"⏳ Yuklanmoqda... {pct}\n⚡ Tezlik: {spd}"), loop
                    )
                except Exception:
                    pass
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

async def cleanup_job(context: ContextTypes.DEFAULT_TYPE):
    cleanup_files()
    cleanup_bot_data(context)

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
    slice_duration = 600
    while True:
        part = f"{base}_part{idx}.mp3"
        cmd = [FFMPEG, "-i", audio_path, "-ss", str(offset), "-t", str(slice_duration),
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
        offset += slice_duration
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
        log.error("shazamio o'rnatilmagan!")
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
        "🔗 YouTube/Instagram havola → video yuklanadi\n"
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
        "*Havola orqali:*\n"
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
    try:
        import shazamio
        shazam_st = f"✅ {shazamio.__version__}"
    except ImportError:
        shazam_st = "❌ O'rnatilmagan"

    ffmpeg_st = f"✅ {FFMPEG}" if shutil.which("ffmpeg") else "❌ Topilmadi"
    cookie_st = "✅ Bor" if os.path.exists(COOKIE_PATH) else "❌ Yo'q (faqat ochiq videolar)"

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
            with yt_dlp.YoutubeDL({
                "quiet": True,
                "no_warnings": True,
                "skip_download": True,
                "extract_flat": "in_playlist",
                "socket_timeout": 20,
                "noplaylist": True,
                "ignoreerrors": True,
                **get_cookie_opt(),
            }) as ydl:
                return ydl.extract_info(f"ytsearch8:{query}", download=False)

        info = await run_in_executor(do_search)
        if not info:
            await msg.edit_text("❌ Hech narsa topilmadi.")
            return

        entries = info.get("entries") or []
        valid = []
        for e in entries:
            if not e: continue
            vid_id = e.get("id")
            if not vid_id: continue
            valid.append({
                "id":    vid_id,
                "title": (e.get("title") or "Nomsiz").strip(),
                "dur":   int(e.get("duration") or 0),
            })

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
        await msg.edit_text("❌ Qidirishda xato yuz berdi.")

# ══════════════════════════════════════════════════════
# YOUTUBE AUDIO YUKLASH (katalog orqali)
# ══════════════════════════════════════════════════════

async def download_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id
    video_id = q.data[3:]
    url = f"https://www.youtube.com/watch?v={video_id}"
    msg = None

    try:
        with UserTask(uid):
            msg = await q.edit_message_text("⏳ Yuklanmoqda... 0%")
            loop = asyncio.get_running_loop()
            os.makedirs(DOWNLOAD_DIR, exist_ok=True)
            await maybe_update_ytdlp()

            def do_dl():
                with yt_dlp.YoutubeDL({
                    "format": "bestaudio/best",
                    "outtmpl": f"{DOWNLOAD_DIR}/{video_id}.%(ext)s",
                    "quiet": True,
                    "no_warnings": True,
                    "socket_timeout": 60,
                    "concurrent_fragment_downloads": 4,
                    "progress_hooks": [make_progress_hook(msg, loop)],
                    "postprocessors": [{
                        "key": "FFmpegExtractAudio",
                        "preferredcodec": "mp3",
                        "preferredquality": "192",
                    }],
                    **get_cookie_opt(),
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
        err = str(e)
        log.error(f"download_callback: {err}")
        if "Sign in" in err or "confirm" in err.lower() or "bot" in err.lower():
            await safe_edit(msg, "❌ YouTube bu videoni yuklashga ruxsat bermadi. (Yosh cheklovi yoki maxfiy video)")
        else:
            await safe_edit(msg, f"❌ Xato: {err[:200]}")

# ══════════════════════════════════════════════════════
# HAVOLA ORQALI VIDEO YUKLASH
# ══════════════════════════════════════════════════════

MOBILE_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1"
)

def get_video_format(platform: str) -> str:
    if platform == "YouTube":
        return "bestvideo[ext=mp4][height<=720]+bestaudio[ext=m4a]/bestvideo[ext=mp4]+bestaudio/best[ext=mp4]/best"
    elif platform in ("Instagram", "TikTok", "Twitter/X", "VK"):
        return "best[ext=mp4]/best"
    elif platform == "Facebook":
        return "best[ext=mp4][filesize<50M]/best"
    elif platform == "SoundCloud":
        return "bestaudio/best"
    else:
        return "best[ext=mp4]/best"

async def download_video(update: Update, context: ContextTypes.DEFAULT_TYPE,
                         url: str, audio_only: bool = False):
    uid = update.effective_user.id
    platform = detect_platform(url)
    ts = int(time.time())
    msg = None

    try:
        with UserTask(uid):
            msg = await update.message.reply_text(
                f"⏳ [{platform}] {'Audio' if audio_only else 'Video'} yuklanmoqda..."
            )
            loop = asyncio.get_running_loop()
            os.makedirs(DOWNLOAD_DIR, exist_ok=True)
            await maybe_update_ytdlp()

            headers = {"User-Agent": MOBILE_UA, "Accept-Language": "en-US,en;q=0.9"}

            if audio_only:
                def do_audio():
                    with yt_dlp.YoutubeDL({
                        "format": "bestaudio/best",
                        "outtmpl": f"{DOWNLOAD_DIR}/audio_{ts}.%(ext)s",
                        "quiet": True, "no_warnings": True, "socket_timeout": 60,
                        "progress_hooks": [make_progress_hook(msg, loop)],
                        "http_headers": headers,
                        "postprocessors": [{
                            "key": "FFmpegExtractAudio",
                            "preferredcodec": "mp3",
                            "preferredquality": "192",
                        }],
                        **get_cookie_opt(),
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

            # VIDEO
            fmt = get_video_format(platform)
            def do_video():
                with yt_dlp.YoutubeDL({
                    "format": fmt,
                    "outtmpl": f"{DOWNLOAD_DIR}/video_{ts}.%(ext)s",
                    "quiet": True, "no_warnings": True, "socket_timeout": 60,
                    "concurrent_fragment_downloads": 4,
                    "merge_output_format": "mp4",
                    "progress_hooks": [make_progress_hook(msg, loop)],
                    "http_headers": headers,
                    **get_cookie_opt(),
                }) as ydl:
                    return ydl.extract_info(url, download=True)

            info = await run_in_executor(do_video)
            filename = find_file(DOWNLOAD_DIR, f"video_{ts}")

            if not filename:
                await msg.edit_text("⚠️ Video yuklanmadi, audio sifatida urinib ko'rilmoqda...")
                def do_audio_fallback():
                    with yt_dlp.YoutubeDL({
                        "format": "bestaudio/best",
                        "outtmpl": f"{DOWNLOAD_DIR}/audio_{ts}.%(ext)s",
                        "quiet": True, "no_warnings": True, "socket_timeout": 60,
                        "postprocessors": [{
                            "key": "FFmpegExtractAudio",
                            "preferredcodec": "mp3",
                            "preferredquality": "192",
                        }],
                        "http_headers": headers,
                        **get_cookie_opt(),
                    }) as ydl:
                        return ydl.extract_info(url, download=True)

                info = await run_in_executor(do_audio_fallback)
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

            fsize = os.path.getsize(filename)
            title = info.get("title", "Video")
            vid_key = f"vid_{ts}"

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
                await update.message.reply_text(
                    f"⚠️ Video katta ({human_size(fsize)}). Audio sifatida yuborilmoqda..."
                )
                audio_out = os.path.join(DOWNLOAD_DIR, f"big_{ts}.mp3")
                if await extract_audio(filename, audio_out):
                    await split_and_send_audio(update.message, audio_out, title,
                                               info.get("uploader", ""), markup=kb)
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

    except TaskLimitExceeded:
        await update.message.reply_text(
            f"⏳ Max {MAX_PARALLEL} ta parallel yuklash.\n/cancel bilan bekor qiling."
        )
    except Exception as e:
        err = str(e)
        log.error(f"download_video uid={uid}: {err}")
        txt = "❌ Xato yuz berdi."
        if "private" in err.lower() or "login" in err.lower():
            txt = "❌ Bu post shaxsiy. Ochiq havola yuboring!"
        elif "Unsupported URL" in err:
            txt = f"❌ [{platform}] qo'llab-quvvatlanmaydi."
        elif "Sign in" in err or "confirm" in err.lower():
            txt = "❌ YouTube bu videoni yuklashga ruxsat bermadi. (Yosh cheklovi yoki maxfiy video)"
        elif "geo" in err.lower():
            txt = "❌ Bu video sizning hududingizda mavjud emas."
        elif "format" in err.lower() or "not available" in err.lower():
            txt = "❌ Bu video uchun mos format topilmadi."
        if msg:
            await safe_edit(msg, txt)
        else:
            await update.message.reply_text(txt)

# ══════════════════════════════════════════════════════
# VIDEO → FAQAT AUDIO
# ══════════════════════════════════════════════════════

async def video_audio_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id
    vid_key = q.data[7:]
    filename = context.bot_data.get(f"vfile_{vid_key}")
    info = context.bot_data.get(f"vinfo_{vid_key}", {})

    if not filename or not os.path.exists(filename):
        await q.message.reply_text("❌ Video fayli topilmadi.\nHavolani qayta yuboring.")
        return

    try:
        with UserTask(uid):
            msg = await q.message.reply_text("🎵 Audio ajratilmoqda...")
            ts = int(time.time())
            audio_out = os.path.join(DOWNLOAD_DIR, f"vaudio_{ts}.mp3")

            if not await extract_audio(filename, audio_out):
                await msg.edit_text("❌ Audio ajratib bo'lmadi.")
                return

            await msg.delete()
            await split_and_send_audio(q.message, audio_out,
                                       info.get("title", "Audio"),
                                       info.get("uploader", ""))
    except TaskLimitExceeded:
        await q.answer(f"⏳ Max {MAX_PARALLEL} ta parallel.", show_alert=True)
    except Exception as e:
        log.error(f"video_audio_callback: {e}")
        try: await msg.edit_text(f"❌ Xato: {e}")
        except: pass

# ══════════════════════════════════════════════════════
# SHAZAM CALLBACK
# ══════════════════════════════════════════════════════

async def shazam_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    vid_key = q.data[7:]
    filename = context.bot_data.get(f"vfile_{vid_key}")

    if not filename or not os.path.exists(filename):
        await q.message.reply_text("❌ Video fayli topilmadi.\nHavolani qayta yuboring.")
        return

    msg = await q.message.reply_text("🎧 Qo'shiq aniqlanmoqda...")
    ts = int(time.time())
    shazam_mp3 = os.path.join(DOWNLOAD_DIR, f"shazam_{ts}.mp3")

    if not await extract_audio(filename, shazam_mp3, duration=SHAZAM_SEC):
        await msg.edit_text("❌ Audio ajratib bo'lmadi.")
        return

    result = await recognize_song(shazam_mp3)
    try: os.remove(shazam_mp3)
    except: pass

    if not result:
        await msg.edit_text(
            "❌ Qo'shiq tanib olinmadi.\n"
            "Sabab: video shovqinli yoki Shazam bazasida yo'q."
        )
        return

    await send_shazam_result(msg, q.message, result)

# ══════════════════════════════════════════════════════
# SHAZAM TOPGAN QOSHIQNI YUKLAB OLISH
# ══════════════════════════════════════════════════════

async def search_dl_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    search_q = q.data[4:]
    msg = await q.message.reply_text("🔍 Qidirilmoqda...")

    try:
        def do_search():
            with yt_dlp.YoutubeDL({
                "quiet": True, "no_warnings": True,
                "skip_download": True, "extract_flat": "in_playlist",
                "socket_timeout": 20, "noplaylist": True,
                **get_cookie_opt(),
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
            dur_str = f"{dur//60}:{dur%60:02d}" if dur else "--:--"
            kb.append([InlineKeyboardButton(
                f"🎵 {title}  {dur_str}",
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
            f"❌ Fayl juda katta ({human_size(fsize)}). Max 20MB."
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
        await msg.edit_text("❌ Audio ajratib bo'lmadi.")
        try: os.remove(tmp)
        except: pass
        return

    try: os.remove(tmp)
    except: pass

    result = await recognize_song(shazam_mp3)
    try: os.remove(shazam_mp3)
    except: pass

    if not result:
        await msg.edit_text("❌ Qo'shiq tanib olinmadi.")
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

# ══════════════════════════════════════════════════════
# ISHGA TUSHIRISH
# ══════════════════════════════════════════════════════

os.makedirs(DOWNLOAD_DIR, exist_ok=True)

app = ApplicationBuilder().token(TOKEN).build()

if app.job_queue:
    app.job_queue.run_repeating(cleanup_job, interval=3600, first=10)

app.add_handler(CommandHandler("start",     cmd_start))
app.add_handler(CommandHandler("help",      cmd_help))
app.add_handler(CommandHandler("cancel",    cmd_cancel))
app.add_handler(CommandHandler("status",    cmd_status))

app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
app.add_handler(MessageHandler(
    filters.VIDEO | filters.AUDIO | filters.VOICE | filters.Document.ALL,
    handle_media,
))

app.add_handler(CallbackQueryHandler(download_callback,    pattern=r"^dl_"))
app.add_handler(CallbackQueryHandler(video_audio_callback, pattern=r"^vaudio_"))
app.add_handler(CallbackQueryHandler(shazam_callback,      pattern=r"^shazam_"))
app.add_handler(CallbackQueryHandler(search_dl_callback,   pattern=r"^sdl_"))

log.info("✅ Music Bot ishga tushdi. Cookie fayl mavjudligi: %s", os.path.exists(COOKIE_PATH))
print("✅ Bot ishlamoqda! Hech qanday cookie so'ralmaydi.")
app.run_polling(poll_interval=0.3, timeout=30, drop_pending_updates=True)
