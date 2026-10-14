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
import zlib

import numpy as np
import shapely
from shapely.geometry import LineString, Polygon, box
from shapely.ops import substring

import fast_photo
import fast_profile
import fast_sources as sources
import fast_style as style
import s1

# Every material has a groundType: without one the wheels find no grip (fast-game-03 spun
# at 74 mph on the spot).
MATERIALS = style.MATERIALS
MAJOR = {'motorway', 'trunk', 'primary', 'secondary'}
WALKS = {'primary', 'secondary', 'tertiary', 'residential', 'living_street', 'unclassified'}
SIDEWALK = 2.0    # m each side, flush with the road
DECOR = {'mark', 'curb', 'roof', 'roof_tile', 'roof_metal', 'parapet', 'fence_wall', 'fence_mesh', 'car'}   # no collision needed: spawned outside the MissionGroup
CURB = 0.25       # m of the sidewalk next to the road drawn as a curb band
LAMP_EVERY = 30.0 # m between street lamps (sidewalk side)
ZEBRA = {'primary', 'secondary', 'tertiary', 'residential', 'unclassified', 'living_street'}
_STORES = OrderedDict()


def is_wall(kind):
    """Vertical faces: keep their facing, keep hard edges, UV along the wall."""
    return kind.startswith('wall_') or kind in ('parapet','fence_wall','fence_mesh','car')


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
        self.statics = []      # TSStatic items (trees), written into the manifest as they are

    def add(self, kind, tri):
        for clipped in clip_triangle(tri,self.rect):
            # rows may carry u, v after x, y, z: clipping interpolates them with the position
            normal=np.cross(clipped[1,:3]-clipped[0,:3],clipped[2,:3]-clipped[0,:3])
            norm=np.linalg.norm(normal)
            if norm<1e-8:
                continue
            if not is_wall(kind) and normal[2]<0:
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
        a,b=t[:,1,:3]-t[:,0,:3],t[:,2,:3]-t[:,0,:3]
        normal=np.column_stack([a[:,1]*b[:,2]-a[:,2]*b[:,1],a[:,2]*b[:,0]-a[:,0]*b[:,2],a[:,0]*b[:,1]-a[:,1]*b[:,0]])
        norm=np.linalg.norm(normal,axis=1)
        keep=norm>=1e-8
        t,normal,norm=t[keep],normal[keep],norm[keep]
        if not is_wall(kind):
            down=normal[:,2]<0
            t[down]=t[down][:,[0,2,1]]; normal[down]*=-1
        unit=normal/norm[:,None]
        self.faces.setdefault(kind,[]).extend(zip(t,unit))

    def polygon(self, kind, geom, height):
        tris=triangles(geom)
        if not tris:
            return
        xy=np.array(tris)
        a,b=xy[:,1]-xy[:,0],xy[:,2]-xy[:,0]
        cw=a[:,0]*b[:,1]-a[:,1]*b[:,0] < 0
        xy[cw]=xy[cw][:,[0,2,1]]
        # height(points) is evaluated once for every vertex of the polygon
        z=np.full(xy.shape[:2],height) if np.isscalar(height) else np.asarray(height(xy.reshape(-1,2))).reshape(-1,3)
        self.add_many(kind,np.concatenate([xy,z[:,:,None]],axis=2))

    PIECE = 1500      # triangles per ProceduralMesh: one createMesh call, ~8 ms at most

    def submesh(self, kind, batch):
        rows=np.array([v for v,n in batch])
        corner=rows[:,:,:3].reshape(-1,3)
        given=rows[:,:,3:5].reshape(-1,2) if rows.shape[2]>=5 else None
        face_n=np.repeat([n for v,n in batch],3,axis=0)
        # Shared vertices: ~2x fewer for the game's Lua to turn into tables, and smooth
        # normals on ground and roads. Walls keep hard edges (welded by normal too).
        weld=np.round(corner*1000).astype(np.int64)
        if is_wall(kind):
            weld=np.column_stack([weld,np.round(face_n*100).astype(np.int64)])
        if given is not None:
            weld=np.column_stack([weld,np.round(given*1e4).astype(np.int64)])
        _,first,inverse=np.unique(weld,axis=0,return_index=True,return_inverse=True)
        inverse=inverse.ravel()
        verts=corner[first]
        normals=np.zeros_like(verts); np.add.at(normals,inverse,face_n)
        normals/=np.maximum(np.linalg.norm(normals,axis=1)[:,None],1e-9)
        # Input is CCW; ProceduralMesh expects the opposite winding.
        idx=inverse.reshape(-1,3)[:,[0,2,1]].ravel().tolist()
        return {'mat':'e2rf_'+kind,'p':(verts-self.origin).ravel().tolist(),
                'n':normals.ravel().tolist(),
                't':(given[first] if given is not None else uv(kind,verts,normals)).ravel().tolist(),'i':idx}

    def write(self, dest, region, key, layer, all_decor=False):
        """Pieces of up to PIECE triangles, each holding submeshes of several kinds: every
        kind its own object made be:reloadCollision 78 ms (fast-replay-game-04). all_decor: the
        whole tile is decoration (the far layer)."""
        items=[]; subs=[]; tris=0; mats=[]
        def flush(decor=False):
            nonlocal subs,tris,mats
            if not subs:
                return
            name=f'{key}_{layer}_{len(items)}'
            rel=f'tiles/{key}/{name}.bin'
            path=dest/rel; path.parent.mkdir(parents=True,exist_ok=True)
            path.write_bytes(s1.write_bin(subs)); warm(path)
            items.append({'kind':'proc','name':'e2rf_'+name,'pos':self.origin,
                          'shape':f'/e2r_stream/{region}/{rel}','triangles':tris,
                          'mats':mats, 'layer':layer, **({'decor':True} if decor else {})})
            subs,tris,mats=[],0,[]
        # decoration (lines, curbs, roofs) in pieces of its own: the game keeps them out of the
        # static collision, which grew to 90 ms with them (fast-replay-game-07)
        for decor in (False,True):
            for kind, faces in sorted(self.faces.items()):
                if (all_decor or kind in DECOR)!=decor:
                    continue
                for offset in range(0,len(faces),750):
                    batch=faces[offset:offset+750]
                    if tris+len(batch)>self.PIECE:
                        flush(decor)
                    subs.append(self.submesh(kind,batch)); tris+=len(batch)
                    if 'e2rf_'+kind not in mats: mats.append('e2rf_'+kind)
            flush(decor)
        return items


def uv(kind, verts, normals):
    """Texture coordinates in world metres / repeat: seamless across pieces and tiles."""
    _, _, _, scale, mode = style.KINDS.get(kind, (None, None, None, 8.0, 'plan'))
    if mode=='wall':
        t=np.column_stack([-normals[:,1],normals[:,0]])
        t/=np.maximum(np.linalg.norm(t,axis=1)[:,None],1e-9)
        return np.column_stack([(verts[:,0]*t[:,0]+verts[:,1]*t[:,1])/scale,verts[:,2]/scale])
    return verts[:,:2]/scale


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


def corner_heights(data):
    """Height at a junction polygon vertex: the corner's own road profile at its mouth. The
    polygon is the corners' convex hull, so every vertex is one of them (nearest = exact)."""
    corners=np.asarray(data['corners'],dtype=float); cz=np.asarray(data['cz'],dtype=float)
    def at(xy):
        xy=np.asarray(xy,dtype=float).reshape(-1,2)
        d=((xy[:,None,:]-corners[None,:,:])**2).sum(axis=2)
        return cz[d.argmin(axis=1)]
    return at


