"""After Effects expressions, evaluated offline.

A small JavaScript interpreter (the subset AE expressions use: var/let/const, if/else, for/while, functions, arrays,
objects, ternaries, try/catch, string and array methods) with AE's vector arithmetic ([a,b] + [c,d]), and the AE
expression globals on top of py-aep objects: thisComp, thisLayer, comp(), layer(), effect()(), content(), mask(),
transform shorthands, value / time / valueAtTime / velocity / key() / numKeys, loopOut / loopIn, linear / ease*,
clamp, length, normalize, random / seedRandom / wiggle (deterministic, not AE's exact noise), sourceRectAtTime,
toComp / fromComp, posterizeTime, hsl / rgb helpers, createPath / points().

Every evaluation records whether it depended on time (time, an animated property, random, wiggle, loops…):
an expression that does not is evaluated once and becomes a static value.
"""
import math
import random as _random
import re

from .util import clean


class ExprError(Exception):
    pass


class _Return(Exception):
    def __init__(self, value):
        self.value = value


class _Break(Exception):
    pass


class _Continue(Exception):
    pass


class _Throw(Exception):
    def __init__(self, value):
        super().__init__(str(value))
        self.value = value


EMPTY = object()

# ================================================================== tokenizer
_TOK = re.compile(r"""
 (?P<ws>[ \t\r\n\f\v ﻿  ]+)
|(?P<com>//[^\n\r]*|/\*.*?\*/)
|(?P<num>0[xX][0-9a-fA-F]+|(?:\d+\.\d*|\.\d+|\d+)(?:[eE][+-]?\d+)?)
|(?P<str>"(?:\\.|[^"\\\n])*"|'(?:\\.|[^'\\\n])*'|`(?:\\.|[^`\\])*`)
|(?P<name>[A-Za-z_$À-￿][A-Za-z0-9_$À-￿]*)
|(?P<op>\.\.\.|>>>=|===|!==|>>>|<<=|>>=|\*\*|==|!=|<=|>=|&&|\|\||\?\?|\+\+|--|\+=|-=|\*=|/=|%=|&=|\|=|\^=|=>|<<|>>|[-+*/%<>=!?:.,;(){}\[\]&|^~])
""", re.S | re.X)

_ESC = {"n": "\n", "t": "\t", "r": "\r", "b": "\b", "f": "\f", "v": "\v", "0": "\0"}


def _unescape(s):
    out, i = [], 0
    while i < len(s):
        c = s[i]
        if c == "\\" and i + 1 < len(s):
            n = s[i + 1]
            if n in _ESC:
                out.append(_ESC[n])
                i += 2
            elif n == "u" and i + 5 < len(s) + 1:
                try:
                    out.append(chr(int(s[i + 2:i + 6], 16)))
                    i += 6
                except ValueError:
                    out.append(n)
                    i += 2
            elif n == "x":
                try:
                    out.append(chr(int(s[i + 2:i + 4], 16)))
                    i += 4
                except ValueError:
                    out.append(n)
                    i += 2
            elif n in "\r\n":
                i += 2
            else:
                out.append(n)
                i += 2
        else:
            out.append(c)
            i += 1
    return "".join(out)


_RE_LIT = re.compile(r"/((?:\\.|\[(?:\\.|[^\]\\\n])*\]|[^/\\\n\[])+)/([a-z]*)")
_NO_RE_BEFORE = {")", "]", "}", "++", "--"}
_RE_KEYWORDS = {"return", "typeof", "case", "in", "of", "new", "delete", "void", "throw", "instanceof"}


def _regex_allowed(toks):
    """a '/' starts a regular expression unless it follows an operand (JS's rule of thumb)"""
    if not toks:
        return True
    kind, val, _ = toks[-1]
    if kind in ("num", "str", "re"):
        return False
    if kind == "name":
        return val in _RE_KEYWORDS
    return val not in _NO_RE_BEFORE


def tokenize(src):
    toks, pos, nl = [], 0, False
    while pos < len(src):
        if src[pos] == "/" and src[pos + 1:pos + 2] not in ("/", "*") and _regex_allowed(toks):
            m = _RE_LIT.match(src, pos)
            if m:
                toks.append(("re", (m.group(1), m.group(2)), nl))
                nl = False
                pos = m.end()
                continue
        m = _TOK.match(src, pos)
        if not m:
            raise ExprError(f"unexpected character {src[pos]!r}")
        kind = m.lastgroup
        text = m.group(kind)
        pos = m.end()
        if kind in ("ws", "com"):
            if "\n" in text or "\r" in text:
                nl = True
            continue
        if kind == "num":
            val = float(int(text, 16)) if text[:2].lower() == "0x" else float(text)
        elif kind == "str":
            val = _unescape(text[1:-1])
        else:
            val = text
        toks.append((kind, val, nl))
        nl = False
    toks.append(("eof", None, True))
    return toks


# ================================================================== parser
_ASSIGN = {"=", "+=", "-=", "*=", "/=", "%=", "&=", "|=", "^="}
_PREC = {"??": 1, "||": 2, "&&": 3, "|": 4, "^": 5, "&": 6, "==": 7, "!=": 7, "===": 7, "!==": 7,
         "<": 8, ">": 8, "<=": 8, ">=": 8, "instanceof": 8, "in": 8, "<<": 9, ">>": 9, ">>>": 9,
         "+": 10, "-": 10, "*": 11, "/": 11, "%": 11, "**": 12}


class Parser:
    def __init__(self, src):
        self.t = tokenize(src)
        self.i = 0

    def peek(self, k=0):
        return self.t[min(self.i + k, len(self.t) - 1)]

    def next(self):
        tok = self.t[self.i]
        self.i = min(self.i + 1, len(self.t) - 1)
        return tok

    def at(self, kind, val=None):
        tok = self.peek()
        return tok[0] == kind and (val is None or tok[1] == val)

    def at_op(self, val):
        return self.at("op", val)

    def eat(self, kind, val=None):
        if self.at(kind, val):
            return self.next()
        return None

    def expect(self, kind, val=None):
        tok = self.eat(kind, val)
        if tok is None:
            got = self.peek()
            raise ExprError(f"expected {val or kind}, got {got[1]!r}")
        return tok

    def program(self):
        body = []
        while not self.at("eof"):
            body.append(self.statement())
        return ("block", body)

    def semi(self):
        self.eat("op", ";")

    def statement(self):
        tok = self.peek()
        if tok[0] == "op":
            if tok[1] == "{":
                return self.block()
            if tok[1] == ";":
                self.next()
                return ("empty",)
        if tok[0] == "name":
            v = tok[1]
            if v in ("var", "let", "const"):
                d = self.var_decl()
                self.semi()
                return d
            if v == "function" and self.peek(1)[0] == "name":
                self.next()
                name = self.expect("name")[1]
                params = self.params()
                return ("fundecl", name, params, self.block())
            if v == "if":
                self.next()
                self.expect("op", "(")
                c = self.expression()
                self.expect("op", ")")
                a = self.statement()
                b = None
                if self.eat("name", "else"):
                    b = self.statement()
                return ("if", c, a, b)
            if v == "for":
                return self.for_stmt()
            if v == "while":
                self.next()
                self.expect("op", "(")
                c = self.expression()
                self.expect("op", ")")
                return ("while", c, self.statement())
            if v == "do":
                self.next()
                body = self.statement()
                self.expect("name", "while")
                self.expect("op", "(")
                c = self.expression()
                self.expect("op", ")")
                self.semi()
                return ("do", body, c)
            if v == "return":
                self.next()
                nt = self.peek()
                if nt[0] == "eof" or (nt[0] == "op" and nt[1] in (";", "}")) or nt[2]:
                    self.semi()
                    return ("ret", None)
                e = self.expression()
                self.semi()
                return ("ret", e)
            if v in ("break", "continue"):
                self.next()
                if self.at("name") and not self.peek()[2]:
                    self.next()                                  # label (ignored)
                self.semi()
                return ("break",) if v == "break" else ("cont",)
            if v == "throw":
                self.next()
                e = self.expression()
                self.semi()
                return ("throw", e)
            if v == "try":
                self.next()
                body = self.block()
                cname, cblock, fblock = None, None, None
                if self.eat("name", "catch"):
                    if self.eat("op", "("):
                        cname = self.expect("name")[1]
                        self.expect("op", ")")
                    cblock = self.block()
                if self.eat("name", "finally"):
                    fblock = self.block()
                return ("try", body, cname, cblock, fblock)
            if v == "switch":
                return self.switch_stmt()
        e = self.expression()
        self.semi()
        return ("expr", e)

    def switch_stmt(self):
        self.next()
        self.expect("op", "(")
        d = self.expression()
        self.expect("op", ")")
        self.expect("op", "{")
        cases = []
        while not self.eat("op", "}"):
            if self.eat("name", "case"):
                test = self.expression()
            else:
                self.expect("name", "default")
                test = None
            self.expect("op", ":")
            body = []
            while not (self.at("name", "case") or self.at("name", "default") or self.at_op("}")):
                body.append(self.statement())
            cases.append((test, body))
        return ("switch", d, cases)

    def pattern(self):
        """const {a, b: c = 1} = … / const [a, , b = 2] = … -> ("obj" | "arr", [(key, target, default)])"""
        if self.eat("op", "{"):
            items = []
            while not self.eat("op", "}"):
                key = self.next()[1]
                target = key
                if self.eat("op", ":"):
                    target = self.pattern() if self.at_op("{") or self.at_op("[") else self.expect("name")[1]
                default = self.assign() if self.eat("op", "=") else None
                items.append((key, target, default))
                self.eat("op", ",")
            return ("obj", items)
        self.expect("op", "[")
        items, k = [], 0
        while not self.eat("op", "]"):
            if self.eat("op", ","):
                k += 1
                continue
            target = self.pattern() if self.at_op("{") or self.at_op("[") else self.expect("name")[1]
            default = self.assign() if self.eat("op", "=") else None
            items.append((k, target, default))
            k += 1
            self.eat("op", ",")
        return ("arr", items)

    def var_decl(self):
        kind = self.next()[1]                     # var (function scope) / let / const (block scope)
        decls = []
        while True:
            name = self.pattern() if self.at_op("{") or self.at_op("[") else self.expect("name")[1]
            init = None
            if self.eat("op", "="):
                init = self.assign()
            decls.append((name, init))
            if not self.eat("op", ","):
                break
        return ("var", decls, kind)

    def block(self):
        self.expect("op", "{")
        body = []
        while not self.eat("op", "}"):
            if self.at("eof"):
                raise ExprError("unclosed block")
            body.append(self.statement())
        return ("block", body)

    def params(self):
        self.expect("op", "(")
        ps = []
        while not self.eat("op", ")"):
            name = self.expect("name")[1]
            default = None
            if self.eat("op", "="):
                default = self.assign()
            ps.append((name, default))
            self.eat("op", ",")
        return ps

    def for_stmt(self):
        self.next()
        self.expect("op", "(")
        init = None
        if not self.at_op(";"):
            if self.at("name") and self.peek()[1] in ("var", "let", "const"):
                if self.peek(2)[0] == "name" and self.peek(2)[1] in ("in", "of"):
                    kw = self.next()
                    name = self.expect("name")[1]
                    kind = self.next()[1]
                    obj = self.expression()
                    self.expect("op", ")")
                    return ("forin", kind, name, obj, self.statement())
                init = self.var_decl()
            else:
                init = ("expr", self.expression())
        self.expect("op", ";")
        cond = None if self.at_op(";") else self.expression()
        self.expect("op", ";")
        upd = None if self.at_op(")") else self.expression()
        self.expect("op", ")")
        return ("for", init, cond, upd, self.statement())

    def expression(self):
        e = self.assign()
        while self.eat("op", ","):
            e = ("seq", e, self.assign())
        return e

    def assign(self):
        left = self.cond()
        tok = self.peek()
        if tok[0] == "op" and tok[1] in _ASSIGN:
            self.next()
            if left[0] not in ("name", "mem", "idx"):
                raise ExprError("invalid assignment target")
            return ("assign", tok[1], left, self.assign())
        return left

    def cond(self):
        c = self.binary(1)
        if self.eat("op", "?"):
            a = self.assign()
            self.expect("op", ":")
            b = self.assign()
            return ("cond", c, a, b)
        return c

    def binary(self, minp):
        left = self.unary()
        while True:
            tok = self.peek()
            op = tok[1] if tok[0] in ("op", "name") else None
            p = _PREC.get(op)
            if p is None or p < minp:
                return left
            self.next()
            right = self.binary(p if op == "**" else p + 1)
            left = ("log" if op in ("&&", "||", "??") else "bin", op, left, right)

    def unary(self):
        tok = self.peek()
        if tok[0] == "op" and tok[1] in ("!", "-", "+", "~"):
            self.next()
            return ("un", tok[1], self.unary())
        if tok[0] == "op" and tok[1] in ("++", "--"):
            self.next()
            return ("pre", tok[1], self.unary())
        if tok[0] == "name" and tok[1] in ("typeof", "void", "delete"):
            self.next()
            return ("un", tok[1], self.unary())
        return self.postfix()

    def postfix(self):
        e = self.callmember()
        tok = self.peek()
        if tok[0] == "op" and tok[1] in ("++", "--") and not tok[2]:
            self.next()
            return ("post", tok[1], e)
        return e

    def args(self):
        self.expect("op", "(")
        a = []
        while not self.eat("op", ")"):
            a.append(("spread", self.assign()) if self.eat("op", "...") else self.assign())
            if not self.at_op(")"):
                self.expect("op", ",")
        return a

    def callmember(self):
        if self.at("name", "new"):
            self.next()
            callee = self.primary()
            while self.eat("op", "."):
                callee = ("mem", callee, self.expect("name")[1])
            args = self.args() if self.at_op("(") else []
            e = ("new", callee, args)
        else:
            e = self.primary()
        while True:
            if self.eat("op", "."):
                e = ("mem", e, self.expect("name")[1])
            elif self.at_op("("):
                e = ("call", e, self.args())
            elif self.at_op("["):
                self.next()
                k = self.expression()
                self.expect("op", "]")
                e = ("idx", e, k)
            else:
                return e

    def primary(self):
        tok = self.next()
        kind, val, _nl = tok
        if kind == "num":
            return ("lit", val)
        if kind == "str":
            return ("lit", val)
        if kind == "re":
            return ("lit", JSRegExp(*val))
        if kind == "name":
            if val == "true":
                return ("lit", True)
            if val == "false":
                return ("lit", False)
            if val in ("null", "undefined"):
                return ("lit", None)
            if val == "function":
                name = self.eat("name")
                params = self.params()
                return ("func", name[1] if name else None, params, self.block())
            if self.at_op("=>"):
                self.next()
                return self.arrow([(val, None)])
            return ("name", val)
        if kind == "op" and val == "(":
            # arrow function with a parameter list?
            j, depth = self.i, 1
            while depth and j < len(self.t) - 1:
                if self.t[j][1] == "(":
                    depth += 1
                elif self.t[j][1] == ")":
                    depth -= 1
                j += 1
            if self.t[j][0] == "op" and self.t[j][1] == "=>":
                params = []
                while not self.eat("op", ")"):
                    params.append((self.expect("name")[1], None))
                    self.eat("op", ",")
                self.expect("op", "=>")
                return self.arrow(params)
            e = self.expression()
            self.expect("op", ")")
            return e
        if kind == "op" and val == "[":
            items = []
            while not self.eat("op", "]"):
                if self.at_op(","):
                    self.next()
                    items.append(("lit", None))
                    continue
                items.append(("spread", self.assign()) if self.eat("op", "...") else self.assign())
                if not self.at_op("]"):
                    self.expect("op", ",")
            return ("arr", items)
        if kind == "op" and val == "{":
            props = []
            while not self.eat("op", "}"):
                kt = self.next()
                if kt[0] not in ("name", "str", "num"):
                    raise ExprError("bad object key")
                key = kt[1] if kt[0] != "num" else _numstr(kt[1])
                if self.eat("op", ":"):
                    props.append((key, self.assign()))
                else:
                    props.append((key, ("name", key)))
                if not self.at_op("}"):
                    self.expect("op", ",")
            return ("obj", props)
        raise ExprError(f"unexpected {val!r}")

    def arrow(self, params):
        if self.at_op("{"):
            return ("func", None, params, self.block())
        body = self.assign()
        return ("func", None, params, ("block", [("ret", body)]))


