---
name: hermes-studio-bridge
description: Use the existing configured Hermes Studio workstation from the current conversation; keep the editor optional.
---
# Studio from this conversation

This is the gateway companion, not a complete editor. It exposes studio_request and studio_status; the separately configured Mac/local Studio worker owns media and editing. Do not propose building a second Studio when the worker already exists. Do not claim the full plugin is installed on this gateway.

When the user asks about video capabilities, explicitly load this skill, then verify a library job completes before saying the workstation is reachable. Submit studio_request with action library, a filename query, and a stable request_key derived from the actual channel/chat/message plus operation. Keep that key on retries. Read studio_status with the returned job ID. queued means waiting for an actual worker pass, not success.

For an attachment use action prepare, source set to its genuine gateway-downloaded video path, and the stable message-based key. Only configured inbound roots are allowed. A raw-source hash match may reuse an explicitly configured reviewed project/preview; it is never a newly rendered edit. Unmatched footage returns needs_reviewed_cut_recipe. Explain that boundary honestly.

To revise a known project use action edit and parent set to its completed prepare/edit job. The current tested edit is edit={"music_gain_db": -28}. It writes a real native project successor and invalidates the old preview. awaiting_render_gate means no updated video exists yet; do not send an old video as the revision. Other edits use the full workstation workflow.

Preview delivery is a separate authorized worker action; no Telegram token or API key goes to the worker/model. Do not run another Telegram poller. A sent receipt confirms Telegram acceptance, not playback on a phone. One reply edit needs an actual user reply and matching parent, not a simulated conversation.

Never render outside the existing render gate, call paid providers, publish publicly, invent review/rights, or claim every catalog animation tool is installed. The shared native project is the truth; the editor is optional for detailed human control.

For a real reply to a delivered preview, use studio_status(reply_to_message_id=<actual replied-to Telegram message ID>) to resolve the parent project. Or pass that actual ID to studio_request with action edit. Never guess an ID. Only a confirmed delivery in the configured owner chat resolves; stale revisions fail instead of overwriting edits.

If the host does not expose the replied-to message ID, pass the actual cached replied-video source to studio_status (or to studio_request action edit). It hashes that real attachment and requires a unique matching confirmed delivery in the owner chat. Never infer a match from filename or a similar-looking clip; ambiguity fails.

## Raw footage to a finished short, in the chat

When the user sends fresh footage and asks for a short, animations, subtitles, sound or a style: call `studio_request(action="alternatives", source=<actual downloaded attachment>, title=<a short hook if the user gave one>, request_key="channel:chat:message:alternatives")`. The worker renders three real style previews (A clean short, B brain rot, C Hermes dither) with word-timed subtitles, audio repair, a kinetic intro and sound design, and sends each one as its own message. Ask: "Short or brain rot? Reply A, B or C."

When the user replies to one of those previews with a letter or a style name, call `studio_request(action="finish", reply_to_message_id=<actual replied-to message ID>, style=<letter or name>, request_key=...)`. The full finished short comes back in that style. A reply with another letter to the finished video re-cuts it from the same source as a new revision. A letter that was not offered fails; say so. These renders are local and immediate; there is no paid model call.

## Generated shots from your own video model

Reply to a finished short with an idea for an opening shot. Hermes calls `studio_request(action="generate", reply_to_message_id=<the finished video>, query=<the shot prompt>, title=<model name from models.json>, request_key=...)`. The worker replies with a quote: model, cost (or UNKNOWN), spent this month and the cap. Nothing is spent yet. Only when the creator types YES as a reply to that quote: `studio_request(action="approve", reply_to_message_id=<the quote message>, query="YES", request_key=...)`. Never type YES on the creator's behalf. The generated shot is put at the start of the short as a new version and sent back.
