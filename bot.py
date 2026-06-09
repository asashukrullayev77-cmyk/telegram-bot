import os
import asyncio
import time
import logging
import shutil
import tempfile
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
import yt_dlp
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    CallbackQueryHandler, filters, ContextTypes
)

# ==================== SOZLAMALAR ====================
TOKEN = "8802164056:AAHUzN18Lr5a8S3lhKmuIJ4Ix0OP4X5_Jo4"

# Railwayda /tmp papkasidan foydalanamiz (disk cheklangan)
DOWNLOAD_DIR = os.environ.get("DOWNLOAD_DIR", "/tmp/music_bot_downloads")
MAX_PARALLEL = int(os.environ.get("MAX_PARALLEL", "2"))
MAX_FILE_MB = int(os.environ.get("MAX_FILE_MB", "49"))
SHAZAM_SEC = int(os.environ.get("SHAZAM_SEC", "30"))

# Cookie fayl - Railway environment variables orqali
COOKIE_CONTENT = os.environ.get("COOKIE_DATA", "")
COOKIE_FILE = "/tmp/cookies.txt"

# Railway muhiti uchun
IS_RAILWAY = os.environ.get("RAILWAY_ENVIRONMENT") is not None

# ==================== COOKIE SOZLASH (FAQAT BOT EGASI UCHUN) ====================
def setup_cookie():
    """Cookie ni environment variable dan o'qiydi (faqat bot egasi o'rnatadi)"""
    if COOKIE_CONTENT and len(COOKIE_CONTENT) > 50:
        try:
            with open(COOKIE_FILE, "w", encoding="utf-8") as f:
                f.write(COOKIE_CONTENT)
            logging.info("✅ Cookie fayli yaratildi")
            return True
        except Exception as e:
            logging.error(f"Cookie yozishda xato: {e}")
    return False

def get_cookie_opt():
    """Agar cookie fayli mavjud bo'lsa ishlatadi"""
    if os.path.exists(COOKIE_FILE) and os.path.getsize(COOKIE_FILE) > 100:
        return {"cookiefile": COOKIE_FILE}
    return {}

# ==================== LOGLASH ====================
logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(message)s",
    level=logging.INFO,
    handlers=[
        logging.StreamHandler(),
    ]
)
log = logging.getLogger(__name__)

# ==================== GLOBAL O'ZGARUVCHILAR ====================
_executor = ThreadPoolExecutor(max_workers=MAX_PARALLEL + 2)
_user_tasks = defaultdict(int)
_downloading = {}

# ==================== FFMPEG ====================
def get_ffmpeg():
    """Railwayda ffmpeg ni topish"""
    # Railwayda ffmpeg o'rnatilganligini tekshirish
    ff = shutil.which("ffmpeg")
    if ff:
        return ff
    
    # Railwayda odatda /usr/bin/ffmpeg da bo'ladi
    if os.path.exists("/usr/bin/ffmpeg"):
        return "/usr/bin/ffmpeg"
    if os.path.exists("/usr/local/bin/ffmpeg"):
        return "/usr/local/bin/ffmpeg"
    
    # Aks holda PATH dan qidirish
    for p in os.environ.get("PATH", "").split(":"):
        ff = os.path.join(p, "ffmpeg")
        if os.path.exists(ff):
            return ff
    
    return None

FFMPEG = get_ffmpeg()
if FFMPEG:
    log.info(f"✅ FFmpeg topildi: {FFMPEG}")
else:
    log.warning("❌ FFmpeg topilmadi! Railway buildpack qo'shing: 'ffmpeg'")

# ==================== YORDAMCHI FUNKSIYALAR ====================
def is_url(text):
    return text.startswith(("http://", "https://"))

def human_size(n):
    for u in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f} {u}"
        n /= 1024
    return f"{n:.1f} GB"

def find_file(directory, prefix):
    try:
        for f in os.listdir(directory):
            if f.startswith(prefix):
                return os.path.join(directory, f)
    except:
        pass
    return None

async def run_in_executor(func):
    return await asyncio.get_running_loop().run_in_executor(_executor, func)

