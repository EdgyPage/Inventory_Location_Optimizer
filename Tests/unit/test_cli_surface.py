"""test_cli_surface.py — the CLI is a seam, not 471 lines in the middle of `main`.

# ── what moved ────────────────────────────────────────────────────────────────────

`run_simulation.main` was 875 lines, of which 471 were one uninterrupted run of
`parser.add_argument(...)`.  Four different things lived in that function — the parser, the
CONFIG override loop, the two contract prechecks, and the run itself — and only the last
three have anything to do with each other.  The parser is now `_build_parser()`, and `main`
is the ~400 lines that actually run a simulation.

Nothing about the CLI changed: the same flags, the same defaults, the same help.  That is
the property this file is here to hold, and it holds it against the PARSER OBJECT rather
than against the text of a function.

# ── why that matters beyond tidiness ──────────────────────────────────────────────

Seven test files currently assert things about this CLI with
`inspect.getsource(run_simulation)` substring checks, because until now there was no parser
to inspect without running `main`.  A substring check cannot tell `'--recv-crew-size'` in a
live `add_argument` from the same text inside a comment, a docstring, or a dead branch — and
this repo has already been bitten by a gate that resolved a name while the relationship
behind it was wrong.

`_build_parser()` returns a real `ArgumentParser`, so a gate can now ask the parser what
flags exist and what they default to.  The existing files are left alone (each says
something specific about its own knob, and rewriting seven of them is a different change);
this one demonstrates the stronger form and pins the invariants that hold for all of them.
"""
#: NO sys.path bootstrap here: `Tests/conftest.py` puts the repo root on the path for
#: the whole suite, and CLAUDE.md names it and entry-script bootstraps as the only
#: legal `sys.path.insert` sites.
import argparse
import inspect

import pytest


from Optimization import run_simulation as rs
from Optimization.config.sim_config import CONFIG


@pytest.fixture(scope='module')
def parser():
    return rs._build_parser()


# ── the split itself ──────────────────────────────────────────────────────────────

def test_the_parser_is_built_by_its_own_function():
    assert callable(rs._build_parser)
    assert isinstance(rs._build_parser(), argparse.ArgumentParser)


def test_main_no_longer_builds_the_parser_inline():
    src = inspect.getsource(rs.main)
    assert 'argparse.ArgumentParser(' not in src, \
        'the parser is constructed inline again — the split was undone'
    assert '_build_parser()' in src, 'main must obtain its parser from _build_parser'


def test_main_still_owns_everything_that_is_not_the_parser():
    """The split is ONE cut.  The override loop, the run spec and the prechecks stay in
    `main`, and seven other test files assert against them by name."""
    src = inspect.getsource(rs.main)
    for token in ('parser.parse_args()', 'explicit', "CONFIG['global']"):
        assert token in src, f'{token!r} left main — that was not part of this split'


def test_the_console_reconfigure_still_precedes_parse_args():
    """`--help` is printed and exited from INSIDE `parse_args`, so the cp1252 tolerance has
    to be installed before it or `--help` dies again on a legacy console."""
    src = inspect.getsource(rs.main)
    i_reconf = src.index('reconfigure')
    i_parse = src.index('parse_args()')
    assert i_reconf < i_parse, 'the stdout reconfigure must run before parse_args'


# ── the CLI surface itself, asked of the parser rather than of its source ──────────

def test_the_parser_carries_the_whole_flag_surface(parser):
    """A floor, not an exact list: flags are added over time and this must not be a chore.

    The named ones are load-bearing across the five-seam chain, one per family — run shape,
    the working day, the crews, the era, inbound — so a family that failed to register at
    all fails here rather than at the first run that quietly ignored it.
    """
    flags = {s for a in parser._actions for s in a.option_strings}
    for flag in ('--n-batches', '--seed-world', '--workers',
                 '--work-day-seconds', '--cut-at-day-end',
                 '--put-crew-size', '--recv-crew-size',
                 '--shift-drain-or-cap', '--couple-channels',
                 '--inbound-dock-doors', '--inbound-yard-policy',
                 '--sampler', '--resume-granularity'):
        assert flag in flags, f'{flag} is no longer registered on the parser'
    assert len(flags) > 60, f'only {len(flags)} flags registered — a whole family is missing'


def test_every_flag_has_help_text(parser):
    """`--help` is the only documentation most of these knobs have."""
    bare = [a.option_strings for a in parser._actions
            if a.option_strings and not a.help and a.dest != 'help']
    assert not bare, f'flags registered without help: {bare}'


def test_no_flag_is_registered_twice(parser):
    seen, dupes = set(), []
    for a in parser._actions:
        for s in a.option_strings:
            (dupes.append(s) if s in seen else seen.add(s))
    assert not dupes, f'duplicate flag registrations: {dupes}'


