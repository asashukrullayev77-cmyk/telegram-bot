import os
import asyncio
import time
import yt_dlp
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    CallbackQueryHandler, filters, ContextTypes
)

TOKEN = "8802164056:AAHUzN18Lr5a8S3lhKmuIJ4Ix0OP4X5_Jo4"
COOKIES_FILE = os.environ.get("COOKIES_FILE", "/root/cookies.txt")
DOWNLOAD_DIR = "downloads"

# ══════════════════════════════════════════════════════
# YORDAMCHI
# ══════════════════════════════════════════════════════

def get_cookies_opt():
    if os.path.exists(COOKIES_FILE):
        return {'cookiefile': COOKIES_FILE}
    return {}

def is_url(text):
    return text.startswith("http://") or text.startswith("https://")

def find_file(directory, prefix):
    """downloads/ papkasidan prefix bilan boshlanadigan faylni topish"""
    try:
        for f in os.listdir(directory):
            if f.startswith(prefix):
                return os.path.join(directory, f)
    except Exception:
        pass
    return None

def make_progress_hook(msg, loop):
    state = {'last_update': 0, 'last_pct': ''}
    def hook(d):
        if d['status'] == 'downloading':
            pct = d.get('_percent_str', '').strip()
            spd = d.get('_speed_str', '').strip()
            now = time.time()
            if pct and pct != state['last_pct'] and now - state['last_update'] > 3:
                state['last_pct'] = pct
                state['last_update'] = now
                asyncio.run_coroutine_threadsafe(
                    msg.edit_text(f"⏳ Yuklanmoqda... {pct}\n⚡ Tezlik: {spd}"),
                    loop
                )
    return hook

# ══════════════════════════════════════════════════════
# SHAZAM
# ══════════════════════════════════════════════════════

async def extract_short_audio(video_path, duration=15):
    """Videoning boshidan qisqa audio kesib olish (Shazam uchun)"""
    out = video_path + "_shazam.mp3"
    proc = await asyncio.create_subprocess_exec(
        'ffmpeg', '-i', video_path,
        '-t', str(duration),
        '-vn', '-acodec', 'libmp3lame', '-q:a', '5',
        '-y', out,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL
    )
    await proc.wait()
    return out if os.path.exists(out) else None

async def recognize_song(audio_path):
    """ShazamIO orqali qo'shiqni tanish"""
    try:
        from shazamio import Shazam
        shazam = Shazam()
        result = await shazam.recognize(audio_path)
        track = result.get('track')
        if not track:
            return None
        return {
            'title':  track.get('title', ''),
            'artist': track.get('subtitle', ''),
            'genre':  track.get('genres', {}).get('primary', ''),
            'cover':  track.get('images', {}).get('coverart', ''),
        }
    except Exception:
        return None

# ══════════════════════════════════════════════════════
# /start
# ══════════════════════════════════════════════════════

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    name = update.effective_user.first_name or "Do'st"
    await update.message.reply_text(
        f"Salom, {name}! 👋\n\n"
        "🎶 Music Bot ga xush kelibsiz!\n\n"
        "📌 Imkoniyatlar:\n"
        "🔍 Qo'shiq nomi → katalog → yuklab olish\n"
        "🔗 YouTube / Instagram / TikTok havolasi → video\n"
        "🎵 Video kelgach → Faqat audio tugmasi\n"
        "🎧 Video kelgach → Qo'shiqni top tugmasi\n"
        "📤 Video yoki audio fayl yuborsangiz → qo'shiq topiladi"
    )

# ══════════════════════════════════════════════════════
# MUSIQA QIDIRISH
# ══════════════════════════════════════════════════════

