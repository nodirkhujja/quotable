import subprocess

from integrations.ffmpeg.port import FfmpegPort


class FfmpegAdapter:
    """Real adapter — shells out to ffmpeg and ffprobe.

    Both commands are called with capture_output=True so that their verbose
    progress output does not pollute application logs. Errors still surface
    via CalledProcessError (check=True).
    """

    def thumbnail(self, video_path: str, timestamp: float, output_path: str) -> None:
        subprocess.run(
            [
                "ffmpeg",
                "-ss",
                str(timestamp),
                "-i",
                video_path,
                "-vframes",
                "1",
                "-q:v",
                "2",
                "-y",
                output_path,
            ],
            check=True,
            capture_output=True,
        )

    def duration(self, video_path: str) -> float:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                video_path,
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        return float(result.stdout.strip())


assert isinstance(FfmpegAdapter(), FfmpegPort)
