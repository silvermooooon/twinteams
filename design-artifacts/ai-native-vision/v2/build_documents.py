"""Build the two design documents from the editable language sources."""

import json
from pathlib import Path
import tempfile

from docx import Document
from docx.enum.table import WD_CELL_VERTICAL_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.opc.constants import RELATIONSHIP_TYPE
from docx.shared import Mm, Pt, RGBColor
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent
FONT = ROOT.parents[1] / 'duet-intro-video/public/fonts/NotoSansSC.ttf'
INK = '202124'
RED = 'B5121B'
GREY = '61656A'


def main():
    with tempfile.TemporaryDirectory(prefix='duet-design-diagrams-') as temporary:
        for language in ['zh-CN', 'en']:
            content = json.loads((ROOT / f'content.{language}.json').read_text())
            document = new_document(language)
            for index, page in enumerate(content['pages']):
                if index == 0:
                    add_text(document, content['title'], 'Title')
                    add_text(document, content['subtitle'], 'Subtitle')
                else:
                    p = add_text(document, f'{index + 1:02d}  /  DUET AI NATIVE', 'Page index')
                    p.paragraph_format.page_break_before = True
                document.add_heading(page['title'], 1)
                add_text(document, page['lead'], 'Lead')
                if 'figure' in page:
                    path = Path(temporary) / f'{language}-{page["figure"]}.png'
                    diagram(page['figure'], language, path)
                    paragraph = document.add_paragraph()
                    paragraph.paragraph_format.line_spacing = 1.0
                    paragraph.paragraph_format.space_after = Pt(11)
                    picture = paragraph.add_run().add_picture(str(path), width=Mm(164))
                    picture._inline.docPr.set('descr', page['title'])
                if 'table' in page:
                    add_table(document, page['table'])
                for section in page['sections']:
                    document.add_heading(section['heading'], 2)
                    for text in section['paragraphs']:
                        add_text(document, text)
                for label, url in page.get('references', []):
                    add_link(document, label, url)
            path = ROOT / f'Duet-AI-Native-Design.{language}.docx'
            document.save(path)
            print(path)


def new_document(language):
    document = Document()
    document.core_properties.title = 'Duet AI Native Design'
    document.core_properties.author = 'Duet'
    section = document.sections[0]
    section.page_width, section.page_height = Mm(210), Mm(297)
    section.top_margin, section.bottom_margin = Mm(19), Mm(19)
    section.left_margin, section.right_margin = Mm(23), Mm(23)
    section.header_distance = section.footer_distance = Mm(9)
    styles = [
        ('Normal', 10.6, 17.5 if language == 'zh-CN' else 15.3, INK, False, 6, 0),
        ('Title', 27, 33, '000000', True, 5, 0),
        ('Subtitle', 12.3, 17, '000000', False, 15, 0),
        ('Heading 1', 21, 27, '000000', True, 10, 0),
        ('Heading 2', 12.3, 18, '000000', True, 4, 8),
        ('Lead', 11.2, 18, INK, False, 12, 0),
        ('Page index', 8.5, 12, RED, True, 7, 0),
        ('Reference', 8, 11.5, GREY, False, 3, 5),
        ('Header', 8, 11, '000000', False, 0, 0),
        ('Footer', 8, 11, GREY, False, 0, 0),
    ]
    for name, size, line, color, bold, after, before in styles:
        style = document.styles[name] if name in document.styles else document.styles.add_style(name, 1)
        style.font.name = 'Arial'
        style.font.size = Pt(size)
        style.font.color.rgb = RGBColor.from_string(color)
        style.font.bold, style.font.italic = bold, False
        style.element.get_or_add_rPr().rFonts.set(qn('w:eastAsia'), 'Noto Sans SC')
        props = style.paragraph_format
        props.line_spacing, props.space_after, props.space_before = Pt(line), Pt(after), Pt(before)
        snap = OxmlElement('w:snapToGrid')
        snap.set(qn('w:val'), '0')
        style.element.get_or_add_pPr().append(snap)
        for border in style.element.xpath('./w:pPr/w:pBdr'):
            border.getparent().remove(border)
    section.header.paragraphs[0].text = 'DUET  /  AI NATIVE'
    footer = section.footer.paragraphs[0]
    footer.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    footer.add_run('核心设计  ·  ' if language == 'zh-CN' else 'DESIGN  ·  ')
    field = OxmlElement('w:fldSimple')
    field.set(qn('w:instr'), 'PAGE')
    footer._p.append(field)
    return document