# ================================================================== values
class JSObject(dict):
    """a plain JS object"""


class JSFunction:
    def __init__(self, name, params, body, scope, interp):
        self.name, self.params, self.body, self.scope, self.interp = name, params, body, scope, interp

    def __call__(self, *args):
        return self.call_with(None, args)

    def call_with(self, this, args):
        """call with `this` bound: the object of obj.method(), the new object of `new F()`"""
        s = Scope(self.scope, fn=True)
        s.declare("this", this)
        for k, (p, default) in enumerate(self.params):
            v = args[k] if k < len(args) else None
            if v is None and default is not None:
                v = self.interp.ev(default, s)
            s.declare(p, v)
        s.declare("arguments", list(args))
        try:
            self.interp.exec_block(self.body[1], s)
        except _Return as r:
            return r.value
        return None


class Scope:
    __slots__ = ("vars", "parent", "fn")

    def __init__(self, parent=None, fn=False):
        self.vars = {}
        self.parent = parent
        self.fn = fn or parent is None            # a function body or the expression itself: where `var` lives

    def function_scope(self):
        s = self
        while not s.fn and s.parent is not None:
            s = s.parent
        return s

    def find(self, name):
        s = self
        while s is not None:
            if name in s.vars:
                return s
            s = s.parent
        return None

    def declare(self, name, value):
        self.vars[name] = value

    def root(self):
        s = self
        while s.parent is not None:
            s = s.parent
        return s


def _below(s, top):
    """is scope s the scope `top` or nested in it"""
    while s is not None:
        if s is top:
            return True
        s = s.parent
    return False


def _numstr(v):
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, float):
        if math.isnan(v):
            return "NaN"
        if math.isinf(v):
            return "Infinity" if v > 0 else "-Infinity"
        if v == int(v) and abs(v) < 1e21:
            return str(int(v))
        return repr(v)
    return str(v)


def tostr(v):
    v = unwrap(v)
    if v is None:
        return "undefined"
    if isinstance(v, str):
        return v
    if isinstance(v, (int, float, bool)):
        return _numstr(v if not isinstance(v, int) or isinstance(v, bool) else float(v))
    if isinstance(v, list):
        return ",".join("" if x is None else tostr(x) for x in v)
    if isinstance(v, JSObject):
        return "[object Object]"
    return str(v)


def tonum(v):
    v = unwrap(v)
    if isinstance(v, bool):
        return 1.0 if v else 0.0
    if isinstance(v, (int, float)):
        return float(v)
    if v is None:
        return float("nan")
    if isinstance(v, str):
        try:
            return float(v.strip()) if v.strip() else 0.0
        except ValueError:
            return float("nan")
    if isinstance(v, list):
        if len(v) == 0:
            return 0.0
        if len(v) == 1:
            return tonum(v[0])
        return float("nan")
    return float("nan")


def truthy(v):
    v = unwrap(v)
    if v is None or v is False:
        return False
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return v != 0 and not math.isnan(v)
    if isinstance(v, str):
        return v != ""
    return True


def unwrap(v):
    """AE property objects used as values -> their value."""
    f = getattr(v, "js_value", None)
    if f is not None:
        return f()
    return v


def _vec_op(a, b, fn):
    if isinstance(a, list) and isinstance(b, list):
        n = max(len(a), len(b))
        return [fn(tonum(a[i]) if i < len(a) else 0.0, tonum(b[i]) if i < len(b) else 0.0) for i in range(n)]
    if isinstance(a, list):
        bb = tonum(b)
        return [fn(tonum(x), bb) for x in a]
    if isinstance(b, list):
        aa = tonum(a)
        return [fn(aa, tonum(x)) for x in b]
    return fn(tonum(a), tonum(b))


def _div(x, y):
    if y == 0:
        if x == 0 or math.isnan(x):
            return float("nan")
        return math.copysign(float("inf"), x) * (1 if math.copysign(1, y) > 0 else -1)
    return x / y


def _mod(x, y):
    if y == 0 or math.isinf(x):
        return float("nan")
    return math.fmod(x, y)


def js_add(a, b):
    a, b = unwrap(a), unwrap(b)
    if isinstance(a, str) or isinstance(b, str) or isinstance(a, JSObject) or isinstance(b, JSObject):
        return tostr(a) + tostr(b)
    return _vec_op(a, b, lambda x, y: x + y)


def js_eq(a, b, strict):
    a, b = unwrap(a), unwrap(b)
    if a is None or b is None:
        return a is None and b is None
    if isinstance(a, (list, JSObject)) or isinstance(b, (list, JSObject)):
        return a is b
    if strict:
        if isinstance(a, str) != isinstance(b, str):
            return False
        if isinstance(a, bool) != isinstance(b, bool):
            return False
    if isinstance(a, str) and isinstance(b, str):
        return a == b
    if isinstance(a, str) or isinstance(b, str):
        return tonum(a) == tonum(b)
    return tonum(a) == tonum(b)


def _cmp(a, b, fn):
    a, b = unwrap(a), unwrap(b)
    if isinstance(a, str) and isinstance(b, str):
        return fn(a, b)
    x, y = tonum(a), tonum(b)
    if math.isnan(x) or math.isnan(y):
        return False
    return fn(x, y)


def binop(op, a, b):
    if op == "+":
        return js_add(a, b)
    if op == "-":
        return _vec_op(unwrap(a), unwrap(b), lambda x, y: x - y)
    if op == "*":
        a, b = unwrap(a), unwrap(b)
        if isinstance(a, list) and isinstance(b, list):
            return _vec_op(a, b, lambda x, y: x * y)
        return _vec_op(a, b, lambda x, y: x * y)
    if op == "/":
        return _vec_op(unwrap(a), unwrap(b), _div)
    if op == "%":
        return _vec_op(unwrap(a), unwrap(b), _mod)
    if op == "**":
        return tonum(a) ** tonum(b)
    if op in ("==", "==="):
        return js_eq(a, b, op == "===")
    if op in ("!=", "!=="):
        return not js_eq(a, b, op == "!==")
    if op == "<":
        return _cmp(a, b, lambda x, y: x < y)
    if op == ">":
        return _cmp(a, b, lambda x, y: x > y)
    if op == "<=":
        return _cmp(a, b, lambda x, y: x <= y)
    if op == ">=":
        return _cmp(a, b, lambda x, y: x >= y)
    if op in ("&", "|", "^", "<<", ">>", ">>>"):
        x, y = _i32(tonum(a)), _i32(tonum(b))
        if op == "&":
            return float(_i32(x & y))
        if op == "|":
            return float(_i32(x | y))
        if op == "^":
            return float(_i32(x ^ y))
        if op == "<<":
            return float(_i32(x << (y & 31)))
        if op == ">>":
            return float(x >> (y & 31))
        return float((x & 0xFFFFFFFF) >> (y & 31))
    if op == "in":
        b = unwrap(b)
        if isinstance(b, (dict, JSObject)):
            return tostr(a) in b
        if isinstance(b, list):
            try:
                return 0 <= int(tonum(a)) < len(b)
            except (ValueError, OverflowError):
                return False
        return False
    if op == "instanceof":
        b = unwrap(b)
        if b is ARRAY_CTOR:
            return isinstance(unwrap(a), list)
        return False
    raise ExprError(f"operator {op}")


def _i32(x):
    if math.isnan(x) or math.isinf(x):
        return 0
    x = int(x) & 0xFFFFFFFF
    return x - (1 << 32) if x & 0x80000000 else x


# ================================================================== builtins (JS side)
class JSRegExp:
    """a JS regular expression literal (/…/flags) on Python's re (same syntax for what expressions use)"""

    def __init__(self, source, flags=""):
        self.source, self.flags = source, flags
        f = (re.I if "i" in flags else 0) | (re.M if "m" in flags else 0) | (re.S if "s" in flags else 0)
        try:
            self.rx = re.compile(re.sub(r"\(\?<([A-Za-z_]\w*)>", r"(?P<\1>", source), f)
        except re.error as ex:
            raise ExprError(f"regular expression /{source}/: {ex}")
        self.glob = "g" in flags

    def js_get(self, name):
        if name == "test":
            return lambda *a: self.rx.search(tostr(a[0]) if a else "undefined") is not None
        if name == "exec":
            def ex(*a):
                m = self.rx.search(tostr(a[0]) if a else "undefined")
                return None if m is None else [m.group(0)] + [g for g in m.groups()]
            return ex
        if name in ("source", "flags"):
            return getattr(self, name)
        if name == "global":
            return self.glob
        raise ExprError(f"RegExp.{name}")


def _js_repl(repl):
    """JS replacement string ($&, $1…) -> a function of a Python match"""
    def f(m):
        out, i = [], 0
        while i < len(repl):
            c = repl[i]
            if c == "$" and i + 1 < len(repl):
                n = repl[i + 1]
                if n == "&":
                    out.append(m.group(0))
                    i += 2
                    continue
                if n == "$":
                    out.append("$")
                    i += 2
                    continue
                if n.isdigit():
                    k = int(n)
                    if i + 2 < len(repl) and repl[i + 2].isdigit() and int(repl[i + 1:i + 3]) <= (m.re.groups or 0):
                        k, i = int(repl[i + 1:i + 3]), i + 1
                    if 0 < k <= (m.re.groups or 0):
                        out.append(m.group(k) or "")
                        i += 2
                        continue
            out.append(c)
            i += 1
        return "".join(out)
    return f


def _str_split(s, a):
    if not a or a[0] is None:
        return [s]
    sep = a[0]
    if isinstance(sep, JSRegExp):
        parts = sep.rx.split(s)
        return ["" if x is None else x for x in parts]
    sep = tostr(sep)
    return list(s) if sep == "" else s.split(sep)


def _str_replace(s, a, everything=False):
    pat, repl = (a[0] if a else None), (a[1] if len(a) > 1 else "undefined")
    fn = repl if callable(repl) else None
    if isinstance(pat, JSRegExp):
        cnt = 0 if (pat.glob or everything) else 1
        if fn is not None:
            return pat.rx.sub(lambda m: tostr(fn(m.group(0), *m.groups())), s, count=cnt)
        return pat.rx.sub(_js_repl(tostr(repl)), s, count=cnt)
    pat = tostr(pat)
    if fn is not None:
        return s.replace(pat, tostr(fn(pat)), -1 if everything else 1)
    return s.replace(pat, tostr(repl), -1 if everything else 1)


def _str_match(s, a):
    pat = a[0] if a else None
    rx = pat.rx if isinstance(pat, JSRegExp) else re.compile(re.escape(tostr(pat)))
    if isinstance(pat, JSRegExp) and pat.glob:
        found = [m.group(0) for m in rx.finditer(s)]
        return found or None
    m = rx.search(s)
    return None if m is None else [m.group(0)] + [g for g in m.groups()]


class TextValue(str):
    """Source Text: a string that also answers getStyleAt() / .style from its py-aep text document"""

    def __new__(cls, text, doc=None):
        o = super().__new__(cls, text)
        o.doc = doc
        return o

    def style_object(self):
        d = self.doc
        g = lambda a, default=None: getattr(d, a, default) if d is not None else default
        size = float(g("font_size", 12) or 12)
        auto = bool(g("auto_leading", True)) if g("auto_leading", None) is not None else True
        lead = float(g("leading", 0) or 0) or size * 1.2
        fill = g("fill_color")
        stroke = g("stroke_color")
        return JSObject({
            "fontSize": size, "font": clean(str(g("font", "") or "")), "leading": lead, "autoLeading": auto,
            "tracking": float(g("tracking", 0) or 0), "baselineShift": float(g("baseline_shift", 0) or 0),
            "fillColor": [float(c) for c in fill] if fill is not None else [0.0, 0.0, 0.0],
            "strokeColor": [float(c) for c in stroke] if stroke is not None else [0.0, 0.0, 0.0],
            "strokeWidth": float(g("stroke_width", 0) or 0),
            "applyFill": g("apply_fill") is not False, "applyStroke": bool(g("apply_stroke", False)),
            "isAllCaps": bool(g("all_caps", False)), "isFauxBold": bool(g("faux_bold", False)),
            "isFauxItalic": bool(g("faux_italic", False)), "horizontalScaling": float(g("horizontal_scale", 1) or 1),
            "verticalScaling": float(g("vertical_scale", 1) or 1), "text": str(self),
        })


