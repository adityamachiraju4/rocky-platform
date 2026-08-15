"""Tasks capability.

Owns task CRUD beneath a project. Ownership is transitive: it derives from
``CurrentUserDep`` and is enforced by scoping every query through the owning
project. A ``project_id`` from the client is never trusted on its own.
"""