def add_text(document, text, style='Normal'):
    paragraph = document.add_paragraph(text, style)
    paragraph.paragraph_format.widow_control = True
    return paragraph


def add_table(document, data):
    table = document.add_table(rows=1, cols=3)
    table.autofit = False
    widths = [48, 56, 60]
    for column, width in zip(table.columns, widths):
        column.width = Mm(width)
    for i, values in enumerate([data['headers'], *data['rows']]):
        row = table.rows[0] if i == 0 else table.add_row()
        no_split = OxmlElement('w:cantSplit')
        row._tr.get_or_add_trPr().append(no_split)
        if i == 0:
            row._tr.get_or_add_trPr().append(OxmlElement('w:tblHeader'))
        for j, (cell, text) in enumerate(zip(row.cells, values)):
            cell.width = Mm(widths[j])
            cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
            props = cell._tc.get_or_add_tcPr()
            borders = OxmlElement('w:tcBorders')
            for edge in ['top', 'left', 'bottom', 'right']:
                element = OxmlElement('w:' + edge)
                for key, value in [('val', 'single'), ('sz', '5'), ('color', 'D9D9D9')]:
                    element.set(qn('w:' + key), value)
                borders.append(element)
            props.append(borders)
            shade = OxmlElement('w:shd')
            shade.set(qn('w:fill'), INK if i == 0 else ('F4F4F2' if i % 2 else 'FFFFFF'))
            props.append(shade)
            margins = OxmlElement('w:tcMar')
            for edge in ['top', 'bottom', 'left', 'right']:
                element = OxmlElement('w:' + edge)
                element.set(qn('w:w'), '105')
                element.set(qn('w:type'), 'dxa')
                margins.append(element)
            props.append(margins)
            paragraph = cell.paragraphs[0]
            paragraph.paragraph_format.space_after = Pt(0)
            paragraph.paragraph_format.line_spacing = Pt(14.5)
            run = paragraph.add_run(text)
            run.font.size = Pt(9.3)
            run.bold = i == 0
            run.font.color.rgb = RGBColor.from_string('FFFFFF' if i == 0 else INK)
    add_text(document, '').paragraph_format.space_after = Pt(0)


def add_link(document, label, url):
    paragraph = document.add_paragraph(style='Reference')
    link = OxmlElement('w:hyperlink')
    link.set(qn('r:id'), paragraph.part.relate_to(url, RELATIONSHIP_TYPE.HYPERLINK, is_external=True))
    run = OxmlElement('w:r')
    properties = OxmlElement('w:rPr')
    color = OxmlElement('w:color')
    color.set(qn('w:val'), RED)
    properties.append(color)
    run.append(properties)
    text = OxmlElement('w:t')
    text.text = label
    run.append(text)
    link.append(run)
    paragraph._p.append(link)


