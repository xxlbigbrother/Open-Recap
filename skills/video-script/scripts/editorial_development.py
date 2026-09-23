"""Bind editorial observations to current evidence before drafting a story.

This checks declared provenance and unresolved premises, not semantic truth or
artistic quality. A ready observation may still need editing-time inspection.
"""
from copy import deepcopy
import hashlib
import json

from editorial_inputs import index_evidence


def evidence_digest(evidence):
    payload = json.dumps(evidence, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(payload.encode()).hexdigest()


def bind_development(proposal, project, evidence):
    if not isinstance(proposal, dict):
        raise ValueError('development proposal must be an object')
    return {**deepcopy(proposal), 'schema_version': 1, 'project_id': project['id'],
            'source_id': project['source']['id'], 'evidence_sha256': evidence_digest(evidence)}


def validate_development(data, project, evidence):
    findings = []

    def fail(code, path, message):
        findings.append({'severity': 'error', 'code': code, 'path': path, 'message': message})

    if not isinstance(data, dict):
        fail('development_shape', '$', '观察计划须为对象')
        return findings
    if data.get('schema_version') != 1:
        fail('development_shape', 'schema_version', '观察计划版本须为1')
    if data.get('project_id') != project['id'] or data.get('source_id') != project['source']['id']:
        fail('source_identity', '$', '观察计划属于其他项目或源片')
    if data.get('evidence_sha256') != evidence_digest(evidence):
        fail('stale_development', 'evidence_sha256', '依据已变化，重新核对观察和缺失前提后绑定当前证据')
    candidates = data.get('candidates')
    if not isinstance(candidates, list) or not candidates:
        fail('development_shape', 'candidates', '需要非空候选观察列表')
        return findings
    records = index_evidence(evidence)
    ids = set()
    for i, candidate in enumerate(candidates):
        path = f'candidates[{i}]'
        if not isinstance(candidate, dict):
            fail('development_shape', path, '候选须为对象')
            continue
        ident = candidate.get('id')
        if not isinstance(ident, str) or not ident.strip():
            fail('development_shape', path+'.id', '需要非空ID')
        elif ident in ids:
            fail('duplicate_candidate', path+'.id', '候选ID重复')
        else:
            ids.add(ident)
        for key in ('observation', 'interpretation', 'viewer_gain', 'return_to_story', 'presentation_intent'):
            if not isinstance(candidate.get(key), str) or not candidate[key].strip():
                fail('development_shape', path+'.'+key, '需要具体说明')
        status = candidate.get('status')
        if status not in ('ready', 'needs_research', 'drop'):
            fail('development_shape', path+'.status', '状态须为ready、needs_research或drop')
        refs = candidate.get('evidence_ids')
        if not isinstance(refs, list) or any(not isinstance(ref, str) for ref in refs):
            fail('development_shape', path+'.evidence_ids', '引用须为ID列表')
            refs = []
        if status == 'ready' and not refs:
            fail('unknown_evidence', path+'.evidence_ids', '可用观察必须有本片依据')
        for ref in refs:
            record = records.get(ref)
            if record is None:
                fail('unknown_evidence', path+'.evidence_ids', f'未知本片证据：{ref}')
            elif record.get('source_id') != project['source']['id']:
                fail('source_identity', path+'.evidence_ids', f'证据来自其他源：{ref}')
            elif record.get('disputed_by'):
                fail('disputed_evidence', path+'.evidence_ids', f'请使用已校正旁注：{ref}')
            elif record.get('kind') in ('model_interpretation', 'context_only'):
                fail('unverified_evidence', path+'.evidence_ids', f'上下文或模型推断不能充当依据：{ref}')
        missing = candidate.get('missing_premises')
        if not isinstance(missing, list):
            fail('development_shape', path+'.missing_premises', '缺失事实前提须为列表')
        else:
            for j, premise in enumerate(missing):
                if not isinstance(premise, dict) or any(not isinstance(premise.get(k), str) or not premise[k].strip() for k in ('claim', 'action')):
                    fail('development_shape', f'{path}.missing_premises[{j}]', '说明缺什么事实以及怎样核查')
            if missing and status == 'ready':
                fail('unresolved_premise', path+'.missing_premises', '尚缺事实前提的角度不能标为ready')
        checks = candidate.get('editing_checks', [])
        if not isinstance(checks, list) or any(not isinstance(c, dict) or not isinstance(c.get('action'), str) or not c['action'].strip() for c in checks):
            fail('development_shape', path+'.editing_checks', '剪辑复核项须说明实际动作')
    selected = data.get('selected_id')
    if 'selected_id' not in data or (selected is not None and (not isinstance(selected, str) or selected not in ids)):
        fail('selected_angle', 'selected_id', '选择有效候选ID；没有可写角度时为null')
    elif selected is not None:
        chosen = next(c for c in candidates if isinstance(c, dict) and c.get('id') == selected)
        if chosen.get('status') != 'ready':
            fail('selected_angle', 'selected_id', '只可选择依据就绪的角度')
    return findings


def selected_context(data):
    ident = data.get('selected_id')
    if ident is None:
        return None
    return next((deepcopy(c) for c in data['candidates'] if c.get('id') == ident), None)
