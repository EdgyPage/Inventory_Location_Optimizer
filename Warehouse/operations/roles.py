"""roles — what an actor DOES and what it does it ON.

Two axes, deliberately separate, because they vary independently: a `pick` can be done on
`foot` or on a `machine`, and so can a `put`.  Four combinations, each with its own travel
speed — which is the whole reason this file exists.

The mode axis is not new; it has just never been readable.  `Optimization/config/channels.py`
has named its two pools `'store_machine'` and `'fulfillment_walker'` for as long as they have
existed, and `simconfig/constants.py` comments them as "machine order-picker pool" and "human
-walker pool".  Nothing anywhere reads either name: no forklift, machine, vehicle or equipment
concept existed in the repo.  The distinction was real, documented in prose, and inert.

## Why `str` enums

A role and a mode are written into a DB column and read back, and they appear in run
metadata.  `str`-valued members compare equal to their own text, so a stored `'machine'`
round-trips without a conversion layer, and an f-string renders the value rather than
`Mode.MACHINE`.  The cost is that any string comparison silently succeeds — hence
`Role.of` / `Mode.of` below, which are the only sanctioned way to turn text back into a
member and which raise on anything unrecognised.
"""
from __future__ import annotations

from enum import Enum


class _Named(str, Enum):
    """Shared behaviour: render as the bare value, and parse strictly."""

    def __str__(self) -> str:                      # so f'{Mode.FOOT}' is 'foot'
        return self.value

    @classmethod
    def of(cls, value):
        """Parse `value` into a member, raising on anything unrecognised.

        The strict counterpart to being a `str` enum.  Reading a role out of a DB gives you
        text, and `'machien' == Role.PICK` is simply False rather than an error — so a typo
        in stored data would quietly route an actor nowhere.  Every text→member conversion
        goes through here.
        """
        if isinstance(value, cls):
            return value
        try:
            return cls(value)
        except ValueError:
            names = ', '.join(m.value for m in cls)
            raise ValueError(f'{value!r} is not a {cls.__name__}; expected one of: {names}')


class Role(_Named):
    """What the actor is doing with inventory: taking it out, or putting it away."""
    PICK = 'pick'
    PUT = 'put'


class Mode(_Named):
    """How the actor moves: on foot, or on a machine.

    The axis that decides speed.  A machine drives faster along an aisle than a person
    walks and lifts differently up a rack — and because x (horizontal run) and y (vertical
    lift) are separate quantities, a machine is a DIFFERENT `SpeedProfile` from a walker,
    not a scaled one.
    """
    FOOT = 'foot'
    MACHINE = 'machine'
