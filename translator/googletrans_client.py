"""
googletrans wrapper with retry, batching, and optional caching.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Iterable, List, Optional, Sequence

from googletrans import Translator

from utils.cache import TranslationCache

LOGGER = logging.getLogger(__name__)


@dataclass
class TranslatorSettings:
    batch_size: int = 8
    max_retries: int = 5
    base_delay: float = 0.5
    service_urls: Sequence[str] = ("translate.google.com", "translate.googleapis.com")


class TranslationError(RuntimeError):
    """Raised when translation fails after retries."""


class TranslatorClient:
    def __init__(
        self,
        *,
        cache_path: Optional[str] = None,
        settings: Optional[TranslatorSettings] = None,
    ) -> None:
        self.settings = settings or TranslatorSettings()
        self.cache = TranslationCache(cache_path) if cache_path else None
        service_urls = list(self.settings.service_urls)
        self._translator = Translator(service_urls=service_urls)
        _patch_raise_exception(self._translator)
        self._fallback = Translator(service_urls=service_urls, use_fallback=True)
        _patch_raise_exception(self._fallback)
        if self.cache:
            self.cache.connect()

    def __enter__(self) -> "TranslatorClient":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def close(self) -> None:
        if self.cache:
            self.cache.close()

    def translate_text(self, text: str, *, src: str = "auto", tgt: str = "fa") -> str:
        result = self.translate_batch([text], src=src, tgt=tgt)
        return result[0] if result else ""

    def translate_batch(
        self,
        texts: Iterable[str],
        *,
        src: str = "auto",
        tgt: str = "fa",
    ) -> List[str]:
        source_list = list(texts)
        results: List[Optional[str]] = [None] * len(source_list)
        pending_indices: List[int] = []
        pending_texts: List[str] = []

        cache = self.cache
        for idx, original in enumerate(source_list):
            value = (original or "").strip()
            if not value:
                results[idx] = ""
                continue
            if cache:
                cached = cache.lookup(value, src, tgt)
                if cached is not None:
                    results[idx] = cached
                    continue
            pending_indices.append(idx)
            pending_texts.append(value)

        if pending_texts:
            batches = _chunk(pending_texts, self.settings.batch_size)
            for offset, batch in batches:
                translations = self._translate_with_retry(batch, src, tgt)
                for local_idx, translated in enumerate(translations):
                    absolute_idx = pending_indices[offset + local_idx]
                    results[absolute_idx] = translated
                    if cache:
                        cache.store(pending_texts[offset + local_idx], src, tgt, translated)

        return [value or "" for value in results]

    def _translate_with_retry(self, batch: Sequence[str], src: str, tgt: str) -> List[str]:
        if not batch:
            return []

        attempt = 0
        last_error: Optional[Exception] = None
        src_param = src or "auto"

        while attempt < self.settings.max_retries:
            try:
                LOGGER.debug("Translating batch of %s segments", len(batch))
                return self._translate_via_legacy(self._translator, batch, src_param, tgt)
            except Exception as primary_exc:
                last_error = primary_exc
                LOGGER.debug("Primary translator failed, attempting fallback: %s", primary_exc)
                try:
                    return self._translate_via_legacy(self._fallback, batch, src_param, tgt)
                except Exception as fallback_exc:
                    last_error = fallback_exc
                delay = self.settings.base_delay * (2**attempt)
                attempt += 1
                LOGGER.warning(
                    "Translation attempt %s/%s failed: %s; retrying in %.2fs",
                    attempt,
                    self.settings.max_retries,
                    last_error,
                    delay,
                )
                time.sleep(delay)

        error_message = f"Translation failed after {self.settings.max_retries} attempts: {last_error}"
        raise TranslationError(error_message)

    def _translate_via_legacy(self, client: Translator, batch: Sequence[str], src: str, tgt: str) -> List[str]:
        outputs: List[str] = []
        for text in batch:
            data, response = client._translate_legacy(text, tgt, src, {})
            if response.status_code != 200:
                raise RuntimeError(f'HTTP {response.status_code} from translation backend')
            if not data or not data[0]:
                outputs.append("")
                continue
            translated = ''.join(part[0] or '' for part in data[0])
            outputs.append(translated)
        return outputs


def _patch_raise_exception(translator: Translator) -> None:
    if hasattr(translator, "raise_exception") and not hasattr(translator, "raise_Exception"):
        translator.raise_Exception = translator.raise_exception


def _chunk(items: Sequence[str], size: int) -> Iterable[tuple[int, Sequence[str]]]:
    if size <= 0:
        size = len(items) or 1
    for start in range(0, len(items), size):
        yield start, items[start : start + size]