def junction_star(data):
    """All mouth corners ordered by angle round the node: every corner is a vertex, so every
    road's strip ends exactly on a junction edge. (The convex hull dropped the corners of a
    narrow road meeting at a sharp angle and left its strip end 0.19 m off the surface.)"""
    corners=np.asarray(data['corners'],dtype=float); cz=np.asarray(data['cz'],dtype=float)
    c=np.asarray(data['xy'],dtype=float)
    order=np.argsort(np.arctan2(corners[:,1]-c[1],corners[:,0]-c[0]))
    ring=np.vstack([corners[order],corners[order[:1]]]); z=np.r_[cz[order],cz[order[:1]]]
    return c, ring, z


def junction_polygon(data):
    c,ring,z=junction_star(data)
    return shapely.make_valid(Polygon(ring))


def junction_fan(geom, data):
    """The junction surface as a fan from its node over the star of corners: every slope inside
    is a slope from the node to one mouth, never a sliver between two mouths (a hull
    triangulation tilted 16-24 % between corners)."""
    c,ring,z=junction_star(data)
    cz=float(data.get('h',np.mean(z)))
    return [np.array([[*c,cz],[*ring[k],z[k]],[*ring[k+1],z[k+1]]]) for k in range(len(ring)-1)]


def junction_walks(store, data):
    """True when a road at this junction has sidewalks: the junction then gets a sidewalk ring."""
    for sid in data['segments']:
        row=store.db.execute('SELECT data FROM features WHERE id=?',(sid,)).fetchone()
        if row and walk_sides(json.loads(row[0])['tags']):
            return True
    return False


def junction_verge(store, geom, height, inset=0.):
    """Rings round a ground-level junction (convex): a sidewalk ring of width inset at the
    junction's own edge heights, then the verge down to the ground. Returns verge triangles,
    the hole they leave in the ground, sidewalk triangles and the sidewalk zone."""
    if isinstance(geom,dict):          # junction data: the star round its node
        c,ring,z=junction_star(geom)
        if not callable(height): z=np.full(len(ring),height)
    else:
        ring=np.asarray(geom.exterior.coords)[:,:2]
        c=np.asarray(geom.centroid.coords[0])
        z=height(ring) if callable(height) else np.full(len(ring),height)
    d=ring-c; d/=np.maximum(np.linalg.norm(d,axis=1)[:,None],1e-9)
    inner3=np.column_stack([ring,z])
    walk=[]; zone=None
    if inset>0:
        mid3=np.column_stack([ring+d*inset,z])
        walk=strip(inner3,mid3); zone=Polygon(mid3[:,:2]).buffer(0)
        inner3=mid3
    outer=ring+d*(inset+SHOULDER)
    outer3=np.column_stack([outer,ground_z(store,outer)])
    return strip(inner3,outer3), Polygon(outer).buffer(0), walk, zone


def walk_sides(tags):
    """Which sides get a sidewalk: city streets by default, unless the tags say otherwise."""
    if tags.get('highway') not in WALKS:
        return ()
    v=tags.get('sidewalk')
    if v is None and tags.get('oneway') in ('yes','1'):
        # one carriageway of a divided road: the inner sidewalk would lie on the other one
        return ('right',)
    return {'no':(),'none':(),'separate':('left','right'),'left':('left',),'right':('right',)}.get(v or 'both',('left','right'))


