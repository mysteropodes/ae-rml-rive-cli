"""Review artboard for a Rive CLI film: the film on a loop + a scrubbable timeline bar (named parts,
ruler marks, musical accents), play / pause, counter, synchronized soundtrack. Self-contained: no dependency on the
project's generator, only ids and two files (ReviewPlayer.luau, a font).

Usage (inside a project's generator):

    import sys; sys.path.insert(0, ".../01_RIV_LIBRARY/review")
    from review_timeline import review_rml, install_player
    install_player(project_dir)                      # copies ReviewPlayer.luau into the project (scanned by the CLI)
    artboard_xml, roots_xml = review_rml(
        next_id=ids.next,                            # function returning a fresh RML id ("900:123")
        film_id=film.id, film_anim_id=film.anim.id,  # the film's artboard and ITS timeline (the one the review scrubs)
        width=1200, height=1460, duration=43.97,
        parts=[(0, "Intro"), (7.06, "Verse"), ...],   # (start in s, name)
        accents=[4.53, 13.6, ...],                   # optional: gold diamonds (music hits)
        font_file="../tools/src/fonts/Poppins-Medium.ttf",
        audio_file="../tools/audio/soundtrack.mp3",  # optional; mp3/wav, path relative to the project
        name="Film Review")
    scene = scene.replace("</Rive>", artboard_xml + roots_xml + "</Rive>")

Then: `rive <project> --artboard="Film Review" --fit=contain` (viewer: loop + sound).

CONTRACT: the film must be a function of its main timeline. Everything nested in it that must follow the scrub
(acts, shots) goes through a NestedRemapAnimation whose `time` (fraction 0..1) is keyed linearly 0 -> 1 in
that timeline — not through a NestedStateMachine (which advances on the real clock and would not follow the scrub).
Characters nested in a NestedStateMachine keep running their ambient loop, even when paused: this is intended.
"""
import json
import os
import shutil

HERE = os.path.dirname(os.path.abspath(__file__))

PANEL_H = 240
COL_W, ROWS, ROW_H, ROW_Y0 = 380, 10, 62, 96      # the "Notes" column: width, rows, step, first row
NOTES = os.environ.get('REVIEW_NOTES', '1') != '0'   # debug: REVIEW_NOTES=0 removes the annotation layer
BG, BAR_TEXT, DIM, TICK, GOLD, WHITE = "FF151216", "FFFFFFFF", "FFB8AEC0", "FF6E6475", "FFF2C94C", "FFFFFFFF"
PART_COLORS = ["FF6557A2", "FF8E2A5E", "FFA30F1B", "FF4B3B8C", "FFB0406E", "FF7A1E3A", "FF5A4D97", "FF9C2230", "FF3E2F72"]


def install_player(project_dir):
    """copy the player + the annotation layer next to scene.rml (the CLI scans the project's .luau)"""
    for f in ("ReviewPlayer.luau", "ReviewNotes.luau"):
        shutil.copy(os.path.join(HERE, f), os.path.join(project_dir, f))


def _f(v):
    s = f"{float(v):.3f}".rstrip("0").rstrip(".")
    return s if s not in ("", "-0") else "0"


def _esc(s):
    return s.replace("&", "&amp;").replace("<", "&lt;").replace('"', "&quot;")


def load_notes(project_dir):
    """the notes already taken (<project>/.review/notes.json) as a string that ReviewNotes reads back:
    one note per line, `id|t|text|x,y,x,y,...` (-1,-1 = end of a stroke).
    The folder is hidden on purpose: the viewer rebuilds the scene as soon as a file of the project changes,
    so putting a note at the root would reload the film (back to 0, place lost)."""
    p = os.path.join(project_dir, ".review", "notes.json")
    if not os.path.exists(p):
        p = os.path.join(project_dir, "review_notes.json")     # old location
    if not os.path.exists(p):
        return ""
    try:
        with open(p) as f:
            notes = json.load(f).get("notes", [])
    except (OSError, ValueError):
        return ""
    rows = []
    for n in notes:
        txt = str(n.get("text", "")).replace("|", "\u00a6").replace("\n", " ")
        pts = ",".join("%.1f" % v for v in n.get("pts", []))
        rows.append("%d|%.3f|%s|%s|%d|%d" % (int(n.get("id", 0)), float(n.get("t", 0)), txt, pts,
                                              1 if n.get("done") else 0, int(n.get("color", 4))))
    return "\n".join(rows)


