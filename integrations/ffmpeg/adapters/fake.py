from integrations.ffmpeg.port import FfmpegPort

# Minimal valid JPEG bytes (SOI + EOI markers) — enough for open()/read().
_STUB_JPEG = b"\xff\xd8\xff\xd9"

_STUB_DURATION = 1800.0  # 30 minutes — a plausible TV episode length


class FakeFfmpegAdapter:
    """Test double — no subprocess, no files required on disk.

    thumbnail() writes a stub JPEG so callers that open the output path
    (e.g. Django's ImageField.save) do not raise FileNotFoundError.

    Usage:
        from integrations.ffmpeg import service as ffmpeg
        monkeypatch.setattr(ffmpeg, "_adapter", FakeFfmpegAdapter())
    """

    def thumbnail(self, video_path: str, timestamp: float, output_path: str) -> None:
        with open(output_path, "wb") as fh:
            fh.write(_STUB_JPEG)

    def duration(self, video_path: str) -> float:
        return _STUB_DURATION


assert isinstance(FakeFfmpegAdapter(), FfmpegPort)
