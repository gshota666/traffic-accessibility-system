"""Cross-platform single-process launcher. No shell, fork or fixed port assumptions."""
import argparse
import json
import logging
from logging.handlers import RotatingFileHandler
import os
import secrets
import socket
import threading
import time
import webbrowser
from pathlib import Path
import httpx
import uvicorn
from filelock import FileLock,Timeout
from utils.paths import data_dir
from backend.app import create_app


def reserve_socket(preferred=8000):
    for port in [*range(preferred,min(preferred+20,65536)),0]:
        sock=socket.socket(socket.AF_INET,socket.SOCK_STREAM)
        if os.name!='nt': sock.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
        try:
            sock.bind(('127.0.0.1',port)); sock.listen(128)
            return sock
        except OSError: sock.close()
    raise RuntimeError('没有可用的本地端口')


def launch(root=None,no_browser=False,port=8000):
    root=data_dir(root)
    log=logging.getLogger('traffic'); log.setLevel(logging.INFO)
    handler=RotatingFileHandler(root/'logs'/'application.log',maxBytes=2_000_000,backupCount=3,encoding='utf-8')
    handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s')); log.addHandler(handler)
    # HTTP client logs must never expose API key query parameters.
    logging.getLogger('httpx').setLevel(logging.WARNING)
    logging.getLogger('httpcore').setLevel(logging.WARNING)
    lock=FileLock(root/'instance.lock',timeout=0)
    state=root/'instance.json'
    try: lock.acquire()
    except Timeout:
        for _ in range(50):
            try:
                info=json.loads(state.read_text(encoding='utf-8'))
                if info.get('port') and info.get('token'):
                    url=f'http://127.0.0.1:{int(info["port"])}'
                    with httpx.Client(trust_env=False,timeout=1) as client:
                        response=client.get(url+'/api/dashboard',headers={'x-session-token':info['token']})
                    if response.status_code==200:
                        if not no_browser: webbrowser.open(url+'/?token='+info['token'])
                        return
            except (OSError,ValueError,httpx.HTTPError): pass
            time.sleep(.1)
        raise RuntimeError('另一个实例正在启动或退出，请稍后重试')
    sock=None
    try:
        token=secrets.token_urlsafe(32); sock=reserve_socket(port); actual=sock.getsockname()[1]
        server=None
        def stop(): server.should_exit=True
        app=create_app(root,token,on_shutdown=stop)
        config=uvicorn.Config(app,host='127.0.0.1',port=actual,log_level='warning',access_log=False,log_config=None,timeout_graceful_shutdown=25)
        server=uvicorn.Server(config)
        fd=os.open(state,os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600)
        with os.fdopen(fd,'w',encoding='utf-8') as f: json.dump({'port':actual,'token':token,'pid':os.getpid()},f)
        url=f'http://127.0.0.1:{actual}/?token={token}'
        def open_when_ready():
            for _ in range(200):
                if server.started:
                    if not no_browser: webbrowser.open(url)
                    return
                if server.should_exit: return
                time.sleep(.05)
        threading.Thread(target=open_when_ready,daemon=True).start()
        log.info('Application started on loopback port %d',actual)
        server.run(sockets=[sock])
    finally:
        if sock: sock.close()
        state.unlink(missing_ok=True)
        lock.release(); log.info('Application exited'); log.removeHandler(handler); handler.close()

if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--data-dir',type=Path)
    parser.add_argument('--no-browser',action='store_true')
    parser.add_argument('--port',type=int,default=8000)
    args=parser.parse_args()
    launch(args.data_dir,args.no_browser,args.port)