async def search_music(update: Update, query: str):
    msg = await update.message.reply_text("🔍 Qidirilmoqda...")
    loop = asyncio.get_running_loop()
    try:
        ydl_opts = {
            'quiet': True,
            'no_warnings': True,
            'skip_download': True,
            'extract_flat': False,
            'socket_timeout': 20,
            'noplaylist': True,
            **get_cookies_opt(),
        }

        def do_search():
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                return ydl.extract_info(f"ytsearch5:{query}", download=False)

        info = await loop.run_in_executor(None, do_search)
        entries = [e for e in (info.get('entries') or []) if e and e.get('id')]

        if not entries:
            await msg.edit_text("❌ Hech narsa topilmadi!")
            return

        keyboard = []
        for e in entries[:5]:
            title = (e.get('title') or 'Nomsiz')[:45]
            dur = int(e.get('duration') or 0)
            keyboard.append([InlineKeyboardButton(
                f"🎵 {title}  {dur//60}:{dur%60:02d}",
                callback_data=f"dl_{e['id']}"
            )])

        await msg.edit_text(
            f"🎵 Natijalar: {query}",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
    except Exception as e:
        await msg.edit_text(f"❌ Xato: {e}")

# ══════════════════════════════════════════════════════
# KATALOGDAN QOSHIQ YUKLASH
# ══════════════════════════════════════════════════════

async def download_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    video_id = q.data.replace("dl_", "", 1)
    url = f"https://www.youtube.com/watch?v={video_id}"
    msg = await q.edit_message_text("⏳ Yuklanmoqda... 0%")
    loop = asyncio.get_running_loop()
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)
    try:
        ydl_opts = {
            'format': 'bestaudio[ext=m4a]/bestaudio/best',
            'outtmpl': f'{DOWNLOAD_DIR}/%(id)s.%(ext)s',
            'quiet': True, 'no_warnings': True,
            'socket_timeout': 30,
            'concurrent_fragment_downloads': 4,
            'progress_hooks': [make_progress_hook(msg, loop)],
            **get_cookies_opt(),
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = await loop.run_in_executor(None, lambda: ydl.extract_info(url, download=True))
            filename = ydl.prepare_filename(info)

        # MUAMMO 1 TUZATILDI: fayl kengaytmasi boshqa bo'lishi mumkin
        if not os.path.exists(filename):
            found = find_file(DOWNLOAD_DIR, video_id)
            if not found:
                await msg.edit_text("❌ Fayl yuklanmadi!")
                return
            filename = found

        await msg.delete()
        with open(filename, 'rb') as f:
            await q.message.reply_audio(
                f,
                title=info.get('title', "Qo'shiq"),
                performer=info.get('uploader', '')
            )
        os.remove(filename)

    except Exception as e:
        await msg.edit_text(f"❌ Xato: {e}")

# ══════════════════════════════════════════════════════
# HAVOLA ORQALI VIDEO YUKLASH
# ══════════════════════════════════════════════════════

async def download_video(update: Update, context: ContextTypes.DEFAULT_TYPE, url: str, audio_only: bool = False):
    msg = await update.message.reply_text(
        "⏳ Audio yuklanmoqda..." if audio_only else "⏳ Video yuklanmoqda..."
    )
    loop = asyncio.get_running_loop()
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)

    headers = {
        'User-Agent': 'Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) AppleWebKit/605.1.15',
        'Accept-Language': 'en-US,en;q=0.5',
    }

    try:
        if audio_only:
            ydl_opts = {
                'format': 'bestaudio[ext=m4a]/bestaudio/best',
                'outtmpl': f'{DOWNLOAD_DIR}/%(id)s.%(ext)s',
                'quiet': True, 'no_warnings': True, 'socket_timeout': 30,
                'progress_hooks': [make_progress_hook(msg, loop)],
                'http_headers': headers,
                **get_cookies_opt(),
            }
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = await loop.run_in_executor(None, lambda: ydl.extract_info(url, download=True))
                filename = ydl.prepare_filename(info)
            if not os.path.exists(filename):
                filename = find_file(DOWNLOAD_DIR, info.get('id', ''))
                if not filename:
                    await msg.edit_text("❌ Fayl yuklanmadi!")
                    return
            await msg.delete()
            with open(filename, 'rb') as f:
                await update.message.reply_audio(
                    f, title=info.get('title', 'Audio'), performer=info.get('uploader', '')
                )
            os.remove(filename)
            return

        # ── Video rejimi ──
        ydl_opts = {
            'format': 'best[ext=mp4][filesize<50M]/best[filesize<50M]/best',
            'outtmpl': f'{DOWNLOAD_DIR}/%(id)s.%(ext)s',
            'quiet': True, 'no_warnings': True, 'socket_timeout': 30,
            'concurrent_fragment_downloads': 4,
            'progress_hooks': [make_progress_hook(msg, loop)],
            'http_headers': headers,
            **get_cookies_opt(),
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = await loop.run_in_executor(None, lambda: ydl.extract_info(url, download=True))
            filename = ydl.prepare_filename(info)

        if not os.path.exists(filename):
            filename = find_file(DOWNLOAD_DIR, info.get('id', ''))
            if not filename:
                await msg.edit_text("❌ Fayl yuklanmadi!")
                return

        vid_id = info.get('id', '')
        title = info.get('title', 'Video')

        # MUAMMO 2 TUZATILDI: video faylni o'chirib yubormay saqlab qolamiz
        context.bot_data[f"vfile_{vid_id}"] = filename
        context.bot_data[f"vinfo_{vid_id}"] = {
            'title': info.get('title', ''),
            'uploader': info.get('uploader', ''),
        }

        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("🎵 Faqat audio", callback_data=f"vaudio_{vid_id}"),
            InlineKeyboardButton("🔍 Qo'shiqni top", callback_data=f"shazam_{vid_id}"),
        ]])

        await msg.delete()
        with open(filename, 'rb') as f:
            await update.message.reply_video(f, caption=f"✅ {title}", reply_markup=keyboard)

        # MUAMMO 3 TUZATILDI: video faylni O'CHIRMAYMIZ — shazam/audio uchun kerak
        # Eski fayllarni tozalash (1 soatdan eski)
        _cleanup_old_files()

    except Exception as e:
        err = str(e)
        if "Private" in err or "login" in err.lower():
            await msg.edit_text("❌ Bu post shaxsiy (private). Ochiq havola yuboring!")
        elif "Unsupported URL" in err:
            await msg.edit_text("❌ Bu havola qo'llab-quvvatlanmaydi.\nYouTube / Instagram / TikTok yuboring.")
        else:
            await msg.edit_text(f"❌ Xato: {err}")

