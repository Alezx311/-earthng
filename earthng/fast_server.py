"""Deadline scheduler for the experimental fast generator."""
from collections import deque
from concurrent.futures import ThreadPoolExecutor
import heapq
import json
import os
import math

import numpy as np
from pathlib import Path
import threading
import time
import traceback

import fast
import fast_sources as sources


FAR_R = float(os.environ.get('EARTHNG_FAR_R', 3000))   # m round the car: the far city (fast_far)


FAR_FAST = 25.0    # m/s: above it the far city shrinks to FAR_R_FAST (the churn of far tiles at
FAR_R_FAST = 1800. # 300 km/h doubled be:reloadCollision, vis-replay-01)

SCENE_LAG = 3.0    # s: a scene ranks with the road of a tile the car reaches this much later
SCENE_R = 1500.    # m: scenes beyond it wait (the far city stands in for them)


def far_radius(pose):
    """Far tiles to build round the pose: none at speed (the game loads none then either)."""
    return 0. if math.hypot(pose.get('vx',0),pose.get('vy',0))>FAR_FAST else FAR_R


def percentile(values, fraction=.99, default=4.):
    return sorted(values)[min(len(values)-1, math.ceil(len(values)*fraction)-1)] if values else default


def candidates(store, pose, horizon, speed):
    """All near directions, then graph distance along every reachable road branch.

    No future route is needed. Deadline uses distance to the tile's *boundary*.
    Graph searches are capped by horizon; capacity overflow is reported, not hidden.
    """
    x,y=pose['x'],pose['y']; tile=sources.TILE
    out={}
    def add_rect(rect, arrival):
        for i in range(math.floor(rect[0]/tile),math.floor(rect[2]/tile)+1):
            for j in range(math.floor(rect[1]/tile),math.floor(rect[3]/tile)+1):
                key=f'{i}_{j}'
                dx=max(i*tile-x,0,x-(i+1)*tile); dy=max(j*tile-y,0,y-(j+1)*tile)
                deadline=max(0,min(arrival,math.hypot(dx,dy)/speed))
                if key not in out or deadline<out[key][0]:
                    out[key]=(deadline,i,j)
    # 750 m guaranteed turning reserve around current pose; larger branch reserve follows.
    add_rect((x-750,y-750,x+750,y+750),0)
    initial=store.query((x-150,y-150,x+150,y+150),{'road'})
    queue=[]; best={}
    for _,_,data,_ in initial:
        for nid in data['nodes']:
            n=store.node(nid)
            d=math.hypot(n['xy'][0]-x,n['xy'][1]-y)
            if d<best.get(nid,math.inf):
                best[nid]=d; heapq.heappush(queue,(d,nid))
    visited=set()
    while queue:
        distance,nid=heapq.heappop(queue)
        if distance != best[nid] or distance>speed*horizon:
            continue
        for (sid,) in store.db.execute('SELECT segment FROM links WHERE node=?',(nid,)):
            if sid in visited:
                continue
            visited.add(sid)
            row=store.db.execute('SELECT data FROM features WHERE id=?',(sid,)).fetchone()
            d=json.loads(row[0]); pts=d['xy']; along=distance
            if d['nodes'][1]==nid:
                pts=pts[::-1]
            for a,b in zip(pts,pts[1:]):
                if along>speed*horizon:
                    break
                add_rect((min(a[0],b[0])-20,min(a[1],b[1])-20,max(a[0],b[0])+20,max(a[1],b[1])+20),along/speed)
                along+=math.dist(a,b)
            other=d['nodes'][1] if d['nodes'][0]==nid else d['nodes'][0]
            if along<best.get(other,math.inf):
                best[other]=along; heapq.heappush(queue,(along,other))
    return out