def review_rml(next_id, film_id, film_anim_id, width, height, duration, parts, font_file, accents=(), audio_file=None,
               audio_name="soundtrack", name="Film Review", x=0, y=-3600, title=None, start_paused=False, notes="", fps=60,
               font_id=None, audio_declared=False):
    """returns (artboard_xml, roots_xml) — roots = ViewModel + ScriptAsset + FontAsset (+ AudioAsset).

    font_id / audio_declared: the scene already declares this font (its id is reused) or this audio
    (it is not redeclared) — the case of an install on an existing project, see review_install.py."""
    W, H = width, height
    i = next_id
    # ---- roots
    vm, inst = i(), i()
    names = ["progress", "headX", "timeText", "playOp", "pauseOp", "annOn", "noteText", "noteHint", "seekTo", "selY", "selA", "cmd", "phA", "noteAt", "sendA", "noteCount", "holdPlay", "boxBgH", "boxBgY", "boxAtY"]
    if NOTES:
        for r in range(1, ROWS + 1):
            names += [f"rowT{r}", f"rowX{r}", f"rowA{r}"]        # row text, displayed time, opacity (0 = empty row)
    props = {n: i() for n in names}
    kinds = {"timeText": "String", "noteText": "String", "noteHint": "String"}
    kinds["noteAt"] = "String"
    kinds["noteCount"] = "String"
    for r in range(1, ROWS + 1):
        kinds[f"rowT{r}"] = "String"
        kinds[f"rowX{r}"] = "String"
    vm_xml = [f'<ViewModel defaultInstanceId="{inst}" name="{_esc(name)} VM" id="{vm}">']
    for n, pid in props.items():
        vm_xml.append(f'<ViewModelProperty{kinds.get(n, "Number")} name="{n}" id="{pid}"/>')
    vm_xml.append(f'<ViewModelInstance exports="true" name="Default" id="{inst}">')
    for n, pid in props.items():
        if kinds.get(n) == "String":
            start = "0.0 s" if n == "timeText" else ("type, or click here" if n == "noteHint" else "")
            vm_xml.append(f'<ViewModelInstanceString propertyValue="{_esc(start)}" viewModelPropertyId="{pid}"/>')
        else:
            v = -1 if n == "seekTo" else 0
            vm_xml.append(f'<ViewModelInstanceNumber propertyValue="{v}" viewModelPropertyId="{pid}"/>')
    vm_xml.append("</ViewModelInstance></ViewModel>")
    script, notes_script = i(), i()
    font = font_id or i()
    roots = ["".join(vm_xml), f'<ScriptAsset file="ReviewPlayer.luau" name="ReviewPlayer" id="{script}"/>',
             f'<ScriptAsset file="ReviewNotes.luau" name="ReviewNotes" id="{notes_script}"/>']
    if font_id is None:
        roots.append(f'<FontAsset file="{_esc(font_file)}" name="ReviewFont" id="{font}"/>')
    if audio_file and not audio_declared:
        roots.append(f'<AudioAsset file="{_esc(audio_file)}" name="{_esc(audio_name)}" id="{i()}"/>')

    def bind(prop, key):
        return f'<DataBindContext sourcePathIds="{vm}-{props[prop]}" propertyKey="{key}"/>'

    def rect(w, h, color, cx, cy, r=0, name="Rect", extra="", path_extra=""):
        cr = f' cornerRadiusTL="{_f(r)}"' if r else ""
        return (f'<Shape x="{_f(cx)}" y="{_f(cy)}" name="{_esc(name)}" id="{i()}">{extra}<Rectangle width="{_f(w)}" height="{_f(h)}"{cr} name="R" id="{i()}">{path_extra}</Rectangle>'
                f'<Fill name="F"><SolidColor colorValue="{color}" name="C"/></Fill></Shape>')

    def poly(pts, color, cx, cy, name="Shape", extra=""):
        v = "".join(f'<StraightVertex x="{_f(px)}" y="{_f(py)}"/>' for px, py in pts)
        return (f'<Shape x="{_f(cx)}" y="{_f(cy)}" name="{_esc(name)}" id="{i()}">{extra}<PointsPath isClosed="true" name="P" id="{i()}">{v}</PointsPath>'
                f'<Fill name="F"><SolidColor colorValue="{color}" name="C"/></Fill></Shape>')

    def text(s, size, color, cx, cy, ox=0.5, name=None, run_extra="", wrap_w=None, oy=0.5):
        """wrap_w: a fixed width makes the text wrap (autoWidth never does);
        with oy=1 the box grows UPWARD, which is what a field placed at the bottom of a column needs"""
        sid = i()
        size_attr = (f'sizingValue="autoHeight" width="{_f(wrap_w)}" wrapValue="wrap"'
                     if wrap_w else 'sizingValue="autoWidth"')
        return (f'<Text x="{_f(cx)}" y="{_f(cy)}" {size_attr} alignValue="left" originX="{ox}" originY="{oy}" name="{_esc(name or s[:20])}" id="{i()}">'
                f'<TextStylePaint fontSize="{_f(size)}" fontAssetId="{font}" name="S" id="{sid}"><Fill name="F"><SolidColor colorValue="{color}" name="C"/></Fill></TextStylePaint>'
                f'<TextValueRun styleId="{sid}" text="{_esc(s)}" name="Run" id="{i()}">{run_extra}</TextValueRun></Text>')

    # ---- panel geometry
    # +98 with notes: their own strip under the blocks (34) + the help line (64)
    panel_h = PANEL_H + (98 if NOTES else 0)
    x0, x1 = 40.0, W - 40.0
    bw = x1 - x0
    px = lambda t: x0 + bw * t / duration
    row_y = H + 40
    bar_y, bar_h = H + 124, 44
    # the comments strip: under the blocks, above the ruler marks (otherwise the dots
    # land in the middle of the blocks and eat their labels)
    note_y = bar_y + bar_h / 2 + (30 if NOTES else 0)
    ruler = 34 + (34 if NOTES else 0)
    btn_x, btn_r = x0 + 24, 24
    kids = []
    # play / pause button (the icons follow the view model)
    kids.append(poly([(-7, -11), (11, 0), (-7, 11)], WHITE, btn_x + 2, row_y, "Icon play", bind("playOp", 18)))
    kids.append(f'<Node x="{_f(btn_x)}" y="{_f(row_y)}" name="Icon pause" id="{i()}">{bind("pauseOp", 18)}'
                + rect(6, 20, WHITE, -6, 0, 1, "Bar") + rect(6, 20, WHITE, 6, 0, 1, "Bar") + "</Node>")
    kids.append(rect(btn_r * 2, btn_r * 2, "FF2B2530", btn_x, row_y, btn_r, "Button"))
    kids.append(text(title or f"{name} · click = pause · drag the bar", 17, DIM, btn_x + btn_r + 16, row_y, ox=0))
    kids.append(text("0.0 s", 24, WHITE, x1, row_y, ox=1, name="Timer", run_extra=bind("timeText", 268)))
    # parts
    for k, (t, label) in enumerate(parts):
        t2 = parts[k + 1][0] if k + 1 < len(parts) else duration
        w = px(t2) - px(t) - 2
        fits = len(label) * 8.2 + 12 <= w
        # first child draws on top: the label before its block
        kids.append(text(label, 15 if fits else 13, BAR_TEXT if fits else DIM, px(t) + 1 + w / 2,
                         bar_y if fits else bar_y - bar_h / 2 - 14 - (k % 2) * 17, name=f"Label · {label}"))
        kids.append(rect(w, bar_h, PART_COLORS[k % len(PART_COLORS)], px(t) + 1 + w / 2, bar_y, 6, f"Part · {label}"))
        kids.append(text(f"{t:.1f}", 12, "FF9E93A6", px(t) + 3, bar_y + bar_h / 2 + 12, ox=0, name="Start"))
    for s in range(0, int(duration) + 1):
        hgt = 20 if s % 5 == 0 else 12
        kids.append(rect(1.5, hgt, TICK, px(s), bar_y + bar_h / 2 + ruler + hgt / 2, 0, "Tick"))
        if s % 5 == 0:
            kids.append(text(f"{s}s", 13, DIM, px(s), bar_y + bar_h / 2 + ruler + 34, name="Tick label"))
    for t in accents:
        kids.append(poly([(0, -6), (6, 0), (0, 6), (-6, 0)], GOLD, px(t), bar_y - bar_h / 2 - 9, f"Accent {t:.2f}"))
    # playhead (x bound)
    head = (f'<Node x="{_f(x0)}" y="0" name="Playhead" id="{i()}">{bind("headX", 13)}'
            + rect(3, bar_h + 36, WHITE, 0, bar_y, 1.5, "Line")
            + poly([(-9, -12), (9, -12), (0, 2)], WHITE, 0, bar_y - bar_h / 2 - 18, "Head") + "</Node>")
    panel = rect(W, panel_h, BG, W / 2, H + panel_h / 2, 0, "Panel")
    # the film, driven by `progress`
    film = (f'<NestedArtboard artboardId="{film_id}" x="0" y="0" name="Film" id="{i()}">'
            f'<NestedRemapAnimation animationId="{film_anim_id}" time="0" mix="1" name="Scrub" id="{i()}">{bind("progress", 202)}</NestedRemapAnimation>'
            f'</NestedArtboard>')
    player = (f'<ScriptedDrawable scriptAssetId="{script}" name="Review player" id="{i()}">'
              + "".join(f'<ScriptInputNumber name="{n}" propertyValue="{_f(v)}"/>' for n, v in (
                  ("duration", duration), ("barX0", x0), ("barX1", x1), ("barY0", bar_y - bar_h / 2 - 50),
                  ("barY1", bar_y + bar_h / 2 + 80), ("btnX", btn_x), ("btnY", row_y), ("btnR", btn_r + 6), ("filmBottom", H)))
              + f'<ScriptInputBoolean name="startPaused" propertyValue="{str(start_paused).lower()}"/>'
              + f'<ScriptInputString name="audio" propertyValue="{_esc(audio_name if audio_file else "")}"/>'
              + "</ScriptedDrawable>")
    # the note line and the hint, in the panel under the bar
    kids.append(text("space = play · arrows = step one frame · type to comment · click a time to jump there",
                     14, DIM, x0, H + panel_h - (34 if NOTES else 0) - 34, ox=0, name="Aide"))
    # ---- the comments column (right of the film): header, thread, field + toolbar
    col_x = W
    PAD, SURF, FIELD, MUTED, LINE = 24, "FF1A171E", "FF23202A", "FF8F8698", "FF2E2936"
    TXT_X = col_x + PAD + 14                              # the field's text; the script's cursor aligns to it
    box_h, send_w, send_h = 158, 76, 30
    box_y = H + panel_h - box_h - 24                      # the field, placed at the bottom of the column
    send_x, send_y = col_x + COL_W - PAD - send_w - 12, box_y + box_h - send_h - 16
    tool_y = send_y + send_h / 2                          # the toolbar: 4 colours + the brush
    dot_x, dot_gap, dot_r = TXT_X + 10, 28, 8
    pen_x, pen_y, pen_r = dot_x + 3 * dot_gap + 46, tool_y, 15
    if NOTES:
        # header
        kids.append(text("COMMENTS", 12, MUTED, col_x + PAD, 46, ox=0, name="Col titre"))
        kids.append(text("", 12, MUTED, col_x + COL_W - PAD, 46, ox=1, name="Col compte", run_extra=bind("noteCount", 268)))
        kids.append(rect(COL_W - PAD * 2, 1, LINE, col_x + COL_W / 2, 66, 0, "Col filet"))
        # the thread: a timecode as a pill, the text below; the actions (check, cross) are drawn
        # by the script on the chosen row
        for r in range(1, ROWS + 1):
            ry = ROW_Y0 + (r - 1) * ROW_H
            kids.append(f'<Node x="0" y="0" name="Ligne {r}" id="{i()}">{bind(f"rowA{r}", 18)}'
                        + text("", 12, "FFFF4D6A", col_x + PAD + 10, ry - 11, ox=0, name=f"Temps {r}", run_extra=bind(f"rowX{r}", 268))
                        + text("", 15, WHITE, col_x + PAD, ry + 14, ox=0, name=f"Note {r}", run_extra=bind(f"rowT{r}", 268))
                        + rect(62, 22, "FF2A2430", col_x + PAD + 31, ry - 11, 6, f"Temps fond {r}")
                        + "</Node>")
        # the highlight of the chosen row: AFTER the rows, hence underneath (first child = on top)
        kids.append(f'<Node x="{_f(col_x + PAD - 12)}" y="0" name="Sel" id="{i()}">{bind("selY", 14)}{bind("selA", 18)}'
                    + rect(COL_W - PAD * 2 + 24, ROW_H - 6, "FF252029", (COL_W - PAD * 2 + 24) / 2, 0, 10, "Sel fond") + "</Node>")
        # the comment field
        field_w = COL_W - PAD * 2 - 28
        text_base = box_y + 70                      # the bottom of the text: lines stack upward
        kids.append(f'<Node x="0" y="0" name="Boite temps" id="{i()}">{bind("boxAtY", 14)}'
                    + text("", 12, "FFFF4D6A", TXT_X, box_y + 28, ox=0, name="Temps", run_extra=bind("noteAt", 268))
                    + "</Node>")
        kids.append(text("", 15, WHITE, TXT_X, text_base, ox=0, oy=1, wrap_w=field_w,
                         name="Boite note", run_extra=bind("noteText", 268)))
        kids.append(f'<Node x="0" y="0" name="Boite invite" id="{i()}">{bind("phA", 18)}'
                    + text("Leave a comment…", 15, "FF6E6475", TXT_X, text_base, ox=0, oy=1, wrap_w=field_w,
                           name="Invite") + "</Node>")
        kids.append(text("Send", 13, WHITE, send_x + send_w / 2, send_y + send_h / 2, name="Envoyer"))
        kids.append(f'<Node x="0" y="0" name="Envoyer fond" id="{i()}">{bind("sendA", 18)}'
                    + rect(send_w, send_h, "FFFF4D6A", send_x + send_w / 2, send_y + send_h / 2, 15, "Envoyer pilule") + "</Node>")
        # the help, on its own line above the toolbar (the script's phrases are short)
        kids.append(text("", 11, MUTED, TXT_X, box_y + 92, ox=0, name="Boite aide", run_extra=bind("noteHint", 268)))
        kids.append(rect(COL_W - PAD * 2 - 28, 1, LINE, col_x + COL_W / 2, box_y + 108, 0, "Boite filet 2"))
        # the background follows the text height: the script writes boxBgH / boxBgY (the bottom stays in place)
        kids.append(rect(COL_W - PAD * 2, box_h, FIELD, col_x + COL_W / 2, box_y + box_h / 2, 14, "Boite fond",
                         extra=bind("boxBgY", 14), path_extra=bind("boxBgH", 21)))
        kids.append(rect(COL_W - PAD * 2, 1, LINE, col_x + COL_W / 2, box_y - 18, 0, "Boite filet"))
        kids.append(rect(COL_W, H + panel_h, SURF, col_x + COL_W / 2, (H + panel_h) / 2, 0, "Col fond"))
    host_style = i()
    notes_layer = (f'<LayoutComponent width="{_f(W + (COL_W if NOTES else 0))}" height="{_f(H + panel_h)}" styleId="{host_style}" name="Notes" id="{i()}">'
                   f'<LayoutComponentStyle layoutWidthScaleType="fill" layoutHeightScaleType="fill" widthUnitsValue="auto" heightUnitsValue="auto" name="Style" id="{host_style}"/>'
                   f'<ScriptedLayout scriptAssetId="{notes_script}" name="Review notes" id="{i()}">'
                   + "".join(f'<ScriptInputNumber name="{n}" propertyValue="{_f(v)}"/>' for n, v in (
                       ("duration", duration), ("filmBottom", H), ("barX0", x0), ("barX1", x1),
                       ("barY", note_y), ("penX", pen_x), ("penY", pen_y), ("penR", pen_r),
                       ("colX", W), ("colW", COL_W), ("rowY0", ROW_Y0), ("rowH", ROW_H), ("rows", ROWS), ("fps", fps),
                       ("boxY", box_y), ("boxH", box_h),
                       ("sendX", send_x), ("sendY", send_y), ("sendW", send_w), ("sendH", send_h),
                       ("dotX", dot_x), ("dotGap", dot_gap), ("dotR", dot_r),
                       ("textX", TXT_X), ("textBase", box_y + 70), ("fieldW", COL_W - PAD * 2 - 28)))
                   + f'<ScriptInputString name="saved" propertyValue="{_esc(notes)}"/>'
                   + "<FocusData/></ScriptedLayout></LayoutComponent>")
    sm, lay, st, stt, anim = i(), i(), i(), i(), i()
    body = "\n".join(([notes_layer] if NOTES else []) + [player, head] + kids + [panel, film])
    art = (f'<Artboard x="{x}" y="{y}" width="{W + (COL_W if NOTES else 0)}" height="{H + panel_h}" styleId="{st}" defaultStateMachineId="{sm}" '
           f'viewModelId="{vm}" viewModelInstanceId="{inst}" name="{_esc(name)}" id="{i()}">\n'
           f'<LayoutComponentStyle name="Artboard Style" id="{st}"/>\n<Fill name="Background"><SolidColor colorValue="{BG}" name="C"/></Fill>\n'
           f'{body}\n<LinearAnimation duration="60" loopValue="loop" name="Idle" id="{anim}"/>\n'
           f'<StateMachine name="State Machine 1" id="{sm}"><StateMachineLayer name="Review" id="{lay}">'
           f'<AnyState x="200" y="-120"/><ExitState x="400" y="-120"/>'
           f'<EntryState x="0" y="0"><StateTransition stateToId="{stt}"/></EntryState>'
           f'<AnimationState x="200" y="0" animationId="{anim}" id="{stt}"/></StateMachineLayer></StateMachine>\n</Artboard>')
    return art, "\n".join(roots)
