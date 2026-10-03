import json
import os
import threading
import keyring
from keyring.errors import KeyringError

# QPS is an application-side ceiling, not a claim about the provider's
# account quota.  New installations use the conservative, user-requested
# target of 3 requests/second.  Existing installations retain their stored
# value because ``get`` overlays saved configuration on these defaults.
DEFAULTS=dict(provider='mock',qps=3.0,max_concurrency=3,daily_limit=1000,retries=3,strategy='32')

class Settings:
    def __init__(self,db,root):
        self.db=db; self.root=root; self._probe=None; self._probe_lock=threading.Lock()
    def key_available(self):
        # A locked keychain must not block the homepage or its setup instructions.
        if (self.root/'credentials.json').exists(): return bool(self.key())
        with self._probe_lock:
            if self._probe is None:
                probe={'event':threading.Event(),'value':None};self._probe=probe
                def check():
                    try: probe['value']=bool(self.key())
                    except Exception: probe['value']=None
                    finally: probe['event'].set()
                threading.Thread(target=check,daemon=True,name='key-status').start()
            probe=self._probe
        probe['event'].wait(.1)
        return probe['value'] if probe['event'].is_set() else None
    def get(self):
        row=self.db.one("SELECT value FROM settings WHERE key='config'")
        return {**DEFAULTS,**(json.loads(row['value']) if row else {})}
    def key(self):
        file=self.root/'credentials.json'
        if file.exists(): return json.loads(file.read_text(encoding='utf-8')).get('amap','')
        try: return keyring.get_password('TrafficAccessibility','amap') or ''
        except (KeyringError,RuntimeError): return ''
    def save(self,config,key=None,plaintext=False):
        if key:
            if plaintext:
                file=self.root/'credentials.json'
                fd=os.open(file,os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600)
                with os.fdopen(fd,'w',encoding='utf-8') as f: json.dump({'amap':key},f)
                if os.name=='posix': file.chmod(0o600)
            else:
                try: keyring.set_password('TrafficAccessibility','amap',key)
                except (KeyringError,RuntimeError): raise ValueError('系统凭据库不可用。可明确选择本机明文配置保存；Windows 继承用户目录 ACL。') from None
                (self.root/'credentials.json').unlink(missing_ok=True)
        if key: self._probe=None
        self.db.execute("INSERT OR REPLACE INTO settings VALUES('config',?)",(json.dumps(config),))
    def public(self):
        return {**self.get(),'has_key':self.key_available(),'key_storage':'本地明文文件' if (self.root/'credentials.json').exists() else '系统凭据库','data_dir':str(self.root)}
