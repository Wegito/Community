"""Permission codes are declared in code; `flask authz sync` copies them into the database.

Each module declares its codes in its permissions.py with declare(). Code checks codes,
never rank or office names (roadmap §1, principle 2).
"""

from dataclasses import dataclass

PERMISSIONS = {}


@dataclass(frozen=True)
class PermissionDef:
    code: str
    description: str
    module: str
    sensitive: bool = False


def declare(code, description, *, sensitive=False):
    module = code.split('.', 1)[0]
    existing = PERMISSIONS.get(code)
    definition = PermissionDef(code, description, module, sensitive)
    if existing is not None and existing != definition:
        raise ValueError(f'Permission {code!r} declared twice with different settings')
    PERMISSIONS[code] = definition
    return code
