"""Free anonymous ZeroGPU video generation; no credentials or paid fallback."""
from concurrent.futures import TimeoutError
from pathlib import Path
from tempfile import TemporaryDirectory


class VideoUnavailable(RuntimeError):
    pass


def generate_video(prompt, image_bytes=None, client_factory=None):
    from gradio_client import Client, handle_file

    if not prompt.strip() or len(prompt) > 2000:
        raise VideoUnavailable("Опиши видео текстом до 2000 символов.")
    client = job = None
    try:
        with TemporaryDirectory(prefix="chaitom-video-") as directory:
            image = None
            if image_bytes:
                if len(image_bytes) > 10_000_000:
                    raise VideoUnavailable("Для видео пришли фото меньше 10 МБ.")
                image_path = Path(directory) / "input.jpg"
                image_path.write_bytes(image_bytes)
                image = handle_file(str(image_path))
            client = (client_factory or Client)(
                "KingNish/wan2-2-fast", token=False, verbose=False,
                analytics_enabled=False, download_files=directory,
                httpx_kwargs={"timeout": 30.0},
            )
            job = client.submit(
                input_image=image, prompt=prompt, height=256, width=448,
                duration_seconds=2, steps=4, guidance_scale=0,
                randomize_seed=True, api_name="/generate_video",
            )
            result = job.result(timeout=240)
            video = Path(result[0]["video"]).resolve()
            if not video.is_relative_to(Path(directory).resolve()):
                raise ValueError("unexpected output path")
            if video.stat().st_size > 49_000_000:
                raise ValueError("video too large")
            data = video.read_bytes()
            if len(data) < 12 or data[4:8] != b"ftyp":
                raise ValueError("invalid MP4")
            return data
    except VideoUnavailable:
        raise
    except TimeoutError:
        if job:
            job.cancel()
        raise VideoUnavailable("Бесплатная очередь занята: за 4 минуты ролик не готов. Попробуй позже.") from None
    except Exception as error:
        print(f"FREE VIDEO ERROR type={type(error).__name__}", flush=True)
        message = str(error).lower()
        if "no gpu" in message:
            raise VideoUnavailable("Сейчас нет свободной GPU у бесплатного генератора. Попробуй позже; списаний нет.") from None
        if any(word in message for word in ("quota", "429", "limit")):
            raise VideoUnavailable("Закончилась бесплатная GPU-квота генератора. Попробуй позже, после её восстановления. Списаний нет.") from None
        raise VideoUnavailable("Бесплатный генератор временно недоступен. Попробуй позже. Платные сервисы не используются.") from None
    finally:
        if client:
            try:
                client.close()
            except Exception:
                pass