def _string_method(s, name):
    if isinstance(s, TextValue):
        if name == "getStyleAt":
            return lambda *a: s.style_object()
        if name == "style":
            return s.style_object()
    def arg_i(args, k, default):
        return int(tonum(args[k])) if len(args) > k and args[k] is not None else default
    table = {
        "charAt": lambda *a: s[arg_i(a, 0, 0)] if 0 <= arg_i(a, 0, 0) < len(s) else "",
        "charCodeAt": lambda *a: float(ord(s[arg_i(a, 0, 0)])) if 0 <= arg_i(a, 0, 0) < len(s) else float("nan"),
        "indexOf": lambda *a: float(s.find(tostr(a[0]), arg_i(a, 1, 0))),
        "lastIndexOf": lambda *a: float(s.rfind(tostr(a[0]))),
        "substring": lambda *a: _substring(s, a),
        "substr": lambda *a: s[arg_i(a, 0, 0): (arg_i(a, 0, 0) + arg_i(a, 1, len(s))) if len(a) > 1 else None],
        "slice": lambda *a: s[slice(arg_i(a, 0, 0), arg_i(a, 1, len(s)) if len(a) > 1 else None)],
        "split": lambda *a: _str_split(s, a),
        "match": lambda *a: _str_match(s, a),
        "search": lambda *a: float(m.start()) if (m := (a[0].rx if isinstance(a[0], JSRegExp) else re.compile(re.escape(tostr(a[0])))).search(s)) else -1.0,
        "toUpperCase": lambda *a: s.upper(),
        "toLowerCase": lambda *a: s.lower(),
        "trim": lambda *a: s.strip(),
        "replace": lambda *a: _str_replace(s, a),
        "replaceAll": lambda *a: _str_replace(s, a, everything=True),
        "concat": lambda *a: s + "".join(tostr(x) for x in a),
        "includes": lambda *a: tostr(a[0]) in s,
        "startsWith": lambda *a: s.startswith(tostr(a[0])),
        "endsWith": lambda *a: s.endswith(tostr(a[0])),
        "repeat": lambda *a: s * max(0, arg_i(a, 0, 0)),
        "padStart": lambda *a: s.rjust(arg_i(a, 0, 0), tostr(a[1]) if len(a) > 1 else " "),
        "padEnd": lambda *a: s.ljust(arg_i(a, 0, 0), tostr(a[1]) if len(a) > 1 else " "),
        "toString": lambda *a: s,
        "valueOf": lambda *a: s,
    }
    if name == "length":
        return float(len(s))
    if name in table:
        return table[name]
    raise ExprError(f"string.{name}")


def _substring(s, a):
    i = int(tonum(a[0])) if a else 0
    j = int(tonum(a[1])) if len(a) > 1 and a[1] is not None else len(s)
    i, j = max(0, min(i, len(s))), max(0, min(j, len(s)))
    return s[min(i, j):max(i, j)]


def _array_method(arr, name, interp):
    def fn(f):
        return lambda *a: f(*a)
    if name == "length":
        return float(len(arr))
    if name == "push":
        return fn(lambda *a: (arr.extend(a), float(len(arr)))[1])
    if name == "pop":
        return fn(lambda *a: arr.pop() if arr else None)
    if name == "shift":
        return fn(lambda *a: arr.pop(0) if arr else None)
    if name == "unshift":
        return fn(lambda *a: (arr.__setitem__(slice(0, 0), list(a)), float(len(arr)))[1])
    if name == "slice":
        return fn(lambda *a: arr[slice(int(tonum(a[0])) if a else 0, int(tonum(a[1])) if len(a) > 1 else None)])
    if name == "concat":
        return fn(lambda *a: arr + [y for x in a for y in (unwrap(x) if isinstance(unwrap(x), list) else [x])])
    if name == "join":
        return fn(lambda *a: (tostr(a[0]) if a else ",").join("" if x is None else tostr(x) for x in arr))
    if name == "indexOf":
        return fn(lambda *a: float(next((i for i, x in enumerate(arr) if js_eq(x, a[0], True)), -1)))
    if name == "includes":
        return fn(lambda *a: any(js_eq(x, a[0], True) for x in arr))
    if name == "reverse":
        return fn(lambda *a: (arr.reverse(), arr)[1])
    if name == "map":
        return fn(lambda f, *a: [f(x, float(i), arr) for i, x in enumerate(arr)])
    if name == "filter":
        return fn(lambda f, *a: [x for i, x in enumerate(arr) if truthy(f(x, float(i), arr))])
    if name == "forEach":
        return fn(lambda f, *a: ([f(x, float(i), arr) for i, x in enumerate(arr)], None)[1])
    if name == "some":
        return fn(lambda f, *a: any(truthy(f(x, float(i), arr)) for i, x in enumerate(arr)))
    if name == "every":
        return fn(lambda f, *a: all(truthy(f(x, float(i), arr)) for i, x in enumerate(arr)))
    if name == "find":
        return fn(lambda f, *a: next((x for i, x in enumerate(arr) if truthy(f(x, float(i), arr))), None))
    if name == "findIndex":
        return fn(lambda f, *a: float(next((i for i, x in enumerate(arr) if truthy(f(x, float(i), arr))), -1)))
    if name == "lastIndexOf":
        return fn(lambda *a: float(max((i for i, x in enumerate(arr) if js_eq(x, a[0], True)), default=-1)))
    if name == "fill":
        def fill(v=None, start=0, end=None):
            n = len(arr)
            a0 = int(tonum(start)) if start is not None else 0
            a1 = int(tonum(end)) if end is not None else n
            for k in range(max(0, a0 if a0 >= 0 else n + a0), min(n, a1 if a1 >= 0 else n + a1)):
                arr[k] = v
            return arr
        return fill
    if name == "flat":
        return fn(lambda *a: [y for x in arr for y in (unwrap(x) if isinstance(unwrap(x), list) else [x])])
    if name == "flatMap":
        return fn(lambda f, *a: [y for i, x in enumerate(arr) for y in (lambda r: r if isinstance(r, list) else [r])(unwrap(f(x, float(i), arr)))])
    if name == "at":
        return fn(lambda i=0, *a: arr[int(tonum(i))] if -len(arr) <= int(tonum(i)) < len(arr) else None)
    if name == "reduceRight":
        def redr(f, *init):
            items = list(arr)[::-1]
            acc = init[0] if init else items.pop(0)
            for i, x in enumerate(items):
                acc = f(acc, x, float(len(arr) - 1 - i), arr)
            return acc
        return redr
    if name == "reduce":
        def red(f, *init):
            items = list(arr)
            acc = init[0] if init else items.pop(0)
            for i, x in enumerate(items):
                acc = f(acc, x, float(i), arr)
            return acc
        return red
    if name == "sort":
        def srt(*a):
            import functools
            if a and a[0] is not None:
                arr.sort(key=functools.cmp_to_key(lambda x, y: int(math.copysign(1, tonum(a[0](x, y))) if tonum(a[0](x, y)) else 0)))
            else:
                arr.sort(key=tostr)
            return arr
        return srt
    if name == "splice":
        def spl(*a):
            i = int(tonum(a[0]))
            n = int(tonum(a[1])) if len(a) > 1 else len(arr) - i
            removed = arr[i:i + n]
            arr[i:i + n] = list(a[2:])
            return removed
        return spl
    if name == "toString":
        return fn(lambda *a: tostr(arr))
    raise ExprError(f"array.{name}")


def _num_method(v, name):
    if name == "toFixed":
        return lambda *a: f"{v:.{int(tonum(a[0])) if a else 0}f}"
    if name == "toString":
        return lambda *a: _numstr(v) if not a else _radix(v, int(tonum(a[0])))
    if name == "toPrecision":
        return lambda *a: f"{v:.{int(tonum(a[0])) if a else 6}g}"
    if name == "valueOf":
        return lambda *a: v
    raise ExprError(f"number.{name}")


def _radix(v, r):
    n, digits, neg = int(v), "0123456789abcdefghijklmnopqrstuvwxyz", v < 0
    n = abs(n)
    s = ""
    while n:
        s = digits[n % r] + s
        n //= r
    return ("-" if neg else "") + (s or "0")


class _MathNS:
    """the JS Math object"""
    PI = math.pi
    E = math.e
    LN2 = math.log(2)
    LN10 = math.log(10)
    SQRT2 = math.sqrt(2)
    SQRT1_2 = math.sqrt(0.5)
    LOG2E = 1 / math.log(2)
    LOG10E = 1 / math.log(10)

    def __init__(self, env):
        self.env = env

    def js_get(self, name):
        if hasattr(_MathNS, name) and name.isupper():
            return getattr(_MathNS, name)
        f1 = {"sin": math.sin, "cos": math.cos, "tan": math.tan, "asin": _safe(math.asin), "acos": _safe(math.acos),
              "atan": math.atan, "sqrt": _safe(math.sqrt), "abs": abs, "floor": math.floor, "ceil": math.ceil,
              "exp": _safe(math.exp), "log": _safe(math.log), "log10": _safe(math.log10), "log2": _safe(math.log2),
              "sinh": math.sinh, "cosh": math.cosh, "tanh": math.tanh, "trunc": math.trunc,
              "sign": lambda x: math.copysign(1.0, x) if x else 0.0, "cbrt": lambda x: math.copysign(abs(x) ** (1 / 3), x),
              "round": lambda x: float(math.floor(x + 0.5))}
        if name in f1:
            f = f1[name]
            return lambda *a: float(f(tonum(a[0]))) if a else float("nan")
        if name == "atan2":
            return lambda *a: math.atan2(tonum(a[0]), tonum(a[1]))
        if name == "pow":
            return lambda *a: _pow(tonum(a[0]), tonum(a[1]))
        if name == "min":
            return lambda *a: min((tonum(x) for x in a), default=float("inf"))
        if name == "max":
            return lambda *a: max((tonum(x) for x in a), default=float("-inf"))
        if name == "hypot":
            return lambda *a: math.sqrt(sum(tonum(x) ** 2 for x in a))
        if name == "random":
            return lambda *a: self.env.random_unit()
        raise ExprError(f"Math.{name}")


def _safe(f):
    def g(x):
        try:
            return f(x)
        except (ValueError, OverflowError):
            return float("nan")
    return g


def _pow(a, b):
    try:
        return float(a ** b)
    except (OverflowError, ZeroDivisionError, ValueError):
        return float("nan")


ARRAY_CTOR = object()


