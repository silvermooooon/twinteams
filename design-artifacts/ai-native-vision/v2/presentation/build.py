"""Build the offline HTML deck from the final V2 video frames and narration."""

import base64
import html
import json
from pathlib import Path
import subprocess


SOURCE = Path(__file__).resolve().parent
FILM = SOURCE.parents[2] / "duet-intro-video"
OUTPUTS = {
    "Duet-AI-Native-Presentation.html": ("zh", "en"),
    "Duet-AI-Native-Presentation.en.html": ("en",),
}
LANGUAGES = {
    "zh": {
        "locale": "zh-CN",
        "switch": "中文",
        "ui": {
            "nav": "演示控制", "prev": "上一页", "next": "下一页", "jump": "跳转到指定页",
            "notes": "旁白", "noteTitle": "原片旁白", "full": "全屏", "exit": "退出全屏",
        },
    },
    "en": {
        "locale": "en",
        "switch": "EN",
        "ui": {
            "nav": "Presentation controls", "prev": "Previous slide", "next": "Next slide",
            "jump": "Go to slide", "notes": "Narration", "noteTitle": "Original narration",
            "full": "Full screen", "exit": "Exit full screen",
        },
    },
}


def film_still(film, frame, fps):
    seconds, offset = divmod(frame, fps)
    # Seek to a whole second before selecting the exact frame at 30 fps.
    image = subprocess.check_output([
        "ffmpeg", "-nostdin", "-v", "error", "-ss", str(seconds), "-i", str(film),
        "-map", "0:v:0", "-frames:v", "1", "-vf", f"trim=start_frame={offset},format=bgra",
        "-c:v", "libwebp", "-lossless", "1", "-compression_level", "4", "-f", "webp", "pipe:1",
    ])
    if not image:
        raise ValueError(f"No image at frame {frame} in {film.name}")
    return "data:image/webp;base64," + base64.b64encode(image).decode("ascii")


def film_pages(chapters, lang):
    locale = LANGUAGES[lang]["locale"]
    film = FILM / "out" / "v2" / f"duet-ai-native-{locale}-1080p.mp4"
    manifest = json.loads((FILM / "public" / "voiceover-v2" / locale / "manifest.json").read_text(encoding="utf-8"))
    fps = manifest["fps"]
    if len(chapters) != len(manifest["chapters"]):
        raise ValueError(f"Chapter count differs from {film.name}")
    for chapter, original in zip(chapters, manifest["chapters"]):
        scene = original["id"]
        pages = chapter["frames"][lang]
        end = original["start"] + original["duration"]
        if not pages or pages[0][0] != original["start"] or len(pages) != len(chapter["frames"]["en"]):
            raise ValueError(f"Unpaired pages for {locale} scene {scene}")
        ends = [start for start, _ in pages[1:]] + [end]
        cues = [cue for cue in manifest["cues"] if cue["scene"] == scene]
        for (start, frame), stop in zip(pages, ends):
            if not start <= frame < stop <= end:
                raise ValueError(f"Frame {frame} is outside its page in {locale} scene {scene}")
            # A cue that crosses a visual transition appears in both pages' notes.
            narration = "\n\n".join(
                cue["text"] for cue in cues
                if cue["start"] < stop and cue["start"] + cue["duration"] > start
            )
            minutes, seconds = divmod(frame // fps, 60)
            time = f"{minutes:02}:{seconds:02}"
            caption = next((cue["text"] for cue in cues if cue["start"] <= frame < cue["start"] + cue["duration"]), "")
            yield scene, frame, time, chapter["title"][lang], caption, narration, film_still(film, frame, fps)


def render_slides(pages, lang, *, hidden):
    locale = LANGUAGES[lang]["locale"]
    visibility = 'data-print="no" hidden' if hidden else 'data-print="yes"'
    for index, (scene, frame, time, title, caption, narration, image) in enumerate(pages, start=1):
        alt = html.escape(" ".join(caption.split()))
        last = ' data-last="yes"' if index == len(pages) else ""
        yield f'''<section class="slide" lang="{locale}" data-language="{lang}" data-scene="{scene}" data-frame="{frame}" data-time="{time}" {visibility}{last}>
          <h2 class="sr-only">{html.escape(title)}</h2>
          <img class="film-frame" width="1920" height="1080" src="{image}" alt="{alt}">
          <template>{html.escape(narration)}</template>
        </section>'''


def main():
    chapters = json.loads((SOURCE / "content.json").read_text(encoding="utf-8"))
    pages = {lang: list(film_pages(chapters, lang)) for lang in LANGUAGES}
    template = (SOURCE / "deck.html").read_text(encoding="utf-8")
    for filename, languages in OUTPUTS.items():
        default = languages[0]
        slides = "\n        ".join(
            slide
            for lang in languages
            for slide in render_slides(pages[lang], lang, hidden=lang != default)
        )
        switch = "\n          ".join(
            f'<button type="button" data-language="{lang}" lang="{LANGUAGES[lang]["locale"]}"'
            f' aria-pressed="{str(lang == default).lower()}">{LANGUAGES[lang]["switch"]}</button>'
            for lang in languages
        ) if len(languages) > 1 else ""
        labels = {lang: LANGUAGES[lang]["ui"] for lang in languages}
        deck = (
            template.replace("{{LOCALE}}", LANGUAGES[default]["locale"])
            .replace("<!-- SLIDES -->", slides)
            .replace("          <!-- LANGUAGE_SWITCH -->\n", f"          {switch}\n" if switch else "")
            .replace("/* UI_LABELS */", json.dumps(labels, ensure_ascii=False))
        )
        output = SOURCE.parent / filename
        output.write_text(deck, encoding="utf-8")
        print(output)


if __name__ == "__main__":
    main()