def lane_marks(xy, z, tangent, knots, width, tags):
    """Lane lines as thin quads on the road plane: dashed 3 on / 9 off, a solid centre on
    four lanes and up, solid edge lines on major roads. The dash phase runs from the
    segment's start, so it is the same in every tile."""
    if width<5 or len(knots)<2:
        return []
    oneway=tags.get('oneway') in ('yes','1','-1')
    n=int(sources.number(tags.get('lanes'),max(1,round(width/3.3)) if oneway else 2))
    n=max(1,min(n,int(width//2.5)))
    lines=[]
    lw=width/n
    for k in range(1,n):
        centre=not oneway and abs(k-n/2)<.01
        lines.append((-width/2+k*lw,.12,None if centre and n>=4 else (3.,9.)))
    if tags.get('highway','').replace('_link','') in MAJOR and width>=7:
        lines+=[(width/2-.3,.15,None),(-width/2+.3,.15,None)]
    out=[]
    s0,s1=knots[0],knots[-1]
    for off,w,dash in lines:
        if dash:
            period=dash[0]+dash[1]
            spans=[(a,a+dash[0]) for a in np.arange(math.ceil(s0/period)*period,s1-dash[0],period)]
        else:
            spans=[(s0,s1)]
        for a,b in spans:
            ss=np.r_[a,knots[(knots>a)&(knots<b)],b]
            p=np.column_stack([np.interp(ss,knots,xy[:,0]),np.interp(ss,knots,xy[:,1])])
            t=np.column_stack([np.interp(ss,knots,tangent[:,0]),np.interp(ss,knots,tangent[:,1])])
            t/=np.maximum(np.linalg.norm(t,axis=1)[:,None],1e-9)
            nrm=np.column_stack([-t[:,1],t[:,0]])
            zz=np.interp(ss,knots,z)+.008
            c=p+nrm*off
            out.extend(strip(np.column_stack([c-nrm*w/2,zz]),np.column_stack([c+nrm*w/2,zz])))
    return out


def zebras(xy, z, tangent, knots, data):
    """Pedestrian crossings at the junction mouths of city streets: 0.5 m stripes every 1 m
    across the carriageway, 3 m long, starting 0.5 m out of the junction."""
    tags=data['tags']; w=data['width']
    if tags.get('highway') not in ZEBRA or w<6 or len(knots)<2:
        return []
    out=[]
    for end,cut in enumerate(data['cuts']):
        if cut<=0:
            continue
        a=knots[0]+.5 if end==0 else knots[-1]-3.5
        b=a+3.
        if a<knots[0] or b>knots[-1]:
            continue
        ss=np.array([a,b])
        p=np.column_stack([np.interp(ss,knots,xy[:,0]),np.interp(ss,knots,xy[:,1])])
        t=np.column_stack([np.interp(ss,knots,tangent[:,0]),np.interp(ss,knots,tangent[:,1])])
        t/=np.maximum(np.linalg.norm(t,axis=1)[:,None],1e-9)
        nrm=np.column_stack([-t[:,1],t[:,0]])
        zz=np.interp(ss,knots,z)+.009
        for off in np.arange(-w/2+.5,w/2-.4,1.):
            l=np.column_stack([p+nrm*off,zz]); r=np.column_stack([p+nrm*(off+.5),zz])
            out.extend(strip(l,r))
    return out


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
    # the whole graph's solved profile (fast_profile): no per-segment DEM, so no ramps between
    # independently sampled node heights, no pits under building passages
    ps,ph=data['prof']
    z=np.interp(knots,ps,ph)+sources.ROAD_LIFT
    k=fast_profile.kind(data['tags'])
    bridge,tunnel=k=='bridge',k=='tunnel'
    left,right=np.column_stack([xy+normal,z]),np.column_stack([xy-normal,z])
    tris=strip(left,right)
    unit=normal/np.maximum(np.linalg.norm(normal,axis=1)[:,None],1e-9)
    # sidewalks: flush with the road, on the sides the tags allow (left = +normal)
    sides=walk_sides(data['tags'])
    walk=[]; curb=[]; edges=[]
    for edge,sign,side in ((left,1,'left'),(right,-1,'right')):
        if side in sides:
            # a light curb band at the road edge, then the paving; both flush with the road
            kerb=np.column_stack([edge[:,:2]+unit*sign*CURB,z])
            outer=np.column_stack([edge[:,:2]+unit*sign*SIDEWALK,z])
            curb.extend(strip(edge,kerb)); walk.extend(strip(kerb,outer))
            edge=outer
        edges.append(edge)
    verge=[]; outline=(edges[0][:,:2],edges[1][:,:2])
    if not bridge and not tunnel:
        # verges slope from the road (or sidewalk) edge to the ground's own height: no step
        outs=[edges[0][:,:2]+unit*SHOULDER,edges[1][:,:2]-unit*SHOULDER]
        heights=ground_z(store,np.vstack(outs)).reshape(2,-1)
        for edge,out,h in zip(edges,outs,heights):
            verge.extend(strip(edge,np.column_stack([out,h])))
        outline=outs
    # one outline per road (not a union of its triangles: 28k Polygon() calls cost ~1 s a tile)
    foot=shapely.make_valid(Polygon(np.vstack([outline[0],outline[1][::-1]]))) if footprint else None
    return tris, foot, xy, z, tangent, knots, verge, walk, edges, curb


def clip_verges(verges, asphalt):
    """Verge triangles minus every ground-level road surface. A verge slopes from its own
    road to the ground and used to lie on top of the next road or a junction's other
    mouths (1-6 % of a tile's asphalt under grass, up to 2 m above it). Cut pieces keep
    their parent triangle's plane. asphalt: the separate road/junction polygons."""
    if not len(verges) or not asphalt:
        return verges
    v=np.asarray(verges,dtype=float)
    polys=shapely.polygons(v[:,:,:2])
    parts=np.array(asphalt,dtype=object)
    # overlap with the road shrunk by 1 cm: an edge shared with its own road is no hit
    ti,ai=shapely.STRtree(shapely.buffer(parts,-.01,quad_segs=1)).query(polys,predicate='intersects')
    hit=np.zeros(len(v),dtype=bool); hit[ti]=True
    out=list(v[~hit])
    if not len(ti):
        return out
    # subtract each triangle's roads one per round, all triangles of a round in one call
    order=np.argsort(ti,kind='stable'); ti,ai=ti[order],ai[order]
    rank=np.arange(len(ti))-np.searchsorted(ti,ti)
    pieces=polys.copy()
    for r in range(rank.max()+1):
        sel=rank==r
        pieces[ti[sel]]=shapely.difference(pieces[ti[sel]],parts[ai[sel]])
    # triangulate every cut piece in one call; heights from each parent triangle's plane
    k=np.flatnonzero(hit)
    k=k[~shapely.is_empty(pieces[k])]
    k=k[shapely.area(pieces[k])>1e-4]
    if not len(k):
        return out
    parts,owner=shapely.get_parts(shapely.constrained_delaunay_triangles(pieces[k]),return_index=True)
    keep=shapely.area(parts)>1e-8
    parts,owner=parts[keep],k[owner[keep]]
    if not len(parts):
        return out
    xy=shapely.get_coordinates(shapely.get_exterior_ring(parts)).reshape(len(parts),4,2)[:,:3]
    a,b,c=v[owner,0],v[owner,1],v[owner,2]
    e1,e2=b[:,:2]-a[:,:2],c[:,:2]-a[:,:2]
    den=e1[:,0]*e2[:,1]-e2[:,0]*e1[:,1]
    ok=np.abs(den)>1e-12
    xy,a,b,c,e1,e2,den=xy[ok],a[ok],b[ok],c[ok],e1[ok],e2[ok],den[ok]
    d=xy-a[:,None,:2]
    u=(d[:,:,0]*e2[:,None,1]-e2[:,None,0]*d[:,:,1])/den[:,None]
    w=(e1[:,None,0]*d[:,:,1]-d[:,:,0]*e1[:,None,1])/den[:,None]
    z=a[:,None,2]+u*(b[:,None,2]-a[:,None,2])+w*(c[:,None,2]-a[:,None,2])
    out.extend(np.concatenate([xy,z[:,:,None]],axis=2))
    return out


def build_road(store, rect, center=None):
    mesh=Mesh(rect); candidates=[]; verges=[]; asphalt=[]; walks=[]; paving=[]; ring_walks=[]; rings=[]; curbs=[]
    pad=SHOULDER+1   # a verge reaches into the tile from a road whose bounds do not
    for fid,kind,data,geom in store.query((rect[0]-pad,rect[1]-pad,rect[2]+pad,rect[3]+pad),{'road','junction'}):
        if kind=='junction':
            h=corner_heights(data)
            mesh.add_many('road',junction_fan(geom,data))
            if data['raise']==0:
                v,_,w,zone=junction_verge(store,data,h,SIDEWALK if junction_walks(store,data) else 0.)
                verges.extend(v); asphalt.append(junction_polygon(data)); ring_walks.extend(w)
                if zone is not None: rings.append(zone)
            continue
        tris,_,xy,z,tangents,knots,verge,walk,edges,curb=road_surface(store,data,footprint=False)
        major=data['tags'].get('highway','').replace('_link','') in MAJOR
        mesh.add_many('road_major' if major else 'road',tris)
        verges.extend(verge)
        if verge:          # ground-level roads only: a bridge deck keeps the ground under it
            half=np.column_stack([-tangents[:,1],tangents[:,0]])*data['width']/2
            asphalt.append(shapely.make_valid(Polygon(np.vstack([xy+half,(xy-half)[::-1]]))))
            walks.extend(walk); curbs.extend(curb)
            # the sidewalk band too, so no other road's verge lies on it
            if walk:
                paving.append(shapely.make_valid(Polygon(np.vstack([edges[0][:,:2],edges[1][:,:2][::-1]]))))
        else:
            mesh.add_many('sidewalk',walk); mesh.add_many('curb',curb)
        # Marking uses the exact strip profile; no independent road height sampling.
        mesh.add_many('mark',lane_marks(xy,z,tangents,knots,data['width'],data['tags']))
        mesh.add_many('mark',zebras(xy,z,tangents,knots,data))
        # spawn away from junctions, on a real stretch of road: a knot near a segment end put
        # the car across the main street at Bila Tserkva (fast-oblast-bc-01)
        long_enough=knots[-1]-knots[0]>=40
        for k,p in enumerate(xy):
            if long_enough and knots[0]+15<=knots[k]<=knots[-1]-15 and rect[0]+5<p[0]<rect[2]-5 and rect[1]+5<p[1]<rect[3]-5:
                normal=np.array([-tangents[k,1],tangents[k,0]])
                # the right lane only where a 2 m car fits in it with room; narrow roads: the middle
                pos=p-normal*max(0.,min(1.65,data['width']/2-1.6))
                target=np.array(center or mesh.origin[:2])
                # prefer a real carriageway over a lane or service road next to the point
                penalty=(0 if data['width']>=5 else 150)+max(0.,150-(knots[-1]-knots[0]))
                # face the longer part of the road (more straight road ahead of the car)
                sign=1 if knots[k]-knots[0]<=knots[-1]-knots[k] else -1
                tx,ty=tangents[k,0]*sign,tangents[k,1]*sign
                if sign<0:
                    pos=p+normal*max(0.,min(1.65,data['width']/2-1.6))     # the right lane that way
                candidates.append((np.linalg.norm(pos-target)+penalty,{'pos':[float(pos[0]),float(pos[1]),float(z[k]+.65)],
                                  # BeamNG: quat(0,0,sin(yaw/2),cos(yaw/2)) faces (sin yaw, cos yaw) --
                                  # atan2(tx,-ty) mirrored y and put the car across diagonal streets
                                  'yaw':float(math.atan2(tx,ty))}))
    # a road's sidewalk stops at the junction's sidewalk ring (two streets' sidewalks overlapped
    # at the corner 0.29 m apart); the ring itself gives way to every road surface
    mesh.add_many('sidewalk',clip_verges(walks,asphalt+rings))
    mesh.add_many('curb',clip_verges(curbs,asphalt+rings))
    mesh.add_many('sidewalk',clip_verges(ring_walks,asphalt))
    mesh.add_many('ground',clip_verges(verges,asphalt+paving+rings))
    spawn=min(candidates,key=lambda x:x[0])[1] if candidates else None
    return mesh,spawn


CENTRE=(30.5234,50.4501)     # Maidan: the old town round it is plaster and brick, not panels
CENTRE_RADIUS=2600.


def overlay(op, a, b):
    """A GEOS overlay that falls back to a 1 mm precision grid, then gives None."""
    try:
        return op(a,b)
    except shapely.errors.GEOSException:
        try:
            return op(a,b,grid_size=.001)
        except shapely.errors.GEOSException:
            return None


def build_scene(store,rect):
    mesh=Mesh(rect)
    cx,cy=store.frame.local(*CENTRE)
    centre=math.hypot((rect[0]+rect[2])/2-float(cx),(rect[1]+rect[3])/2-float(cy))<CENTRE_RADIUS
    ground_holes=[]; lands={}; blocked=[]; parking=[]; rows=[]; streets=[]; osm_points=[]
    pad=SHOULDER+1
    entries=store.query((rect[0]-pad,rect[1]-pad,rect[2]+pad,rect[3]+pad))+store.scenery(rect)
    for fid,kind,data,geom in entries:
        if kind=='road' and data['tags'].get('bridge') in (None,'no'):
            surf=road_surface(store,data)
            ground_holes.append(surf[1])
            place_lamps(mesh,rect,data,surf)
            streets.extend(street_tree_spots(rect,data,surf))
        elif kind=='junction' and data['raise']==0:
            ground_holes.append(junction_polygon(data).union(junction_verge(store,data,0.,SIDEWALK if junction_walks(store,data) else 0.)[1]))
        elif kind=='water':
            ground_holes.append(geom); blocked.append(geom)
            # Canonical water body level, shared by all tiles.
            p=geom.representative_point()
            h=float(store.terrain.sample([[p.x,p.y]])[0])-.5
            mesh.polygon('water',geom.intersection(box(*rect)),h)
        elif kind in ('paved','forest','park','residential'):
            lands.setdefault(kind,[]).append(geom)
            if data['tags'].get('amenity')=='parking':
                parking.append(geom)
        elif kind=='building':
            blocked.append(geom)
            add_building(store,mesh,rect,fid,data['tags'],geom,centre)
        elif kind=='path':
            # a footpath is ground cover like a car park: cut into the same cells, flush with them
            w=style.PATHS.get(data['tags'].get('highway'),1.5)
            w=sources.number(data['tags'].get('width'),w)
            lands.setdefault(style.path_kind(data['tags']),[]).append(geom.buffer(min(max(w,1.),6.)/2,cap_style=2,quad_segs=2))
        elif kind=='fence':
            add_fence(store,mesh,rect,data['tags'],geom)
        elif kind=='tree_row':
            rows.append(geom)
        elif kind in ('stop','signal'):
            osm_points.append((fid,kind,geom))
    holes=shapely.union_all(ground_holes)
    # landuse splits the ground cells (paved first): same cell heights, so seams do not move
    cover=[]
    for k in ('paved','footway_asphalt','footway','trodden','forest','park'):
        if k in lands:
            # on a 1 mm grid: many thin footpath buffers made GEOS overlays fail ("Unable to
            # determine overlay result geometry dimension", vis-06-akadem)
            u=shapely.set_precision(shapely.union_all(lands[k]).intersection(box(*rect)),.001)
            for prev in cover: u=overlay(shapely.difference,u,prev[1]) or u
            if not u.is_empty:
                shapely.prepare(u); cover.append((k,u))
    # Fixed 25 m grid (20 intervals/tile); vector sample once, then interpolate planar cells.
    xs=np.arange(rect[0],rect[2]+1,GRID); ys=np.arange(rect[1],rect[3]+1,GRID)
    points=np.array([(x,y) for y in ys for x in xs]); heights=store.terrain.sample(points).reshape(len(ys),len(xs))-.25
    for j in range(len(ys)-1):
        for i in range(len(xs)-1):
            cell=box(xs[i],ys[j],xs[i+1],ys[j+1])
            square=cell.difference(holes)
            def at(xy):
                u=(xy[:,0]-xs[i])/25; v=(xy[:,1]-ys[j])/25
                return (heights[j,i]*(1-u)+heights[j,i+1]*u)*(1-v)+(heights[j+1,i]*(1-u)+heights[j+1,i+1]*u)*v
            for k,u in cover:
                if shapely.intersects(u,cell):
                    part=overlay(shapely.intersection,square,u)
                    if part is not None and not part.is_empty:
                        rest=overlay(shapely.difference,square,u)
                        if rest is None:
                            continue
                        mesh.polygon(k,part,at); square=rest
            mesh.polygon('ground',square,at)
    free=shapely.union_all([holes,*blocked])
    plant_trees(store,mesh,rect,lands,free,streets,rows)
    if parking:
        add_parked_cars(store,mesh,rect,parking,shapely.union_all(blocked).buffer(1.) if blocked else None)
    if osm_points:
        add_street_points(store,mesh,rect,osm_points)
    return mesh


def static(shape, x, y, z, yaw):
    return {'kind':'static','shape':shape,'pos':[float(x),float(y),float(z)],
            'rot':[0,0,math.sin(yaw/2),math.cos(yaw/2)],'collision':'None','decal':'None','triangles':0}


def add_street_points(store, mesh, rect, points):
    """Bus shelters and traffic lights where OSM puts them. A shelter stands beyond the curb of
    the nearest road, its front to the road; a light stands at the mouth of every road that meets
    at a signal node, on the right of the traffic coming in, facing it."""
    placed=[]
    for fid,kind,geom in sorted(points,key=lambda x:x[0],reverse=True):
        p=np.array(geom.coords[0])
        roads=[r for r in store.query((p[0]-60,p[1]-60,p[0]+60,p[1]+60),{'road'})
               if r[2]['tags'].get('bridge') in (None,'no') and r[2]['tags'].get('tunnel') in (None,'no')]
        if not roads:
            continue
        if kind=='stop':
            if not (rect[0]<=p[0]<rect[2] and rect[1]<=p[1]<rect[3]):
                continue
            if any(math.dist(p,q)<30 for q in placed):
                continue           # a platform and its stop position: one shelter
            _,_,data,_=min(roads,key=lambda r:LineString(r[2]['xy']).distance(geom))
            line=LineString(data['xy']); at=line.project(geom)
            a=np.array(line.interpolate(max(0,at-1)).coords[0]); b=np.array(line.interpolate(min(line.length,at+1)).coords[0])
            t=(b-a)/max(np.linalg.norm(b-a),1e-9); n=np.array([-t[1],t[0]])
            q=np.array(line.interpolate(at).coords[0])
            side=np.sign(np.dot(p-q,n)) or -1.
            if np.linalg.norm(p-q)<data['width']/2+.5:
                side=-1.               # a stop on the carriageway: the right-hand side of the way
            walk=SIDEWALK if ('left' if side>0 else 'right') in walk_sides(data['tags']) else 0
            pos=q+n*side*(data['width']/2+walk+1.4)
            # its front (shape +y after BUS_STOP_YAW) toward the road
            face=-n*side
            yaw=math.atan2(face[0],face[1])+style.BUS_STOP_YAW
            mesh.statics.append(static(style.BUS_STOP,pos[0],pos[1],float(ground_z(store,[pos])[0])+.25,yaw))
            placed.append(p)
        else:
            nid=-fid-10**12
            for _,_,data,_ in roads:
                if nid not in data['nodes']:
                    continue
                line=LineString(data['xy'])
                end=0 if data['nodes'][0]==nid else 1
                if data['tags'].get('oneway') in ('yes','1') and end==0:
                    continue           # the one-way leaves this junction: no light for it
                cut=data['cuts'][end]+1.5
                if line.length<cut+2:
                    continue
                at=cut if end==0 else line.length-cut
                q=np.array(line.interpolate(at).coords[0])
                o=np.array(line.interpolate(at+(1 if end==0 else -1)).coords[0])
                inward=(q-o)/max(np.linalg.norm(q-o),1e-9)        # travel toward the junction
                right=np.array([inward[1],-inward[0]])
                pos=q+right*(data['width']/2+.7)
                if not (rect[0]<=pos[0]<rect[2] and rect[1]<=pos[1]<rect[3]):
                    continue
                face=-inward          # the lamps look at the traffic coming in
                yaw=math.atan2(face[0],face[1])+style.TRAFFIC_LIGHT_YAW
                z=np.interp(at,*data['prof'])
                mesh.statics.append(static(style.TRAFFIC_LIGHT,pos[0],pos[1],z,yaw))


PARAPET=0.9        # m of facade above a flat roof
PARAPET_T=0.25     # parapet thickness
EAVE=0.45          # pitched roof overhang, m


def wall_quads(ring, z0, z1, base, scale, u0=0.):
    """Wall quads round a ring (counter-clockwise faces out) with u along it and v in floors from
    the building base (so windows sit on storeys). Returns (triangles per edge, end u)."""
    out=[]; u=u0; top=(z1-base)/scale; bottom=(z0-base)/scale
    for p,q in zip(ring,ring[1:]):
        du=math.dist(p[:2],q[:2])/scale
        quad=np.array([[*p[:2],z0,u,bottom],[*q[:2],z0,u+du,bottom],[*q[:2],z1,u+du,top],[*p[:2],z1,u,top]])
        out.append([quad[[0,1,2]],quad[[0,2,3]]])
        u+=du
    return out,u


def add_building(store, mesh, rect, fid, tags, geom, centre):
    """Walls from below the lowest ground under the footprint (no floating corners on slopes),
    photo facades with blank end walls, a parapet round flat roofs, a gabled roof on houses."""
    p=geom.representative_point()
    height=max(2.5,min(250,sources.number(tags.get('height'),sources.number(tags.get('building:levels'),3)*3+(.5 if 'building:levels' in tags else 0))))
    if 'roof:levels' in tags:
        height+=sources.number(tags.get('roof:levels'),0)*2.5
    min_h=sources.number(tags.get('min_height'),0.)
    ring_xy=np.vstack([np.asarray(r.coords)[:,:2] for r in [g.exterior for g in shapely.get_parts(geom) if g.geom_type=='Polygon']] or [np.zeros((1,2))])
    ground=ground_z(store,ring_xy)
    base=float(store.terrain.sample([[p.x,p.y]])[0])
    bottom=float(ground.min())-.3 if min_h<=0 else base+min_h
    top=base+height
    kind=style.wall(tags,height,fid,centre)
    end=style.end_wall(kind)
    scale=style.KINDS[kind][3]
    area=geom.area
    gable=style.pitched(tags,height,area) and gable_roof(mesh,geom,top,kind,scale,base,tags)
    flat=not gable
    parapet=flat and height>=7.5 and area>60
    wall_top=top+(PARAPET if parapet else 0.)
    walls={}
    shop=style.shopfront(tags,height,fid,centre,kind) if min_h<=0 else None
    split=base+style.SHOP_HEIGHT if shop else bottom
    for poly in shapely.get_parts(shapely.orient_polygons(geom)):
        if poly.geom_type!='Polygon':
            continue
        for k,ring in enumerate([poly.exterior,*poly.interiors]):
            coords=list(ring.coords)
            mask=fast_photo.end_walls(coords[:-1]) if k==0 and end!=kind else [False]*(len(coords)-1)
            quads,_=wall_quads(coords,split,wall_top,base,scale)
            for m,q in zip(mask,quads):
                walls.setdefault(end if m else kind,[]).extend(q)
            if shop:
                # the storefront band: v from the base too, so its picture sits on the street
                low,_=wall_quads(coords,bottom,split,base,style.KINDS[shop][3])
                for m,q in zip(mask,low):
                    walls.setdefault(end if m else shop,[]).extend(q)
    for k,tris in walls.items():
        mesh.add_many(k,tris)
    if flat:
        roof_geom=geom
        if parapet:
            inner=geom.buffer(-PARAPET_T,join_style=2)
            if not inner.is_empty and inner.area>area*.3:
                cap=geom.difference(inner)
                mesh.polygon('roof',cap.intersection(box(*rect)),wall_top)
                for poly in shapely.get_parts(shapely.orient_polygons(inner)):
                    if poly.geom_type!='Polygon':
                        continue
                    for ring in [poly.exterior,*poly.interiors]:
                        # the inner face looks into the roof: the ring walked backwards
                        quads,_=wall_quads(list(ring.coords)[::-1],top,wall_top,top,3.)
                        mesh.add_many('parapet',[t for q in quads for t in q])
                roof_geom=inner
        mesh.polygon('roof',roof_geom.intersection(box(*rect)),top)
        p=geom.representative_point()
        if parapet and height>=24 and area>300 and rect[0]<=p.x<rect[2] and rect[1]<=p.y<rect[3]:
            add_roof_boxes(mesh,geom,top,kind)


def gable_roof(mesh, geom, eave, kind, scale, base, tags):
    """A gabled roof over the footprint's minimum rectangle, ridge along its long side, when the
    footprint is near that rectangle; gable ends in the facade. False when it does not fit."""
    if geom.geom_type!='Polygon' or len(geom.interiors):
        return False
    mrr=shapely.orient_polygons(shapely.minimum_rotated_rectangle(geom))   # counter-clockwise: gables face out
    if mrr.geom_type!='Polygon' or mrr.area<=0 or geom.area/mrr.area<.82:
        return False
    c=np.asarray(mrr.exterior.coords)[:4,:2]
    e1,e2=c[1]-c[0],c[2]-c[1]
    if np.linalg.norm(e1)<np.linalg.norm(e2):
        c=np.roll(c,-1,axis=0); e1,e2=c[1]-c[0],c[2]-c[1]
    long_,wide=np.linalg.norm(e1),np.linalg.norm(e2)
    if wide<3 or wide>18:
        return False
    a=e1/long_; b=e2/wide
    pitch=math.radians(35 if tags.get('building') in style.HOUSE else 28)
    rise=min(wide/2*math.tan(pitch),6.)
    o=EAVE
    drop=o*math.tan(pitch)
    # eave corners pushed out by the overhang, the ridge over the middle of the short side
    A=c[0]-a*o-b*o; B=c[1]+a*o-b*o; C=c[2]+a*o+b*o; D=c[3]-a*o+b*o
    R0=(c[0]+c[3])/2-a*o; R1=(c[1]+c[2])/2+a*o
    z0=eave-drop; zr=eave+rise
    P=lambda xy,z: [float(xy[0]),float(xy[1]),z]
    material='roof_tile' if tags.get('roof:material') not in ('metal','tin') and (zlib.crc32(str(geom.centroid.wkb).encode())%4) else 'roof_metal'
    mesh.add_many(material,[np.array([P(A,z0),P(B,z0),P(R1,zr)]),np.array([P(A,z0),P(R1,zr),P(R0,zr)]),
                            np.array([P(C,z0),P(D,z0),P(R0,zr)]),np.array([P(C,z0),P(R0,zr),P(R1,zr)])])
    # gable triangles on the two short sides, in the facade (u along the side, v in floors)
    g=[]
    for p,q,m in ((c[1],c[2],(c[1]+c[2])/2),(c[3],c[0],(c[3]+c[0])/2)):
        w=math.dist(p,q)/scale
        g.append(np.array([[*p,eave,0,(eave-base)/scale],[*q,eave,w,(eave-base)/scale],[*m,zr,w/2,(zr-base)/scale]]))
    mesh.add_many(kind,g)
    return True


# --- parked cars and roof boxes -----------------------------------------------------------------
# cars.color.png (fast_art): CAR_COLOURS cells left to right, then glass and tyre
CAR_COLOURS=[(232,232,228),(190,192,194),(120,122,126),(28,30,34),(150,24,22),(34,64,126),(40,72,52),(196,180,150)]
CAR_WEIGHTS=[.24,.2,.14,.16,.07,.08,.05,.06]
CAR_CELLS=len(CAR_COLOURS)+2
CAR_ATLAS=16      # cells across the texture: a power of two wide (320 px was never cooked)


def car_uv(cell):
    return ((cell+.5)/CAR_ATLAS,.5)


def box_faces(lo, hi, top_lo=None, top_hi=None, skip_bottom=True):
    """Faces of a (possibly tapered) box: lo/hi the bottom corners, top_lo/top_hi the top ones
    (x, y at the top; z from hi). Returns (name, quad of four 3D points) per face."""
    (x0,y0,z0),(x1,y1,z1)=lo,hi
    tx0,ty0=top_lo or (x0,y0); tx1,ty1=top_hi or (x1,y1)
    b=[(x0,y0,z0),(x1,y0,z0),(x1,y1,z0),(x0,y1,z0)]
    t=[(tx0,ty0,z1),(tx1,ty0,z1),(tx1,ty1,z1),(tx0,ty1,z1)]
    faces=[('top',t),('front',[b[1],b[2],t[2],t[1]]),('back',[b[3],b[0],t[0],t[3]]),
           ('left',[b[2],b[3],t[3],t[2]]),('right',[b[0],b[1],t[1],t[0]])]
    if not skip_bottom:
        faces.append(('bottom',b))
    return faces


def car_parts(colour):
    """(quad, uv cell) of a low car in its own frame: x forward, 4.3 m long, 1.76 m wide."""
    glass,tyre=len(CAR_COLOURS),len(CAR_COLOURS)+1
    parts=[]
    for name,q in box_faces((-2.15,-.88,.32),(2.15,.88,.86)):
        parts.append((q,colour))
    for name,q in box_faces((-1.25,-.8,.86),(.85,.8,1.4),(-1.0,-.72),(.3,.72)):
        parts.append((q,colour if name=='top' else glass))
    for name,q in box_faces((-1.55,-.84,.0),(1.55,.84,.32)):
        if name!='top':
            parts.append((q,tyre))
    return parts


def car_triangles(x, y, z, yaw, colour):
    """Triangles (x, y, z, u, v rows) of one car, every face wound to look out of it."""
    c,s_=math.cos(yaw),math.sin(yaw)
    out=[]
    for quad,cell in car_parts(colour):
        q=np.array(quad,dtype=float)
        centre=q.mean(axis=0)
        n=np.cross(q[1]-q[0],q[2]-q[0])
        out_dir=centre-np.array([0,0,.6])
        if np.dot(n,out_dir)<0:
            q=q[::-1]
        w=np.column_stack([x+q[:,0]*c-q[:,1]*s_,y+q[:,0]*s_+q[:,1]*c,z+q[:,2]])
        u,v=car_uv(cell)
        rows=np.column_stack([w,np.full(4,u),np.full(4,v)])
        out+=[rows[[0,1,2]],rows[[0,2,3]]]
    return out


def add_parked_cars(store, mesh, rect, parking, buildings):
    """Cars in the car parks of OSM: rows across the lot's long side (2.7 m stalls, 5.5 m deep,
    every third row an aisle), about two stalls in three taken. A car belongs to the tile
    holding its centre; the stall grid is the lot's own, so the same in every tile."""
    tris=[]
    for lot in parking:
        if lot.area<120 or not lot.intersects(box(*rect)):
            continue
        mrr=shapely.minimum_rotated_rectangle(lot)
        if mrr.geom_type!='Polygon':
            continue
        c=np.asarray(mrr.exterior.coords)[:4]
        e1,e2=c[1]-c[0],c[2]-c[1]
        if np.linalg.norm(e1)<np.linalg.norm(e2):
            c=np.roll(c,-1,axis=0); e1,e2=c[1]-c[0],c[2]-c[1]
        L,W=np.linalg.norm(e1),np.linalg.norm(e2)
        a,b=e1/L,e2/W
        inner=lot.buffer(-1.3)
        if inner.is_empty:
            continue
        us=np.arange(1.35,L-1.3,2.7); vs=np.arange(2.75,W-2.7,5.5)
        if not len(us) or not len(vs):
            continue
        U,V=np.meshgrid(us,vs); row=np.meshgrid(us,np.arange(len(vs)))[1]
        U,V,row=U.ravel(),V.ravel(),row.ravel()
        xy=c[0]+np.outer(U,a)+np.outer(V,b)
        ok=(row%3!=1)&shapely.contains_xy(inner,xy[:,0],xy[:,1])
        ok&=(xy[:,0]>=rect[0])&(xy[:,0]<rect[2])&(xy[:,1]>=rect[1])&(xy[:,1]<rect[3])
        if buildings is not None:
            ok&=~shapely.contains_xy(buildings,xy[:,0],xy[:,1])
        taken=unit_at(xy[:,0],xy[:,1],21)<.66
        xy=xy[ok&taken]
        if not len(xy):
            continue
        z=ground_z(store,xy)
        base_yaw=math.atan2(b[1],b[0])
        flip=unit_at(xy[:,0],xy[:,1],23)<.5; tone=unit_at(xy[:,0],xy[:,1],25)
        cum=np.cumsum(CAR_WEIGHTS)
        for k,(x,y) in enumerate(xy):
            colour=int(np.searchsorted(cum,tone[k]*cum[-1]))
            yaw=base_yaw+(math.pi if flip[k] else 0)+(tone[k]-.5)*.08
            tris+=car_triangles(float(x),float(y),float(z[k])+.02,yaw,min(colour,len(CAR_COLOURS)-1))
        if len(tris)>12000:
            break
    mesh.add_many('car',tris)


def add_roof_boxes(mesh, geom, top, scale_kind):
    """Lift machine rooms on a tall flat roof: a box per ~25 m section along the block."""
    if geom.geom_type!='Polygon':
        return
    mrr=shapely.minimum_rotated_rectangle(geom)
    if mrr.geom_type!='Polygon' or geom.area/mrr.area<.7:
        return
    c=np.asarray(mrr.exterior.coords)[:4]
    e1,e2=c[1]-c[0],c[2]-c[1]
    if np.linalg.norm(e1)<np.linalg.norm(e2):
        c=np.roll(c,-1,axis=0); e1,e2=c[1]-c[0],c[2]-c[1]
    L,W=np.linalg.norm(e1),np.linalg.norm(e2)
    if W<8:
        return
    a,b=e1/L,e2/W
    n=max(1,int(round(L/26)))
    tris=[]; roofs=[]
    for k in range(n):
        centre=c[0]+a*(L*(k+.5)/n)+b*(W/2)
        hx,hy=2.6,1.8
        corners=[centre-a*hx-b*hy,centre+a*hx-b*hy,centre+a*hx+b*hy,centre-a*hx+b*hy]
        if not geom.buffer(-1).contains(Polygon(corners)):
            continue
        z0,z1=top,top+2.8
        ring=corners+[corners[0]]
        if Polygon(corners).exterior.is_ccw is False:
            ring=ring[::-1]
        quads,_=wall_quads([(float(p[0]),float(p[1])) for p in ring],z0,z1,z0,3.)
        tris+=[t for q in quads for t in q]
        roofs+=[np.array([[*corners[0],z1],[*corners[1],z1],[*corners[2],z1]]),np.array([[*corners[0],z1],[*corners[2],z1],[*corners[3],z1]])]
    mesh.add_many('parapet',tris)
    mesh.add_many('roof',roofs)



def place_lamps(mesh, rect, data, surf):
    """Street lamps on the sidewalk every LAMP_EVERY m (phase from the segment start, so the
    same in every tile; a lamp belongs to the tile holding it). Right side; both on major roads."""
    tags=data['tags']; sides=walk_sides(tags)
    if not sides or tags.get('highway')=='service':
        return
    _,_,xy,z,tangent,knots,*_=surf
    if len(knots)<2:
        return
    a,b=knots[0]+3,knots[-1]-3
    ss=np.arange(math.ceil(a/LAMP_EVERY)*LAMP_EVERY,b,LAMP_EVERY)
    if not len(ss):
        return
    p=np.column_stack([np.interp(ss,knots,xy[:,0]),np.interp(ss,knots,xy[:,1])])
    t=np.column_stack([np.interp(ss,knots,tangent[:,0]),np.interp(ss,knots,tangent[:,1])])
    t/=np.maximum(np.linalg.norm(t,axis=1)[:,None],1e-9)
    nrm=np.column_stack([-t[:,1],t[:,0]]); zz=np.interp(ss,knots,z)
    major=tags.get('highway','').replace('_link','') in MAJOR
    for side,sign in (('right',-1),('left',1)):
        if side not in sides or (side=='left' and not major):
            continue
        q=p+nrm*sign*(data['width']/2+SIDEWALK*.75)
        for k in range(len(ss)):
            if not (rect[0]<=q[k,0]<rect[2] and rect[1]<=q[k,1]<rect[3]):
                continue
            # face the road: the lamp's arm reaches over the carriageway
            yaw=math.atan2(-sign*nrm[k,1],-sign*nrm[k,0])
            mesh.statics.append({'kind':'static','shape':style.LAMP,'pos':[float(q[k,0]),float(q[k,1]),float(zz[k])],
                                 'rot':[0,0,math.sin(yaw/2),math.cos(yaw/2)],'collision':'None','decal':'None','triangles':0})


def lattice(rect, sp, salt):
    """A global jittered lattice of spacing sp: (x, y, hash function) of the points in rect."""
    gi,gj=np.meshgrid(np.arange(math.floor(rect[0]/sp),math.ceil(rect[2]/sp)),
                      np.arange(math.floor(rect[1]/sp),math.ceil(rect[3]/sp)))
    gi,gj=gi.ravel().astype(np.int64),gj.ravel().astype(np.int64)
    h=((gi*73856093)^(gj*19349663)^int(sp*1000+salt))&0xffffffff
    r=lambda k: ((h*(2654435761+k*40503)+k*97)&0xffffffff)/2**32
    x=(gi+.5+(r(1)-.5)*.8)*sp; y=(gj+.5+(r(2)-.5)*.8)*sp
    return x,y,r


def unit_at(x, y, k):
    """A stable 0..1 number for a point (tile independent)."""
    h=((np.round(np.asarray(x)*10).astype(np.int64)*73856093)^(np.round(np.asarray(y)*10).astype(np.int64)*19349663)^k)&0xffffffff
    return ((h*2654435761+k*97)&0xffffffff)/2**32


STREET_TREES={'residential','tertiary','secondary','primary','unclassified','living_street'}
STREET_TREE_EVERY=9.0


def street_tree_spots(rect, data, surf):
    """Points for trees beside a street, beyond the sidewalk, every STREET_TREE_EVERY m from the
    segment start (the same in every tile), away from the junctions."""
    tags=data['tags']
    if tags.get('highway','').replace('_link','') not in STREET_TREES or tags.get('bridge') not in (None,'no'):
        return []
    _,_,xy,z,tangent,knots,*_=surf
    a,b=knots[0]+12,knots[-1]-12
    if b<=a:
        return []
    ss=np.arange(math.ceil(a/STREET_TREE_EVERY)*STREET_TREE_EVERY,b,STREET_TREE_EVERY)
    if not len(ss):
        return []
    p=np.column_stack([np.interp(ss,knots,xy[:,0]),np.interp(ss,knots,xy[:,1])])
    t=np.column_stack([np.interp(ss,knots,tangent[:,0]),np.interp(ss,knots,tangent[:,1])])
    t/=np.maximum(np.linalg.norm(t,axis=1)[:,None],1e-9)
    nrm=np.column_stack([-t[:,1],t[:,0]])
    sides=walk_sides(tags); out=[]
    for side,sign in (('left',1),('right',-1)):
        off=data['width']/2+(SIDEWALK if side in sides else 0)+1.8
        q=p+nrm*sign*off
        out.extend(map(tuple,q))
    return out


def plant_trees(store, mesh, rect, lands, blocked, streets=(), rows=()):
    """Trees beside streets, in tree rows, yards, parks and woods. Every tree stands on a point
    fixed in the world (a lattice or a distance along its OSM way), so a tree near a tile seam
    belongs to exactly one tile (x in [x0, x1)), the same one every time."""
    spots=[]   # (x, y, kind)
    clear=blocked.buffer(1.5) if blocked is not None and not blocked.is_empty else None
    paved=shapely.union_all(lands['paved']) if 'paved' in lands else None
    def keep(x, y, kind, room=None):
        x=np.asarray(x,dtype=float); y=np.asarray(y,dtype=float)
        ok=(x>=rect[0])&(x<rect[2])&(y>=rect[1])&(y<rect[3])
        if room is not None and not room.is_empty:
            ok&=~shapely.contains_xy(room,x,y)
        elif clear is not None:
            ok&=~shapely.contains_xy(clear,x,y)
        if paved is not None:
            ok&=~shapely.contains_xy(paved,x,y)
        spots.extend((float(a),float(b),kind) for a,b in zip(x[ok],y[ok]))
    if len(streets):
        st=np.asarray(streets,dtype=float)
        sel=unit_at(st[:,0],st[:,1],11)<.72
        keep(st[sel,0],st[sel,1],'street')
    for line in rows:
        n=max(1,int(line.length//7))
        pts=shapely.get_coordinates(shapely.line_interpolate_point(line,np.arange(n+1)*line.length/max(n,1)))
        keep(pts[:,0],pts[:,1],'row')
    # yards: open ground between the blocks (landuse=residential, else 45 m round any building),
    # kept clear of walls by 4 m and of paths by 1.2 m. Kyiv's yards are full of grown trees.
    if 'residential' in lands:
        area=shapely.union_all(lands['residential']).intersection(box(*rect))
    elif blocked is not None and not blocked.is_empty:
        area=blocked.buffer(45.,quad_segs=2).intersection(box(*rect))
    else:
        area=None
    if area is not None and not area.is_empty:
        x,y,r=lattice(rect,9.,7)
        inside=shapely.contains_xy(area,x,y)&(r(5)<.6)
        room=blocked.buffer(4.,quad_segs=2) if blocked is not None and not blocked.is_empty else None
        for k in ('footway','trodden','footway_asphalt'):
            if k in lands and room is not None:
                room=room.union(shapely.union_all(lands[k]).buffer(1.2,quad_segs=2))
        keep(x[inside],y[inside],'yard',room)
    for kind in ('park','forest'):
        if kind not in lands:
            continue
        x,y,r=lattice(rect,style.TREE_SPACING[kind],0)
        area=shapely.union_all(lands[kind])
        inside=shapely.contains_xy(area,x,y)
        keep(x[inside],y[inside],kind)
    spots=spots[:style.TREES_PER_TILE]
    if not spots:
        return
    xy=np.array([(a,b) for a,b,_ in spots])
    z=ground_z(store,xy)
    pick=unit_at(xy[:,0],xy[:,1],3); turn=unit_at(xy[:,0],xy[:,1],5); size=unit_at(xy[:,0],xy[:,1],9)
    for n,(x,y,kind) in enumerate(spots):
        shapes=style.TREE_SETS[kind]
        yaw=turn[n]*2*math.pi
        mesh.statics.append({'kind':'static','shape':shapes[int(pick[n]*len(shapes))%len(shapes)],
                             'pos':[x,y,float(z[n])],'rot':[0,0,math.sin(yaw/2),math.cos(yaw/2)],
                             'scale':round(.85+.35*float(size[n]),3),
                             'collision':'None','decal':'None','triangles':0})


def add_fence(store, mesh, rect, tags, line):
    """A fence along an OSM barrier: vertical quads following the ground (samples every 4 m),
    the Soviet PO-2 concrete slab for walls and concrete fences, chain link for the rest."""
    concrete=tags.get('barrier')=='wall' or tags.get('fence_type') in ('concrete','brick','stone')
    kind='fence_wall' if concrete else 'fence_mesh'
    height=min(3.,sources.number(tags.get('height'),2.0 if concrete else 1.6))
    if line.length<1 or not line.intersects(box(*rect)):
        return
    n=max(1,int(math.ceil(line.length/4.)))
    pts=shapely.get_coordinates(shapely.line_interpolate_point(line,np.linspace(0,line.length,n+1)))
    z=ground_z(store,pts)-.05
    scale=style.KINDS[kind][3]
    u=np.r_[0,np.cumsum(np.linalg.norm(np.diff(pts,axis=0),axis=1))]/scale
    tris=[]
    for k in range(n):
        q=np.array([[*pts[k],z[k],u[k],0],[*pts[k+1],z[k+1],u[k+1],0],
                    [*pts[k+1],z[k+1]+height,u[k+1],height/scale],[*pts[k],z[k]+height,u[k],height/scale]])
        tris+=[q[[0,1,2]],q[[0,2,3]]]
    mesh.add_many(kind,tris)


def run(directory,i,j,work,dest_root,cache=None,center=None,layer='road'):
    started=time.perf_counter(); store=store_at(directory); rect=store.frame.rect(i,j)
    if not store.covered(rect):
        raise ValueError(f'no_osm: tile {i},{j} outside source coverage')
    if layer=='road':
        local=tuple(float(v) for v in store.frame.local(*center)) if center else None
        mesh,spawn=build_road(store,rect,local)
    elif layer=='far':
        import fast_far
        mesh,spawn=fast_far.build_far(store,rect),None
    else:
        mesh,spawn=build_scene(store,rect),None
    geometry=time.perf_counter()-started
    region=store.meta['id']; dest=Path(dest_root)/region; key=f'{i}_{j}'
    items=mesh.write(dest,region,key,layer,all_decor=layer=='far')
    for n,it in enumerate(mesh.statics):
        # trees and lamps have no collision: outside the MissionGroup, so be:reloadCollision does
        # not walk them (thousands of yard trees took it from 59 to 140 ms, vis-replay-01)
        items.append(dict(it,name=f'e2rf_{key}_{layer}_static_{n}',layer=layer,decor=True))
    mat=dest/'tiles'/f'{key}.{layer}.mats.json'; mat.parent.mkdir(parents=True,exist_ok=True)
    mat.write_text(json.dumps(MATERIALS),encoding='utf8'); warm(mat)
    manifest={'version':1,'generator':sources.GEOMETRY,'source':region,'layer':layer,'tile':[i,j],
              'rect':rect,'lift':sources.LIFT,'materials':f'/e2r_stream/{region}/tiles/{mat.name}',
              'mats':list(MATERIALS),'items':items,'spawn':spawn,
              'drivable':bool(mesh.faces.get('road') or mesh.faces.get('road_major')),
              'warmShapes':style.ALL_TREES+[style.LAMP,style.BUS_STOP,style.TRAFFIC_LIGHT],'packs':style.ART+'/packs.json',
              'triangles':sum(it['triangles'] for it in items)}
    suffix='' if layer=='road' else '.'+layer
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
    directory,meta=sources.prepare(a.sources,Path(a.out)/'index',a.dem,point=(a.lon,a.lat))
    i,j=sources.Frame.of(meta).tile_of(a.lon,a.lat)
    print(json.dumps(run(directory,i,j,a.out,Path(a.out)/'stream',center=(a.lon,a.lat))))
    if a.scene:
        print(json.dumps(run(directory,i,j,a.out,Path(a.out)/'stream',layer='scene')))
    print(f'elapsed {time.perf_counter()-started:.2f}s (index original build {meta["seconds"]:.2f}s; use a new --out for cold measurement)')


if __name__=='__main__':
    main()
