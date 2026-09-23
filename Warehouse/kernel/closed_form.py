"""closed_form.py — equations written once: evaluated to numbers AND rendered to LaTeX.

THE GAP THIS CLOSES.  The repo's closed forms (`Optimization/simconfig/expected_travel.py`,
`coverage.py`, `staffing.py`, `fragmentation.py`) are Python functions whose mathematics lives
only in docstrings and scratch derivations, and `Optimization/config/objectives.py` says of
its own LaTeX that it is NOT derived from the Python.  A formula written twice -- once as code,
once as prose -- is two things to rot, and the prose rots silently: nothing executes it.

Here an equation is ONE expression tree.  The same tree

  * evaluates to a number (`Equation.evaluate`, `Model.evaluate`);
  * renders to LaTeX, bare or with the numbers substituted (`Equation.latex`, `Model.to_markdown`,
    MathJax-compatible, which the docs site already renders through `pymdownx.arithmatex`);
  * draws its dependency graph (`Model.to_dot`);
  * and, when it models a simulator function, names that function (`Mirror`) so
    `Tests/unit/test_closed_form.py` can hold the two equal on seeded random inputs.  That test
    is what makes the rendered LaTeX trustworthy: the formula on the page and the number the
    test compared against the simulator come from the same tree.

WHY IT LIVES IN THE KERNEL, AND IS STDLIB ONLY.  `architecture.yml` forbids `wh_kernel -> *`, so
everything may import this and it may import nothing in the repo -- which is what lets the pick,
put-away and receiving classes carry their own laws as attributes (`PutawayCost.closed_form`,
`UnloadCost.closed_form`, `PickConfig.closed_form`) while the analysis composes them into models
(`Optimization/simconfig/models/`).  The same rule is why the kernel's OWN cost laws are declared
at the bottom of this module rather than in `cost_model.py`: a kernel module may not import a
sibling (the reason `SpeedProfile` lives inside `cost_model`), so they mirror `cost_model`'s
functions BY NAME and the mirror test imports both.

WHAT IT IS NOT.  Not a computer-algebra system: there is no simplification, differentiation or
solving -- `sympy` is not a declared dependency, and a model here is a record of what the
simulator charges, not a derivation engine.  Anything that is not closed algebra (a Panjer
recursion, the expected-routing chain) enters as a `Call` node: an opaque, named callable with
its own LaTeX name, evaluated as a function of the tree's other values.
"""
from __future__ import annotations

import importlib
import math
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Callable, Iterable

# ── numbers on the page ──────────────────────────────────────────────────────────────────────

#: Significant figures a substituted number is printed with.  A rendering choice only: values
#: are never rounded where they are computed.
SIG_FIGS = 4


def fmt_num(x) -> str:
    """`x` for the page: integers exactly, other floats to `SIG_FIGS` significant figures."""
    if isinstance(x, bool):
        return str(x)
    if isinstance(x, int):
        return f'{x:,}'.replace(',', r'{,}')
    x = float(x)
    if math.isnan(x):
        return r'\mathrm{NaN}'
    if math.isinf(x):
        return r'\infty' if x > 0 else r'-\infty'
    if x == int(x) and abs(x) < 1e15:
        return f'{int(x):,}'.replace(',', r'{,}')
    s = f'{x:.{SIG_FIGS}g}'
    if 'e' in s:
        m, e = s.split('e')
        return rf'{m} \times 10^{{{int(e)}}}'
    return s


# ── the expression tree ──────────────────────────────────────────────────────────────────────

# Binding strength for parenthesisation: a child whose precedence is LOWER than the context
# it sits in is wrapped in \left( \right).
_P_ADD, _P_MUL, _P_NEG, _P_POW, _P_ATOM = 1, 2, 3, 4, 5


def _e(x) -> 'Expr':
    """Coerce a Python number to a `Const`; pass an `Expr` through."""
    if isinstance(x, Expr):
        return x
    if isinstance(x, (int, float)):
        return Const(x)
    raise TypeError(f'cannot use {type(x).__name__} {x!r} in an expression')


