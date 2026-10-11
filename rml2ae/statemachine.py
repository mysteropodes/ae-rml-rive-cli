"""Rive state machine with inputs -> data for an After Effects expression that plays it.

The Rive runtime (rive-runtime src/animation/state_machine_instance.cpp, state_transition.cpp, blend_state_*_instance.cpp)
advances a layer frame by frame: the current state's clock moves, the transition mix moves, then transitions are tried
(the Any State's first, then the current state's; the first allowed one is taken). A transition is allowed when every
condition holds (number / bool comparison, a trigger fired this frame) and, with exit time, once the state's animation
has reached it.

After Effects has no state: an expression is a pure function of time. `SIM_JS` replays the layer from frame 0 up to the
frame asked for, reading the inputs (the controller's effect controls) at each frame, so keying an input in AE changes
what plays from that frame on. Nothing here knows about the AE layers; convert.py builds them.
"""
import json

from .model import is_a

OPS = {"equal": 0, "notequal": 1, "lessthanorequal": 2, "greaterthanorequal": 3, "lessthan": 4, "greaterthan": 5}
LOOPS = {"oneshot": 0, "loop": 1, "pingpong": 2, "0": 0, "1": 1, "2": 2}

# StateTransitionFlags (rive-runtime state_transition_flags.hpp)
F_DISABLED, F_DURATION_PCT, F_EXIT_TIME, F_EXIT_PCT, F_PAUSE_ON_EXIT = 1, 2, 4, 8, 16


def _op(v):
    v = (v or "0").strip()
    if v.lstrip("-").isdigit():
        return int(v)
    return OPS.get(v.lower().replace("_", ""), 0)


def _flag(tr, name, bit, aliases=()):
    """a transition flag, written as the packed `flags` or as a named boolean (RML spells both)"""
    raw = tr.get("flags")
    if raw not in (None, "") and raw.isdigit() and int(raw) & bit:
        return True
    return any(tr.get(n) == "true" for n in (name,) + tuple(aliases))


def first_layer(sm):
    layers = sm.findall("StateMachineLayer") if sm is not None else []
    return layers[0] if layers else None


def default_sm(project, artboard):
    sm_id = artboard.get("defaultStateMachineId")
    sms = artboard.findall("StateMachine")
    return project.by_id.get(sm_id) if sm_id else (sms[0] if sms else None)


def is_interactive(project, artboard):
    """the default state machine needs more than a chain of exit-time transitions: inputs read by its conditions or
    blend states, Any State transitions, or blend states"""
    sm = default_sm(project, artboard)
    layer = first_layer(sm)
    if layer is None:
        return False
    for st in layer.children:
        if is_a(st.tag, "BlendState"):
            return True
        if st.tag == "AnyState" and st.findall("StateTransition"):
            return True
        for tr in st.children:
            if is_a(tr.tag, "StateTransition") and any(is_a(c.tag, "TransitionCondition") for c in tr.children):
                return True
    return False