# ================================================================== interpreter
class Interp:
    def __init__(self, env):
        self.env = env
        self.steps = 0

    def run(self, prog, scope):
        try:
            v = self.exec_block(prog[1], scope)
        except _Return as r:
            return r.value
        return None if v is EMPTY else v

    def exec_block(self, stmts, scope):
        for s in stmts:
            if s[0] == "fundecl":
                scope.declare(s[1], JSFunction(s[1], s[2], s[3], scope, self))
            elif s[0] == "var" and (len(s) < 3 or s[2] == "var"):
                # hoisting: `var delay = delay || false;` reads an undefined `delay`, not an unknown name
                for name, _init in s[1]:
                    if isinstance(name, str) and scope.find(name) is None and name not in _AE_GLOBALS:
                        scope.function_scope().declare(name, None)
        last = EMPTY
        for s in stmts:
            v = self.exec(s, scope)
            if v is not EMPTY:
                last = v
        return last

    def bind(self, pat, value, scope):
        """destructuring declaration"""
        kind, items = pat
        for key, target, default in items:
            if kind == "obj":
                v = self.member(value, key) if value is not None else None
            else:
                v = self.index(value, float(key)) if value is not None else None
            if v is None and default is not None:
                v = self.ev(default, scope)
            if isinstance(target, tuple):
                self.bind(target, v, scope)
            else:
                scope.declare(target, v)

    def tick(self):
        self.steps += 1
        if self.steps > 2_000_000:
            raise ExprError("expression too long to evaluate (loop?)")

    def exec(self, s, scope):
        k = s[0]
        self.tick()
        if k == "expr":
            return self.ev(s[1], scope)
        if k == "var":
            fn_var = len(s) < 3 or s[2] == "var"
            for name, init in s[1]:
                if isinstance(name, tuple):
                    self.bind(name, self.ev(init, scope) if init is not None else None, scope)
                elif fn_var:
                    # `var` belongs to the function (JS): a redeclaration inside a block updates the same variable
                    target = scope.find(name)
                    if target is None or not _below(target, scope.function_scope()):
                        target = scope.function_scope()
                    if init is not None:
                        target.vars[name] = self.ev(init, scope)
                    elif name not in target.vars:
                        target.vars[name] = None
                else:
                    scope.declare(name, self.ev(init, scope) if init is not None else (scope.vars.get(name)))
            return EMPTY
        if k == "fundecl" or k == "empty":
            return EMPTY
        if k == "if":
            if truthy(self.ev(s[1], scope)):
                return self.exec(s[2], scope)
            if s[3] is not None:
                return self.exec(s[3], scope)
            return EMPTY
        if k == "block":
            return self.exec_block(s[1], Scope(scope))
        if k == "ret":
            raise _Return(self.ev(s[1], scope) if s[1] is not None else None)
        if k == "break":
            raise _Break()
        if k == "cont":
            raise _Continue()
        if k == "throw":
            raise _Throw(self.ev(s[1], scope))
        if k in ("for", "while", "do", "forin"):
            return self.loop(s, scope)
        if k == "try":
            try:
                return self.exec(s[1], scope)
            except (_Throw, ExprError) as ex:
                if s[3] is None:
                    if s[4] is None:
                        raise
                    return EMPTY
                cs = Scope(scope)
                if s[2]:
                    cs.declare(s[2], ex.value if isinstance(ex, _Throw) else JSObject(message=str(ex)))
                return self.exec(s[3], cs)
            finally:
                if s[4] is not None:
                    self.exec(s[4], scope)
        if k == "switch":
            d = self.ev(s[1], scope)
            matched = False
            last = EMPTY
            try:
                for test, body in s[2]:
                    if not matched and (test is None or js_eq(d, self.ev(test, scope), True)):
                        matched = True
                    if matched:
                        for st in body:
                            v = self.exec(st, scope)
                            if v is not EMPTY:
                                last = v
                if not matched:
                    for test, body in s[2]:
                        if test is None:
                            for st in body:
                                v = self.exec(st, scope)
                                if v is not EMPTY:
                                    last = v
            except _Break:
                pass
            return last
        raise ExprError(f"statement {k}")

    def loop(self, s, scope):
        k = s[0]
        last = EMPTY
        ls = Scope(scope)
        if k == "forin":
            obj = unwrap(self.ev(s[3], ls))
            keys = (list(range(len(obj))) if s[1] == "in" else list(obj)) if isinstance(obj, list) else \
                   (list(obj.keys()) if s[1] == "in" else list(obj.values())) if isinstance(obj, dict) else []
            for key in keys:
                ls.declare(s[2], float(key) if isinstance(key, int) else key)
                try:
                    v = self.exec(s[4], ls)
                    if v is not EMPTY:
                        last = v
                except _Break:
                    break
                except _Continue:
                    continue
            return last
        if k == "for" and s[1] is not None:
            self.exec(s[1], ls)
        n = 0
        while True:
            n += 1
            if n > 1_000_000:
                raise ExprError("loop too long")
            if k == "do":
                try:
                    v = self.exec(s[1], ls)
                    if v is not EMPTY:
                        last = v
                except _Break:
                    break
                except _Continue:
                    pass
                if not truthy(self.ev(s[2], ls)):
                    break
                continue
            cond = s[2] if k == "for" else s[1]
            if cond is not None and not truthy(self.ev(cond, ls)):
                break
            body = s[4] if k == "for" else s[2]
            try:
                v = self.exec(body, ls)
                if v is not EMPTY:
                    last = v
            except _Break:
                break
            except _Continue:
                pass
            if k == "for" and s[3] is not None:
                self.ev(s[3], ls)
        return last

    # -------------------------------------------------------------- expressions
    def ev(self, n, scope):
        k = n[0]
        if k == "lit":
            return n[1]
        if k == "name":
            return self.lookup(n[1], scope)
        if k == "mem":
            return self.member(self.ev(n[1], scope), n[2])
        if k == "call":
            return self.call(n, scope)
        if k == "bin":
            return binop(n[1], self.ev(n[2], scope), self.ev(n[3], scope))
        if k == "log":
            a = self.ev(n[2], scope)
            if n[1] == "&&":
                return self.ev(n[3], scope) if truthy(a) else a
            if n[1] == "||":
                return a if truthy(a) else self.ev(n[3], scope)
            return a if unwrap(a) is not None else self.ev(n[3], scope)
        if k == "un":
            op = n[1]
            if op == "typeof":
                try:
                    v = unwrap(self.ev(n[2], scope))
                except ExprError:
                    return "undefined"
                return ("undefined" if v is None else "boolean" if isinstance(v, bool) else "number"
                        if isinstance(v, (int, float)) else "string" if isinstance(v, str) else
                        "function" if callable(v) else "object")
            v = self.ev(n[2], scope)
            if op == "-":
                return _vec_op(unwrap(v), 0.0, lambda x, y: -x) if isinstance(unwrap(v), list) else -tonum(v)
            if op == "+":
                return tonum(v)
            if op == "!":
                return not truthy(v)
            if op == "~":
                return float(~_i32(tonum(v)))
            if op == "void":
                return None
            if op == "delete":
                return True
        if k == "cond":
            return self.ev(n[2], scope) if truthy(self.ev(n[1], scope)) else self.ev(n[3], scope)
        if k == "arr":
            return self.ev_list(n[1], scope)
        if k == "obj":
            return JSObject((key, self.ev(v, scope)) for key, v in n[1])
        if k == "func":
            return JSFunction(n[1], n[2], n[3], scope, self)
        if k == "assign":
            return self.assign(n, scope)
        if k in ("pre", "post"):
            old = tonum(self.ev(n[2], scope))
            new = old + (1 if n[1] == "++" else -1)
            self.store(n[2], new, scope)
            return new if k == "pre" else old
        if k == "seq":
            self.ev(n[1], scope)
            return self.ev(n[2], scope)
        if k == "idx":
            return self.index(self.ev(n[1], scope), self.ev(n[2], scope))
        if k == "new":
            callee = self.ev(n[1], scope)
            args = self.ev_list(n[2], scope)
            if callee is ARRAY_CTOR:
                if len(args) == 1 and isinstance(unwrap(args[0]), (int, float)):
                    return [None] * int(tonum(args[0]))
                return list(args)
            if callee is OBJECT_CTOR:
                return JSObject()
            if isinstance(callee, JSFunction):
                # a constructor: `function Path(p, i, o) { this.p = p; … }` then `new Path(…)`
                obj = JSObject()
                r = callee.call_with(obj, args)
                return r if isinstance(r, (JSObject, list)) else obj
            if callable(callee):
                return callee(*args)
            raise ExprError("new on a non-constructor")
        raise ExprError(f"node {k}")

    def lookup(self, name, scope):
        s = scope.find(name)
        if s is not None:
            return s.vars[name]
        return self.env.resolve(name)

    def member(self, obj, name):
        if obj is ARRAY_CTOR:
            if name == "isArray":
                return lambda v=None, *a: isinstance(unwrap(v), list)
            if name == "from":
                def arr_from(src=None, fn=None, *a):
                    src = unwrap(src)
                    if isinstance(src, (JSObject, dict)):
                        n = int(tonum(src.get("length", 0)))
                        items = [None] * max(0, n)
                    elif isinstance(src, str):
                        items = list(src)
                    else:
                        items = list(src or [])
                    if fn is None:
                        return items
                    call = fn.call_with if isinstance(fn, JSFunction) else None
                    return [call(None, [v, float(k)]) if call else fn(v, float(k)) for k, v in enumerate(items)]
                return arr_from
            if name == "of":
                return lambda *a: list(a)
            raise ExprError(f"Array.{name}")
        g = getattr(obj, "js_get", None)
        if g is not None:
            return g(name)
        o = obj
        if isinstance(o, str):
            return _string_method(o, name)
        if isinstance(o, list):
            return _array_method(o, name, self)
        if isinstance(o, bool):
            if name == "toString":
                return lambda *a: "true" if o else "false"
            raise ExprError(f"boolean.{name}")
        if isinstance(o, (int, float)):
            return _num_method(float(o), name)
        if isinstance(o, dict):
            if name in o:
                return o[name]
            if name == "hasOwnProperty":
                return lambda *a: tostr(a[0]) in o
            return None
        if o is None:
            raise ExprError(f"undefined has no property {name}")
        raise ExprError(f"no property {name} on {type(o).__name__}")

    def index(self, obj, key):
        g = getattr(obj, "js_index", None)
        if g is not None:
            return g(key)
        o = unwrap(obj) if not isinstance(obj, (list, dict, str)) else obj
        if isinstance(o, list):
            if isinstance(key, str) and not re.fullmatch(r"-?\d+", key):
                return self.member(o, key)
            i = int(tonum(key))
            return o[i] if 0 <= i < len(o) else None
        if isinstance(o, str):
            if isinstance(key, str) and not re.fullmatch(r"\d+", key):
                return self.member(o, key)
            i = int(tonum(key))
            return o[i] if 0 <= i < len(o) else None
        if isinstance(o, dict):
            return o.get(tostr(key))
        if getattr(o, "js_get", None):
            return o.js_get(tostr(key))
        raise ExprError("index on a non-object")

    def ev_list(self, nodes, scope):
        """argument / array items, `...x` spread"""
        out = []
        for x in nodes:
            if x[0] == "spread":
                v = unwrap(self.ev(x[1], scope))
                out.extend(list(v) if isinstance(v, (list, str)) else [v])
            else:
                out.append(self.ev(x, scope))
        return out

    def call(self, n, scope):
        callee = n[1]
        if callee == ("name", "eval") and scope.find("eval") is None:
            # rig presets hide their code as eval(String.fromCharCode(…)); it runs in the caller's scope and gives
            # its last expression statement (broadcast-test's character: the head's opacity per keyframe)
            args = self.ev_list(n[2], scope)
            v = self.exec_block(parse(tostr(args[0]) if args else "")[1], scope)
            return None if v is EMPTY else v
        if callee[0] == "mem" and callee[1] == ("name", "String") and callee[2] == "fromCharCode":
            return "".join(chr(int(tonum(x))) for x in self.ev_list(n[2], scope))
        if callee[0] == "mem":
            obj = self.ev(callee[1], scope)
            fn = self.member(obj, callee[2])
        elif callee[0] == "idx":
            obj = self.ev(callee[1], scope)
            fn = self.index(obj, self.ev(callee[2], scope))
        else:
            fn = self.ev(callee, scope)
        args = self.ev_list(n[2], scope)
        if isinstance(fn, JSFunction) and callee[0] in ("mem", "idx"):
            return fn.call_with(obj, args)                  # obj.method(): `this` is obj
        if not callable(fn):
            c = getattr(fn, "js_call", None)
            if c is not None:
                return c(*args)
            raise ExprError(f"not a function: {_describe(callee)}")
        return fn(*args)

    def assign(self, n, scope):
        op, target, valn = n[1], n[2], n[3]
        v = self.ev(valn, scope)
        if op != "=":
            cur = self.ev(target, scope)
            v = binop(op[0], cur, v)
        self.store(target, v, scope)
        return v

    def store(self, target, v, scope):
        if target[0] == "name":
            s = scope.find(target[1])
            (s or scope.root()).vars[target[1]] = v
            return
        obj = self.ev(target[1], scope)
        if target[0] == "mem":
            key = target[2]
        else:
            key = self.ev(target[2], scope)
        if isinstance(obj, list):
            i = int(tonum(key))
            while len(obj) <= i:
                obj.append(None)
            obj[i] = v
        elif isinstance(obj, dict):
            obj[tostr(key)] = v
        else:
            raise ExprError("cannot assign a property of this object")


def _describe(n):
    if n[0] == "name":
        return n[1]
    if n[0] == "mem":
        return _describe(n[1]) + "." + n[2]
    return n[0]


OBJECT_CTOR = object()

_AST = {}


def parse(src):
    ast = _AST.get(src)
    if ast is None:
        ast = Parser(src).program()
        _AST[src] = ast
    return ast


# ================================================================== AE environment
TRANSFORM_NAMES = {
    "anchorPoint": "ADBE Anchor Point", "position": "ADBE Position", "xPosition": "ADBE Position_0",
    "yPosition": "ADBE Position_1", "zPosition": "ADBE Position_2", "scale": "ADBE Scale",
    "orientation": "ADBE Orientation", "xRotation": "ADBE Rotate X", "yRotation": "ADBE Rotate Y",
    "rotation": "ADBE Rotate Z", "zRotation": "ADBE Rotate Z", "opacity": "ADBE Opacity",
}
SHAPE_NAMES = {
    "transform": ("ADBE Vector Transform Group", "ADBE Vector Repeater Transform"),
    "content": ("ADBE Vectors Group",), "contents": ("ADBE Vectors Group",),
    "anchorPoint": ("ADBE Vector Anchor", "ADBE Vector Repeater Anchor"),
    "position": ("ADBE Vector Position", "ADBE Vector Rect Position", "ADBE Vector Ellipse Position",
                 "ADBE Vector Star Position", "ADBE Vector Repeater Position"),
    "scale": ("ADBE Vector Scale", "ADBE Vector Repeater Scale"),
    "rotation": ("ADBE Vector Rotation", "ADBE Vector Star Rotation", "ADBE Vector Repeater Rotation"),
    "opacity": ("ADBE Vector Group Opacity", "ADBE Vector Fill Opacity", "ADBE Vector Stroke Opacity"),
    "skew": ("ADBE Vector Skew",), "skewAxis": ("ADBE Vector Skew Axis",),
    "path": ("ADBE Vector Shape",), "size": ("ADBE Vector Rect Size", "ADBE Vector Ellipse Size"),
    "roundness": ("ADBE Vector Rect Roundness", "ADBE Vector RoundCorner Radius"),
    "color": ("ADBE Vector Fill Color", "ADBE Vector Stroke Color"),
    "strokeWidth": ("ADBE Vector Stroke Width",), "lineCap": ("ADBE Vector Stroke Line Cap",),
    "lineJoin": ("ADBE Vector Stroke Line Join",), "miterLimit": ("ADBE Vector Stroke Miter Limit",),
    # stroke.dash = the Dashes group; dash.dash / dash.gap / dash.offset its first dash, gap and the offset
    "dash": ("ADBE Vector Stroke Dashes", "ADBE Vector Stroke Dash 1"), "gap": ("ADBE Vector Stroke Gap 1",),
    "start": ("ADBE Vector Trim Start",), "end": ("ADBE Vector Trim End",),
    "offset": ("ADBE Vector Trim Offset", "ADBE Vector Repeater Offset", "ADBE Vector Stroke Offset"),
    "copies": ("ADBE Vector Repeater Copies",),
    "startOpacity": ("ADBE Vector Repeater Opacity 1",), "endOpacity": ("ADBE Vector Repeater Opacity 2",),
    "points": ("ADBE Vector Star Points",), "innerRadius": ("ADBE Vector Star Inner Radius",),
    "outerRadius": ("ADBE Vector Star Outer Radius",), "innerRoundness": ("ADBE Vector Star Inner Roundess",),
    "outerRoundness": ("ADBE Vector Star Outer Roundess",),
    "startPoint": ("ADBE Vector Grad Start Pt",), "endPoint": ("ADBE Vector Grad End Pt",),
    "maskPath": ("ADBE Mask Shape",), "maskOpacity": ("ADBE Mask Opacity",), "maskFeather": ("ADBE Mask Feather",),
    "maskExpansion": ("ADBE Mask Offset",),
    "sourceText": ("ADBE Text Document",), "timeRemap": ("ADBE Time Remapping",),
    "marker": ("ADBE Marker",), "audioLevels": ("ADBE Audio Levels",),
}
CONTROL_PARAMS = {"slider": "ADBE Slider Control-0001", "curseur": "ADBE Slider Control-0001",
                  "color": "ADBE Color Control-0001", "couleur": "ADBE Color Control-0001",
                  "checkbox": "ADBE Checkbox Control-0001", "case": "ADBE Checkbox Control-0001",
                  "angle": "ADBE Angle Control-0001", "point": "ADBE Point Control-0001",
                  "3d point": "ADBE Point3D Control-0001", "layer": "ADBE Layer Control-0001",
                  "calque": "ADBE Layer Control-0001", "menu": "ADBE Dropdown Control-0001"}


# After Effects resolves a name given to an expression in its OWN UI language only (measured in AE 26.5 French:
# effect("C")("Color") -> "expression disabled", ("Couleur") and (1) work). Control parameters, per language:
PARAM_NAMES = {
    "en": {"ADBE Slider Control-0001": "Slider", "ADBE Color Control-0001": "Color",
           "ADBE Checkbox Control-0001": "Checkbox", "ADBE Angle Control-0001": "Angle",
           "ADBE Point Control-0001": "Point", "ADBE Point3D Control-0001": "3D Point",
           "ADBE Layer Control-0001": "Layer", "ADBE Dropdown Control-0001": "Menu"},
    "fr": {"ADBE Slider Control-0001": "Curseur", "ADBE Color Control-0001": "Couleur",
           "ADBE Checkbox Control-0001": "Case", "ADBE Angle Control-0001": "Angle",
           "ADBE Point Control-0001": "Ponctuelle", "ADBE Point3D Control-0001": "Point 3D",
           "ADBE Layer Control-0001": "Calque", "ADBE Dropdown Control-0001": "Menu"},
}
GROUP_NAMES = {
    "en": {"ADBE Transform Group": "Transform", "ADBE Opacity": "Opacity", "ADBE Position": "Position",
           "ADBE Scale": "Scale", "ADBE Rotate Z": "Rotation", "ADBE Anchor Point": "Anchor Point"},
    "fr": {"ADBE Transform Group": "Transformer", "ADBE Opacity": "Opacité", "ADBE Position": "Position",
           "ADBE Scale": "Echelle", "ADBE Rotate Z": "Rotation", "ADBE Anchor Point": "Point d'ancrage"},
}


def ae_lang():
    """the After Effects UI language to emulate: AE2RML_LANG (fr / en / any), else the system's (macOS AppleLocale)"""
    import os
    import subprocess
    v = (os.environ.get("AE2RML_LANG") or "").strip().lower()
    if v in ("fr", "en", "any"):
        return v
    try:
        loc = subprocess.run(["defaults", "read", "-g", "AppleLocale"], capture_output=True, text=True).stdout.strip()
        return "fr" if loc.lower().startswith("fr") else "en"
    except Exception:
        return "any"


