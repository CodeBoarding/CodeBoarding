# README website animation

`codeboarding-story.gif` captures the opening animation on [codeboarding.org](https://codeboarding.org), inspected and captured on 2026-09-20. Its source is the website's `src/components/site/Story.tsx` and `story.module.css`, mounted by `src/app/page.tsx` directly beneath the headline. It is the illustrative “Add Google sign-in #2841” story, not the later PR #586 product screenshot or workflow showcase. All names, files and comments are the website's public example; no customer data or credentials are involved.

The original sequence is preserved: **The pull request** (5.2 seconds), **The system it touches** (5.6 seconds), then **What the diff hides** (7 seconds). The last scene highlights Identity and Payments and adds the Payments → Identity dependency. The GIF plays once and holds the last frame, matching the website's one-shot autoplay. Its pictured tabs and Replay control are not interactive; the README links to the website and supplies a text summary and static alternative. GIF playback cannot itself respond to reduced-motion preferences.

| Asset | Dimensions | Size | Encoding |
| --- | --- | --- | --- |
| `codeboarding-story.gif` | 960 × 846 | 845,790 bytes (826 KiB) | 18.37 seconds, 8 fps output cap, 128-color palette, 106 optimized frames, no loop extension |
| `codeboarding-story-static.png` | 960 × 846 | 154,725 bytes (151 KiB) | Final scene, lossless PNG, metadata stripped |

## Reproduce the capture

1. Open the live homepage in Chromium, light theme, **1440 × 1100 viewport at 2× pixel density**, with reduced motion off. Wait for fonts to load. Keep the pointer outside the figure so it does not highlight a node.
2. Reload the page and start capturing when `figure[data-stage]` has `data-playing="true"`. Do not start by replaying from stage 3: its CSS exit transition leaves a ghost of the old map in the first frames. Do not change the component's content, styling, timers or animation speed.
3. Capture about 18.4 seconds of device-scale PNG frames with their actual monotonic timestamps, targeting 100 ms between captures. Encode using those timestamps rather than assuming screenshots completed at a fixed rate.
4. Crop to the figure **and all caption paragraphs**, with 4 CSS pixels of padding. The second caption extends below the figure's bounding box: use the maximum bottom edge of the figure and its `p` descendants. This capture used `(x=700, y=125, width=624, height=550)` in CSS pixels, giving 1248 × 1100 source frames. Recalculate bounds if the website layout changes.
5. Save the final scene separately as `still.png`. Create an FFmpeg concat list, `frames.txt`, with a `file '/absolute/path/frame.png'` line and `duration <seconds-until-next-frame>` for each frame; repeat the final file once at the end.
6. Encode and optimize with FFmpeg, Gifsicle and ImageMagick:

```bash
ffmpeg -f concat -safe 0 -i frames.txt \
  -filter_complex '[0:v]fps=8,scale=960:-1:flags=lanczos,split[a][b];[a]palettegen=max_colors=128:stats_mode=diff[p];[b][p]paletteuse=dither=none:diff_mode=rectangle' \
  -map_metadata -1 -loop -1 story-unoptimized.gif
gifsicle -O3 --no-comments --no-names --no-extensions \
  story-unoptimized.gif -o codeboarding-story.gif
magick still.png -strip -resize 960x -define png:compression-level=9 \
  codeboarding-story-static.png
```

No dithering keeps the text clean and avoids noisy moving pixels. Gifsicle merges repeated frames while retaining elapsed time. Inspect the decoded animation, all three captions, the first frame for ghosting, and the static image after optimization. Check the rendered README at desktop and narrow widths; its caption and text summary must remain useful when diagram labels are too small to read inline.