def _paren(s: str, child_prec: int, ctx_prec: int) -> str:
    return rf'\left({s}\right)' if child_prec < ctx_prec else s


class Expr:
    """A node of an expression tree.  Python operators build trees, so an equation reads as
    the mathematics it states:  `M * (I + q * p + q * v)`."""

    prec = _P_ATOM

    # -- building --------------------------------------------------------------------------
    def __add__(self, o):  return Add(self, _e(o))
    def __radd__(self, o): return Add(_e(o), self)
    def __sub__(self, o):  return Add(self, Neg(_e(o)))
    def __rsub__(self, o): return Add(_e(o), Neg(self))
    def __mul__(self, o):  return Mul(self, _e(o))
    def __rmul__(self, o): return Mul(_e(o), self)
    def __truediv__(self, o):  return Div(self, _e(o))
    def __rtruediv__(self, o): return Div(_e(o), self)
    def __pow__(self, o):  return Pow(self, _e(o))
    def __rpow__(self, o): return Pow(_e(o), self)
    def __neg__(self):     return Neg(self)

    def __lt__(self, o): return Cmp('<', self, _e(o))
    def __le__(self, o): return Cmp('<=', self, _e(o))
    def __gt__(self, o): return Cmp('>', self, _e(o))
    def __ge__(self, o): return Cmp('>=', self, _e(o))

    # A tree is not a value: `==` on nodes would silently build or compare the wrong thing.
    __hash__ = object.__hash__

    # -- the three things a node does ------------------------------------------------------
    def evaluate(self, env: dict) -> float:
        raise NotImplementedError

    def latex(self, env: dict | None = None) -> str:
        """LaTeX for this node; with `env`, every bound symbol is replaced by its value."""
        raise NotImplementedError

    def symbols(self) -> set:
        """The free symbol names this node reads."""
        raise NotImplementedError


@dataclass(eq=False)
class Const(Expr):
    value: float
    tex: str | None = None           # a named constant keeps its name on the page

    def evaluate(self, env):
        return self.value

    def latex(self, env=None):
        return self.tex if self.tex is not None else fmt_num(self.value)

    def symbols(self):
        return set()


@dataclass(eq=False)
class Sym(Expr):
    """A named quantity.  `name` is the key in an environment; `tex` its LaTeX."""
    name: str
    tex: str | None = None

    def evaluate(self, env):
        try:
            return env[self.name]
        except KeyError:
            raise KeyError(f'symbol {self.name!r} has no value; bind it as an input or '
                           f'declare the equation that defines it') from None

    def latex(self, env=None):
        if env is not None and self.name in env and not isinstance(env[self.name], (list, tuple, dict)):
            return fmt_num(env[self.name])
        return self.tex if self.tex is not None else self.name

    def symbols(self):
        return {self.name}


@dataclass(eq=False, init=False)
class Add(Expr):
    terms: tuple
    prec = _P_ADD

    def __init__(self, *terms):
        flat = []
        for t in terms:                      # flatten nested sums so a + b + c renders flat
            flat.extend(t.terms if isinstance(t, Add) else (t,))
        self.terms = tuple(flat)

    def evaluate(self, env):
        # Left to right, one term at a time: the association the simulator's own Python
        # expressions use, so a mirror compares bit-for-bit where the code does the same.
        acc = self.terms[0].evaluate(env)
        for t in self.terms[1:]:
            acc = acc + t.evaluate(env)
        return acc

    def latex(self, env=None):
        out = ''
        for i, t in enumerate(self.terms):
            if isinstance(t, Neg):
                out += ' - ' + _paren(t.x.latex(env), t.x.prec, _P_MUL)
            else:
                s = _paren(t.latex(env), t.prec, _P_ADD)
                out += s if i == 0 else ' + ' + s
        return out

    def symbols(self):
        return set().union(*(t.symbols() for t in self.terms))


