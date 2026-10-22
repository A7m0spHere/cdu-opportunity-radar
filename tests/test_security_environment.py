from pathlib import Path
from types import SimpleNamespace
import ssl
import pytest

from scripts.verify_security_environment import patched, insecure_settings, context_report
from cdu_radar.tls import trusted_context


def test_certifi_date_versions_are_normalized_and_unpatched_versions_rejected():
    assert not patched('2026.07.22') and not patched('2026.10.18')
    assert patched('2026.10.19') and patched('2027.1.1')
    assert not patched('2026.10.19rc1')


def test_effective_context_checks_verification_hostname_and_affected_root():
    def context(mode=ssl.CERT_REQUIRED, hostname=True, root='Unrelated Test Root'):
        return SimpleNamespace(verify_mode=mode,check_hostname=hostname,
            get_ca_certs=lambda:[{'subject':((('commonName',root),),)}])
    assert context_report(context())['certificate_required']
    assert context_report(context(root='emSign ECC Root CA - G3'))['compromised_root_present']
    assert not context_report(context(mode=ssl.CERT_NONE))['certificate_required']
    assert not context_report(context(hostname=False))['hostname_checked']


def test_source_audit_detects_real_insecure_settings_not_comments(tmp_path):
    (tmp_path/'safe.py').write_text('# Do not use verify=False\nclient(verify=True)\n',encoding='utf-8')
    assert not insecure_settings(tmp_path)
    (tmp_path/'unsafe.py').write_text('client(verify=False)\nctx.check_hostname=False\nctx.verify_mode=ssl.CERT_NONE\n',encoding='utf-8')
    assert [r['line'] for r in insecure_settings(tmp_path)]==[1,2,3]


def test_project_client_ignores_a_stale_global_ca_override(monkeypatch,tmp_path):
    from cdu_radar.http import Fetcher,ResolveTransport
    monkeypatch.setenv('SSL_CERT_FILE',str(tmp_path/'missing-old-ca.pem'))
    context=trusted_context()
    assert context.verify_mode==ssl.CERT_REQUIRED and context.check_hostname
    with Fetcher(trust_env=True) as fetcher:
        assert fetcher.client is not None
    resolver=ResolveTransport({'www.cdu.edu.cn':'1.1.1.1'},{'www.cdu.edu.cn'})
    resolver.close()


def test_old_certifi_is_rejected_before_building_a_context(monkeypatch):
    import cdu_radar.tls as tls
    monkeypatch.setattr(tls.certifi,'__version__','2026.7.22')
    with pytest.raises(ValueError,match='2026.10.19'):trusted_context()


def test_model_client_uses_the_patched_context_with_an_invalid_environment_ca(monkeypatch,tmp_path):
    import httpx
    from cdu_radar.llm import LLMConfig,ChatCompletionsProvider
    monkeypatch.setenv('SSL_CERT_FILE',str(tmp_path/'missing-old-ca.pem'))
    monkeypatch.setenv('CDU_TLS_TEST_KEY','synthetic-only')
    config=LLMConfig('https://provider.example.test/v1','synthetic-model',key_env='CDU_TLS_TEST_KEY',retries=0)
    transport=httpx.MockTransport(lambda request:httpx.Response(200,json={
        'choices':[{'finish_reason':'stop','message':{'content':'{"ok":true}'}}],
        'usage':{'prompt_tokens':1,'completion_tokens':1,'total_tokens':2}}))
    result=ChatCompletionsProvider(config,transport=transport).complete([],lambda payload:payload)
    assert result=={'ok':True}
