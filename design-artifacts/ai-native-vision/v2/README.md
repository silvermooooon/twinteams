# Duet AI Native — reader and viewer edition

The films introduce the architecture and component responsibilities first: Agent
Loop forms the central ring, Message Center and LoopLab sit inside, and personal
Duets surround it, each connected to its own Owner. The everyday report request
and fictional project then follow research, product definition, development, review
and acceptance. The two languages use the same roles, examples and interaction model.

| Deliverable | Chinese | English |
| --- | --- | --- |
| Design document, nine pages | [中文设计文档](Duet-AI-Native-Design.zh-CN.docx) | [English design document](Duet-AI-Native-Design.en.docx) |
| Video script, architecture opening + twelve story scenes | [中文视频文案](video-script.zh-CN.md) | [English video script](video-script.en.md) |
| Narrated 1080p film | [中文成片 · 11:31](../../duet-intro-video/out/v2/duet-ai-native-zh-CN-1080p.mp4) | [English film · 11:10](../../duet-intro-video/out/v2/duet-ai-native-en-1080p.mp4) |
| Offline HTML presentation, 14 slides | [打开双语演示](Duet-AI-Native-Presentation.html#zh-01) | [Open English edition](Duet-AI-Native-Presentation.en.html) |

Each script includes the viewer takeaway, progressive screen action, full narration
and transition. Final timing follows the recorded narration and visual pauses.
The completed films use independent
language timings, with matching subtitles and progressive screen action. Their
[Remotion source and reproduction instructions](../../duet-intro-video/README.md)
are in the sibling media project. Both final MP4 files are included in Git; on
GitHub, use **View raw** or **Download raw file** to open or download them.

The design documents use the editable `content.zh-CN.json` and `content.en.json`
sources. `build_documents.py` generates both DOCX files and their embedded diagrams
with python-docx and Pillow. It uses the existing Noto Sans SC font in
`../../duet-intro-video/public/fonts/`.

From the repository root, using a Python environment with those libraries:

```sh
python3 design-artifacts/ai-native-vision/v2/build_documents.py
```

Edit the sources and regenerate the documents. The delivered documents were rendered
and all nine pages in each language were visually checked, including tables, diagrams
and page boundaries. Preview PDFs and page images are QA outputs and are not committed.

## HTML presentation

Both HTML editions embed their film stills and open directly in a browser
offline. Use the arrow keys to advance, **F** for full screen and **N** for
speaker notes. The bilingual edition switches languages without leaving the
current slide. The separate English edition opens with English slides, notes
and controls. Printing lays out one slide per page. On GitHub, download the
HTML before opening it.

The deck follows the final films chapter for chapter. Speaker notes distinguish
the film's design examples from current product behavior where relevant. Edit
the presentation copy or frame selections in [content.json](presentation/content.json)
and its layout in [deck.html](presentation/deck.html), then rebuild from the
repository root with Python 3 and FFmpeg:

```sh
python3 design-artifacts/ai-native-vision/v2/presentation/build.py
```
