"""Evaluation corpus helpers (eval-dev owned).

``corpus.ground_truth``   load/validate ``ground_truth.json`` from a package
``corpus.adjudication``   adjudicated expected topics for the sample packages
``corpus.adversarial``    prompt-injection page fixtures + output scan
``corpus.clean_control``  synthetic clean package (false-positive control)

Nothing here writes into ``data/``: fixtures are built in a caller-provided
temporary directory.  ``tools/`` and ``data/sample_package*`` are Lead-owned
and never modified.
"""
from __future__ import annotations

from corpus.ground_truth import (
    GT_FILENAME,
    load_ground_truth,
    package_files,
    package_ground_truth_path,
)

__all__ = [
    "GT_FILENAME",
    "load_ground_truth",
    "package_files",
    "package_ground_truth_path",
]
