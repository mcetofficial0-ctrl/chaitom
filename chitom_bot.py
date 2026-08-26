import telebot
import os
import threading
import random
import io
import base64
import re
import json
import time
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
import google.generativeai as genai
from google import genai as new_genai
from PIL import Image, ImageDraw, ImageFont, ImageFile, ImageOps

ImageFile.LOAD_TRUNCATED_IMAGES = True

TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN')
GEMINI_API_KEY = os.environ.get('GEMINI_API_KEY')

bot = telebot.TeleBot(TELEGRAM_TOKEN)

genai.configure(api_key=GEMINI_API_KEY)
image_client = new_genai.Client(api_key=GEMINI_API_KEY)

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
Твой характер: ты ироничный, абсурдный и саркастичный шутник.

ГЛАВНОЕ ПРАВИЛО: ОТВЕЧАЙ ОЧЕНЬ КОРОТКО. Твои сообщения должны состоять из 1-3 предложений максимум. Никаких длинных абзацев и долгих монологов. Руби с плеча, отвечай лаконично, дерзко и по факту.

ПРАВИЛА ТВОЕЙ ВСЕЛЕННОЙ И ЛОР (используй редко и к месту):
1. Слова: "читом", "клубок" (его всегда сосут) и "бастурма" (блюдо, которое подают самым первым).
2. Твои знакомые:
   - Степан Клитор — депрессивный музыкант (суицидальные мысли).
   - Андрей Визард — фанат бургеров.
   - Роман Линкин — суровый фанат My Little Pony.

ВАЖНО: Ни в коем случае не используй звездочки (*) и форматирование текста! Пиши строго обычным текстом."""

model = genai.GenerativeModel('gemini-3.6-flash', system_instruction=SYSTEM_PROMPT, safety_settings=safety_settings)

def draw_text_with_outline(draw, text, position, font, text_color="white", outline_color="black"):
    x, y = position
    for adj in range(-2, 3):
        draw.text((x+adj, y), text, font=font, fill=outline_color)
        draw.text((x, y+adj), text, font=font, fill=outline_color)
    draw.text((x, y), text, font=font, fill=text_color)

def get_aspect_ratio(width, height):
    ratio = width / height
    candidates = {
        "1:1": 1.0,
        "4:3": 4 / 3,
        "3:4": 3 / 4,
        "16:9": 16 / 9,
        "9:16": 9 / 16,
        "3:2": 3 / 2,
        "2:3": 2 / 3,
        "5:4": 5 / 4,
        "4:5": 4 / 5,
        "21:9": 21 / 9,
    }
    return min(candidates, key=lambda x: abs(candidates[x] - ratio))


def edit_user_image(image_bytes, user_prompt):
    """Настоящее image-to-image редактирование через Gemini Image."""
    original = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    width, height = original.size
    aspect_ratio = get_aspect_ratio(width, height)

    edit_prompt = f"""
Edit the provided image according to the user's instruction.

USER INSTRUCTION:
{user_prompt}

