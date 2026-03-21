"""Arbiter autonomous mission runner."""

from __future__ import annotations

import warnings

warnings.filterwarnings(
    "ignore",
    message=r"Core Pydantic V1 functionality isn't compatible with Python 3\.14 or greater\.",
    module=r"langchain_core\._api\.deprecation",
)

__all__ = ["__version__"]

__version__ = "0.1.0"
