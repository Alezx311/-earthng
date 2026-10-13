"""Canonical road surfaces -> clipped, bounded binary meshes, without roadgen/SUMO.

Experimental backend. Acceptance is measured by fast_bench and the in-game harness,
never inferred from a successful mesh export.
"""
import argparse
from collections import OrderedDict
import json
import math
import os
from pathlib import Path
import time

import numpy as np
import shapely
from shapely.geometry import LineString, Polygon, box
from shapely.ops import substring

import fast_sources as sources
import s1

COLORS = {'road': [.18,.19,.20,1], 'mark': [.9,.88,.75,1], 'ground': [.29,.36,.22,1],
          'water': [.12,.29,.38,1], 'building': [.58,.55,.49,1]}
# Without groundType the wheels find no grip: fast-game-03 spun at 74 mph on the spot.
GROUND = {'road': 'ASPHALT', 'mark': 'ASPHALT', 'ground': 'GRASS', 'water': 'MUD', 'building': 'ROCK'}
MATERIALS = {f'e2rf_{name}': {'name':f'e2rf_{name}', 'mapTo':f'e2rf_{name}', 'class':'Material', 'version':1.5,
             'groundType':GROUND[name], 'Stages':[{'diffuseColor':color}], 'doubleSided':False}
             for name,color in COLORS.items()}
_STORES = OrderedDict()


def store_at(directory):
    key = str(directory)
    if key not in _STORES:
        _STORES[key] = sources.Store(directory)
        if len(_STORES)>2:
            _, old = _STORES.popitem(last=False); old.db.close()
    return _STORES[key]


def warm(path):
    """Read a file just written: the on-access virus scan of a new file happens here, in the
    worker, and is cached per file. Unwarmed, the game's first io.open of a piece took up to
    179 ms in one frame under build load (fast-replay-game-01); warmed it takes ~0.1 ms."""
    with open(path,'rb') as f:
        f.read()


def triangles(polygon):
    if polygon.is_empty:
        return []
    return [np.asarray(t.exterior.coords)[:3,:2] for t in
            shapely.get_parts(shapely.constrained_delaunay_triangles(polygon)) if t.area>1e-8]


def clip_triangle(tri, rect):
    """Sutherland-Hodgman in 3D. Interpolate z, never resample at a tile seam."""
    points = [np.asarray(p,dtype=float) for p in tri]
    for axis, limit, sign in ((0,rect[0],1),(0,rect[2],-1),(1,rect[1],1),(1,rect[3],-1)):
        clipped=[]
        for a,b in zip(points,points[1:]+points[:1]):
            ain,bin = sign*(a[axis]-limit)>=-1e-9, sign*(b[axis]-limit)>=-1e-9
            if ain:
                clipped.append(a)
            if ain != bin:
                c=a+(b-a)*((limit-a[axis])/(b[axis]-a[axis])); c[axis]=limit
                clipped.append(c)
        points=clipped
        if len(points)<3:
            return []
    return [np.array([points[0],points[k],points[k+1]]) for k in range(1,len(points)-1)]


