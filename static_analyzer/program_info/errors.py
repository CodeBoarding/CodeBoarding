"""Program-information validation failures."""


class ProgramInformationError(ValueError):
    """Base error for invalid program information."""


class UnknownEndpointError(ProgramInformationError):
    """An edge references a symbol absent from the graph."""


class InvalidWeightError(ProgramInformationError):
    """Evidence has a negative or non-finite weight."""


class InvalidClusterCoverError(ProgramInformationError):
    """A cluster partition does not cover every symbol exactly once."""