def diagram(kind, language, path):
    chinese = language == 'zh-CN'
    image = Image.new('RGB', (1500, 470), 'white')
    draw = ImageDraw.Draw(image)
    def label(text, x, y, size=29, color=INK):
        font = ImageFont.truetype(str(FONT), size)
        font.set_variation_by_name('Regular')
        draw.multiline_text((x, y), text, font=font, fill='#' + color, anchor='mm', align='center', spacing=8)
    def box(x, y, width, title, detail):
        draw.rounded_rectangle((x, y, x + width, y + 126), radius=14, fill='#F3F3F1')
        label(title, x + width / 2, y + 39, 31)
        label(detail, x + width / 2, y + 89, 23, GREY)
    def arrow(x, y, end, color=RED):
        draw.line((x, y, end, y), fill='#' + color, width=4)
        tail = end - 13 if end > x else end + 13
        draw.polygon([(end, y), (tail, y-8), (tail, y+8)], fill='#' + color)
    if kind == 'overview':
        draw.ellipse((30, 32, 1470, 385), outline='#' + RED, width=4)
        draw.rectangle((495, 5, 1005, 65), fill='white')
        label('Agent Loop', 750, 32, 36, RED)
        label('policy 决定下一步' if chinese else 'Policy routes the next step', 750, 95, 25, GREY)
        box(160, 160, 330, 'Duet', '理解并执行工作' if chinese else 'Understands and acts')
        box(585, 160, 330, 'Message Center', '收集和共享信息' if chinese else 'Organizes shared information')
        box(1010, 160, 330, 'LoopLab', '验收并提供证据' if chinese else 'Tests and returns evidence')
        label('员工设定目标并作关键判断' if chinese else 'People set goals and make key decisions', 750, 431, 29)
    elif kind == 'handoff':
        titles = ['鲍勃', '我', '我的 Duet', '鲍勃'] if chinese else ['Bob', 'Me', 'My Duet', 'Bob']
        details = ['向真人提出请求', '引用消息并委托', '制作并按指令发送', '使用收到的报表'] if chinese else ['Asks a colleague', 'Quotes and delegates', 'Creates and sends as asked', 'Uses the report']
        for i, (title, detail) in enumerate(zip(titles, details)):
            box(24+i*382, 124, 304, title, detail)
            if i < 3:
                arrow(338+i*382, 187, 391+i*382)
        label('委托内容包括做好报表并发给鲍勃' if chinese else 'The instruction includes both making the report and sending it to Bob', 750, 345, 29, RED)
    elif kind == 'topics':
        for x, title in [(60, 'Alex'), (1100, 'Sam')]:
            box(x, 14, 340, title, '真人群聊' if chinese else 'Human group chat')
            box(x, 289, 340, title + ' 的 Duet' if chinese else title + "'s Duet", '在主题中参与工作' if chinese else 'Works through the topic')
            draw.line((x+170, 152, x+170, 274), fill='#' + RED, width=4)
            draw.polygon([(x+170, 280), (x+162, 264), (x+178, 264)], fill='#' + RED)
        label('各自委托自己的 Duet' if chinese else 'Each person delegates to their own Duet', 750, 210, 29, RED)
        arrow(415, 77, 1085, GREY)
        label('真人之间交流' if chinese else 'People talk to people', 750, 46, 25)
        box(570, 289, 360, '共享主题留言板' if chinese else 'Shared topic board', '留言 · @ · 交付物' if chinese else 'Posts · @ mentions · Files')
        arrow(415, 353, 555)
        arrow(1085, 353, 945)
        label('共享历史与文件  ·  发起 Duet 管理主题' if chinese else 'Shared history and files  ·  The initiating Duet manages the topic', 750, 452, 23, GREY)
    elif kind == 'testing':
        titles = ['明确检查目标', '编排固定版本流程', '独立浏览器回放'] if chinese else ['Define expected behavior', 'Compose a versioned flow', 'Replay in a fresh browser']
        details = ['状态可见且导出正确', '复用操作节点', '断言 步骤 截图'] if chinese else ['Visible status and correct export', 'Reuse action nodes', 'Assertions steps and screenshots']
        for i, (title, detail) in enumerate(zip(titles, details)):
            box(20+i*510, 90, 445, title, detail)
            if i < 2:
                arrow(475+i*510, 153, 520+i*510)
        label('失败证据交回修复' if chinese else 'Failed checks return evidence for a fix', 410, 325, 29, RED)
        label('通过结果进入下一关' if chinese else 'Passing results move to the next step', 1110, 325, 29)
        label('同一份结果供人阅读 也供规则分发' if chinese else 'One result can inform a person and trigger a routing rule', 750, 414, 24, GREY)
    image.save(path)


if __name__ == '__main__':
    main()