class Mesh:
    def __init__(self, rect):
        self.rect = rect
        self.origin = [(rect[0]+rect[2])/2,(rect[1]+rect[3])/2,0]
        self.faces = {}

    def add(self, kind, tri):
        for clipped in clip_triangle(tri,self.rect):
            normal=np.cross(clipped[1]-clipped[0],clipped[2]-clipped[0])
            norm=np.linalg.norm(normal)
            if norm<1e-8:
                continue
            if kind != 'building' and normal[2]<0:
                clipped=clipped[[0,2,1]]; normal=-normal
            self.faces.setdefault(kind,[]).append((clipped,normal/norm))

    def add_many(self, kind, tris):
        """add() for many triangles: the ones wholly inside the tile skip clipping, in one pass."""
        if not len(tris):
            return
        t=np.asarray(tris,dtype=float)
        x0,y0,x1,y1=self.rect
        inside=((t[:,:,0]>=x0)&(t[:,:,0]<=x1)&(t[:,:,1]>=y0)&(t[:,:,1]<=y1)).all(axis=1)
        for tri in t[~inside]:
            self.add(kind,tri)
        t=t[inside]
        a,b=t[:,1]-t[:,0],t[:,2]-t[:,0]
        normal=np.column_stack([a[:,1]*b[:,2]-a[:,2]*b[:,1],a[:,2]*b[:,0]-a[:,0]*b[:,2],a[:,0]*b[:,1]-a[:,1]*b[:,0]])
        norm=np.linalg.norm(normal,axis=1)
        keep=norm>=1e-8
        t,normal,norm=t[keep],normal[keep],norm[keep]
        if kind!='building':
            down=normal[:,2]<0
            t[down]=t[down][:,[0,2,1]]; normal[down]*=-1
        unit=normal/norm[:,None]
        self.faces.setdefault(kind,[]).extend(zip(t,unit))

    def polygon(self, kind, geom, height):
        for xy in triangles(geom):
            a,b=xy[1]-xy[0],xy[2]-xy[0]
            if a[0]*b[1]-a[1]*b[0] < 0:
                xy=xy[[0,2,1]]
            z = np.full(3,height) if np.isscalar(height) else height(xy)
            self.add(kind,np.column_stack([xy,z]))

    def write(self, dest, region, key, layer):
        items=[]
        for kind, faces in sorted(self.faces.items()):
            for offset in range(0,len(faces),750):
                batch=faces[offset:offset+750]
                verts=np.array([v for v,n in batch]).reshape(-1,3)
                normals=np.repeat([n for v,n in batch],3,axis=0)
                # Input is CCW; ProceduralMesh expects the opposite winding.
                idx=np.arange(len(verts)).reshape(-1,3)[:,[0,2,1]].ravel().tolist()
                sub={'mat':'e2rf_'+kind,'p':(verts-self.origin).ravel().tolist(),
                     'n':normals.ravel().tolist(),'t':(verts[:,:2]*.1).ravel().tolist(),'i':idx}
                name=f'{key}_{layer}_{kind}_{offset//750}'
                rel=f'tiles/{key}/{name}.bin'
                path=dest/rel; path.parent.mkdir(parents=True,exist_ok=True)
                path.write_bytes(s1.write_bin([sub])); warm(path)
                items.append({'kind':'proc','name':'e2rf_'+name,'pos':self.origin,
                              'shape':f'/e2r_stream/{region}/{rel}','triangles':len(batch),
                              'mats':['e2rf_'+kind], 'layer':layer})
        return items


GRID = 25.0       # ground cells, m; global grid (tiles are multiples of it)
SHOULDER = 3.0    # verge from the road edge down/up to the ground, m


def ground_z(store, xy):
    """The ground mesh height at xy: the same bilinear cell as build_scene, so a verge's outer
    edge meets the ground exactly instead of leaving a step (fast-game-04 hung a wheel in one)."""
    xy=np.asarray(xy,dtype=float).reshape(-1,2)
    i=np.floor(xy[:,0]/GRID); j=np.floor(xy[:,1]/GRID)
    corners=np.array([np.column_stack([i+di,j+dj])*GRID for di,dj in ((0,0),(1,0),(0,1),(1,1))]).reshape(-1,2)
    h=(store.terrain.sample(corners)-.25).reshape(4,-1)
    u=xy[:,0]/GRID-i; v=xy[:,1]/GRID-j
    return (h[0]*(1-u)+h[1]*u)*(1-v)+(h[2]*(1-u)+h[3]*u)*v


def strip(inner, outer):
    """Triangles between two matching 3D polylines."""
    tris=[]
    for k in range(len(inner)-1):
        tris.extend([np.array([inner[k],outer[k],outer[k+1]]),np.array([inner[k],outer[k+1],inner[k+1]])])
    return tris


def junction_verge(store, geom, height):
    """A verge ring round a ground-level junction (convex) and the hole it leaves in the ground."""
    ring=np.asarray(geom.exterior.coords)[:,:2]
    c=np.asarray(geom.centroid.coords[0])
    d=ring-c; d/=np.maximum(np.linalg.norm(d,axis=1)[:,None],1e-9)
    outer=ring+d*SHOULDER
    inner3=np.column_stack([ring,np.full(len(ring),height)])
    outer3=np.column_stack([outer,ground_z(store,outer)])
    return strip(inner3,outer3), Polygon(outer).buffer(0)


