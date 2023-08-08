# NOTE: All patches must be applied in original locations!
from collections.abc import Callable
from functools import wraps
from typing import TypeAlias

_PatchType: TypeAlias = Callable[[], None]
_patches: set[_PatchType] = set()

__all__=[
    'register',
    'install',
    'aioimaplib_rfc2971'
]
def register(func: _PatchType) -> _PatchType:
    _patches.add(func)
    return func


@register
def aioimaplib_rfc2971() -> None:
    from aioimaplib import aioimaplib as aioimap
    original_rfc2971 = aioimap.arguments_rfs2971

    @wraps(original_rfc2971)
    def patch(**kwds: str) -> list[str]:
        """A patch to fix 'ID' command parsing
        errors in certain situations.
        """
        args = original_rfc2971(**kwds)
        # Combine words next to the brackets together.
        if len(args) <= 3:
            res = [''.join(args)]
        else:
            res = [''.join(args[:2]), *args[2:-2], ''.join(args[-2:])]
        return res
    aioimap.arguments_rfs2971 = patch


def install() -> None:
    for patch in _patches:
        patch()
