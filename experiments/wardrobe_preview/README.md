# ID3 wardrobe local preview

Production remains unchanged. The media package comes from:
`/Users/taoxin/my-videos/assets/presenters/ruotang-wardrobe-v1`.
It is marked production_ready=false; clothing texture/sleeve changes during movement remain visible.

`offline_speech.py` is an isolated remote batch job under
`/data1/taoxin/lingcast-wardrobe-local-test/20261030`: copies upstream code to its own tree,
links checkpoint files read-only, prepares separate white/blue face profiles, and calls the existing
CosyVoice for two utterances. GPU2 free-memory gate and 20% process memory cap apply.
No live queues, configs, S3 objects or service lifecycle are changed. Existing service PIDs are
recorded before/after. It creates temporary model weights in this separate batch process; the
resident models are not restarted. The batch process has completed and released its GPU memory.

`record_preview.py` runs the actual LiveAvatarSession Watchdog and wardrobe queue locally,
sinks frames/audio into files instead of RTMP, and verifies zero movement PCM and a new idle
frame before return speech. It does not start live sessions or use Redis. Media remains in ignored
`runtime/wardrobe-preview`. Output is recorded playback, not a production live-room deployment.

```bash
uv run --directory worker python ../experiments/wardrobe_preview/record_preview.py \
  --assets /Users/taoxin/my-videos/assets/presenters/ruotang-wardrobe-v1 \
  --speech /Users/taoxin/LingCast/runtime/wardrobe-preview/speech \
  --output /Users/taoxin/LingCast/runtime/wardrobe-preview
cp experiments/wardrobe_preview/index.html runtime/wardrobe-preview/index.html
python3 -m http.server 59508 --bind 127.0.0.1 --directory runtime/wardrobe-preview
```

Preview has both directions, normal/half speed and timeline annotations. Port59508 is loopback only.
`recordings.json`, per-direction JSON, `asset-verification.json`, and `offline-status.json` record checks.
Complete videos decode; audio/video stream duration differences are under100ms. This is a duration
check, not a claim of perceptually measured perfect lip sync. Facial/mouth quality and costume
continuity still need human review in the local player.

## New styles and autonomous-host reminder test (partial local integration)

`runtime/wardrobe-style-v2-preview` is served on127.0.0.1:59509. At this check, burgundy idle/exit/enter
were delivered and checked; champagne lacks its reviewed enter.mp4, so the player explicitly excludes
champagne. It must not be described as a complete three-look integration.

Three local movies are available: white↔burgundy, plus a26s host-reminder→simulated viewer request→
burgundy change. The latter uses actual `_play_wardrobe`, tick/drain logic and Watchdog, with local
file-storage/resident-identity adapters instead of live S3/Redis/model RPC. All real face clips were made
in the isolated batch, which completed with resident/Worker PIDs unchanged. 3002/3003 have NOT been
registered in the production resident server.

`verify_host_hints.py <output-root> white,burgundy` tests the actual HostDirector with a simulated
4-minute clock and file-cache adapter. Ordinary paragraphs are timing fixtures, never shown; the
selected reminder is a real cloned-voice/lip-sync clip. Covers enablement, profile/clip-descriptor
readiness, reply switch, manual priority, pause and cooldown. The reminder does not itself enqueue a
wardrobe request; the combined movie supplies a simulated viewer request explicitly. Test-only enabled
manifests stay under ignored runtime/, never in the deploy configuration.

When reviewed champagne enter arrives, verify final manifest hashes and isolated-input SHA matches,
then record the new cycles and rerun `verify_host_hints.py` with its default three-look order. Do not
use raw work/ clips as delivery media, and do not silently overwrite the existing review notes.

### Updated verification: all three looks available

The reviewed champagne entrance arrived during the update. Revision `20261030-130322` supersedes
the partial result above; root `recordings.json` now points to four versioned movies: white→burgundy,
burgundy→champagne, champagne→white, plus host-reminder→simulated viewer request→burgundy.
All four use the actual Session/tick/_play_wardrobe/Watchdog with file/resident adapters. The normal
and reminder demos preserve the exact appearance commit boundary and end-of-outro barrier.

13 delivered input assets were SHA checked, silent videos had dimensions/fps/absence of audio checked
and decoded fully, and all three idles matched isolated model preparation inputs. Four final movies
decoded, have audio/video stream duration differences below100ms, and passed movement-zero-PCM and
idle-before-outro checks. HostDirector tests now cover the full white/burgundy/champagne order using a
simulated clock. Root `asset-state.json` is complete=true **for local integration**, production_ready=false.
No service deployment, production appearance registration or model restart occurred.