def road_surface(store, data, footprint=True):
    """Stable strips with shared, flat junction mouths and a sampled longitudinal profile."""
    line=LineString(data['xy']); a,b=data['cuts']
    length=line.length
    knots=np.unique(np.r_[a,np.arange(math.ceil(a/8)*8,length-b,8),length-b])
    xy=np.array([line.interpolate(float(s)).coords[0] for s in knots])
    # The tangent uses a fixed metric neighbourhood, independent of tile and mesh subdivision.
    before=np.array([line.interpolate(max(0,float(s)-.25)).coords[0] for s in knots])
    after=np.array([line.interpolate(min(length,float(s)+.25)).coords[0] for s in knots])
    tangent=after-before
    tangent/=np.maximum(np.linalg.norm(tangent,axis=1)[:,None],1e-9)
    normal=np.column_stack([-tangent[:,1],tangent[:,0]])*data['width']/2
    z=store.terrain.sample(xy,smooth=True)+.12
    ends=[store.height(n) for n in data['nodes']]
    tags=data['tags']
    bridge=tags.get('bridge') not in (None,'no')
    tunnel=tags.get('tunnel') not in (None,'no')
    u=(knots-a)/max(.01,length-a-b)
    if bridge:
        z=ends[0]*(1-u)+ends[1]*u
    else:
        # C1 endpoint corrections; junction elevations are shared for every incident road.
        span=min(120.,max(1.,(length-a-b)/2))
        w0=np.clip(1-(knots-a)/span,0,1); w0=w0*w0*(3-2*w0)
        w1=np.clip(1-(length-b-knots)/span,0,1); w1=w1*w1*(3-2*w1)
        z=z+(ends[0]-z[0])*w0+(ends[1]-z[-1])*w1
        if tunnel:
            z-=number_layer(tags)*3.5*np.sin(np.pi*u)**2
    left,right=np.column_stack([xy+normal,z]),np.column_stack([xy-normal,z])
    tris=strip(left,right)
    verge=[]; outline=(left[:,:2],right[:,:2])
    if not bridge and not tunnel:
        # verges slope from the road edge to the ground's own height: no step, no gap
        unit=normal/np.maximum(np.linalg.norm(normal,axis=1)[:,None],1e-9)
        outs=[xy+normal*sign+unit*sign*SHOULDER for sign in (1,-1)]
        heights=ground_z(store,np.vstack(outs)).reshape(2,-1)
        for edge,out,h in zip((left,right),outs,heights):
            verge.extend(strip(edge,np.column_stack([out,h])))
        outline=outs
    # one outline per road (not a union of its triangles: 28k Polygon() calls cost ~1 s a tile)
    foot=shapely.make_valid(Polygon(np.vstack([outline[0],outline[1][::-1]]))) if footprint else None
    return tris, foot, xy, z, tangent, knots, verge


def number_layer(tags):
    return max(1,abs(sources.number(tags.get('layer'),1)))


def build_road(store, rect, center=None):
    mesh=Mesh(rect); candidates=[]
    pad=SHOULDER+1   # a verge reaches into the tile from a road whose bounds do not
    for fid,kind,data,geom in store.query((rect[0]-pad,rect[1]-pad,rect[2]+pad,rect[3]+pad),{'road','junction'}):
        if kind=='junction':
            h=store.height(data['node'])
            mesh.polygon('road',geom,h)
            if data['raise']==0:
                mesh.add_many('ground',junction_verge(store,geom,h)[0])
            continue
        tris,_,xy,z,tangents,knots,verge=road_surface(store,data,footprint=False)
        mesh.add_many('road',tris)
        mesh.add_many('ground',verge)
        # Marking uses the exact strip profile; no independent road height sampling.
        if data['width']>=5:
            for k in range(len(xy)-1):
                if int(knots[k]/8)%2:
                    continue
                n0=np.array([-tangents[k,1],tangents[k,0]])*.06
                n1=np.array([-tangents[k+1,1],tangents[k+1,0]])*.06
                q=np.array([[*(xy[k]+n0),z[k]+.008],[*(xy[k]-n0),z[k]+.008],
                            [*(xy[k+1]-n1),z[k+1]+.008],[*(xy[k+1]+n1),z[k+1]+.008]])
                mesh.add('mark',q[[0,1,2]]); mesh.add('mark',q[[0,2,3]])
        for k,p in enumerate(xy):
            if rect[0]+5<p[0]<rect[2]-5 and rect[1]+5<p[1]<rect[3]-5:
                normal=np.array([-tangents[k,1],tangents[k,0]])
                # the right lane only where a 2 m car fits in it with room; narrow roads: the middle
                pos=p-normal*max(0.,min(1.65,data['width']/2-1.6))
                target=np.array(center or mesh.origin[:2])
                # prefer a real carriageway over a lane or service road next to the point
                penalty=0 if data['width']>=5 else 150
                candidates.append((np.linalg.norm(pos-target)+penalty,{'pos':[float(pos[0]),float(pos[1]),float(z[k]+.65)],
                                  'yaw':float(math.atan2(tangents[k,0],-tangents[k,1]))}))
    spawn=min(candidates,key=lambda x:x[0])[1] if candidates else None
    return mesh,spawn


