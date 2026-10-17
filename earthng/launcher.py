"""EarthNG launcher: one window to set up and start the mod, the companion and the game.

    EarthNG.bat                       (repository root or the tester package)
    <python>/pythonw.exe earthng/launcher.py

Replaces run_world.ps1 / package start.ps1 for everyday use (they stay as console fallbacks).
Works in both layouts: the repository (its own .venv from requirements-fast.txt; the mod
is rebuilt from mod/) and the tester package (its own python/, the ready mod/earthng_planet.zip).
The old v2 generator still needs an Earth2Road checkout. fast and the country window do not.
Settings are kept in launcher.json next to earthng/. The companion's output goes to the log pane;
closing the window stops the companion and its tile builders. The window speaks the language of the
tile package (lang.py): Ukrainian for Ukraine, English for the UK and California.
"""
import json
import os
from pathlib import Path
import queue
import shutil
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import lang

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
PACKAGED = (ROOT / 'python' / 'python.exe').exists()
SETTINGS = ROOT / 'launcher.json'
STEAM_GAME = 'steam://rungameid/284160'
MOD_ZIP = 'earthng_planet.zip'
NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0)


def default_userfolder():
    base = Path(os.environ.get('LOCALAPPDATA', ''))
    candidates = [base / 'BeamNG' / 'BeamNG.drive' / 'current', base / 'BeamNG.drive' / 'current']
    return str(next((c for c in candidates if c.exists()), candidates[0]))


def tile_packages():
    """OSM tile packages (osm_tiles.py / osm_tiles_country.py output): folder name -> path."""
    base = ROOT / 'data' / 'osm_tiles'
    if not base.exists():
        return {}
    return {d.name: d for d in sorted(base.iterdir()) if (d / 'index.json').exists()}


def default_tiles(packages):
    for name in ('ukraine-260918', 'kyiv-260918'):
        if name in packages:
            return name
    return next(iter(packages), '')


