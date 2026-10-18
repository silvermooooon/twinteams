import json,re,html,subprocess,collections
from pathlib import Path
from lxml import etree
BASE=Path('/workspace/shared/twinteams/design-artifacts')
FILES=['runtime-fields.json','identity-message-fields.json','scheduling-fields.json','source-workspace-fields.json']
data=[]
for f in FILES:data+=json.loads((BASE/f).read_text())
lookup={t['name']:t for t in data}
assert len(lookup)==len(data)
groups=[
 ('identity','身份、配对与历史权限',['actors','owners','twins','chats','chat_memberships','history_grants']),
 ('messages','消息、版本与真人查看',['messages','message_revisions','message_origins','human_read_receipts','view_events']),
 ('routing','目标路由与接受去重',['presence_leases','routing_decisions','routing_targets','trigger_candidates','reply_intents']),
 ('replies','回复请求、FIFO 与执行映射',['reply_requests','reply_lanes','reply_attempts','chat_twin_sessions']),
 ('runtime','SDK 已有七表',['agent_sessions','agent_session_events','agent_session_archives','agent_runs','agent_audit_events','agent_model_usage','agent_session_checkpoints']),
 ('durability','租约、输入、通信与可选流',['agent_session_leases','agent_commands','agent_outbox','agent_inbox','agent_run_edges','agent_stream_events','outbox']),
 ('operations','工具账本、审批与通知',['agent_tool_operations','operation_attempts','agent_approvals','approval_events','approval_contexts','notifications','notification_deliveries']),
 ('context','上下文来源与摘要',['context_manifests','context_sources','summaries','summary_segments']),
 ('tracking','Tracking 与 Activity 来源',['tracking_topics','tracking_topic_revisions','tracking_entries','tracking_tombstones','run_origins']),
 ('workspace','文件工作空间与个人设置',['workspaces','workspace_nodes','file_versions','workspace_grants','owner_settings','owner_chat_settings'])]
assert set(sum([x[2] for x in groups],[]))==set(lookup), set(lookup)-set(sum([x[2] for x in groups],[]))
statuslabel={'existing':'SDK 现有','proposal':'业务提案','extension':'Runtime 增强提案','optional':'可选提案'}
kindlabel={'physical':'现有物理 FK','proposed':'拟议 FK','logical':'逻辑引用'}
colors={'existing':('#e6f4ee','#2b765c'),'proposal':('#e9efff','#4c65ad'),'extension':('#fff0dc','#9c6b25'),'optional':('#f1eafb','#8062ad')}
relation_count=0
for t in data:
 for r in t.get('relations',[]):
  relation_count+=1;r['rid']=f'R{relation_count:03d}'
# all fields have every required property, tenant scoping, full relationship keys.
issues=[]
for t in data:
 fs={f['name']:f for f in t['fields']}
 for f in t['fields']:
  for key in ('name','type','nullable','default','meaning','origin'):assert key in f,(t['name'],f,key)
 assert 'tenant_id' in fs and fs['tenant_id']['type']=='text' and not fs['tenant_id']['nullable'],t['name']
 assert 'tenant_id' in t['primary_key'],t['name']
 assert set(t['primary_key'])<=set(fs),t['name']
 for r in t.get('relations',[]):
  assert 'tenant_id' in r['columns'],(t['name'],r)
  # Polymorphic and external logical targets are permitted, but never rendered as a physical table.
  if r['target'] not in lookup:
   if r['kind']!='logical':issues.append((t['name'],r['target'],'missing target'))
   continue
  assert set(r['columns'])<=set(fs),(t['name'],r)
  assert set(r['target_columns'])<=set(f['name'] for f in lookup[r['target']]['fields']),(t['name'],r)
assert not issues,issues

