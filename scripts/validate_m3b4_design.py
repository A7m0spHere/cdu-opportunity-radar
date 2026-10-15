"""Local PyYAML contract check. Never executes Docker/SSH or reads credentials."""
from pathlib import Path
import json

import yaml

ROOT = Path(__file__).resolve().parents[1]


def main():
    draft = yaml.safe_load((ROOT/'deploy/m3b4/compose.rss.draft.yaml').read_text('utf-8'))
    assert draft['name']=='cdu-radar-m3b4-rss'
    assert set(draft['services'])=={'rss','init-synthetic'}
    rss = draft['services']['rss']; init = draft['services']['init-synthetic']
    assert rss['network_mode']=='container:${ASTRBOT_CONTAINER_ID:?Approve the exact existing AstrBot container ID}'
    assert init['network_mode']=='none'
    for service in [rss,init]:
        assert not any(service.get(k) for k in ['ports','networks','pid','ipc','privileged','devices','env_file','volumes_from'])
        assert service['profiles'] and service['pull_policy']=='never' and service['restart']=='no'
        assert service['user']=='10001:10001' and service['read_only'] and service['cap_drop']==['ALL']
        assert service['mem_limit']=='256m' and service['cpus']==0.25 and service['pids_limit']==128
        assert not any('KEY' in k or 'TOKEN' in k for k in service['environment'])
        for volume in service['volumes']:
            assert volume['type']=='volume' and volume['source'] in draft['volumes']
    assert {v['target'] for v in rss['volumes']}=={'/var/lib/cdu-lab','/var/lib/cdu-rss-state'}
    assert {v['source'] for v in init['volumes']}=={'synthetic'}
    assert all(not v.get('name') and not v.get('external') for v in draft['volumes'].values())
    assert rss['command'][rss['command'].index('--bind')+1]=='127.0.0.1'
    assert init['command'][:2]==['rss-synthetic','init']
    schema = json.loads((ROOT/'astrbot_plugin_cdu_radar/_conf_schema.json').read_text('utf-8'))
    assert schema['enabled']['default'] is False and schema['target_umo']['default']==''
    assert schema['max_send_attempts']['default']==2 and schema['retry_limit']['default']==0
    print('M3-B.4 single-RSS draft isolation contracts passed; Docker config/build/up NOT executed')


if __name__ == '__main__':
    main()
