"""Ground daily AI jobs in server snapshots and apply only validated proposals."""
import copy
import json

import daily_plan_store as store
import daily_planner as planner
from ai_planning_prompts import SCHEMAS, validate_schema


def prepare(s, db_path, data, user_id):
    plan=store.get(s,db_path,data.get('city_id'),data.get('start_date'))
    if data.get('day_version')!=plan['version']:
        raise s.ApiError(409,'每日计划已变化，请载入最新版本后生成。')
    refs, places = {}, []
    for i,v in enumerate(plan['visits']):
        ref=f'v{i+1}'
        refs[ref]={'type':'visit','id':v['id'],'data':v}
        places.append({'ref':ref,'name':v['title'],'priority':v['priority'],
                       'duration_minutes':v['duration_minutes'],'duration_source':v['duration_source'],
                       'time_block':v['time_block'],'opening_start':v['opening_start'],'opening_end':v['opening_end'],
                       'visit_kind':v['visit_kind'],'location':v.get('poi',{}).get('name','') if v.get('poi') else v.get('location',''),
                       'block_locked':v['block_locked'],'is_backup':v['is_backup'],
                       'notes':v.get('notes',''),'category':v.get('category','')})
    current=s.snapshot(db_path,city_id=plan['city_id'])
    for i,v in enumerate(current['items']['attraction']):
        if any(v['name'] in visit.get('attraction_names',[]) for visit in plan['visits']):
            continue
        ref=f'p{i+1}'
        refs[ref]={'type':'attraction','id':v['id'],'data':v}
        places.append({'ref':ref,'name':v['name'],'description':v.get('description',''),
                       'duration':v.get('duration',''),'itinerary_dates':[r['date'] for r in v.get('itinerary_refs',[])]})
    city=next(c['name'] for c in current['cities'] if c['id']==plan['city_id'])
    context={'plan':plan,'refs':refs,'candidate_map':{},'city':city}
    inverse={v['id']:k for k,v in refs.items() if v['type']=='visit'}
    context['input']={'city':city,'date':plan['date'],'places':places,
                       'selected_refs':[k for k,v in refs.items() if v['type']=='visit' and not v['data']['is_backup']],
                       'preserve_selection':data.get('preserve_selection',False),
                       'visits':[p for p in places if p['ref'].startswith('v')],
                       'night_enabled':plan['settings']['night_enabled'], 'time_blocks':planner.BLOCKS,
                       'locked_constraints':{'before':[[inverse.get(a),inverse.get(b)] for a,b in plan['settings']['before']]},
                       'requirements':data.get('requirements',''),'pace':plan['settings']['pace']}
    if type(context['input']['preserve_selection']) is not bool:
        raise s.ApiError(400,'保留地点开关格式无效。')
    if data['purpose']=='daily_hours':
        target_id=data.get('visit_id')
        if target_id is not None and (not isinstance(target_id,str) or target_id not in inverse):
            raise s.ApiError(400,'请指定当天已保存的行程。')
        context['input']['hours_targets']=[p for p in places if p['ref'].startswith('v')
            and not p['opening_start'] and not p['opening_end'] and p['visit_kind'] not in {'legacy','rest','transit'}
            and (target_id is None or refs[p['ref']]['id']==target_id)]
        if not context['input']['hours_targets']:
            raise s.ApiError(400,'该范围的开放时间已填写，或行程不是可查询开放时间的具体地点。')
    if data['purpose']=='daily_duration':
        context['input']['duration_targets']=[p for p in places if p['ref'].startswith('v') and p['duration_minutes'] is None]
        if not context['input']['duration_targets']:
            raise s.ApiError(400,'当天已填写所有停留时长，可直接在地点编辑中调整。')
    if data['purpose']=='daily_choose':
        candidates=[]
        refs_requested=data.get('candidate_refs',[])
        if not isinstance(refs_requested,list) or not 1<=len(refs_requested)<=2:
            raise s.ApiError(400,'请先核算候选路线。')
        for i,ref in enumerate(refs_requested):
            record=store.candidate(s,db_path,user_id,ref)
            if any(record['plan'][k]!=plan[k] for k in ('city_id','date','version')):
                raise s.ApiError(409,'候选与当前计划不一致，请重新核算。')
            key=f'c{i+1}'
            context['candidate_map'][key]=ref
            ev=copy.deepcopy(record['candidate']['evaluation'])
            ev.pop('legs',None); ev.pop('rows',None)
            for j,issue in enumerate(ev['issues']):
                issue['ref']=f'{key}_issue{j+1}'
                issue['visit_ref']=inverse.get(issue.get('visit_ref'))
            candidates.append({'candidate_ref':key,'order':[inverse[x] for x in record['candidate']['order']],**ev})
        context['input']['candidates']=candidates
        context['input']['adjustment_options']=[]
    if len(json.dumps(context['input'],ensure_ascii=False).encode())>48*1024:
        raise s.ApiError(400,'地点上下文过长，请缩小地点清单后再生成。')
    return context


