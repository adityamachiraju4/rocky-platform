"""Memory ownership and lifecycle errors."""


class MemoryNotFoundError(Exception):
    pass


class InvalidMemoryTransitionError(Exception):
    pass
