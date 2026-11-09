#!/usr/bin/env python3
"""Success Does Wonders - reel maker v2 (short, fast "viral" format).

Builds a 1080x1920 reel from a post in the SDW Instagram Calendar:
  - big hook title on screen from the very first frame
  - full-screen moving footage per line (AI hook clip / free stock B-roll),
    falling back to the branded slide when no footage is available
  - fast 2-word captions, AI voiceover (free Microsoft voices via edge-tts),
    soft music bed. Output: out/reel.mp4

Env:
  POST_URL     n8n endpoint returning the post JSON (slides, reel_script, broll_urls, hook_clip_url)
  SLIDE_BASE   n8n slide renderer URL (.../webhook/sdw-slide)
  ROW_ID       calendar row id
  VOICE        edge-tts voice (default en-IN-NeerjaNeural)
  OFFLINE=1    local test mode: fake voice (tone) instead of edge-tts
  POST_FILE / SLIDE_DIR   local test inputs instead of POST_URL / SLIDE_BASE
"""
import json, os, random, re, shutil, subprocess, sys, textwrap, time, urllib.request
from pathlib import Path

W, H, FPS = 1080, 1920, 30
BG = '0x14161A'
GOLD = '0xD4B06A'
ROOT = Path(__file__).resolve().parent
FONT = os.environ.get('FONT', str(ROOT / 'fonts' / 'Poppins-Bold.ttf'))
VOICE = os.environ.get('VOICE') or 'en-IN-NeerjaNeural'
OFFLINE = os.environ.get('OFFLINE') == '1'
WORK = ROOT / 'work'
OUT = ROOT / 'out'
GAP = 0.12  # pause after each spoken line (keeps the pace fast)


def run(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        sys.stderr.write(' '.join(cmd)[:800] + '\n' + r.stderr[-3000:])
        raise SystemExit('ffmpeg step failed')
    return r.stdout


def duration(path):
    return float(run(['ffprobe', '-v', 'error', '-show_entries', 'format=duration',
                      '-of', 'default=nw=1:nk=1', str(path)]).strip())


def fetch(url, dest, tries=6):
    """Download with retries - the n8n server is sometimes slow to answer."""
    last = None
    for attempt in range(tries):
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'sdw-reels'})
            with urllib.request.urlopen(req, timeout=60) as r, open(dest, 'wb') as f:
                shutil.copyfileobj(r, f)
            return
        except Exception as e:
            last = e
            print(f'::warning::fetch attempt {attempt + 1} failed for {url.split("?")[0]}: {e}', flush=True)
            time.sleep(8 * (attempt + 1))
    raise RuntimeError(f'could not download {url.split("?")[0]}: {last}')


def clean(t):
    t = str(t or '')
    t = t.replace('‘', "'").replace('’', "'").replace('“', '"').replace('”', '"')
    t = re.sub(r'[–—]', '-', t)
    t = re.sub(r'[^\x20-\x7E]', '', t)  # drawtext font has no emoji
    return re.sub(r'\s+', ' ', t).strip()


def tts(text, mp3):
    if OFFLINE:  # placeholder voice for local tests: soft tone, ~0.33 s per word
        secs = max(1.2, 0.33 * len(text.split()))
        run(['ffmpeg', '-y', '-f', 'lavfi', '-i', f'sine=frequency=220:duration={secs}',
             '-af', 'volume=0.15', str(mp3)])
        return
    run([sys.executable, '-m', 'edge_tts', '--voice', VOICE, '--rate', '+15%',
         '--text', text, '--write-media', str(mp3)])


def txt(name, content):
    f = WORK / name
    f.write_text(content)
    return f


