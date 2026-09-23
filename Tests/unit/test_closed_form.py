"""test_closed_form.py -- one expression tree: evaluated, rendered, and held to the simulator.

`Warehouse/kernel/closed_form.py` writes an equation once and derives its number and its LaTeX
from the same tree.  What this file pins:

  1. THE ALGEBRA evaluates as Python would, in the association the simulator's own expressions
     use (left to right), and renders with the parentheses the precedence needs.
  2. A MODEL composes equations by symbol: inputs are what nothing defines, evaluation runs in
     topological order, and a cycle, a duplicate or a missing input refuses loudly.
  3. THE WRITER renders every equation from its tree -- bare, and with numbers substituted --
     and the dependency graph as DOT.
  4. THE MIRROR GATE.  Every equation registered with a `Mirror` is evaluated against the
     simulator function it names, on seeded random inputs, to its tolerance.  This is what
     makes the rendered LaTeX trustworthy: the formula on the page and the number compared here
     are one tree.  A registry that went EMPTY would pass vacuously, so it is also asserted to
     hold at least the kernel's own laws and every declaring module's.

Run:  python -m pytest Tests/unit/test_closed_form.py -q
"""
from __future__ import annotations

import importlib
import math
import random

import pytest

from Warehouse.kernel import closed_form as cf
from Warehouse.kernel.closed_form import (Call, Const, Equation, Model, Piecewise, Sum, Sym,
                                          ceil, exp, fmax, fmin, ln)

#: Every module that declares registered equations.  Imported by the gate so their
#: registrations run; a module added here that registers nothing fails `test_every_declaring_*`.
DECLARING_MODULES = (
    'Warehouse.kernel.closed_form',
    'Optimization.simconfig.models.levels',
)

a, b, c = Sym('a'), Sym('b'), Sym('c')


# ── 1. the algebra ───────────────────────────────────────────────────────────────────────────

def test_operators_build_trees_that_evaluate_like_python():
    env = {'a': 3.0, 'b': 4.0, 'c': 2.0}
    cases = [
        (a + b * c, 3.0 + 4.0 * 2.0),
        ((a + b) * c, (3.0 + 4.0) * 2.0),
        (a - b - c, 3.0 - 4.0 - 2.0),
        (a / b / c, 3.0 / 4.0 / 2.0),
        (a ** c, 9.0),
        (-a + 1, -2.0),
        (2 * a - 1 / b, 2 * 3.0 - 1 / 4.0),
        (exp(-a) + ln(b), math.exp(-3.0) + math.log(4.0)),
        (ceil(a / c) + fmax(a, b) - fmin(a, b), 2 + 4.0 - 3.0),
    ]
    for expr, want in cases:
        assert math.isclose(expr.evaluate(env), want, rel_tol=1e-15), expr.latex()


def test_sums_associate_left_to_right_like_the_code_they_mirror():
    """`((I + q·p) + q·v)` -- the association `per_pick`'s own expression uses, so a mirror
    can compare to the last bit where the code does the same arithmetic."""
    vals = {'a': 0.1, 'b': 0.2, 'c': 0.3}
    assert (a + b + c).evaluate(vals) == (0.1 + 0.2) + 0.3


def test_rendering_parenthesises_by_precedence():
    assert (a + b).latex() == 'a + b'
    assert ((a + b) * c).latex() == r'\left(a + b\right)\,c'
    assert (a * (b + c)).latex() == r'a\,\left(b + c\right)'
    assert (a - (b + c)).latex() == r'a - \left(b + c\right)'
    assert ((a + b) ** c).latex() == r'\left(a + b\right)^{c}'
    assert (a / (b + c)).latex() == r'\frac{a}{b + c}'
    assert exp(-a).latex() == r'e^{-a}'
    assert ceil(a).latex() == r'\left\lceil a \right\rceil'


