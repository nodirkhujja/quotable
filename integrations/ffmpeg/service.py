"""Public surface of the ffmpeg integration.

Call thumbnail() and duration() from anywhere — the subprocess details
are an implementation detail of the adapter layer.
"""

from integrations.ffmpeg.adapters.ffmpeg import FfmpegAdapter
from integrations.ffmpeg.port import FfmpegPort

_adapter: FfmpegPort = FfmpegAdapter()


def thumbnail(video_path: str, timestamp: float, output_path: str) -> None:
    """Extract a JPEG frame from *video_path* at *timestamp* seconds."""
    _adapter.thumbnail(video_path, timestamp, output_path)


def duration(video_path: str) -> float:
    """Return video duration in seconds via ffprobe."""
    return _adapter.duration(video_path)
