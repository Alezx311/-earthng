"""Cold source/geometry and held-out-pose replay benchmark (not a physics proof).

No future route is passed to the scheduler. JSON reports distinguish generation-only
deadlines from in-game collision readiness, which must be measured by test_world.
"""
import argparse
import heapq
import json
import math
from pathlib import Path
import time
from types import SimpleNamespace

import numpy as np
import fast
import fast_sources as sources
import fast_server
from world_server import Server


def route(store, waypoints):
    roads={fid:data for fid,kind,data,geom in store.query((-1e6,-1e6,1e6,1e6),{'road'})}
    links={}; coords={}
    for sid,d in roads.items():
        length=sum(math.dist(a,b) for a,b in zip(d['xy'],d['xy'][1:]))
        a,b=d['nodes']; coords[a]=d['xy'][0]; coords[b]=d['xy'][-1]
        links.setdefault(a,[]).append((b,sid,length)); links.setdefault(b,[]).append((a,sid,length))
    ids=list(coords); xy=np.array([coords[n] for n in ids]); targets=[]
    for lon,lat in waypoints:
        p=np.array(store.frame.local(lon,lat))
        targets.append(ids[int(np.argmin(np.linalg.norm(xy-p,axis=1)))])
    points=[]
    for start,goal in zip(targets,targets[1:]):
        dist={start:0}; prev={}; queue=[(0,start)]
        while queue:
            d,n=heapq.heappop(queue)
            if d!=dist[n]: continue
            if n==goal: break
            for other,sid,length in links[n]:
                nd=d+length
                if nd<dist.get(other,math.inf):
                    dist[other]=nd; prev[other]=(n,sid); heapq.heappush(queue,(nd,other))
        if goal not in prev:
            raise ValueError('route disconnected')
        path=[]; n=goal
        while n!=start:
            p,sid=prev[n]; path.append((p,n,sid)); n=p
        for p,n,sid in reversed(path):
            data=roads[sid]; pts=data['xy'] if data['nodes'][0]==p else data['xy'][::-1]
            points.extend(pts if not points else pts[1:])
    arr=np.array(points); keep=np.r_[True,np.linalg.norm(np.diff(arr,axis=0),axis=1)>.01]
    arr=arr[keep]; along=np.r_[0,np.cumsum(np.linalg.norm(np.diff(arr,axis=0),axis=1))]
    return arr,along


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--sources',default='data/osm_tiles/kyiv-260918')
    p.add_argument('--dem',default='out/world/cache'); p.add_argument('--out',type=Path,required=True)
    p.add_argument('--workers',type=int,default=4); p.add_argument('--speed',type=float,default=83.333333)
    p.add_argument('--seconds',type=float,default=120); p.add_argument('--index',type=Path)
    a=p.parse_args()
    if a.out.exists(): raise SystemExit('Use a new output directory: cached results must not count as cold.')
    a.out.mkdir(parents=True); started=time.perf_counter()
    directory,meta=sources.prepare(a.sources,a.out/'index' if a.index is None else a.index,a.dem)
    prep=time.perf_counter()-started
    store=sources.Store(directory)
    points,along=route(store,[(30.499,50.507),(30.5225,50.4475),(30.615,50.454),(30.64,50.405),(30.508,50.392)])
    (a.out/'route.json').write_text(json.dumps({'points':points.tolist(),'length':float(along[-1]),
                                             'frame':store.frame.spec(),'speed':a.speed}),encoding='utf8')
    # Production server receives a cold tile cache and the timed source index above.
    args=SimpleNamespace(userfolder=str(a.out/'user'),work=str(a.out/'work'),tiles=a.sources,
                         regions=str(a.out/'none'),cache=a.dem,workers=a.workers,radius=800,generator='fast')
    server=fast_server.server_class(Server)(args)
    lon,lat=store.frame.geographic(*points[0]); i,j=store.frame.tile_of(lon,lat)
    server.frame=store.frame
    server.landing={'seq':1,'tile':[i,j],'lon':float(lon),'lat':float(lat),'at':time.monotonic()-prep}
    server.status['job']={'state':'working','seq':1}
    server.stream={'region':meta['id'],'dir':str(directory.resolve()),'tile':sources.TILE,
                   'ready':{},'scenes':{},'inflight':{},'failed':{},'queued':0,'built':0,
                   'buildSeconds':0.,'bounds':meta['bbox'],'generator':sources.GEOMETRY}
    misses=[]; trace=[]; startup=None; error=None
    try:
        wait=time.perf_counter()
        while time.perf_counter()-wait+prep<120:
            server.schedule()
            if server.desired and all(k in server.stream['ready'] for k,v in server.desired.items() if v[0]==0):
                startup=time.perf_counter()-wait+prep; break
            time.sleep(.05)
        if startup is None:
            error='startup_budget_exceeded'
        else:
            drive=time.perf_counter(); last=-1
            while time.perf_counter()-drive<a.seconds:
                elapsed=time.perf_counter()-drive; distance=elapsed*a.speed
                if distance>=along[-1]: break
                k=min(len(points)-2,max(0,int(np.searchsorted(along,distance,side='right')-1)))
                delta=points[k+1]-points[k]; tangent=delta/np.linalg.norm(delta)
                pos=points[k]+tangent*(distance-along[k])
                server.pose={'x':float(pos[0]),'y':float(pos[1]),'vx':float(tangent[0]*a.speed),'vy':float(tangent[1]*a.speed)}
                server.schedule()
                ti,tj=math.floor(pos[0]/sources.TILE),math.floor(pos[1]/sources.TILE); key=f'{ti}_{tj}'
                if key not in server.stream['ready'] and (not misses or misses[-1]['tile']!=key):
                    misses.append({'second':elapsed,'tile':key,'distance':distance})
                if int(elapsed)!=last:
                    last=int(elapsed)
                    trace.append({'second':elapsed,'distance':distance,'ready':len(server.stream['ready']),
                                  'queued':server.stream['queued'],'p99':fast_server.percentile(server.history)})
                    if last%10==0: print(trace[-1],flush=True)
                time.sleep(.05)
    finally:
        server.builders.shutdown(wait=True,cancel_futures=True)
        server.pool.shutdown(wait=True,cancel_futures=True); server.preparer.shutdown(wait=True,cancel_futures=True)
    report={'kind':'generation_only','cold_index':a.index is None,'source_prepare_seconds':prep,
            'startup_seconds':startup,'error':error,'speed_mps':a.speed,'deadline_misses':misses,
            'failed':server.stream['failed'],'trace':trace,'p99_build_seconds':fast_server.percentile(server.history),
            'collision_verified':False,'physics_verified':False}
    (a.out/'report.json').write_text(json.dumps(report,indent=2),encoding='utf8')
    print(json.dumps({k:v for k,v in report.items() if k!='trace'},indent=2),flush=True)
    if error or misses or report['failed']: raise SystemExit(1)


if __name__=='__main__': main()
