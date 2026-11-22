"""火山引擎豆包语音单向流式合成。"""
from __future__ import annotations

import asyncio
import base64
import json
import pathlib
import uuid

import httpx

from ..config import ROOT
from .audio import SAMPLE_RATE

ENDPOINT = "https://openspeech.bytedance.com/api/v3/tts/unidirectional"
FINISHED = 20000000


class VolcengineTTS:
    def __init__(self, settings: dict):
        self.key_file = settings.get("key_file") or ""
        self.resource_id = settings.get("resource_id") or "seed-tts-2.0"

    def active_base(self) -> str:
        return ENDPOINT

    async def synth_pcm(self, text: str, preset: dict) -> bytes:
        text = text.strip()
        if not text:
            return b""
        speaker = preset.get("name")
        if not speaker:
            raise RuntimeError("未配置火山引擎音色")
        if not self.key_file:
            raise RuntimeError("未配置火山引擎 API Key 文件")
        try:
            key_path = ROOT / pathlib.Path(self.key_file).expanduser()
            api_key = key_path.read_text(encoding="utf-8-sig").strip()
        except (OSError, UnicodeError) as exc:
            raise RuntimeError("无法读取火山引擎 API Key 文件") from exc
        if not api_key:
            raise RuntimeError("火山引擎 API Key 文件为空")

        headers = {
            "X-Api-Key": api_key,
            "X-Api-Resource-Id": self.resource_id,
            "X-Api-Request-Id": str(uuid.uuid4()),
        }
        payload = {"req_params": {
            "text": text,
            "speaker": speaker,
            "audio_params": {"format": "pcm", "sample_rate": SAMPLE_RATE},
        }}
        pcm = bytearray()
        try:
            async with asyncio.timeout(30), httpx.AsyncClient(timeout=10) as client:
                async with client.stream("POST", ENDPOINT, headers=headers, json=payload) as response:
                    log_id = response.headers.get("X-Tt-Logid", "未知")
                    if not response.is_success:
                        raise RuntimeError(f"火山引擎合成失败：HTTP {response.status_code}（日志 ID {log_id}）")
                    async for line in response.aiter_lines():
                        if not line.strip():
                            continue
                        chunk = json.loads(line)
                        if not isinstance(chunk, dict) or not isinstance(chunk.get("code"), int):
                            raise RuntimeError("火山引擎返回的音频分片缺少状态码")
                        code = chunk["code"]
                        if code == FINISHED:
                            break
                        if code != 0:
                            raise RuntimeError(f"火山引擎合成失败：错误码 {code}（日志 ID {log_id}）")
                        if chunk.get("data"):
                            pcm.extend(base64.b64decode(chunk["data"], validate=True))
                    else:
                        raise RuntimeError(f"火山引擎合成未完成（日志 ID {log_id}）")
        except (TimeoutError, httpx.TimeoutException) as exc:
            raise RuntimeError("火山引擎合成超时") from exc
        except httpx.HTTPError as exc:
            raise RuntimeError("火山引擎连接失败") from exc
        except (TypeError, ValueError) as exc:
            raise RuntimeError("火山引擎返回了无效的音频数据") from exc
        if not pcm or len(pcm) % 2:
            raise RuntimeError("火山引擎未返回完整的 16 位 PCM 音频")
        return bytes(pcm)
