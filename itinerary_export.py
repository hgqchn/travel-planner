"""Portable Office Open XML downloads; no runtime dependencies or saved files."""

from __future__ import annotations
import daily_planner
from route_image import route_map

import io
import math
import re
import unicodedata
import zipfile
from datetime import date
from itertools import groupby
from urllib.parse import urlsplit
from xml.sax.saxutils import escape, quoteattr


REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PKG = "http://schemas.openxmlformats.org/package/2006/relationships"
WORD = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
SHEET = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
MIME_TYPES = {
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}
XML_DECL = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'


def clean(value) -> str:
    # JSON can contain XML 1.0 control characters and unpaired surrogates.
    return re.sub(r"[^\x09\x0a\x0d\x20-\ud7ff\ue000-\ufffd\U00010000-\U0010ffff]", "", str(value or ""))


def text(value) -> str:
    return escape(clean(value))


def attr(value) -> str:
    return quoteattr(clean(value))


def safe_link(value) -> str:
    value = clean(value).strip()
    try:
        parsed = urlsplit(value)
        return value if parsed.scheme.lower() in {"https", "http"} and parsed.netloc else ""
    except ValueError:
        return ""


def filename(data: dict, file_format: str) -> str:
    base = f'{data["project_name"]}-{data["scope_name"]}-行程'
    base = re.sub(r'[<>:"/\\|?*\x00-\x1f\x7f]', "_", clean(base)).strip(" .")[:100]
    return f'{base}-{data["exported_at"][:10]}.{file_format}'


def relationships(entries) -> str:
    return f'<Relationships xmlns="{PKG}">' + "".join(
        f'<Relationship Id="{rid}" Type="{REL}/{kind}" Target={attr(target)}'
        + (' TargetMode="External"' if external else '') + '/>'
        for rid, kind, target, external in entries
    ) + '</Relationships>'


def package(parts: dict, content_types: dict, main: str) -> bytes:
    parts = dict(parts)
    parts['[Content_Types].xml'] = (
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        + ''.join(f'<Override PartName="/{name}" ContentType="{kind}"/>' for name, kind in content_types.items())
        + '</Types>'
    )
    parts['_rels/.rels'] = relationships([('rId1', 'officeDocument', main, False)])
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for path, xml in parts.items():
            archive.writestr(path, xml if isinstance(xml,bytes) else (XML_DECL + xml).encode('utf-8'))
    return output.getvalue()


def overview(data: dict) -> list:
    items = data['items']
    dates = sorted({item['date'] for item in items})
    return [
        ('旅行项目', data['project_name']), ('导出范围', data['scope_name']),
        ('行程日期', f'{dates[0]} 至 {dates[-1]}'),
        ('行程天数', len(dates)), ('安排数量', len(items)),
        ('导出时间', data['exported_at']),
        ('说明', '本文件包含导出时已保存的行程，可离线查看；后续修改请重新下载。'),
    ]


def word_runs(value, properties='') -> str:
    lines = clean(value).replace('\r\n', '\n').replace('\r', '\n').split('\n')
    return f'<w:r>{properties}' + '<w:br/>'.join(
        '<w:tab/>'.join(f'<w:t xml:space="preserve">{text(part)}</w:t>' for part in line.split('\t'))
        for line in lines
    ) + '</w:r>'


def paragraph(value, style='Normal') -> str:
    return f'<w:p><w:pPr><w:pStyle w:val="{style}"/></w:pPr>{word_runs(value)}</w:p>'


def guidance_scope(group) -> str:
    return '、'.join(group.get('dates', [])) or '城市出行提示'


