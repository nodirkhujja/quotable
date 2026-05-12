from typing import Protocol, runtime_checkable


@runtime_checkable
class FfmpegPort(Protocol):
    """Thin subprocess boundary for ffmpeg/ffprobe operations.

    Implementors must be stateless. Raise subprocess.CalledProcessError (or
    any exception) on failure — callers are responsible for handling it.
    """

    def thumbnail(self, video_path: str, timestamp: float, output_path: str) -> None:
        """Extract a single JPEG frame from *video_path* at *timestamp* seconds.

        Writes the result to *output_path*. Raises on non-zero exit code.
        """
        ...

    def duration(self, video_path: str) -> float:
        """Return the video duration in seconds. Raises on non-zero exit code."""
        ...
