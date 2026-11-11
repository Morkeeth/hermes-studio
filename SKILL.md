---
name: hermes-studio
description: Use for any video request (edit a video, make a Short, TikTok or Reel, captions, music, trailer, animate, B-roll, generate video). Call studio_doctor first to see what this machine can do, then compose a reviewed local take into an editable portrait cut with animated captions and library music, and inspect the export.
---

# Take to Short

Use `studio_take` for fresh local transcription with an existing model. Hook ranking is a heuristic; check the speech and story. Use `studio_library` on explicit media roots for filename discovery; never infer content approval or music rights from a matching file.

For one-command composition, prepare a source-bound reviewed recipe as documented in README and call `studio_build(recipe, output, render=false)`. It transcribes afresh, selects actual matching shelf music, and saves a real native project preserving reviewed script, crop and word-entry caption timing. No fabricated uniform timings, face tracking, new look approval, semantic discovery or paid generation. The native project template and alignment proof are required inputs, not silently invented outputs.

Use `studio_cut` for lower-level original-source intervals/title/captions and optional fixed crop. Plan first. Under applicable render authorization, dispatch render=true through the existing render gate with a fresh absent output directory. Do not install dependencies, overwrite media, bypass failed checks or launch an encode on authority from this skill alone.

Inspect the actual finished file. `studio_gate` combines aspect/duration/loudness/freezedetect measurements with hash-bound supplied observations for first-screen title, visible captions and no plain zoom. Missing review fails. It is not semantic vision, taste certification or creative approval. Preserve failed versions and repair through a new job.

Optional Eyes and Showreel integrations must run in a compatible real Hermes runtime. Eyes provides measurements, not visual approval. Showreel records actual events; narration off. Label compressed actual-capture montages honestly. Do not claim other scouted animation engines were executed.

Show the actual result on the existing authorized review surface. Story and look remain separate. Do not post or submit the event form under the current brief. `studio_verify_post` may read a human-posted public page when requested; it never publishes or proves authorship.


## Conversation-first connector

When the user is in Telegram/chat, keep them there. The optional editor is for detailed control. First check `studio_request`/`studio_status` are actually registered and the local worker is configured. Do not offer to build a second studio or claim the workstation is connected without a completed job.

For a received video, use its real downloaded attachment path with `studio_request(action="prepare", source=..., request_key="channel:chat:message:prepare")`. Keep that stable key on retries. Use `studio_status(job=...)` for actual results. Use `studio_request(action="library", query=..., request_key=...)` to search the worker's configured library. A filename result is not semantic analysis or license approval.

A prepared project can reuse an existing reviewed preview only when its original-source hash matches a locally configured reviewed project. New footage returns needs_reviewed_cut_recipe. Never present an old export as a new edit. `studio_request(action="edit", parent=..., edit={"music_gain_db": -28}, request_key=...)` currently supports explicit music-level revisions only. It preserves older projects and returns awaiting_render_gate until a new gated export is actually supplied. Other edits need the normal native project workflow; do not silently pretend they were applied.

Delivery is a separate explicitly enabled worker action to a configured allowed chat. queued, completed metadata, awaiting_render_gate and Telegram sent are distinct states. A sent receipt confirms API acceptance, not phone playback or a human reply. No worker polling daemon is installed by this template. Do not run a second Telegram poller.

For a real reply to a delivered preview, use studio_status(reply_to_message_id=<actual replied-to Telegram message ID>) to resolve the parent project. Or pass that actual ID to studio_request with action edit. Never guess an ID. Only a confirmed delivery in the configured owner chat resolves; stale revisions fail instead of overwriting edits.

If the host does not expose the replied-to message ID, pass the actual cached replied-video source to studio_status (or to studio_request action edit). It hashes that real attachment and requires a unique matching confirmed delivery in the owner chat. Never infer a match from filename or a similar-looking clip; ambiguity fails.

## Raw footage to a finished short, in the chat

When the user sends fresh footage and asks for a short, animations, subtitles, sound or a style: call `studio_request(action="alternatives", source=<actual downloaded attachment>, title=<a short hook if the user gave one>, request_key="channel:chat:message:alternatives")`. The worker renders three real style previews (A clean short, B brain rot, C Hermes dither) with word-timed subtitles, audio repair, a kinetic intro and sound design, and sends each one as its own message. Ask: "Short or brain rot? Reply A, B or C."

When the user replies to one of those previews with a letter or a style name, call `studio_request(action="finish", reply_to_message_id=<actual replied-to message ID>, style=<letter or name>, request_key=...)`. The full finished short comes back in that style. A reply with another letter to the finished video re-cuts it from the same source as a new revision. A letter that was not offered fails; say so. These renders are local and immediate; there is no paid model call.

## Generated shots from your own video model

Reply to a finished short with an idea for an opening shot. Hermes calls `studio_request(action="generate", reply_to_message_id=<the finished video>, query=<the shot prompt>, title=<model name from models.json>, request_key=...)`. The worker replies with a quote: model, cost (or UNKNOWN), spent this month and the cap. Nothing is spent yet. Only when the creator types YES as a reply to that quote: `studio_request(action="approve", reply_to_message_id=<the quote message>, query="YES", request_key=...)`. Never type YES on the creator's behalf. The generated shot is put at the start of the short as a new version and sent back.

## Several changes in one reply

When the creator replies to a finished short with direction like "keep C, use A's captions, tighten the opening, quieter music", call `studio_request(action="revise", reply_to_message_id=<that video>, edit={"style": "C", "captions_from": "A", "trim_start": 1.5, "punchier": true, "quieter_music": true}, request_key=...)`. Combine any of: style, captions_from, trim_start (seconds), punchier, title, quieter_music. It renders a new version and keeps the old one. The reply lists what changed and what could not be done (for example, music mixed into the speech track cannot be lowered on its own). Never claim a change the reply does not list.