def cleanup_old_files():
    """Eski fayllarni tozalash (Railwayda disk to'lib qolmasligi uchun)"""
    try:
        now = time.time()
        deleted = 0
        for f in os.listdir(DOWNLOAD_DIR):
            fp = os.path.join(DOWNLOAD_DIR, f)
            if os.path.isfile(fp) and now - os.path.getmtime(fp) > 3600:  # 1 soatdan eski
                try:
                    os.remove(fp)
                    deleted += 1
                except:
                    pass
        if deleted:
            log.info(f"{deleted} ta eski fayl tozalandi")
    except Exception as e:
        log.error(f"Tozalash xatosi: {e}")

# ==================== SHAZAM ====================
async def recognize_song(audio_path):
    try:
        from shazamio import Shazam
        result = await Shazam().recognize(audio_path)
        track = result.get("track")
        if not track:
            return None
        return {
            "title": track.get("title", "Noma'lum"),
            "artist": track.get("subtitle", "Noma'lum ijrochi"),
            "cover": track.get("images", {}).get("coverart", ""),
            "url": f"https://www.youtube.com/results?search_query={track.get('title', '')}+{track.get('subtitle', '')}"
        }
    except ImportError:
        log.error("shazamio o'rnatilmagan!")
        return None
    except Exception as e:
        log.error(f"Shazam xato: {e}")
        return None

# ==================== AUDIO AJRATISH ====================
async def extract_audio(video_path, out_path, duration=None):
    if not FFMPEG:
        return False
    cmd = [FFMPEG, "-i", video_path]
    if duration:
        cmd += ["-t", str(duration)]
    cmd += ["-vn", "-acodec", "libmp3lame", "-q:a", "2", "-ar", "44100", "-ac", "2", "-y", out_path]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, 
            stdout=asyncio.subprocess.DEVNULL, 
            stderr=asyncio.subprocess.DEVNULL
        )
        await proc.wait()
        return os.path.exists(out_path) and os.path.getsize(out_path) > 1000
    except Exception as e:
        log.error(f"Audio ajratish xatosi: {e}")
        return False

# ==================== NAVBAT CHEKLASH ====================
class TaskLimitExceeded(Exception):
    pass

class UserTask:
    def __init__(self, uid):
        self.uid = uid
    def __enter__(self):
        if _user_tasks[self.uid] >= MAX_PARALLEL:
            raise TaskLimitExceeded()
        _user_tasks[self.uid] += 1
        return self
    def __exit__(self, *args):
        _user_tasks[self.uid] = max(0, _user_tasks[self.uid] - 1)

# ==================== BUYRUQLAR ====================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🎵 *Musiqa Boti*\n\n"
        "📌 *Nima qila olaman?*\n"
        "• Qo'shiq nomi yozing → katalog chiqaraman\n"
        "• YouTube/Instagram/TikTok havola yuboring → video yuklayman\n"
        "• Video yuboring → Shazam orqali qo'shiqni topaman\n\n"
        "🔧 /status – bot holati\n"
        "❓ /help – batafsil yordam\n\n"
        "💡 *Bot Railwayda ishlayapti!*",
        parse_mode="Markdown"
    )

