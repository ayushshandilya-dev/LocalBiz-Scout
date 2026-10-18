"""LocalBiz Scout.

Provides deterministic, offline data-quality rules over business CSVs
(:mod:`localbiz_scout.rules`), a command-line ``analyze`` command that writes
review reports (:mod:`localbiz_scout.cli`, :mod:`localbiz_scout.reports`), and
an optional local-AI adjudicator for ambiguous duplicate candidates
(:mod:`localbiz_scout.ai`). The AI path is opt-in and degrades to the
deterministic result when no local model is available.
"""

__version__ = "0.1.0"

__all__ = ["__version__"]