@dataclass(eq=False, init=False)
class Mul(Expr):
    factors: tuple
    prec = _P_MUL

    def __init__(self, *factors):
        flat = []
        for f in factors:
            flat.extend(f.factors if isinstance(f, Mul) else (f,))
        self.factors = tuple(flat)

    def evaluate(self, env):
        acc = self.factors[0].evaluate(env)
        for f in self.factors[1:]:
            acc = acc * f.evaluate(env)
        return acc

    def latex(self, env=None):
        parts = [_paren(f.latex(env), f.prec, _P_MUL + (1 if i else 0))
                 for i, f in enumerate(self.factors)]
        # A dot between two things that print as numbers; a thin space everywhere else.
        out = parts[0]
        for a, b, s in zip(self.factors, self.factors[1:], parts[1:]):
            numeric = (_prints_numeric(a, env) and _prints_numeric(b, env))
            out += (r' \cdot ' if numeric else r'\,') + s
        return out

    def symbols(self):
        return set().union(*(f.symbols() for f in self.factors))


def _prints_numeric(node, env) -> bool:
    return isinstance(node, Const) and node.tex is None or (
        isinstance(node, Sym) and env is not None and node.name in env)


@dataclass(eq=False)
class Div(Expr):
    num: Expr
    den: Expr

    def evaluate(self, env):
        return self.num.evaluate(env) / self.den.evaluate(env)

    def latex(self, env=None):
        return rf'\frac{{{self.num.latex(env)}}}{{{self.den.latex(env)}}}'

    def symbols(self):
        return self.num.symbols() | self.den.symbols()


@dataclass(eq=False)
class Pow(Expr):
    base: Expr
    exp: Expr
    prec = _P_POW

    def evaluate(self, env):
        return self.base.evaluate(env) ** self.exp.evaluate(env)

    def latex(self, env=None):
        return f'{_paren(self.base.latex(env), self.base.prec, _P_ATOM)}^{{{self.exp.latex(env)}}}'

    def symbols(self):
        return self.base.symbols() | self.exp.symbols()


@dataclass(eq=False)
class Neg(Expr):
    x: Expr
    prec = _P_NEG

    def evaluate(self, env):
        return -self.x.evaluate(env)

    def latex(self, env=None):
        return '-' + _paren(self.x.latex(env), self.x.prec, _P_MUL)

    def symbols(self):
        return self.x.symbols()


@dataclass(eq=False)
class Cmp(Expr):
    """A comparison, for `Piecewise` conditions.  Evaluates to a bool."""
    op: str
    a: Expr
    b: Expr

    _PY = {'<': lambda a, b: a < b, '<=': lambda a, b: a <= b,
           '>': lambda a, b: a > b, '>=': lambda a, b: a >= b}
    _TEX = {'<': '<', '<=': r'\le', '>': '>', '>=': r'\ge'}

    def evaluate(self, env):
        return self._PY[self.op](self.a.evaluate(env), self.b.evaluate(env))

    def latex(self, env=None):
        return f'{self.a.latex(env)} {self._TEX[self.op]} {self.b.latex(env)}'

    def symbols(self):
        return self.a.symbols() | self.b.symbols()


# name -> (python implementation, LaTeX renderer taking the rendered args)
_FNS: dict = {
    'exp':   (math.exp,  lambda a: rf'e^{{{a[0]}}}'),
    'ln':    (math.log,  lambda a: rf'\ln\left({a[0]}\right)'),
    'sqrt':  (math.sqrt, lambda a: rf'\sqrt{{{a[0]}}}'),
    'ceil':  (math.ceil, lambda a: rf'\left\lceil {a[0]} \right\rceil'),
    'floor': (math.floor, lambda a: rf'\left\lfloor {a[0]} \right\rfloor'),
    'round': (round,     lambda a: rf'\operatorname{{round}}\left({a[0]}\right)'),
    'abs':   (abs,       lambda a: rf'\left|{a[0]}\right|'),
    'max':   (max,       lambda a: rf'\max\left({", ".join(a)}\right)'),
    'min':   (min,       lambda a: rf'\min\left({", ".join(a)}\right)'),
}


