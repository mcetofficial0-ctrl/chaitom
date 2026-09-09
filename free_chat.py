"""Groq conversation backend. No automatic fallback to a paid provider."""

import io
import math
import threading
import time


class ChatUnavailable(RuntimeError):
    """A short message that is safe to show in Telegram."""


class GroqChat:
    def __init__(self, client, system_prompt, model="openai/gpt-oss-120b"):
        self.client = client
        self.system_prompt = system_prompt
        self.model = model
        self._lock = threading.Lock()
        self._retry_at = {}
        self.health = {"provider": "groq", "model": model, "status": "pending"}

    def _call(self, operation, request):
        if self.client is None:
            self.health = {**self.health, "status": "missing_key"}
            raise ChatUnavailable("Разговорная модель ещё не подключена. Администратору нужно настроить ключ Groq.")
        with self._lock:
            wait = self._retry_at.get(operation, 0) - time.monotonic()
        if wait > 0:
            raise ChatUnavailable(f"Бесплатная модель отдыхает: лимит запросов. Попробуй через {math.ceil(wait / 60)} мин.")
        try:
            return request()
        except Exception as error:
            status = getattr(error, "status_code", None)
            # Log only the type and HTTP status, never provider payloads or keys.
            print(f"GROQ ERROR operation={operation} type={type(error).__name__} status={status}", flush=True)
            if operation == "chat":
                self.health = {**self.health, "status": "error", "http_status": status}
            if status == 429:
                headers = getattr(getattr(error, "response", None), "headers", {})
                try:
                    delay = float(headers.get("retry-after", 60))
                    if not math.isfinite(delay) or delay < 1:
                        delay = 60
                except (TypeError, ValueError):
                    delay = 60
                with self._lock:
                    self._retry_at[operation] = time.monotonic() + delay
                raise ChatUnavailable(f"Закончился бесплатный лимит модели. Попробуй через {math.ceil(delay / 60)} мин.; /rym продолжает работать.") from None
            if status in (401, 403):
                raise ChatUnavailable("Ключ разговорной модели не принят. Администратору нужно проверить доступ Groq.") from None
            if status in (400, 413):
                raise ChatUnavailable("Сообщение не удалось обработать. Попробуй отправить его короче.") from None
            raise ChatUnavailable("Разговорная модель сейчас недоступна. Попробуй чуть позже; /rym работает отдельно.") from None

    def reply(self, prompt):
        def request():
            result = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": self.system_prompt},
                    {"role": "user", "content": prompt[-6500:]},
                ],
                max_completion_tokens=1024,
                reasoning_effort="low",
                extra_body={"include_reasoning": False},
            )
            content = (result.choices[0].message.content or "").strip()
            if not content:
                raise ValueError("empty_response")
            return content.replace("*", "")[:4000]

        answer = self._call("chat", request)
        self.health = {"provider": "groq", "model": self.model, "status": "ok"}
        return answer

    def transcribe(self, data, filename):
        if len(data) > 25_000_000:
            raise ChatUnavailable("Голосовой файл слишком большой. Пришли запись меньше 25 МБ.")

        def request():
            audio = io.BytesIO(data)
            audio.name = filename
            result = self.client.audio.transcriptions.create(
                model="whisper-large-v3-turbo", file=audio, response_format="json",
            )
            content = result.text.strip()
            if not content:
                raise ValueError("empty_transcript")
            return content

        return self._call("audio", request)

    def startup_check(self):
        try:
            self.reply("Проверка связи: ответь одним коротким приветствием на русском языке.")
            print(f"CHAT SELFTEST OK provider=groq model={self.model}", flush=True)
        except ChatUnavailable:
            print(f"CHAT SELFTEST FAILED provider=groq model={self.model}", flush=True)