def server_class(base):
    class FastServer(base):
        def __init__(self,args):
            self.history=deque(maxlen=256)
            self.future_jobs={}
            self.fast_store=None
            self.desired={}
            self.next_schedule=0
            self.generation=0
            self.far_covered={}
            self.preparer=ThreadPoolExecutor(1)
            super().__init__(args)
            # the art (photo facades, ground sheets) is built once in data/art: ~20 s the first
            # time, alongside the index of the first landing instead of before it. It is copied
            # into the user folder only at the landing: the game's first start in a new profile
            # moves unknown files out of it (vis-01-akadem lost the whole art folder)
            import fast_art
            self.art=ThreadPoolExecutor(1).submit(fast_art.prebuild)

        def region_boxes(self):
            """Tile cells merged into rectangles (runs per row, then equal runs of adjacent
            rows): the oblast's ~2900 tiles as single boxes made status.json ~100 KB, written
            twice a second and read by the game four times a second."""
            cell=self.index['cell']
            keys=sorted({tuple(map(int,k.split('_'))) for k in self.index['tiles']},key=lambda k:(k[1],k[0]))
            runs={}
            for x,y in keys:
                row=runs.setdefault(y,[])
                if row and row[-1][1]==x-1: row[-1][1]=x
                else: row.append([x,x])
            boxes={}                      # (x0,x1) -> [y0,y1] of the run still growing
            out=[]
            for y in sorted(runs):
                grown={}
                for x0,x1 in runs[y]:
                    prev=boxes.get((x0,x1))
                    grown[(x0,x1)]=[prev[0],y] if prev and prev[1]==y-1 else [y,y]
                    if prev and prev[1]!=y-1: out.append((x0,x1,*prev))
                for k,v in boxes.items():
                    if k not in grown: out.append((*k,*v))
                boxes=grown
            out+=[(*k,*v) for k,v in boxes.items()]
            return [[round(x0*cell,6),round(y0*cell,6),round((x1+1)*cell,6),round((y1+1)*cell,6)] for x0,x1,y0,y1 in out]

        def land(self,req):
            with self.lock:
                self.generation+=1
                generation=self.generation
                self.stream=None; self.pose=None; self.desired={}; self.far_covered={}
                self.landing={'seq':req['seq'],'lat':req['lat'],'lon':req['lon'],'at':time.monotonic()}
            self.set_job(id=f'fast-{generation}',seq=req['seq'],lat=req['lat'],lon=req['lon'],
                         state='working',stage='Індекс джерел',progress=.02,message=None)
            self.preparer.submit(self.prepare_landing,dict(req),generation)

        def prepare_landing(self,req,generation):
            try:
                directory,meta=sources.prepare(self.tiles_src,self.work/'fast-index',self.cache,
                                              emit=lambda msg: print(msg,flush=True),point=(req['lon'],req['lat']))
                self.art.result()                   # facades and texture packs (started with the server)
                import fast_art
                fast_art.ensure(self.user)
                frame=sources.Frame.of(meta); i,j=frame.tile_of(req['lon'],req['lat'])
                # Connections stay on scheduler thread; preparation owns no persistent SQLite reader.
                with self.lock:
                    if generation!=self.generation:
                        return
                    self.frame=frame
                    self.landing['tile']=[i,j]
                    dest=self.user/'e2r_stream'/meta['id']/'tiles'
                    ready={}; scenes={}; far={}
                    for p in dest.glob('*.json'):
                        if p.name.endswith('.mats.json'):
                            continue
                        try:
                            m=json.loads(p.read_text())
                            if m.get('source')!=meta['id'] or m.get('generator')!=sources.GEOMETRY:
                                continue
                            if not all((self.user/it['shape'].lstrip('/')).exists() for it in m['items']):
                                continue
                            key='_'.join(map(str,m['tile']))
                            {'scene':scenes,'far':far}.get(m['layer'],ready)[key]=1
                        except (OSError,ValueError,KeyError):
                            continue
                    self.stream={'region':meta['id'],'dir':str(directory),'tile':sources.TILE,
                                 'ready':ready,'scenes':scenes,'far':far,'inflight':{},'failed':{},'queued':0,
                                 'built':0,'buildSeconds':0.,'bounds':meta['bbox'],'generator':sources.GEOMETRY}
                    self.out_of_region=set(); self.next_schedule=0
                self.set_job(region=meta['id'],tileSize=sources.TILE,tile=[i,j],
                             stage='Дороги і стартовий запас',progress=.2,generator=sources.GEOMETRY)
            except Exception as e:
                traceback.print_exc()
                if generation==self.generation:
                    self.set_job(state='error',message=str(e))

        def schedule(self):
            with self.lock:
                st=self.stream
                if not st:
                    return
                # Futures are consumed by the scheduler thread, not worker callbacks.
                for job,(future,region,key,layer) in list(self.future_jobs.items()):
                    if not future.done():
                        continue
                    del self.future_jobs[job]
                    if region!=st['region']:
                        continue
                    st['inflight'].pop(job,None)
                    try:
                        result=future.result()
                        st[{'road':'ready','scene':'scenes','far':'far'}[layer]][key]=1
                        st['built']+=1; st['buildSeconds']+=result['timings']['total']
                        if layer=='road':
                            self.history.append(result['timings']['total'])
                        self.work.mkdir(parents=True,exist_ok=True)
                        with (self.work/'fast-timings.jsonl').open('a',encoding='utf8') as f:
                            f.write(json.dumps(dict(result,region=region,at=time.time()))+'\n')
                    except Exception as e:
                        st['failed'][job]=st['failed'].get(job,0)+1
                        print('fast job failed',job,repr(e),flush=True)
                        traceback.print_exc()
                if not self.fast_store or str(self.fast_store.directory)!=st['dir']:
                    if self.fast_store:
                        self.fast_store.db.close()
                    self.fast_store=sources.Store(st['dir'])
                now=time.monotonic()
                if now>=self.next_schedule:
                    pose=self.pose
                    if not pose:
                        x,y=self.frame.local(self.landing['lon'],self.landing['lat'])
                        pose={'x':float(x),'y':float(y),'vx':0,'vy':0}
                    speed=max(83.333,math.hypot(pose.get('vx',0),pose.get('vy',0)))
                    delivery=max(2.,float(pose.get('deliveryP99',2.)))
                    horizon=max(12.,2*(percentile(self.history)+delivery+2))
                    wanted=candidates(self.fast_store,pose,horizon,speed)
                    self.desired={k:v for k,v in wanted.items() if self.fast_store.covered(self.frame.rect(v[1],v[2]))}
                    st['horizonSeconds']=horizon
                    st['loadRadius']=max(1000.,speed*(delivery+4)+sources.TILE*.71)
                    st['pose']=pose; self.next_schedule=now+.5
                landing_key='_'.join(map(str,self.landing['tile']))
                job=self.status['job'] or {}
                if job.get('state')=='working':
                    elapsed=now-self.landing['at']
                    if elapsed>120:
                        self.set_job(state='error',message='startup_budget_exceeded: 120 seconds')
                    elif landing_key in st['ready']:
                        path=self.user/'e2r_stream'/st['region']/'tiles'/f'{landing_key}.json'
                        m=json.loads(path.read_text())
                        if not m.get('spawn'):
                            self.set_job(state='error',message='no_drivable_spawn: select a road')
                        else:
                            self.set_job(state='ready',progress=1,stage='Завантаження доріг',
                                manifest=f'/e2r_stream/{st["region"]}/tiles/{landing_key}.json',
                                timings={'first_tile':elapsed},horizonZ=self.horizon_z(m['spawn']['pos']))
                # No unbounded executor backlog. Roads and scenes share one order: the car's arrival
                # at the tile (desired deadline), a scene SCENE_LAG s behind its road. Scenes used to
                # wait for an empty road queue and run one at a time (p50 1.2 s, p90 4 s in the
                # city): in the city the buildings came 10-30 s after the road (out/world 10-14).
                pending=[]
                for key,(deadline,i,j) in self.desired.items():
                    task=key+':road'
                    if key not in st['ready'] and task not in self.future_jobs and st['failed'].get(task,0)<2:
                        pending.append((-1 if key==landing_key and not self.pose else deadline,i,j,key,'road'))
                scene_running=sum(v[3]=='scene' for v in self.future_jobs.values())
                px,py=st['pose']['x'],st['pose']['y']
                for key,(deadline,i,j) in self.desired.items():
                    task=key+':scene'
                    if key in st['ready'] and key not in st['scenes'] and task not in self.future_jobs and st['failed'].get(task,0)<2:
                        distance=math.hypot((i+.5)*sources.TILE-px,(j+.5)*sources.TILE-py)
                        if key==landing_key and not self.pose:
                            pending.append((0,i,j,key,'scene'))
                        elif distance<=SCENE_R:
                            pending.append((deadline+SCENE_LAG+distance/1000,i,j,key,'scene'))
                # the far city: one job at a time while no road is waiting, nearest first,
                # for tiles within FAR_R that have no scene yet
                far_running=sum(v[3]=='far' for v in self.future_jobs.values())
                # one worker always stays free for a road (a far job at the start of a 300 km/h
                # replay delayed one past its deadline, vis-replay-05)
                if not any(x[4]=='road' for x in pending) and far_running==0 and len(self.future_jobs)<self.workers-1:
                    px,py=st['pose']['x'],st['pose']['y']
                    far_r=far_radius(st['pose'])
                    n=int(far_r//sources.TILE)+1; ci,cj=int(px//sources.TILE),int(py//sources.TILE)
                    best=None
                    for i in range(ci-n,ci+n+1):
                        for j in range(cj-n,cj+n+1):
                            key=f'{i}_{j}'
                            if key in st['far'] or key in st['scenes'] or st['failed'].get(key+':far',0)>=2 or key+':far' in self.future_jobs:
                                continue
                            d=math.hypot((i+.5)*sources.TILE-px,(j+.5)*sources.TILE-py)
                            if d>far_r or (best and d>=best[0]):
                                continue
                            if key not in self.far_covered:
                                self.far_covered[key]=self.fast_store.covered(self.frame.rect(i,j))
                            if self.far_covered[key]:
                                best=(d,i,j,key)
                    if best:
                        pending.append((5000+best[0],best[1],best[2],best[3],'far'))
                st['queued']=len(pending)
                scene_cap=max(1,self.workers-1)     # one worker is always there for a road
                for _,i,j,key,layer in sorted(pending):
                    if len(self.future_jobs)>=self.workers:
                        break
                    if layer=='scene' and scene_running>=scene_cap:
                        continue
                    task=key+':'+layer
                    center=(self.landing['lon'],self.landing['lat']) if key==landing_key else None
                    future=self.builders.submit(fast.run,st['dir'],i,j,str(self.work),
                        str(self.user/'e2r_stream'),str(self.cache),center,layer)
                    self.future_jobs[task]=(future,st['region'],key,layer)
                    st['inflight'][task]=now
                    if layer=='scene':
                        scene_running+=1

        def horizon_z(self,pos):
            """Height of the plane under the whole world (the land beyond the far city): the
            lowest ground within 9 km, so it never covers a valley nearer than the horizon."""
            try:
                g=np.linspace(-9000,9000,13)
                pts=np.array([(pos[0]+a,pos[1]+b) for a in g for b in g if math.hypot(a,b)<=9000])
                h=self.fast_store.terrain.sample(pts)
                h=h[np.isfinite(h)]
                return float(h.min())-5 if len(h) else float(pos[2])-40
            except Exception:
                return float(pos[2])-40

        def stream_status(self):
            status=super().stream_status()
            if status:
                st=self.stream
                status.update(generator=sources.GEOMETRY,scenes=dict(st['scenes']),far=dict(st['far']),farRadius=FAR_R,farRadiusFast=min(FAR_R,FAR_R_FAST),farFast=FAR_FAST,
                    wanted={k:v[0] for k,v in self.desired.items()},
                    buildP99=percentile(self.history),horizonSeconds=st.get('horizonSeconds',12),
                    loadRadius=st.get('loadRadius',1000),startupSeconds=time.monotonic()-self.landing['at'])
                # the widget's statistics: the frame and the car on the globe; how far the car is
                # from the edge of the indexed window (the game warns near it)
                pose,frame,b=self.pose,self.frame,st.get('bounds')
                if frame:
                    lon,lat=frame.geographic(0.,0.)
                    status.update(origin=[round(float(lon),6),round(float(lat),6)],crs=frame.crs)
                if pose and frame and b:
                    try:
                        lon,lat=frame.geographic(pose['x'],pose['y'])
                        lon,lat=float(lon),float(lat)
                        status['car']=[round(lon,6),round(lat,6)]
                        status['edgeKm']=round(min((lon-b[0])*71.5,(b[2]-lon)*71.5,(lat-b[1])*111.2,(b[3]-lat)*111.2),2)
                    except Exception:
                        pass
            return status
    return FastServer
