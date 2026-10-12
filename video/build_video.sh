#!/usr/bin/env bash
# Builds demo.mp4 from the screenshots in frames/ plus local neural narration.
# Needs: pip install piper-tts, and the voice files in voices/ (see VOICE_URL below).
set -euo pipefail
cd "$(dirname "$0")"

VOICE="${VOICE:-en_US-hfc_female-medium}"  # Piper voice, runs offline
VOICE_URL="https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/hfc_female/medium"
LENGTH_SCALE="${LENGTH_SCALE:-1.05}"  # >1 = slightly slower, calmer speech
PAD_SECONDS=0.8

# frame | narration (in the order the video plays)
SCENES=(
"01|Hi! This is Private Doc Agent, built for the hackathon \"Build AI agents that own their inference\", at San Francisco Tech Week."
"02|Here's the problem. Most AI apps rent their brain. When you paste a lease into ChatGPT, your Social Security number and bank details go to another company. Every word is billed, and the provider can slow you down right on launch day."
"03|Private Doc Agent runs on our own GPU, on Akamai Cloud, using the Qwen 3, 4 B model served by V L L M. Let me load a sample apartment lease, and click Analyze privately."
"04|The agent splits the document into parts and sends them to the GPU at the same time, so V L L M batches them together. In seconds, we get a plain-English summary, red flags ranked by severity, each with the exact quote, the key facts, and the personal data it found. And none of it ever left our GPU."
"05|Now I can just ask: can I end the lease early? The agent answers only from the document, and it cites the exact sentence. That took about two seconds."
"07|Every outgoing request goes through one egress guard that only allows our own GPU. So when something tries to send the document to OpenAI, it's blocked before anything leaves. Zero bytes sent."
"06|Now, rush hour. Sixty-four users at once. This live panel reads V L L M's own metrics. Thirty-two requests run together, thirty-two wait in line, and throughput jumps to over twelve hundred tokens per second."
"08|Here's the whole path. The document goes from the web page, to the agent on the laptop, through a secure tunnel to our GPU, and back again. Third-party AI is never reached."
"09|And here's what we learned. GPU memory stayed under seven percent, so the bottleneck isn't the hardware. It's the setting of thirty-two users at once, and that's the next thing we'd tune. Private by design, no per-token bill, and full control. The code is on GitHub. Thanks for watching!"
)

mkdir -p voices audio
[ -f "voices/${VOICE}.onnx" ] || { curl -sL -o "voices/${VOICE}.onnx" "${VOICE_URL}/${VOICE}.onnx"; curl -sL -o "voices/${VOICE}.onnx.json" "${VOICE_URL}/${VOICE}.onnx.json"; }
rm -f audio/*.wav audio/*.mp3 audio/*.aiff audio/*.mp4 audio/list.txt
for scene in "${SCENES[@]}"; do
  frame="${scene%%|*}"
  text="${scene#*|}"
  printf '%s\n' "$text" | python3 -m piper -m "voices/${VOICE}.onnx" --length-scale "$LENGTH_SCALE" -f "audio/${frame}.wav"
  ffmpeg -loglevel error -y -loop 1 -framerate 30 -i "frames/${frame}.png" -i "audio/${frame}.wav" \
    -af "apad=pad_dur=${PAD_SECONDS}" -shortest \
    -vf "scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2:white,format=yuv420p" \
    -c:v libx264 -tune stillimage -preset veryfast -crf 22 -c:a aac -b:a 160k -ar 44100 -ac 2 "audio/${frame}.mp4"
  echo "file '${frame}.mp4'" >> audio/list.txt
done

ffmpeg -loglevel error -y -f concat -safe 0 -i audio/list.txt -c copy -movflags +faststart demo.mp4
echo "Built video/demo.mp4"
