"""Amazon Nova Sonic — the live voice persona, over a bidirectional stream.

Speech-to-speech on Bedrock through `InvokeModelWithBidirectionalStream`,
using AWS's async SDK for bidirectional streaming (boto3 does not support it).
This module only speaks the protocol; app/realtime/bedrock_proxy.py decides
what flows where.

The protocol is a sequence of JSON events in each direction:

  to Nova Sonic    sessionStart, promptStart (voice, audio formats, tools),
                   SYSTEM text (the persona prompt), prior turns as text when a
                   stream is renewed, then a long USER audio content with
                   audioInput chunks (16 kHz 16-bit mono PCM, base64), toolResult
                   contents answering toolUse, and finally contentEnd /
                   promptEnd / sessionEnd.
  from Nova Sonic  contentStart (role, type, generation stage), textOutput
                   (transcripts — the student's speech as recognised, and the
                   persona's words), audioOutput (24 kHz PCM, base64),
                   toolUse, contentEnd (stopReason), completionEnd.

Raw audio passes through in memory only; nothing here stores it.
"""

from __future__ import annotations

import json
import uuid
from typing import Any, AsyncIterator, Optional, Protocol

from app.ai.aws import BotocoreIdentityResolver
from app.config import settings

INPUT_SAMPLE_RATE = 16000
OUTPUT_SAMPLE_RATE = 24000

RETRIEVE_TOOL_NAME = "retrieve_context"
RETRIEVE_TOOL_SPEC = {
    "toolSpec": {
        "name": RETRIEVE_TOOL_NAME,
        "description": (
            "Look up grounded facts about this stakeholder persona or about the "
            "Harbortown world. Call this whenever the user asks a specific factual "
            "question — names, places, plans, history, statistics, opinions on file. "
            "Skip for greetings and small talk."
        ),
        "inputSchema": {
            "json": json.dumps({
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Concise search query summarizing what to look up",
                    }
                },
                "required": ["query"],
            })
        },
    }
}


class SpeechStream(Protocol):
    """What the proxy needs from a speech-to-speech stream (real or test double)."""

    async def send(self, event: dict) -> None: ...
    def events(self) -> AsyncIterator[dict]: ...
    async def close(self) -> None: ...


class NovaSonicStream:
    """`SpeechStream` over the AWS SDK's duplex event stream."""

    def __init__(self, duplex: Any) -> None:
        self._duplex = duplex

    async def send(self, event: dict) -> None:
        from aws_sdk_bedrock_runtime.models import (
            BidirectionalInputPayloadPart,
            InvokeModelWithBidirectionalStreamInputChunk,
        )

        payload = json.dumps({"event": event}).encode("utf-8")
        await self._duplex.input_stream.send(
            InvokeModelWithBidirectionalStreamInputChunk(
                value=BidirectionalInputPayloadPart(bytes_=payload)
            )
        )

    async def events(self) -> AsyncIterator[dict]:
        _, receiver = await self._duplex.await_output()
        async for chunk in receiver:
            value = getattr(chunk, "value", None)
            raw = getattr(value, "bytes_", None)
            if raw is None:
                # A modelled error event (throttling, timeout, validation...).
                raise RuntimeError(type(chunk).__name__)
            message = json.loads(raw)
            event = message.get("event")
            if isinstance(event, dict):
                yield event

    async def close(self) -> None:
        try:
            await self._duplex.input_stream.close()
        except Exception:
            pass


async def open_nova_sonic() -> SpeechStream:
    """Open a stream to Nova Sonic with server-side AWS credentials."""
    from aws_sdk_bedrock_runtime.client import AsyncBedrockRuntimeClient
    from aws_sdk_bedrock_runtime.config import AsyncBedrockRuntimeConfig
    from aws_sdk_bedrock_runtime.models import InvokeModelWithBidirectionalStreamOperationInput

    config = await AsyncBedrockRuntimeConfig.resolve(
        region=settings.aws_region,
        aws_credentials_identity_resolver=BotocoreIdentityResolver(),
    )
    client = AsyncBedrockRuntimeClient(config=config)
    duplex = await client.invoke_model_with_bidirectional_stream(
        InvokeModelWithBidirectionalStreamOperationInput(model_id=settings.bedrock_speech_model_id)
    )
    return NovaSonicStream(duplex)