def E(s):return html.escape(str(s),quote=True)
def q(s):return json.dumps(s,ensure_ascii=False)
def tablelink(name):return f'<a href="#table-{E(name)}">{E(name)}</a>'
def fq(t):return t['schema']+'.'+t['name']
def relkey(r):return '('+', '.join(r['columns'])+')'
def fieldflags(t,f):
 bits=[]
 if f['name'] in t['primary_key']:bits.append('PK')
 for r in t.get('relations',[]):
  if f['name']!='tenant_id' and f['name'] in r['columns']:bits.append(('FK ' if r['kind']!='logical' else 'REF ')+r['rid'])
 return ' · '.join(bits) or '—'

# Every outgoing relationship is rendered exactly once, partitioned for a legible chart.
chart_seen=[]
def render_graph(own,gid,num):
 nodes=set(own);rels=[]
 for n in own:
  for r in lookup[n].get('relations',[]):
   if r['target'] in lookup:nodes.add(r['target']);rels.append((n,r));chart_seen.append(r['rid'])
 dot=['digraph ER {','graph [rankdir=LR, bgcolor="transparent", pad="0.35", nodesep="0.32", ranksep="1.1", splines=polyline, outputorder=edgesfirst];','node [shape=plain, fontname="DejaVu Sans"];','edge [fontname="DejaVu Sans", fontsize=10, color="#7d8a9c", arrowsize=0.65, labeldistance=1.8, labelfloat=false];']
 for n in sorted(nodes):
  t=lookup[n];fill,stroke=colors[t['status']]
  ext=n not in own
  if ext:fill='#f4f6fa';stroke='#9ba6b6'
  keys=', '.join(t['primary_key'])
  # Split key lines instead of shrinking the font.
  keylines=[];cur=''
  for key in t['primary_key']:
   if len(cur)+len(key)>38:keylines.append(cur.rstrip(', '));cur=''
   cur+=key+', '
  keylines.append(cur.rstrip(', '))
  label=f'<TABLE BORDER="1" CELLBORDER="0" CELLSPACING="0" CELLPADDING="8" COLOR="{stroke}" BGCOLOR="{fill}"><TR><TD ALIGN="LEFT"><FONT POINT-SIZE="11" COLOR="{stroke}">{E(t["schema"])}'+(' · 引用' if ext else '')+f'</FONT></TD></TR><TR><TD ALIGN="LEFT"><B><FONT POINT-SIZE="14">{E(n)}</FONT></B></TD></TR><TR><TD ALIGN="LEFT"><FONT POINT-SIZE="11" COLOR="#435067">'+ '<BR ALIGN="LEFT"/>'.join('PK '+E(x) if i==0 else E(x) for i,x in enumerate(keylines))+'</FONT></TD></TR></TABLE>'
  dot.append(f'{q(n)} [label=<{label}>, URL="#table-{n}", tooltip={q(t["title"])}];')
 for n,r in rels:
  fs={f['name']:f for f in lookup[n]['fields']}
  nullable=any(fs[c]['nullable'] for c in r['columns'])
  typ=r['kind'];style='solid' if typ=='physical' else ('dashed' if typ=='proposed' else 'dotted')
  color='#26765a' if typ=='physical' else ('#5a71ac' if typ=='proposed' else '#9273a7')
  # Arrow points from foreign-key holder to referenced table. Endpoint counts describe relation multiplicity.
  tail='0..N';head='0..1' if nullable else '1'
  card=r.get('cardinality','N:1')
  if card in ('1:1','0..1:1','1:0..1'):tail='0..1'
  if card=='N:N':head='0..N'
  dot.append(f'{q(n)} -> {q(r["target"])} [style={style}, color="{color}", fontcolor="{color}", label="{r["rid"]}", taillabel="{tail}", headlabel="{head}", URL="#rel-{r["rid"]}", tooltip={q(relkey(r)+" → "+r["target"]+"("+", ".join(r["target_columns"])+")") }];')
 dot.append('}')
 src='\n'.join(dot)
 output=subprocess.check_output(['dot','-Tsvg'],input=src.encode()).decode()
 svg=output[output.index('<svg'):]
 svg=re.sub(r'id="([^"]+)"', lambda m: 'id="svg-'+gid+'-'+str(num)+'-'+m.group(1)+'"', svg)
 svg=re.sub(r'<svg width="([^"]+)" height="([^"]+)"',r'<svg class="er-svg" data-natural-width="\1" data-natural-height="\2"',svg,count=1)
 # SVG uses internal href anchors, no network requests.
 svg=svg.replace('xmlns:xlink="http://www.w3.org/1999/xlink"','xmlns:xlink="http://www.w3.org/1999/xlink" role="img" aria-label="'+E(gid)+'"')
 (BASE/f'er-{gid}-{num}.dot').write_text(src)
 return svg

