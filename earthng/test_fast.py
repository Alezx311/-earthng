"""Geometry/streaming invariants independent of a BeamNG installation."""
import math
from pathlib import Path
import struct
import tempfile
import unittest

import numpy as np
from PIL import Image
import shapely
from shapely.geometry import Polygon

import fast
import fast_profile
import fast_sources as sources
from fast_server import candidates


class FastGeometryTests(unittest.TestCase):
    def test_clipping_preserves_surface_and_seams(self):
        tri=np.array([[-20.,0.,10.],[30.,-20.,25.],[30.,40.,40.]])
        a=fast.clip_triangle(tri,(-100,-100,0,100)); b=fast.clip_triangle(tri,(0,-100,100,100))
        edge=lambda ts: sorted({tuple(np.round(p,9)) for t in ts for p in t if abs(p[0])<1e-9})
        self.assertEqual(edge(a),edge(b))
        area=lambda ts:sum(Polygon(t[:,:2]).area for t in ts)
        self.assertAlmostEqual(area(a)+area(b),Polygon(tri[:,:2]).area)
        normal=np.cross(tri[1]-tri[0],tri[2]-tri[0])
        for t in a+b:
            self.assertTrue(np.allclose((t-tri[0])@normal,0))

    def test_polygon_holes_stay_open(self):
        polygon=Polygon([(0,0),(10,0),(10,10),(0,10)],holes=[[(2,2),(2,8),(8,8),(8,2)]])
        tris=fast.triangles(polygon)
        self.assertAlmostEqual(sum(Polygon(t).area for t in tris),polygon.area)
        self.assertTrue(all(polygon.covers(Polygon(t)) for t in tris))
        mesh=fast.Mesh((0,0,500,500))
        mesh.polygon('road',polygon,50.)
        self.assertTrue(all(n[2]>0 for _,n in mesh.faces['road']))

    def test_binary_winding_and_local_positions(self):
        with tempfile.TemporaryDirectory() as d:
            m=fast.Mesh((0,0,500,500)); m.add('road',np.array([[10.,10.,50.],[20.,10.,50.],[10.,20.,50.]]))
            items=m.write(Path(d),'test','0_0','road')
            data=(Path(d)/'tiles/0_0/0_0_road_0.bin').read_bytes()
            self.assertEqual(struct.unpack_from('<I',data)[0],1)
            name_len=struct.unpack_from('<I',data,4)[0]; at=8+name_len
            nv,ni=struct.unpack_from('<II',data,at); at+=8
            verts=np.array(struct.unpack_from(f'<{nv*3}f',data,at)).reshape(-1,3)
            idx=struct.unpack_from(f'<{ni}I',data,at+nv*32)
            # the indexed triangle, wound the opposite way for ProceduralMesh
            self.assertTrue(np.allclose(verts[list(idx)]+items[0]['pos'],[[10,10,50],[10,20,50],[20,10,50]]))

    def test_dem_missing_is_error(self):
        with tempfile.TemporaryDirectory() as d:
            terrain=sources.Terrain(d)
            with self.assertRaisesRegex(FileNotFoundError,'no_dem'):
                terrain.sample([[0,0]])

    def test_frame_roundtrip_and_negative_tiles(self):
        f=sources.Frame()
        lon,lat=f.geographic(-501,501)
        self.assertEqual(f.tile_of(lon,lat),(-2,1))
        x,y=f.local(lon,lat)
        self.assertAlmostEqual(float(x),-501,places=6)
        self.assertAlmostEqual(float(y),501,places=6)

    def test_large_package_is_indexed_as_a_window(self):
        cell=.05
        tiles={f'{x}_{y}':{'cell':[x*cell,y*cell,(x+1)*cell,(y+1)*cell]} for x in range(584,644) for y in range(983,1032)}
        index={'tiles':tiles,'package_box':[29.2,49.15,32.2,51.6]}
        small={'tiles':dict(list(tiles.items())[:50]),'package_box':[0,0,1,1]}
        self.assertIs(sources.window_of(small,(30.5,50.4))[0],small)
        a,wa=sources.window_of(index,(30.36,50.46)); b,wb=sources.window_of(index,(30.31,50.42))
        self.assertEqual(wa,wb)                                  # nearby landings share it
        self.assertTrue(140<=len(a['tiles'])<=17*11)               # ~ the Kyiv package (162)
        self.assertTrue(wa[0]<30.36-.15 and wa[2]>30.36+.15 and wa[1]<50.46-.1 and wa[3]>50.46+.1)
        with self.assertRaisesRegex(ValueError,'no_osm'):
            sources.window_of(index,(10,10))

    def test_road_profile_identical_for_different_tile_clips(self):
        class Terrain:
            def sample(self,xy,smooth=False):
                return np.asarray(xy)[:,0]*.015+40
        class Store:
            terrain=Terrain()
            def height(self,n): return {1:40.12,2:55.12}[n]
        data={'xy':[[0,0],[1000,0]],'cuts':[5,5],'width':7,'tags':{},'nodes':[1,2],
              'prof':[[0,333,1000],[40,47,55]]}
        tris,*_=fast.road_surface(Store(),data)
        a=[c for t in tris for c in fast.clip_triangle(t,(0,-100,500,100))]
        b=[c for t in tris for c in fast.clip_triangle(t,(500,-100,1000,100))]
        edge=lambda ts:sorted({tuple(np.round(p,6)) for t in ts for p in t if p[0]==500})
        self.assertEqual(edge(a),edge(b))

    def test_verge_meets_road_edge_and_ground(self):
        class Terrain:
            def sample(self,xy,smooth=False):
                xy=np.asarray(xy,dtype=float); return xy[:,0]*.01+xy[:,1]*.02+40
        class Store:
            terrain=Terrain()
            def height(self,n): return 43.
        data={'xy':[[10,10],[300,40]],'cuts':[0,0],'width':6,'tags':{},'nodes':[1,2],
              'prof':[[0,291.5],[43,44]]}
        tris,_,_,_,_,_,verge,*_=fast.road_surface(Store(),data)
        self.assertTrue(verge)
        road_pts={tuple(np.round(p,6)) for t in tris for p in t}
        for t in verge:
            inner=[p for p in t if tuple(np.round(p,6)) in road_pts]
            outer=[p for p in t if tuple(np.round(p,6)) not in road_pts]
            self.assertTrue(inner and outer)
            for p in outer:
                self.assertAlmostEqual(float(p[2]),float(fast.ground_z(Store(),p[:2])[0]),places=6)
        # the ground mesh cell (build_scene's planar interpolation) has the same height there
        h=lambda x,y: x*.01+y*.02+40-.25
        x,y=137.3,62.9
        self.assertAlmostEqual(float(fast.ground_z(Store(),[x,y])[0]),h(x,y),places=6)

    def test_bridge_has_no_verge(self):
        class Terrain:
            def sample(self,xy,smooth=False): return np.full(len(np.asarray(xy)),40.)
        class Store:
            terrain=Terrain()
            def height(self,n): return 48.
        data={'xy':[[0,0],[200,0]],'cuts':[0,0],'width':8,'tags':{'bridge':'yes'},'nodes':[1,2],
              'prof':[[0,200],[48,48]]}
        self.assertEqual(fast.road_surface(Store(),data)[6],[])

    def test_verges_are_cut_out_of_other_roads(self):
        # a sloped verge strip crossing a 6 m road: none of it may stay on the road
        verge=[np.array([[0.,0,10],[20,0,12],[20,4,14]]),np.array([[0.,0,10],[20,4,14],[0,4,12]])]
        road=Polygon([(8,-5),(14,-5),(14,9),(8,9)])
        own=Polygon([(0,-3),(20,-3),(20,0),(0,0)])       # shares an edge only: untouched
        out=fast.clip_verges(verge,[road,own])
        plane=lambda x,y: 10+x*.1+y*.5
        area=0
        for t in out:
            poly=Polygon(t[:,:2]); area+=poly.area
            self.assertLess(poly.intersection(road).area,1e-6)
            for x,y,z in t: self.assertAlmostEqual(z,plane(x,y),places=6)
        self.assertAlmostEqual(area,80-6*4,places=4)

    def test_written_mesh_shares_vertices_and_keeps_triangles(self):
        with tempfile.TemporaryDirectory() as d:
            m=fast.Mesh((0,0,500,500))
            h=lambda x,y:[x,y,5+x*.01+y*.02]
            grid=[np.array([h(x,y),h(x+10,y),h(x+10,y+10)]) for x in range(0,50,10) for y in range(0,50,10)]
            grid+=[np.array([h(x,y),h(x+10,y+10),h(x,y+10)]) for x in range(0,50,10) for y in range(0,50,10)]
            m.add_many('ground',grid)
            item=m.write(Path(d),'t','0_0','road')[0]
            data=(Path(d)/'tiles/0_0/0_0_road_0.bin').read_bytes()
            l=struct.unpack_from('<I',data,4)[0]; at=8+l
            nv,ni=struct.unpack_from('<II',data,at); at+=8
            self.assertEqual(nv,36)                      # 6x6 grid points instead of 150 corners
            verts=np.array(struct.unpack_from(f'<{nv*3}f',data,at)).reshape(-1,3)+item['pos']
            idx=np.array(struct.unpack_from(f'<{ni}I',data,at+nv*32)).reshape(-1,3)[:,[0,2,1]]
            got=sorted(tuple(np.round(verts[t],4).ravel()) for t in idx)
            want=sorted(tuple(np.round(v,4).ravel()) for v,n in m.faces['ground'])
            self.assertEqual(got,want)

    def test_sidewalk_sides_follow_tags(self):
        self.assertEqual(fast.walk_sides({'highway':'residential'}),('left','right'))
        self.assertEqual(fast.walk_sides({'highway':'residential','sidewalk':'no'}),())
        self.assertEqual(fast.walk_sides({'highway':'tertiary','sidewalk':'right'}),('right',))
        self.assertEqual(fast.walk_sides({'highway':'service'}),())

    def test_lane_marks(self):
        knots=np.arange(0,200.1,8.); xy=np.column_stack([knots,np.zeros_like(knots)])
        t=np.tile([1.,0],(len(knots),1)); z=np.full(len(knots),10.)
        area=lambda tris: sum(Polygon(q[:,:2]).area for q in tris)
        two=fast.lane_marks(xy,z,t,knots,7,{'highway':'residential'})
        four=fast.lane_marks(xy,z,t,knots,14,{'highway':'primary','lanes':'4'})
        self.assertAlmostEqual(area(two),.12*3*17,places=6)        # dashed centre, 3 on 9 off
        centre=[q for q in four if abs(q[:,1].mean())<.1]
        self.assertAlmostEqual(area(centre),.12*200,places=6)     # solid centre on four lanes
        self.assertTrue(all(np.allclose(q[:,2],10.008) for q in two))

    def test_building_walls_face_outwards(self):
        cw=Polygon([(0,0),(0,10),(10,10),(10,0)])                  # clockwise in OSM order
        for poly in shapely.get_parts(shapely.orient_polygons(cw)):
            ring=list(poly.exterior.coords)
            for p,q in zip(ring,ring[1:]):
                quad=np.array([[*p,0],[*q,0],[*q,5]])
                n=np.cross(quad[1]-quad[0],quad[2]-quad[0])
                mid=(np.array(p)+np.array(q))/2
                self.assertGreater(float(n[:2]@(mid-[5,5])),0)

    def test_pieces_pack_kinds_and_stay_bounded(self):
        with tempfile.TemporaryDirectory() as d:
            m=fast.Mesh((0,0,500,500))
            rng=np.random.default_rng(2)
            for kind,n in (('road',900),('mark',300),('ground',1000),('sidewalk',100)):
                base=rng.uniform(10,490,(n,1,3)); base[:,:,2]=5
                m.add_many(kind,base+np.array([[0,0,0],[1,0,0],[0,1,0]]))
            items=m.write(Path(d),'t','0_0','road')
            self.assertEqual(sum(it['triangles'] for it in items),2300)
            self.assertTrue(all(it['triangles']<=fast.Mesh.PIECE for it in items))
            self.assertLess(len(items),5)                 # was one object per kind and 750 tris
            for it in items:
                data=(Path(d)/it['shape'].split('/t/',1)[1]).read_bytes()
                subs=struct.unpack_from('<I',data)[0]; at=4; names=[]
                for _ in range(subs):
                    l=struct.unpack_from('<I',data,at)[0]; names.append(data[at+4:at+4+l].rstrip(b'\x00').decode())
                    at+=4+l; nv,ni=struct.unpack_from('<II',data,at); at+=8+4*(8*nv+ni)
                self.assertEqual(at,len(data))
                self.assertEqual(sorted(set(names)),sorted(it['mats']))

    def test_add_many_equals_add(self):
        rng=np.random.default_rng(1); tris=rng.uniform(-50,550,(400,3,3)); tris[:,:,2]=rng.uniform(0,5,(400,3))
        for kind in ('road','building'):
            a=fast.Mesh((0,0,500,500)); b=fast.Mesh((0,0,500,500))
            for t in tris: a.add(kind,t)
            b.add_many(kind,tris)
            key=lambda faces: sorted((tuple(np.round(v,6).ravel()),tuple(np.round(n,6))) for v,n in faces)
            self.assertEqual(key(a.faces[kind]),key(b.faces[kind]))



