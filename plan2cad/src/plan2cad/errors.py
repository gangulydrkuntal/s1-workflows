"""Exception types. Every failure the pipeline can anticipate has its own type and a readable message."""


class Plan2CadError(Exception):
    """Base class for all expected pipeline errors."""


class ConfigError(Plan2CadError):
    """The per-drawing YAML configuration is invalid. `.problems` lists every problem found."""

    def __init__(self, problems, path=None):
        self.problems = list(problems)
        self.path = path
        head = f"Invalid configuration{f' ({path})' if path else ''}:"
        super().__init__(head + "".join(f"\n  - {p}" for p in self.problems))


class IngestError(Plan2CadError):
    """The source image is missing, unreadable or inconsistent with the configuration."""


class EngineError(Plan2CadError):
    """A conversion engine could not complete (bad input for that engine, nothing detected, ...)."""


class OutputError(Plan2CadError):
    """A written output failed its read-back check."""