@dataclass(eq=False, init=False)
class Fn(Expr):
    """A named elementary function: exp, ln, sqrt, ceil, floor, round, abs, max, min."""
    name: str
    args: tuple

    def __init__(self, name: str, *args):
        if name not in _FNS:
            raise ValueError(f'unknown function {name!r}; known: {sorted(_FNS)}')
        self.name, self.args = name, tuple(_e(a) for a in args)

    def evaluate(self, env):
        return _FNS[self.name][0](*(a.evaluate(env) for a in self.args))

    def latex(self, env=None):
        return _FNS[self.name][1]([a.latex(env) for a in self.args])

    def symbols(self):
        return set().union(*(a.symbols() for a in self.args))


def exp(x):   return Fn('exp', x)
def ln(x):    return Fn('ln', x)
def sqrt(x):  return Fn('sqrt', x)
def ceil(x):  return Fn('ceil', x)
def floor(x): return Fn('floor', x)
def rnd(x):   return Fn('round', x)
def absv(x):  return Fn('abs', x)
def fmax(*xs): return Fn('max', *xs)
def fmin(*xs): return Fn('min', *xs)


@dataclass(eq=False)
class Piecewise(Expr):
    """`cases` is a sequence of `(value, condition)`, taken in order: the FIRST whose condition
    holds wins; `otherwise` when none does.  The order is the semantics -- it mirrors code that
    walks a list and returns on the first match (`cost_model.height_multiplier`)."""
    cases: tuple
    otherwise: Expr

    def evaluate(self, env):
        for value, cond in self.cases:
            if cond.evaluate(env):
                return value.evaluate(env)
        return self.otherwise.evaluate(env)

    def latex(self, env=None):
        rows = [rf'{v.latex(env)} & \text{{if }} {c.latex(env)}' for v, c in self.cases]
        rows.append(rf'{self.otherwise.latex(env)} & \text{{otherwise}}')
        return r'\begin{cases} ' + r' \\ '.join(rows) + r' \end{cases}'

    def symbols(self):
        out = self.otherwise.symbols()
        for v, c in self.cases:
            out |= v.symbols() | c.symbols()
        return out


@dataclass(eq=False)
class Sum(Expr):
    """Σ over the items of a collection.  `env[over]` is a sequence of dicts, each binding the
    per-item symbols named in `binds`; `body` is evaluated once per item with those bindings
    laid over the environment.  `index` is the LaTeX index letter."""
    body: Expr
    over: str
    binds: tuple
    index: str = 's'
    over_tex: str | None = None

    def evaluate(self, env):
        items = env[self.over]
        acc = 0.0
        for item in items:
            acc = acc + self.body.evaluate({**env, **item})
        return acc

    def latex(self, env=None):
        over = self.over_tex or self.over
        if env is not None and self.over in env:
            inner = {k: v for k, v in env.items() if k not in self.binds}
            return rf'\sum_{{{self.index} \in {over}}} {self.body.latex(inner)}'
        return rf'\sum_{{{self.index} \in {over}}} {self.body.latex(None)}'

    def symbols(self):
        return (self.body.symbols() - set(self.binds)) | {self.over}


@dataclass(eq=False)
class Call(Expr):
    """An OPAQUE named callable: how a closed form that is not algebra (a Panjer recursion, the
    expected-routing chain, a bisection) enters a model.  `args` maps the callable's keyword
    parameters to expressions; `tex` is the name printed, e.g. `\\operatorname{fill}`."""
    name: str
    fn: Callable
    args: dict
    tex: str | None = None

    def __post_init__(self):
        self.args = {k: _e(v) for k, v in self.args.items()}

    def evaluate(self, env):
        return self.fn(**{k: a.evaluate(env) for k, a in self.args.items()})

    def latex(self, env=None):
        name = self.tex or rf'\operatorname{{{self.name}}}'
        return rf'{name}\left({", ".join(a.latex(env) for a in self.args.values())}\right)'

    def symbols(self):
        return set().union(*(a.symbols() for a in self.args.values())) if self.args else set()


# ── equations and the simulator code they model ──────────────────────────────────────────────