def daily_summary(plan):
    settings=plan['settings']
    ev=plan.get('evaluation')
    lines=[f"{plan.get('city_name','')} {plan['date']} · 当天 {settings['start_time']}–{settings['end_time']}",
           f"午餐 {settings['lunch_minutes']} 分钟，晚餐 {settings['dinner_minutes']} 分钟，午休 {settings['rest_minutes']} 分钟；目标机动 {settings['slack_target']} 分钟。"]
    if not ev or ev['route_status']=='stale':
        lines.append('路线与时间预算待重新核算。')
    else:
        slack=ev['slack_minutes']
        lines.append(f"交通 {ev['travel_minutes']} 分钟，缓冲 {ev['buffer_minutes']} 分钟；"+(f"机动余量 {slack} 分钟。" if slack is not None else '机动余量未知。'))
        names={v['id']:v['title'] for v in plan.get('visits',[])}
        names.update({'@start':'出发地点','@end':'返回地点'})
        modes={'transit':'公交 / 地铁','walking':'步行','driving':'驾车','bicycling':'骑行'}
        for leg in ev.get('leg_summaries',[]):
            duration=leg['duration_minutes']
            lines.append(f"{names.get(leg['from_ref'],'上一站')} → {names.get(leg['to_ref'],'下一站')}：{modes[leg['mode']]}，"+(f"约 {duration} 分钟" if duration is not None else '耗时待核算'))
        lines.extend(issue['message'] for issue in ev['issues'])
    return lines


def build_docx(data: dict) -> bytes:
    images = {}
    body = [paragraph(f'{data["project_name"]} 行程安排', 'Title')]
    body.extend(paragraph(f'{label}：{value}') for label, value in overview(data))
    if data.get('travel_guidance'):
        body.append(paragraph('出行提示', 'Heading1'))
        body.append(paragraph('以下为随计划保存的 AI 摘要与注意事项。'))
        for group in data['travel_guidance']:
            body.append(paragraph(f'{group["city_name"]} · {guidance_scope(group)}', 'Heading2'))
            if group.get('summary'):
                body.append(paragraph(group['summary']))
            body.extend(paragraph(f'• {notice}') for notice in group.get('notices', []))
    rels = [('rId1', 'styles', 'styles.xml', False), ('rId2', 'footer', 'footer1.xml', False)]
    for day, entries in groupby(data['items'], key=lambda item: item['date']):
        body.append(paragraph(day, 'Heading1'))
        for plan in data.get('daily_plans',[]):
            if plan['date']==day:
                body.extend(paragraph(line) for line in daily_summary(plan))
                mapped = route_map(plan)
                if mapped:
                    png, legend = mapped
                    number = len(images)+1; image_path = f'word/media/route{number}.png'
                    images[image_path] = png
                    rid = f'rId{len(rels)+1}'; rels.append((rid,'image',f'media/route{number}.png',False))
                    body.append(paragraph(f"{plan.get('city_name','')} · 已保存路线图",'Heading2'))
                    body.append(paragraph('按已保存高德轨迹绘制，北向上，无街道底图；仅展示已规划路段，耗时为规划时参考。'))
                    body.append(word_picture(rid,number))
                    body.extend(paragraph(line) for line in legend)
        for item in entries:
            body.append(paragraph(f'{daily_planner.block_label(item) if item.get("plan_version")==2 else item.get("start_time") or "时间待定"}  {item["title"]}', 'Heading2'))
            details = [f'城市：{item["city_name"]}']
            if item.get('plan_version')==2:
                details.extend([f"顺序：{item.get('position','')}", f"建议停留：{item.get('duration_minutes') or '待确认'} 分钟", f"状态：{'备选' if item.get('is_backup') else '已安排'}"])
            for key, label in [('category', '类型'), ('location', '地点')]:
                if item.get(key):
                    details.append(f'{label}：{item[key]}')
            body.append(paragraph('　'.join(details)))
            if item.get('notes'):
                body.append(paragraph(f'备注：{item["notes"]}'))
            if item.get('link'):
                link = safe_link(item['link'])
                if link:
                    rid = f'rId{len(rels) + 1}'
                    rels.append((rid, 'hyperlink', link, True))
                    body.append('<w:p>' + word_runs('相关链接：') + f'<w:hyperlink r:id="{rid}">'
                                + word_runs(link, '<w:rPr><w:color w:val="245C78"/><w:u w:val="single"/></w:rPr>')
                                + '</w:hyperlink></w:p>')
                else:
                    body.append(paragraph(f'相关链接：{item["link"]}'))
    body.append('<w:sectPr><w:footerReference w:type="default" r:id="rId2"/>'
                '<w:pgSz w:w="11906" w:h="16838"/>'
                '<w:pgMar w:top="1134" w:right="1134" w:bottom="1134" w:left="1134" w:header="567" w:footer="567" w:gutter="0"/>'
                '</w:sectPr>')
    styles = f'<w:styles xmlns:w="{WORD}"><w:docDefaults><w:rPrDefault><w:rPr>'
    styles += '<w:rFonts w:ascii="Arial" w:hAnsi="Arial" w:eastAsia="Microsoft YaHei"/><w:color w:val="000000"/><w:sz w:val="22"/><w:lang w:val="zh-CN" w:eastAsia="zh-CN"/>'
    styles += '</w:rPr></w:rPrDefault><w:pPrDefault><w:pPr><w:spacing w:after="100" w:line="300" w:lineRule="auto"/></w:pPr></w:pPrDefault></w:docDefaults>'
    styles += '<w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/></w:style>'
    for style, name, size, before, outline in [('Title', 'Title', 38, 0, None), ('Heading1', 'heading 1', 29, 300, 0), ('Heading2', 'heading 2', 24, 180, 1)]:
        styles += f'<w:style w:type="paragraph" w:styleId="{style}"><w:name w:val="{name}"/><w:basedOn w:val="Normal"/><w:next w:val="Normal"/><w:pPr><w:keepNext/><w:spacing w:before="{before}" w:after="120"/>'
        if outline is not None:
            styles += f'<w:outlineLvl w:val="{outline}"/>'
        styles += f'</w:pPr><w:rPr><w:b/><w:color w:val="000000"/><w:sz w:val="{size}"/></w:rPr></w:style>'
    styles += '</w:styles>'
    parts = {
        'word/document.xml': f'<w:document xmlns:w="{WORD}" xmlns:r="{REL}"><w:body>{"".join(body)}</w:body></w:document>',
        'word/styles.xml': styles,
        'word/_rels/document.xml.rels': relationships(rels),
        'word/footer1.xml': f'<w:ftr xmlns:w="{WORD}"><w:p><w:pPr><w:jc w:val="center"/></w:pPr>' + word_runs('第 ') + '<w:fldSimple w:instr="PAGE"><w:r><w:t>1</w:t></w:r></w:fldSimple>' + word_runs(' 页') + '</w:p></w:ftr>',
    }
    parts.update(images)
    return package(parts, {
        **{name:'image/png' for name in images},
        'word/document.xml': MIME_TYPES['docx'] + '.main+xml',
        'word/styles.xml': 'application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml',
        'word/footer1.xml': 'application/vnd.openxmlformats-officedocument.wordprocessingml.footer+xml',
    }, 'word/document.xml')


