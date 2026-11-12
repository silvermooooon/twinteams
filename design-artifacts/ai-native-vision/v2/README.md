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
| Offline HTML presentation, 84 slides per language | [打开双语演示](Duet-AI-Native-Presentation.html#zh-01) | [Open English edition](Duet-AI-Native-Presentation.en.html) |

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

Both HTML editions reproduce the 13 chapters and their 84 visual stages in
film order. Every slide is a full 1920 × 1080 frame from the corresponding final
MP4, including the original on-screen text and subtitle at that moment. **N**
opens the original narration for that stage. The two languages use independent
frame numbers and switch to the same stage in the bilingual edition.

Download the HTML and open it directly in a browser. The frames are embedded for
offline use. Use the arrow keys to turn pages or **F** for full screen. Printing
lays out one slide per page in the selected language.

[content.json](presentation/content.json) pairs the languages by visual stage.
Each `[start, frame]` uses absolute, zero-based video frame numbers: `start`
marks the beginning of the stage and `frame` is the still shown on the slide.
The build pulls the narration from the final films' caption manifests. After
editing the selections or the [viewer template](presentation/deck.html), rebuild
from the repository root with Python 3 and FFmpeg:

```sh
python3 design-artifacts/ai-native-vision/v2/presentation/build.py
```