def test_building_the_parser_twice_is_side_effect_free(parser):
    """`_build_parser` reads CONFIG for its defaults; it must not WRITE it.

    Every default is `CONFIG['global'][key]` read at build time — that is what makes a flag's
    default follow the configured value.  A build that mutated CONFIG would make the second
    call disagree with the first, and a test that builds a parser would silently reshape the
    run that follows it.
    """
    before = dict(CONFIG['global'])
    again = rs._build_parser()
    assert dict(CONFIG['global']) == before, '_build_parser mutated CONFIG'
    assert {s for a in again._actions for s in a.option_strings} == \
           {s for a in parser._actions for s in a.option_strings}


def test_no_flag_default_diverges_from_its_config_key(parser):
    """THE INVARIANT the five-seam chain rests on, stated over EVERY flag rather than a list.

    A flag whose `dest` names a CONFIG key must default to one of exactly two things:

      * **the configured value**, read at build time — 63 flags do this, and it is what makes
        `--help` tell the truth about what a run will actually do; or
      * **None**, the explicit "not given" sentinel — 4 flags do this, and `main` then tests
        `if args.<x> is not None` so an unspecified flag leaves CONFIG untouched.  The
        distinction is load-bearing: `--n-batches` cannot use CONFIG's 100 as its default,
        because then "the user typed 100" and "the user typed nothing" are the same input.

    A THIRD case — a default that is neither, i.e. a literal written out here — is the drift
    this test exists to catch.  It would disagree with `settings.py` the moment the setting
    moved, and the run would be shaped by whichever of the two the reader happened to trust.
    There are currently zero, and that is the assertion.
    """
    g = CONFIG['global']
    divergent, from_config, sentinel = [], 0, 0
    for a in parser._actions:
        if not a.option_strings or a.dest not in g:
            continue
        if a.default == g[a.dest]:
            from_config += 1
        elif a.default is None:
            sentinel += 1
        else:
            divergent.append((a.option_strings[0], a.default, g[a.dest]))

    assert not divergent, (
        'these flags default to a literal that disagrees with CONFIG '
        '(flag, its default, CONFIG): ' + repr(divergent))
    assert from_config >= 50, (
        f'only {from_config} flags take their default from CONFIG; a whole family has '
        f'stopped reading it')
    assert sentinel <= 8, (
        f'{sentinel} flags use the None sentinel.  It is correct for a knob whose "not '
        f'given" must differ from its configured value, but it is the exception — a spread '
        f'of them means defaults have quietly stopped coming from CONFIG.')


def test_every_none_sentinel_flag_is_resolved_with_an_is_not_none_test(parser):
    """A `None` default is only safe if `main` actually tests for it.

    TWO DIFFERENT THINGS LOOK LIKE `default=None`, and only one of them is a sentinel:

      * CONFIG's own value IS None — `--work-day-seconds`, `--put-cart-staging`,
        `--inbound-door-team` and fourteen others.  None is the CONFIGURED value there, and
        it means something specific (no whistle, an unbounded floor, uncapped dealing).
        These are ordinary CONFIG-backed defaults that happen to be None, not sentinels.
      * CONFIG's value is NOT None and the flag defaults to None anyway — `--n-batches`,
        `--seed-world`, `--seed-batches`, `--checkpoint-frac`.  These four are the real
        sentinels: the default cannot be CONFIG's value, because then "the user typed 100"
        and "the user typed nothing" would be the same input.

    Only the second kind is checked here, and what is checked is that `main` reads the
    sentinel.  One nobody tests for reaches the run as None and is then either a crash or —
    worse — an `or`-guard that substitutes something plausible.  That is the `--n-batches 0`
    defect exactly: accepted, discarded by a truthiness guard, and a smoke run asking for
    nothing quietly became a full one.
    """
    from Optimization.config.sim_config import KNOB_BY_NAME
    g = CONFIG['global']
    sentinels = [a.dest for a in parser._actions
                 if a.option_strings and a.dest in g
                 and a.default is None and g[a.dest] is not None]
    assert sentinels, 'no sentinel flags found — the query no longer matches the CLI'
    # `main` no longer tests each sentinel by hand: one loop over the registry applies
    # every knob, and a sentinel is one that declares `apply='if_set'` -- which the loop
    # implements as `is not None`, never truthiness.  A sentinel declaring 'always' would
    # overwrite CONFIG with None, which is the defect this test exists for.
    unresolved = [d for d in sentinels
                  if d not in KNOB_BY_NAME or KNOB_BY_NAME[d].apply != 'if_set']
    assert not unresolved, (
        f'{unresolved} default to None while CONFIG holds a real value, but main never tests '
        f'`args.<x> is not None`; the sentinel is unread, so the flag is either a crash or a '
        f'silent substitution')