def _cleanup_old_files():
    """1 soatdan eski fayllarni o'chirish"""
    try:
        now = time.time()
        for f in os.listdir(DOWNLOAD_DIR):
            fpath = os.path.join(DOWNLOAD_DIR, f)
            if os.path.isfile(fpath) and now - os.path.getmtime(fpath) > 3600:
                os.remove(fpath)
    except Exception:
        pass

# ══════════════════════════════════════════════════════
# VIDEO → FAQAT AUDIO
# ══════════════════════════════════════════════════════

async def video_audio_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    vid_id = q.data.replace("vaudio_", "", 1)
    filename = context.bot_data.get(f"vfile_{vid_id}")
    info = context.bot_data.get(f"vinfo_{vid_id}", {})

    if not filename or not os.path.exists(filename):
        await q.message.reply_text("❌ Fayl topilmadi. Havolani qayta yuboring.")
        return

    msg = await q.message.reply_text("🎵 Audio ajratilmoqda...")
    audio_out = filename + "_audio.mp3"
    proc = await asyncio.create_subprocess_exec(
        'ffmpeg', '-i', filename,
        '-vn', '-acodec', 'libmp3lame', '-q:a', '2',
        '-y', audio_out,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL
    )
    await proc.wait()

    if os.path.exists(audio_out):
        await msg.delete()
        with open(audio_out, 'rb') as f:
            await q.message.reply_audio(
                f,
                title=info.get('title', 'Audio'),
                performer=info.get('uploader', '')
            )
        os.remove(audio_out)
    else:
        await msg.edit_text("❌ ffmpeg topilmadi yoki xato yuz berdi.")

# ══════════════════════════════════════════════════════
# SHAZAM CALLBACK
# ══════════════════════════════════════════════════════

async def shazam_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    vid_id = q.data.replace("shazam_", "", 1)
    filename = context.bot_data.get(f"vfile_{vid_id}")

    if not filename or not os.path.exists(filename):
        await q.message.reply_text("❌ Fayl topilmadi. Havolani qayta yuboring.")
        return

    msg = await q.message.reply_text("🎧 Qo'shiq tanib olinmoqda...")
    short_audio = await extract_short_audio(filename)

    if not short_audio:
        await msg.edit_text("❌ ffmpeg topilmadi. Serverni tekshiring.")
        return

    result = await recognize_song(short_audio)
    try:
        os.remove(short_audio)
    except Exception:
        pass

    if not result:
        await msg.edit_text(
            "❌ Qo'shiq tanib olinmadi.\n"
            "Sabab: video shovqinli yoki qo'shiq bazada yo'q.\n\n"
            "O'rnatish: pip install shazamio"
        )
        return

    text = f"🎵 *{result['title']}*\n👤 {result['artist']}\n"
    if result.get('genre'):
        text += f"🎸 {result['genre']}\n"

    search_q = f"{result['artist']} {result['title']}"
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("⬇️ Yuklab olish", callback_data=f"sdl_{search_q[:50]}")
    ]])

    if result.get('cover'):
        try:
            await msg.delete()
            await q.message.reply_photo(
                result['cover'], caption=text,
                parse_mode='Markdown', reply_markup=keyboard
            )
            return
        except Exception:
            pass

    await msg.edit_text(text, parse_mode='Markdown', reply_markup=keyboard)

# ══════════════════════════════════════════════════════
# SHAZAM TOPGAN QOSHIQNI YUKLAB OLISH
# ══════════════════════════════════════════════════════

