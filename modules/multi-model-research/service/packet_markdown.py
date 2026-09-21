"""Lossless presentation of a structured packet; the archival schema stays unchanged."""
import hashlib
import json
import re

FORMAT = 'markdown-v1'

CLAIM_FIELDS = ('id','statement','status','reason','counter_evidence','limits','sources','counter_sources')


def ledger_claims(reports):
    """References are allowed only to unambiguous, complete JSON claims in full reports."""
    found = {}
    for report in reports:
        candidates = []
        for block in re.findall(r'^```(?:evidence-ledger|json)?[^\S\n]*\n(.*?)^```[^\S\n]*$', report['content'], re.M | re.S):
            try:
                value = json.loads(block)
                if isinstance(value,dict) and isinstance(value.get('claims'),list) and 'blind_spots' in value:
                    candidates.append(value['claims'])
            except ValueError:
                pass
        if len(candidates) != 1:
            continue
        claims = candidates[0]
        for claim in claims:
            if (isinstance(claim,dict) and isinstance(claim.get('id'),str)
                    and sum(isinstance(c,dict) and c.get('id')==claim['id'] for c in claims)==1
                    and all(k in claim for k in CLAIM_FIELDS)):
                found[(report['stage'],claim['id'])] = {k:claim[k] for k in CLAIM_FIELDS}
    return found


def compact_claims(claims, reports):
    """A lossless index, not a new model summary; overrides preserve audited source unions."""
    lookup = ledger_claims(reports)
    result = []
    for claim in claims:
        item = dict(claim)
        item['assessments'] = []
        for assessment in claim['assessments']:
            stage = assessment['stage']
            original = lookup.get((stage,assessment['id']))
            if original is None:
                item['assessments'].append(assessment)
                continue
            base = dict(original,stage=stage)
            # Do not reference unexpected schemas; retain the full record instead.
            if base.keys() != assessment.keys():
                item['assessments'].append(assessment)
                continue
            item['assessments'].append({'report_claim':[stage,assessment['id']],
                'overrides':{k:v for k,v in assessment.items() if v!=base[k]}})
        result.append(item)
    return result


def expand_claims(claims, reports):
    """Inverse used by preservation tests and offline validation tooling."""
    lookup = ledger_claims(reports)
    result = []
    for claim in claims:
        item = dict(claim)
        item['assessments'] = []
        for assessment in claim['assessments']:
            if 'report_claim' not in assessment:
                item['assessments'].append(assessment)
                continue
            stage,ident = assessment['report_claim']
            item['assessments'].append(dict(lookup[(stage,ident)],stage=stage) | assessment['overrides'])
        result.append(item)
    return result


def source_references(value, urls, expand=False):
    """Encode only URL-list fields; arbitrary prose and unknown URLs are untouched."""
    if isinstance(value,list):
        return [source_references(v,urls,expand) for v in value]
    if not isinstance(value,dict):
        return value
    result={}
    index={url:i+1 for i,url in enumerate(urls)}
    for key, item in value.items():
        if key in ('sources','counter_sources') and isinstance(item,list):
            result[key]=[(urls[v-1] if isinstance(v,int) else v) if expand else index.get(v,v) for v in item]
        else:
            result[key]=source_references(item,urls,expand)
    return result


def markdown_packet(packet):
    # A delimiter present in imported material must never terminate its data boundary.
    original = json.dumps(packet, ensure_ascii=False, sort_keys=True)
    marker = 'REFERENCE_' + hashlib.sha256(original.encode()).hexdigest()
    while marker in original:
        marker += '_'
    parts = ['BEGIN_' + marker]

    def metadata(value):
        parts.append(json.dumps(value, ensure_ascii=False, separators=(',', ':')))

    def text_block(label, text):
        parts.extend([f'### {label} · UTF-8 bytes: {len(text.encode())}',
                      f'BEGIN_{marker}_{label}', text, f'END_{marker}_{label}'])

    metadata({k: v for k, v in packet.items() if k not in ('question', 'scope', 'reports', 'evidence_register')})
    text_block('question', packet['question'])
    text_block('scope', packet['scope'])
    sources = {}
    for report in packet['reports']:
        parts.append('## Report: ' + report['stage'])
        metadata({k: v for k, v in report.items() if k not in ('content', 'evidence', 'citations')})
        text_block(report['stage'] + '_content', report['content'])
        for i, evidence in enumerate(report['evidence']):
            parts.append('### Evidence attachment')
            metadata({k: v for k, v in evidence.items() if k != 'content'})
            text_block(report['stage'] + '_evidence_' + str(i), evidence['content'])
        for url in report['citations']:
            stages = sources.setdefault(url, [])
            if report['stage'] not in stages:
                stages.append(report['stage'])
    parts.extend(['## Source inventory (exact URLs; inline citations remain unchanged)',
                  'URL recorded; source content/support not independently verified'])
    for i,(url, stages) in enumerate(sources.items(),1):
        parts.append(f'[{i}] ' + url + '\nCited by: ' + ', '.join(stages))
    if 'evidence_register' in packet:
        parts.append('## evidence_register (source inventory above; complete claim and objection history below)')
        # Only the duplicate sources array is represented by the inventory above.
        parts.append('Each report_claim [stage, id] refers to the complete claim with that id in that report’s evidence-ledger JSON block above. '
                     'Read all fields: id, statement, status, reason, counter_evidence, limits, sources, counter_sources. '
                     'Add stage and apply overrides (including preserved earlier sources). This is a lossless index, not a summary or a new judgment. '
                     'In sources/counter_sources arrays below, integers are exact URL references to the numbered inventory above. '
                     'Source IDs are S- followed by the first 16 hexadecimal digits of SHA-256 of the exact URL.')
        registry={k: v for k, v in packet['evidence_register'].items() if k != 'sources'}
        registry['claims']=compact_claims(registry['claims'],packet['reports'])
        metadata(source_references(registry,list(sources)))
    parts.append('END_' + marker)
    return '\n\n'.join(parts) + '\n'
