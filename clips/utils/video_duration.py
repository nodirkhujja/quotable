"""Backward-compatible shim — delegates to integrations.ffmpeg.service."""

from integrations.ffmpeg.service import duration as get_video_duration

__all__ = ["get_video_duration"]
