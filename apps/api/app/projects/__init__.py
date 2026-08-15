"""Projects capability.

Owns project CRUD for the authenticated user. Ownership derives from
``CurrentUserDep`` — never from a client-supplied identifier.
"""
