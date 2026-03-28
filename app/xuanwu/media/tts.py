"""Text-to-speech support models and helpers."""

from __future__ import annotations

import asyncio
import base64
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Optional

from pydantic import BaseModel, Field


class TTSProvider(Enum):
    """Supported text-to-speech providers."""
    OPENAI = "openai"
    GOOGLE = "google"
    AZURE = "azure"
    ELEVENLABS = "elevenlabs"
    MINIMAX = "minimax"


class TTSVoice(Enum):
    """Built-in voice presets."""
    # OpenAI voices
    ALLOY = "alloy"
    ECHO = "echo"
    FABLE = "fable"
    ONYX = "onyx"
    NOVA = "nova"
    SHIMMER = "shimmer"


class TTSFormat(Enum):
    """output format"""
    MP3 = "mp3"
    WAV = "wav"
    OGG = "ogg"
    FLAC = "flac"
    AAC = "aac"


@dataclass
class TTSConfig:
    """

TTS configuration
    
    Attributes:
        provider:provider
        voice:
        model:model
        speed:(0.25-4.0)
        format:output format
        sample_rate:
    
"""
    provider: TTSProvider = TTSProvider.OPENAI
    voice: str = "alloy"
    model: str = "tts-1"
    speed: float = 1.0
    format: TTSFormat = TTSFormat.MP3
    sample_rate: int = 24000
    
    # configuration
    pitch: float = 0.0  # 
    volume: float = 0.0  # (d-B)
    
    def validate(self) -> tuple[bool, str]:
        """configuration"""
        if not 0.25 <= self.speed <= 4.0:
            return False, "Speed must be between 0.25 and 4.0"
        return True, ""


@dataclass
class TTSResult:
    """

TTS
    
    Attributes:
        success:
        audio_data:count()
        audio_base64:Base64
        format:output format
        duration_seconds:
        characters:character count
        error:
    
"""
    success: bool
    audio_data: Optional[bytes] = None
    audio_base64: str = ""
    format: TTSFormat = TTSFormat.MP3
    duration_seconds: float = 0.0
    characters: int = 0
    provider: str = ""
    error: Optional[str] = None
    metadata: dict[str, Any] = field(default_factory=dict)
    
    def save(self, path: str) -> bool:
        """


        
        Args:
            path:
            
        Returns:
            
        
"""
        if not self.audio_data:
            return False
        
        try:
            Path(path).write_bytes(self.audio_data)
            return True
        except Exception:
            return False
    
    def to_base64(self) -> str:
        """Base64"""
        if self.audio_base64:
            return self.audio_base64
        if self.audio_data:
            return base64.b64encode(self.audio_data).decode("utf-8")
        return ""


