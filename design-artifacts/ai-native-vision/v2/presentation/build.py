"""Build the offline HTML presentation from the editorial copy and final films."""

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
            "notes": "备注", "noteTitle": "演讲备注", "full": "全屏", "exit": "退出全屏",
        },
        "cover": "设计导览",
        "subtitle": "个人委托如何接成业务闭环",
        "notes": "这套设计从员工与自己的 Duet 开始。先看各组件分工，再用日常报表委托和一项虚构的监管变化，说明工作如何跨岗位交接和验收。",
        "system": "系统架构",
        "personal": "日常委托",
        "workflow": "业务案例",
        "closing": "闭环与扩展",
        "caption": "原片画面",
        "map_alt": "Agent Loop 围绕 Message Center 与 LoopLab。每个 Duet 位于圆环外，并连接自己的 Owner。",
    },
    "en": {
        "locale": "en",
        "switch": "EN",
        "ui": {
            "nav": "Presentation controls", "prev": "Previous slide", "next": "Next slide",
            "jump": "Go to slide", "notes": "Notes", "noteTitle": "Speaker notes",
            "full": "Full screen", "exit": "Exit full screen",
        },
        "cover": "Design overview",
        "subtitle": "How personal Duets connect work across a company",
        "notes": "Start with the architecture, then follow a weekly report and a fictional regulatory update to see how work moves between roles and how the team verifies the result.",
        "system": "Architecture",
        "personal": "Personal delegation",
        "workflow": "Workflow example",
        "closing": "Closing the loop",
        "caption": "From the film",
        "map_alt": "Agent Loop surrounds Message Center and LoopLab. Each Duet sits outside the ring and connects to its own Owner.",
    },
}


def data_url(mime, data):
    return f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"


def film_still(language, frame, *, architecture):
    film = FILM / "out" / "v2" / f"duet-ai-native-{language['locale']}-1080p.mp4"
    crop = "1160:640:80:240" if architecture else "1760:630:80:248,scale=1600:-1"
    image = subprocess.check_output([
        "ffmpeg", "-nostdin", "-v", "error", "-ss", f"{frame / 30:.6f}", "-i", str(film),
        "-map", "0:v:0", "-frames:v", "1", "-vf", f"crop={crop}",
        "-c:v", "libwebp", "-quality", "88", "-f", "webp", "pipe:1",
    ])
    if not image:
        raise ValueError(f"No image at frame {frame} in {film.name}")
    minutes, seconds = divmod(frame // 30, 60)
    alt = language["map_alt"] if architecture else ""
    return (
        f'<figure class="film-still"><img src="{data_url("image/webp", image)}" alt="{html.escape(alt)}">'
        f'<figcaption>{language["caption"]} {minutes:02}:{seconds:02}</figcaption></figure>'
    )


def render_slides(chapters, lang, mark, *, hidden):
    language = LANGUAGES[lang]
    total = len(chapters) + 1
    initial = ' data-print="no" hidden' if hidden else ' data-print="yes"'
    for page, chapter in enumerate([None, *chapters], start=1):
        if chapter is None:
            section = language["cover"]
            css = "slide cover"
            notes = language["notes"]
            body = (
                f'<div><h1>Duet <span>AI Native</span></h1><p class="lead">{language["subtitle"]}</p></div>'
                '<div class="brand-mark" aria-hidden="true"></div>'
            )
        else:
            section = language[chapter["section"]]
            css = "slide"
            copy = chapter[lang]
            notes = copy["notes"]
            points = "".join(f"<li>{html.escape(point)}</li>" for point in copy["points"])
            body = (
                f'<div><h2>{html.escape(copy["title"])}</h2><p class="lead">{html.escape(copy["lead"])}</p>'
                f'<ul class="points">{points}</ul></div>'
                f'{film_still(language, chapter["frames"][lang], architecture=page == 2)}'
            )
        last = ' data-last="yes"' if page == total else ""
        yield f"""<section class="{css}" lang="{language['locale']}" data-language="{lang}"{initial}{last}>
          <header class="slide-head"><div class="brand">{mark}<span>Duet</span></div><span class="chapter">{section}</span></header>
          <div class="story">{body}</div>
          <footer class="slide-foot"><span>{page:02} / {total:02}</span></footer>
          <template>{html.escape(notes)}</template>
        </section>"""


def main():
    chapters = json.loads((SOURCE / "content.json").read_text(encoding="utf-8"))
    mark = '<span class="brand-mark" aria-hidden="true"></span>'
    logo = data_url("image/svg+xml", (FILM / "public" / "brand" / "duet-symbol.svg").read_bytes())
    template = (SOURCE / "deck.html").read_text(encoding="utf-8")
    for filename, languages in OUTPUTS.items():
        default = languages[0]
        slides = "\n        ".join(
            slide for lang in languages
            for slide in render_slides(chapters, lang, mark, hidden=lang != default)
        )
        switch = "\n          ".join(
            f'<button type="button" data-language="{lang}" lang="{LANGUAGES[lang]["locale"]}"'
            f' aria-pressed="{str(lang == default).lower()}">{LANGUAGES[lang]["switch"]}</button>'
            for lang in languages
        ) if len(languages) > 1 else ""
        labels = {lang: LANGUAGES[lang]["ui"] for lang in languages}
        deck = (
            template.replace("{{LOCALE}}", LANGUAGES[default]["locale"])
            .replace("/* DUET_MARK */", f'url("{logo}")')
            .replace("<!-- SLIDES -->", slides)
            .replace("          <!-- LANGUAGE_SWITCH -->\n", f"          {switch}\n" if switch else "")
            .replace("/* UI_LABELS */", json.dumps(labels, ensure_ascii=False))
        )
        output = SOURCE.parent / filename
        output.write_text(deck, encoding="utf-8")
        print(output)


if __name__ == "__main__":
    main()