def validate(value, purpose, context):
    validate_schema(value,SCHEMAS[purpose])
    refs, inp=context['refs'],context['input']
    if purpose=='daily_hours':
        ids=[item['place_ref'] for item in value['hours']]
        if len(ids)!=len(set(ids)) or set(ids)!={p['ref'] for p in inp['hours_targets']}:
            raise ValueError('只能为指定的缺失开放时间行程各补充一次。')
        for item in value['hours']:
            a,b=item['opening_start'],item['opening_end']
            if bool(a)!=bool(b) or (a and (planner.minutes(a) is None or planner.minutes(b) is None or planner.minutes(a)>=planner.minutes(b))):
                raise ValueError('开放与关闭时间须同时留空，或填写有效的同日时间范围。')
            if not item['note'].strip():
                raise ValueError('AI 开放时间须附带待核实说明。')
    elif purpose=='daily_duration':
        ids=[item['place_ref'] for item in value['durations']]
        targets={p['ref'] for p in inp['duration_targets']}
        if len(ids)!=len(set(ids)) or set(ids)!=targets:
            raise ValueError('只能为每个缺失时长的活动补充一次建议，不能修改已填写的时长。')
    elif purpose=='daily_select':
        selected=value['selected_places']
        ids=[p['place_ref'] for p in selected]
        if len(set(ids))!=len(ids) or any(x not in refs for x in ids):
            raise ValueError('AI 引用了不存在或重复的地点。')
        must={k for k,v in refs.items() if v['type']=='visit' and (v['data']['priority']=='must' or v['data']['block_locked']) and not v['data']['is_backup']}
        if not must.issubset(ids):
            raise ValueError('AI 遗漏了必去地点。')
        if inp['preserve_selection'] and (set(ids)!=set(inp['selected_refs']) or value['new_place_suggestions']):
            raise ValueError('AI 不得改变已选地点集合。')
        for p in selected:
            original=refs[p['place_ref']]
            if p['duration_source']=='user' and (original['type']!='visit' or original['data']['duration_source']!='user' or original['data']['duration_minutes'] is None):
                raise ValueError('AI 估计不能标为用户设置。')
            if p['duration_source']=='supplied_data' and (original['type']!='visit' or original['data']['duration_source']!='supplied_data' or original['data']['duration_minutes']!=p['duration_minutes_estimate']):
                raise ValueError('AI 估计不能标为已知资料。')
            if original['type']=='visit':
                v=original['data']
                if v['priority']=='must' and p['priority']!='must':
                    raise ValueError('AI 不得降低必去地点优先级。')
                if v['duration_source']=='user' and v['duration_minutes'] is not None and p['duration_minutes_estimate']!=v['duration_minutes']:
                    raise ValueError('AI 不得改写用户设置的停留时长。')
                if v['block_locked'] and p['preferred_blocks']!=[v['time_block']]:
                    raise ValueError('AI 不得改变锁定时段。')
            if 'night' in p['preferred_blocks'] and not inp['night_enabled']:
                raise ValueError('AI 安排了未开启的夜游。')
        if any(p['city']!=context['city'] for p in value['new_place_suggestions']):
            raise ValueError('新地点不属于当前城市。')
        if any(q['place_ref'] is not None and q['place_ref'] not in refs for q in value['questions']):
            raise ValueError('AI 问题引用无效。')
    elif purpose=='daily_choose':
        candidates={c['candidate_ref']:c for c in inp['candidates']}
        issue_refs={i['ref'] for c in candidates.values() for i in c['issues']}
        chosen=value['recommended_candidate_ref']
        if chosen is not None:
            c=candidates.get(chosen)
            if not c or c['route_status']!='checked' or c['time_fit'] not in {'fits','tight'} or c['constraint_status']=='conflict':
                raise ValueError('AI 推荐的方案尚未通过路线与容量校验。')
        allowed=set(candidates)|issue_refs|set(refs)
        if any(r not in allowed for reason in value['reasons'] for r in reason['evidence_refs']) or value['adjustment_refs'] or any(r not in issue_refs for r in value['blocking_issue_refs']):
            raise ValueError('AI 解释引用无效。')
    else:
        proposal(value,context)  # Validate operations, locks and references now, not just on save.
    result={**value,'notices':[], 'reference_labels':{k:v['data'].get('title',v['data'].get('name','')) for k,v in refs.items()}}
    if purpose=='daily_select':
        result['moved_to_backup']=[k for k in inp['selected_refs'] if k not in ids]
    if purpose=='daily_choose' and chosen is not None:
        result['recommended_order']=[result['reference_labels'][ref] for ref in candidates[chosen]['order']]
    return result