def picture(rid, number):
    return (f'<pic:pic xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture" xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" xmlns:r="{REL}">'
            f'<pic:nvPicPr><pic:cNvPr id="{number}" name="路线图 {number}" descr="已保存路线轨迹与地点编号"/><pic:cNvPicPr/></pic:nvPicPr>'
            f'<pic:blipFill><a:blip r:embed="{rid}"/><a:stretch><a:fillRect/></a:stretch></pic:blipFill>'
            '<pic:spPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="6000000" cy="3600000"/></a:xfrm>'
            '<a:prstGeom prst="rect"><a:avLst/></a:prstGeom></pic:spPr></pic:pic>')


def word_picture(rid, number):
    return ('<w:p><w:r><w:drawing><wp:inline xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing" distT="0" distB="0" distL="0" distR="0">'
            f'<wp:extent cx="6000000" cy="3600000"/><wp:docPr id="{number}" name="路线图 {number}" descr="已保存路线轨迹与地点编号"/>'
            '<a:graphic xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"><a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/picture">'
            +picture(rid,number)+'</a:graphicData></a:graphic></wp:inline></w:drawing></w:r></w:p>')


def spreadsheet_text(value) -> str:
    # Excel interprets _xHHHH_ escapes even inside inline strings. Escape the
    # leading underscore to preserve a user's literal text (including _x005F_).
    return text(re.sub(r'_x[0-9a-fA-F]{4}_', lambda match: '_x005F_' + match[0][1:], clean(value)))