@dataclass(frozen=True)
class Mirror:
    """The simulator function an equation models, and how to call it from the equation's
    inputs.  `target` is `'package.module:function'`; `args` maps the function's keyword
    parameters to SYMBOL names (or to fixed values through `fixed`); `domain` gives each input
    symbol a `(lo, hi)` range the mirror test draws from.  `rel_tol` is the float tolerance --
    floats compare with a tolerance in this repo, never with `==`."""
    target: str
    args: dict
    domain: dict
    fixed: dict = field(default_factory=dict)
    rel_tol: float = 1e-12

    def resolve(self) -> Callable:
        mod, _, fn = self.target.partition(':')
        return getattr(importlib.import_module(mod), fn)

    def call(self, values: dict) -> float:
        kwargs = {param: values[sym] for param, sym in self.args.items()}
        return self.resolve()(**kwargs, **self.fixed)


@dataclass(eq=False)
class Equation:
    """`symbol = expr`.  `name` is the key the value is stored under in a model's results
    and the name other equations read it by; `symbol` is its LaTeX."""
    name: str
    symbol: str
    expr: Expr
    unit: str = ''
    doc: str = ''
    mirrors: Mirror | None = None

    @property
    def inputs(self) -> set:
        return self.expr.symbols()

    def evaluate(self, env: dict) -> float:
        return self.expr.evaluate(env)

    def latex(self, env: dict | None = None) -> str:
        """`symbol = expr`; with `env`, `symbol = expr = substituted = value`."""
        bare = f'{self.symbol} = {self.expr.latex()}'
        if env is None:
            return bare
        sub = self.expr.latex(env)
        val = self.evaluate(env)
        unit = rf'\ \mathrm{{{self.unit}}}' if self.unit else ''
        if sub == self.expr.latex():                 # nothing substituted: no middle step
            return f'{bare} = {fmt_num(val)}{unit}'
        return f'{bare} = {sub} = {fmt_num(val)}{unit}'


#: Every equation registered with a `Mirror`, by qualified name -- what the mirror gate walks.
#: Filled at import by `register`; `Tests/unit/test_closed_form.py` imports every declaring
#: module and checks each entry against the simulator function it names.
REGISTRY: dict = {}


def register(qualified: str, eq: Equation) -> Equation:
    """Record `eq` under `qualified` (e.g. `'kernel.per_pick'`) and return it.  A second,
    different equation under the same name is refused: two laws answering to one name is the
    drift this module exists to end."""
    prior = REGISTRY.get(qualified)
    if prior is not None and prior is not eq:
        raise ValueError(f'{qualified!r} is already registered to a different equation')
    REGISTRY[qualified] = eq
    return eq


# ── models: equations composed by symbol ─────────────────────────────────────────────────────

@dataclass
class Result:
    """A model's values: its inputs plus every equation's output, and the order they ran in."""
    values: dict
    order: tuple

    def __getitem__(self, name):
        return self.values[name]


