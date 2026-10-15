"""Execute hash-pinned SDK method bodies locally with mocked external boundaries.

Not a full AstrBot runtime or a claim about the modified production installation.
No network/download, SDK installation, production filesystem or QQ access.
"""
import argparse
import ast
import asyncio
from dataclasses import dataclass, field
from enum import Enum
import hashlib
import importlib
import inspect
import json
import logging
import os
from pathlib import Path
import sys
import tempfile
from types import ModuleType, SimpleNamespace
import warnings

ROOT = Path(__file__).resolve().parents[1]


def validate(source_root):
    manifest = json.loads((ROOT/'docs/sdk-4255-source-manifest.json').read_text('utf-8'))
    for name, digest in manifest['files'].items():
        if hashlib.sha256((source_root/name).read_bytes()).hexdigest() != digest:
            raise ValueError('SDK source hash mismatch: '+name)
    for path, name in [('astrbot/core/utils/command_parser.py','CommandParserMixin'),
                       ('astrbot/core/utils/plugin_kv_store.py','PluginKVStoreMixin')]:
        tree=ast.parse((source_root/path).read_text('utf-8'))
        mixin=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name==name)
        if mixin.bases or mixin.keywords or any(isinstance(n,ast.FunctionDef)
                and n.name in {'__init__','__init_subclass__'} for n in mixin.body):
            raise ValueError('Review SDK mixin construction hooks before substituting unused helpers')
    sdk = ModuleType('_cdu_pinned_sdk_contract')
    sys.modules[sdk.__name__] = sdk
    ns = sdk.__dict__
    ns.update({'asyncio':asyncio,'dataclass':dataclass,'field':field,'Enum':Enum,'Path':Path,
               'os':os,'json':json,'inspect':inspect,'warnings':warnings,
               'logger':logging.getLogger('cdu-sdk-offline'),'star_registry':[],'star_map':{},
               '_warned_register_star':False,'CommandParserMixin':type('CommandParserMixin',(),{}),
               'PluginKVStoreMixin':type('PluginKVStoreMixin',(),{})})

    def extract(path, name, methods=None):
        tree = ast.parse((source_root/path).read_text('utf-8'))
        node = next(n for n in tree.body if isinstance(n,(ast.ClassDef,ast.FunctionDef)) and n.name==name)
        if methods is not None:
            node.body = [n for n in node.body if not isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)) or n.name in methods]
        module = ast.Module(body=[ast.ImportFrom(module='__future__',names=[ast.alias(name='annotations')],level=0),node],type_ignores=[])
        exec(compile(ast.fix_missing_locations(module),path,'exec'),ns)
        return ns[name]

    extract('astrbot/core/star/star.py','StarMetadata')
    extract('astrbot/core/star/base.py','Star')
    extract('astrbot/core/star/register/star.py','register_star')
    extract('astrbot/core/platform/message_type.py','MessageType')
    extract('astrbot/core/platform/message_session.py','MessageSession')
    ns['MessageSesion'] = ns['MessageSession']
    # Plain serialization and actual OneBot transport are external Mock boundaries.
    ns['Plain'] = lambda text:SimpleNamespace(text=text)
    ns['BaseMessageComponent'] = object
    extract('astrbot/core/message/message_event_result.py','MessageChain',{'message'})
    extract('astrbot/core/star/context.py','Context',{'send_message','get_registered_star'})
    extract('astrbot/core/star/star_tools.py','StarTools',{'get_data_dir'})
    default_tree = ast.parse((source_root/'astrbot/core/config/default.py').read_text('utf-8'))
    value_node = next(n.value for n in default_tree.body if isinstance(n,ast.Assign)
                      and any(isinstance(t,ast.Name) and t.id=='DEFAULT_VALUE_MAP' for t in n.targets))
    ns.update({'DEFAULT_VALUE_MAP':ast.literal_eval(value_node),'DEFAULT_CONFIG':{},'ASTRBOT_CONFIG_PATH':'NEVER_USE_DEFAULT_PATH'})
    extract('astrbot/core/config/astrbot_config.py','AstrBotConfig')
    extract('astrbot/core/star/star_manager.py','PluginManager',{'_terminate_plugin','turn_off_plugin','uninstall_plugin'})

    modules = {'astrbot':ModuleType('astrbot'),'astrbot.api':ModuleType('astrbot.api'),
               'astrbot.api.star':ModuleType('astrbot.api.star'),'astrbot.api.event':ModuleType('astrbot.api.event')}
    modules['astrbot.api'].AstrBotConfig=ns['AstrBotConfig']
    modules['astrbot.api'].logger=ns['logger']
    modules['astrbot.api.event'].MessageChain=ns['MessageChain']
    for name in ['Context','Star','StarTools']:
        setattr(modules['astrbot.api.star'],name,ns[name])
    modules['astrbot.api.star'].register=ns['register_star']
    old_modules={name:sys.modules.get(name) for name in modules}
    sys.modules.update(modules)
    sys.path.insert(0,str(ROOT))
    checks=['source_mixins_have_no_constructor_or_registration_hooks']
    try:
        plugin_module=importlib.import_module('astrbot_plugin_cdu_radar.main')
        cls=plugin_module.CDURadarPlugin
        assert len(ns['star_registry'])==1 and ns['star_registry'][0].name=='astrbot_plugin_cdu_radar'
        checks.append('real_star_subclass_and_register_metadata')
        assert {'context','config'} <= set(inspect.signature(cls).parameters)
        checks.append('loader_keyword_constructor_signature')
        with tempfile.TemporaryDirectory(prefix='cdu-sdk-') as directory:
            base=Path(directory)
            ns['get_astrbot_data_path']=lambda:str(base/'data')
            ns['ensure_dir']=lambda p:Path(p).mkdir(parents=True,exist_ok=True)
            schema=json.loads((ROOT/'astrbot_plugin_cdu_radar/_conf_schema.json').read_text('utf-8'))
            cfg=ns['AstrBotConfig'](config_path=str(base/'plugin-config.json'),schema=schema)
            assert cfg['enabled'] is False and cfg['target_umo']==''
            checks.append('real_config_schema_defaults_and_private_file_load')
            events=[]
            class Platform:
                def meta(self):return SimpleNamespace(id='mockbot')
                async def send_by_session(self,session,chain):
                    events.append((str(session),chain.chain[0].text))
            context=ns['Context']();context.platform_manager=SimpleNamespace(platform_insts=[Platform()])
            unload_notifications=[]
            async def unload_hook(meta):unload_notifications.append(meta.name)
            ns['star_map']['existing.unload_hook']=SimpleNamespace(name='synthetic existing hook')
            ns['star_handlers_registry']=SimpleNamespace(get_handlers_by_event_type=lambda kind:[
                SimpleNamespace(handler_module_path='existing.unload_hook',handler_name='observe',handler=unload_hook)])
            ns['EventType']=SimpleNamespace(OnPluginUnloadedEvent=object())
            async def exercise():
                nonlocal cfg
                meta=ns['star_registry'][0]
                disabled=cls(context=context,config=cfg);meta.star_cls=disabled
                await disabled.initialize()
                assert disabled.consumer is None and not (base/'data').exists() and not events
                await ns['PluginManager']._terminate_plugin(meta)
                checks.append('real_sdk_terminate_hook_on_default_disabled_plugin')
                cfg['enabled']=True;cfg['target_umo']='mockbot:GroupMessage:123456789';cfg.save_config()
                cfg=ns['AstrBotConfig'](config_path=str(base/'plugin-config.json'),schema=schema)
                class Fetch:
                    body=b'<rss version="2.0"><channel><item><guid>seed</guid><title>synthetic</title><link>https://example.invalid/seed</link><description>synthetic only</description><pubDate>Thu, 15 Oct 2026 12:00:00 +0800</pubDate></item></channel></rss>'
                    async def __call__(self,url):return 200,self.body
                    async def close(self):self.closed=True
                fetch=Fetch();plugin_module.HttpFetcher=lambda:fetch
                active=cls(context=context,config=cfg);meta.star_cls=active
                meta.root_dir_name='astrbot_plugin_cdu_radar'
                await active.initialize();await asyncio.sleep(0)
                assert not events
                assert (base/'data/plugin_data/astrbot_plugin_cdu_radar/state.sqlite3').is_file()
                checks.append('real_get_data_dir_stays_in_temporary_plugin_namespace')
                fetch.body=fetch.body.replace(b'</channel>',b'<item><guid>new</guid><title>new</title><link>https://example.invalid/new</link><description>unknown eligibility</description><pubDate>Thu, 15 Oct 2026 12:00:01 +0800</pubDate></item></channel>')
                await active.consumer.poll_once()
                assert len(events)==1 and events[0][0]==cfg['target_umo']
                assert active.consumer.last_status=='submitted_to_platform'
                checks.append('real_message_chain_session_parser_and_context_send_to_mock_platform')
                missing=await context.send_message('missing:GroupMessage:123456789',ns['MessageChain']().message('synthetic'))
                assert missing is False and len(events)==1
                checks.append('real_missing_platform_returns_false_without_fallback')
                prefs={'inactivated_plugins':['existing.disabled'],'inactivated_llm_tools':['existing.tool']}
                class Preferences:
                    async def global_get(self,k,default):return list(prefs.get(k,default))
                    async def global_put(self,k,v):prefs[k]=list(v)
                ns['sp']=Preferences()
                existing_tool=SimpleNamespace(handler_module_path='data.plugins.unrelated.main',name='existing.tool',active=True)
                ns['llm_tools']=SimpleNamespace(func_list=[existing_tool])
                manager=ns['PluginManager']();manager._pm_lock=asyncio.Lock();manager.context=context
                manager.plugin_store_path=str(base/'plugins')
                cleanup=[]
                async def unbind(name,module):
                    assert name==meta.name and module==meta.module_path
                    cleanup.append('unbind_one')
                manager._unbind_plugin=unbind
                manager._cleanup_plugin_optional_artifacts=lambda **kw:cleanup.append(kw)
                ns['remove_dir']=lambda p:cleanup.append('remove_one_source_directory')
                await manager.turn_off_plugin(meta.name)
                assert active.consumer is None and active.fetcher is None and fetch.closed
                assert prefs['inactivated_plugins']==['existing.disabled',meta.module_path] and existing_tool.active
                checks.append('real_turn_off_waits_own_terminate_and_preserves_unrelated_entries')
                await manager.uninstall_plugin(meta.name)
                assert cleanup[-1]['delete_config'] is False and cleanup[-1]['delete_data'] is False
                checks.append('real_uninstall_defaults_preserve_config_and_dedup_state')
                assert unload_notifications==[meta.name,meta.name]
                checks.append('real_sdk_emits_global_unload_hook_even_for_targeted_stop')
                assert all(t.get_name()!='cdu-radar-fixed-rss' for t in asyncio.all_tasks() if not t.done())
                checks.append('no_owned_polling_task_after_sdk_stop')
            asyncio.run(exercise())
        return {'sdk_commit':manifest['commit'],'mode':'hash_pinned_sdk_method_bodies_with_mock_external_boundaries',
                'checks':checks,'full_framework_started':False,'production_sdk_hash_equivalence_verified':False,
                'qq_calls':0,'production_connections':0}
    finally:
        sys.modules.pop('astrbot_plugin_cdu_radar.main',None)
        for name,old in old_modules.items():
            if old is None:sys.modules.pop(name,None)
            else:sys.modules[name]=old


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--source-root',type=Path,required=True)
    parser.add_argument('--output',type=Path)
    args=parser.parse_args();result=validate(args.source_root)
    if args.output:
        with args.output.open('x',encoding='utf-8') as file:
            json.dump(result,file,ensure_ascii=False,indent=2)
    print(json.dumps(result,ensure_ascii=False))
