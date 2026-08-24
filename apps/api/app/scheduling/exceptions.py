"""Typed scheduling errors."""


class SchedulingError(Exception):
    pass


class ScheduledTimeInPastError(SchedulingError):
    pass


class ScheduledJobNotFoundError(SchedulingError):
    pass


class IdempotencyConflictError(SchedulingError):
    pass


class WorkerLeaseError(SchedulingError):
    pass


class InvalidJobTransitionError(SchedulingError):
    pass