def _mn(p):
    try:
        return p.match_name
    except Exception:
        return ""


def _children(g):
    try:
        return list(g.properties)
    except Exception:
        return []


def _is_group(p):
    return type(p).__name__ != "Property"


class ShapeValue:
    """a path value inside expressions (points() / inTangents() / outTangents() / isClosed())"""

    def __init__(self, verts, ins, outs, closed):
        self.v, self.i, self.o, self.closed = verts, ins, outs, closed

    def js_get(self, name):
        if name == "points":
            return lambda *a: [list(p) for p in self.v]
        if name == "inTangents":
            return lambda *a: [list(p) for p in self.i]
        if name == "outTangents":
            return lambda *a: [list(p) for p in self.o]
        if name == "isClosed":
            return lambda *a: self.closed
        if name in ("pointOnPath", "tangentOnPath", "normalOnPath"):
            return lambda pct=0.5, *a: self.on_path(name, tonum(pct))
        raise ExprError(f"path.{name}")

    def on_path(self, what, pct):
        """AE pointOnPath / tangentOnPath / normalOnPath: at `pct` (0..1) of the path's length (arc length on a
        flattened polyline, 24 steps per segment)"""
        n = len(self.v)
        if n == 0:
            return [0.0, 0.0]
        segs = list(range(n if self.closed else n - 1))
        pts, tans = [], []
        for k in segs:
            a, b = self.v[k], self.v[(k + 1) % n]
            c1 = [a[0] + self.o[k][0], a[1] + self.o[k][1]]
            c2 = [b[0] + self.i[(k + 1) % n][0], b[1] + self.i[(k + 1) % n][1]]
            for j in range(24 if k == segs[-1] else 24):
                u = j / 24.0
                m = 1 - u
                x = m ** 3 * a[0] + 3 * m * m * u * c1[0] + 3 * m * u * u * c2[0] + u ** 3 * b[0]
                y = m ** 3 * a[1] + 3 * m * m * u * c1[1] + 3 * m * u * u * c2[1] + u ** 3 * b[1]
                dx = 3 * m * m * (c1[0] - a[0]) + 6 * m * u * (c2[0] - c1[0]) + 3 * u * u * (b[0] - c2[0])
                dy = 3 * m * m * (c1[1] - a[1]) + 6 * m * u * (c2[1] - c1[1]) + 3 * u * u * (b[1] - c2[1])
                pts.append((x, y))
                tans.append((dx, dy))
        if segs:
            last = self.v[(segs[-1] + 1) % n]
            pts.append((last[0], last[1]))
            tans.append(tans[-1] if tans else (1.0, 0.0))
        else:
            return [float(self.v[0][0]), float(self.v[0][1])] if what == "pointOnPath" else [1.0, 0.0]
        acc = [0.0]
        for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
            acc.append(acc[-1] + math.hypot(x1 - x0, y1 - y0))
        target = max(0.0, min(1.0, pct)) * acc[-1]
        k = 0
        while k < len(acc) - 2 and acc[k + 1] < target:
            k += 1
        seg = acc[k + 1] - acc[k]
        u = (target - acc[k]) / seg if seg > 0 else 0.0
        if what == "pointOnPath":
            return [pts[k][0] + (pts[k + 1][0] - pts[k][0]) * u, pts[k][1] + (pts[k + 1][1] - pts[k][1]) * u]
        tx = tans[k][0] + (tans[k + 1][0] - tans[k][0]) * u
        ty = tans[k][1] + (tans[k + 1][1] - tans[k][1]) * u
        ln = math.hypot(tx, ty) or 1.0
        if what == "tangentOnPath":
            return [tx / ln, ty / ln]
        return [ty / ln, -tx / ln]


def shape_to_value(s):
    return ShapeValue([list(map(float, p[:2])) for p in s.vertices], [list(map(float, p[:2])) for p in s.in_tangents],
                      [list(map(float, p[:2])) for p in s.out_tangents], bool(s.closed))


def null_opacity_fixed(p, L):
    """py-aep 0.17 reports 0 % for a null layer's static opacity although the file stores 100 (checked against AE's
    own ExtendScript dump and the raw cdat chunk): a null's untouched opacity is 100."""
    try:
        if L is None or not getattr(L, "null_layer", False) or p.match_name != "ADBE Opacity":
            return False
        if len(p.keyframes) or p.is_modified:
            return False
        cd = getattr(p, "_cdat", None)
        vals = getattr(cd, "values", None) or []
        return not vals or abs(float(vals[0]) - 100.0) < 1e-6 or abs(float(vals[0]) - 1.0) < 1e-6
    except Exception:
        return False


# value of an expression control's parameter the instance does not store: AE's own default. py-aep synthesizes it
# from the project's effect definition instead, which can hold another value (measured: Motion Bro's Glass Animation 01,
# three sliders read 75 where AE renders 0 — the box is 1935 × 0.75 px wide in aerender's frame, not 2010 × 0.75)
CONTROL_DEFAULTS = {
    "ADBE Slider Control-0001": 0.0, "ADBE Angle Control-0001": 0.0, "ADBE Checkbox Control-0001": 0.0,
    "ADBE Layer Control-0001": 0.0, "ADBE Dropdown Control-0001": 1.0,
}


def _unstored(p):
    return getattr(p, "_composition", None) is None and getattr(p, "_tdum", None) is None and hasattr(p, "_tdum")


# Essential Properties of the precomp instance being built: id(source property) -> (fn(t) -> py-aep value, keyed,
# id of the comp owning the source, signature). AE renders each instance of a precomp with its own overridden values
# (rig-test: the same character rig white in one shot, orange in another) — the converter builds one artboard per set.
OVERRIDES = {}


def nkeys(p):
    """keyframe count as the evaluation sees it: an overridden property has the override's keys"""
    o = OVERRIDES.get(id(p))
    if o is not None:
        return 2 if o[1] else 0
    try:
        return len(list(p.keyframes or []))
    except Exception:
        return 0


def raw_value(p, L, t=None):
    """pre-expression value of a py-aep property (at t when animated), with the null-opacity fix"""
    o = OVERRIDES.get(id(p))
    if o is not None:
        return o[0](t)
    if null_opacity_fixed(p, L):
        return 100.0
    mn = getattr(p, "match_name", "")
    if (mn in _PARAM_DEFAULTS or mn in CONTROL_DEFAULTS) and _unstored(p):
        return _PARAM_DEFAULTS.get(mn, CONTROL_DEFAULTS.get(mn))
    try:
        if t is not None and len(p.keyframes):
            return p.value_at_time(t)
    except Exception:
        pass
    return p.value


def to_js(v):
    """py-aep value -> JS value"""
    if v is None:
        return None
    if isinstance(v, bool):
        return 1.0 if v else 0.0
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, (list, tuple)):
        return [to_js(x) for x in v]
    if hasattr(v, "vertices") and hasattr(v, "in_tangents"):
        return shape_to_value(v)
    if hasattr(v, "text") and hasattr(v, "font_size"):
        return TextValue(clean(v.text), v)
    if hasattr(v, "value") and not callable(v.value):
        return to_js(v.value)
    return v


class Env:
    """one evaluation: the AE globals for (property, layer, comp, time)"""

    def __init__(self, engine, prop, layer, comp, t):
        self.engine = engine
        self.prop = prop
        self.layer = layer
        self.comp = comp
        self.time = t
        self.dep = False                     # did the result depend on time?
        self.rng = None
        self.timeless = False
        self.inexact = False                 # random / wiggle / noise: AE's generator differs from this one

    def mark(self):
        self.dep = True

    # ---- randomness (deterministic; AE's own generator is not reproducible outside AE)
    def _seed(self, seed=0):
        key = f"{seed}|{getattr(self.layer, 'index', 0)}|{_mn(self.prop)}|{'' if self.timeless else round(self.time, 5)}"
        self.rng = _random.Random(key)

    def random_unit(self):
        self.inexact = True
        if self.rng is None:
            self._seed(0)
        if not self.timeless:
            self.mark()
        return self.rng.random()

    def resolve(self, name):
        e = self.engine
        extra = getattr(self, "extra", None)
        if extra and name in extra:
            return extra[name]
        if name == "time":
            self.mark()
            return self.time
        if name == "value":
            return e.pre_value(self, self.prop, self.time)
        if name == "thisComp":
            return CompRef(e, self.comp, self)
        if name == "thisLayer":
            return LayerRef(e, self.layer, self.comp, self)
        if name == "thisProperty":
            return PropRef(e, self.prop, self.layer, self.comp, self, own=True)
        if name == "Math":
            return _MathNS(self)
        if name == "index":
            return float(self.layer.index + 1)
        if name in ("NaN",):
            return float("nan")
        if name == "Infinity":
            return float("inf")
        if name == "Array":
            return ARRAY_CTOR
        if name == "Object":
            return OBJECT_CTOR
        if name == "colorDepth":
            return 8.0
        f = GLOBAL_FUNCS.get(name)
        if f is not None:
            return lambda *a: f(self, *a)
        if name in PROP_SHORTHANDS:
            return PropRef(e, self.prop, self.layer, self.comp, self, own=True).js_get(name)
        if name in LAYER_SHORTHANDS:
            return LayerRef(e, self.layer, self.comp, self).js_get(name)
        if name == "comp":
            return lambda n: CompRef(e, e.comp_by_name(tostr(n)), self)
        if name == "footage":
            raise ExprError("footage() is not supported offline")
        raise ExprError(f"unknown name '{name}'")


_AE_GLOBALS = {"value", "time", "index", "thisComp", "thisLayer", "thisProperty", "Math", "colorDepth", "inPoint",
               "outPoint", "startTime", "position", "scale", "rotation", "anchorPoint", "opacity", "transform",
               "effect", "content", "text", "mask", "name", "width", "height", "parent", "hasParent", "numKeys"}
PROP_SHORTHANDS = {"valueAtTime", "velocity", "speed", "velocityAtTime", "speedAtTime", "numKeys", "key", "nearestKey",
                   "wiggle", "temporalWiggle", "loopOut", "loopIn", "loopOutDuration", "loopInDuration", "smooth",
                   "propertyGroup", "propertyIndex", "points", "inTangents", "outTangents", "isClosed"}
LAYER_SHORTHANDS = {"transform", "position", "anchorPoint", "scale", "rotation", "opacity", "effect", "mask", "content",
                    "text", "name", "inPoint", "outPoint", "startTime", "width", "height", "hasParent", "parent",
                    "toComp", "fromComp", "toWorld", "fromWorld", "sourceRectAtTime", "marker", "source", "timeRemap",
                    "fromCompToSurface", "toCompVec", "fromCompVec", "toWorldVec", "fromWorldVec",
                    "enabled", "active", "xPosition", "yPosition", "zRotation", "orientation", "xRotation",
                    "yRotation", "sourceTime", "containingComp", "audioLevels", "zPosition", "hasVideo", "hasAudio"}


class CompRef:
    def __init__(self, engine, comp, env):
        if comp is None:
            raise ExprError("comp not found")
        self.e, self.c, self.env = engine, comp, env

    def js_get(self, name):
        c = self.c
        if name == "width":
            return float(c.width)
        if name == "height":
            return float(c.height)
        if name == "duration":
            return float(c.duration)
        if name == "frameDuration":
            return 1.0 / float(c.frame_rate or 25)
        if name == "numLayers":
            return float(c.num_layers)
        if name == "name":
            return clean(c.name)
        if name == "pixelAspect":
            return float(getattr(c, "pixel_aspect", 1.0) or 1.0)
        if name == "bgColor":
            return [float(x) for x in (list(c.bg_color) + [1.0])[:4]]
        if name == "displayStartTime":
            return float(getattr(c, "display_start_time", 0.0) or 0.0)
        if name == "layer":
            return self.layer
        if name == "marker":
            return MarkerRef(getattr(c, "marker_property", None), self.env)
        if name == "activeCamera":
            raise ExprError("activeCamera")
        raise ExprError(f"comp.{name}")

    def layer(self, which, rel=None):
        c = self.c
        w = unwrap(which)
        if isinstance(w, LayerRef):
            idx = w.L.index + int(tonum(rel or 0))
            return LayerRef(self.e, c.layers[idx], c, self.env)
        if isinstance(w, str):
            for L in c.layers:
                if clean(L.name) == w:
                    return LayerRef(self.e, L, c, self.env)
            raise ExprError(f'layer("{w}") not found in {clean(c.name)}')
        i = int(tonum(w)) - 1
        if 0 <= i < len(c.layers):
            return LayerRef(self.e, c.layers[i], c, self.env)
        raise ExprError(f"layer({i + 1}) out of range")


class MarkerRef:
    def __init__(self, prop, env):
        self.p, self.env = prop, env

    def _keys(self):
        try:
            return list(self.p.keyframes) if self.p is not None else []
        except Exception:
            return []

    def js_get(self, name):
        ks = self._keys()
        if name == "numKeys":
            return float(len(ks))
        if name == "key":
            def key(n):
                n = unwrap(n)
                if isinstance(n, str):
                    for k in ks:
                        if clean(getattr(k.value, "comment", "")) == n:
                            return KeyRef(k.time, getattr(k.value, "comment", ""), 0)
                    raise ExprError(f"marker {n}")
                k = ks[int(tonum(n)) - 1]
                return KeyRef(k.time, clean(getattr(k.value, "comment", "")), int(tonum(n)))
            return key
        if name == "nearestKey":
            def near(t):
                if not ks:
                    raise ExprError("no marker")
                t = tonum(t)
                j = min(range(len(ks)), key=lambda i: abs(ks[i].time - t))
                return KeyRef(ks[j].time, clean(getattr(ks[j].value, "comment", "")), j + 1)
            return near
        raise ExprError(f"marker.{name}")


class KeyRef:
    def __init__(self, t, v, i):
        self.t, self.v, self.i = t, v, i

    def js_get(self, name):
        if name == "time":
            return float(self.t)
        if name == "value":
            return self.v
        if name == "index":
            return float(self.i)
        if name == "comment":
            return tostr(self.v)
        if name == "duration":
            return 0.0
        raise ExprError(f"key.{name}")

    def js_index(self, k):
        v = unwrap(self.v)
        if isinstance(v, list):
            return v[int(tonum(k))]
        raise ExprError("key[] on a scalar")

    def js_value(self):
        return self.v


