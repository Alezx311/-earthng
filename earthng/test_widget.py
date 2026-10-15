"""Regression checks for the map widget. Lua checks require lupa (or out/test-tools)."""
import sys
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'out/test-tools'))
from lupa.luajit21 import LuaRuntime
from world_server import Server


UK_LABELS = {'pick': 'Обрати інше місце', 'back': 'Повернутися до їзди', 'hide': 'Приховати', 'show': 'Показати мапу'}
EN_LABELS = {'pick': 'Pick another place', 'back': 'Back to driving', 'hide': 'Hide', 'show': 'Show map'}


class WidgetTests(unittest.TestCase):
    def test_map_ui_keeps_streaming(self):
        self.map_ui_flow('uk', UK_LABELS)

    def test_map_ui_in_english(self):
        # the UK and California packages: build_mod.py --lang en rewrites the LANG line
        self.map_ui_flow('en', EN_LABELS)

    def test_package_offers_its_own_buttons(self):
        lua = LuaRuntime()
        lua.execute('''
          ui_imgui = setmetatable({ArrayChar=function() return {} end}, {__index=function() return function() end end})
          require=function() return {} end
          hptimer=function() return {stopAndReset=function() return 1 end} end''')
        src = (Path(__file__).resolve().parents[1] / 'mod/lua/ge/extensions/e2r/world.lua').read_text(encoding='utf8')
        lua.globals().mod = lua.execute(src)
        shown = lua.eval('''function(status)
          local function up(fn, name)
            for i=1,100 do local n,v=debug.getupvalue(fn,i); if n==name then return v end end
          end
          local draw=up(mod.onUpdate,'drawWindow')
          up(mod.tick,'S').status=status
          local inPackage, out = up(draw,'presetInPackage'), {}
          for _, p in ipairs(up(draw,'PRESETS')) do if inPackage(p) then out[#out+1]=p[1] end end
          return table.concat(out, ',')
        end''')
        # Wales' box overlaps Bristol and Liverpool; only Welsh buttons
        wales = shown(lua.table_from({'area': 'uk-wales', 'packageBox': lua.table_from([-5.4, 51.35, -2.6, 53.45])}))
        self.assertEqual(wales.split(','), ['Cardiff', 'Swansea', 'Snowdonia', 'Aberystwyth', 'Conwy', 'Brecon Beacons'])
        # the whole UK: every UK button
        whole = shown(lua.table_from({'area': 'united-kingdom', 'packageBox': lua.table_from([-8.75, 49.4192, 1.9, 61.0])}))
        self.assertIn('Bristol', whole)
        self.assertIn('Stornoway', whole)
        self.assertNotIn('Times Square', whole)
        # the Ukraine box holds Bucharest and Iasi; the Romania box, Chernivtsi
        ukraine = shown(lua.table_from({'area': 'ukraine', 'packageBox': lua.table_from([22.1, 44.3, 40.3, 52.4])}))
        self.assertIn('Чернівці', ukraine)
        self.assertNotIn('Bucharest', ukraine)
        romania = shown(lua.table_from({'area': 'romania', 'packageBox': lua.table_from([20.2, 43.6, 29.8, 48.3])}))
        self.assertEqual(romania.split(',')[0], 'Bucharest')
        self.assertNotIn('Чернівці', romania)
        # a package the buttons do not know (a dev extract): by the box
        other = shown(lua.table_from({'area': 'kyiv', 'packageBox': lua.table_from([30.2, 50.2, 30.8, 50.6])}))
        self.assertEqual(other.split(','), ['Оболонь', 'Поділ', 'Хрещатик', 'Печерськ', 'Голосіїв', 'Біличі', 'Ірпінь', 'Бровари'])

    def map_ui_flow(self, lang, labels):
        lua = LuaRuntime()
        lua.execute('''
          ui_imgui = setmetatable({ArrayChar=function() return {} end,
            Button=function(label) return label == clicked end,
            GetIO=function() return {MouseWheel=0} end,
            GetMousePos=function() return {x=0,y=0} end,
            GetItemRectMin=function() return {x=0,y=0} end,
            IsItemHovered=function() return false end,
            IsMouseDown=function() return false end,
            CalcTextSize=function() return {x=10,y=10} end,
            GetStyle=function() return {ItemSpacing={x=4}} end,
            ImVec2=function(x, y) return {x=x, y=y} end,
            GetContentRegionAvailWidth=function() return 360 end,
            WindowFlags_NoCollapse=1, WindowFlags_AlwaysAutoResize=2},
            {__index=function() return function() end end})
          require=function() return {} end
          FS={fileExists=function() return false end}
          maptileRequests=0
          jsonWriteFile=function(path, tbl) if tbl.type=='maptiles' then maptileRequests=maptileRequests+1 end end
          hptimer=function() return {stopAndReset=function() return 1 end} end
          function up(fn, name, replacement)
            for i=1,100 do
              local n,v=debug.getupvalue(fn,i)
              if not n then break end
              if n==name then
                if replacement then debug.setupvalue(fn,i,replacement) end
                return v
              end
            end
            error('missing upvalue '..name)
          end
        ''')
        src = (Path(__file__).resolve().parents[1] / 'mod/lua/ge/extensions/e2r/world.lua').read_text(encoding='utf8')
        mod = lua.execute(src.replace("local LANG = 'uk'", f"local LANG = '{lang}'"))
        lua.globals().mod = mod
        for key, label in labels.items():
            lua.globals()['label_' + key] = label
        lua.execute('''
          S=up(mod.tick,'S')
          draw=up(mod.onUpdate,'drawWindow')
          S.state='driving'; S.now=10; S.stream={}; S.viewDirty=true
          test=up(mod.onUpdate,'test')
          up(mod.tick,'test',{skipMap=true,manual=true})
          up(mod.tick,'placeSun',function() end)
          up(mod.tick,'readStatus',function() end)
          up(mod.tick,'replayStep',function() end)
          poses=0; tiles=0; collision=0
          up(mod.tick,'writePose',function() poses=poses+1 end)
          up(mod.tick,'updateTiles',function() tiles=tiles+1 end)
          up(mod.tick,'updateCollision',function() collision=collision+1; return true end)
          up(draw,'focus',function() return nil end)
          clicked=label_pick; draw()
          assert(S.state=='driving' and S.pickerOpen)
          mod.tick(0.016)
          assert(poses==1 and tiles==1 and collision==1)
          -- the map asks the companion for its OSM tiles (none are on disk here)
          assert(maptileRequests==1)
          clicked=label_back; draw()
          assert(S.state=='driving' and not S.pickerOpen)
          clicked=label_hide; draw()
          assert(S.widgetHidden)
          local asked=maptileRequests
          mod.tick(0.016); draw()
          assert(poses==2 and tiles==2 and maptileRequests==asked)  -- a hidden map asks nothing
          clicked=label_show; draw()
          assert(not S.widgetHidden and S.state=='driving')
          mod.tick(0.016)
          assert(poses==3)
          clicked=nil; S.now=30; draw()
          assert(maptileRequests>asked)  -- shown again: asks again (retry after MAP_RETRY)
        ''')

    def test_slow_map_is_nonblocking_and_coalesced(self):
        server = Server.__new__(Server)
        server.view_future = server.pending_view = None
        started, release = threading.Event(), threading.Event()
        seen = []

        def render(req):
            seen.append(req['seq'])
            started.set()
            self.assertTrue(release.wait(3))

        server.render_view = render
        with ThreadPoolExecutor(1) as server.view_worker:
            try:
                server.queue_view({'seq': 1})
                server.pump_view()
                self.assertTrue(started.wait(1))
                server.queue_view({'seq': 2})
                server.queue_view({'seq': 3})
                server.pump_view()  # must return while the map is still blocked
                self.assertFalse(server.view_future.done())
                self.assertEqual(server.pending_view['seq'], 3)
            finally:
                release.set()
            server.view_future.result(timeout=2)
            server.pump_view()
            server.view_future.result(timeout=2)
        self.assertEqual(seen, [1, 3])


if __name__ == '__main__':
    unittest.main()