class ProfileTests(unittest.TestCase):
    @staticmethod
    def chain(xs, tags_at=lambda k: {}):
        """Straight road split into segments at xs (shared nodes 1..n)."""
        return [{'xy':[[a,0],[b,0]],'nodes':[k+1,k+2],'tags':tags_at(k)} for k,(a,b) in enumerate(zip(xs,xs[1:]))]

    def test_noisy_flat_dem_gives_gentle_road(self):
        rng=np.random.default_rng(3)
        noise=lambda xy: 40+rng.normal(0,1.5,len(xy))      # 24 m DEM noise floor
        segs=self.chain([0,12,25,31,60,200,215,400])
        prof,_=fast_profile.solve(segs,noise)
        g=max(np.abs(np.diff(h)/np.maximum(np.diff(s),.5)).max() for s,h in prof)
        self.assertLess(g,.06)

    def test_shared_nodes_have_one_height(self):
        segs=self.chain([0,30,90,140])+[{'xy':[[30,0],[30,80]],'nodes':[2,9],'tags':{}}]
        prof,nodes=fast_profile.solve(segs,lambda xy: 40+np.asarray(xy)[:,0]*.03+np.asarray(xy)[:,1]*.05)
        self.assertAlmostEqual(prof[0][1][-1],prof[1][1][0])
        self.assertAlmostEqual(prof[0][1][-1],prof[3][1][0])
        self.assertAlmostEqual(nodes[2],prof[3][1][0])

    def test_building_passage_is_not_a_tunnel(self):
        flat=lambda xy: np.full(len(xy),40.)
        segs=self.chain([0,100,140,240],lambda k:{'tunnel':'building_passage'} if k==1 else {})
        prof,_=fast_profile.solve(segs,flat)
        self.assertLess(max(np.abs(h-40).max() for _,h in prof),1e-3)

    def test_bridge_over_a_valley_ramps_smoothly(self):
        # river valley 12 m deep under a 300 m bridge: no step at the abutments
        valley=lambda xy: 40-12*(np.abs(np.asarray(xy)[:,0]-500)<150)
        segs=self.chain([0,200,350,650,800,1000],lambda k:{'bridge':'yes','layer':'1'} if k==2 else {})
        prof,_=fast_profile.solve(segs,valley)
        s=np.concatenate([p[0]+segs[k]['xy'][0][0] for k,p in enumerate(prof)])
        h=np.concatenate([p[1] for p in prof])
        order=np.argsort(s,kind='stable'); s,h=s[order],h[order]
        keep=np.r_[True,np.diff(s)>.01]; s,h=s[keep],h[keep]
        grade=np.abs(np.diff(h)/np.diff(s))
        self.assertLess(grade.max(),.08)
        mid=h[np.argmin(np.abs(s-500))]
        self.assertGreater(mid,40-12+6)          # the deck stays well above the river

    def test_parallel_carriageways_share_height(self):
        # two oneway halves 12 m apart, 13 m wide (they overlap), over a DEM tilted across them
        dem=lambda xy: 40+np.asarray(xy)[:,1]*.15
        segs=[{'xy':[[0,0],[300,0]],'nodes':[1,2],'tags':{},'width':13.},
              {'xy':[[300,12],[0,12]],'nodes':[3,4],'tags':{},'width':13.}]
        prof,_=fast_profile.solve(segs,dem)
        a=np.interp(150,prof[0][0],prof[0][1]); b=np.interp(150,prof[1][0],prof[1][1])
        self.assertLess(abs(a-b),.05)
        far=[dict(segs[0]),dict(segs[1],xy=[[300,60],[0,60]])]
        prof,_=fast_profile.solve(far,dem)                      # apart: each keeps its ground
        self.assertGreater(abs(np.interp(150,prof[1][0],prof[1][1])-np.interp(150,prof[0][0],prof[0][1])),8)

    def test_station_ids_end_at_nodes(self):
        segs=self.chain([0,7,50,51.5])
        st,ids,pos,seg_of,run,nvar=fast_profile.stations(segs,{1:0,2:1,3:2,4:3})
        for k in range(len(segs)):
            run_ids=ids[seg_of==k]
            self.assertEqual((run_ids[0],run_ids[-1]),(k,k+1))
        self.assertEqual(len(set(ids.tolist())),nvar)


if __name__=='__main__':
    unittest.main()
