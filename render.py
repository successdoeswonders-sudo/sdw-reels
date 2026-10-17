#!/usr/bin/env python3
"""Success Does Wonders - reel maker.

Builds a 1080x1920 voiceover reel from a post in the SDW Instagram Calendar:
  optional cinematic hook clip (Higgsfield) -> branded slides with slow zoom,
  word-synced captions, AI voiceover (free Microsoft voices via edge-tts),
  soft music bed. Output: out/reel.mp4

Env:
  POST_URL     n8n endpoint returning the post JSON (slides, reel_script, ...)
  SLIDE_BASE   n8n slide renderer URL (…/webhook/sdw-slide)
  ROW_ID       calendar row id
  VOICE        edge-tts voice (default en-IN-NeerjaNeural)
  OFFLINE=1    local test mode: fake voice (tone) instead of edge-tts
"""
import json, os, random, re, shutil, subprocess, sys, urllib.request
from pathlib import Path

W, H, FPS = 1080, 1920, 30
BG = '0x14161A'
GOLD = '0xD4B06A'
FONT = os.environ.get('FONT', '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf')
VOICE = os.environ.get('VOICE', 'en-IN-NeerjaNeural')
OFFLINE = os.environ.get('OFFLINE') == '1'
ROOT = Path(__file__).resolve().parent
WORK = ROOT / 'work'
OUT = ROOT / 'out'


