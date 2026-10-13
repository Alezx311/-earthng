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
DECOR = {'mark', 'curb', 'roof'}   # no collision needed: spawned outside the MissionGroup
CURB = 0.25       # m of the sidewalk next to the road drawn as a curb band
LAMP_EVERY = 30.0 # m between street lamps (sidewalk side)
ZEBRA = {'primary', 'secondary', 'tertiary', 'residential', 'unclassified', 'living_street'}
_STORES = OrderedDict()


def is_wall(kind):
    """Vertical faces: keep their facing, keep hard edges, UV along the wall."""
    return kind.startswith('wall_')


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

    def write(self, dest, region, key, layer):
        """Pieces of up to PIECE triangles, each holding submeshes of several kinds: every
        kind its own object made be:reloadCollision 78 ms (fast-replay-game-04)."""
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
                if (kind in DECOR)!=decor:
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


def build_scene(store,rect):
    mesh=Mesh(rect)
    ground_holes=[]; lands={}; blocked=[]
    pad=SHOULDER+1
    entries=store.query((rect[0]-pad,rect[1]-pad,rect[2]+pad,rect[3]+pad))+store.scenery(rect)
    for fid,kind,data,geom in entries:
        if kind=='road' and data['tags'].get('bridge') in (None,'no'):
            surf=road_surface(store,data)
            ground_holes.append(surf[1])
            place_lamps(mesh,rect,data,surf)
        elif kind=='junction' and data['raise']==0:
            ground_holes.append(junction_polygon(data).union(junction_verge(store,data,0.,SIDEWALK if junction_walks(store,data) else 0.)[1]))
        elif kind=='water':
            ground_holes.append(geom); blocked.append(geom)
            # Canonical water body level, shared by all tiles.
            p=geom.representative_point()
            h=float(store.terrain.sample([[p.x,p.y]])[0])-.5
            mesh.polygon('water',geom.intersection(box(*rect)),h)
        elif kind in ('paved','forest','park'):
            lands.setdefault(kind,[]).append(geom)
        elif kind=='building':
            blocked.append(geom)
            tags=data['tags']; p=geom.representative_point()
            base=float(store.terrain.sample([[p.x,p.y]])[0])
            height=max(2.5,min(250,sources.number(tags.get('height'),sources.number(tags.get('building:levels'),3)*3)))
            mesh.polygon('roof',geom.intersection(box(*rect)),base+height)
            walls=[]
            top=height/style.KINDS['wall_panel'][3]
            # exterior rings counter-clockwise: every wall quad then faces out of the building.
            # u runs along the outline (windows continue round corners), v counts floors.
            for poly in shapely.get_parts(shapely.orient_polygons(geom)):
                if poly.geom_type!='Polygon':
                    continue
                for ring in [poly.exterior,*poly.interiors]:
                    u=0.
                    for p,q in zip(list(ring.coords),list(ring.coords)[1:]):
                        du=math.dist(p[:2],q[:2])/style.KINDS['wall_panel'][3]
                        quad=np.array([[*p[:2],base,u,0],[*q[:2],base,u+du,0],[*q[:2],base+height,u+du,top],[*p[:2],base+height,u,top]])
                        walls.extend([quad[[0,1,2]],quad[[0,2,3]]])
                        u+=du
            mesh.add_many(style.wall(tags,height,fid),walls)
    holes=shapely.union_all(ground_holes)
    # landuse splits the ground cells (paved first): same cell heights, so seams do not move
    cover=[]
    for k in ('paved','forest'):
        if k in lands:
            u=shapely.union_all(lands[k]).intersection(box(*rect))
            for prev in cover: u=u.difference(prev[1])
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
                    part=square.intersection(u)
                    if not part.is_empty:
                        mesh.polygon(k,part,at); square=square.difference(u)
            mesh.polygon('ground',square,at)
    plant_trees(store,mesh,rect,lands,shapely.union_all([holes,*blocked]))
    return mesh


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


def plant_trees(store, mesh, rect, lands, blocked):
    """Trees in woods and parks on a global jittered lattice: a tree near a tile seam belongs
    to exactly one tile (x in [x0, x1)), the same one every time."""
    key=mesh.rect
    for kind in ('forest','park'):
        if kind not in lands or len(mesh.statics)>=style.TREES_PER_TILE:
            continue
        sp=style.TREE_SPACING[kind]
        gi,gj=np.meshgrid(np.arange(math.floor(rect[0]/sp),math.ceil(rect[2]/sp)),
                          np.arange(math.floor(rect[1]/sp),math.ceil(rect[3]/sp)))
        gi,gj=gi.ravel().astype(np.int64),gj.ravel().astype(np.int64)
        h=((gi*73856093)^(gj*19349663)^int(sp*1000))&0xffffffff
        r=lambda k: ((h*(2654435761+k*40503)+k*97)&0xffffffff)/2**32
        x=(gi+.5+(r(1)-.5)*.8)*sp; y=(gj+.5+(r(2)-.5)*.8)*sp
        ok=(x>=rect[0])&(x<rect[2])&(y>=rect[1])&(y<rect[3])
        area=shapely.union_all(lands[kind])
        ok&=shapely.contains_xy(area,x,y)
        if blocked is not None and not blocked.is_empty:
            ok&=~shapely.contains_xy(blocked.buffer(1.5),x,y)
        idx=np.flatnonzero(ok)[:style.TREES_PER_TILE-len(mesh.statics)]
        if not len(idx):
            continue
        z=ground_z(store,np.column_stack([x[idx],y[idx]]))
        for n,k in enumerate(idx):
            yaw=r(3)[k]*2*math.pi
            mesh.statics.append({'kind':'static','shape':style.TREES[int(r(4)[k]*len(style.TREES))%len(style.TREES)],
                                 'pos':[float(x[k]),float(y[k]),float(z[n])],
                                 'rot':[0,0,math.sin(yaw/2),math.cos(yaw/2)],
                                 'collision':'None','decal':'None','triangles':0})


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
    for n,it in enumerate(mesh.statics):
        items.append(dict(it,name=f'e2rf_{key}_{layer}_static_{n}',layer=layer))
    mat=dest/'tiles'/f'{key}.{layer}.mats.json'; mat.parent.mkdir(parents=True,exist_ok=True)
    mat.write_text(json.dumps(MATERIALS),encoding='utf8'); warm(mat)
    manifest={'version':1,'generator':sources.GEOMETRY,'source':region,'layer':layer,'tile':[i,j],
              'rect':rect,'lift':sources.LIFT,'materials':f'/e2r_stream/{region}/tiles/{mat.name}',
              'mats':list(MATERIALS),'items':items,'spawn':spawn,
              'drivable':bool(mesh.faces.get('road') or mesh.faces.get('road_major')),
              'warmShapes':style.TREES+[style.LAMP],'packs':style.ART+'/packs.json',
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