class LayerRef:
    def __init__(self, engine, L, comp, env):
        self.e, self.L, self.comp, self.env = engine, L, comp, env

    def _tprop(self, mn):
        tr = self.L.transform
        p = tr.property(mn) if tr is not None else None
        if p is None:
            raise ExprError(f"transform {mn}")
        return PropRef(self.e, p, self.L, self.comp, self.env)

    def js_get(self, name):
        L, e, env = self.L, self.e, self.env
        if name in TRANSFORM_NAMES:
            return self._tprop(TRANSFORM_NAMES[name])
        if name == "transform":
            return GroupRef(e, L.transform, L, self.comp, env, TRANSFORM_NAMES)
        if name == "name":
            return clean(L.name)
        if name == "index":
            return float(L.index + 1)
        if name == "inPoint":
            return float(L.in_point)
        if name == "outPoint":
            return float(L.out_point)
        if name == "startTime":
            return float(L.start_time)
        if name in ("enabled", "active"):
            return bool(L.enabled)
        if name in ("width", "height"):
            src = getattr(L, "source", None)
            if src is not None and getattr(src, name, None):
                return float(getattr(src, name))
            return float(getattr(self.comp, name))
        if name == "hasParent":
            return L.parent is not None
        if name == "parent":
            if L.parent is None:
                raise ExprError("layer has no parent")
            return LayerRef(e, L.parent, self.comp, env)
        if name == "effect":
            return lambda which: EffectRef(e, self._effect(which), L, self.comp, env)
        if name == "mask":
            def mask(which):
                ms = _children(L.masks) if getattr(L, "masks", None) is not None else []
                m = _pick(ms, which, getattr(e, 'lang', 'any'))
                return GroupRef(e, m, L, self.comp, env)
            return mask
        if name == "content":
            def content(which):
                root = L.property("ADBE Root Vectors Group")
                if root is None:
                    raise ExprError("content() on a layer without contents")
                return GroupRef(e, _pick(_children(root), which, getattr(e, 'lang', 'any')), L, self.comp, env)
            return content
        if name == "text":
            return GroupRef(e, L.property("ADBE Text Properties") or L.text, L, self.comp, env)
        if name == "timeRemap":
            p = L.property("ADBE Time Remapping")
            if p is None:
                raise ExprError("timeRemap")
            return PropRef(e, p, L, self.comp, env)
        if name == "marker":
            return MarkerRef(L.property("ADBE Marker"), env)
        if name == "source":
            src = getattr(L, "source", None)
            if src is None:
                raise ExprError("layer has no source")
            if hasattr(src, "layers"):
                return CompRef(e, src, env)
            return FootageRef(src)
        if name == "containingComp":
            return CompRef(e, self.comp, env)
        if name == "sourceRectAtTime":
            return lambda t=None, ext=False: e.source_rect(L, self.comp, env, env.time if t is None else tonum(t), truthy(ext))
        if name in ("toComp", "toWorld"):
            w = name == "toWorld"
            return lambda p, t=None: e.to_comp(L, self.comp, env, unwrap(p), env.time if t is None else tonum(t), w)
        if name in ("fromComp", "fromWorld"):
            w = name == "fromWorld"
            return lambda p, t=None: e.from_comp(L, self.comp, env, unwrap(p), env.time if t is None else tonum(t), w)
        if name == "fromCompToSurface":
            # the layer-surface point under a comp point (Create Nulls From Paths' path expressions); exact on a 2D
            # layer, where it is fromComp without z
            def to_surface(p, t=None):
                v = e.from_comp(L, self.comp, env, unwrap(p), env.time if t is None else tonum(t), False)
                return list(v[:2]) if isinstance(v, list) else v
            return to_surface
        if name in ("toCompVec", "fromCompVec", "toWorldVec", "fromWorldVec"):
            fn = e.to_comp if name.startswith("to") else e.from_comp
            w = "World" in name

            def vec(p, t=None):
                tt = env.time if t is None else tonum(t)
                p = unwrap(p)
                a = fn(L, self.comp, env, p, tt, w)
                o = fn(L, self.comp, env, [0.0] * len(p), tt, w)
                return [tonum(x) - tonum(y) for x, y in zip(a, o)]
            return vec
        if name == "sourceTime":
            return lambda t=None: (env.time if t is None else tonum(t)) - float(L.start_time)
        if name in ("hasVideo", "hasAudio"):
            return bool(getattr(L, "has_video" if name == "hasVideo" else "has_audio", True))
        if name == "audioLevels":
            raise ExprError("audioLevels")
        raise ExprError(f"layer.{name}")

    def js_call(self, which):
        # thisLayer("ADBE Effect Parade"), layer("ADBE Root Vectors Group")(1)…: AE looks among the layer's own
        # groups first (Transform, Effects, Contents, Masks), then in its contents
        w = unwrap(which)
        if isinstance(w, str):
            try:
                p = _pick(_children(self.L), w, getattr(self.e, "lang", "any"))
                if _is_group(p):
                    return GroupRef(self.e, p, self.L, self.comp, self.env)
                return PropRef(self.e, p, self.L, self.comp, self.env)
            except ExprError:
                pass
        return self.js_get("content")(which)

    def _effect(self, which):
        fx = [p for p in _children(self.L.effects)]
        return _pick(fx, which)


class FootageRef:
    def __init__(self, src):
        self.src = src

    def js_get(self, name):
        if name in ("width", "height", "duration"):
            return float(getattr(self.src, name, 0) or 0)
        if name == "name":
            return clean(getattr(self.src, "name", ""))
        raise ExprError(f"footage.{name}")


def _pick(items, which, lang="any"):
    w = unwrap(which)
    if isinstance(w, str):
        names = GROUP_NAMES.get(lang)
        for p in items:
            mn = _mn(p)
            if names is not None and mn in names:
                if w == mn or w == names[mn]:           # built-in property: AE's own language only
                    return p
                continue
            if clean(p.name) == w or mn == w:
                return p
        if names is not None:
            raise ExprError(f'"{w}" not found (a {lang.upper()} After Effects disables this expression)')
        low = w.lower()
        for p in items:
            if clean(p.name).lower() == low:
                return p
        raise ExprError(f'"{w}" not found')
    i = int(tonum(w)) - 1
    if 0 <= i < len(items):
        return items[i]
    raise ExprError(f"index {i + 1} out of range")


class GroupRef:
    """a property group (transform, a shape group, a mask, text…): attributes by AE expression name, call by name/index"""

    def __init__(self, engine, g, L, comp, env, names=None):
        if g is None:
            raise ExprError("group not found")
        self.e, self.g, self.L, self.comp, self.env, self.names = engine, g, L, comp, env, names

    def _wrap(self, p):
        if _is_group(p):
            return GroupRef(self.e, p, self.L, self.comp, self.env)
        return PropRef(self.e, p, self.L, self.comp, self.env)

    def js_get(self, name):
        kids = _children(self.g)
        if self.names and name in self.names:
            for p in kids:
                if _mn(p) == self.names[name]:
                    return self._wrap(p)
        cand = SHAPE_NAMES.get(name)
        if cand:
            for p in kids:
                if _mn(p) in cand:
                    return self._wrap(p)
            if name in ("content", "contents"):
                return lambda which: GroupRef(self.e, _pick(kids, which, getattr(self.e, 'lang', 'any')), self.L, self.comp, self.env)
        if name == "name":
            return clean(self.g.name)
        if name == "numProperties":
            return float(len(kids))
        if name == "propertyGroup":
            return lambda n=1: _parent_group(self, int(tonum(n)))
        # a child named like the attribute (camelCase of its match-name tail)
        for p in kids:
            tail = _mn(p).split("ADBE Vector ")[-1].split("ADBE ")[-1]
            if _camel(tail) == name or _camel(clean(p.name)) == name:
                return self._wrap(p)
        raise ExprError(f"{clean(self.g.name)}.{name}")

    def js_call(self, which):
        kids = _children(self.g)
        lang = getattr(self.e, 'lang', 'any')
        # a vector group's children live in its "Contents" sub-group; a name may also be one of the group's own
        # (group("ADBE Vectors Group")(1)("ADBE Vector Shape"), the Joysticks'n Sliders / rig idiom)
        inner = next((p for p in kids if _mn(p) == "ADBE Vectors Group"), None)
        if isinstance(unwrap(which), str) and inner is not None:
            try:
                return self._wrap(_pick(kids, which, lang))
            except ExprError:
                pass
        if inner is not None:
            kids = _children(inner)
        return self._wrap(_pick(kids, which, lang))


def _parent_group(ref, n):
    g = getattr(ref, "g", None) or getattr(ref, "p", None)       # a group, or a property (thisProperty)
    for _ in range(n):
        g = getattr(g, "parent_property", None)
        if g is None:
            raise ExprError("propertyGroup beyond the layer")
    return GroupRef(ref.e, g, ref.L, ref.comp, ref.env)


def _camel(s):
    words = re.split(r"[^A-Za-z0-9]+", s)
    words = [w for w in words if w]
    if not words:
        return ""
    return words[0].lower() + "".join(w[:1].upper() + w[1:].lower() for w in words[1:])


class EffectRef:
    def __init__(self, engine, fx, L, comp, env):
        self.e, self.fx, self.L, self.comp, self.env = engine, fx, L, comp, env

    def params(self):
        return [p for p in _children(self.fx) if _mn(p) != "ADBE Effect Built In Params"]

    def param(self, which):
        ps = self.params()
        w = unwrap(which)
        p = None
        pseudo = getattr(self.e, "pseudo", {})

        def name_of(q):
            d = pseudo.get(_mn(q))
            return d[0] if d else clean(q.name)
        if isinstance(w, str):
            lang = getattr(self.e, "lang", "any")
            if lang in PARAM_NAMES:
                # strict, like AE: the match name, or the parameter's name in AE's own language (exact case);
                # other effects' parameters: their name as stored in the project
                table = PARAM_NAMES[lang]
                for q in ps:
                    mn = _mn(q)
                    if mn == w or (table.get(mn) == w) or (mn not in table and name_of(q) == w):
                        p = q
                        break
                if p is None:
                    raise ExprError(f'effect("{clean(self.fx.name)}")("{w}"): no such parameter in a {lang.upper()} '
                                    f'After Effects (expression disabled there: pre-expression value)')
            else:
                key = CONTROL_PARAMS.get(w.lower())
                for q in ps:
                    if _mn(q) == w or name_of(q) == w or (key and _mn(q) == key):
                        p = q
                        break
                if p is None:
                    for q in ps:
                        if name_of(q).lower() == w.lower():
                            p = q
                            break
            if p is None:
                raise ExprError(f'effect("{clean(self.fx.name)}")("{w}") not found')
        else:
            i = int(tonum(w)) - 1
            if not 0 <= i < len(ps):
                raise ExprError("effect parameter index out of range")
            p = ps[i]
        if _mn(p).endswith("Layer Control-0001") or pseudo.get(_mn(p), ("", -1))[1] == 0:
            # a layer parameter: the layer, or null when it is set to None (templates test `!= undefined`)
            idx = int(round(tonum(self.e.value(p, self.L, self.comp, self.env.time)))) - 1
            if 0 <= idx < len(self.comp.layers):
                return LayerRef(self.e, self.comp.layers[idx], self.comp, self.env)
            return None
        return PropRef(self.e, p, self.L, self.comp, self.env)

    def js_call(self, which):
        return self.param(which)

    def js_get(self, name):
        if name == "param":
            return self.param
        if name == "name":
            return clean(self.fx.name)
        if name == "active":
            return bool(getattr(self.fx, "enabled", True))
        if name == "numProperties":
            return float(len(self.params()))
        return self.param(name)


class PropRef:
    """an AE property inside an expression"""

    def __init__(self, engine, p, L, comp, env, own=False):
        self.e, self.p, self.L, self.comp, self.env, self.own = engine, p, L, comp, env, own

    def at(self, t):
        # a property's own valueAtTime() is its pre-expression value (AE rule); others are post-expression
        if self.own:
            return self.e.pre_value(self.env, self.p, t)
        return self.e.value(self.p, self.L, self.comp, t, parent_env=self.env)

    def js_value(self):
        return self.at(self.env.time)

    def js_index(self, k):
        v = self.js_value()
        if isinstance(v, list):
            i = int(tonum(k))
            return v[i] if 0 <= i < len(v) else None
        raise ExprError("index on a scalar property")

    def _keys(self):
        try:
            return list(self.p.keyframes)
        except Exception:
            return []

    def js_get(self, name):
        e, env = self.e, self.env
        if name == "value":
            return self.js_value()
        if name == "valueAtTime":
            def vat(t):
                env.mark()
                return self.at(tonum(t))
            return vat
        if name in ("velocity", "speed"):
            env.mark()
            v = self._velocity(env.time)
            return v if name == "velocity" else _length(v)
        if name in ("velocityAtTime", "speedAtTime"):
            def vel(t):
                env.mark()
                v = self._velocity(tonum(t))
                return v if name == "velocityAtTime" else _length(v)
            return vel
        if name == "numKeys":
            return float(len(self._keys()))
        if name == "key":
            def key(n):
                ks = self._keys()
                k = ks[int(tonum(n)) - 1]
                return KeyRef(k.time, to_js(k.value), int(tonum(n)))
            return key
        if name == "nearestKey":
            def near(t):
                ks = self._keys()
                if not ks:
                    raise ExprError("no keys")
                j = min(range(len(ks)), key=lambda i: abs(ks[i].time - tonum(t)))
                return KeyRef(ks[j].time, to_js(ks[j].value), j + 1)
            return near
        if name == "wiggle":
            return lambda freq, amp, octaves=1, amp_mult=0.5, t=None: e.wiggle(self, env, tonum(freq), unwrap(amp),
                                                                                 int(tonum(octaves)), tonum(amp_mult),
                                                                                 env.time if t is None else tonum(t))
        if name == "temporalWiggle":
            def tw(freq, amp, octaves=1, amp_mult=0.5, t=None):
                t = env.time if t is None else tonum(t)
                off = e.noise1(hash((id(self.p), "tw")), t * tonum(freq), int(tonum(octaves)), tonum(amp_mult))
                env.mark()
                env.inexact = env.inexact or abs(tonum(amp)) > 1e-12
                return self.at(t + off * tonum(amp))
            return tw
        if name in ("loopOut", "loopIn", "loopOutDuration", "loopInDuration"):
            def loop(kind="cycle", n=0):
                env.mark()
                return e.loop(self, env, name, tostr(kind), tonum(n))
            return loop
        if name == "smooth":
            def smooth(width=0.2, samples=5, t=None):
                t = env.time if t is None else tonum(t)
                w, n = tonum(width), max(1, int(tonum(samples)))
                env.mark()
                vals = [self.at(t - w / 2 + w * k / max(1, n - 1)) for k in range(n)]
                acc = vals[0]
                for v in vals[1:]:
                    acc = js_add(acc, v)
                return binop("/", acc, float(n))
            return smooth
        if name == "propertyGroup":
            return lambda n=1: _parent_group(self, int(tonum(n)))
        if name == "propertyIndex":
            return float(getattr(self.p, "property_index", 0) or 0)
        if name == "name":
            return clean(self.p.name)
        if name in ("points", "inTangents", "outTangents", "isClosed", "pointOnPath", "tangentOnPath", "normalOnPath"):
            v = self.js_value()
            if isinstance(v, ShapeValue):
                return v.js_get(name)
            raise ExprError(f"{name}() on a non-path property")
        if name == "expression":
            return self.p.expression or ""
        if name == "numKeysSelected":
            return 0.0
        if name == "text" or name == "sourceText":
            return self.js_value()
        v = self.js_value()
        if isinstance(v, str):
            return _string_method(v, name)
        if isinstance(v, list):
            return _array_method(v, name, None)
        if isinstance(v, float):
            return _num_method(v, name)
        raise ExprError(f"property.{name}")

    def _velocity(self, t):
        h = 0.001
        a, b = unwrap(self.at(t - h)), unwrap(self.at(t + h))
        return binop("/", binop("-", b, a), 2 * h)


