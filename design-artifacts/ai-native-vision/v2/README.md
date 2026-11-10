# Duet AI Native — reader and viewer edition

This edition explains the design through an everyday report request and a fictional
project that follows research, product definition, development, review and acceptance.
The two languages use the same roles, examples and interaction model.

| Deliverable | Chinese | English |
| --- | --- | --- |
| Design document, nine pages | [中文设计文档](Duet-AI-Native-Design.zh-CN.docx) | [English design document](Duet-AI-Native-Design.en.docx) |
| Video script, twelve scenes | [中文视频文案](video-script.zh-CN.md) | [English video script](video-script.en.md) |

Each script includes the viewer takeaway, progressive screen action, full narration
and transition. The scene guide totals 8 minutes 50 seconds; final timing follows
the recorded narration and visual pauses. These scripts are the source for a new
film edit. The earlier rendered film belongs to the preceding edition.

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