async def help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📖 *Yordam*\n\n"
        "1️⃣ *Qo'shiq qidirish:* Qo'shiq yoki artist nomini yozing\n"
        "2️⃣ *Havola yuborish:* YouTube/Instagram/TikTok/Twitter havolasini yuboring\n"
        "3️⃣ *Faqat audio:* `havola audio` deb yozing\n"
        "4️⃣ *Video yuborish:* Bot sizga Shazam orqali qo'shiqni topib beradi\n"
        "5️⃣ *Video kelgandagi tugmalar:*\n"
        "   • 🎵 Faqat audio – mp3 yasaydi\n"
        "   • 🔍 Qo'shiqni top – Shazam orqali aniqlaydi\n\n"
        "/cancel – yuklanishni bekor qilish",
        parse_mode="Markdown"
    )

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid in _downloading:
        _downloading[uid] = False
    if _user_tasks.get(uid, 0) > 0:
        _user_tasks[uid] = 0
        await update.message.reply_text("🛑 Yuklash bekor qilindi.")
    else:
        await update.message.reply_text("ℹ️ Hech qanday faol yuklanish yo'q.")

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cookie_stat = "✅ Bor" if os.path.exists(COOKIE_FILE) and os.path.getsize(COOKIE_FILE) > 100 else "❌ Yo'q"
    ffmpeg_stat = "✅ Bor" if FFMPEG else "❌ Yo'q"
    
    try:
        import shazamio
        shazam_stat = f"✅ {shazamio.__version__}"
    except:
        shazam_stat = "❌ O'rnatilmagan"
    
    total_size = 0
    file_count = 0
    if os.path.exists(DOWNLOAD_DIR):
        for f in os.listdir(DOWNLOAD_DIR):
            p = os.path.join(DOWNLOAD_DIR, f)
            if os.path.isfile(p):
                total_size += os.path.getsize(p)
                file_count += 1
    
    railway_info = "🚂 Railway" if IS_RAILWAY else "💻 Local"
    
    await update.message.reply_text(
        f"📊 *Bot holati*\n\n"
        f"• Platforma: {railway_info}\n"
        f"• FFmpeg: {ffmpeg_stat}\n"
        f"• Shazam: {shazam_stat}\n"
        f"• Cookie: {cookie_stat}\n"
        f"• Faol yuklanishlar: {sum(_user_tasks.values())}\n"
        f"• Vaqtinchalik fayllar: {file_count} ta ({human_size(total_size)})\n"
        f"• Disk: /tmp ({shutil.disk_usage('/tmp')[2] // 1024 // 1024} MB bo'sh)",
        parse_mode="Markdown"
    )

# ==================== MUSIQA QIDIRISH (KATALOG) ====================
async def search_music(update: Update, query: str):
    msg = await update.message.reply_text("🔍 Qidirilmoqda...")
    try:
        def search():
            ydl_opts = {
                "quiet": True,
                "no_warnings": True,
                "skip_download": True,
                "extract_flat": "in_playlist",
                "noplaylist": True,
                "socket_timeout": 30,
                **get_cookie_opt()
            }
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                return ydl.extract_info(f"ytsearch6:{query}", download=False)
        
        data = await run_in_executor(search)
        entries = data.get("entries", []) if data else []
        
        results = []
        for e in entries:
            if e and e.get("id"):
                dur = e.get("duration", 0)
                dur_str = f"{dur//60}:{dur%60:02d}" if dur else "??:??"
                results.append({
                    "id": e["id"],
                    "title": e.get("title", "Nomsiz")[:50],
                    "duration": dur_str
                })
        
        if not results:
            await msg.edit_text("❌ Hech narsa topilmadi. Boshqa so'z bilan yozing.")
            return
        
        keyboard = []
        for r in results[:5]:
            keyboard.append([InlineKeyboardButton(
                f"🎵 {r['title']} [{r['duration']}]",
                callback_data=f"dl_{r['id']}"
            )])
        
        await msg.edit_text(
            f"🎵 *{query[:50]}* bo'yicha natijalar:",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown"
        )
    except Exception as e:
        log.error(f"Qidiruv xatosi: {e}")
        await msg.edit_text("❌ Qidirishda xatolik yuz berdi.")

# ==================== AUDIO YUKLASH ====================
async def download_audio_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    video_id = query.data[3:]
    url = f"https://youtube.com/watch?v={video_id}"
    uid = query.from_user.id
    
    try:
        with UserTask(uid):
            msg = await query.edit_message_text("⏳ Yuklanmoqda...")
            os.makedirs(DOWNLOAD_DIR, exist_ok=True)
            
            def download():
                ydl_opts = {
                    "format": "bestaudio/best",
                    "outtmpl": f"{DOWNLOAD_DIR}/%(id)s.%(ext)s",
                    "quiet": True,
                    "no_warnings": True,
                    "socket_timeout": 30,
                    "postprocessors": [{
                        "key": "FFmpegExtractAudio",
                        "preferredcodec": "mp3",
                        "preferredquality": "192",
                    }],
                    **get_cookie_opt()
                }
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    return ydl.extract_info(url, download=True)
            
            info = await run_in_executor(download)
            file_path = find_file(DOWNLOAD_DIR, video_id)
            
            if not file_path or not os.path.exists(file_path):
                await msg.edit_text("❌ Faylni yuklab bo'lmadi.")
                return
            
            size = os.path.getsize(file_path)
            if size > MAX_FILE_MB * 1024 * 1024:
                await msg.edit_text(f"❌ Fayl juda katta ({human_size(size)}).")
                os.remove(file_path)
                return
            
            await msg.delete()
            with open(file_path, "rb") as f:
                await query.message.reply_audio(
                    f,
                    title=info.get("title", "Qo'shiq")[:200],
                    performer=info.get("uploader", "Noma'lum"),
                    caption="✅ Yuklab olindi!"
                )
            os.remove(file_path)
            cleanup_old_files()
            
    except TaskLimitExceeded:
        await query.answer(f"⏳ Bir vaqtda {MAX_PARALLEL} ta yuklash mumkin.", show_alert=True)
    except Exception as e:
        log.error(f"Yuklash xatosi: {e}")
        await query.edit_message_text(f"❌ Xato: {str(e)[:100]}")

