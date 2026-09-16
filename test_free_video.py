import unittest
from pathlib import Path
from unittest.mock import Mock
from concurrent.futures import TimeoutError

from free_video import generate_video, VideoUnavailable


class VideoTests(unittest.TestCase):
    def make_client(self, **kwargs):
        self.options = kwargs
        self.client = Mock()
        self.directory = Path(kwargs['download_files'])
        video = self.directory / 'test.mp4'
        video.write_bytes(b'\x00\x00\x00\x18ftypisomtest')
        self.client.submit.return_value.result.return_value = ({'video': str(video)}, 42)
        return self.client

    def factory(self, name, **kwargs):
        self.assertEqual(name, 'KingNish/wan2-2-fast')
        return self.make_client(**kwargs)

    def test_success_is_anonymous_and_cleans_files(self):
        self.assertIn(b'ftyp', generate_video('cat', client_factory=self.factory))
        self.assertIs(self.options['token'], False)
        self.assertFalse(self.directory.exists())
        self.client.close.assert_called_once()
        self.assertEqual(self.client.submit.call_args.kwargs['duration_seconds'], 2)

    def test_photo_is_uploaded(self):
        generate_video('cat', b'photo', client_factory=self.factory)
        self.assertIsNotNone(self.client.submit.call_args.kwargs['input_image'])

    def test_errors_are_safe_and_do_not_retry(self):
        for error, expected in [(RuntimeError('quota secret'), 'квота'),
                                (RuntimeError('No GPU was available'), 'GPU'),
                                (TimeoutError(), '4 минуты')]:
            def factory(name, **kwargs):
                client = self.make_client(**kwargs)
                client.submit.return_value.result.side_effect = error
                return client
            with self.assertRaisesRegex(VideoUnavailable, expected) as caught:
                generate_video('cat', client_factory=factory)
            self.assertNotIn('secret', str(caught.exception))
            self.client.submit.assert_called_once()
            self.assertFalse(self.directory.exists())

    def test_invalid_prompt_never_submits(self):
        factory = Mock()
        with self.assertRaises(VideoUnavailable):
            generate_video('x' * 2001, client_factory=factory)
        factory.assert_not_called()

    def test_rejects_output_outside_temp_folder(self):
        def factory(name, **kwargs):
            client = self.make_client(**kwargs)
            client.submit.return_value.result.return_value = ({'video': __file__}, 42)
            return client
        with self.assertRaises(VideoUnavailable):
            generate_video('cat', client_factory=factory)


if __name__ == '__main__':
    unittest.main()
