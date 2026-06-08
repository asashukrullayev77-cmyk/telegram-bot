import os
import asyncio
import time
import subprocess
import yt_dlp
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes

TOKEN = "8802164056:AAHUzN18Lr5a8S3lhKmuIJ4Ix0OP4X5_Jo4"

# ─────────────────────────────────────────
# /start
# ─────────────────────────────────────────
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    name = user.first_name or "Do'st"
    await update.message.reply_text(
        f"Salom, {name}! 👋\n\n"
        f"🎶 Music botga xush kelibsiz!\n\n"
        "🎵 Musiqa — qo'shiq yoki qo'shiqchi ismini yozing\n"
        "📥 Video — YouTube/Instagram/TikTok havolasini yozing\n"
        "🎧 Instagram video'dan faqat audio kerak bo'lsa — havoladan keyin 'audio' yozing\n"
        "Masalan: https://instagram.com/... audio"
    )

# ─────────────────────────────────────────
# URL tekshirish
# ─────────────────────────────────────────
def is_url(text):
    return text.startswith("http://") or text.startswith("https://")

def is_instagram_url(url):
    return "instagram.com" in url or "instagr.am" in url

# ─────────────────────────────────────────
# Progress hook — rate limit xatosini oldini olish
# ─────────────────────────────────────────
def make_progress_hook(msg, loop):
    state = {'last_update': 0, 'last_percent': ''}

    def hook(d):
        if d['status'] == 'downloading':
            percent = d.get('_percent_str', '').strip()
            speed = d.get('_speed_str', '').strip()
            now = time.time()
            if percent and percent != state['last_percent'] and now - state['last_update'] > 3:
                state['last_percent'] = percent
                state['last_update'] = now
                asyncio.run_coroutine_threadsafe(
                    msg.edit_text(f"⏳ Yuklanmoqda... {percent}\n⚡ Tezlik: {speed}"),
                    loop
                )
    return hook

# ─────────────────────────────────────────
# Musiqa qidirish — TO'G'RILANGAN
# ─────────────────────────────────────────
async def search_music(update: Update, query: str):
    msg = await update.message.reply_text("🔍 Qidirilmoqda...")
    try:
        ydl_opts = {
            'quiet': True,
            'no_warnings': True,
            'skip_download': True,
            'extract_flat': False,       # ✅ False — to'liq ma'lumot olish
            'socket_timeout': 20,
            'noplaylist': True,
        }

        loop = asyncio.get_running_loop()

        def do_search():
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                # ✅ ytsearch5: — 5 ta natija
                return ydl.extract_info(f"ytsearch5:{query}", download=False)

        info = await loop.run_in_executor(None, do_search)

        entries = info.get('entries', [])
        if not entries:
            await msg.edit_text("❌ Hech narsa topilmadi!")
            return

        keyboard = []
        for entry in entries[:5]:
            if not entry:
                continue
            title = (entry.get('title') or 'Nomsiz')[:50]
            duration = entry.get('duration') or 0
            mins = int(duration) // 60
            secs = int(duration) % 60
            vid_id = entry.get('id', '')
            if not vid_id:
                continue
            keyboard.append([InlineKeyboardButton(
                f"🎵 {title} ({mins}:{secs:02d})",
                callback_data=f"dl_{vid_id}"
            )])

        if not keyboard:
            await msg.edit_text("❌ Natijalar topilmadi!")
            return

        await msg.edit_text(
            f"🎵 *{query}* bo'yicha natijalar:",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown'
        )

    except Exception as e:
        await msg.edit_text(f"❌ Xato: {e}")