@dataclass(eq=False)
class Model:
    """Equations composed into a dependency graph by symbol: an equation that reads a symbol
    another equation defines depends on it.  Everything not defined by an equation is an
    INPUT.  Evaluation runs in topological order; a cycle raises at construction."""
    name: str
    equations: tuple
    doc: str = ''

    def __post_init__(self):
        self.equations = tuple(self.equations)
        names = [e.name for e in self.equations]
        dup = sorted({n for n in names if names.count(n) > 1})
        if dup:
            raise ValueError(f'{self.name}: {dup} defined more than once')
        self._by_name = {e.name: e for e in self.equations}
        self._order = self._toposort()

    def _toposort(self) -> tuple:
        defined = set(self._by_name)
        deps = {e.name: e.inputs & defined for e in self.equations}
        done, order, temp = set(), [], set()

        def visit(n, path):
            if n in done:
                return
            if n in temp:
                raise ValueError(f'{self.name}: dependency cycle {" -> ".join(path + [n])}')
            temp.add(n)
            for d in sorted(deps[n]):
                visit(d, path + [n])
            temp.discard(n)
            done.add(n)
            order.append(n)

        for e in self.equations:                        # declaration order breaks ties
            visit(e.name, [])
        return tuple(order)

    @property
    def inputs(self) -> set:
        defined = set(self._by_name)
        return set().union(*(e.inputs for e in self.equations)) - defined

    def equation(self, name: str) -> Equation:
        return self._by_name[name]

    def evaluate(self, inputs: dict) -> Result:
        missing = sorted(self.inputs - set(inputs))
        if missing:
            raise KeyError(f'{self.name}: inputs {missing} have no value')
        env = dict(inputs)
        for n in self._order:
            env[n] = self._by_name[n].evaluate(env)
        return Result(values=env, order=self._order)

    def sweep(self, name: str, values: Iterable, inputs: dict, outputs: Iterable) -> list:
        """Evaluate once per value of input `name`; return `[{name: v, out: ...}, ...]`."""
        outs = tuple(outputs)
        rows = []
        for v in values:
            r = self.evaluate({**inputs, name: v})
            rows.append({name: v, **{o: r[o] for o in outs}})
        return rows

    # -- the writer ----------------------------------------------------------------------
    def to_markdown(self, result: Result | None = None, *, title: str | None = None,
                    input_tex: dict | None = None) -> str:
        """The model as a page: every equation rendered from its own tree, in evaluation order,
        each with its doc line and -- given a `result` -- the numbers substituted in.  Inputs
        are listed first, with their values when known."""
        lines = [f'## {title or self.name}', '']
        if self.doc:
            lines += [self.doc, '']
        env = result.values if result is not None else None
        ins = sorted(self.inputs)
        if ins:
            lines += ['**Inputs**', '']
            for n in ins:
                tex = (input_tex or {}).get(n, n)
                val = f' = {fmt_num(env[n])}' if env is not None and n in env \
                    and not isinstance(env[n], (list, tuple, dict)) else ''
                lines.append(f'- ${tex}{val}$')
            lines.append('')
        for n in self._order:
            e = self._by_name[n]
            head = f'**{e.name}**' + (f' ({e.unit})' if e.unit else '')
            if e.doc:
                head += f' — {e.doc}'
            if e.mirrors is not None:
                head += f' _(mirrors `{e.mirrors.target}`)_'
            lines += [head, '', f'$$ {e.latex(env)} $$', '']
        return '\n'.join(lines)

    def to_dot(self) -> str:
        """The dependency graph in Graphviz DOT: inputs as boxes, equations as ellipses."""
        out = [f'digraph "{self.name}" {{', '  rankdir=LR;']
        for n in sorted(self.inputs):
            out.append(f'  "{n}" [shape=box];')
        for e in self.equations:
            out.append(f'  "{e.name}" [shape=ellipse];')
            for d in sorted(e.inputs):
                out.append(f'  "{d}" -> "{e.name}";')
        out.append('}')
        return '\n'.join(out)


@dataclass(eq=False)
class Law:
    """A model BOUND to one object's coefficients: what that object charges for one event.

    The form a cost class carries as its `closed_form` attribute (`PutawayCost`, `UnloadCost`,
    `PickConfig`): `model` is the law's equations, `fixed` the object's own coefficients bound
    to their symbols, `output` the equation whose value is the charge.  What is left unbound is
    the EVENT -- where the bin is, how much is moved, how heavy it is -- and `evaluate` takes
    exactly that:  `PutawayCost(...).closed_form.evaluate(x=..., y=..., q=..., ...)`."""
    model: Model
    output: str
    fixed: dict

    @property
    def event_inputs(self) -> set:
        return self.model.inputs - set(self.fixed)

    def result(self, **event) -> Result:
        return self.model.evaluate({**self.fixed, **event})

    def evaluate(self, **event) -> float:
        return self.result(**event)[self.output]

    def to_markdown(self, *, title: str | None = None, **event) -> str:
        """The law as a page; with a complete event, every number substituted."""
        full = self.event_inputs <= set(event)
        return self.model.to_markdown(self.result(**event) if full else None, title=title)


