# Review kit for Rive CLI — install

**[review_kit_demo.mp4](review_kit_demo.mp4)** — 63 s, the whole thing at work on a real film
(the *a game* trailer).

Frame.io-style review inside the Rive CLI viewer: scrub a film, **type timed comments**, **draw on
the frame**, and read everything back from the command line — as JSON your agent (Claude Code,
Codex, anything that runs a shell) can act on.

Works on any Rive CLI project with a `scene.rml` and an animated artboard. Nothing is hosted, nothing
leaves your machine.

## Requirements

- Rive CLI (`rive`) — tested on 1.2.0
- Python 3.9+ (standard library only)
- A `.ttf`/`.otf` in the project, or any font file you can point at: the panel needs one

## Install

Copy this folder next to your project (or anywhere — you call it by path), then:

```bash
python3 review/review_install.py path/to/project
```

That's it. It reads your `scene.rml`, picks the artboard to review and its longest animation, and
adds a `<Film> Review` artboard between two markers, plus the two Luau scripts the project needs.

```bash
# pick a specific film, name the bar blocks, use a given font
python3 review/review_install.py path/to/project \
    --artboard="My film" --parts "0:Intro,7.1:Chorus,20.2:Outro" --font=fonts/Inter-Medium.ttf
```

| Option | |
|---|---|
| `--artboard=NAME` | which film (default: the first exported artboard) |
| `--animation=NAME` | which animation (default: the longest one in that artboard) |
| `--parts "0:Intro,7.1:Chorus"` | the coloured blocks on the bar (default: one block) |
| `--font=PATH` `--audio=PATH` | override what was found in the scene |
| `--name="Review"` | name of the review artboard |
| `--refresh` | re-install with the saved settings — **reloads the notes into the thread** |
| `--remove` | take the review artboard and scripts back out |

Everything it writes: the marked block in `scene.rml`, `ReviewPlayer.luau`, `ReviewNotes.luau`, and
a `.review/` folder (notes, log, settings). `--remove` gives you the project back as it was.

## Review

```bash
python3 review/review_notes.py open path/to/project "My film Review"
```

The viewer opens; notes land in `path/to/project/.review/notes.json` as you post them, and are
echoed in the terminal.

| In the viewer | |
|---|---|
| **type**, or click the comment box | opens a comment on the frame you are watching, and pauses |
| **Enter**, or **Send** | posts it · **Esc** cancels · **⌫** deletes a letter, then the last stroke |
| one of the **4 colours**, or the **brush** | takes the brush — draw on the frame in that colour |
| **space** · **← →** | play/pause · step one frame (when not typing) |
| **click the bar** · **drag** | jump there · scrub |
| **click a dot** under the bar | stop on that note and select it |
| **click a row** | stop on its frame (its drawing reappears) · **click again** to edit it |
| **✓ / ✕** on the selected row | mark done · delete |

A drawing only shows at **its own** frame — that is the point of a timed note.

## Read the notes back

```bash
python3 review/review_notes.py list path/to/project              # all of them
python3 review/review_notes.py list path/to/project --new        # only what arrived since last time
python3 review/review_notes.py list path/to/project --format=md  # ready to paste
```

`.review/notes.json` holds `{id, t (seconds), text, color, pts, box, done, at}`. `pts` is a flat
`x1,y1,x2,y2,…` with `-1,-1` between strokes, in the review artboard's coordinates — the film sits
at scale 1 from (0,0), so a point you drew is the same point in the film.

## With an agent (Claude Code, Codex…)

The agent starts the session, you annotate, the agent reads the notes while you go:

```bash
python3 review/review_notes.py open path/to/project "My film Review" --background
python3 review/review_notes.py status path/to/project     # running? how many notes?
python3 review/review_notes.py list path/to/project --new # what arrived since the last read
python3 review/review_notes.py stop path/to/project
```

`--background` returns immediately (the viewer keeps running, output in `.review/session.out`), so
an agent can poll `list --new` between edits. Ask it to fix what a note says, then `--refresh` to
bring the updated thread back into the scene.

## Two things that will bite you

- **The viewer rebuilds the scene whenever a file in the project folder changes** — and playback
  jumps back to 0. That is why notes live in `.review/` (a hidden folder is not watched). For the
  same reason, don't regenerate `scene.rml` during a review pass.
- **Notes are baked into the scene at install time.** They keep coming into the JSON while you work,
  but the thread in the viewer only picks up older notes when you run `--refresh`.

## Limits

- One brush (four colours). No arrow, no box, no replies, no avatars — a single reviewer's voice.
- The comment column is 380 px wide, the thread shows 10 rows and scrolls with the playhead.
- Keyboard reaches the layer through `<FocusData/>`; synthetic `--key` in headless runs does not
  produce text input (that is a CLI limitation, not a bug in the kit).