CSS=r'''
:root{--ink:#17233b;--muted:#5f6c83;--line:#dce3ef;--bg:#f2f5fa;--blue:#455f9c;--paper:#fff}*{box-sizing:border-box}html{scroll-behavior:smooth;scroll-padding-top:68px}body{margin:0;background:var(--bg);color:var(--ink);font-family:Inter,"Noto Sans CJK SC","Microsoft YaHei",system-ui,sans-serif;font-size:14px;line-height:1.6}a{color:#315aac;text-decoration:none}a:hover{text-decoration:underline}header{background:#162840;color:white;padding:42px max(28px,calc((100vw - 1440px)/2));border-bottom:5px solid #84c4af}header .kicker{font-size:11px;letter-spacing:2px;color:#b8d3e7}h1{font-size:31px;line-height:1.25;margin:9px 0 12px;font-weight:650}header p{color:#d4deea;margin:0;font-size:13px}nav{position:sticky;top:0;z-index:4;background:#ffffffed;backdrop-filter:blur(12px);border-bottom:1px solid var(--line);padding:11px max(28px,calc((100vw - 1440px)/2));display:flex;gap:20px;align-items:center;font-size:13px}nav input{margin-left:auto;min-width:230px;background:#f3f6fb;border:1px solid #dce3ed;border-radius:6px;padding:8px 10px;color:var(--ink);font:inherit}main{max-width:1500px;padding:28px;margin:auto}h2{font-size:23px;margin:14px 0 18px;letter-spacing:-.4px}h2 .number{font-size:12px;font-weight:500;letter-spacing:2px;color:#7887a0;display:block;margin-bottom:3px}h3{font-size:18px;margin:0 0 8px}p{margin:8px 0}.legend{background:white;padding:19px 22px;border:1px solid var(--line);border-radius:10px;margin-bottom:25px}.legend-row{display:flex;gap:12px;flex-wrap:wrap;margin:8px 0}.badge{display:inline-block;border-radius:4px;padding:2px 8px;font-size:11px;font-weight:600;line-height:1.7;vertical-align:middle;background:#edf1f7;color:#516179}.existing{background:#e6f4ee;color:#2b765c}.proposal{background:#e9efff;color:#4c65ad}.extension{background:#fff0dc;color:#9c6b25}.optional{background:#f1eafb;color:#8062ad}.small{font-size:12px;color:var(--muted)}.mini-line{display:inline-block;vertical-align:middle;width:34px;border-top:2px solid #26765a;margin-right:6px}.mini-line.proposed{border-color:#5a71ac;border-top-style:dashed}.mini-line.logical{border-color:#9273a7;border-top-style:dotted}.group-links{display:flex;flex-wrap:wrap;gap:6px 15px;font-size:12px;padding-top:10px;border-top:1px solid var(--line);margin-top:14px}.diagram{background:white;border:1px solid var(--line);border-radius:10px;padding:22px;margin-bottom:20px;break-inside:avoid}.diagram h3{font-size:17px}.diagram-top{display:flex;justify-content:space-between;gap:15px;flex-wrap:wrap}.diagram .members{font-size:11px;color:var(--muted);margin:0 0 14px}.diagram .members a{margin-right:12px}.svg-scroll{overflow:auto;border:1px solid #e8edf5;border-radius:6px;background:linear-gradient(90deg,#fbfcff,#fff);padding:10px}.er-svg{display:block;width:100%;height:auto;min-width:760px;max-height:none}.table-card{background:white;border:1px solid var(--line);border-radius:10px;margin:18px 0 25px;overflow:hidden;break-before:auto}.table-head{padding:20px 22px 14px;border-bottom:1px solid var(--line);background:#fbfcff}.table-head h3{font-family:ui-monospace,"SFMono-Regular",Consolas,monospace;font-size:18px;word-break:break-word}.table-head .zh-title{font-family:inherit;font-size:15px;font-weight:500;color:#53627a;margin:6px 0}.table-head .topline{display:flex;justify-content:space-between;gap:10px;align-items:flex-start}.table-head .source{font-size:11px;color:#8390a5;margin-top:8px}.table-scroll{overflow-x:auto}.fields{width:100%;border-collapse:collapse;font-size:12px;table-layout:fixed}.fields th{background:#eef2f8;color:#5b6b84;font-size:11px;letter-spacing:.2px;padding:9px 12px;text-align:left;position:static}.fields td{padding:9px 12px;border-bottom:1px solid #edf0f5;vertical-align:top;overflow-wrap:anywhere}.fields tr:last-child td{border-bottom:0}.fields tbody tr:nth-child(even){background:#fafbfd}.fields td:first-child{font-family:ui-monospace,"SFMono-Regular",Consolas,monospace;color:#24395f;font-weight:600}.fields .type{font-family:ui-monospace,monospace;color:#555f73}.fields .keys{font-size:10px;color:#607299}.fields col:nth-child(1){width:19%}.fields col:nth-child(2){width:11%}.fields col:nth-child(3){width:6%}.fields col:nth-child(4){width:13%}.fields col:nth-child(5){width:13%}.fields col:nth-child(6){width:38%}.origin{display:inline-block;font-size:9px;line-height:1.5;border:1px solid #dbc6ab;color:#9a6a31;background:#fff7ec;border-radius:3px;padding:0 4px;margin-left:5px;font-family:inherit;font-weight:400}.constraints{padding:13px 22px;background:#fcfdff;border-top:1px solid var(--line);font-size:12px}.constraints .label{font-size:10px;letter-spacing:1px;color:#78869b;font-weight:700;margin-bottom:4px}.constraints ul{margin:5px 0 12px;padding-left:18px}.constraints li{padding:2px 0;overflow-wrap:anywhere}.relation-list{list-style:none!important;padding-left:0!important}.relation-list li{border-left:2px solid #b4c2d9;padding:5px 10px;margin:5px 0;background:#f5f7fb}.relation-list li.physical{border-color:#26765a}.relation-list li.logical{border-color:#9273a7}.rel-id{font-family:ui-monospace,monospace;font-size:10px;background:#e5ebf5;color:#4c607f;padding:1px 4px;border-radius:3px;display:inline-block;margin-right:5px}.subgroup{margin-top:38px;margin-bottom:12px;padding-bottom:10px;border-bottom:2px solid #cbd6e6}.subgroup span{font-size:11px;color:#8a97aa;margin-left:12px}.empty{display:none;background:white;padding:25px;border-radius:8px}.hide{display:none!important}.back-top{font-size:11px;white-space:nowrap}footer{font-size:11px;color:#7e8ba0;text-align:center;padding:20px 0 38px}.jump{display:inline-block;margin:3px 12px 3px 0;font-family:ui-monospace,monospace;font-size:11px}.index{background:white;border:1px solid var(--line);border-radius:8px;padding:18px 22px;margin-bottom:20px}.index summary{cursor:pointer;color:#526b94;font-size:13px}.print-btn{border:1px solid #d6deeb;background:white;color:#526b94;padding:7px 11px;border-radius:5px;font:inherit;cursor:pointer}.count{font-size:12px;color:#8b98ac}.note-inline{font-size:10px;color:#926b3d}
@media(max-width:800px){header{padding:27px 18px}h1{font-size:24px}nav{padding:9px 14px;gap:14px;flex-wrap:wrap}nav input{min-width:0;width:100%;order:4}main{padding:14px}.legend,.diagram{padding:16px}.diagram h3{font-size:15px}.table-head{padding:16px}.fields{min-width:900px}.constraints{padding:12px 16px}.table-head h3{font-size:16px}.er-svg{min-width:720px}.print-btn{margin-left:auto}}
@media print{@page{size:A3 landscape;margin:12mm}html{scroll-padding:0}body{background:white;font-size:10pt}header{padding:15px 0;background:white;color:#17233b;border-bottom:2px solid #2b765c}header p,header .kicker{color:#58647b}h1{font-size:25px}nav,.print-btn,.back-top,#empty,footer{display:none!important}main{padding:0;max-width:none}.legend{padding:12px 14px;margin:15px 0}.diagram{padding:12px;margin:12px 0;break-inside:avoid;page-break-inside:avoid}.svg-scroll{overflow:visible;padding:0;border:0}.er-svg{width:100%;min-width:0;max-height:235mm}.table-card{break-inside:auto;margin:14px 0;overflow:visible}.table-head{break-after:avoid;padding:12px 14px}.table-scroll{overflow:visible}.fields{min-width:0;font-size:9pt}.fields th{font-size:8pt}.fields td{padding:6px 8px}.fields thead{display:table-header-group}.fields tr{break-inside:avoid}.constraints{padding:10px 14px;font-size:9pt;break-inside:auto}.constraints li{break-inside:avoid}a{color:inherit}h2,.subgroup{break-after:avoid}.index{display:none}.hide{display:block!important}#dictionary{break-before:page}}
'''
parts=[f'<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Twinteams · 数据库 ER 图与字段字典 v1</title><style>{CSS}</style></head><body><header id="top"><div class="kicker">TWINTEAMS / DATABASE / V1 · 2026-10-17</div><h1>数据库 ER 图与完整字段字典</h1><p>{len(data)} 张表 · {sum(len(t["fields"]) for t in data)} 个字段 · SDK 现有结构与设计提案分开标注 · 离线 HTML</p></header><nav><a href="#relationships">ER 关系图</a><a href="#dictionary">字段字典</a><button class="print-btn" onclick="window.print()">打印</button><input id="filter" type="search" placeholder="搜索表名、字段或中文含义" aria-label="搜索字段字典"></nav><main>']
parts.append('<section class="legend"><div class="legend-row">'+''.join(f'<span class="badge {k}">{v}</span>' for k,v in statuslabel.items())+'</div><div class="legend-row small"><span><i class="mini-line"></i>现有物理 FK</span><span><i class="mini-line proposed"></i>拟议 FK</span><span><i class="mini-line logical"></i>逻辑引用（非数据库 FK）</span></div><p class="small">箭头从引用字段指向被引用表；端点标注关系基数，1 = 恰一，0..1 = 可空/至多一，0..N = 多条。R 编号对应字段字典的完整复合引用；灰色节点为该图跨组引用，点击节点可查看全部字段。</p><p class="small">RT 表示 SDK 原有 search_path 所在 schema，不代表已创建名为 runtime 的 schema。现有表逐列按 001 → 002 → 003 合并核对；其余全部是待评审首稿。所有拟议键均含 tenant_id，新增业务 ID 使用 uuid，Runtime 标识保持 text。</p><p class="small">新增表的类型、NULL、默认值和约束均为提案；“展开”进一步标记原文压缩字段的拆分、补充字段或生命周期修正。默认值“无”表示不设数据库 DEFAULT；不会以默认值代替待定产品决策。可选方案尚未启用。</p></section>')
parts.append('<section id="relationships"><h2><span class="number">01 / RELATIONSHIPS</span>分组 ER 关系图</h2><div class="group-links">'+''.join(f'<a href="#er-{gid}">{i+1:02d} {title}</a>' for i,(gid,title,_) in enumerate(groups))+'</div>')
chartcount=0
for gi,(gid,title,names) in enumerate(groups,1):
 # Greedy split by relationship density. Keep every source table once.
 chunks=[];current=[]
 for n in names:
  candidate=current+[n]
  targets={r['target'] for x in candidate for r in lookup[x].get('relations',[]) if r['target'] in lookup}
  edges=sum(len(lookup[x].get('relations',[])) for x in candidate)
  if current and (len(set(candidate)|targets)>15 or edges>20 or len(candidate)>6):chunks.append(current);current=[n]
  else:current=candidate
 if current:chunks.append(current)
 for ci,chunk in enumerate(chunks,1):
  chartcount+=1
  suffix=f' · {ci}/{len(chunks)}' if len(chunks)>1 else ''
  svg=render_graph(chunk,gid,ci)
  parts.append(f'<article class="diagram" id="er-{gid if ci==1 else gid+"-"+str(ci)}"><div class="diagram-top"><h3>{gi:02d} / {E(title)}{suffix}</h3><a class="back-top" href="#dictionary">查看字段字典 →</a></div><p class="members">'+''.join(tablelink(x) for x in chunk)+'</p><div class="svg-scroll">'+svg+'</div></article>')