# --- outbound events -------------------------------------------------------------


class Prompt:
    """Builds the events for one prompt (one stream's conversation)."""

    def __init__(self, voice_id: str) -> None:
        self.name = str(uuid.uuid4())
        self.voice_id = voice_id
        self.audio_content = str(uuid.uuid4())

    @staticmethod
    def session_start() -> dict:
        return {"sessionStart": {"inferenceConfiguration": {
            "maxTokens": 1024, "topP": 0.9, "temperature": 0.7,
        }}}

    def prompt_start(self) -> dict:
        return {"promptStart": {
            "promptName": self.name,
            "textOutputConfiguration": {"mediaType": "text/plain"},
            "audioOutputConfiguration": {
                "mediaType": "audio/lpcm", "sampleRateHertz": OUTPUT_SAMPLE_RATE,
                "sampleSizeBits": 16, "channelCount": 1, "voiceId": self.voice_id,
                "encoding": "base64", "audioType": "SPEECH",
            },
            "toolUseOutputConfiguration": {"mediaType": "application/json"},
            "toolConfiguration": {"tools": [RETRIEVE_TOOL_SPEC]},
        }}

    def text(self, role: str, text: str) -> list[dict]:
        """A complete non-interactive text content: SYSTEM prompt or a past turn."""
        content = str(uuid.uuid4())
        return [
            {"contentStart": {
                "promptName": self.name, "contentName": content, "type": "TEXT",
                "interactive": False, "role": role,
                "textInputConfiguration": {"mediaType": "text/plain"},
            }},
            {"textInput": {"promptName": self.name, "contentName": content, "content": text}},
            {"contentEnd": {"promptName": self.name, "contentName": content}},
        ]

    def audio_start(self) -> dict:
        return {"contentStart": {
            "promptName": self.name, "contentName": self.audio_content, "type": "AUDIO",
            "interactive": True, "role": "USER",
            "audioInputConfiguration": {
                "mediaType": "audio/lpcm", "sampleRateHertz": INPUT_SAMPLE_RATE,
                "sampleSizeBits": 16, "channelCount": 1, "audioType": "SPEECH",
                "encoding": "base64",
            },
        }}

    def audio_chunk(self, b64: str) -> dict:
        return {"audioInput": {
            "promptName": self.name, "contentName": self.audio_content, "content": b64,
        }}

    def tool_result(self, tool_use_id: str, result: str) -> list[dict]:
        content = str(uuid.uuid4())
        return [
            {"contentStart": {
                "promptName": self.name, "contentName": content, "interactive": False,
                "type": "TOOL", "role": "TOOL",
                "toolResultInputConfiguration": {
                    "toolUseId": tool_use_id, "type": "TEXT",
                    "textInputConfiguration": {"mediaType": "text/plain"},
                },
            }},
            {"toolResult": {
                "promptName": self.name, "contentName": content,
                "content": json.dumps({"context": result}),
            }},
            {"contentEnd": {"promptName": self.name, "contentName": content}},
        ]

    def finish(self) -> list[dict]:
        return [
            {"contentEnd": {"promptName": self.name, "contentName": self.audio_content}},
            {"promptEnd": {"promptName": self.name}},
            {"sessionEnd": {}},
        ]


def generation_stage(content_start: dict) -> Optional[str]:
    """SPECULATIVE / FINAL for assistant text, from additionalModelFields."""
    raw = content_start.get("additionalModelFields")
    if not raw:
        return None
    try:
        return json.loads(raw).get("generationStage")
    except (ValueError, AttributeError):
        return None
