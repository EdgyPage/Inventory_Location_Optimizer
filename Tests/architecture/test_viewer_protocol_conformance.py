"""test_viewer_protocol_conformance.py — the SimReader protocol IS the viewer's data model.

Before this file the Protocol had drifted four methods behind the implementation and nothing
enforced it anywhere (`isinstance` against `SimReader` appeared in no production or test
code) — the data models were pinned by integration tests and JS consumers, not by the
declared interface.  These tests make the Protocol authoritative in both directions and tie
the server's routes to it, per the producer-broker-consumer mandate: routes are CONSUMERS
and may only ask the broker for protocol members.

Run:  python -m pytest Tests/architecture/test_viewer_protocol_conformance.py -q
"""
from __future__ import annotations

import inspect
import os
import re

from Visualization.readers.base import SqliteSimReader
from Visualization.readers.protocol import SimReader

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))


def _protocol_members():
    return {n for n, v in vars(SimReader).items()
            if not n.startswith('_') and callable(v)}


def _public_reader_methods():
    return {n for n, v in vars(SqliteSimReader).items()
            if not n.startswith('_') and callable(v)}


def test_every_protocol_member_is_implemented():
    missing = _protocol_members() - _public_reader_methods()
    assert not missing, f'SimReader declares methods SqliteSimReader lacks: {sorted(missing)}'


def test_every_public_reader_method_is_declared():
    """The reverse direction — the drift that actually happened.  A public method on the
    implementation that the Protocol does not declare is an undocumented data model."""
    extra = _public_reader_methods() - _protocol_members()
    assert not extra, (f'SqliteSimReader grew public method(s) the Protocol does not declare: '
                       f'{sorted(extra)} — declare them (with the return shape) or prefix _')


def test_a_bound_reader_satisfies_the_runtime_checkable_protocol(tmp_path):
    """Structural (names-only, runtime_checkable's contract) — but it is the check that was
    never made anywhere, and it now fails the moment a member is removed."""
    class _Stub(SqliteSimReader):
        SCHEMA_IDS = ('stub',)

    stub = _Stub.__new__(_Stub)                     # no DB needed for a names-only check
    assert isinstance(stub, SimReader)


def test_server_routes_call_only_protocol_members():
    """Scan server.py for `.reader().<name>(` / `reader.<name>(` call sites: every reader
    method a route touches must be a protocol member."""
    with open(os.path.join(_ROOT, 'Visualization', 'server.py'), encoding='utf-8') as fh:
        src = fh.read()
    called = set(re.findall(r'_reader\(rid\)\.(\w+)\(', src))
    called |= set(re.findall(r'\breader\.(\w+)\(', src))
    unknown = called - _protocol_members()
    assert not unknown, (f'server.py calls reader method(s) outside the Protocol: '
                         f'{sorted(unknown)}')
    assert called, 'the route scan found no reader calls — its pattern rotted'


def test_top_skus_default_is_50_everywhere():
    """protocol == implementation == the route literal.  They used to say nothing/100/50."""
    proto_n = inspect.signature(SimReader.top_skus).parameters['n'].default
    impl_n = inspect.signature(SqliteSimReader.top_skus).parameters['n'].default
    assert proto_n == impl_n == 50
    with open(os.path.join(_ROOT, 'Visualization', 'server.py'), encoding='utf-8') as fh:
        assert "_int_arg('n', 50)" in fh.read()
