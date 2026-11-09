"""限速、有限重试、持久化熔断；502/503绝不在本次调用内重试。"""
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import os
import socket
import tempfile
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, HTTPRedirectHandler, build_opener

from .types import CircuitOpen, InvalidData, ProviderUnavailable

# 第一轮已失败的东方财富来源，本批直接阻断，不尝试换子域名绕过。
BLOCKED_SUFFIXES = ('eastmoney.com',)


class NoDataRedirect(HTTPRedirectHandler):
    def redirect_request(self,req,fp,code,msg,headers,newurl):
        raise ProviderUnavailable('数据接口重定向被阻止，不自动访问其他来源')


def verified_urlopen(request,timeout):
    return build_opener(NoDataRedirect()).open(request,timeout=timeout)


class CircuitRegistry:
    def __init__(self, directory: Path, clock=time.time):
        self.directory=Path(directory);self.directory.mkdir(parents=True,exist_ok=True)
        self.clock=clock

    def path(self, host):
        return self.directory/(hashlib.sha256(host.encode()).hexdigest()+'.json')

    def check(self, host):
        path=self.path(host)
        if path.exists():
            try:state=json.loads(path.read_text())
            except (ValueError,OSError):raise CircuitOpen('熔断记录不可读，停止请求')
            if state.get('until',0)>self.clock():
                raise CircuitOpen(f"{host}仍在冷却期：{state.get('reason','上次访问失败')}")

    def trip(self, host, reason, seconds=3600):
        path=self.path(host)
        record={'host':host,'until':self.clock()+max(60,seconds),'reason':reason}
        fd,tmp=tempfile.mkstemp(dir=self.directory,prefix='.circuit-')
        try:
            with os.fdopen(fd,'w') as handle:json.dump(record,handle)
            os.replace(tmp,path)
        finally:
            if os.path.exists(tmp):os.unlink(tmp)


class RateLimiter:
    def __init__(self, interval=1.0, clock=time.monotonic, sleep=time.sleep):
        if interval<0:raise ValueError('限速间隔不能为负')
        self.interval=interval;self.clock=clock;self.sleep=sleep
        self.last={};self.lock=threading.Lock()

    def wait(self, host):
        with self.lock:
            remaining=self.interval-(self.clock()-self.last.get(host,-float('inf')))
            if remaining>0:self.sleep(remaining)
            self.last[host]=self.clock()


@dataclass(frozen=True)
class Response:
    body: bytes
    status: int
    headers: dict
    collected_at: datetime


class HttpTransport:
    def __init__(self, *, circuit: CircuitRegistry, allowed_hosts: set[str], attempts=2,
                 timeout=10, limiter=None, opener=verified_urlopen, sleep=time.sleep):
        if not 1<=attempts<=3:raise ValueError('最多允许3次尝试')
        self.circuit=circuit;self.allowed_hosts=allowed_hosts;self.attempts=attempts
        self.timeout=timeout;self.limiter=limiter or RateLimiter();self.opener=opener;self.sleep=sleep

    def get(self,url,headers=None):
        parsed=urlsplit(url);host=parsed.hostname or ''
        if parsed.scheme!='https' or host not in self.allowed_hosts or parsed.username or parsed.password or parsed.port not in (None,443):
            raise ProviderUnavailable('该HTTPS来源不在显式允许清单中')
        if any(host==suffix or host.endswith('.'+suffix) for suffix in BLOCKED_SUFFIXES):
            raise CircuitOpen('本批禁止重试第一轮失败的东方财富来源')
        self.circuit.check(host)
        for attempt in range(self.attempts):
            self.limiter.wait(host)
            try:
                request=Request(url,headers={'User-Agent':'AshareLab-Research/2.0',**(headers or {})})
                with self.opener(request,timeout=self.timeout) as response:
                    final_host=urlsplit(response.geturl()).hostname
                    if final_host!=host:raise ProviderUnavailable('数据接口重定向到其他来源，停止解析')
                    body=response.read(2*1024*1024+1)
                    if len(body)>2*1024*1024:raise InvalidData('响应超过2MB安全上限')
                    return Response(body,response.status,dict(response.headers),datetime.now(timezone.utc))
            except HTTPError as exc:
                if exc.code in {401,403,412,429,502,503}:
                    self.circuit.trip(host,f'HTTP {exc.code}')
                    raise ProviderUnavailable(f'HTTP {exc.code}，已熔断，不在本次请求重试') from None
                if exc.code not in {408,500,504} or attempt+1==self.attempts:
                    raise ProviderUnavailable(f'HTTP {exc.code}，请求失败') from None
            except (URLError,TimeoutError,socket.timeout):
                if attempt+1==self.attempts:
                    self.circuit.trip(host,'连接或超时失败',300)
                    raise ProviderUnavailable('连接或超时失败，有限重试已耗尽') from None
            self.sleep(min(2**attempt,4))
        raise ProviderUnavailable('请求失败')
