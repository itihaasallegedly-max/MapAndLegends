"""Shared exception types.

Every one of these used to be a caught-and-templated condition. The whole
point of naming them is that a run which hits one stops, loudly, instead of
producing a finished video nobody can trust.
"""


class PipelineError(Exception):
    """Base for anything that should abort the daily run."""


class ScriptGenerationError(PipelineError):
    """The model did not return a usable script."""


class FactCheckError(PipelineError):
    """The fact-check gate could not be run. Never treat as a pass."""


class FactCheckRejected(PipelineError):
    """The gate ran and said no. Expected; routes to the hold queue."""


class AssetGenerationError(PipelineError):
    """Cover art, voiceover or video assembly failed."""


class PublishError(PipelineError):
    """Upload failed. The video exists; it is not live."""


class ConfigError(PipelineError):
    """Misconfiguration that would otherwise degrade silently."""