def _length(v):
    v = unwrap(v)
    if isinstance(v, list):
        return math.sqrt(sum(tonum(x) ** 2 for x in v))
    return abs(tonum(v))


# ---------------------------------------------------------------- global functions
def _interp(env, t, a, b, c=None, d=None, curve=None):
    if c is None:                         # linear(t, v1, v2): t in 0..1
        t0, t1, v0, v1 = 0.0, 1.0, a, b
    elif d is None:
        raise ExprError("linear() needs 3 or 5 arguments")
    else:
        t0, t1, v0, v1 = tonum(a), tonum(b), c, d
    t = tonum(t)
    if t1 == t0:
        u = 0.0 if t < t0 else 1.0
    else:
        u = (t - t0) / (t1 - t0)             # also right when tMin > tMax (the ramp runs backwards)
    u = max(0.0, min(1.0, u))
    if curve:
        u = _bez(u, *curve)
    v0, v1 = unwrap(v0), unwrap(v1)
    return binop("+", v0, binop("*", binop("-", v1, v0), u))


def _bez(x, x1, y1, x2, y2):
    lo, hi, u = 0.0, 1.0, x
    for _ in range(40):
        xx = 3 * (1 - u) ** 2 * u * x1 + 3 * (1 - u) * u ** 2 * x2 + u ** 3
        if abs(xx - x) < 1e-7:
            break
        if xx < x:
            lo = u
        else:
            hi = u
        u = (lo + hi) / 2
    return 3 * (1 - u) ** 2 * u * y1 + 3 * (1 - u) * u ** 2 * y2 + u ** 3


EASE_IN_OUT = (0.33, 0.0, 0.667, 1.0)          # the curves AE's ease()/easeIn()/easeOut() follow
EASE_IN = (0.333, 0.0, 0.833, 0.833)
EASE_OUT = (0.167, 0.167, 0.667, 1.0)


def _clamp(env, v, lo, hi):
    v, lo, hi = unwrap(v), unwrap(lo), unwrap(hi)
    if isinstance(v, list):
        return [max(tonum(lo[i] if isinstance(lo, list) else lo), min(tonum(hi[i] if isinstance(hi, list) else hi), tonum(x)))
                for i, x in enumerate(v)]
    return max(tonum(lo), min(tonum(hi), tonum(v)))


def _normalize(env, v):
    v = unwrap(v)
    n = _length(v)
    return binop("/", v, n) if n else v


def _lengthf(env, a, b=None):
    return _length(a) if b is None else _length(binop("-", a, b))


def _seed_random(env, seed, timeless=False):
    env.timeless = truthy(timeless)
    env._seed(tonum(seed))
    return None


def _randomf(env, a=None, b=None):
    u = env.random_unit()
    if a is None:
        return u
    a = unwrap(a)
    if b is None:
        return binop("*", a, u) if not isinstance(a, list) else [tonum(x) * env.random_unit() for x in a]
    b = unwrap(b)
    if isinstance(a, list):
        return [tonum(x) + (tonum(y) - tonum(x)) * env.random_unit() for x, y in zip(a, b)]
    return tonum(a) + (tonum(b) - tonum(a)) * u


def _gauss(env, a=None, b=None):
    u1, u2 = max(1e-12, env.random_unit()), env.random_unit()
    g = math.sqrt(-2 * math.log(u1)) * math.cos(2 * math.pi * u2) / 6 + 0.5
    g = max(0.0, min(1.0, g))
    if a is None:
        return g
    if b is None:
        return binop("*", unwrap(a), g)
    return binop("+", unwrap(a), binop("*", binop("-", unwrap(b), unwrap(a)), g))


def _hsl_to_rgb(env, hsla):
    h, s, l, a = [tonum(x) for x in (list(unwrap(hsla)) + [1, 1, 1, 1])[:4]]
    import colorsys
    r, g, b = colorsys.hls_to_rgb(h % 1.0, l, s)
    return [r, g, b, a]


def _rgb_to_hsl(env, rgba):
    r, g, b, a = [tonum(x) for x in (list(unwrap(rgba)) + [1, 1, 1, 1])[:4]]
    import colorsys
    h, l, s = colorsys.rgb_to_hls(r, g, b)
    return [h, s, l, a]


def _hex_to_rgb(env, h):
    h = tostr(h).lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    return [int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)] + [1.0]


def _posterize(env, fps):
    fps = tonum(fps)
    if fps > 0:
        env.time = math.floor(env.time * fps + 1e-6) / fps
    return None


def _create_path(env, points=None, ins=None, outs=None, closed=True):
    pts = [list(map(tonum, p)) for p in (unwrap(points) or [])]
    n = len(pts)
    ins = [list(map(tonum, p)) for p in (unwrap(ins) or [])] or [[0.0, 0.0]] * n
    outs = [list(map(tonum, p)) for p in (unwrap(outs) or [])] or [[0.0, 0.0]] * n
    return ShapeValue(pts, ins, outs, truthy(closed))


def _vec2(fn):
    return lambda env, a, b=None: _vec_op(unwrap(a), unwrap(b) if b is not None else 0.0, fn)


GLOBAL_FUNCS = {
    "linear": lambda env, *a: _interp(env, *a),
    "ease": lambda env, *a: _interp(env, *a, curve=EASE_IN_OUT),
    "easeIn": lambda env, *a: _interp(env, *a, curve=EASE_IN),
    "easeOut": lambda env, *a: _interp(env, *a, curve=EASE_OUT),
    "clamp": _clamp,
    "length": _lengthf,
    "normalize": _normalize,
    "add": lambda env, a, b: binop("+", a, b),
    "sub": lambda env, a, b: binop("-", a, b),
    "mul": lambda env, a, b: binop("*", a, b),
    "div": lambda env, a, b: binop("/", a, b),
    "dot": lambda env, a, b: sum(tonum(x) * tonum(y) for x, y in zip(unwrap(a), unwrap(b))),
    "cross": lambda env, a, b: [tonum(unwrap(a)[1]) * tonum(unwrap(b)[2]) - tonum(unwrap(a)[2]) * tonum(unwrap(b)[1]),
                                tonum(unwrap(a)[2]) * tonum(unwrap(b)[0]) - tonum(unwrap(a)[0]) * tonum(unwrap(b)[2]),
                                tonum(unwrap(a)[0]) * tonum(unwrap(b)[1]) - tonum(unwrap(a)[1]) * tonum(unwrap(b)[0])],
    "degreesToRadians": lambda env, d: tonum(d) * math.pi / 180,
    "radiansToDegrees": lambda env, r: tonum(r) * 180 / math.pi,
    "framesToTime": lambda env, f, fps=None: tonum(f) / (tonum(fps) if fps is not None else float(env.comp.frame_rate or 25)),
    "timeToFrames": lambda env, t=None, fps=None, isDuration=False: float(math.floor(
        (env.time if t is None else tonum(t)) * (tonum(fps) if fps is not None else float(env.comp.frame_rate or 25)) + 1e-6)),
    "seedRandom": _seed_random,
    "random": _randomf,
    "gaussRandom": _gauss,
    "noise": lambda env, v: setattr(env, "inexact", True) or env.engine.noise1(0, tonum(unwrap(v)[0] if isinstance(unwrap(v), list) else v), 1, 0.5),
    "hslToRgb": _hsl_to_rgb,
    "rgbToHsl": _rgb_to_hsl,
    "hexToRgb": _hex_to_rgb,
    "posterizeTime": _posterize,
    "createPath": _create_path,
    "parseInt": lambda env, s, r=None: float(int(re.match(r"\s*[-+]?\d*", tostr(s)).group(0) or "nan", int(tonum(r)) if r else 10))
    if re.match(r"\s*[-+]?\d", tostr(s)) else float("nan"),
    "parseFloat": lambda env, s: tonum(re.match(r"\s*[-+]?(\d+\.?\d*|\.\d+)([eE][-+]?\d+)?", tostr(s)).group(0))
    if re.match(r"\s*[-+]?(\d|\.\d)", tostr(s)) else float("nan"),
    "isNaN": lambda env, v: math.isnan(tonum(v)),
    "isFinite": lambda env, v: math.isfinite(tonum(v)),
    "Number": lambda env, v=0.0: tonum(v),
    "String": lambda env, v="": tostr(v),
    "Boolean": lambda env, v=False: truthy(v),
    "timeToCurrentFormat": lambda env, t=None, *a: tostr(env.time if t is None else t),
    "timeToTimecode": lambda env, t=None, *a: tostr(env.time if t is None else t),
    "lookAt": lambda env, *a: (_ for _ in ()).throw(ExprError("lookAt (3D) is not supported")),
}


# ================================================================== the engine
def bake_steps(p):
    """[(match name, rank among same-named siblings)] from the layer down to p: the address a script finds p by
    in After Effects (ranks keep two Fills or two Stroke effects apart)"""
    steps, q = [], p
    while q is not None:
        par = getattr(q, "parent_property", None)
        if par is None:
            break
        mn = _mn(q)
        sib = [x for x in _children(par) if _mn(x) == mn]
        steps.append((mn, next((i for i, x in enumerate(sib) if x is q), 0)))
        q = par
    return list(reversed(steps))


def bake_key(p, L, comp):
    return f"{clean(comp.name)}|{L.index}|" + "/".join(f"{mn}#{k}" for mn, k in bake_steps(p))


def pseudo_param_names(path):
    """{match name: (parameter name, type)} of the pseudo effects (custom effect presets) defined in a .aep. py-aep
    names their parameters by type ("Color 1", "Color 2"…) and does not know a layer parameter from a number; the
    names expressions use ("Background") and the types are only in the project's effect definitions: a `tdmn` chunk
    (Pseudo/…-00NN) followed by its `pard` chunk (type at byte 15 — the SDK's PF_Param_*: 0 layer, 4 checkbox,
    5 colour, 7 popup… —, name on the 32 bytes after it). Custom names are not translated: they resolve in any AE
    language."""
    import struct
    try:
        b = open(path, "rb").read()
    except Exception:
        return {}
    out = {}
    i = b.find(b"tdmn")
    while i >= 0:
        try:
            n = struct.unpack(">I", b[i + 4:i + 8])[0]
            mn = b[i + 8:i + 8 + n].split(b"\0", 1)[0].decode("latin-1")
            j = i + 8 + n + (n & 1)
            if mn.startswith("Pseudo/") and b[j:j + 4] == b"pard":
                data = b[j + 8:j + 8 + struct.unpack(">I", b[j + 4:j + 8])[0]]
                raw = data[16:48].split(b"\0", 1)[0]
                try:
                    name = raw.decode("utf-8")
                except UnicodeDecodeError:
                    name = raw.decode("latin-1")
                if name.strip():
                    out.setdefault(mn, (name, data[15] if len(data) > 15 else -1))
        except Exception:
            pass
        i = b.find(b"tdmn", i + 4)
    return out


def param_defaults(path):
    """{match name: AE's default value} of effect parameters, read in the project's effect definitions (`tdmn` +
    `pard`). A parameter the effect instance does not store takes this default in AE. py-aep takes another pard field
    instead (the definition's last value): 75 for three Glass Animation sliders where AE renders 0, 0 for TextEvo's
    opacity where AE keeps 100 (the numbers then vanished in Rive). Layout measured: float slider (type 10) default =
    float32 at pard byte 48 + 72; popup (7) = uint16 at 48 + 14; checkbox (4) = byte 48 + 12; angle / layer (3, 0)
    = 0."""
    import struct
    try:
        b = open(path, "rb").read()
    except Exception:
        return {}
    POPUP_N.clear()
    out = {}
    i = b.find(b"tdmn")
    while i >= 0:
        try:
            n = struct.unpack(">I", b[i + 4:i + 8])[0]
            mn = b[i + 8:i + 8 + n].split(b"\0", 1)[0].decode("latin-1")
            j = i + 8 + n + (n & 1)
            if mn not in out and b[j:j + 4] == b"pard":
                data = b[j + 8:j + 8 + struct.unpack(">I", b[j + 4:j + 8])[0]]
                ty = data[15] if len(data) > 15 else -1
                if ty == 10 and len(data) >= 124:
                    out[mn] = float(struct.unpack(">f", data[120:124])[0])
                elif ty == 7 and len(data) >= 64:
                    out[mn] = float(struct.unpack(">H", data[62:64])[0])
                    POPUP_N[mn] = struct.unpack(">H", data[60:62])[0]        # number of menu items
                elif ty == 4 and len(data) >= 61:
                    # checkbox: default at byte 48 + 12 (Randomatic's "on / off" and "absolute" default to ON — read as
                    # 0 they sent every rig-test camera level down the "off" branch; AE reads 1, checked in AE 26.5)
                    out[mn] = float(data[60] & 1)
                elif ty in (0, 3):
                    out[mn] = 0.0
        except Exception:
            pass
        i = b.find(b"tdmn", i + 4)
    return out


_PARAM_DEFAULTS = {}
POPUP_N = {}          # popup / dropdown parameter match name -> item count (AE clamps a value to 1..n)


