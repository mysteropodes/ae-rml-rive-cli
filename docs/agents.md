# Setting up a coding agent

Two layers help a coding agent (Claude Code, Codex, Cursor, Gemini, and others) work with these tools: the Rive CLI documents itself to the agent, and this repository adds skills for the After Effects side. No MCP server is needed: everything goes through the command line (`ae ...`) and files (RML, report, log).

## 1. The Rive CLI project

`rive create <folder>` writes `AGENTS.md` and `CLAUDE.md` into the project. They tell the agent to use `rive docs`, `rive schema`, `rive . --verify`, `rive inspect` and `--screenshot`, and never to guess a type or a property. Keep these files.

For ambitious scenes (films, games) it helps to add to the project, or to the agent's memory, your own notes on the Rive CLI's pitfalls (keys, bones, layouts, Luau, WGSL, publishing) and on your method from storyboard to RML to render.

## 2. Claude Code (recommended)

The skills live in `skills/` at the repository root:

| Skill | Use |
|---|---|
| `rml2ae` | Rive CLI project to After Effects with `ae`: build, incremental update, pull, render, compare. |
| `ae2rml` | After Effects project (`.aep`) to Rive CLI project, and checking how close it is. |
| `rive-review-kit` | Frame.io-like review notes inside the Rive CLI viewer. |

Install them with the repository installer:

```bash
./install.sh skills            # copies skills/* into ~/.claude/skills
```

or copy a folder by hand (`cp -R skills/rml2ae ~/.claude/skills/`). Claude then loads the matching skill whenever After Effects, `.aep` or review notes come up, and knows the commands, the rules and what a conversion produces.

In the project, add to its `CLAUDE.md` (created by `rive create`):

```markdown
## After Effects
This project converts to After Effects with `ae` (rml2ae, skill `rml2ae`). Never run `ae build`, `ae watch`
or `ae pull` without asking me: they act on the After Effects I have open. After a build, read
build/rml2ae/<name>.ae-report.md and tell me what is approximated.
```

The panel button in After Effects (Window › Rive.jsx) stays the way to update AE **when you decide to**: the agent writes the RML, you look at it in the Rive viewer, you click.

## 3. Other agents

Put the content of the skill in the agent's instruction file: paste `skills/rml2ae/SKILL.md` (without the `---` header) into the project's `AGENTS.md` or into the tool's global rules. Do the same for the other skills you need.

## 4. What not to give the agent

- **No direct access to After Effects** (an AE MCP server, ad hoc scripts). Everything AE has to do goes through `ae`, which is deterministic and logged (`build/rml2ae/<name>.ae.log`). An agent that tinkers with AE directly breaks the `rive:<id>` tags that incremental update and pull rely on.
- **No hand-written `.riv` or `.aep`.** They are produced by the Rive CLI and by After Effects.

## Rules the skills enforce

- Never run `ae build`, `ae watch` or `ae pull` without the user's explicit go; they act on the open After Effects.
- Run `ae doctor <project>` before any AE work; every line should be `[ok]`.
- After a build, read the report and tell the user what was approximated, instead of claiming pixel parity.
- The RML is the source of truth. AE edits survive only on layers rml2ae did not rebuild; `ae pull --dry` then `ae pull` brings transforms and keys back into the RML.
