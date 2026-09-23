"""Load a scoped editorial decision about an actual saved model review.

This is an explicitly attributed semantic judgment, never a machine pass or a
bypass of the structural/presentation checks. Original findings remain intact.
"""
import hashlib
import json
from pathlib import Path

from editorial_development import evidence_digest
from editorial_inputs import index_evidence
from editorial_projection import story_fingerprint


def load_adjudication(path, story, evidence):
    path = Path(path).resolve()
    raw = path.read_bytes()
    decision = json.loads(raw)
    if not isinstance(decision, dict) or decision.get('schema_version') != 1 or decision.get('verdict') != 'accept':
        raise ValueError('invalid review decision')
    reviewer = decision.get('reviewer', {})
    if not isinstance(reviewer, dict) or reviewer.get('kind') not in ('agent', 'human') or not isinstance(reviewer.get('name'), str) or not reviewer['name'].strip():
        raise ValueError('attributed reviewer required')
    if decision.get('story_sha256') != story_fingerprint(story) or decision.get('evidence_sha256') != evidence_digest(evidence):
        raise ValueError('review decision is stale')
    source = decision.get('source_review')
    if not isinstance(source, dict) or not isinstance(source.get('path'), str) or not source['path']:
        raise ValueError('original review required')
    review_path = (path.parent/source['path']).resolve()
    review_raw = review_path.read_bytes()
    review_hash = hashlib.sha256(review_raw).hexdigest()
    if source.get('sha256') != review_hash:
        raise ValueError('original review changed')
    review = json.loads(review_raw)
    if not isinstance(review, dict) or not isinstance(review.get('story'), dict) or story_fingerprint(review['story']) != story_fingerprint(story):
        raise ValueError('original review belongs to a different story')
    if (review.get('record_type') != 'editorial_semantic_review' or
            review.get('review_stage') != 'semantic' or review.get('review_completed') is not True):
        raise ValueError('only completed semantic reviews can be adjudicated')
    if review.get('evidence_sha256') != evidence_digest(evidence) or review.get('source_id') != evidence.get('source_id'):
        raise ValueError('original review evidence identity changed')
    findings = review.get('findings')
    semantic_codes = {'coherence', 'added_value', 'evidence', 'question_payoff', 'timing'}
    if not isinstance(findings, list) or not all(isinstance(f, dict) and f.get('severity') in ('error', 'warning')
            and f.get('code') in semantic_codes and all(isinstance(f.get(k), str) and f[k].strip() for k in ('path','message')) for f in findings):
        raise ValueError('original review findings malformed')
    errors = {i for i, finding in enumerate(findings) if finding['severity'] == 'error'}
    decisions = decision.get('decisions')
    if not isinstance(decisions, list) or not errors:
        raise ValueError('decisions must address a failed review')
    addressed = set()
    records = index_evidence(evidence)
    for item in decisions:
        if not isinstance(item, dict):
            raise ValueError('decision must be an object')
        i = item.get('finding_index')
        if type(i) != int or i not in range(len(findings)) or i in addressed:
            raise ValueError('invalid or duplicate finding index')
        addressed.add(i)
        if item.get('disposition') != 'dismiss' or not isinstance(item.get('reason'), str) or not item['reason'].strip():
            raise ValueError('each dismissal needs an explicit reason')
        refs = item.get('evidence_ids')
        if not isinstance(refs, list) or not refs:
            raise ValueError('each dismissal needs target evidence')
        for ref in refs:
            if not isinstance(ref, str) or ref not in records or records[ref].get('source_id') != evidence.get('source_id') or records[ref].get('disputed_by') or records[ref].get('kind') in ('model_interpretation', 'context_only'):
                raise ValueError('dismissal cites unknown or disputed evidence')
    if not errors <= addressed:
        raise ValueError('unresolved review errors remain')
    fingerprints = {str(path): hashlib.sha256(raw).hexdigest(), str(review_path): review_hash}
    supporting = decision.get('supporting_artifacts', {})
    if not isinstance(supporting, dict):
        raise ValueError('supporting artifacts must map paths to hashes')
    for name, expected in supporting.items():
        if not isinstance(name, str) or not name or not isinstance(expected, str):
            raise ValueError('supporting artifact identity malformed')
        artifact = (path.parent/name).resolve()
        actual = hashlib.sha256(artifact.read_bytes()).hexdigest()
        if actual != expected:
            raise ValueError('supporting artifact changed')
        fingerprints[str(artifact)] = actual
    return {'decision': decision, 'source_review': review,
            'fingerprints': fingerprints,
            'origin': reviewer['kind']+'_adjudication'}