def caption_filters(text, secs, idx, y):
    """Fast 2-word captions, timed across the spoken line (word-count weighted)."""
    words = text.split()
    chunks, cur = [], []
    for w in words:
        cur.append(w)
        if len(cur) >= 2 or w.endswith(('.', ',', '?', '!', ':')):
            chunks.append(' '.join(cur)); cur = []
    if cur:
        chunks.append(' '.join(cur))
    total = max(1, len(words))
    t, out = 0.0, []
    speak = max(0.4, secs - GAP)
    for j, c in enumerate(chunks):
        d = speak * len(c.split()) / total
        end = secs if j == len(chunks) - 1 else t + d
        f = txt(f'c{idx:02d}_{j:02d}.txt', c.upper())
        out.append(f"drawtext=fontfile={FONT}:textfile={f}:fontcolor={GOLD}:fontsize=92:"
                   f"x=(w-text_w)/2:y={y}:borderw=9:bordercolor=black:"
                   f"enable='between(t,{t:.2f},{end:.2f})'")
        t += d
    return out


def title_filter(title):
    """Hook title, top of screen, visible from the first frame (one drawtext per line,
    so no line-break glyphs ever show)."""
    lines = textwrap.wrap(clean(title), 20)[:3]
    out = []
    for k, line in enumerate(lines):
        f = txt(f'title{k}.txt', line)
        out.append(f"drawtext=fontfile={FONT}:textfile={f}:fontcolor=white:fontsize=70:"
                   f"x=(w-text_w)/2:y={230 + k * 112}:box=1:boxcolor=black@0.62:boxborderw=22")
    return ','.join(out) if out else 'null'


def handle_filter():
    return (f"drawtext=fontfile={FONT}:text='@successdoeswonders':fontcolor=white@0.85:fontsize=36:"
            f"x=(w-text_w)/2:y=h-150:borderw=3:bordercolor=black@0.6")


def brand_bars():
    return f"drawbox=x=0:y=0:w={W}:h=12:color={GOLD}:t=fill,drawbox=x=0:y={H - 12}:w={W}:h=12:color={GOLD}:t=fill"