parts.append('</section><section id="dictionary"><h2><span class="number">02 / FIELD DICTIONARY</span>完整字段字典</h2><details class="index"><summary>展开全部表索引</summary>'+''.join(f'<a class="jump" href="#table-{E(t["name"])}">{E(fq(t))}</a>' for t in data)+'</details><div id="empty" class="empty">没有匹配的表或字段。</div>')
for gi,(gid,title,names) in enumerate(groups,1):
 parts.append(f'<h3 class="subgroup" data-group="{gid}">{gi:02d} / {E(title)} <span>{len(names)} 张表</span></h3>')
 for n in names:
  t=lookup[n];search=' '.join([n,t['title'],t.get('purpose','')]+[f['name']+' '+f['meaning'] for f in t['fields']]).lower()
  parts.append(f'<article class="table-card" id="table-{n}" data-group="{gid}" data-search="{E(search)}"><div class="table-head"><div class="topline"><div><h3>{E(fq(t))}</h3><div class="zh-title">{E(t["title"])}</div></div><span class="badge {t["status"]}">{statuslabel[t["status"]]}</span></div><p class="small">{E(t.get("purpose",""))}</p><div class="small">PK ({E(", ".join(t["primary_key"]))}) · {len(t["fields"])} 字段</div><div class="source">依据：{E(t["source"])}</div></div><div class="table-scroll"><table class="fields"><colgroup><col><col><col><col><col><col></colgroup><thead><tr><th>字段 / COLUMN</th><th>类型 / TYPE</th><th>可空</th><th>主键 / 引用</th><th>默认值</th><th>含义</th></tr></thead><tbody>')
  for f in t['fields']:
   origin='<span class="origin">展开</span>' if f['origin']=='expanded' else ''
   flags=fieldflags(t,f)
   flags=re.sub(r'(R\d{3})',r'<a href="#rel-\1">\1</a>',E(flags))
   parts.append(f'<tr><td>{E(f["name"])}{origin}</td><td class="type">{E(f["type"])}</td><td>{"是" if f["nullable"] else "否"}</td><td class="keys">{flags}</td><td>{E(f["default"])}</td><td>{E(f["meaning"])}</td></tr>')
  parts.append('</tbody></table></div><div class="constraints"><div class="label">约束 / CONSTRAINTS'+(' · 当前 SQL' if t['status']=='existing' else ' · 首稿提案')+'</div><ul>')
  for c in t.get('constraints',[]):parts.append('<li>'+E(c)+'</li>')
  if not t.get('constraints'):parts.append('<li>除上列主键及下列引用外，无附加约束。'+('' if t['status']=='existing' else '状态和生命周期约束仍需 DDL 评审。')+'</li>')
  parts.append('</ul>')
  if t.get('indexes'):
   parts.append('<div class="label">索引 / INDEXES</div><ul>'+''.join('<li>'+E(c)+'</li>' for c in t['indexes'])+'</ul>')
  parts.append('<div class="label">关系 / REFERENCES</div><ul class="relation-list">')
  for r in t.get('relations',[]):
   target=tablelink(r['target']) if r['target'] in lookup else E(r['target'])
   parts.append(f'<li class="{r["kind"]}" id="rel-{r["rid"]}"><span class="rel-id">{r["rid"]}</span><strong>{kindlabel[r["kind"]]}</strong> · {E(relkey(r))} → {target} ({E(", ".join(r["target_columns"]))})<br><span class="small">基数 {E(r.get("cardinality","N:1"))}'+('；'+E(r['note']) if r.get('note') else '')+'</span></li>')
  if not t.get('relations'):parts.append('<li>本表无外向表引用。</li>')
  parts.append('</ul></div></article>')
