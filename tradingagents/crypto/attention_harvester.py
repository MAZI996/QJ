"""Automated local attention harvesting into the crypto hotlist."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .attention import candidates_to_hot_symbols, extract_attention_candidates
from .config import CryptoTradingConfig
from .hotlist import merge_hot_symbols


@dataclass(frozen=True)
class AttentionHarvestResult:
    files_read: int
    candidates_found: int
    hotlist_path: Path


class AttentionHarvester:
    """Harvest text files dropped by X/Binance Square/news scrapers."""

    def __init__(self, config: CryptoTradingConfig):
        self.config = config

    def harvest(
        self,
        source_dir: Path | None = None,
        source: str = "auto-harvest",
        min_mentions: int = 1,
        ttl_hours: float = 24.0,
    ) -> AttentionHarvestResult:
        directory = source_dir or self.config.attention_source_dir
        directory.mkdir(parents=True, exist_ok=True)
        chunks: list[str] = []
        files_read = 0
        for path in sorted(directory.glob("*.txt")):
            chunks.append(path.read_text(encoding="utf-8"))
            files_read += 1

        if not chunks:
            return AttentionHarvestResult(0, 0, self.config.hotlist_path)

        candidates = extract_attention_candidates(
            "\n".join(chunks),
            min_mentions=min_mentions,
        )
        entries = candidates_to_hot_symbols(candidates, source=source, ttl_hours=ttl_hours)
        merge_hot_symbols(self.config.hotlist_path, entries)
        return AttentionHarvestResult(
            files_read=files_read,
            candidates_found=len(candidates),
            hotlist_path=self.config.hotlist_path,
        )