def video_segment(idx, src, text, secs, title, reuse=0):
    """Full-screen footage: fill 9:16, slow push-in, darkened for readable text.
    reuse>0 means the clip was already shown: mirror it and start later so it looks fresh."""
    out = WORK / f'seg{idx:02d}.mp4'
    caps = caption_filters(text, secs, idx, 1180)
    vary = ['hflip'] if reuse % 2 == 1 else []
    vf = ','.join([
        f"scale={W}:{H}:force_original_aspect_ratio=increase", f"crop={W}:{H}", *vary,
        f"scale=w='{W}*(1+0.04*t/{secs})':h=-2:eval=frame", f"crop={W}:{H}",
        "setsar=1", f"fps={FPS}", "eq=brightness=-0.10:saturation=1.15",
        title_filter(title), *caps, handle_filter(), brand_bars(), "format=yuv420p"])
    start = ['-ss', f'{(reuse * 1.1) % 2:.2f}'] if reuse else []
    run(['ffmpeg', '-y', '-stream_loop', '-1', *start, '-i', str(src), '-t', f'{secs}', '-an',
         '-vf', vf, '-r', str(FPS), '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '21', str(out)])
    return out


def slide_segment(idx, still, text, secs):
    """Fallback: branded slide card on a blurred copy of itself, slow zoom, captions below."""
    out = WORK / f'seg{idx:02d}.mp4'
    frames = int(secs * FPS) + 1
    caps = caption_filters(text, secs, idx, 1560)
    z = ("zoompan=z='min(1+0.0012*on,1.10)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
         f"d={frames}:s=960x1200:fps={FPS}")
    fc = (f"[0:v]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},boxblur=30:2,"
          f"eq=brightness=-0.25,setsar=1,fps={FPS}[bg];"
          f"[0:v]scale=1920:2400,{z}[s];"
          f"[bg][s]overlay=60:250:shortest=1,{','.join(caps)},{handle_filter()},{brand_bars()},format=yuv420p[v]")
    run(['ffmpeg', '-y', '-loop', '1', '-i', str(still), '-filter_complex', fc, '-map', '[v]', '-an',
         '-t', f'{secs}', '-r', str(FPS), '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '21', str(out)])
    return out


def music_bed(total, path):
    tracks = sorted((ROOT / 'music').glob('*.mp3')) if (ROOT / 'music').exists() else []
    if tracks:
        src = random.choice(tracks)
        run(['ffmpeg', '-y', '-stream_loop', '-1', '-i', str(src), '-t', f'{total}',
             '-af', f'volume=0.12,afade=t=in:d=0.5,afade=t=out:st={max(0, total - 1.2)}:d=1.2', str(path)])
        return
    # fallback: soft generated pad with a gentle pulse (royalty-free by construction)
    expr = ("(0.05*sin(2*PI*220*t)+0.04*sin(2*PI*277.18*t)+0.04*sin(2*PI*329.63*t))"
            "*(0.55+0.45*sin(2*PI*2*t))")
    run(['ffmpeg', '-y', '-f', 'lavfi', '-i', f"aevalsrc='{expr}':s=44100:d={total}",
         '-af', f'volume=0.5,afade=t=in:d=0.5,afade=t=out:st={max(0, total - 1.2)}:d=1.2', str(path)])


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
        script = json.loads(script) if script.strip() else []
    if not script:  # fall back: short narration from slide headings
        script = [{'slide': i + 1, 'text': clean(s.get('heading', ''))} for i, s in enumerate(slides)]
    broll = post.get('broll_urls') or []
    if isinstance(broll, str):
        broll = json.loads(broll) if broll.strip() else []
    title = clean(slides[0].get('heading', '')) if slides else ''

    # slide images from the n8n renderer (only needed for fallback segments)
    def slide_img(n):
        n = min(max(1, n), len(slides))
        dest = WORK / f'slide{n}.jpg'
        if not dest.exists():
            local = os.environ.get('SLIDE_DIR')
            if local:
                shutil.copy(Path(local) / f'slide{n}.jpg', dest)
            else:
                fetch(f"{os.environ['SLIDE_BASE']}?id={os.environ['ROW_ID']}&n={n}", dest)
        return dest

    cache, uses = {}, {}

    def footage(i):
        """Moving footage for line i: AI hook clip for the opening, then AI / stock B-roll.
        Returns (path, times_already_used)."""
        url = post.get('hook_clip_url') if i == 0 and post.get('hook_clip_url') else (broll[i] if i < len(broll) else '')
        if not url:
            return None, 0
        if url in cache:
            uses[url] += 1
            return cache[url], uses[url]
        dest = WORK / f'clip{i:02d}.mp4'
        try:
            fetch(url, dest, tries=3)
            if duration(dest) < 0.5:
                return None, 0
            cache[url], uses[url] = dest, 0
            return dest, 0
        except Exception as e:  # never block the reel on optional footage
            print('::warning::footage skipped:', e)
            return None, 0

    segs, voices = [], []
    for i, line in enumerate(script):
        text = clean(line.get('text', ''))
        if not text:
            continue
        mp3 = WORK / f'v{i:02d}.mp3'
        tts(text, mp3)
        secs = round(duration(mp3) + GAP, 2)
        clip, reuse = footage(i)
        if clip:
            segs.append(video_segment(i, clip, text, secs, title, reuse))
        else:
            segs.append(slide_segment(i, slide_img(int(line.get('slide', 1) or 1)), text, secs))
        pad = WORK / f'p{i:02d}.wav'
        run(['ffmpeg', '-y', '-i', str(mp3), '-af', f'apad=pad_dur={GAP}', '-ar', '44100', '-ac', '2', str(pad)])
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
    print(json.dumps({'ok': True, 'seconds': round(duration(OUT / 'reel.mp4'), 1),
                      'footage_segments': sum(1 for i in range(len(script)) if (WORK / f'clip{i:02d}.mp4').exists())}))


if __name__ == '__main__':
    main()
