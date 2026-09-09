import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from free_chat import ChatUnavailable, GroqChat


class ProviderError(Exception):
    def __init__(self, status, retry_after="60"):
        super().__init__("secret-provider-payload")
        self.status_code = status
        self.response = SimpleNamespace(headers={"retry-after": retry_after})


class FreeChatTests(unittest.TestCase):
    def setUp(self):
        self.client = Mock()
        self.backend = GroqChat(self.client, "Отвечай коротко.")
        self.client.chat.completions.create.return_value = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(
                content="Привет, *клубок*!", reasoning="Do not send this to Telegram",
            ))],
        )

    def test_russian_response_uses_only_final_text(self):
        self.assertEqual(self.backend.reply("Привет"), "Привет, клубок!")
        args = self.client.chat.completions.create.call_args.kwargs
        self.assertEqual(args["model"], "openai/gpt-oss-120b")
        self.assertEqual(args["messages"][0]["role"], "system")
        self.assertEqual(args["extra_body"], {"include_reasoning": False})
        self.assertEqual(self.backend.health["status"], "ok")

    def test_long_context_retains_latest_message(self):
        self.backend.reply("a" * 20000 + "Последнее сообщение")
        prompt = self.client.chat.completions.create.call_args.kwargs["messages"][-1]["content"]
        self.assertLessEqual(len(prompt), 6500)
        self.assertTrue(prompt.endswith("Последнее сообщение"))

    def test_rate_limit_respects_retry_after_without_repeated_requests(self):
        self.client.chat.completions.create.side_effect = ProviderError(429, "180")
        with self.assertRaisesRegex(ChatUnavailable, "3 мин"):
            self.backend.reply("Первое")
        with self.assertRaisesRegex(ChatUnavailable, "3 мин"):
            self.backend.reply("Второе")
        self.assertEqual(self.client.chat.completions.create.call_count, 1)
        self.assertEqual(self.backend.health["http_status"], 429)

    def test_provider_details_never_reach_user(self):
        for status in (401, 403, 400, 413, 500):
            with self.subTest(status=status):
                self.client.chat.completions.create.side_effect = ProviderError(status)
                with self.assertRaises(ChatUnavailable) as caught:
                    self.backend.reply("Привет")
                self.assertNotIn("secret-provider-payload", str(caught.exception))

    def test_empty_reply_is_unavailable(self):
        self.client.chat.completions.create.return_value.choices[0].message.content = None
        with self.assertRaises(ChatUnavailable):
            self.backend.reply("Привет")
        self.assertEqual(self.backend.health["status"], "error")

    def test_missing_key_keeps_other_commands_possible(self):
        backend = GroqChat(None, "system")
        backend.startup_check()
        self.assertEqual(backend.health["status"], "missing_key")

    def test_voice_uses_groq_whisper(self):
        self.client.audio.transcriptions.create.return_value = SimpleNamespace(text=" Привет ")
        self.assertEqual(self.backend.transcribe(b"test-audio", "voice.ogg"), "Привет")
        args = self.client.audio.transcriptions.create.call_args.kwargs
        self.assertEqual(args["model"], "whisper-large-v3-turbo")
        self.assertEqual(args["file"].name, "voice.ogg")
        self.assertEqual(args["file"].getvalue(), b"test-audio")

    def test_audio_rate_limit_does_not_block_text(self):
        self.client.audio.transcriptions.create.side_effect = ProviderError(429)
        with self.assertRaises(ChatUnavailable):
            self.backend.transcribe(b"test-audio", "voice.ogg")
        self.assertEqual(self.backend.reply("Привет"), "Привет, клубок!")

    def test_startup_check_verifies_provider_without_sending_telegram_messages(self):
        self.backend.startup_check()
        self.client.chat.completions.create.assert_called_once()
        self.assertEqual(self.backend.health["status"], "ok")


if __name__ == "__main__":
    unittest.main()