def proposal(result, context):
    plan, refs=context['plan'], context['refs']
    data={'city_id':plan['city_id'],'date':plan['date'],'version':plan['version'],'updates':[],'settings':{}}
    changes={}
    for op in result['operations']:
        name,ref,value=op['op'],op['place_ref'],op['value']
        if name!='set_order' and op['order']:
            raise ValueError('只有排序操作允许提供顺序。')
        if name in {'set_pace','set_night','set_order'}:
            if ref is not None:
                raise ValueError('每日设置不能引用单个地点。')
            if name=='set_pace':
                if value not in {'relaxed','balanced','packed'}: raise ValueError('节奏无效。')
                data['settings'].update(pace=value,slack_target={'relaxed':90,'balanced':60,'packed':30}[value])
            elif name=='set_night':
                if value not in {'true','false'}: raise ValueError('夜游开关无效。')
                data['settings'].update(night_enabled=value=='true',end_time='22:00' if value=='true' else '20:00')
            else:
                if value!='' or len(op['order'])!=len(plan['visits']) or set(op['order'])!={k for k,v in refs.items() if v['type']=='visit'}:
                    raise ValueError('AI 排序未包含所有活动。')
                data['order']=[refs[x]['id'] for x in op['order']]
                for a,b in plan['settings']['before']:
                    if data['order'].index(a)>=data['order'].index(b): raise ValueError('AI 排序违背先后约束。')
                if plan['settings']['order_mode']=='manual':
                    raise ValueError('当前采用手动顺序，请使用优化顺序入口预览新的排序。')
        else:
            if ref not in refs or refs[ref]['type']!='visit': raise ValueError('活动引用无效。')
            visit=refs[ref]['data']
            patch=changes.setdefault(visit['id'],{})
            if name=='move_to_block':
                if value not in planner.BLOCK_IDS or (value=='night' and not plan['settings']['night_enabled']): raise ValueError('时段无效。')
                if visit['block_locked']: raise ValueError('不能修改已锁定活动的时段。')
                patch['time_block']=value
            elif name=='set_duration':
                if not value.isascii() or not value.isdigit(): raise ValueError('停留分钟数无效。')
                patch.update(duration_minutes=planner.integer(int(value),'停留分钟数',1),duration_source='ai_estimate')
            elif name=='set_backup':
                if value not in {'true','false'} or (value=='true' and (visit['priority']=='must')): raise ValueError('不能移走必去活动。')
                patch['is_backup']=value=='true'
    data['updates']=[{'id':ref,'changes':patch} for ref,patch in changes.items()]
    return data