# ── the kernel's own cost laws ───────────────────────────────────────────────────────────────
# Declared here, not in `cost_model.py`, because a kernel module may not import a sibling (see
# the module docstring).  Each mirrors its `cost_model` function by name; the mirror gate calls
# both.  Symbols follow the model statement in `cost_model.py`:  M(y)·(I + q·p + q·v).

M, I, P_ITEM, V, Q = (Sym('M', 'M(y)'), Sym('I'), Sym('p', 'p'), Sym('v', 'v_s'), Sym('q'))
X, Y = Sym('x', 'x_b'), Sym('y', 'y_b')
VX, VY = Sym('vx', 'v_x'), Sym('vy', 'v_y')
W, VOL = Sym('w', 'w_s'), Sym('vol', r'\mathrm{vol}_s')
CW, CV = Sym('cw', 'c_w'), Sym('cv', 'c_v')

INCHES_PER_FOOT = Const(12.0, '12')

PER_PICK = register('kernel.per_pick', Equation(
    'at_location', r'c_{\mathrm{loc}}', M * (I + Q * P_ITEM + Q * V), unit='s',
    doc='the at-location labour of one visit: one intercept per line, a per-unit charge and '
        'a per-unit handling term, all scaled by the height bracket',
    mirrors=Mirror('Warehouse.kernel.cost_model:per_pick',
                   args={'mult': 'M', 'intercept': 'I', 'var': 'v', 'qty': 'q', 'per_item': 'p'},
                   domain={'M': (1.0, 1.4), 'I': (0.5, 20.0), 'v': (0.0, 5.0),
                           'q': (1.0, 20.0), 'p': (0.0, 1.0)})))

TRAVEL = register('kernel.travel', Equation(
    'travel', r't_{\mathrm{travel}}',
    X / (INCHES_PER_FOOT * VX) + Y / (INCHES_PER_FOOT * VY), unit='s',
    doc='mouth-to-bin travel: inches over (12 in/ft · ft/s) on each axis',
    mirrors=Mirror('Warehouse.kernel.cost_model:travel_cost',
                   args={'x_phys': 'x', 'y_phys': 'y', 'x_speed': 'vx', 'y_speed': 'vy'},
                   domain={'x': (0.0, 2400.0), 'y': (0.0, 480.0),
                           'vx': (0.5, 6.0), 'vy': (0.5, 6.0)},
                   rel_tol=1e-12)))


def _transform_expr(spec: str, x: Expr) -> Expr:
    """The handling term's base function as an expression -- `cost_model.resolve_transform`'s
    spec language: 'log' / 'log:e' (ln, clamped at 1), 'log:b', 'linear', 'sqrt' (clamped at 0),
    'pow:p' (clamped at 0)."""
    if ':' in spec:
        name, p = spec.split(':', 1)
        if name == 'pow':
            return fmax(x, 0.0) ** Const(float(p))
        if name == 'log':
            if p == 'e':
                return ln(fmax(x, 1.0))
            return ln(fmax(x, 1.0)) / ln(Const(float(p)))
        raise ValueError(f'unknown parametric transform {name!r}')
    if spec == 'log':
        return ln(fmax(x, 1.0))
    if spec == 'linear':
        return x
    if spec == 'sqrt':
        return sqrt(fmax(x, 0.0))
    raise ValueError(f'unknown transform {spec!r}')


def handle_var_equation(weight_fn: str = 'log', volume_fn: str = 'log') -> Equation:
    """The per-unit handling term `v_s = c_w·f_w(w) + c_v·f_v(vol)` for one pair of base
    functions -- one equation per config, because the functions are a config's choice."""
    return Equation(
        'v', 'v_s', CW * _transform_expr(weight_fn, W) + CV * _transform_expr(volume_fn, VOL),
        doc=f'per-unit handling of weight ({weight_fn}) and volume ({volume_fn})',
        mirrors=Mirror('Warehouse.kernel.cost_model:handle_var',
                       args={'weight': 'w', 'volume': 'vol', 'weight_coef': 'cw',
                             'volume_coef': 'cv'},
                       fixed={'weight_fn': weight_fn, 'volume_fn': volume_fn},
                       domain={'w': (0.1, 80.0), 'vol': (1.0, 80000.0),
                               'cw': (0.0, 1.0), 'cv': (0.0, 1.0)}))