def test_substitution_renders_values_in_place_of_symbols():
    expr = Sym('n') * Sym('pi', r'\pi_s') * (Sym('mu', r'\mu') + exp(-Sym('mu', r'\mu')))
    assert expr.latex() == r'n\,\pi_s\,\left(\mu + e^{-\mu}\right)'
    sub = expr.latex({'n': 98.0, 'pi': 0.00031, 'mu': 4.0})
    assert '98' in sub and '0.00031' in sub and r'\cdot' in sub


def test_piecewise_takes_the_first_true_case_in_order():
    y = Sym('y')
    pw = Piecewise(((Const(1.0), y < 96.0), (Const(1.2), y < 240.0)), Const(1.4))
    assert [pw.evaluate({'y': v}) for v in (0.0, 95.9, 96.0, 239.0, 240.0, 1e9)] == \
        [1.0, 1.0, 1.2, 1.2, 1.4, 1.4]
    assert r'\begin{cases}' in pw.latex()


def test_a_sum_binds_its_per_item_symbols_and_reads_the_rest():
    body = Sym('w') * Sym('k')
    s = Sum(body, over='items', binds=('w',))
    env = {'k': 2.0, 'items': [{'w': 1.0}, {'w': 3.0}]}
    assert s.evaluate(env) == 8.0
    assert s.symbols() == {'k', 'items'}


def test_a_call_wraps_an_opaque_function_of_the_trees_values():
    node = Call('hyp', lambda x, y: math.hypot(x, y), {'x': a, 'y': b + 1})
    assert node.evaluate({'a': 3.0, 'b': 3.0}) == 5.0
    assert node.symbols() == {'a', 'b'}
    assert node.latex().startswith(r'\operatorname{hyp}\left(')


def test_numbers_print_readably():
    assert cf.fmt_num(3) == '3'
    assert cf.fmt_num(1234567) == '1{,}234{,}567'
    assert cf.fmt_num(0.000123456) == '0.0001235'
    assert cf.fmt_num(2.0) == '2'
    assert r'\times 10^' in cf.fmt_num(1.5e-9)


# ── 2. models ────────────────────────────────────────────────────────────────────────────────

def _chain():
    d = Equation('d', 'd_s', Sym('n') * Sym('pi') * Sym('Eq'), unit='units/day')
    eq = Equation('Eq', r'\mathbb{E}[q]', Sym('mu') + exp(-Sym('mu')))
    L = Equation('L', 'L_s', fmax(1, ceil(Sym('f') * Sym('Eq'))))
    Q = Equation('Q', 'Q_s', fmax(Sym('L'), cf.rnd(Sym('C') * Sym('d'))))
    return Model('levels', (Q, L, d, eq), doc='a toy chain')


def test_a_model_runs_in_dependency_order_whatever_the_declaration_order():
    m = _chain()
    assert m.inputs == {'n', 'pi', 'mu', 'f', 'C'}
    r = m.evaluate({'n': 100.0, 'pi': 0.01, 'mu': 3.0, 'f': 1.3, 'C': 10.0})
    Eq = 3.0 + math.exp(-3.0)
    assert math.isclose(r['Eq'], Eq)
    assert math.isclose(r['d'], 100.0 * 0.01 * Eq)
    assert r['L'] == max(1, math.ceil(1.3 * Eq))
    assert r['Q'] == max(r['L'], round(10.0 * r['d']))
    order = r.order
    assert order.index('Eq') < order.index('d') < order.index('Q')
    assert order.index('L') < order.index('Q')


def test_a_model_refuses_cycles_duplicates_and_missing_inputs():
    with pytest.raises(ValueError, match='cycle'):
        Model('x', (Equation('a', 'a', Sym('b') + 1), Equation('b', 'b', Sym('a') * 2)))
    with pytest.raises(ValueError, match='more than once'):
        Model('x', (Equation('a', 'a', Const(1)), Equation('a', 'a', Const(2))))
    with pytest.raises(KeyError, match="'mu'"):
        _chain().evaluate({'n': 1.0, 'pi': 1.0, 'f': 1.0, 'C': 1.0})