def run(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        sys.stderr.write(' '.join(cmd)[:600] + '\n' + r.stderr[-3000:])
        raise SystemExit('ffmpeg step failed')
    return r.stdout


def duration(path):
    return float(run(['ffprobe', '-v', 'error', '-show_entries', 'format=duration',
                      '-of', 'default=nw=1:nk=1', str(path)]).strip())


def fetch(url, dest):
    req = urllib.request.Request(url, headers={'User-Agent': 'sdw-reels'})
    with urllib.request.urlopen(req, timeout=120) as r, open(dest, 'wb') as f:
        shutil.copyfileobj(r, f)


def clean(t):
    t = str(t or '')
    t = t.replace('‘', "'").replace('’', "'").replace('“', '"').replace('”', '"')
    t = re.sub(r'[–—]', '-', t)
    return re.sub(r'\s+', ' ', t).strip()


def tts(text, mp3):
    if OFFLINE:  # placeholder voice for local tests: soft tone, ~0.38 s per word
        secs = max(1.6, 0.38 * len(text.split()))
        run(['ffmpeg', '-y', '-f', 'lavfi', '-i', f'sine=frequency=220:duration={secs}',
             '-af', 'volume=0.15', str(mp3)])
        return
    run([sys.executable, '-m', 'edge_tts', '--voice', VOICE, '--rate', '+8%',
         '--text', text, '--write-media', str(mp3)])


def caption_chunks(text, secs, idx):
    """Split a line into 2-4 word captions timed across the segment (word-count weighted)."""
    words = text.split()
    chunks, cur = [], []
    for w in words:
        cur.append(w)
        if len(cur) >= 3 or w.endswith(('.', ',', '?', '!', ':')):
            chunks.append(' '.join(cur)); cur = []
    if cur:
        chunks.append(' '.join(cur))
    total_words = max(1, len(words))
    t, filters = 0.12, []
    speak = max(0.5, secs - 0.35)
    for j, c in enumerate(chunks):
        d = speak * len(c.split()) / total_words
        f = WORK / f'c{idx:02d}_{j:02d}.txt'
        f.write_text(c.upper())
        filters.append(f"drawtext=fontfile={FONT}:textfile={f}:fontcolor=white:fontsize=64:"
                       f"x=(w-text_w)/2:y=1470:borderw=6:bordercolor=black:"
                       f"enable='between(t,{t:.2f},{t + d:.2f})'")
        t += d
    return ','.join(filters)


def segment(idx, still, text, secs, hook_clip=None):
    """One reel segment: slide (or hook clip) on brand background + timed captions."""
    out = WORK / f'seg{idx:02d}.mp4'
    frames = int(secs * FPS) + 1
    caps = caption_chunks(text, secs, idx)
    brand = f"drawbox=x=0:y=0:w={W}:h=10:color={GOLD}:t=fill,drawbox=x=0:y={H - 10}:w={W}:h=10:color={GOLD}:t=fill"
    if hook_clip:
        vf = (f"[0:v]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},"
              f"trim=duration={secs},setpts=PTS-STARTPTS,fps={FPS},{brand},{caps},format=yuv420p[v]")
        run(['ffmpeg', '-y', '-i', str(hook_clip), '-filter_complex', vf, '-map', '[v]', '-an',
             '-t', f'{secs}', '-r', str(FPS), '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '20', str(out)])
    else:
        # slow zoom on the slide (960x1200), placed on a 9:16 brand canvas above the captions
        z = ("zoompan=z='min(1+0.0009*on,1.08)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
             f"d={frames}:s=960x1200:fps={FPS}")
        vf = (f"color=c={BG}:s={W}x{H}:r={FPS}:d={secs}[bg];"
              f"[0:v]scale=1920:2400,{z}[s];"
              f"[bg][s]overlay=60:200:shortest=1,{brand},{caps},"
              f"fade=t=in:st=0:d=0.2,format=yuv420p[v]")
        run(['ffmpeg', '-y', '-loop', '1', '-i', str(still), '-filter_complex', vf, '-map', '[v]', '-an',
             '-t', f'{secs}', '-r', str(FPS), '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '20', str(out)])
    return out


def music_bed(total, path):
    tracks = sorted((ROOT / 'music').glob('*.mp3')) if (ROOT / 'music').exists() else []
    if tracks:
        src = random.choice(tracks)
        run(['ffmpeg', '-y', '-stream_loop', '-1', '-i', str(src), '-t', f'{total}',
             '-af', f'volume=0.10,afade=t=in:d=1,afade=t=out:st={max(0, total - 1.5)}:d=1.5', str(path)])
        return
    # fallback: soft generated ambient pad (royalty-free by construction)
    expr = ("0.05*sin(2*PI*220*t)*(0.6+0.4*sin(2*PI*0.1*t))+0.04*sin(2*PI*277.18*t)"
            "+0.04*sin(2*PI*329.63*t)+0.03*sin(2*PI*440*t)*(0.5+0.5*sin(2*PI*0.07*t))")
    run(['ffmpeg', '-y', '-f', 'lavfi', '-i', f"aevalsrc='{expr}':s=44100:d={total}",
         '-af', f'volume=0.6,afade=t=in:d=1.5,afade=t=out:st={max(0, total - 1.5)}:d=1.5', str(path)])


def main():
    shutil.rmtree(WORK, ignore_errors=True)
    WORK.mkdir(parents=True)
    OUT.mkdir(exist_ok=True)

    if os.environ.get('POST_FILE'):
        post = json.loads(Path(os.environ['POST_FILE']).read_text())
    else:
        fetch(os.environ['POST_URL'], WORK / 'post.json')
        post = json.loads((WORK / 'post.json').read_text())
    slides = post['slides'] if isinstance(post['slides'], list) else json.loads(post['slides'])
    script = post.get('reel_script') or []
    if isinstance(script, str):
        script = json.loads(script)
    if not script:  # fall back: narrate the slides themselves
        script = [{'slide': i + 1, 'text': clean(s.get('heading', '') + '. ' + s.get('body', ''))}
                  for i, s in enumerate(slides)]

    # slide images from the n8n renderer (or local files in test mode)
    for i in range(1, len(slides) + 1):
        dest = WORK / f'slide{i}.jpg'
        local = os.environ.get('SLIDE_DIR')
        if local:
            shutil.copy(Path(local) / f'slide{i}.jpg', dest)
        else:
            fetch(f"{os.environ['SLIDE_BASE']}?id={os.environ['ROW_ID']}&n={i}", dest)

    hook_clip = None
    if post.get('hook_clip_url'):
        try:
            hook_clip = WORK / 'hook.mp4'
            fetch(post['hook_clip_url'], hook_clip)
        except Exception as e:  # never block the reel on the optional clip
            print('hook clip skipped:', e)
            hook_clip = None

    segs, voices = [], []
    for i, line in enumerate(script):
        text = clean(line.get('text', ''))
        if not text:
            continue
        mp3 = WORK / f'v{i:02d}.mp3'
        tts(text, mp3)
        secs = round(duration(mp3) + 0.35, 2)
        slide_no = int(line.get('slide', 1) or 1)
        still = WORK / f'slide{min(max(1, slide_no), len(slides))}.jpg'
        use_hook = hook_clip if (i == 0 and hook_clip) else None
        segs.append(segment(i, still, text, secs, use_hook))
        pad = WORK / f'p{i:02d}.wav'
        run(['ffmpeg', '-y', '-i', str(mp3), '-af', 'apad=pad_dur=0.35', '-ar', '44100', '-ac', '2', str(pad)])
        voices.append(pad)

    (WORK / 'v.txt').write_text(''.join(f"file '{p.name}'\n" for p in segs))
    (WORK / 'a.txt').write_text(''.join(f"file '{p.name}'\n" for p in voices))
    run(['ffmpeg', '-y', '-f', 'concat', '-safe', '0', '-i', str(WORK / 'v.txt'), '-c', 'copy', str(WORK / 'video.mp4')])
    run(['ffmpeg', '-y', '-f', 'concat', '-safe', '0', '-i', str(WORK / 'a.txt'), str(WORK / 'voice.wav')])
    total = duration(WORK / 'video.mp4')
    music_bed(total, WORK / 'music.wav')
    run(['ffmpeg', '-y', '-i', str(WORK / 'video.mp4'), '-i', str(WORK / 'voice.wav'), '-i', str(WORK / 'music.wav'),
         '-filter_complex', '[1:a][2:a]amix=inputs=2:duration=first:normalize=0,volume=1.6[a]',
         '-map', '0:v', '-map', '[a]', '-c:v', 'copy', '-c:a', 'aac', '-b:a', '160k', '-ar', '44100',
         '-movflags', '+faststart', '-shortest', str(OUT / 'reel.mp4')])
    print(json.dumps({'ok': True, 'seconds': round(duration(OUT / 'reel.mp4'), 1)}))


if __name__ == '__main__':
    main()
