"""
通用多媒体播放生命周期契约。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator

MediaPlaybackMode = Literal["manual", "once", "loop"]
MediaPreload = Literal["none", "metadata", "auto"]


class MediaPlaybackPolicy(BaseModel):
    """
    功能：描述音视频物料的播放生命周期策略。
    入参：mode（MediaPlaybackMode，默认 manual）：播放触发模式；
        controls（bool，默认 True）：是否显示原生控件；muted（bool，默认 False）：是否静音；
        preload（MediaPreload，默认 metadata）：浏览器预加载策略；
        volume（float，默认 1.0）：播放音量，范围 0 到 1；
        start_time_seconds（float，默认 0）：播放窗口起点；
        end_time_seconds（float | None，默认 None）：播放窗口终点，必须大于起点。
    出参：MediaPlaybackPolicy。
    异常：字段类型、枚举或数值范围非法时由 Pydantic 抛出 ValidationError。
    """

    mode: MediaPlaybackMode = "manual"
    controls: bool = True
    muted: bool = False
    preload: MediaPreload = "metadata"
    volume: float = Field(default=1.0, ge=0.0, le=1.0)
    start_time_seconds: float = Field(default=0.0, ge=0.0)
    end_time_seconds: float | None = Field(default=None, ge=0.0)

    @model_validator(mode="after")
    def validate_time_window(self) -> MediaPlaybackPolicy:
        """
        功能：校验播放窗口终点必须晚于起点。
        入参：self（MediaPlaybackPolicy）：当前播放策略实例。
        出参：MediaPlaybackPolicy，校验通过后返回自身。
        异常：end_time_seconds 小于等于 start_time_seconds 时抛出 ValueError。
        """
        if self.end_time_seconds is not None and self.end_time_seconds <= self.start_time_seconds:
            raise ValueError("end_time_seconds must be greater than start_time_seconds")
        return self
