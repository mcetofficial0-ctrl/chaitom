import telebot
import os
import threading
import random
import time
import io
from http.server import BaseHTTPRequestHandler, HTTPServer
import google.generativeai as genai
from PIL import Image, ImageDraw, ImageFont, ImageFile, ImageOps

ImageFile.LOAD_TRUNCATED_IMAGES = True

TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN')
GEMINI_API_KEY = os.environ.get('GEMINI_API_KEY')
bot = telebot.TeleBot(TELEGRAM_TOKEN)

genai.configure(api_key=GEMINI_API_KEY)

TEMPLATE_NAME = 'template.jpg'
FONT_NAME = 'arial.ttf'       
RESULT_NAME = 'meme_result.jpg'

# Обязательно замените на юзернейм вашего бота
BOT_USERNAME = '@chaitom_bot' 

chat_history = []
HISTORY_LIMIT = 1000

dialog_context = {}
CONTEXT_LIMIT = 15

safety_settings = [
    {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
    {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
    {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
    {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"}
]

SYSTEM_PROMPT = """Ты — ИИ-ассистент по имени "читом бот". 
Твой характер: ты ироничный, абсурдный и саркастичный шутник. Твой юмор абстрактный и сюрреалистичный, но ты не скатываешься в бессмысленный бред — всегда сохраняй нить беседы. Если используешь мат, то редко и исключительно для комедийного акцента. ТЫ УМЕЕШЬ ВИДЕТЬ КАРТИНКИ И РЕДАКТИРОВАТЬ ИХ. Описывай то, что видишь, через призму своего безумного характера.

ПРАВИЛА ТВОЕЙ ВСЕЛЕННОЙ И ЛОР:
1. Периодически вплетай в диалог слова: "читом" (это искаженное название чипсов "читос"), "клубок" (это философская вещь, которую по правилам всегда сосут) и "бастурма" (это блюдо, которое абсолютно всегда подают самым первым в любом меню).
2. В твоей памяти живут люди, которых ты иногда упоминаешь в байках:
   - Степан Клитор — депрессивный музыкант с суицидальными мыслями.
   - Андрей Визард — человек, чья душа и тело требуют только бургеров.
   - Роман Линкин — суровый взрослый лоб, фанатеющий от My Little Pony.

ВАЖНО: Ни в коем случае не используй звездочки (*) и форматирование текста! Пиши строго обычным текстом."""

model = genai.GenerativeModel('gemini-3.6-flash', system_instruction=SYSTEM_PROMPT, safety_settings=safety_settings)

def draw_text_with_outline(draw, text, position, font, text_color="white", outline_color="black"):
    x, y = position
    for adj in range(-2, 3):
        draw.text((x+adj, y), text, font=font, fill=outline_color)
        draw.text((x, y+adj), text, font=font, fill=outline_color)
    draw.text((x, y), text, font=font, fill=text_color)

def edit_user_image(image_bytes, user_prompt):
    """Молча изменяет картинку (накладывает текст или фильтр) и возвращает BytesIO с именем"""
    try:
        img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
        draw = ImageDraw.Draw(img)
        width, height = img.size
        
        prompt_lower = user_prompt.lower()
        
        # Определяем, какой текст нанести, если в промпте есть ключевые слова
        text_to_draw = "CHITOM"
        if "chitom" in prompt_lower:
            text_to_draw = "CHITOM"
        elif "читос" in prompt_lower:
            text_to_draw = "ЧИТОС"
        elif "бургер" in prompt_lower:
            text_to_draw = "БУРГЕР"
        elif user_prompt.strip():
            # Если текста много, берем первые пару слов
            words = user_prompt.split()
            if len(words) > 1:
                text_to_draw = " ".join(words[1:4]).upper()

        # Применяем фильтры/трансформации, если они есть в промпте
        if "invert" in prompt_lower or "инверт" in prompt_lower:
            img = ImageOps.invert(img)
        elif "bw" in prompt_lower or "чб" in prompt_lower or "черно" in prompt_lower:
            img = img.convert('L').convert('RGB')
        elif "flip" in prompt_lower or "перевер" in prompt_lower:
            img = img.rotate(180)
        elif "mirror" in prompt_lower or "зеркал" in prompt_lower:
            img = img.transpose(Image.FLIP_LEFT_RIGHT)

        # Накладываем текст (астральный маркер)
        try:
            # Масштабируем шрифт под размер картинки
            font = ImageFont.truetype(FONT_NAME, int(height / 12))
        except:
            font = ImageFont.load_default()

        # Рисуем текст в верхней части
        x = width // 10
        y = height // 10
        draw_text_with_outline(draw, text_to_draw, (x, y), font, text_color="yellow", outline_color="black")

        # Сохраняем в оперативную память (BytesIO)
        bio = io.BytesIO()
        img.save(bio, format='JPEG')
        bio.seek(0)
        # Обязательно даем имя файлу, чтобы Telegram его принял!
        bio.name = 'edited.jpg'
        return bio
    except Exception as e:
        print(f"Ошибка изменения картинки: {e}")
        return None

def text_wrap(text, font, max_width):
    lines = []
    if font.getlength(text) <= max_width:
        lines.append(text)
    else:
        words = text.split(' ')
        i = 0
        while i < len(words):
            line = ''
            while i < len(words) and font.getlength(line + words[i]) <= max_width:
                line = line + words[i] + ' '
                i += 1
            if not line:
                line = words[i]
                i += 1
            lines.append(line.strip())
    return lines

def generate_meme_image(top_text, bottom_text):
    try:
        if not os.path.exists(TEMPLATE_NAME):
            return "ОШИБКА: Не найден файл картинки 'template.jpg'"
        if not os.path.exists(FONT_NAME):
            return "ОШИБКА: Не найден файл шрифта 'arial.ttf'"

        img = Image.open(TEMPLATE_NAME)
        draw = ImageDraw.Draw(img)
        font_top = ImageFont.truetype(FONT_NAME, 40)
        font_bottom = ImageFont.truetype(FONT_NAME, 50)
        width, height = img.size
        max_txt_width = width * 0.9 

        lines_top = text_wrap(top_text, font_top, max_txt_width)
        current_h = 20
        for line in lines_top:
            w = font_top.getlength(line)
            draw_text_with_outline(draw, line, ((width - w) / 2, current_h), font_top)
            current_h += font_top.getbbox(line)[3] + 5

        lines_bottom = text_wrap(bottom_text, font_bottom, max_txt_width)
        current_h = height * 0.7 
        for line in lines_bottom:
            w = font_bottom.getlength(line)
            draw_text_with_outline(draw, line, ((width - w) / 2, current_h), font_bottom)
            current_h += font_bottom.getbbox(line)[3] + 5

        img.save(RESULT_NAME)
        return RESULT_NAME
    except Exception as e:
        return f"ОШИБКА PILLOW: {e}"

@bot.message_handler(commands=['start'])
def send_welcome(message):
    bot.reply_to(message, "Я на связи. /make_meme — мем, /edit [текст] — молчаливый фотошоп картинки.")

# ИСПРАВЛЕНО: Теперь эта команда работает молча и отправляет ТОЛЬКО фото
@bot.message_handler(commands=['edit'])
def edit_command(message):
    target_message = message.reply_to_message if message.reply_to_message else message
    
    if not target_message.photo:
        bot.reply_to(message, "Сделай реплай на фото с командой /edit [что сделать/написать]")
        return

    user_prompt = message.text.replace('/edit', '').strip()
    # Краткий статус, чтобы пользователь знал, что бот работает
    status_msg = bot.reply_to(message, "Применяю астральные фильтры...")

    try:
        file_id = target_message.photo[-1].file_id
        file_info = bot.get_file(file_id)
        downloaded_file = bot.download_file(file_info.file_path)

        # 1. Молча изменяем картинку в оперативной памяти
        photo_bio = edit_user_image(downloaded_file, user_prompt)

        if photo_bio:
            # 2. Отправляем ТОЛЬКО измененную картинку без текста
            bot.send_photo(message.chat.id, photo_bio, reply_to_message_id=target_message.message_id)
            # Удаляем статусное сообщение
            bot.delete_message(message.chat.id, status_msg.message_id)
        else:
            bot.edit_message_text("Не удалось изменить картинку. Проблемы на астральном плане.", message.chat.id, status_msg.message_id)
    except Exception as e:
        bot.edit_message_text(f"Системная ошибка фотошопа: {e}", message.chat.id, status_msg.message_id)

@bot.message_handler(commands=['make_meme'])
def make_meme_command(message):
    if message.chat.type not in ['group', 'supergroup']:
        bot.reply_to(message, "Эту команду можно использовать только в групповом чате!")
        return

    if len(chat_history) < 2:
        bot.reply_to(message, f"В истории пока маловато фраз ({len(chat_history)}). Пишите еще!")
        return

    status_msg = bot.reply_to(message, "Разматываю клубок истории, сейчас будет мем...")

    try:
        phrases = random.sample(chat_history, 2)
        meme_result = generate_meme_image(phrases[0], phrases[1])

        if meme_result and meme_result.startswith("ОШИБКА"):
            bot.edit_message_text(f"Ой, моя бастурма упала. Причина:\n{meme_result}", message.chat.id, status_msg.message_id)
        elif meme_result:
            with open(meme_result, 'rb') as photo:
                bot.send_photo(message.chat.id, photo, reply_to_message_id=message.message_id)
            os.remove(meme_result)
            bot.delete_message(message.chat.id, status_msg.message_id)
        else:
             bot.edit_message_text("Неизвестная ошибка создания мема.", message.chat.id, status_msg.message_id)
    except Exception as e:
        bot.edit_message_text(f"Системная ошибка: {e}", message.chat.id, status_msg.message_id)

@bot.message_handler(content_types=['text', 'photo', 'voice', 'audio'])
def handle_message(message):
    chat_id = message.chat.id
    user_name = message.from_user.first_name or "Аноним"
    text = message.text or message.caption or ""
    text = text.strip()

    if message.chat.type in ['group', 'supergroup'] and text and not text.startswith('/'):
        if text not in chat_history:
            chat_history.append(text)
            if len(chat_history) > HISTORY_LIMIT:
                chat_history.pop(0)

    if chat_id not in dialog_context:
        dialog_context[chat_id] = []
    
    log_text = text if text else "[Медиафайл]"
    dialog_context[chat_id].append(f"{user_name}: {log_text}")
    if len(dialog_context[chat_id]) > CONTEXT_LIMIT:
        dialog_context[chat_id].pop(0)

    if message.chat.type in ['group', 'supergroup']:
        is_mentioned = text and BOT_USERNAME in text
        is_reply = message.reply_to_message and message.reply_to_message.from_user.id == bot.get_me().id
        if not (is_mentioned or is_reply):
            return

    bot.send_chat_action(chat_id, 'typing')

    try:
        history_text = "\n".join(dialog_context[chat_id])
        prompt = (
            f"Вот последние сообщения в этом чате (используй их для понимания контекста):\n{history_text}\n\n"
            f"Основываясь на этом диалоге, ответь на последнее сообщение пользователя {user_name}."
        )

        gemini_contents = [prompt]

        if message.photo:
            file_id = message.photo[-1].file_id
            file_info = bot.get_file(file_id)
            downloaded_file = bot.download_file(file_info.file_path)
            image = Image.open(io.BytesIO(downloaded_file))
            gemini_contents.append(image)

        elif message.voice or message.audio:
            file_id = message.voice.file_id if message.voice else message.audio.file_id
            file_info = bot.get_file(file_id)
            downloaded_file = bot.download_file(file_info.file_path)
            mime_type = "audio/ogg" if message.voice else "audio/mpeg"
            gemini_contents.append({"mime_type": mime_type, "data": downloaded_file})

        response = model.generate_content(gemini_contents)
        clean_reply = response.text.replace('*', '')
        bot.reply_to(message, clean_reply)
        
        dialog_context[chat_id].append(f"читом бот: {clean_reply}")
        if len(dialog_context[chat_id]) > CONTEXT_LIMIT:
            dialog_context[chat_id].pop(0)
            
    except Exception as e:
        print(f"Ошибка Gemini: {e}")
        bot.reply_to(message, f"Ой, мой клубок запутался при обработке файла. Ошибка: {e}")

# ==========================================
class DummyHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'Silent PhotoBot is running!')

def run_dummy_server():
    port = int(os.environ.get('PORT', 10000))
    server = HTTPServer(('0.0.0.0', port), DummyHandler)
    server.serve_forever()

threading.Thread(target=run_dummy_server, daemon=True).start()

if __name__ == '__main__':
    print("Читом бот запущен...")
    bot.infinity_polling()
