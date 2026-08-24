"""Typed Lists domain errors."""


class ListError(Exception):
    pass


class ListNotFoundError(ListError):
    pass


class ListItemNotFoundError(ListError):
    pass


class InvalidListTransitionError(ListError):
    pass


class InvalidListItemTransitionError(ListError):
    pass
