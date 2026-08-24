"""Typed Notes domain errors."""


class NoteError(Exception):
    pass


class NoteNotFoundError(NoteError):
    pass


class InvalidNoteTransitionError(NoteError):
    pass
