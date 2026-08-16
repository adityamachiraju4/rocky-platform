"""Activity capability.

Owns an append-only ledger of user actions. Ownership is direct: every row
carries the acting ``user_id`` (from ``CurrentUserDep``), and reads are scoped
to that user. Another user's activity is unreachable — 404 when addressed by
id, empty when listed.

The public router is READ-ONLY. Activity is never created through an HTTP POST;
it is emitted by the domain services (Projects, Tasks) via
:class:`~app.activity.recorder.ActivityRecorder`, within the transaction the
domain service already owns, so an event commits atomically with the mutation
it records.
"""
