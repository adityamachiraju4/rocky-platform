"""Typed Reminders domain errors."""


class ReminderError(Exception):
    pass


class ReminderNotFoundError(ReminderError):
    pass


class InvalidReminderTransitionError(ReminderError):
    pass


class ReminderNotDueError(ReminderError):
    pass


class ReminderIdempotencyConflictError(ReminderError):
    pass


class InvalidReminderPayloadError(ReminderError):
    pass