def cell(ref, value, style=0) -> str:
    if isinstance(value, (int, float)):
        return f'<c r="{ref}" s="{style}"><v>{value}</v></c>'
    return f'<c r="{ref}" s="{style}" t="inlineStr"><is><t xml:space="preserve">{spreadsheet_text(value)}</t></is></c>'


def row_height(values, widths) -> int:
    lines = max(sum(max(1, math.ceil(sum(2 if unicodedata.east_asian_width(char) in 'WF' else 1 for char in line) / (width - 2)))
                    for line in clean(value).splitlines() or ['']) for value, width in zip(values, widths))
    return min(409, max(30, lines * 16 + 10))


def excel_date(value):
    parsed = date.fromisoformat(value)
    if parsed < date(1900, 1, 1):
        return value
    # Excel's 1900 date system includes the fictitious 1900-02-29.
    return (parsed - date(1899, 12, 31)).days + (parsed >= date(1900, 3, 1))


def worksheet(rows, widths, *, links=(), filtered=False) -> str:
    end = f'{chr(64 + len(widths))}{len(rows)}'
    xml = f'<worksheet xmlns="{SHEET}" xmlns:r="{REL}"><dimension ref="A1:{end}"/>'
    xml += '<sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/></sheetView></sheetViews><sheetFormatPr defaultRowHeight="30"/>'
    xml += '<cols>' + ''.join(f'<col min="{i}" max="{i}" width="{width}" customWidth="1"/>' for i, width in enumerate(widths, 1)) + '</cols><sheetData>'
    xml += ''.join(rows) + '</sheetData>'
    if filtered:
        xml += f'<autoFilter ref="A1:{end}"/>'
    if links:
        xml += '<hyperlinks>' + ''.join(f'<hyperlink ref="{ref}" r:id="{rid}"/>' for ref, rid in links) + '</hyperlinks>'
    xml += '<pageMargins left="0.3" right="0.3" top="0.5" bottom="0.5" header="0.2" footer="0.2"/></worksheet>'
    return xml