async def search_dl_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    search_q = q.data.replace("sdl_", "", 1)
    msg = await q.message.reply_text("🔍 Qidirilmoqda...")
    loop = asyncio.get_running_loop()
    try:
        ydl_opts = {
            'quiet': True, 'no_warnings': True,
            'skip_download': True, 'extract_flat': False,
            'socket_timeout': 20, 'noplaylist': True,
            **get_cookies_opt(),
        }
        def do_search():
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                return ydl.extract_info(f"ytsearch3:{search_q}", download=False)

        info = await loop.run_in_executor(None, do_search)
        entries = [e for e in (info.get('entries') or []) if e and e.get('id')]

        if not entries:
            await msg.edit_text("❌ Topilmadi!")
            return

        keyboard = []
        for e in entries[:3]:
            title = (e.get('title') or 'Nomsiz')[:45]
            dur = int(e.get('duration') or 0)
            keyboard.append([InlineKeyboardButton(
                f"🎵 {title}  {dur//60}:{dur%60:02d}",
                callback_data=f"dl_{e['id']}"
            )])

        await msg.edit_text(
            f"Natijalar: {search_q}",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
    except Exception as e:
        await msg.edit_text(f"❌ Xato: {e}")

# ══════════════════════════════════════════════════════
# TELEGRAM ORQALI VIDEO/AUDIO → SHAZAM
# ══════════════════════════════════════════════════════

async def handle_media(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = await update.message.reply_text("🎧 Qo'shiq tanib olinmoqda...")
    file = (
        update.message.video or update.message.audio or
        update.message.voice or update.message.document
    )
    if not file:
        await msg.edit_text("❌ Fayl topilmadi.")
        return

    # MUAMMO TUZATILDI: Telegram max 20MB fayl uzatadi, katta fayllar xato beradi
    file_size = getattr(file, 'file_size', 0) or 0
    if file_size > 20 * 1024 * 1024:
        await msg.edit_text("❌ Fayl juda katta (max 20MB). Kichikroq fayl yuboring.")
        return

    os.makedirs(DOWNLOAD_DIR, exist_ok=True)
    tmp_path = f"{DOWNLOAD_DIR}/tg_{file.file_id[:12]}.tmp"

    try:
        tg_file = await context.bot.get_file(file.file_id)
        await tg_file.download_to_drive(tmp_path)
    except Exception as e:
        await msg.edit_text(f"❌ Fayl yuklab olinmadi: {e}")
        return

    short_audio = await extract_short_audio(tmp_path)
    try:
        os.remove(tmp_path)
    except Exception:
        pass

    if not short_audio:
        await msg.edit_text("❌ ffmpeg topilmadi. Serverni tekshiring.")
        return

    result = await recognize_song(short_audio)
    try:
        os.remove(short_audio)
    except Exception:
        pass

    if not result:
        await msg.edit_text(
            "❌ Qo'shiq tanib olinmadi.\n"
            "Sabab: audio sifati past yoki bazada yo'q."
        )
        return

    text = f"🎵 *{result['title']}*\n👤 {result['artist']}\n"
    if result.get('genre'):
        text += f"🎸 {result['genre']}\n"

    search_q = f"{result['artist']} {result['title']}"
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("⬇️ Yuklab olish", callback_data=f"sdl_{search_q[:50]}")
    ]])

    if result.get('cover'):
        try:
            await msg.delete()
            await update.message.reply_photo(
                result['cover'], caption=text,
                parse_mode='Markdown', reply_markup=keyboard
            )
            return
        except Exception:
            pass

    await msg.edit_text(text, parse_mode='Markdown', reply_markup=keyboard)

# ══════════════════════════════════════════════════════
# MATN HANDLER
# ══════════════════════════════════════════════════════

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    parts = text.split()

    if len(parts) >= 2 and is_url(parts[0]) and parts[-1].lower() == 'audio':
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
app.add_handler(CommandHandler("start", start))
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
app.add_handler(MessageHandler(
    filters.VIDEO | filters.AUDIO | filters.VOICE |
    filters.Document.VIDEO | filters.Document.AUDIO,
    handle_media
))
app.add_handler(CallbackQueryHandler(download_callback,    pattern="^dl_"))
app.add_handler(CallbackQueryHandler(video_audio_callback, pattern="^vaudio_"))
app.add_handler(CallbackQueryHandler(shazam_callback,      pattern="^shazam_"))
app.add_handler(CallbackQueryHandler(search_dl_callback,   pattern="^sdl_"))

print("Bot ishlamoqda ✅")
app.run_polling(poll_interval=0.5)
