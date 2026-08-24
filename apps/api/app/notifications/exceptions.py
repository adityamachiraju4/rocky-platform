"""Typed Notifications domain errors."""


class NotificationError(Exception):
    pass


class NotificationNotFoundError(NotificationError):
    pass


class InvalidNotificationTransitionError(NotificationError):
    pass


class NotificationSourceConflictError(NotificationError):
    pass
