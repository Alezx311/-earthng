"""Regression checks for the map widget. Lua checks require lupa (or out/test-tools)."""
import sys
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'out/test-tools'))
from lupa.luajit21 import LuaRuntime
from world_server import Server


class WidgetTests(unittest.TestCase):
    def test_map_ui_keeps_streaming(self):
        lua = LuaRuntime()
        lua.execute('''
          ui_imgui = setmetatable({ArrayChar=function() return {} end,
            Button=function(label) return label == clicked end,
            GetIO=function() return {MouseWheel=0} end,
            GetMousePos=function() return {x=0,y=0} end,
            GetItemRectMin=function() return {x=0,y=0} end,
            IsItemHovered=function() return false end,
            IsMouseDown=function() return false end,
            WindowFlags_NoCollapse=1, WindowFlags_AlwaysAutoResize=2},
            {__index=function() return function() end end})
          require=function() return {} end
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
        mod = lua.execute((Path(__file__).resolve().parents[1] /
                           'mod/lua/ge/extensions/e2r/world.lua').read_text(encoding='utf8'))
        lua.globals().mod = mod
        lua.execute('''
          S=up(mod.tick,'S')
          draw=up(mod.onUpdate,'drawWindow')
          S.state='driving'; S.now=10; S.stream={}; S.viewDirty=true
          test=up(mod.onUpdate,'test')
          up(mod.tick,'test',{skipMap=true,manual=true})
          up(mod.tick,'placeSun',function() end)
          up(mod.tick,'readStatus',function() end)
          up(mod.tick,'replayStep',function() end)
          views=0; poses=0; tiles=0; collision=0
          up(mod.tick,'requestView',function() views=views+1 end)
          up(mod.tick,'writePose',function() poses=poses+1 end)
          up(mod.tick,'updateTiles',function() tiles=tiles+1 end)
          up(mod.tick,'updateCollision',function() collision=collision+1; return true end)
          up(draw,'focus',function() return nil end)
          clicked='Обрати інше місце'; draw()
          assert(S.state=='driving' and S.pickerOpen)
          mod.tick(0.016)
          assert(poses==1 and tiles==1 and collision==1 and views==1)
          clicked='Повернутися до їзди'; draw()
          assert(S.state=='driving' and not S.pickerOpen)
          clicked='Приховати'; draw()
          assert(S.widgetHidden)
          mod.tick(0.016)
          assert(poses==2 and tiles==2 and views==1)
          clicked='Показати мапу'; draw()
          assert(not S.widgetHidden and S.state=='driving')
          mod.tick(0.016)
          assert(poses==3 and views==2)
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