# ==================== HAVOLADAN VIDEO YUKLASH ====================
async def download_video(update: Update, context: ContextTypes.DEFAULT_TYPE, url: str, audio_only=False):
    uid = update.effective_user.id
    msg = None
    
    try:
        with UserTask(uid):
            msg = await update.message.reply_text(f"⏳ Yuklanmoqda...")
            os.makedirs(DOWNLOAD_DIR, exist_ok=True)
            timestamp = int(time.time())
            
            if audio_only:
                out_template = f"{DOWNLOAD_DIR}/audio_{timestamp}.%(ext)s"
                ydl_opts = {
                    "format": "bestaudio/best",
                    "outtmpl": out_template,
                    "quiet": True,
                    "socket_timeout": 30,
                    "postprocessors": [{
                        "key": "FFmpegExtractAudio",
                        "preferredcodec": "mp3",
                        "preferredquality": "192",
                    }],
                    **get_cookie_opt()
                }
            else:
                out_template = f"{DOWNLOAD_DIR}/video_{timestamp}.%(ext)s"
                ydl_opts = {
                    "format": "best[ext=mp4]/best",
                    "outtmpl": out_template,
                    "quiet": True,
                    "socket_timeout": 30,
                    "merge_output_format": "mp4",
                    **get_cookie_opt()
                }
            
            def download():
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    return ydl.extract_info(url, download=True)
            
            info = await run_in_executor(download)
            prefix = "audio" if audio_only else "video"
            file_path = find_file(DOWNLOAD_DIR, f"{prefix}_{timestamp}")
            
            if not file_path:
                await msg.edit_text("❌ Yuklab bo'lmadi.")
                return
            
            size = os.path.getsize(file_path)
            title = info.get("title", "Media")[:100]
            
            await msg.delete()
            
            if audio_only:
                with open(file_path, "rb") as f:
                    await update.message.reply_audio(f, title=title, performer=info.get("uploader", ""))
            else:
                with open(file_path, "rb") as f:
                    await update.message.reply_video(f, caption=f"✅ {title}")
            
            os.remove(file_path)
            cleanup_old_files()
            
    except TaskLimitExceeded:
        await update.message.reply_text(f"⏳ Bir vaqtda {MAX_PARALLEL} ta yuklash mumkin. /cancel")
    except Exception as e:
        log.error(f"Video yuklash xatosi: {e}")
        txt = "❌ Yuklab bo'lmadi."
        if "private" in str(e).lower():
            txt = "❌ Bu video shaxsiy (private)."
        elif "age" in str(e).lower():
            txt = "❌ Bu video yosh cheklangan."
        elif "Unsupported URL" in str(e):
            txt = "❌ Bu platforma qo'llab-quvvatlanmaydi."
        if msg:
            await msg.edit_text(txt)
        else:
            await update.message.reply_text(txt)

