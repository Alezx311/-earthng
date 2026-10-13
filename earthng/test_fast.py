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
            data=(Path(d)/'tiles/0_0/0_0_road_road_0.bin').read_bytes()
            self.assertEqual(struct.unpack_from('<I',data)[0],1)
            name_len=struct.unpack_from('<I',data,4)[0]; at=8+name_len
            nv,ni=struct.unpack_from('<II',data,at); at+=8
            verts=np.array(struct.unpack_from(f'<{nv*3}f',data,at)).reshape(-1,3)
            self.assertTrue(np.allclose(verts+items[0]['pos'],[[10,10,50],[20,10,50],[10,20,50]]))
            self.assertEqual(struct.unpack_from('<3I',data,at+nv*32),(0,2,1))

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

    def test_road_profile_identical_for_different_tile_clips(self):
        class Terrain:
            def sample(self,xy,smooth=False):
                return np.asarray(xy)[:,0]*.015+40
        class Store:
            terrain=Terrain()
            def height(self,n): return {1:40.12,2:55.12}[n]
        data={'xy':[[0,0],[1000,0]],'cuts':[5,5],'width':7,'tags':{},'nodes':[1,2]}
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
        data={'xy':[[10,10],[300,40]],'cuts':[0,0],'width':6,'tags':{},'nodes':[1,2]}
        tris,_,_,_,_,_,verge=fast.road_surface(Store(),data)
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
        data={'xy':[[0,0],[200,0]],'cuts':[0,0],'width':8,'tags':{'bridge':'yes'},'nodes':[1,2]}
        self.assertEqual(fast.road_surface(Store(),data)[6],[])

    def test_add_many_equals_add(self):
        rng=np.random.default_rng(1); tris=rng.uniform(-50,550,(400,3,3)); tris[:,:,2]=rng.uniform(0,5,(400,3))
        for kind in ('road','building'):
            a=fast.Mesh((0,0,500,500)); b=fast.Mesh((0,0,500,500))
            for t in tris: a.add(kind,t)
            b.add_many(kind,tris)
            key=lambda faces: sorted((tuple(np.round(v,6).ravel()),tuple(np.round(n,6))) for v,n in faces)
            self.assertEqual(key(a.faces[kind]),key(b.faces[kind]))


if __name__=='__main__':
    unittest.main()