DEFAULTS = {
    'userfolder': default_userfolder(),
    'earth2road': os.environ.get('EARTH2ROAD', 'C:\\js\\Earth2Road'),
    'tiles': '',
    'generator': 'fast',
    'workers': max(2, min(8, (os.cpu_count() or 4) // 2)),
    'radius': 800,
    'far_radius': 3000,
    'launch_game': True,
    'rebuild_mod': True,
}


def load_settings():
    s = dict(DEFAULTS)
    try:
        s.update(json.loads(SETTINGS.read_text(encoding='utf8')))
    except (OSError, ValueError):
        pass
    return s


class Launcher:
    def __init__(self, root):
        self.root = root
        self.proc = None
        self.busy = False
        self.lines = queue.Queue()
        self.packages = tile_packages()
        s = load_settings()
        if s['tiles'] not in self.packages:
            s['tiles'] = default_tiles(self.packages)
        self.lang = lang.of(s['tiles'])
        T = self.t

        root.title(T('EarthNG — запуск', 'EarthNG — launcher') +
                   (T(' (пакет)', ' (package)') if PACKAGED else T(' (розробка)', ' (development)')))
        root.geometry('820x640')
        root.minsize(640, 480)
        self.v = {
            'userfolder': tk.StringVar(value=s['userfolder']),
            'earth2road': tk.StringVar(value=s['earth2road']),
            'tiles': tk.StringVar(value=s['tiles']),
            'generator': tk.StringVar(value=s['generator'] if not PACKAGED else 'fast'),
            'workers': tk.IntVar(value=s['workers']),
            'radius': tk.IntVar(value=s['radius']),
            'far_radius': tk.IntVar(value=s['far_radius']),
            'launch_game': tk.BooleanVar(value=s['launch_game']),
            'rebuild_mod': tk.BooleanVar(value=s['rebuild_mod']),
        }

        frame = ttk.Frame(root, padding=10)
        frame.pack(fill='both', expand=True)
        frame.columnconfigure(0, weight=1)

        start = ttk.LabelFrame(frame, text=T('Запуск', 'Start'), padding=8)
        start.grid(row=0, column=0, sticky='ew')
        start.columnconfigure(1, weight=1)
        self.path_row(start, 0, T('Тека профілю BeamNG', 'BeamNG user folder'), 'userfolder')
        r = 1
        self.e2r = None
        if not PACKAGED:
            self.e2r = ttk.Frame(start)
            self.e2r.grid(row=r, column=0, columnspan=3, sticky='ew')
            self.e2r.columnconfigure(1, weight=1)
            self.path_row(self.e2r, 0, T('Earth2Road, лише для v2', 'Earth2Road, only for v2'), 'earth2road')
            r += 1
        ttk.Checkbutton(start, text=T('Запустити BeamNG.drive через Steam', 'Start BeamNG.drive via Steam'), variable=self.v['launch_game']).grid(
            row=r, column=0, columnspan=3, sticky='w', pady=(4, 0))
        r += 1
        if not PACKAGED:
            ttk.Checkbutton(start, text=T('Перезібрати мод з mod/ перед встановленням', 'Rebuild the mod from mod/ before installing'),
                            variable=self.v['rebuild_mod']).grid(row=r, column=0, columnspan=3, sticky='w')

        gen = ttk.LabelFrame(frame, text=T('Генерація', 'Generation'), padding=8)
        gen.grid(row=1, column=0, sticky='ew', pady=(8, 0))
        gen.columnconfigure(1, weight=1)
        ttk.Label(gen, text=T('Дані OSM', 'OSM data')).grid(row=0, column=0, sticky='w')
        tiles = ttk.Combobox(gen, textvariable=self.v['tiles'], values=list(self.packages), state='readonly')
        tiles.grid(row=0, column=1, columnspan=2, sticky='ew', padx=6, pady=2)
        if not self.packages:
            ttk.Label(gen, text=T('немає тайлів у data/osm_tiles', 'no tiles in data/osm_tiles'), foreground='#b00').grid(row=0, column=1, sticky='w')
        ttk.Label(gen, text=T('Генератор', 'Generator')).grid(row=1, column=0, sticky='w')
        row = ttk.Frame(gen)
        row.grid(row=1, column=1, columnspan=2, sticky='w', padx=6, pady=2)
        ttk.Radiobutton(row, text=T('fast — уся країна, тайли довкола авто', 'fast — the whole area, tiles around the car'), value='fast',
                        variable=self.v['generator'], command=self.sync).pack(side='left')
        if not PACKAGED:
            ttk.Radiobutton(row, text=T('v2 — підготовлений регіон (out/region)', 'v2 — a prepared region (out/region)'), value='v2',
                            variable=self.v['generator'], command=self.sync).pack(side='left', padx=(12, 0))
        self.spin_row(gen, 2, T('Процеси генерації', 'Generation processes'), 'workers', 1, os.cpu_count() or 16, 1,
                      T('більше — швидше встигає за авто, але навантажує CPU разом із грою',
                        'more keeps up with the car better, but loads the CPU along with the game'))
        self.far_spin = self.spin_row(gen, 3, T('Далекий план, м', 'Far view, m'), 'far_radius', 0, 8000, 250,
                                      T('fast: місто вдалині довкола авто; 0 — вимкнено',
                                        'fast: the distant city around the car; 0 — off'))
        self.radius_spin = self.spin_row(gen, 4, T('Радіус тайлів, м', 'Tile radius, m'), 'radius', 300, 3000, 100,
                                         T('v2: тайли будуються в цьому радіусі від авто',
                                           'v2: tiles are built within this radius of the car'))

        buttons = ttk.Frame(frame)
        buttons.grid(row=2, column=0, sticky='ew', pady=8)
        self.start_btn = ttk.Button(buttons, text=T('▶ Запустити', '▶ Start'), command=self.start)
        self.start_btn.pack(side='left')
        self.stop_btn = ttk.Button(buttons, text=T('■ Зупинити', '■ Stop'), command=self.stop, state='disabled')
        self.stop_btn.pack(side='left', padx=6)
        ttk.Button(buttons, text=T('Тека BeamNG', 'BeamNG folder'), command=lambda: self.open_dir(self.v['userfolder'].get())).pack(
            side='left', padx=(12, 0))
        ttk.Button(buttons, text=T('Скинути налаштування', 'Reset settings'), command=self.reset).pack(side='right')
        self.status = tk.StringVar(value=T('Зупинено', 'Stopped'))
        ttk.Label(buttons, textvariable=self.status, foreground='#555').pack(side='left', padx=12)

        log = ttk.LabelFrame(frame, text=T('Журнал компаньйона', 'Companion log'), padding=4)
        log.grid(row=3, column=0, sticky='nsew')
        frame.rowconfigure(3, weight=1)
        self.log = tk.Text(log, height=12, wrap='none', font=('Consolas', 9), state='disabled')
        scroll = ttk.Scrollbar(log, command=self.log.yview)
        self.log.configure(yscrollcommand=scroll.set)
        scroll.pack(side='right', fill='y')
        self.log.pack(fill='both', expand=True)

        self.write(T('У грі: Freeroam → «Earth2Road - Планета». Вікно не закривати, поки граєте.\n',
                     'In the game: Freeroam → "Earth2Road - Planet". Keep this window open while you play.\n'))
        self.sync()
        root.protocol('WM_DELETE_WINDOW', self.close)
        root.after(100, self.pump)

    def t(self, uk, en):
        return lang.text(self.lang, uk, en)

    # --- form ---------------------------------------------------------------------------------
    def path_row(self, parent, row, label, key):
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky='w')
        ttk.Entry(parent, textvariable=self.v[key]).grid(row=row, column=1, sticky='ew', padx=6, pady=2)

        def browse():
            d = filedialog.askdirectory(initialdir=self.v[key].get() or str(ROOT))
            if d:
                self.v[key].set(str(Path(d)))
        ttk.Button(parent, text=self.t('Огляд…', 'Browse…'), command=browse).grid(row=row, column=2)

    def spin_row(self, parent, row, label, key, lo, hi, step, hint):
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky='w')
        cell = ttk.Frame(parent)
        cell.grid(row=row, column=1, columnspan=2, sticky='w', padx=6, pady=2)
        spin = ttk.Spinbox(cell, textvariable=self.v[key], from_=lo, to=hi, increment=step, width=8)
        spin.pack(side='left')
        ttk.Label(cell, text=hint, foreground='#666').pack(side='left', padx=(10, 0))
        return spin

    def sync(self):
        fast = self.v['generator'].get() == 'fast'
        self.far_spin.configure(state='normal' if fast else 'disabled')
        self.radius_spin.configure(state='disabled' if fast else 'normal')
        if self.e2r is not None:
            if fast:
                self.e2r.grid_remove()
            else:
                self.e2r.grid()

    def settings(self):
        out = {}
        for k, var in self.v.items():
            try:
                out[k] = var.get()
            except tk.TclError:   # a spinbox left with text that is not a number
                raise ValueError(self.t(f'«{k}»: потрібне число', f'"{k}": a number is needed')) from None
        if out['workers'] < 1:
            raise ValueError(self.t('Процесів генерації має бути щонайменше 1', 'At least 1 generation process is needed'))
        return out

    def reset(self):
        for k, var in self.v.items():
            var.set(DEFAULTS[k] if k != 'tiles' else default_tiles(self.packages))
        self.sync()

    # --- run ----------------------------------------------------------------------------------
    def start(self):
        if self.busy:
            return
        try:
            s = self.settings()
        except ValueError as e:
            messagebox.showerror('EarthNG', str(e))
            return
        if not s['tiles']:
            messagebox.showerror('EarthNG', self.t('Немає даних OSM у data/osm_tiles', 'No OSM data in data/osm_tiles'))
            return
        SETTINGS.write_text(json.dumps(s, ensure_ascii=False, indent=2), encoding='utf8')
        self.busy = True
        self.start_btn.configure(state='disabled')
        self.stop_btn.configure(state='normal')
        threading.Thread(target=self.run, args=(s,), daemon=True).start()

    def python(self, s):
        if PACKAGED or s['generator'] != 'v2':
            return ROOT / 'python' / 'python.exe' if PACKAGED else ROOT / '.venv' / 'Scripts' / 'python.exe'
        return Path(s['earth2road']) / '.venv' / 'Scripts' / 'python.exe'

    def environment(self, s):
        env = dict(os.environ, PYTHONUNBUFFERED='1', PYTHONIOENCODING='utf-8',
                   EARTHNG_FAR_R=str(s['far_radius']))
        if PACKAGED or s['generator'] != 'v2':
            env.update(EARTH2ROAD=str(ROOT / 'none'), PYTHONNOUSERSITE='1')
            env.pop('PYTHONPATH', None)
        else:
            env['EARTH2ROAD'] = s['earth2road']
        return env

    def run(self, s):
        try:
            T = self.t
            py, env, user = self.python(s), self.environment(s), Path(s['userfolder'])
            if not py.exists():
                raise RuntimeError(T(f'Немає Python: {py}', f'No Python: {py}'))
            self.status_set(T('Встановлюю мод…', 'Installing the mod…'))
            (user / 'mods').mkdir(parents=True, exist_ok=True)
            if PACKAGED or not s['rebuild_mod']:
                src = ROOT / 'mod' / MOD_ZIP if PACKAGED else ROOT / 'out' / 'mod' / MOD_ZIP
                if not src.exists():
                    raise RuntimeError(T(f'Немає зібраного мода: {src}', f'No built mod: {src}'))
                shutil.copy2(src, user / 'mods' / MOD_ZIP)
                self.lines.put(T('Мод встановлено: ', 'Mod installed: ') + f'{user / "mods" / MOD_ZIP}\n')
            else:
                code = self.execute([py, HERE / 'build_mod.py', '--out', ROOT / 'out' / 'mod', '--install', user,
                                     '--lang', lang.of(s['tiles']), '--legacy'], env)
                if code:
                    raise RuntimeError(T(f'build_mod.py завершився з кодом {code}', f'build_mod.py exited with code {code}'))
            if s['launch_game']:
                self.lines.put(T('Запускаю BeamNG.drive через Steam…\n', 'Starting BeamNG.drive via Steam…\n'))
                os.startfile(STEAM_GAME)
            cmd = [py, HERE / 'world_server.py', '--tiles', self.packages[s['tiles']], '--userfolder', user,
                   '--workers', s['workers'], '--radius', s['radius'], '--generator', s['generator']]
            if PACKAGED:
                cmd += ['--work', ROOT / 'work', '--cache', ROOT / 'data' / 'dem', '--regions', ROOT / 'work' / 'regions']
            self.status_set(T('Компаньйон працює: ', 'Companion running: ') +
                            f'{s["tiles"]}, {s["generator"]}, {s["workers"]} ' + T('процесів', 'processes'))
            code = self.execute(cmd, env, keep=True)
            self.lines.put(T(f'\nКомпаньйон зупинився (код {code}).\n', f'\nThe companion stopped (code {code}).\n'))
        except Exception as e:  # noqa: BLE001  everything goes to the log, the window stays
            self.lines.put(self.t('\nПОМИЛКА: ', '\nERROR: ') + f'{e}\n')
        finally:
            self.proc = None
            self.busy = False
            self.root.after(0, self.stopped)

    def execute(self, cmd, env, keep=False):
        cmd = [str(c) for c in cmd]
        self.lines.put('> ' + ' '.join(cmd) + '\n')
        proc = subprocess.Popen(cmd, cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                stdin=subprocess.DEVNULL, creationflags=NO_WINDOW,
                                text=True, encoding='utf-8', errors='replace', bufsize=1)
        if keep:
            self.proc = proc
        for line in proc.stdout:
            self.lines.put(line)
        return proc.wait()

    def stop(self):
        proc = self.proc
        if proc and proc.poll() is None:
            self.status_set(self.t('Зупиняю…', 'Stopping…'))
            # the companion's tile builders are child processes: stop the whole tree
            subprocess.run(['taskkill', '/T', '/F', '/PID', str(proc.pid)], capture_output=True,
                           creationflags=NO_WINDOW)

    def stopped(self):
        self.start_btn.configure(state='normal')
        self.stop_btn.configure(state='disabled')
        self.status.set(self.t('Зупинено', 'Stopped'))

    def close(self):
        if self.proc and self.proc.poll() is None:
            if not messagebox.askokcancel('EarthNG', self.t('Компаньйон працює. Зупинити його й закрити вікно?',
                                                             'The companion is running. Stop it and close the window?')):
                return
            self.stop()
        self.root.destroy()

    # --- ui plumbing --------------------------------------------------------------------------
    def status_set(self, text):
        self.root.after(0, self.status.set, text)

    def open_dir(self, path):
        if Path(path).exists():
            os.startfile(path)

    def write(self, text):
        self.log.configure(state='normal')
        self.log.insert('end', text)
        if int(self.log.index('end-1c').split('.')[0]) > 5000:
            self.log.delete('1.0', '1000.0')
        self.log.see('end')
        self.log.configure(state='disabled')

    def pump(self):
        chunk = []
        try:
            while len(chunk) < 500:
                chunk.append(self.lines.get_nowait())
        except queue.Empty:
            pass
        if chunk:
            self.write(''.join(chunk))
        self.root.after(100, self.pump)


def main():
    root = tk.Tk()
    try:
        ttk.Style(root).theme_use('vista')
    except tk.TclError:
        pass
    Launcher(root)
    root.mainloop()


if __name__ == '__main__':
    main()
