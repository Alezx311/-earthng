"""Style assets and Lua transitions, including BeamNG's per-frame celestial driver."""
from pathlib import Path
import sys
import tempfile
import unittest

import numpy as np
from PIL import Image
import fast_art as art
import fast_pixel as pixel
import fast_style as style
import fast_packs
import fast_dds

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'out/test-tools'))
from lupa.luajit21 import LuaRuntime


class AssetTests(unittest.TestCase):
    def test_dark_surfaces_are_not_light_sources(self):
        for pack in ('night', 'rain', 'cyber'):
            fields = art.pack_fields(pack, style.ART)
            for kind in ('road', 'sidewalk', 'ground', 'roof', 'water', 'wall_pnl_grey_end'):
                self.assertEqual(fields['e2rf_' + kind]['emissiveFactor'], '0 0 0', (pack, kind))
                self.assertFalse(fields['e2rf_' + kind]['emissiveMap'], (pack, kind))
        glow = np.asarray(fast_packs.facade_glow('panel', 'night'))
        self.assertTrue((glow[0, 0] == 0).all())
        self.assertGreater(glow.max(), 0)

    def test_pixel_facades_are_palette_limited_and_repeat_at_floor_scale(self):
        for name, n in [('panel', 1), ('pnl_grey', 4), ('lgg_cream', 11)]:
            im = pixel.facade(name)
            self.assertLessEqual(len(im.getcolors(100) or []), len(pixel.PALETTE))
            self.assertIsNotNone(im.getcolors(100))
            # Count cream sills on one vertical scan line: one per 3m floor.
            a = np.asarray(im)
            x = round(im.width * 7 / (32 * n))
            line = (a[:, x] == pixel.CREAM).all(axis=1)
            runs = np.count_nonzero(line[1:] != line[:-1])
            self.assertGreaterEqual(runs, n * 2)
        self.assertNotEqual(pixel.facade('pnl_grey').tobytes(), pixel.facade('pnl_grey_end').tobytes())
        self.assertEqual(pixel.facade('shop_red').tobytes(), pixel.facade('shop_red').tobytes())

    def test_pixel_maps_and_dds_chain(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            pixel.build(root)
            fields = pixel.fields(style.ART)
            self.assertTrue(all('e2rf_' + k in fields for k in style.KINDS))
            for kind in style.KINDS:
                with Image.open(root / 'pixel' / f'{kind}.color.png') as im:
                    self.assertGreater(im.width, 0)
                self.assertEqual(fields['e2rf_' + kind]['metallicFactor'], 0)
            image = pixel.facade('panel')
            dest = root / 'panel.color.dds'
            fast_dds.save(image, dest)
            import struct
            data = dest.read_bytes()
            self.assertEqual(struct.unpack_from('<II', data, 12), image.size)
            self.assertEqual(struct.unpack_from('<I', data, 28)[0], 11)
            self.assertEqual(struct.unpack_from('<I', data, 128)[0], fast_dds.BC1_UNORM_SRGB)
            self.assertEqual(len(data), 148 + sum(max(1, (1024 >> i) // 4) ** 2 * 8 for i in range(11)))

    def test_version_paths_agree(self):
        self.assertEqual(style.ART, '/e2r_stream/fast-art/' + art.VERSION)
        source = (ROOT / 'mod/lua/ge/extensions/e2r/native.lua').read_text(encoding='utf8')
        self.assertIn(style.ART + '/packs.json', source)
        self.assertIn('pixel', art.PACKS)
        for name in ('pixel', 'night', 'rain', 'cyber', 'synth'):
            self.assertTrue(art.environment(name)['authoredSun'])
        self.assertFalse(art.environment('real').get('authoredSun', False))


class TransitionTests(unittest.TestCase):
    def setUp(self):
        self.lua = lua = LuaRuntime()
        lua.execute('''
          ui_imgui = setmetatable({ArrayChar=function() return {} end}, {__index=function() return function() end end})
          require=function() return {} end
          hptimer=function() return {stop=function() return 1 end, stopAndReset=function() return 1 end} end
          function up(fn, name)
            for i=1,200 do
              local n,v=debug.getupvalue(fn,i)
              if not n then break end
              if n==name then return v end
            end
            error('missing upvalue '..name)
          end
          exposure={autoExposure=true, manualEV=12}
          function object(fields)
            return {fields=fields, getField=function(self,k) return self.fields[k] or '' end,
              setField=function(self,k,_,v) self.fields[k]=tostring(v) end,
              postApply=function() if exposure then exposure.autoExposure=true end end,
              reload=function() end}
          end
          sky=object({elevation='15', azimuth='142', nightColor='0.1 0.1 0.1 1'})
          road=object({roughnessFactor='0.8', emissiveFactor='0 0 0'})
          objects={sunsky=sky, e2rf_road=road}
          scenetree={findObject=function(name)
            if name=='PostEffectLocalExposureObject' then return exposure end
            return objects[name]
          end}
          priorDriver=function(ctx) ctx.sunEl=15; ctx.sunAz=142; ctx.other=true end
          driver=priorDriver
          core_celestial={getDriver=function() return driver end, setDriver=function(fn) driver=fn end}
        ''')
        lua.globals().mod = lua.execute((ROOT / 'mod/lua/ge/extensions/e2r/world.lua').read_text(encoding='utf8'))
        lua.execute('''
          S=up(mod.tick,'S'); S.now=10
          apply=up(up(mod.tick,'beginArrival'),'applyPack')
          again=up(mod.tick,'envAgainStep')
          S.packs={night={e2rf_road={roughnessFactor=.2}}, real={e2rf_road={emissiveFactor='0 0 0'}}}
          S.packEnv={night={exposure=5,authoredSun=true,objects={sunsky={elevation='-25',azimuth='142'}}},
            real={objects={sunsky={elevation='48',azimuth='142'}}}}
        ''')

    def test_celestial_driver_holds_authored_sun_and_restores_previous(self):
        self.lua.execute('''
          apply('night')
          local ctx={}; driver(ctx)
          assert(ctx.sunEl==-25 and ctx.other)
          assert(exposure.autoExposure==false and exposure.manualEV==5)
          S.now=11; again()
          assert(exposure.autoExposure==false)
          apply('real')
          assert(driver==priorDriver and exposure.autoExposure==true and exposure.manualEV==12)
          assert(road.fields.roughnessFactor=='0.8')
          S.now=20; again()
          assert(sky.fields.elevation=='48' and exposure.autoExposure==true)
        ''')

    def test_late_exposure_and_unload_restore(self):
        self.lua.execute('''
          exposure=nil; apply('night'); assert(S.exposurePending)
          exposure={autoExposure=true,manualEV=11}; S.now=10.6; again()
          assert(exposure.manualEV==5 and not exposure.autoExposure)
          mod.onExtensionUnloaded()
          assert(exposure.manualEV==11 and exposure.autoExposure)
          assert(driver==priorDriver and S.envAgain==nil and S.exposurePending==nil)
          assert(sky.fields.elevation=='15' and S.packOrig==nil and S.packsWarm==nil)
        ''')

    def test_other_driver_is_not_removed_and_older_engine_works(self):
        self.lua.execute('''
          apply('night'); local other=function() end; driver=other
          apply('real'); assert(driver==other)
          core_celestial=nil; apply('night')
          assert(exposure.manualEV==5)
          mod.onExtensionUnloaded(); assert(exposure.manualEV==12)
        ''')

    def test_rapid_switch_preserves_pending_material_reloads(self):
        self.lua.execute('''
          S.packs.second={e2rf_road={roughnessFactor=.2}}
          S.packEnv.second=S.packEnv.night
          apply('night'); assert(#S.reloadQueue==1)
          apply('second'); assert(#S.reloadQueue==1)
          apply('real'); assert(#S.reloadQueue==1)
          assert(road.fields.roughnessFactor=='0.8')
          S.now=30; again()
          assert(driver==priorDriver and exposure.autoExposure==true)
        ''')


if __name__ == '__main__':
    unittest.main()