def build_scene(store,rect):
    mesh=Mesh(rect)
    ground_holes=[]
    pad=SHOULDER+1
    entries=store.query((rect[0]-pad,rect[1]-pad,rect[2]+pad,rect[3]+pad))+store.scenery(rect)
    for _,kind,data,geom in entries:
        if kind=='road' and data['tags'].get('bridge') in (None,'no'):
            ground_holes.append(road_surface(store,data)[1])
        elif kind=='junction' and data['raise']==0:
            ground_holes.append(geom.union(junction_verge(store,geom,0.)[1]))
        elif kind=='water':
            ground_holes.append(geom)
            # Canonical water body level, shared by all tiles.
            p=geom.representative_point()
            h=float(store.terrain.sample([[p.x,p.y]])[0])-.5
            mesh.polygon('water',geom.intersection(box(*rect)),h)
        elif kind=='building':
            tags=data['tags']; p=geom.representative_point()
            base=float(store.terrain.sample([[p.x,p.y]])[0])
            height=max(2.5,min(250,sources.number(tags.get('height'),sources.number(tags.get('building:levels'),3)*3)))
            mesh.polygon('building',geom.intersection(box(*rect)),base+height)
            walls=[]
            for poly in shapely.get_parts(geom):
                if poly.geom_type!='Polygon':
                    continue
                for ring in [poly.exterior,*poly.interiors]:
                    for p,q in zip(list(ring.coords),list(ring.coords)[1:]):
                        quad=np.array([[*p[:2],base],[*q[:2],base],[*q[:2],base+height],[*p[:2],base+height]])
                        walls.extend([quad[[0,1,2]],quad[[0,2,3]]])
            mesh.add_many('building',walls)
    holes=shapely.union_all(ground_holes)
    # Fixed 25 m grid (20 intervals/tile); vector sample once, then interpolate planar cells.
    xs=np.arange(rect[0],rect[2]+1,GRID); ys=np.arange(rect[1],rect[3]+1,GRID)
    points=np.array([(x,y) for y in ys for x in xs]); heights=store.terrain.sample(points).reshape(len(ys),len(xs))-.25
    for j in range(len(ys)-1):
        for i in range(len(xs)-1):
            square=box(xs[i],ys[j],xs[i+1],ys[j+1]).difference(holes)
            def at(xy):
                u=(xy[:,0]-xs[i])/25; v=(xy[:,1]-ys[j])/25
                return (heights[j,i]*(1-u)+heights[j,i+1]*u)*(1-v)+(heights[j+1,i]*(1-u)+heights[j+1,i+1]*u)*v
            mesh.polygon('ground',square,at)
    return mesh


def run(directory,i,j,work,dest_root,cache=None,center=None,layer='road'):
    started=time.perf_counter(); store=store_at(directory); rect=store.frame.rect(i,j)
    if not store.covered(rect):
        raise ValueError(f'no_osm: tile {i},{j} outside source coverage')
    if layer=='road':
        local=tuple(float(v) for v in store.frame.local(*center)) if center else None
        mesh,spawn=build_road(store,rect,local)
    else:
        mesh,spawn=build_scene(store,rect),None
    geometry=time.perf_counter()-started
    region=store.meta['id']; dest=Path(dest_root)/region; key=f'{i}_{j}'
    items=mesh.write(dest,region,key,layer)
    mat=dest/'tiles'/f'{key}.{layer}.mats.json'; mat.parent.mkdir(parents=True,exist_ok=True)
    mat.write_text(json.dumps(MATERIALS),encoding='utf8'); warm(mat)
    manifest={'version':1,'generator':sources.GEOMETRY,'source':region,'layer':layer,'tile':[i,j],
              'rect':rect,'lift':sources.LIFT,'materials':f'/e2r_stream/{region}/tiles/{mat.name}',
              'mats':list(MATERIALS),'items':items,'spawn':spawn,'drivable':bool(mesh.faces.get('road')),
              'triangles':sum(it['triangles'] for it in items)}
    suffix='' if layer=='road' else '.scene'
    final=dest/'tiles'/f'{key}{suffix}.json'; tmp=final.with_suffix('.json.tmp')
    tmp.write_text(json.dumps(manifest,separators=(',',':')),encoding='utf8'); os.replace(tmp,final); warm(final)
    timings={'geometry':geometry,'deliver':time.perf_counter()-started-geometry,'total':time.perf_counter()-started}
    return {'tile':[i,j],'layer':layer,'items':len(items),'triangles':manifest['triangles'],
            'spawn':spawn,'timings':timings}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--sources',required=True); p.add_argument('--dem',required=True)
    p.add_argument('--out',required=True); p.add_argument('--lat',type=float,default=50.4475)
    p.add_argument('--lon',type=float,default=30.5225); p.add_argument('--scene',action='store_true')
    a=p.parse_args(); started=time.perf_counter()
    directory,meta=sources.prepare(a.sources,Path(a.out)/'index',a.dem)
    i,j=sources.Frame().tile_of(a.lon,a.lat)
    print(json.dumps(run(directory,i,j,a.out,Path(a.out)/'stream',center=(a.lon,a.lat))))
    if a.scene:
        print(json.dumps(run(directory,i,j,a.out,Path(a.out)/'stream',layer='scene')))
    print(f'elapsed {time.perf_counter()-started:.2f}s (index original build {meta["seconds"]:.2f}s; use a new --out for cold measurement)')


if __name__=='__main__':
    main()
