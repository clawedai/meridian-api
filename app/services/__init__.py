"""
Services module for Drishti Intelligence Platform

Main services:
- pipeline.py: Data collection and AI analysis
- processor.py: Background job processing
- scraper.py: Legacy scrapers (deprecated, use pipeline)
- analyzer.py: Legacy analyzer (deprecated, use pipeline)
- notifier.py: Email and webhook notifications
"""

from .pipeline import DataCollector, ContentAnalyzer, IntelligencePipeline
from .processor import (
    run_pipeline_sync,
    run_entity_pipeline_sync,
    process_single_entity,
)

__all__ = [
    "DataCollector",
    "ContentAnalyzer",
    "IntelligencePipeline",
    "run_pipeline_sync",
    "run_entity_pipeline_sync",
    "process_single_entity",
]