# ==================== VIDEO SHAZAM ====================
async def handle_video_for_shazam(update: Update, context: ContextTypes.DEFAULT_TYPE):
    video = update.message.video or update.message.document
    if not video:
        await update.message.reply_text("❌ Video topilmadi.")
        return
    
    if video.file_size > 20 * 1024 * 1024:
        await update.message.reply_text("❌ Fayl 20MB dan kichik bo'lishi kerak.")
        return
    
    msg = await update.message.reply_text("🎧 Qo'shiq aniqlanmoqda...")
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)
    tmp_file = os.path.join(DOWNLOAD_DIR, f"shazam_{int(time.time())}.mp4")
    audio_file = tmp_file.replace(".mp4", ".mp3")
    
    try:
        tg_file = await context.bot.get_file(video.file_id)
        await tg_file.download_to_drive(tmp_file)
        
        if not await extract_audio(tmp_file, audio_file, duration=SHAZAM_SEC):
            await msg.edit_text("❌ Audioni ajratib bo'lmadi.")
            return
        
        result = await recognize_song(audio_file)
        
        for f in [tmp_file, audio_file]:
            if os.path.exists(f):
                os.remove(f)
        
        if not result:
            await msg.edit_text("❌ Qo'shiq tanib olinmadi.")
            return
        
        text = f"🎵 *{result['title']}*\n👤 {result['artist']}"
        search_query = f"{result['title']} {result['artist']}"[:40]
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("🔍 Qidirish", callback_data=f"search_{search_query}")
        ]])
        
        if result.get("cover"):
            try:
                await msg.delete()
                await update.message.reply_photo(result["cover"], caption=text, parse_mode="Markdown", reply_markup=kb)
                return
            except:
                pass
        
        await msg.edit_text(text, parse_mode="Markdown", reply_markup=kb)
        cleanup_old_files()
        
    except Exception as e:
        log.error(f"Shazam xatosi: {e}")
        await msg.edit_text("❌ Xatolik yuz berdi.")

# ==================== QIDIRUV NATIJASINI YUKLASH ====================
async def search_query_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    search_text = query.data[7:]  # "search_" dan keyingi qism
    msg = await query.message.reply_text(f"🔍 {search_text} qidirilmoqda...")
    
    try:
        def search():
            ydl_opts = {
                "quiet": True,
                "no_warnings": True,
                "skip_download": True,
                "extract_flat": "in_playlist",
                "noplaylist": True,
                **get_cookie_opt()
            }
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                return ydl.extract_info(f"ytsearch3:{search_text}", download=False)
        
        data = await run_in_executor(search)
        entries = data.get("entries", []) if data else []
        
        results = []
        for e in entries:
            if e and e.get("id"):
                results.append({
                    "id": e["id"],
                    "title": e.get("title", "Nomsiz")[:45],
                })
        
        if not results:
            await msg.edit_text("❌ Topilmadi!")
            return
        
        keyboard = []
        for r in results[:3]:
            keyboard.append([InlineKeyboardButton(
                f"🎵 {r['title']}",
                callback_data=f"dl_{r['id']}"
            )])
        
        await msg.edit_text(
            f"🎶 Natijalar:",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown"
        )
    except Exception as e:
        log.error(f"Qidiruv xatosi: {e}")
        await msg.edit_text("❌ Xatolik yuz berdi.")

# ==================== ASOSIY HANDLER ====================
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    
    if " " in text:
        parts = text.split()
        if is_url(parts[0]) and parts[-1].lower() == "audio":
            await download_video(update, context, parts[0], audio_only=True)
            return
    
    if is_url(text):
        await download_video(update, context, text, audio_only=False)
        return
    
    await search_music(update, text)

# ==================== ISHGA TUSHIRISH ====================
def main():
    # Cookie sozlash
    setup_cookie()
    
    # Papkani yaratish
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)
    
    # Eski fayllarni tozalash
    cleanup_old_files()
    
    app = ApplicationBuilder().token(TOKEN).build()
    
    # Buyruqlar
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help))
    app.add_handler(CommandHandler("cancel", cancel))
    app.add_handler(CommandHandler("status", status))
    
    # Callbacklar
    app.add_handler(CallbackQueryHandler(download_audio_callback, pattern=r"^dl_"))
    app.add_handler(CallbackQueryHandler(search_query_callback, pattern=r"^search_"))
    
    # Xabarlar
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(MessageHandler(filters.VIDEO, handle_video_for_shazam))
    
    log.info("✅ Bot ishga tushdi!")
    print("🎵 Bot Railwayda ishlayapti...")
    
    app.run_polling(allowed_updates=["message", "callback_query"])

if __name__ == "__main__":
    main()
