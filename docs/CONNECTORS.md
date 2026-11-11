# Connectors

One shared timeline, every tool. Editors, motion engines and models all connect through one OpenTimelineIO timeline, so adding a tool means writing one adapter. `studio_connectors` returns this table live.

| Group | Connector | Status |
|---|---|---|
| Editor | FFmpeg | Working: local render of the shared timeline |
| Editor | OpenTimelineIO | Working: export and import; tests read it back with the real `opentimelineio` library |
| Editor | Final Cut Pro | Export: FCPXML 1.10 |
| Editor | DaVinci Resolve | Export: imports the same FCPXML or OTIO; scripting API next |
| Editor | Premiere Pro | Next |
| Editor | hermes-video-editor | Working: `studio_cut` |
| Motion | Lottie | Guidance |
| Motion | Motion Canvas, Revideo, Manim | Next |
| Generate | Higgsfield | Dry run: plans a request, never sends, never spends |
| Audio | whisper.cpp | Working: `studio_take` |
| Review | Eyes | Working: `studio_gate` |

## Tools

- `studio_connectors`: list connectors and their real status.
- `studio_timeline`: write a timeline as OTIO and FCPXML, optionally render it with FFmpeg and run the gate.
- `studio_generate`: plan a generation request. Dry run only.

## Tests

`python3 -m unittest test_connectors` includes an OTIO round trip that fails if any cut is dropped, and a test proving that check can go red.