def test_a_sweep_evaluates_once_per_value():
    rows = _chain().sweep('n', [10.0, 20.0], {'pi': 0.1, 'mu': 2.0, 'f': 1.0, 'C': 5.0},
                          outputs=('d',))
    assert [r['n'] for r in rows] == [10.0, 20.0]
    assert math.isclose(rows[1]['d'], 2 * rows[0]['d'])


# ── 3. the writer ────────────────────────────────────────────────────────────────────────────

def test_the_page_renders_every_equation_bare_and_substituted():
    m = _chain()
    bare = m.to_markdown()
    assert bare.count('$$') == 2 * len(m.equations)
    assert r'Q_s = \max\left(L, \operatorname{round}\left(C\,d\right)\right)' in bare
    r = m.evaluate({'n': 100.0, 'pi': 0.01, 'mu': 3.0, 'f': 1.3, 'C': 10.0})
    page = m.to_markdown(r)
    assert '- $n = 100$' in page, 'inputs are listed with their values'
    # every equation line ends in its own computed value
    for e in m.equations:
        assert f'= {cf.fmt_num(r[e.name])}' in page


def test_the_graph_draws_inputs_and_dependencies():
    dot = _chain().to_dot()
    assert '"mu" [shape=box];' in dot and '"Eq" -> "d";' in dot and '"d" -> "Q";' in dot


# ── 4. the mirror gate ───────────────────────────────────────────────────────────────────────

def _load_declaring_modules():
    for mod in DECLARING_MODULES:
        importlib.import_module(mod)


def test_every_declaring_module_registers_equations():
    _load_declaring_modules()
    owners = {name.split('.', 1)[0] for name in cf.REGISTRY}
    assert {'kernel', 'levels'} <= owners
    assert {'kernel.per_pick', 'kernel.travel', 'kernel.handle_var',
            'kernel.height_multiplier'} <= set(cf.REGISTRY), 'the kernel laws went missing'


@pytest.mark.parametrize('seed', range(3))
def test_every_registered_equation_equals_the_simulator_function_it_mirrors(seed):
    _load_declaring_modules()
    rng = random.Random(1000 + seed)
    checked = 0
    for name, eq in sorted(cf.REGISTRY.items()):
        mir = eq.mirrors
        assert mir is not None, f'{name} is registered without a mirror'
        missing = eq.inputs - set(mir.domain)
        assert not missing, f'{name}: inputs {sorted(missing)} have no sampling domain'
        for _ in range(200):
            vals = mir.draw(rng)
            got, want = eq.evaluate(vals), mir.call(vals)
            assert math.isclose(got, want, rel_tol=mir.rel_tol, abs_tol=1e-12), (
                f'{name} = {got!r} but {mir.target} = {want!r} at {vals}')
        checked += 1
    assert checked >= 4


def test_the_mirrored_bracket_table_is_the_simulators():
    from Warehouse.kernel.cost_model import DEFAULT_HEIGHT_BRACKETS
    assert cf.DEFAULT_HEIGHT_BRACKETS == DEFAULT_HEIGHT_BRACKETS


@pytest.mark.parametrize('wfn, vfn', [('log', 'log'), ('pow:1.5', 'log:2'), ('linear', 'sqrt'),
                                      ('log:e', 'pow:0.5')])
def test_the_handling_term_mirrors_every_transform_the_configs_use(wfn, vfn):
    from Warehouse.kernel.cost_model import handle_var
    eq = cf.handle_var_equation(wfn, vfn)
    rng = random.Random(7)
    for _ in range(200):
        vals = {s: rng.uniform(*eq.mirrors.domain[s]) for s in eq.inputs}
        want = handle_var(vals['w'], vals['vol'], vals['cw'], vals['cv'], wfn, vfn)
        assert math.isclose(eq.evaluate(vals), want, rel_tol=1e-12, abs_tol=1e-12)


def test_a_second_equation_under_a_registered_name_is_refused():
    with pytest.raises(ValueError, match='already registered'):
        cf.register('kernel.per_pick', Equation('x', 'x', Const(1)))
