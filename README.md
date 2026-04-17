# Living Memory Map

`Living Memory Map` is a visual RAM observability tool for Game Boy and Game Boy Color games built on top of `PyBoy`.

It started as a simple memory watcher and turned into something closer to:

> BGB hexdump, but alive and optimized for human pattern recognition.

The goal is not full automation. The goal is to make memory readable enough that a human can play, observe, hypothesize, and discover game state quickly.

## What It Does

- Renders a fixed hexdump-style view of `WRAM` and `HRAM`
- Highlights bytes that change in real time with fading visual trails
- Embeds a live game preview directly in the same UI
- Uses Xbox controller input for gameplay and keeps the keyboard for tool navigation
- Lets you freeze emulator advancement without using the game's own pause
- Saves timestamped snapshots of:
  - the current UI as a PNG
  - the full RAM dump as JSON
- Supports direct byte editing from the UI for fast testing
- Shows controller-triggered before/after diffs in a dedicated side panel

This makes it much easier to answer questions like:

- What lit up when I jumped?
- Which bytes changed when I took damage?
- Which addresses persist after an event versus just flicker?
- Which values look like timers, health, lives, or position?

## Why This Exists

The original workflow that worked best was:

1. Play the game
2. Watch a stable hexdump
3. Notice where bytes changed
4. Correlate gameplay actions with memory regions

That loop is surprisingly powerful, but traditional tools make it hard to keep spatial awareness while also playing.

This project tries to preserve that manual intuition while making memory feel:

- alive
- localized
- stream-friendly
- easier to inspect mid-play

## Core Scripts

### `memory_watcher.py`

Main interactive tool.

Features:

- live RAM grid
- embedded emulator view
- controller-driven gameplay
- freeze/resume emulator ticking
- snapshots
- byte editing
- action-triggered diff summaries
- mute/unmute audio

### `analyze_snapshots.py`

Offline analysis tool for saved snapshot JSON files.

It can:

- rank timer-like addresses
- rank counter-like addresses
- rank bursty/event-like addresses
- analyze recent pairwise diffs

### `sniper.py`

A small value-search experiment used to narrow down specific game state from snapshots.

This is the script that helped identify health in Super Mario Bros. Deluxe by comparing a `before` value and `after` value across two saved RAM states.

It is intentionally rough, but it captures an important idea:

- selective, event-driven narrowing often beats constant scanning

## Requirements

Python 3.10+ is recommended.

Main runtime dependencies:

- `pyboy`
- `numpy`
- `pillow`

Optional:

- `sounddevice` for embedded audio playback
- `python-dotenv` if you use optional local tooling around the project

## Running The Tool

Use the project virtual environment:

```powershell
.\.venv\Scripts\python.exe memory_watcher.py .\games\smb.gbc
```

You can also point it at any other supported ROM:

```powershell
.\.venv\Scripts\python.exe memory_watcher.py .\games\SpaceInvaders.gb
```

## Controls

### Gameplay

Gameplay is driven by an Xbox controller through Windows `XInput`.

- D-pad / left stick: move
- `A`, `B`, `Start`, `Back`: sent to the emulator

Keyboard is intentionally reserved for the tool UI.

### Tool UI

- `Q` / `E`: previous / next memory page
- `R`: switch memory region (`WRAM` / `HRAM`)
- `M`: mark an event
- `S`: save snapshot
- `P`: freeze / resume emulator ticking
- `Esc`: quit

UI buttons:

- mute / unmute audio
- pop out / dock game preview
- adjust playback speed
- apply memory edits to the selected byte

## Snapshots

Press `S` in the UI to save:

- a PNG screenshot of the current tool window
- a JSON file containing RAM state and metadata

Snapshots are saved into:

```text
snapshots/
```

Each snapshot JSON includes:

- tick count
- current region and page
- selected address
- controller status
- `WRAM`
- `HRAM`

This makes it easy to capture:

- before / after jump
- before / after damage
- before / after item pickup
- idle / movement / combat comparisons

## Offline Analysis

Generate a structured summary from saved snapshots:

```powershell
.\.venv\Scripts\python.exe analyze_snapshots.py --last-pairs 2
```

## Design Philosophy

This project is not trying to replace reverse engineering.

It is trying to improve the loop:

> act -> watch -> notice -> test -> understand

The emphasis is on:

- spatial memory
- temporal memory
- readable motion
- fast hypothesis testing
- keeping the human in the loop

## Current Strengths

- very good for finding candidate gameplay state by observation
- useful for stream/demo scenarios because memory changes are visual
- fast enough to do controller-triggered selective diffs
- snapshot workflow is strong for before/after comparisons
- direct byte editing makes hypothesis testing much faster

## Current Limitations

- Windows-first right now because controller input uses `ctypes` + `XInput`
- audio is more fragile than the rest of the tool because it shares timing pressure with the UI
- some helper scripts are still experimental
- the tool is designed around `WRAM` / `HRAM`, not full-system debugging

## Roadmap Ideas

- clustered action-diff view instead of only top changed bytes
- explicit persistent vs transient change labeling
- region pinning / zoom mode
- compare-two-snapshots mode inside the main UI
- event labeling for snapshots
- stronger classification of:
  - player state
  - timers
  - score
  - flags
  - transient engine buffers

## Credits

Built on:

- [PyBoy](https://github.com/Baekalfen/PyBoy)

And heavily inspired by the manual workflow of watching live memory in tools like BGB, then asking:

> what just changed, and why?