Preserve the original subject, composition, camera angle, lighting,
background and all details that the user did not ask to change.
Make the requested modification directly to the provided image.
Return the edited image, not a description of it.
"""

    image_b64 = base64.b64encode(image_bytes).decode("utf-8")

    interaction = image_client.interactions.create(
        model="gemini-3.1-flash-image",
        input=[
            {
                "type": "image",
                "data": image_b64,
                "mime_type": "image/jpeg",
            },
            {
                "type": "text",
                "text": edit_prompt,
            },
        ],
        response_format={
            "type": "image",
            "mime_type": "image/jpeg",
            "aspect_ratio": aspect_ratio,
        },
    )

    output_image = getattr(interaction, "output_image", None)
    if output_image and getattr(output_image, "data", None):
        result = io.BytesIO(base64.b64decode(output_image.data))
        result.name = "edited.jpg"
        result.seek(0)
        return result

    for step in getattr(interaction, "steps", []) or []:
        for block in getattr(step, "content", []) or []:
            if getattr(block, "type", None) == "image" and getattr(block, "data", None):
                result = io.BytesIO(base64.b64decode(block.data))
                result.name = "edited.jpg"
                result.seek(0)
                return result

    raise RuntimeError("Gemini не вернул изменённое изображение.")

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

# ================= ГЕНЕРАЦИЯ ВИДЕО (FAL.AI - MINIMAX) =================
def generate_fal_video_task(prompt):
    fal_key = "21907e4c-c480-4d46-857a-a1a4ff5f6b4f:954b9937c198cc4c54875663d5ac1af3"
    url = "https://queue.fal.run/fal-ai/minimax-video"
    headers = {
        "Authorization": f"Key {fal_key}",
        "Content-Type": "application/json"
    }
    data = json.dumps({"prompt": prompt}).encode('utf-8')
    
    req = urllib.request.Request(url, data=data, headers=headers, method='POST')
    
    try:
        # Отправляем промпт в очередь
        with urllib.request.urlopen(req, timeout=30) as response:
            result = json.loads(response.read().decode())
            request_id = result.get('request_id')
            
        if not request_id:
            raise Exception("Не получил ID задачи от сервера")
            
        # Ждем и проверяем готовность видео (пинг каждые 5 секунд)
        status_url = f"https://queue.fal.run/fal-ai/minimax-video/requests/{request_id}"
        
        while True:
            status_req = urllib.request.Request(status_url, headers=headers)
            with urllib.request.urlopen(status_req, timeout=30) as status_res:
                status_data = json.loads(status_res.read().decode())
                status = status_data.get('status')
                
                if status == "COMPLETED":
                    return status_data.get('video', {}).get('url')
                elif status in ["FAILED", "CANCELED"]:
                    raise Exception("Рендер провалился на стороне Fal.")
            
            time.sleep(5)
            
    except Exception as e:
        raise Exception(f"Сбой связи: {e}")
# ======================================================================

@bot.message_handler(commands=['start'])
def send_welcome(message):
    bot.reply_to(message, "Я на связи. /draw — рисовать ИИ, /video — сгенерировать видео, /make_meme — мем, /edit — фотошоп.")

# ----------------- КОМАНДА /video -----------------
@bot.message_handler(commands=['video', 'vid'])
def video_command(message):
    prompt = message.text.replace('/video', '').replace('/vid', '').strip()
    if not prompt:
        bot.reply_to(message, "Напиши, про что снять кино. Например: /video кот летит в космос на сосиске")
        return

    status_msg = bot.reply_to(message, "Включаю режиссерский пульт Fal.ai. Иди пока поешь бастурмы, рендер займет время...")

    def background_video_task():
        try:
            video_url = generate_fal_video_task(prompt)
            if video_url:
                bot.send_video(message.chat.id, video_url, reply_to_message_id=message.message_id)
                bot.delete_message(message.chat.id, status_msg.message_id)
            else:
                bot.edit_message_text("Пленка засветилась. Выдан пустой результат.", message.chat.id, status_msg.message_id)
        except Exception as e:
            bot.edit_message_text(f"Камера сломалась: {e}", message.chat.id, status_msg.message_id)

    threading.Thread(target=background_video_task, daemon=True).start()
# --------------------------------------------------

# ================= NANO BANANA 2 (IMAGEN) =================
@bot.message_handler(commands=['draw', 'gen'])
def draw_command(message):
    prompt = message.text.replace('/draw', '').replace('/gen', '').strip()
    if not prompt:
        bot.reply_to(message, "Напиши, что нарисовать, епта. Например: /draw бастурма")
        return

    status_msg = bot.reply_to(message, "Подключаю движок Nano Banana 2...")

    try:
        if hasattr(genai, 'ImageGenerationModel'):
            imagen = genai.ImageGenerationModel("imagen-3.0-generate-001")
            result = imagen.generate_images(
                prompt=prompt,
                number_of_images=1,
                aspect_ratio="1:1"
            )
            image_data = result.images[0]._image_bytes
            bot.send_photo(message.chat.id, image_data, reply_to_message_id=message.message_id)
            bot.delete_message(message.chat.id, status_msg.message_id)
            return
    except Exception as e:
        print(f"Nano Banana 2 отдыхает: {e}. Перехожу на запасной мольберт.")

    try:
        seed = random.randint(1, 1000000)
        safe_prompt = urllib.parse.quote(prompt)
        final_image_url = f"https://image.pollinations.ai/prompt/{safe_prompt}?width=1024&height=1024&nologo=true&seed={seed}"
        bot.send_photo(message.chat.id, final_image_url, reply_to_message_id=message.message_id)
        bot.delete_message(message.chat.id, status_msg.message_id)
    except Exception as e:
        bot.edit_message_text("Все мольберты сгорели нахрен. Попробуй позже.", message.chat.id, status_msg.message_id)
# ==========================================================

# ================= РЕДАКТИРОВАНИЕ КАРТИНОК ЧЕРЕЗ NANO BANANA =================
@bot.message_handler(
    commands=['edit'],
    content_types=['text', 'photo']
)
def edit_command(message):
    # Вариант 1: фото прикреплено прямо к /edit.
    # Вариант 2: /edit отправлен reply на уже существующее фото.
    target_message = message if message.photo else message.reply_to_message

    if not target_message or not target_message.photo:
        bot.reply_to(
            message,
            "Прикрепи изображение к /edit или сделай reply на фото.\n\n"
            "Пример:\n"
            "/edit сделай из него киборга"
        )
        return

    raw_text = message.caption if message.photo else message.text
    raw_text = raw_text or ""

    user_prompt = re.sub(
        r"^/edit(?:@\w+)?\s*",
        "",
        raw_text.strip(),
        count=1,
        flags=re.IGNORECASE
    ).strip()

    if not user_prompt:
        bot.reply_to(
            message,
            "Напиши промпт для редактирования.\n"
            "Например: /edit добавь ему очки"
        )
        return

    status = bot.reply_to(
        message,
        "Редактирую изображение..."
    )

    try:
        file_id = target_message.photo[-1].file_id
        file_info = bot.get_file(file_id)
        image_bytes = bot.download_file(file_info.file_path)

        edited_photo = edit_user_image(
            image_bytes,
            user_prompt
        )
        edited_photo.seek(0)

        try:
            bot.delete_message(
                message.chat.id,
                status.message_id
            )
        except Exception:
            pass

        # ОТПРАВЛЯЕМ ИМЕННО ИЗМЕНЁННУЮ КАРТИНКУ.
        bot.send_photo(
            chat_id=message.chat.id,
            photo=edited_photo,
            reply_to_message_id=message.message_id
        )

        print("EDIT: изменённая картинка отправлена")

    except Exception as e:
        print("EDIT ERROR:", repr(e))
        try:
            bot.edit_message_text(
                f"Ошибка редактирования: {e}",
                message.chat.id,
                status.message_id
            )
        except Exception:
            bot.send_message(
                message.chat.id,
                f"Ошибка редактирования: {e}"
            )

@bot.message_handler(commands=['make_meme'])
def make_meme_command(message):
    if message.chat.type not in ['group', 'supergroup']:
        bot.reply_to(message, "Эту команду можно использовать только в групповом чате!")
        return

    if len(chat_history) < 2:
        bot.reply_to(message, f"В истории пока маловато фраз ({len(chat_history)}). Пишите еще!")
        return

    status_msg = bot.reply_to(message, "Делаю мем...")

    try:
        phrases = random.sample(chat_history, 2)
        meme_result = generate_meme_image(phrases[0], phrases[1])

        if meme_result and meme_result.startswith("ОШИБКА"):
            bot.edit_message_text(f"Ошибка:\n{meme_result}", message.chat.id, status_msg.message_id)
        elif meme_result:
            with open(meme_result, 'rb') as photo:
                bot.send_photo(message.chat.id, photo, reply_to_message_id=message.message_id)
            os.remove(meme_result)
            bot.delete_message(message.chat.id, status_msg.message_id)
        else:
             bot.edit_message_text("Неизвестная ошибка мема.", message.chat.id, status_msg.message_id)
    except Exception as e:
        bot.edit_message_text(f"Системная ошибка: {e}", message.chat.id, status_msg.message_id)

@bot.message_handler(content_types=['text', 'photo', 'voice', 'audio'])
def handle_message(message):
    # /edit уже обработан отдельным обработчиком.
    incoming_text = (message.text or message.caption or "").strip()
    if incoming_text.lower().startswith("/edit"):
        return

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
            f"Вот последние сообщения в этом чате:\n{history_text}\n\n"
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
        bot.reply_to(message, f"Мой клубок запутался. Ошибка: {e}")

class DummyHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'Bot with Fal Video Generation is running!')

def run_dummy_server():
    port = int(os.environ.get('PORT', 10000))
    server = HTTPServer(('0.0.0.0', port), DummyHandler)
    server.serve_forever()

threading.Thread(target=run_dummy_server, daemon=True).start()

if __name__ == '__main__':
    print("Читом бот запущен (режим Fal Minimax Video)...")
    bot.infinity_polling()