# ─────────────────────────────────────────
# Callback — tugmadan qo'shiq yuklash
# ─────────────────────────────────────────
async def download_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    video_id = query.data.replace("dl_", "", 1)
    url = f"https://www.youtube.com/watch?v={video_id}"

    msg = await query.edit_message_text("⏳ Yuklanmoqda... 0%")
    loop = asyncio.get_running_loop()

    try:
        os.makedirs("downloads", exist_ok=True)
        ydl_opts = {
            'format': 'bestaudio[ext=m4a]/bestaudio/best',
            'outtmpl': 'downloads/%(id)s.%(ext)s',
            'quiet': True,
            'no_warnings': True,
            'socket_timeout': 30,
            'concurrent_fragment_downloads': 4,
            'progress_hooks': [make_progress_hook(msg, loop)],
        }

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = await loop.run_in_executor(
                None, lambda: ydl.extract_info(url, download=True)
            )
            filename = ydl.prepare_filename(info)
            if not os.path.exists(filename):
                base_id = info.get('id', video_id)
                for f in os.listdir("downloads"):
                    if f.startswith(base_id):
                        filename = os.path.join("downloads", f)
                        break

        await msg.delete()
        with open(filename, 'rb') as audio:
            await query.message.reply_audio(
                audio,
                title=info.get('title', "Qo'shiq"),
                performer=info.get('uploader', ''),
            )
        os.remove(filename)

    except Exception as e:
        await msg.edit_text(f"❌ Xato: {e}")

# ─────────────────────────────────────────
# Instagram'dan audio ajratish (ffmpeg)
# ─────────────────────────────────────────
async def extract_audio_from_video(video_path: str) -> str:
    """Video fayldan audio ajratib olish (mp3 formatda)"""
    audio_path = video_path.rsplit('.', 1)[0] + '_audio.mp3'
    cmd = [
        'ffmpeg', '-i', video_path,
        '-vn',                    # video yo'q
        '-acodec', 'libmp3lame',
        '-q:a', '2',              # yuqori sifat
        '-y',                     # ustiga yozish
        audio_path
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL
    )
    await proc.wait()
    if os.path.exists(audio_path):
        return audio_path
    return None

# ─────────────────────────────────────────
# Video yuklash (URL orqali)
# ─────────────────────────────────────────
async def download_video(update: Update, url: str, audio_only: bool = False):
    if audio_only:
        status_text = "⏳ Audio yuklanmoqda... 0%"
    else:
        status_text = "⏳ Video yuklanmoqda... 0%"

    msg = await update.message.reply_text(status_text)
    loop = asyncio.get_running_loop()

    try:
        os.makedirs("downloads", exist_ok=True)

        # ─── Agar to'g'ridan-to'g'ri audio kerak bo'lsa ───
        if audio_only:
            ydl_opts = {
                'format': 'bestaudio[ext=m4a]/bestaudio/best',
                'outtmpl': 'downloads/%(id)s.%(ext)s',
                'quiet': True,
                'no_warnings': True,
                'socket_timeout': 30,
                'progress_hooks': [make_progress_hook(msg, loop)],
                'http_headers': {
                    'User-Agent': 'Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) AppleWebKit/605.1.15',
                    'Accept-Language': 'en-US,en;q=0.5',
                },
            }
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = await loop.run_in_executor(
                    None, lambda: ydl.extract_info(url, download=True)
                )
                filename = ydl.prepare_filename(info)
                if not os.path.exists(filename):
                    vid_id = info.get('id', '')
                    for f in os.listdir("downloads"):
                        if f.startswith(vid_id):
                            filename = os.path.join("downloads", f)
                            break

            await msg.delete()
            with open(filename, 'rb') as audio:
                await update.message.reply_audio(
                    audio,
                    title=info.get('title', "Audio"),
                    performer=info.get('uploader', ''),
                )
            os.remove(filename)
            return

        # ─── Oddiy video yuklash ───
        ydl_opts = {
            'format': 'best[ext=mp4][filesize<50M]/best[filesize<50M]/best',
            'outtmpl': 'downloads/%(id)s.%(ext)s',
            'quiet': True,
            'no_warnings': True,
            'socket_timeout': 30,
            'concurrent_fragment_downloads': 4,
            'progress_hooks': [make_progress_hook(msg, loop)],
            'http_headers': {
                'User-Agent': 'Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) AppleWebKit/605.1.15',
                'Accept-Language': 'en-US,en;q=0.5',
            },
        }

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = await loop.run_in_executor(
                None, lambda: ydl.extract_info(url, download=True)
            )
            filename = ydl.prepare_filename(info)
            if not os.path.exists(filename):
                vid_id = info.get('id', '')
                for f in os.listdir("downloads"):
                    if f.startswith(vid_id):
                        filename = os.path.join("downloads", f)
                        break

        title = info.get('title', 'Video')

        # ─── Instagram uchun audio tugmasi taklif qilish ───
        keyboard = None
        if is_instagram_url(url):
            keyboard = InlineKeyboardMarkup([[
                InlineKeyboardButton(
                    "🎵 Faqat audio olish",
                    callback_data=f"igaudio_{info.get('id', '')}_{url[:100]}"
                )
            ]])

        await msg.delete()
        with open(filename, 'rb') as video:
            await update.message.reply_video(
                video,
                caption=f"✅ {title}",
                reply_markup=keyboard
            )

        # ─── Audio faylini ham tayyorlab qo'yish (cache) ───
        # Faylni o'chirmasdan saqlab qo'yish mumkin, lekin xotira uchun o'chiramiz
        os.remove(filename)

    except Exception as e:
        error_msg = str(e)
        if "Private" in error_msg or "Login" in error_msg or "login" in error_msg:
            await msg.edit_text(
                "❌ Bu post/video shaxsiy (private) yoki login talab qiladi.\n"
                "Iltimos, ochiq (public) havolani yuboring!"
            )
        elif "Unsupported URL" in error_msg:
            await msg.edit_text(
                "❌ Bu havola qo'llab-quvvatlanmaydi.\n"
                "YouTube, Instagram, TikTok havolalarini yuboring."
            )
        else:
            await msg.edit_text(f"❌ Xato: {error_msg}")