def apply_job(s, service, db_path, data, user_id, device):
    with service._db() as db:
        row=service._owned_job(db,user_id,device,data.get('job_id'))
        if row['status'] not in {'ready','imported'}:
            raise s.ApiError(409,'请等待生成完成。')
        request=json.loads(row['request_json'])
        context=json.loads(row['context_json']).get('daily')
        result=json.loads(row['result_json'])
    if not context or any(data.get(k)!=context['plan'][k] for k in ('city_id','date','version')):
        raise s.ApiError(409,'AI 方案与当前日期或版本不一致。')
    purpose=request['purpose']
    # Validate stored output again; display-only notices are outside the provider schema.
    validate({k:v for k,v in result.items() if k in SCHEMAS[purpose]['properties']},purpose,context)
    if purpose=='daily_choose':
        chosen=result['recommended_candidate_ref']
        if not chosen: raise s.ApiError(400,'没有可应用的推荐候选。')
        return store.apply(s,db_path,{**data,'candidate_ref':context['candidate_map'][chosen]},user_id)
    if purpose=='daily_hours':
        change={'city_id':data['city_id'],'date':data['date'],'version':data['version'],
            'updates':[{'id':context['refs'][item['place_ref']]['id'],
                        'changes':{'opening_start':item['opening_start'],'opening_end':item['opening_end'],
                                   'opening_source':'ai_estimate','opening_note':item['note']}} for item in result['hours']]}
    elif purpose=='daily_duration':
        change={'city_id':data['city_id'],'date':data['date'],'version':data['version'],
            'updates':[{'id':context['refs'][item['place_ref']]['id'],
                        'changes':{'duration_minutes':item['duration_minutes'],'duration_source':'ai_estimate'}} for item in result['durations']]}
    elif purpose=='daily_adjust':
        change=proposal(result,context)
    else:
        change={'city_id':data['city_id'],'date':data['date'],'version':data['version'],'updates':[],'additions':[]}
        for selected in result['selected_places']:
            original=context['refs'][selected['place_ref']]
            patch={'duration_minutes':selected['duration_minutes_estimate'],'duration_source':selected['duration_source'],
                   'priority':selected['priority'],'time_block':next(iter(selected['preferred_blocks']),''),'is_backup':False}
            if original['type']=='visit':
                change['updates'].append({'id':original['id'],'changes':patch})
            else:
                name=original['data']['name']
                change['additions'].append({**patch,'title':name,'location':name,'attraction_names':[name],'visit_kind':'attraction'})
        selected_refs={v['place_ref'] for v in result['selected_places']}
        for ref in context['input']['selected_refs']:
            if ref not in selected_refs:
                change['updates'].append({'id':context['refs'][ref]['id'],'changes':{'is_backup':True}})
        # Suggestions are explicitly shown in the preview. Applying adds unresolved places, never invented POIs.
        for p in result['new_place_suggestions']:
            change['additions'].append({'title':p['name'],'location':p['name'],'visit_kind':p['kind'],
                                       'attraction_names':[p['name']] if p['kind']=='attraction' else [], 'notes':p['reason']})
    try:
        return store.save(s,db_path,change,user_id,receipt='ai:'+row['id'])
    except ValueError as exc:
        raise s.ApiError(400,str(exc)) from None
