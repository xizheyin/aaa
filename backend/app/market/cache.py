"""仅缓存完整且有来源/时间的数据；过期不兜底，JSON文件原子替换。"""
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import tempfile
import time
from .types import InvalidData


@dataclass(frozen=True)
class CacheResult:
    data: object
    provider: str
    observed_at: str | None
    cached_at: float
    expires_at: float
    cache_hit: bool


class JsonCache:
    def __init__(self,directory:Path,clock=time.time):
        self.directory=Path(directory);self.directory.mkdir(parents=True,exist_ok=True);self.clock=clock
    def path(self,provider,key):
        digest=hashlib.sha256(json.dumps([provider,key],ensure_ascii=False,sort_keys=True).encode()).hexdigest()
        return self.directory/(digest+'.json')
    def get(self,provider,key):
        path=self.path(provider,key)
        if not path.exists():return None
        try:entry=json.loads(path.read_text())
        except (ValueError,OSError):raise InvalidData('缓存损坏，未当作行情返回')
        if entry.get('schema')!=1 or entry.get('provider')!=provider or entry.get('complete') is not True:raise InvalidData('缓存来源或完整性标记不符')
        times=[entry.get('cached_at'),entry.get('expires_at')]
        if any(isinstance(t,bool) or not isinstance(t,(int,float)) or not math.isfinite(t) for t in times) or times[1]<=times[0]:raise InvalidData('缓存时间戳非法')
        if 'data' not in entry:raise InvalidData('缓存缺少数据字段')
        if self.clock()<entry['cached_at'] or self.clock()>=entry['expires_at']:return None
        try:encoded=json.dumps(entry['data'],ensure_ascii=False,sort_keys=True,allow_nan=False)
        except (TypeError,ValueError):raise InvalidData('缓存含非法数值或类型') from None
        if hashlib.sha256(encoded.encode()).hexdigest()!=entry.get('sha256'):raise InvalidData('缓存校验和不符')
        return CacheResult(entry['data'],provider,entry.get('observed_at'),entry['cached_at'],entry['expires_at'],True)
    def put(self,provider,key,data,*,ttl,complete,observed_at=None):
        if complete is not True or isinstance(ttl,bool) or not isinstance(ttl,(int,float)) or not math.isfinite(ttl) or ttl<=0:raise InvalidData('不缓存不完整结果或非正有效期')
        encoded=json.dumps(data,ensure_ascii=False,sort_keys=True,allow_nan=False)
        cached=self.clock();entry={'schema':1,'provider':provider,'complete':True,'data':data,'cached_at':cached,'expires_at':cached+ttl,'observed_at':observed_at,'sha256':hashlib.sha256(encoded.encode()).hexdigest()}
        fd,tmp=tempfile.mkstemp(dir=self.directory,prefix='.cache-')
        try:
            with os.fdopen(fd,'w') as handle:json.dump(entry,handle,ensure_ascii=False,allow_nan=False)
            os.replace(tmp,self.path(provider,key))
        finally:
            if os.path.exists(tmp):os.unlink(tmp)
        return CacheResult(data,provider,observed_at,cached,cached+ttl,False)
    def get_or_fetch(self,provider,key,fetch,*,ttl,force_refresh=False):
        if not force_refresh:
            cached=self.get(provider,key)
            if cached:return cached
        data,observed_at=fetch()  # fetch失败直接抛出，绝不返回旧缓存。
        return self.put(provider,key,data,ttl=ttl,complete=True,observed_at=observed_at)