# ─────────────────────────────────────────
# Instagram audio callback
# ─────────────────────────────────────────
async def instagram_audio_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    # callback_data: igaudio_{id}_{url}
    data = query.data.replace("igaudio_", "", 1)
    # URL ni ajratib olish (id _ dan keyin)
    parts = data.split("_", 1)
    if len(parts) < 2:
        await query.edit_message_caption("❌ Havola topilmadi!")
        return

    url = parts[1]
    msg = await query.message.reply_text("🎵 Audio ajratilmoqda...")
    loop = asyncio.get_running_loop()

    try:
        os.makedirs("downloads", exist_ok=True)
        ydl_opts = {
            'format': 'bestaudio[ext=m4a]/bestaudio/best',
            'outtmpl': 'downloads/ig_%(id)s.%(ext)s',
            'quiet': True,
            'no_warnings': True,
            'socket_timeout': 30,
            'http_headers': {
                'User-Agent': 'Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) AppleWebKit/605.1.15',
                'Accept-Language': 'en-US,en;q=0.5',
            },
        }

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = await loop.run_in_executor(
                None, lambda: ydl.extract_info(url, download=True)
            )
            filename = ydl.prepare_filename(info)
            if not os.path.exists(filename):
                vid_id = info.get('id', '')
                for f in os.listdir("downloads"):
                    if f.startswith(f"ig_{vid_id}"):
                        filename = os.path.join("downloads", f)
                        break

        await msg.delete()
        with open(filename, 'rb') as audio:
            await query.message.reply_audio(
                audio,
                title=info.get('title', "Instagram audio"),
                performer=info.get('uploader', ''),
            )
        os.remove(filename)

    except Exception as e:
        await msg.edit_text(f"❌ Audio ajratishda xato: {e}")

# ─────────────────────────────────────────
# Xabar handler
# ─────────────────────────────────────────
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()

    # ─── "havola audio" formatini tekshirish ───
    parts = text.split()
    if len(parts) >= 2 and is_url(parts[0]) and parts[-1].lower() == 'audio':
        url = parts[0]
        await download_video(update, url, audio_only=True)
        return

    if is_url(text):
        await download_video(update, text)
    else:
        await search_music(update, text)

# ─────────────────────────────────────────
# Botni ishga tushirish
# ─────────────────────────────────────────
app = ApplicationBuilder().token(TOKEN).build()
app.add_handler(CommandHandler("start", start))
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
app.add_handler(CallbackQueryHandler(download_callback, pattern="^dl_"))
app.add_handler(CallbackQueryHandler(instagram_audio_callback, pattern="^igaudio_"))

print("Bot ishlamoqda... ✅")
app.run_polling(poll_interval=0.5)
