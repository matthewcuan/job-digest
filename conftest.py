# Presence of this file at the project root makes pytest add the root to sys.path,
# so `import job_aggregator` resolves to the package directory.
import pytest


@pytest.fixture(autouse=True)
def _freeze_clock(monkeypatch):
    """Freeze ``now_utc()`` to the fixtures' ``NOW`` for every test.

    Test fixtures stamp jobs with a constant ``NOW`` (tests/helpers.py), but the pipeline,
    ranker, and storage compare against the real wall clock via ``now_utc()``. Without this,
    date-window and recency tests silently rot as real time drifts past ``NOW``. Freezing both
    sides to the same instant makes the suite deterministic regardless of when it runs.
    """
    from tests.helpers import NOW
    import job_aggregator.util as util
    import job_aggregator.pipeline as pipeline
    import job_aggregator.rank as rank
    import job_aggregator.email_renderer as email_renderer
    import job_aggregator.storage as storage

    def frozen():
        return NOW

    # Patch util plus every module that imported now_utc into its own namespace.
    monkeypatch.setattr(util, "now_utc", frozen)
    for mod in (pipeline, rank, email_renderer, storage):
        monkeypatch.setattr(mod, "now_utc", frozen, raising=False)