def build(project, artboard):
    """-> (data dict for SIM_JS, notes [str]).

    data = {inputs: [{name, kind: number|bool|trigger, value}],
            states: [{name, kind: entry|anim|blend1d|direct|exit|any, speed, anims: [{id, dur, loop, speed, value,
                      input, mix}], input: index of the blend input or -1, tr: [transition]}],
            any: [transition], start: index of the entry state}
    transition = {to, conds: [{input, op, value}], exit: seconds or -1 (fraction of the animation when pct), pct,
                  mixDur: seconds or -1, mixPct, pause}
    """
    sm = default_sm(project, artboard)
    layer = first_layer(sm)
    notes = []
    layers = sm.findall("StateMachineLayer")
    if len(layers) > 1:
        notes.append(f"state machine '{sm.name}' has {len(layers)} layers: only '{layer.name}' is played in AE "
                     f"(the others: " + ", ".join(repr(x.name) for x in layers[1:]) + ")")
    inputs, in_idx = [], {}
    for c in sm.children:
        kind = {"StateMachineNumber": "number", "StateMachineBool": "bool", "StateMachineTrigger": "trigger"}.get(c.tag)
        if kind is None:
            continue
        if c.id:
            in_idx[c.id] = len(inputs)
        val = c.num("value", 0) if kind == "number" else (1 if c.get("value") == "true" else 0)
        inputs.append({"name": c.name, "kind": kind, "value": val})

    states, st_idx, order = [], {}, []
    for st in layer.children:
        if st.tag == "AnyState" or not (is_a(st.tag, "LayerState") or st.tag in ("EntryState", "ExitState")):
            continue
        order.append(st)
    for st in order:
        key = st.id or f"#{len(states)}"
        st_idx[key] = len(states)
        st_idx[id(st)] = len(states)
        entry = {"name": st.get("name") or st.tag, "kind": "entry", "speed": st.num("speed", 1) or 1.0,
                 "anims": [], "input": -1, "tr": []}
        if st.tag == "EntryState":
            entry["kind"] = "entry"
        elif st.tag == "ExitState":
            entry["kind"] = "exit"
        elif st.tag == "AnimationState":
            a = project.animations.get(st.get("animationId"))
            entry["kind"] = "anim"
            if a is not None:
                entry["anims"].append(_anim(a))
                if not st.get("name"):
                    entry["name"] = a.name
        elif is_a(st.tag, "BlendState1D"):
            entry["kind"] = "blend1d"
            entry["input"] = in_idx.get(st.get("inputId"), -1)
            if st.tag != "BlendState1DInput":
                notes.append(f"blend state '{entry['name']}' reads a ViewModel property: driven by its first animation value")
            for b in st.findall("BlendAnimation1D"):
                a = project.animations.get(b.get("animationId"))
                if a is not None:
                    d = _anim(a)
                    d["value"] = b.num("value", 0)
                    entry["anims"].append(d)
            entry["anims"].sort(key=lambda d: d["value"])
        elif st.tag == "BlendStateDirect":
            entry["kind"] = "direct"
            for b in st.findall("BlendAnimationDirect"):
                a = project.animations.get(b.get("animationId"))
                if a is not None:
                    d = _anim(a)
                    src = (b.get("blendSource") or "0").strip().lower()
                    d["input"] = in_idx.get(b.get("inputId"), -1) if src in ("0", "inputid", "input") else -1
                    d["mix"] = b.num("mixValue", 100)
                    entry["anims"].append(d)
        else:
            notes.append(f"state '{entry['name']}' ({st.tag}): not converted, it shows nothing")
            entry["kind"] = "exit"
        states.append(entry)

    def transitions(st, from_state):
        out = []
        for tr in st.children:
            if not is_a(tr.tag, "StateTransition"):
                continue
            to = st_idx.get(tr.get("stateToId"))
            if to is None or _flag(tr, "disabled", F_DISABLED, ("isDisabled",)):
                continue
            conds = []
            for c in tr.children:
                if not is_a(c.tag, "TransitionCondition"):
                    continue
                i = in_idx.get(c.get("inputId"))
                if i is None:
                    # rive-runtime: a condition without its input evaluates to true
                    if not is_a(c.tag, "TransitionInputCondition"):
                        notes.append(f"transition from '{from_state}' has a {c.tag}: ignored (always true in AE)")
                    continue
                conds.append({"input": i, "op": _op(c.get("opValue")), "value": c.num("value", 0)})
            exit_on = _flag(tr, "enableExitTime", F_EXIT_TIME)
            exit_pct = _flag(tr, "exitTimeIsPercentage", F_EXIT_PCT, ("exitTimeIsPercetange",))
            dur_pct = _flag(tr, "durationIsPercentage", F_DURATION_PCT)
            et, d = tr.num("exitTime", 0), tr.num("duration", 0)
            out.append({"to": to, "conds": conds,
                        "exit": (et / 100.0 if exit_pct else et / 1000.0) if exit_on else -1, "pct": 1 if exit_pct else 0,
                        "mixDur": (d / 100.0 if dur_pct else d / 1000.0) if d > 0 else 0, "mixPct": 1 if dur_pct else 0,
                        "pause": 1 if _flag(tr, "pauseOnExit", F_PAUSE_ON_EXIT) else 0})
        return out

    for st in order:
        states[st_idx[id(st)]]["tr"] = transitions(st, states[st_idx[id(st)]]["name"])
    anys = []
    for st in layer.children:
        if st.tag == "AnyState":
            anys += transitions(st, "Any State")
    start = next((i for i, s in enumerate(states) if s["kind"] == "entry"), 0)
    return {"inputs": inputs, "states": states, "any": anys, "start": start}, notes