HANDLE_VAR = register('kernel.handle_var', handle_var_equation())


def height_multiplier_equation(brackets: tuple) -> Equation:
    """`M(y)` for one bracket table: the first bracket whose threshold exceeds `y`."""
    cases = tuple((Const(mult), Y < Const(thr)) for thr, mult in brackets[:-1])
    return Equation(
        'M', 'M(y)', Piecewise(cases, Const(brackets[-1][1])),
        doc='the height bracket that scales every at-location term',
        mirrors=Mirror('Warehouse.kernel.cost_model:height_multiplier',
                       args={'y_phys': 'y'}, fixed={'brackets': brackets},
                       domain={'y': (0.0, 480.0)}))


# The default bracket table, mirrored.  `cost_model.DEFAULT_HEIGHT_BRACKETS` is imported by the
# mirror test, never here (the kernel sibling rule); a drift between the two tables is exactly
# what that test catches.
DEFAULT_HEIGHT_BRACKETS = ((96.0, 1.0), (240.0, 1.2), (float('inf'), 1.4))
HEIGHT_MULTIPLIER = register('kernel.height_multiplier',
                             height_multiplier_equation(DEFAULT_HEIGHT_BRACKETS))


# ── the three event laws the cost classes carry ─────────────────────────────────────────────
# Composed BY SYMBOL from the kernel laws above: an equation that reads `at_location` or
# `travel` depends on the equation that defines it, so each law below is the kernel's own
# equations plus the one line that says how that crew combines them.  Built once per
# coefficient SHAPE (bracket table and handling transforms) -- the coefficients' VALUES are
# bound by the class's `Law`, not baked into the tree.

@lru_cache(maxsize=None)
def pick_event_model(brackets: tuple = DEFAULT_HEIGHT_BRACKETS, weight_fn: str = 'log',
                     volume_fn: str = 'log') -> Model:
    """What one pick LINE costs at the bin: `M(y)·(I + q·p + q·v)` (`Pick._pick_time`)."""
    return Model('pick event', (height_multiplier_equation(brackets),
                                handle_var_equation(weight_fn, volume_fn), PER_PICK),
                 doc='One bin visit for one SKU: the intercept once per line, the per-item '
                     'charge and the handling term once per unit, all scaled by the height '
                     'bracket.  The cart-swap penalty is a separate timed step.')


@lru_cache(maxsize=None)
def put_event_model(brackets: tuple = DEFAULT_HEIGHT_BRACKETS, weight_fn: str = 'log',
                    volume_fn: str = 'log') -> Model:
    """What one put costs: travel from the aisle mouth plus the at-location term, at the
    put crew's coefficients (`putaway.put_cost`)."""
    put = Equation('put', r't_{\mathrm{put}}', Sym('travel', r't_{\mathrm{travel}}')
                   + Sym('at_location', r'c_{\mathrm{loc}}'), unit='s',
                   doc='mouth-to-bin travel plus the at-location labour')
    return Model('put event', (height_multiplier_equation(brackets),
                               handle_var_equation(weight_fn, volume_fn), PER_PICK, TRAVEL,
                               put),
                 doc='One pack put away: the same at-location law as a pick, at the put '
                     "crew's coefficients, plus travel from the aisle mouth.")


@lru_cache(maxsize=None)
def unload_event_model(weight_fn: str = 'log', volume_fn: str = 'log') -> Model:
    """What one pack costs to take off a trailer: `p + (I + q·v)` -- no height bracket (a
    trailer floor is one height) and the per-item charge ONCE PER PACK (`unload.unload_cost`)."""
    unload = Equation('unload', r't_{\mathrm{unload}}', P_ITEM + (I + Q * V), unit='s',
                      doc='the intercept and the per-item charge once per pack, the handling '
                          'term once per item in it')
    return Model('unload event', (handle_var_equation(weight_fn, volume_fn), unload),
                 doc='One pack off a trailer.  No travel term and no height multiplier.')
