# Review kit

Frame.io-style review inside the Rive CLI viewer. You scrub a film, type timed comments, draw on the frame, and
read everything back from the command line as JSON, so that an agent (Claude Code, Codex, anything that runs a
shell) can act on the notes. Nothing is hosted and nothing leaves your machine.

It works on any Rive CLI project that has a `scene.rml` and an animated artboard. The kit lives in `review-kit/`.

Contents: [Requirements](#requirements) · [Install into a project](#install-into-a-project) ·
[Review](#review) · [Read the notes back](#read-the-notes-back) · [Working with an agent](#working-with-an-agent) ·
[Files](#files) · [Contract with the film](#contract-with-the-film) · [Testing without a window](#testing-without-a-window) ·
[Gotchas](#gotchas) · [Limits](#limits)

## What you get

A review artboard wrapped around the film:

- the film looping at the top;
- a 240 px bar below it with the **named parts**, **ticks** (every second, labelled every 5 s) and **music accents**
  (gold diamonds);
- a **playhead** you click or drag, a play/pause button (clicking the picture also pauses), a time counter, and the
  **soundtrack** re-synchronised after every jump;
- a 380 px comment column on the right: header with the note count, the thread, and a permanent comment box with
  four colours, a brush and a Send button.

## Requirements

- Rive CLI (`rive`), tested on 1.2.0 to 1.4.0
- Python 3.9+ (standard library only)
- A `.ttf` / `.otf` font in the project, or any font file you can point to: the panel needs one

## Install into a project

Copy the `review-kit/` folder next to your project (or anywhere; you call it by path), then:

```bash
python3 review-kit/review_install.py path/to/project
```

The installer reads `scene.rml`, picks the artboard to review and its longest animation, and adds a
`<Film> Review` artboard between two markers, plus the two Luau scripts the project needs.

```bash
# pick a film, name the bar blocks, use a given font
python3 review-kit/review_install.py path/to/project \
    --artboard="My film" --parts "0:Intro,7.1:Chorus,20.2:Outro" --font=fonts/Inter-Medium.ttf
```

| Option | Meaning |
|---|---|
| `--artboard=NAME` | Which film to review (default: the first exported artboard) |
| `--animation=NAME` | Which animation (default: the longest one of that artboard) |
| `--parts "0:Intro,7.1:Chorus"` | The coloured blocks on the bar, as `seconds:label` (default: one block) |
| `--font=PATH`, `--audio=PATH` | Override what was found in the scene |
| `--name="Review"` | Name of the review artboard |
| `--refresh` | Re-install with the saved settings and reload the notes into the thread |
| `--remove` | Take the review artboard and scripts back out; the project returns to its previous state |

It writes: the marked block in `scene.rml`, `ReviewPlayer.luau`, `ReviewNotes.luau`, and a hidden `.review/` folder
(notes, log, settings).

## Review

```bash
python3 review-kit/review_notes.py open path/to/project "My film Review"
# or directly:
rive path/to/project --artboard="My film Review" --fit=contain
```

The viewer opens. Notes are saved to `path/to/project/.review/notes.json` as you post them and echoed in the
terminal. The viewer interface is in English.

| In the viewer | Effect |
|---|---|
| **type**, or click the comment box | Opens a comment on the frame you are watching and pauses |
| **Enter** or **Send** | Posts the note |
| **Esc** | Cancels |
| **Backspace** | Deletes a letter, then the last stroke |
| one of the **4 colours**, or the **brush** | Takes the brush (and opens a comment): draw on the frame in that colour |
| **Space** | Play / pause |
| **Left / Right** | Step one frame (when not typing) |
| **click the bar**, **drag** | Jump there, scrub |
| **click a dot** in the comment strip under the blocks | Stop on that note and select it in the thread |
| **click a row** in the thread | Stop on its frame (its drawing reappears); click again to edit |
| **check / cross** on the selected row | Mark as done / delete |
| click anywhere else (picture, bar, panel) | Leaves typing; what you wrote or drew is posted, an empty draft is discarded |

A drawing is shown only near **its own** frame (within 0.25 s), like an annotation layer in a review tool. Going to
a note pauses on its frame so the drawing stays visible.

The thread lists notes by timecode, follows the playhead (the current note is highlighted and the thread scrolls),
shows a small stroke in the note's colour when it has a drawing, and greys out notes marked done. The comment text
wraps and the box grows upward as you type.

## Read the notes back

```bash
python3 review-kit/review_notes.py list path/to/project                 # all notes
python3 review-kit/review_notes.py list path/to/project --new           # only what arrived since the last read
python3 review-kit/review_notes.py list path/to/project --format=md     # ready to paste
```

`.review/notes.json` holds objects of the form `{id, t, text, color, pts, box, done, at}`:

| Field | Meaning |
|---|---|
| `id` | Note number |
| `t` | Time in seconds |
| `text` | The comment |
| `color` | Brush colour, 1-4 |
| `pts` | Flat list `x1,y1,x2,y2,...` with `-1,-1` between strokes, in the review artboard's coordinates. The film sits at scale 1 from (0,0), so a drawn point is the same point in the film |
| `box` | Bounding box of the drawing |
| `done` | Marked as processed |
| `at` | Wall-clock time of the note |

Nothing is lost: each event is appended to `.review/notes.log` first, then the JSON is rewritten atomically. If the
JSON disappears, `review_notes.py rebuild path/to/project` reconstructs it from the log. Ids restart at 1 in each
viewer launch: the same id **and** the same time means a correction, the same id at another time becomes a new,
renumbered note.

## Working with an agent

The agent starts the session, you annotate, the agent reads the notes while you go:

```bash
python3 review-kit/review_notes.py open path/to/project "My film Review" --background
python3 review-kit/review_notes.py status path/to/project       # running? how many notes?
python3 review-kit/review_notes.py list path/to/project --new   # what arrived since the last read
python3 review-kit/review_notes.py stop path/to/project
```

`--background` returns immediately; the viewer keeps running and its output goes to `.review/session.out`. An agent
can poll `list --new` between edits, fix what a note says, then run `review_install.py ... --refresh` to bring the
updated thread back into the scene. To follow a viewer you started yourself, pipe its output into
`review_notes.py watch path/to/project`.

## Files

| File | Role |
|---|---|
| `ReviewPlayer.luau` | The player (scripted drawable, `Node` protocol): clock, pause, scrub, audio; writes the ViewModel |
| `ReviewNotes.luau` | The annotation layer (scripted layout with focus data): drawing, typing, markers, `#ANN` output |
| `review_timeline.py` | `review_rml(...)` returns `(artboard_xml, roots_xml)` to paste into a scene; `install_player(project)` copies the two scripts (`REVIEW_NOTES=0` removes the annotation layer) |
| `review_install.py` | One-command install, refresh and removal on a project that was not prepared for review |
| `review_notes.py` | CLI side: `open`, `watch`, `list`, `status`, `stop`, `rebuild` |

If you generate your scene from a script, call `review_rml(..., notes=load_notes(PROJECT))` so existing notes are
loaded into the thread.

### What the player reads and writes

- Inputs (script inputs, by name): `duration` (s), `barX0`/`barX1` (bar edges), `barY0`/`barY1` (bar click area),
  `btnX`/`btnY`/`btnR` (button), `filmBottom` (a click above it pauses), `startPaused`, `audio` (name of the audio
  asset; empty = silent). `review_rml` fills them.
- ViewModel `<Name> VM`: `progress` (drives the film's remap), `headX` (playhead x), `timeText` (counter), `playOp` and
  `pauseOp` (icon opacities).
- Pointer: the script receives all the artboard's pointer events and calls `event:hit()` on those it handles.
- Audio: `context:audio(name)` and `Audio.play`; pause and resume, seek on scrub release, restart on each loop.
  `Audio.play` returns nil in headless runs (`--screenshot`, `--data-dump`); everything else works, silently.
- The annotation layer prints `#ANN {...}`, `#ANNDEL id` and `#ANNRES id 0|1` to the viewer's standard output (the
  only channel a script has); `review_notes.py` reads them. It talks to the player through the ViewModel (`annOn`,
  `seekTo`, `cmd`). Keyboard events only reach a scripted layout that has `<FocusData/>`, which is why annotation is
  a separate layer.

## Contract with the film

The review wraps any film, but the film must follow these rules:

1. **The film is a function of its own main timeline.** The review nests the film with a `NestedRemapAnimation`
   whose `time` (a 0..1 fraction) is bound to `progress`; the script writes it every frame. Everything that must
   follow scrubbing is keyed in that timeline, or nested through a `NestedRemapAnimation` keyed **linearly** in it
   (acts, shots).
2. **No `NestedStateMachine` for acts.** A nested state machine runs on real time and follows neither pause nor
   scrub. Looping ambience characters may stay as nested state machines; they keep moving while paused.
3. **A nested remap is applied one frame late (measured).** An act remapped to time `f/DUR` shows frame `f-1`. Key
   `(0, 1/DUR)` to `(DUR-2, (DUR-1)/DUR)` to make the film identical to a state-machine version.
4. All timelines (film and acts) have the same duration and `loopValue="loop"`, so the film played alone also loops
   in phase.

## Testing without a window

```bash
F='progress,timeText,headX,playOp,pauseOp'
rive . --artboard="X Review" --data-dump=- --data-dump-filter=$F --advance=5s                         # about 5.0 s
rive . --artboard="X Review" --data-dump=- --data-dump-filter=$F --advance=1s \
       --pointer=click@376,1584 --advance=0.5s                                                         # jump to 30 %
rive . --artboard="X Review" --data-dump=- --data-dump-filter=$F --advance=1s \
       --pointer=click@64,1500 --advance=2s                                                            # pause: time stops
rive . --artboard="X Review" --data-dump=- --data-dump-filter=$F --advance=1s \
       '--pointer=drag@200,1584>800,1584:12'                                                           # drag
```

Coordinates are for a 1200 x 1460 film: the bar is at `y = H + 124`, the button at `(64, H + 40)`.

Drawing and posting in a headless run:

```bash
rive . --artboard="X Review" --screenshot=/tmp/x.png --viewport=1580x1764 --fit=none \
  --advance=1s --pointer=click@1010,1500 --advance=0.1s '--pointer=drag@420,620>820,760:10' \
  --advance=0.1s --key=enter:press --advance=0.3s | python3 review-kit/review_notes.py watch .
```

Synthetic `--key` events do not produce text input headlessly (a CLI limitation, not a kit bug).

## Gotchas

- **The viewer rebuilds the scene whenever a file in the project folder changes, and playback jumps back to 0.**
  That is why notes live in `.review/` (a hidden folder is not watched). Do not regenerate `scene.rml` during a
  review pass.
- **Notes are baked into the scene at install time.** New notes keep arriving in the JSON, but the thread in the
  viewer only picks up older ones when you run `--refresh`.

Design notes, useful if you modify the scripts:

- A text cursor drawn at an estimated position always ends up misplaced, since a script has no font metrics. The
  cursor is a `|` appended to the text itself on alternate frames, so the text engine places it.
- `autoWidth` text never wraps. Use a fixed width with `wrapValue="wrap"`, and anchor the box at the bottom
  (`originY="1"`) so it grows upward, with the background following through bound height and position.
- Two scripts sharing the pointer stream: the one handing over must do so on **all** events, `pointerUp` and
  `pointerExit` included. Otherwise a release can restart the film and the music and the note seems to vanish.
  Likewise never hand the pointer back in the middle of a click: the annotation layer runs before the player, so
  releasing its lock during `pointerDown` makes the player see that same click as an ordinary click on the picture.
  The release is deferred to `pointerUp`.
- A paused audio track is not a stopped one. During annotation the player calls `stop()`, not `pause()`, so a
  `play()` triggered elsewhere cannot stack a second track. A jump (`seekTo`) stops and restarts at the right place.
- A click in the column's empty space below the thread must leave typing like any click elsewhere.

## Limits

- One brush with four colours: no arrow, no box, no replies, no avatars; a single reviewer's voice.
- The comment column is 380 px wide; the thread shows 10 rows and scrolls with the playhead.
- The keyboard reaches the layer through `<FocusData/>` only; a scripted drawable receives no key events.