def build_xlsx(data: dict) -> bytes:
    modern=any(item.get('plan_version')==2 for item in data['items'])
    widths = [14, 12, 24 if modern else 12, 32, 16, 30, 64, 44] + ([10,14,12] if modern else [])
    headers = ['日期', '城市', '偏好时段' if modern else '开始时间', '安排名称', '类型', '地点', '备注', '相关链接'] + (['顺序','停留分钟','状态'] if modern else [])
    rows = ['<row r="1" ht="32" customHeight="1">' + ''.join(cell(f'{chr(65 + i)}1', label, 1) for i, label in enumerate(headers)) + '</row>']
    links, rels = [], []
    for index, item in enumerate(data['items'], 2):
        time = item.get('start_time', '')
        time_value = (int(time[:2]) * 60 + int(time[3:])) / 1440 if time else ''
        values = [excel_date(item['date']), item['city_name'], time_value, item['title'], item.get('category', ''), item.get('location', ''), item.get('notes', ''), item.get('link', '')]
        styles = [2, 0, 3 if time else 0, 0, 0, 0, 0, 4 if safe_link(values[-1]) else 0]
        if modern:
            values[2]=daily_planner.block_label(item)
            styles[2]=0
            values += [item.get('position',''),item.get('duration_minutes') or '待确认','备选' if item.get('is_backup') else '已安排']
            styles += [0,0,0]
        rows.append(f'<row r="{index}" ht="{row_height(values, widths)}" customHeight="1">' + ''.join(cell(f'{chr(65 + i)}{index}', value, styles[i]) for i, value in enumerate(values)) + '</row>')
        if safe_link(item.get('link','')):
            rid = f'rId{len(rels) + 1}'
            links.append((f'H{index}', rid))
            rels.append((rid, 'hyperlink', safe_link(item.get('link','')), True))
    summary_rows = []
    for i, values in enumerate([('项目', '内容'), *overview(data)], 1):
        summary_rows.append(f'<row r="{i}" ht="{row_height(values, [18, 86])}" customHeight="1">' + cell(f'A{i}', values[0], 1) + cell(f'B{i}', values[1], 1 if i == 1 else 0) + '</row>')
    for plan in data.get('daily_plans',[]):
        i=len(summary_rows)+1
        summary_rows.append(f'<row r="{i}" ht="120" customHeight="1">'+cell(f'A{i}',plan['date'],1)+cell(f'B{i}','\n'.join(daily_summary(plan)))+'</row>')
    styles = f'<styleSheet xmlns="{SHEET}"><numFmts count="2"><numFmt numFmtId="164" formatCode="yyyy-mm-dd"/><numFmt numFmtId="165" formatCode="hh:mm"/></numFmts>'
    styles += '<fonts count="3"><font><sz val="11"/><name val="Microsoft YaHei"/></font><font><b/><color rgb="FFFFFFFF"/><sz val="11"/><name val="Microsoft YaHei"/></font><font><u/><color rgb="FF245C78"/><sz val="11"/><name val="Microsoft YaHei"/></font></fonts>'
    styles += '<fills count="3"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill><fill><patternFill patternType="solid"><fgColor rgb="FF123B3A"/><bgColor indexed="64"/></patternFill></fill></fills>'
    styles += '<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders><cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs><cellXfs count="5">'
    for numfmt, font, fill in [(0, 0, 0), (0, 1, 2), (164, 0, 0), (165, 0, 0), (0, 2, 0)]:
        styles += f'<xf numFmtId="{numfmt}" fontId="{font}" fillId="{fill}" borderId="0" xfId="0" applyAlignment="1" applyNumberFormat="1" applyFont="1" applyFill="1"><alignment vertical="top" wrapText="1"/></xf>'
    styles += '</cellXfs><cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles></styleSheet>'
    parts = {
        'xl/workbook.xml': f'<workbook xmlns="{SHEET}" xmlns:r="{REL}"><sheets><sheet name="行程明细" sheetId="1" r:id="rId1"/><sheet name="导出说明" sheetId="2" r:id="rId2"/></sheets></workbook>',
        'xl/_rels/workbook.xml.rels': relationships([('rId1', 'worksheet', 'worksheets/sheet1.xml', False), ('rId2', 'worksheet', 'worksheets/sheet2.xml', False), ('rId3', 'styles', 'styles.xml', False)]),
        'xl/styles.xml': styles,
        'xl/worksheets/sheet1.xml': worksheet(rows, widths, links=links, filtered=True),
        'xl/worksheets/sheet2.xml': worksheet(summary_rows, [18, 86]),
    }
    if rels:
        parts['xl/worksheets/_rels/sheet1.xml.rels'] = relationships(rels)
    content_types = {
        'xl/workbook.xml': MIME_TYPES['xlsx'] + '.main+xml',
        'xl/styles.xml': 'application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml',
        'xl/worksheets/sheet1.xml': 'application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml',
        'xl/worksheets/sheet2.xml': 'application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml',
    }
    if data.get('travel_guidance'):
        guidance_rows = [('城市', '适用日期', '提示类型', 'AI 生成的出行提示')]
        for group in data['travel_guidance']:
            prefix = (group['city_name'], guidance_scope(group))
            if group.get('summary'):
                guidance_rows.append((*prefix, '计划摘要', group['summary']))
            guidance_rows.extend((*prefix, '注意事项', notice) for notice in group.get('notices', []))
        guidance_widths = [14, 32, 16, 90]
        guidance_xml = [f'<row r="{i}" ht="{row_height(values, guidance_widths)}" customHeight="1">'
                        + ''.join(cell(f'{chr(65 + j)}{i}', value, 1 if i == 1 else 0)
                                  for j, value in enumerate(values)) + '</row>'
                        for i, values in enumerate(guidance_rows, 1)]
        parts['xl/worksheets/sheet3.xml'] = worksheet(guidance_xml, guidance_widths, filtered=True)
        parts['xl/workbook.xml'] = parts['xl/workbook.xml'].replace('</sheets>', '<sheet name="出行提示" sheetId="3" r:id="rId4"/></sheets>')
        parts['xl/_rels/workbook.xml.rels'] = relationships([
            ('rId1', 'worksheet', 'worksheets/sheet1.xml', False),
            ('rId2', 'worksheet', 'worksheets/sheet2.xml', False),
            ('rId3', 'styles', 'styles.xml', False),
            ('rId4', 'worksheet', 'worksheets/sheet3.xml', False),
        ])
        content_types['xl/worksheets/sheet3.xml'] = 'application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml'
    maps = [(plan,route_map(plan)) for plan in data.get('daily_plans',[])]
    maps = [(plan,mapped) for plan,mapped in maps if mapped]
    if maps:
        sheet_number = 4 if data.get('travel_guidance') else 3
        sheet_path = f'xl/worksheets/sheet{sheet_number}.xml'
        parts['xl/workbook.xml'] = parts['xl/workbook.xml'].replace('</sheets>',f'<sheet name="路线图" sheetId="{sheet_number}" r:id="rIdRoutes"/></sheets>')
        extra = relationships([('rIdRoutes','worksheet',f'worksheets/sheet{sheet_number}.xml',False)]).split('>',1)[1].removesuffix('</Relationships>')
        parts['xl/_rels/workbook.xml.rels'] = parts['xl/_rels/workbook.xml.rels'].replace('</Relationships>',extra+'</Relationships>')
        rows, anchors, image_rels = [], [], []
        row = 1
        for number,(plan,(png,legend)) in enumerate(maps,1):
            rows.append(f'<row r="{row}" ht="30" customHeight="1">'+cell(f'A{row}',f"{plan.get('city_name','')} {plan['date']} · 已保存路线图",1)+'</row>')
            row += 1
            rows.append(f'<row r="{row}" ht="36" customHeight="1">'+cell(f'A{row}','北向上，无街道底图；仅展示已规划路段，耗时为规划时参考。')+'</row>')
            anchors.append(f'<xdr:oneCellAnchor><xdr:from><xdr:col>0</xdr:col><xdr:colOff>0</xdr:colOff><xdr:row>{row}</xdr:row><xdr:rowOff>0</xdr:rowOff></xdr:from><xdr:ext cx="6000000" cy="3600000"/>'
                           +picture(f'rId{number}',number).replace('pic:','xdr:').replace('xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture"','')+'<xdr:clientData/></xdr:oneCellAnchor>')
            row += 12
            for line in legend:
                values = [line]
                rows.append(f'<row r="{row}" ht="{row_height(values,[110])}" customHeight="1">'+cell(f'A{row}',line)+'</row>'); row+=1
            row+=2
            path=f'xl/media/route{number}.png'; parts[path]=png; content_types[path]='image/png'
            image_rels.append((f'rId{number}','image',f'../media/route{number}.png',False))
        parts[sheet_path]=worksheet(rows,[110]).replace('<dimension ', '<sheetPr><pageSetUpPr fitToPage="1"/></sheetPr><dimension ').replace('</worksheet>',
            '<pageSetup paperSize="9" orientation="landscape" fitToWidth="1" fitToHeight="0"/><drawing r:id="rIdDrawing"/></worksheet>')
        parts[f'xl/worksheets/_rels/sheet{sheet_number}.xml.rels']=relationships([('rIdDrawing','drawing','../drawings/routes.xml',False)])
        parts['xl/drawings/routes.xml']='<xdr:wsDr xmlns:xdr="http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing">'+''.join(anchors)+'</xdr:wsDr>'
        parts['xl/drawings/_rels/routes.xml.rels']=relationships(image_rels)
        content_types[sheet_path]='application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml'
        content_types['xl/drawings/routes.xml']='application/vnd.openxmlformats-officedocument.drawing+xml'
    return package(parts, content_types, 'xl/workbook.xml')
