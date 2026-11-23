"""Evaluation package for AssetDD AI (eval-dev owned).

Modules
-------
``eval.metrics``    pure metric functions (no DB, no I/O)
``eval.benchmark``  CLI harness that runs the pipeline and writes scorecards
"""
from __future__ import annotations

from eval.metrics import METRIC_VERSION

__all__ = ["METRIC_VERSION"]
