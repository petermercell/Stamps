# Stamps C++

A native NDK port of [Adrian Pueyo & Alexey Kuchinski's Stamps](https://github.com/adrianpueyo/Stamps) for Nuke 17.

## Why this exists

Some Nuke TDs have asked for native performance at scale. This port keeps the same workflow and UI but moves the hot paths into C++ so the toolset behaves the way a production pipeline expects: zero pixel-processing overhead, native `knob_changed` filtering, and proper deep-data pass-through.

Original concept and design: Adrian Pueyo and Alexey Kuchinski (BSD-2-Clause).
C++ port: Peter Mercell.

## What's different

**2D stamps — `StampAnchor` / `StampWired` (native `Iop`)**
Pure pass-through via `copy_info()` + `input(0)->get()`. Nuke's runtime recognises output == input, so tiles stay on device in the GPU pipeline. The original uses `PostageStamp`, which is fine for compositing but adds a thumbnail draw and a cache cycle even when it's just passing pixels through.

**Deep stamps — `StampDeepAnchor` / `StampDeepWired` (native `DeepFilterOp`)**
This is the largest performance gap. The original implements deep stamps as `DeepExpression` nodes that evaluate a TCL expression for each sample. Yours are native `DeepFilterOp` subclasses that hand samples through directly with `getUnorderedSample` → `push_back`. No expression parsing, no per-sample TCL eval. On heavy deep volumes the difference is visible, not a microbenchmark.

**3D / Camera / Axis / Particle streams (`NoOp` fallback)**
Same approach as the original — `NoOp` is the only universal pass-through for non-2D non-deep data in Nuke. No advantage either way; included for parity.

**Native `knob_changed` fast path**
Every time you click, drag, or change anything on a node, Nuke fires a `knob_changed` event. In the original Python Stamps, those events land in Python — which means crossing into Python's interpreter and acquiring something called the GIL (Global Interpreter Lock). The GIL is a mutex inside CPython that only lets one thread run Python code at a time, even on a 16-core machine. Useful work like file I/O releases it, but every Python callback still has to acquire and release it. The acquisition is fast in isolation, but it serializes against every other thread doing Python work in the same process — and Nuke has many.

So in Adrian's version, dragging one stamp around the DAG fires a Python callback for every `xpos` / `ypos` tick, each one taking the GIL, building a Python frame, running a name check, tearing it down, releasing the GIL. One stamp is fine. A script with two hundred stamps during a multi-select drag is not.

In this port, `knob_changed` is a C++ method. The early-returns for `xpos` / `ypos` / `selected` happen entirely in C++ — no GIL, no Python frame, no overhead. Only operations that genuinely need Python (anchor lookup, dialogs, reconnection) dispatch back via `script_command`. The result is that interactive work — dragging selections, panning the DAG, expanding groups — stays smooth as the script grows.

**Nuke 17 native API compliance**
- `std::string*` String_knobs (Nuke 17 dropped the `char[]` form)
- 2-arg `Op::Description` (3-arg form deprecated)
- 2-arg `Tab_knob` (3-arg form deprecated)

**Robust copy-paste auto-reconnect**
`addOnCreate` callback deferred via `QTimer.singleShot(0)` so the reconnect runs after Nuke's paste/script-load tcl stack fully unwinds. Catches all three flavours: `StampWired`, `StampDeepWired`, and `NoOp` legacy/3D wireds.

## What's the same

The user-facing feature set, panel layout, hotkey (F8), tags, anchor selector, reconnect-by-title / reconnect-by-selection flows, and the legacy detection for scripts authored with the original Python Stamps. Existing `.nk` files with Adrian's stamps load fine and can be converted in-place via *Stamps → Convert Legacy Stamps*.

## Trade-offs (be honest)

- The original is pure Python, which means zero compile, zero per-platform build, no ABI exposure to Nuke point releases. This port is a `.so` and needs a rebuild for each Nuke major version.
- Adrian's version has years of studio deployment behind it. This port is newer; edge cases around group/gizmo nesting, cross-script paste, and undo will surface as users hit them. Bug reports welcome.

## Credits

Original Stamps: Adrian Pueyo & Alexey Kuchinski — design, UX, and the entire feature set this port reproduces. All credit for the toolset itself goes to them. This port exists to close a performance gap, not to replace their work.

## License

BSD-2-Clause, matching the original.