class TTSSynthesizer:
    """

TTS
    
    Example usage::
    
        synth = TTSSynthesizer(api_key="...")
        
        result = await synth.synthesize(
            ",.",
            config=TTSConfig(voice="alloy", speed=1.2),
        )
        
        if result.success:
            result.save("output.mp3")
    
"""
    
    def __init__(
        self,
        api_key: str,
        *,
        default_config: Optional[TTSConfig] = None,
    ) -> None:
        """


initialize
 
 Args:
 api_key:API key
 default_config:defaultconfiguration
 
"""
        self.api_key = api_key
        self.default_config = default_config or TTSConfig()
    
    async def synthesize(
        self,
        text: str,
        *,
        config: Optional[TTSConfig] = None,
    ) -> TTSResult:
        """
Text-to-speech synthesis
        
        Args:
            text:input text
            config:TTS configuration(optional, usedefaultconfiguration)
            
        Returns:
            Synthesis result
        
"""
        cfg = config or self.default_config
        
        # configuration
        valid, error = cfg.validate()
        if not valid:
            return TTSResult(success=False, error=error)
        
        # based onprovider
        if cfg.provider == TTSProvider.OPENAI:
            return await self._synthesize_openai(text, cfg)
        else:
            return TTSResult(
                success=False,
                error=f"Unsupported provider: {cfg.provider.value}",
            )
    
    async def _synthesize_openai(
        self,
        text: str,
        config: TTSConfig,
    ) -> TTSResult:
        """use OpenAI TTS"""
        import time
        import httpx
        
        start = time.monotonic()
        
        try:
            async with httpx.AsyncClient(trust_env=True) as client:

                response = await client.post(
                    "https://api.openai.com/v1/audio/speech",
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": config.model,
                        "input": text,
                        "voice": config.voice,
                        "speed": config.speed,
                        "response_format": config.format.value,
                    },
                    timeout=60.0,
                )
                
                if response.status_code != 200:
                    return TTSResult(
                        success=False,
                        error=f"API error: {response.status_code} - {response.text}",
                        provider="openai",
                    )
                
                audio_data = response.content
                
                # estimate(estimate:150 /300 characters,)
                char_count = len(text)
                estimated_duration = (char_count / 300) * 60 / config.speed
                
                return TTSResult(
                    success=True,
                    audio_data=audio_data,
                    format=config.format,
                    duration_seconds=estimated_duration,
                    characters=char_count,
                    provider="openai",
                    metadata={
                        "model": config.model,
                        "voice": config.voice,
                        "speed": config.speed,
                        "synthesis_time_ms": int((time.monotonic() - start) * 1000),
                    },
                )
                
        except Exception as e:
            return TTSResult(
                success=False,
                error=str(e),
                provider="openai",
            )
    
    async def synthesize_streaming(
        self,
        text: str,
        *,
        config: Optional[TTSConfig] = None,
        chunk_size: int = 4096,
    ):
        """

streamingText-to-speech synthesis
        
        Args:
            text:input text
            config:TTS configuration
            chunk_size:
            
        Yields:
            count
        
"""
        cfg = config or self.default_config
        
        # 
        valid, error = cfg.validate()
        if not valid:
            raise ValueError(error)
        
        if cfg.provider == TTSProvider.OPENAI:
            async for chunk in self._synthesize_openai_streaming(text, cfg, chunk_size):
                yield chunk
        else:
            raise NotImplementedError(f"Streaming not supported for {cfg.provider.value}")
    
    async def _synthesize_openai_streaming(
        self,
        text: str,
        config: TTSConfig,
        chunk_size: int,
    ):
        """Stream audio chunks from the OpenAI speech API."""
        import httpx
        
        async with httpx.AsyncClient(trust_env=True) as client:
            async with client.stream(

                "POST",
                "https://api.openai.com/v1/audio/speech",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": config.model,
                    "input": text,
                    "voice": config.voice,
                    "speed": config.speed,
                    "response_format": config.format.value,
                },
                timeout=60.0,
            ) as response:
                if response.status_code != 200:
                    error_text = await response.aread()
                    raise RuntimeError(f"API error: {response.status_code} - {error_text}")
                
                async for chunk in response.aiter_bytes(chunk_size):
                    yield chunk


class TTSManager:
    """

TTS manager
    
    managemulti TTS providerand.
    
"""
    
    def __init__(self) -> None:
        self._synthesizers: dict[TTSProvider, TTSSynthesizer] = {}
        self._cache: dict[str, TTSResult] = {}
        self._cache_enabled = True
    
    def register_provider(
        self,
        provider: TTSProvider,
        api_key: str,
        *,
        default_config: Optional[TTSConfig] = None,
    ) -> None:
        """
registerprovider
        
        Args:
            provider:provider
            api_key:API key
            default_config:defaultconfiguration
        
"""
        config = default_config or TTSConfig(provider=provider)
        config.provider = provider
        self._synthesizers[provider] = TTSSynthesizer(
            api_key=api_key,
            default_config=config,
        )
    
    async def synthesize(
        self,
        text: str,
        *,
        provider: Optional[TTSProvider] = None,
        config: Optional[TTSConfig] = None,
        use_cache: bool = True,
    ) -> TTSResult:
        """

Text-to-speech synthesis
        
        Args:
            text:input text
            provider:provider(optional, use available)
            config:TTS configuration
            use_cache:use
            
        Returns:
            Synthesis result
        
"""
        # provider
        if provider:
            synth = self._synthesizers.get(provider)
            if not synth:
                return TTSResult(
                    success=False,
                    error=f"Provider not registered: {provider.value}",
                )
        elif self._synthesizers:
            synth = next(iter(self._synthesizers.values()))
        else:
            return TTSResult(
                success=False,
                error="No TTS provider registered",
            )
        
        # check
        if use_cache and self._cache_enabled:
            cache_key = self._cache_key(text, config)
            if cache_key in self._cache:
                return self._cache[cache_key]
        
        # 
        result = await synth.synthesize(text, config=config)
        
        # 
        if result.success and use_cache and self._cache_enabled:
            cache_key = self._cache_key(text, config)
            self._cache[cache_key] = result
        
        return result
    
    def _cache_key(
        self,
        text: str,
        config: Optional[TTSConfig],
    ) -> str:
        """"""
        import hashlib
        
        cfg = config or TTSConfig()
        key_str = f"{text}:{cfg.provider.value}:{cfg.voice}:{cfg.speed}:{cfg.format.value}"
        return hashlib.md5(key_str.encode()).hexdigest()
    
    def clear_cache(self) -> int:
        """



 
 Returns:
 entry count
 
"""
        count = len(self._cache)
        self._cache.clear()
        return count