def _anim(a):
    return {"id": a.id, "name": a.name, "dur": a.duration / a.fps, "loop": LOOPS.get(str(a.loop).lower(), 1),
            "speed": a.speed or 1.0, "value": 0.0, "input": -1, "mix": 100.0}


# The layer, replayed frame by frame (time -> [state, state time, previous state, its time, mix]). `IN(i, t)` reads
# input i at time t; it is defined by the expression that includes this code (effect controls of the controller).
SIM_JS = r"""
function rsmTotal(a, tt) { return tt * a.speed; }
function rsmLocal(a, tt) {
  var d = a.dur, u = tt * a.speed;
  if (d <= 0) return 0;
  if (a.loop == 0) return Math.min(Math.max(u, 0), d);
  if (a.loop == 1) return u - Math.floor(u / d) * d;
  var c = u - Math.floor(u / (2 * d)) * 2 * d; return c <= d ? c : 2 * d - c;
}
function rsmCond(c, t, dt, k, used) {
  var inp = RSM.inputs[c.input], v = IN(c.input, t);
  if (inp.kind == "trigger") {
    if (used[c.input]) return false;
    var before = k > 0 ? IN(c.input, t - dt) : 0;
    return v > 0.5 && !(before > 0.5);
  }
  if (inp.kind == "bool") return c.op == 0 ? v > 0.5 : (c.op == 1 ? !(v > 0.5) : true);
  switch (c.op) {
    case 0: return v == c.value; case 1: return v != c.value; case 2: return v <= c.value;
    case 3: return v >= c.value; case 4: return v < c.value; case 5: return v > c.value;
  }
  return false;
}
function rsmAllowed(tr, s, st, t, dt, k, used) {
  for (var i = 0; i < tr.conds.length; i++) if (!rsmCond(tr.conds[i], t, dt, k, used)) return false;
  if (tr.exit >= 0 && s >= 0 && RSM.states[s].kind == "anim" && RSM.states[s].anims.length) {
    var a = RSM.states[s].anims[0], d = a.dur;
    var e = tr.pct ? tr.exit * d : tr.exit;
    var now = rsmTotal(a, st), last = rsmTotal(a, Math.max(0, st - dt * RSM.states[s].speed));
    if (d > 0 && e <= d && a.loop != 0) e += Math.floor(last / d) * d;
    if (now < e) return false;
  }
  return true;
}
function rsmRun(T) {
  var dt = thisComp.frameDuration, n = Math.round(T / dt);
  var cur = RSM.start, ct = 0, from = -1, ft = 0, mix = 1, mixT = 0, pause = 0;
  for (var k = 0; k <= n; k++) {
    var t = k * dt;
    if (k > 0) {
      ct += dt * RSM.states[cur].speed;
      if (from >= 0 && mix < 1) {
        mix = mixT > 0 ? Math.min(1, mix + dt / mixT) : 1;
        if (!pause) ft += dt * RSM.states[from].speed;
      }
    }
    var used = {};
    for (var it = 0; it < 8; it++) {
      var lists = [RSM.any, RSM.states[cur].tr], taken = null;
      for (var L = 0; L < 2 && !taken; L++) {
        var trs = lists[L];
        for (var j = 0; j < trs.length; j++) {
          var tr = trs[j];
          if (tr.to == cur) continue;
          if (rsmAllowed(tr, L == 0 ? -1 : cur, ct, t, dt, k, used)) { taken = tr; break; }
        }
      }
      if (!taken) break;
      for (var c = 0; c < taken.conds.length; c++) if (RSM.inputs[taken.conds[c].input].kind == "trigger") used[taken.conds[c].input] = 1;
      if (RSM.states[taken.to].kind == "exit") { cur = taken.to; break; }
      var fromKind = RSM.states[cur].kind;
      if (fromKind == "entry" || fromKind == "exit") { from = -1; ft = 0; mix = 1; mixT = 0; }
      else {
        mixT = taken.mixPct ? taken.mixDur * (RSM.states[cur].anims.length ? RSM.states[cur].anims[0].dur : 0) : taken.mixDur;
        from = cur; ft = ct; mix = mixT > 0 ? 0 : 1; pause = taken.pause;
        if (taken.pause && taken.exit >= 0 && RSM.states[cur].anims.length) {
          var a0 = RSM.states[cur].anims[0];
          ft = (taken.pct ? taken.exit * a0.dur : taken.exit) / a0.speed;
        }
      }
      cur = taken.to; ct = 0;
    }
  }
  return [cur, ct, from, ft, mix];
}
function rsmWeights(P, Q) {
  // P = [state, its time, previous state], Q = [previous state's time, mix] -> weight of every (state, animation)
  // in RSM.slots order, the base pose first (what no state covers)
  var w = [], sum = 0;
  for (var i = 0; i < RSM.slots.length; i++) {
    var sl = RSM.slots[i];
    if (sl.s < 0) { w.push(0); continue; }
    var ws = (sl.s == P[0] ? (P[2] >= 0 ? Q[1] : 1) : 0) + (sl.s == P[2] && P[2] != P[0] ? 1 - Q[1] : 0);
    var st = RSM.states[sl.s], wa = 1;
    if (st.kind == "blend1d") {
      var x = st.input >= 0 ? IN(st.input, time) : 0, A = st.anims, idx = 0;
      while (idx < A.length && A[idx].value < x) idx++;
      if (idx == 0 || idx >= A.length) wa = (sl.a == Math.min(idx, A.length - 1)) ? 1 : 0;
      else {
        var lo = A[idx - 1].value, hi = A[idx].value, m = hi == lo ? 1 : (x - lo) / (hi - lo);
        wa = sl.a == idx ? m : (sl.a == idx - 1 ? 1 - m : 0);
      }
    } else if (st.kind == "direct") {
      var an = st.anims[sl.a], mv = an.input >= 0 ? IN(an.input, time) : an.mix;
      wa = Math.min(1, Math.max(0, mv / 100));
    }
    w.push(ws * wa); sum += ws * wa;
  }
  w[0] = Math.max(0, 1 - sum);
  return w;
}
"""


def slots(data):
    """the layers of the AE comp, bottom first: the base pose, then every (state, animation) pair"""
    out = [{"s": -1, "a": -1}]
    for i, s in enumerate(data["states"]):
        for j, _a in enumerate(s["anims"]):
            out.append({"s": i, "a": j})
    return out


def header(data, ctrl):
    """the code every expression of the comp starts with: the data, the input reader, the simulation"""
    reads = []
    for i, inp in enumerate(data["inputs"]):
        mn = "ADBE Slider Control-0001" if inp["kind"] == "number" else "ADBE Checkbox Control-0001"
        reads.append(f"{ctrl}.effect({json.dumps(control_name(inp))})({json.dumps(mn)})")
    return ("var RSM = " + json.dumps(dict(data, slots=slots(data)), separators=(",", ":")) + ";\n"
            "var RSM_IN = [" + ",".join(reads) + "];\n"
            "function IN(i, t) { return RSM_IN[i].valueAtTime(t); }\n" + SIM_JS)


def control_name(inp):
    return inp["name"] + (" (trigger)" if inp["kind"] == "trigger" else "")