parts.append('</section></main><footer>Twinteams · ER 与字段字典 v1 · 2026-10-17</footer><script>const q=document.getElementById("filter");q.addEventListener("input",()=>{const s=q.value.trim().toLowerCase();let n=0;document.querySelectorAll(".table-card").forEach(x=>{const hit=!s||x.dataset.search.includes(s);x.classList.toggle("hide",!hit);if(hit)n++});document.querySelectorAll(".subgroup").forEach(h=>{h.classList.toggle("hide",![...document.querySelectorAll(".table-card[data-group=\\""+h.dataset.group+"\\"]")].some(x=>!x.classList.contains("hide")))});document.getElementById("empty").style.display=n?"none":"block"});document.querySelectorAll("a[href^=\\"#table-\\"]").forEach(a=>a.addEventListener("click",()=>{if(q.value){q.value="";q.dispatchEvent(new Event("input"))}}));</script></body></html>')
result='\n'.join(parts)
output=BASE/'Twinteams-Database-ER-Fields-v1.html';output.write_text(result)
(BASE/'database-fields-all-v1.json').write_text(json.dumps(data,ensure_ascii=False,indent=2))
expectedrels=[r['rid'] for t in data for r in t.get('relations',[]) if r['target'] in lookup]
assert sorted(chart_seen)==sorted(expectedrels)
# Verify anchors and static field coverage.
doc=etree.HTML(result);ids=doc.xpath('//@id');assert len(ids)==len(set(ids))
links=doc.xpath('//@href')+doc.xpath('//@*[local-name()="href" and namespace-uri()!=""]')
broken=[x for x in links if x.startswith('#') and x[1:] not in set(ids)];assert not broken,broken
assert len(doc.xpath('//article[contains(@class,"table-card")]'))==len(data)
assert len(doc.xpath('//table[@class="fields"]/tbody/tr'))==sum(len(t['fields']) for t in data)
external=doc.xpath('//script[@src]|//link[@href]|//img[starts-with(@src,"http")]');assert not external
report={'tables':len(data),'fields':sum(len(t['fields']) for t in data),'relations':relation_count,'charted_relations':len(chart_seen),'charts':chartcount,'existing':sum(t['status']=='existing' for t in data),'statuses':dict(collections.Counter(t['status'] for t in data)),'bytes':output.stat().st_size,'checks':['tenant-scoped primary/reference keys','all columns represented','all known table references charted exactly once','all internal anchors resolve','no external assets'],'output':str(output)}
(BASE/'er-fields-verification.json').write_text(json.dumps(report,ensure_ascii=False,indent=2));print(json.dumps(report,ensure_ascii=False,indent=2))
