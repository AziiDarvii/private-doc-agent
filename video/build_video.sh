#!/usr/bin/env bash
# Builds demo.mp4 from the screenshots in frames/ plus narration from macOS `say`.
set -euo pipefail
cd "$(dirname "$0")"

VOICE="${VOICE:-Samantha}"
RATE="${RATE:-178}"
PAD_SECONDS=0.8

# frame | narration (in the order the video plays)
SCENES=(
"01|This is Private Doc Agent, built for the hackathon, Build A I agents that own their inference, at San Francisco Tech Week."
"02|Most A I apps rent their brain. Pasting a lease into Chat G P T sends your social security number and bank details to another company. Every word is billed, and the provider can slow you down on launch day."
"03|Private Doc Agent runs on our own G P U on Akamai Cloud, using the Qwen 3, 4 B model, served by v L L M. Here I load a sample apartment lease, and click Analyze privately."
"04|The agent splits the document into parts, and sends them to the G P U at the same time, so v L L M batches them together. It returns a plain English summary, red flags ranked by severity, each with the exact quote, the key facts, and the personal data it found, which never left our G P U."
"05|I can ask questions. Can I end the lease early? The agent answers only from the document, and cites the source sentence, in about two seconds."
"07|Every outgoing request passes one egress guard that only allows our own G P U. When something tries to send the document to Open A I, it is blocked before anything leaves. Zero bytes sent."
"06|Now, rush hour. Sixty four users at once. The live panel reads v L L M's own metrics. Thirty two requests run together, while thirty two wait in line, and throughput jumps to over twelve hundred tokens per second."
"08|Here is the whole path. The document goes from the web page, to the agent on the laptop, through a secure tunnel to our G P U, and back. Third party A I is never reached."
"09|Our finding: G P U memory stayed under seven percent, so the bottleneck is the setting of thirty two users at once, not the hardware. That is the next thing we would tune. Private by design, no per token bill, and full control. The code is on GitHub. Thank you!"
)

rm -f audio/*.aiff audio/*.mp4 audio/list.txt
for scene in "${SCENES[@]}"; do
  frame="${scene%%|*}"
  text="${scene#*|}"
  say -v "$VOICE" -r "$RATE" -o "audio/${frame}.aiff" "$text"
  ffmpeg -loglevel error -y -loop 1 -framerate 30 -i "frames/${frame}.png" -i "audio/${frame}.aiff" \
    -af "apad=pad_dur=${PAD_SECONDS}" -shortest \
    -vf "scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2:white,format=yuv420p" \
    -c:v libx264 -tune stillimage -preset veryfast -crf 22 -c:a aac -b:a 160k -ar 44100 "audio/${frame}.mp4"
  echo "file '${frame}.mp4'" >> audio/list.txt
done

ffmpeg -loglevel error -y -f concat -safe 0 -i audio/list.txt -c copy -movflags +faststart demo.mp4
echo "Built video/demo.mp4"
