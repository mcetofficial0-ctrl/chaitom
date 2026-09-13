import unittest
from html.parser import HTMLParser

from chat_formatting import format_chat_reply


class ParsedReply(HTMLParser):
    def __init__(self, markup):
        super().__init__(convert_charrefs=True)
        self.stack, self.text = [], []
        self.feed(markup)
        assert not self.stack

    def handle_starttag(self, tag, attrs):
        assert tag in ("b", "code", "pre") and not attrs
        self.stack.append(tag)

    def handle_endtag(self, tag):
        assert self.stack.pop() == tag

    def handle_data(self, data):
        self.text.append(data)


class ChatFormattingTests(unittest.TestCase):
    def test_headings_lists_bold_and_inline_code(self):
        answer = "## Итог\n\n**Важно**\n- первый\n- `x * 2`\n1. Шаг"
        result = format_chat_reply(answer)[0]
        self.assertEqual(result, "<b>Итог</b>\n\n<b>Важно</b>\n• первый\n• <code>x * 2</code>\n1. Шаг")

    def test_code_is_not_interpreted_as_formatting(self):
        result = format_chat_reply('```python\n# comment\nx = "**text** <tag> &"\n```\n')[0]
        self.assertIn('<pre># comment\nx = "**text** &lt;tag&gt; &amp;"\n</pre>', result)

    def test_model_html_is_escaped(self):
        result = format_chat_reply('<b>Не тег</b> & 2 < 3')[0]
        self.assertEqual(result, '&lt;b&gt;Не тег&lt;/b&gt; &amp; 2 &lt; 3')

    def test_long_bold_and_code_have_valid_tags_in_every_chunk(self):
        for text in ('**' + '😀' * 5000 + '**', '```\n' + 'x < 2 & y\n' * 1500 + '```'):
            chunks = format_chat_reply(text)
            self.assertGreater(len(chunks), 1)
            for chunk in chunks:
                parsed = ParsedReply(chunk)
                self.assertLessEqual(len(''.join(parsed.text).encode('utf-16-le')) // 2, 4000)

    def test_plain_text_and_emoji_are_not_lost_at_boundaries(self):
        text = 'я' * 3999 + '😀' * 4001 + ' конец'
        chunks = format_chat_reply(text)
        self.assertEqual(''.join(''.join(ParsedReply(c).text) for c in chunks), text)

    def test_unfinished_markup_is_safe(self):
        self.assertEqual(format_chat_reply('**не закрыто <')[0], '**не закрыто &lt;')
        self.assertEqual(format_chat_reply('```python\nx < y')[0], '<pre>x &lt; y</pre>\n')

    def test_ampersands_count_as_one_visible_character(self):
        chunks = format_chat_reply('&' * 8000)
        self.assertEqual(len(chunks), 2)
        self.assertEqual(''.join(''.join(ParsedReply(c).text) for c in chunks), '&' * 8000)

    def test_no_whitespace_only_message_after_full_code_block(self):
        chunks = format_chat_reply('```\n' + 'x' * 4000 + '\n```')
        self.assertEqual(len(chunks), 1)


if __name__ == '__main__':
    unittest.main()