class Engine:
    """Post-expression values of AE properties, with a cache and time-dependency analysis."""

    def __init__(self, project, report=None, lang=None, pseudo=None, defaults=None):
        self.project = project
        self.report = report
        _PARAM_DEFAULTS.clear()
        _PARAM_DEFAULTS.update(defaults or {})
        self.pseudo = pseudo or {}             # pseudo effects' parameter names, by match name
        self.bake = {}                         # bake_key -> (fps, [values per frame]) sampled by After Effects
        self.bake_requests = {}                # bake_key -> (comp, layer, property) whose expression failed here
        self.bake_once = set()                 # of those, evaluated OK but random/wiggle: static ones need 1 sample
        self.lang = lang or ae_lang()          # AE's UI language to emulate (names in expressions): fr / en / any
        self.cache = {}
        self.static = {}             # id(prop) -> (is_static, value)
        self._ctx_key = None         # Essential Properties context (None: no override) -> its own caches
        self._ctxs = {}
        self.failed = {}             # expression text -> error
        self.stack = []
        self.comps = {}
        for c in project.compositions:
            self.comps.setdefault(clean(c.name), c)

    def use_context(self, key):
        """switch the value caches to those of an Essential Properties context (a precomp built with an
        instance's overrides: the same property has other values there). -> the previous key"""
        prev = self._ctx_key
        if key == prev:
            return prev
        self._ctxs[prev] = (self.cache, self.static)
        self.cache, self.static = self._ctxs.pop(key, ({}, {}))
        self._ctx_key = key
        return prev

    def comp_by_name(self, name):
        c = self.comps.get(name)
        if c is None:
            raise ExprError(f'comp("{name}") not found')
        return c

    @staticmethod
    def has_expr(p):
        try:
            return bool(p.expression) and bool(p.expression_enabled)
        except Exception:
            return False

    def pre_value(self, env, p, t):
        """pre-expression value (marks the evaluation time-dependent when the property is animated)"""
        if nkeys(p) >= 2:
            env.mark()
        try:
            v = raw_value(p, env.layer if getattr(env, "prop", None) is p else None, t)
        except Exception:
            v = p.value
        return to_js(v)

    def value(self, p, L, comp, t, parent_env=None):
        """post-expression value at t (JS form)"""
        if not self.has_expr(p):
            if nkeys(p) >= 2 and parent_env is not None:
                parent_env.mark()
            try:
                return to_js(raw_value(p, L, t))
            except Exception:
                return to_js(p.value)
        st = self.static.get(id(p))
        if st is not None and st[0]:
            return st[1]
        key = (id(p), round(t * 1e4))
        if key in self.cache:
            v, dep = self.cache[key]
            if dep and parent_env is not None:
                parent_env.mark()
            return v
        try:
            v, dep = self._eval(p, L, comp, t)
        except ExprError:
            # a failing expression is disabled in AE: whoever reads the property gets its pre-expression value
            # (rig-test: a parent's position reads effect("2D Camera")("enabled"), unknown in a French AE)
            try:
                v, dep = to_js(raw_value(p, L, t)), nkeys(p) >= 2
            except Exception:
                v, dep = to_js(p.value), False
        self.cache[key] = (v, dep)
        if dep and parent_env is not None:
            parent_env.mark()
        return v

    def eval_extra(self, p, L, comp, t, extra):
        """the expression of p at t with extra globals (a text selector's textIndex / textTotal / selectorValue);
        not cached. -> (value, depends on time)"""
        env = Env(self, p, L, comp, t)
        env.extra = extra
        v = Interp(env).run(parse(p.expression), Scope())
        return unwrap(v), env.dep

    def _eval(self, p, L, comp, t):
        if id(p) in self.stack:
            # a property read from its own expression (`transform.scale` in Scale's): AE gives the pre-expression
            # value (Motion Bro's [AMD] nulls rely on it)
            return to_js(raw_value(p, L, t)), nkeys(p) >= 2
        if self.bake:
            b = self.bake.get(bake_key(p, L, comp))
            if b is not None:
                fps, vals = b
                i = max(0, min(len(vals) - 1, int(round(t * fps))))
                return vals[i], len(vals) > 1 and any(v != vals[0] for v in vals)
        self.stack.append(id(p))
        env = Env(self, p, L, comp, t)
        try:
            src = p.expression
            prog = parse(src)
            v = Interp(env).run(prog, Scope())
            v = unwrap(v)
            v = self._conform(p, env, v)
            if env.inexact:
                # evaluated, but with this module's random generator / noise: AE's values differ (Randomatic, Duik
                # camera levels in rig-test). Offered to bake_expressions.jsx; ours stand in until then
                try:
                    k = bake_key(p, L, comp)
                    if k not in self.bake_requests:
                        self.bake_requests[k] = (comp, L, p)
                        if not env.dep:
                            self.bake_once.add(k)
                    elif env.dep:
                        self.bake_once.discard(k)
                except Exception:
                    pass
            return v, env.dep
        except (ExprError, _Throw, RecursionError, ZeroDivisionError, TypeError, ValueError, IndexError, KeyError,
                AttributeError, OverflowError) as ex:
            src = p.expression or ""
            if src not in self.failed:
                self.failed[src] = f"{type(ex).__name__}: {ex}"
            try:
                self.bake_requests.setdefault(bake_key(p, L, comp), (comp, L, p))
            except Exception:
                pass
            raise ExprError(str(ex))
        finally:
            self.stack.pop()

    def _conform(self, p, env, v):
        """AE fits the result to the property: missing dimensions keep the pre-expression value"""
        pre = self.pre_value(Env(self, p, env.layer, env.comp, env.time), p, env.time)
        if isinstance(pre, list):
            if isinstance(v, list):
                out = list(pre)
                for i in range(min(len(v), len(out))):
                    out[i] = tonum(v[i])
                if any(math.isnan(x) for x in out if isinstance(x, float)):
                    raise ExprError("NaN in result")
                return out
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                raise ExprError("number returned for a multi-dimensional property")
            raise ExprError(f"result type {type(v).__name__}")
        if isinstance(pre, ShapeValue):
            if isinstance(v, ShapeValue):
                return v
            raise ExprError("path expression did not return a path")
        if isinstance(pre, str):
            return tostr(v)
        if isinstance(v, list):
            v = v[0] if v else 0.0
        v = tonum(v)
        if math.isnan(v):
            raise ExprError("NaN result")
        n = POPUP_N.get(getattr(p, "match_name", ""))
        if n:
            # a dropdown holds 1..n: AE clamps what an expression gives it (broadcast-test: a 3-view menu fed the 8-way
            # glace menu, 7 → 3, which is why the popsicles show a view at all)
            v = float(max(1, min(n, int(round(v)))))
        return v

    def analyze(self, p, L, comp, fps, nframes):
        """-> ('static', value) or ('frames', [value per frame]) or ('failed', message). Values are JS form."""
        try:
            v0, dep = self._eval(p, L, comp, 0.0)
        except ExprError as ex:
            return ("failed", self.failed.get(p.expression or "", str(ex)))
        if not dep:
            self.static[id(p)] = (True, v0)
            return ("static", v0)
        vals = []
        try:
            for f in range(nframes + 1):
                t = f / fps
                key = (id(p), round(t * 1e4))
                if key in self.cache:
                    vals.append(self.cache[key][0])
                    continue
                v, d = self._eval(p, L, comp, t)
                self.cache[key] = (v, d)
                vals.append(v)
        except ExprError as ex:
            return ("failed", self.failed.get(p.expression or "", str(ex)))
        return ("frames", vals)

    # ---- helpers used by the globals
    def noise1(self, seed, x, octaves=1, mult=0.5):
        total, amp, freq, norm = 0.0, 1.0, 1.0, 0.0
        for o in range(max(1, octaves)):
            total += amp * self._grad_noise(hash((seed, o)) & 0xFFFFFF, x * freq)
            norm += amp
            amp *= mult
            freq *= 2
        return total / norm if norm else 0.0

    @staticmethod
    def _grad_noise(seed, x):
        i = math.floor(x)
        f = x - i

        def grad(k):
            h = _random.Random((seed * 1000003 + int(k)) & 0xFFFFFFFF).random()
            return h * 2 - 1
        g0, g1 = grad(i), grad(i + 1)
        u = f * f * (3 - 2 * f)
        return (g0 * f * (1 - u) + g1 * (f - 1) * u) * 2.0

    def wiggle(self, ref, env, freq, amp, octaves, mult, t):
        env.mark()
        if any(abs(tonum(a)) > 1e-12 for a in (amp if isinstance(amp, list) else [amp])) and freq:
            env.inexact = True
        base = unwrap(ref.at(t))
        seed = hash((clean(ref.L.name), _mn(ref.p)))
        if isinstance(base, list):
            amps = amp if isinstance(amp, list) else [amp] * len(base)
            return [tonum(b) + self.noise1(seed + d, t * freq, octaves, mult) * tonum(amps[d] if d < len(amps) else 0)
                    for d, b in enumerate(base)]
        return tonum(base) + self.noise1(seed, t * freq, octaves, mult) * tonum(amp)

    def loop(self, ref, env, kind_name, mode, n):
        ks = ref._keys()
        t = env.time
        pre = (lambda tt: self.pre_value(env, ref.p, tt)) if ref.own else ref.at
        if len(ks) < 2:
            return pre(t)
        out = kind_name.startswith("loopOut")
        if kind_name.endswith("Duration"):
            dur = n if n > 0 else (ks[-1].time - ks[0].time)
            first, last = ((ks[-1].time - dur, ks[-1].time) if out else (ks[0].time, ks[0].time + dur))
        else:
            n = int(n)
            if out:
                i0 = 0 if n <= 0 else max(0, len(ks) - 1 - n)
                first, last = ks[i0].time, ks[-1].time
            else:
                i1 = len(ks) - 1 if n <= 0 else min(len(ks) - 1, n)
                first, last = ks[0].time, ks[i1].time
        dur = last - first
        if dur <= 0:
            return pre(t)
        if out and t <= last:
            return pre(t)
        if not out and t >= first:
            return pre(t)
        mode = mode.lower()
        if mode == "continue":
            if out:
                v = self._vel(pre, last - 1e-3)
                return binop("+", pre(last), binop("*", v, t - last))
            v = self._vel(pre, first + 1e-3)
            return binop("-", pre(first), binop("*", v, first - t))
        k = math.floor((t - first) / dur)
        r = (t - first) - k * dur
        if mode == "pingpong":
            tt = first + (r if k % 2 == 0 else dur - r)
            return pre(tt)
        tt = first + r
        v = pre(tt)
        if mode == "offset":
            delta = binop("-", pre(last), pre(first))
            v = binop("+", v, binop("*", delta, float(k)))
        return v

    @staticmethod
    def _vel(pre, t):
        h = 1e-3
        return binop("/", binop("-", unwrap(pre(t + h)), unwrap(pre(t - h))), 2 * h)

    # ---- geometry helpers (sourceRectAtTime, toComp)
    def layer_matrix(self, L, comp, env, t):
        from . import geom
        m = geom.IDENT
        chain = []
        cur = L
        while cur is not None:
            chain.append(cur)
            cur = cur.parent
        for lay in reversed(chain):
            tr = lay.transform

            def g(mn, default):
                p = tr.property(mn) if tr is not None else None
                if p is None:
                    return default
                return unwrap(self.value(p, lay, comp, t, parent_env=env))
            pos = g("ADBE Position", [0, 0, 0])
            anc = g("ADBE Anchor Point", [0, 0, 0])
            sc = g("ADBE Scale", [100, 100, 100])
            rot = g("ADBE Rotate Z", 0.0)
            m = geom.mul(m, geom.trs(pos, rot, (sc[0] / 100, sc[1] / 100), anc))
        return m

    def projector(self, comp, env):
        from .three import Projector
        if not hasattr(self, "_proj"):
            self._proj = {}
        pr = self._proj.get(comp.id)
        if pr is None:
            pr = self._proj[comp.id] = Projector(self, comp)
        pr.env = env
        return pr

    def to_comp(self, L, comp, env, p, t, world=False):
        from . import geom
        from .three import is3d
        if is3d(L):
            # a 3D layer: world space in 3D; comp space through the active camera (AE's toComp does see the camera)
            pr = self.projector(comp, env)
            pt = [tonum(p[i]) if i < len(p) else 0.0 for i in range(3)]
            if world:
                return pr.world(L, t).transform_point(pt)
            return pr.to_comp(L, pt, t)
        m = self.layer_matrix(L, comp, env, t)
        x, y = geom.apply(m, (tonum(p[0]), tonum(p[1])))
        return [x, y] + ([tonum(p[2])] if len(p) > 2 else [])

    def from_comp(self, L, comp, env, p, t, world=False):
        from . import geom
        from .three import is3d
        if is3d(L):
            pr = self.projector(comp, env)
            if world:
                try:
                    return pr.world(L, t).inverse().transform_point([tonum(p[i]) if i < len(p) else 0.0 for i in range(3)])
                except ValueError:
                    return [0.0, 0.0, 0.0]
            r = pr.from_comp(L, [tonum(p[0]), tonum(p[1])], t)
            return r if r is not None else [0.0, 0.0]          # AE's own answer when the ray misses the plane
        m = geom.invert(self.layer_matrix(L, comp, env, t)) or geom.IDENT
        x, y = geom.apply(m, (tonum(p[0]), tonum(p[1])))
        return [x, y] + ([tonum(p[2])] if len(p) > 2 else [])

    def source_rect(self, L, comp, env, t, extents):
        """bounds of the layer content in layer space (shapes, solids, footage; text approximated by font metrics)"""
        env.mark()                                   # conservative: a static result is detected later (constant frames)
        kind = type(L).__name__
        if kind == "TextLayer":
            from .util import find_font, font_metrics, font_ink, STAND_IN_TTC
            doc = L.text.property("ADBE Text Document").value
            size = float(doc.font_size or 12)
            found = find_font(clean(getattr(doc.font_object, "post_script_name", "") or doc.font))
            lines = clean(doc.text).replace("\r", "\n").split("\n")
            if getattr(doc, "all_caps", False):
                lines = [l.upper() for l in lines]
            lead = float(doc.leading) if (doc.leading and not getattr(doc, "auto_leading", False)) else size * 1.2
            just = getattr(doc.justification, "name", None) or str(doc.justification)
            # glyph ink bounds, like AE (a missing font is measured in AE's stand-in, Helvetica)
            ink = font_ink(*found) if found else font_ink(STAND_IN_TTC, 0)
            if ink is not None:
                box = None
                bshift = float(getattr(doc, "baseline_shift", 0) or 0)
                for i, line in enumerate(lines):
                    x0, y0, x1, y1, adv = ink(line, size, float(getattr(doc, "tracking", 0) or 0))
                    if x1 <= x0:
                        continue
                    dx = -adv / 2 if "CENTER" in just else (-adv if "RIGHT" in just else 0.0)
                    dy = i * lead - bshift
                    b = (x0 + dx, y0 + dy, x1 + dx, y1 + dy)
                    box = b if box is None else (min(box[0], b[0]), min(box[1], b[1]), max(box[2], b[2]), max(box[3], b[3]))
                if box is not None:
                    return JSObject(top=box[1], left=box[0], width=box[2] - box[0], height=box[3] - box[1])
            asc, desc, measure = font_metrics(found[0]) if found else (0.93, 0.25, lambda s, z: len(s) * z * 0.55)
            w = max((measure(l, size) for l in lines), default=0.0)
            h = asc * size + desc * size + lead * (len(lines) - 1)
            left = -w / 2 if "CENTER" in just else (-w if "RIGHT" in just else 0.0)
            return JSObject(top=-asc * size, left=left, width=w, height=h)
        if kind == "ShapeLayer":
            from .shapes import layer_bounds
            x0, y0, x1, y1 = layer_bounds(L, self, comp, t, extents)
            return JSObject(top=y0, left=x0, width=x1 - x0, height=y1 - y0)
        src = getattr(L, "source", None)
        w = float(getattr(src, "width", 0) or comp.width)
        h = float(getattr(src, "height", 0) or comp.height)
        return JSObject(top=0.0, left=0.0, width=w, height=h)
