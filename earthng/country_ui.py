"""Window: download a ready country pack, or build one with its own layers and assets.

    Country.bat
    pythonw earthng/country_ui.py
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import queue
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import country_fetch
import looks
import places

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
SETTINGS = ROOT / 'country_setup.json'


def load_settings():
    mods = str(country_fetch.default_mods())
    settings = {'mods': mods, 'query': ''}
    if SETTINGS.exists():
        try:
            saved = json.loads(SETTINGS.read_text(encoding='utf-8'))
        except (OSError, json.JSONDecodeError):
            saved = {}
        if isinstance(saved, dict) and isinstance(saved.get('mods'), str):
            settings['mods'] = saved['mods']
    return settings


def save_settings(mods):
    SETTINGS.write_text(json.dumps({'mods': mods}, ensure_ascii=False, indent=1), encoding='utf-8')


class Setup:
    def __init__(self, root):
        self.root = root
        self.settings = load_settings()
        self.places = []
        self.by_id = {}
        self.shown = []
        self.place = None
        self.lines = queue.Queue()
        self.cancel = None
        self.worker = None
        root.title('EarthNG — country pack')
        root.geometry('980x740')
        root.minsize(860, 640)
        self.mods = tk.StringVar(value=self.settings['mods'])
        self.query = tk.StringVar(value='')
        self.status = tk.StringVar(value='Loading the region list…')
        self.size = tk.StringVar(value='')
        self.ready = tk.StringVar(value='')
        self.block = tk.StringVar()
        self.slots = {slot: tk.StringVar() for slot in looks.SLOTS}
        self.layers = {key: tk.BooleanVar(value=default) for key, _label, default in looks.LAYERS}
        self.use_box = tk.BooleanVar(value=False)
        self.box = {name: tk.StringVar() for name in ('w', 's', 'e', 'n')}
        self._build()
        root.after(200, self.pump)
        threading.Thread(target=self.load_places, daemon=True).start()

    def _build(self):
        outer = ttk.Frame(self.root, padding=10)
        outer.pack(fill='both', expand=True)
        top = ttk.Frame(outer)
        top.pack(fill='x')
        ttk.Label(top, text='BeamNG user folder').pack(side='left')
        ttk.Entry(top, textvariable=self.mods).pack(side='left', fill='x', expand=True, padx=8)
        ttk.Button(top, text='Browse', command=self.browse_mods).pack(side='left')
        ttk.Button(top, text='Refresh planet code', command=self.refresh_planet).pack(side='left', padx=(8, 0))

        body = ttk.Frame(outer)
        body.pack(fill='both', expand=True, pady=8)
        left = ttk.Frame(body)
        left.pack(side='left', fill='both', expand=True)
        search = ttk.Frame(left)
        search.pack(fill='x')
        ttk.Label(search, text='Region').pack(side='left')
        entry = ttk.Entry(search, textvariable=self.query)
        entry.pack(side='left', fill='x', expand=True, padx=8)
        entry.bind('<KeyRelease>', lambda _event: self.refill())
        ttk.Button(search, text='Refresh list', command=lambda: self.reload(True)).pack(side='left')
        self.list = tk.Listbox(left, exportselection=False)
        self.list.pack(fill='both', expand=True, pady=6)
        self.list.bind('<<ListboxSelect>>', lambda _event: self.on_select())

        right = ttk.Frame(body, padding=(12, 0, 0, 0))
        right.pack(side='left', fill='y')
        ttk.Label(right, textvariable=self.ready, wraplength=340).pack(anchor='w')
        ttk.Label(right, textvariable=self.size, foreground='#555').pack(anchor='w', pady=(2, 8))
        buttons = ttk.Frame(right)
        buttons.pack(fill='x', pady=(0, 8))
        self.download_btn = ttk.Button(buttons, text='Install a ready pack', command=self.download_ready)
        self.download_btn.pack(side='left')
        self.build_btn = ttk.Button(buttons, text='Build my own', command=self.build)
        self.build_btn.pack(side='left', padx=6)
        self.stop_btn = ttk.Button(buttons, text='Stop', command=self.stop, state='disabled')
        self.stop_btn.pack(side='left')

        ttk.Label(right, text='Asset block').pack(anchor='w')
        titles = [block['title'] for block in looks.BLOCKS]
        self.block_box = ttk.Combobox(right, textvariable=self.block, values=titles, state='readonly', width=36)
        self.block_box.pack(anchor='w', pady=(0, 6))
        self.block_box.bind('<<ComboboxSelected>>', lambda _event: self.apply_block())
        for slot, label in looks.SLOT_LABELS.items():
            row = ttk.Frame(right)
            row.pack(fill='x', pady=1)
            ttk.Label(row, text=label, width=22).pack(side='left')
            values = list(looks.SLOTS[slot].values())
            ttk.Combobox(row, textvariable=self.slots[slot], values=values, state='readonly', width=22).pack(side='left')
        ttk.Label(right, text='What goes into the pack. Roads and terrain are always included.').pack(anchor='w', pady=(8, 2))
        for key, label, _default in looks.LAYERS:
            ttk.Checkbutton(right, text=label, variable=self.layers[key]).pack(anchor='w')
        box = ttk.Frame(right)
        box.pack(fill='x', pady=(8, 0))
        ttk.Checkbutton(box, text='Limit to a box (west south east north)', variable=self.use_box).pack(anchor='w')
        coords = ttk.Frame(right)
        coords.pack(fill='x')
        for name, hint in (('w', 'W'), ('s', 'S'), ('e', 'E'), ('n', 'N')):
            ttk.Label(coords, text=hint).pack(side='left')
            ttk.Entry(coords, textvariable=self.box[name], width=8).pack(side='left', padx=(2, 6))

        ttk.Label(outer, textvariable=self.status, foreground='#333').pack(anchor='w')
        log_frame = ttk.Frame(outer)
        log_frame.pack(fill='both', expand=True, pady=(4, 0))
        self.log = tk.Text(log_frame, height=10, wrap='word')
        scroll = ttk.Scrollbar(log_frame, command=self.log.yview)
        self.log.configure(yscrollcommand=scroll.set)
        scroll.pack(side='right', fill='y')
        self.log.pack(fill='both', expand=True)

    def say(self, text):
        self.lines.put(str(text))

    def pump(self):
        while True:
            try:
                line = self.lines.get_nowait()
            except queue.Empty:
                break
            self.log.insert('end', line + '\n')
            self.log.see('end')
            self.status.set(line)
        self.root.after(200, self.pump)

    def browse_mods(self):
        chosen = filedialog.askdirectory(initialdir=self.mods.get() or os.getcwd())
        if chosen:
            self.mods.set(chosen)

    def load_places(self, refresh=False):
        try:
            self.places = places.load_index(ROOT, refresh, self.say)
            self.by_id = places.by_id(self.places)
            self.root.after(0, self.refill)
            self.say(f'{len(self.places)} regions. Pick a country or a smaller region inside it.')
        except Exception as exc:
            self.say(f'could not read the region list: {exc}')

    def reload(self, refresh):
        threading.Thread(target=self.load_places, args=(refresh,), daemon=True).start()

    def refill(self):
        self.shown = places.search(self.places, self.query.get())
        self.list.delete(0, 'end')
        for place in self.shown:
            parent = places.parent_name(place, self.by_id)
            suffix = f'  —  {parent}' if parent else ''
            self.list.insert('end', f"{place['name']}{suffix}")

    def on_select(self):
        selection = self.list.curselection()
        if not selection:
            return
        self.place = self.shown[selection[0]]
        look = looks.recommended(self.place['id'], self.by_id)
        self.show_look(look)
        self.refresh_ready()
        threading.Thread(target=self.fetch_size, args=(self.place['pbf'], self.place['id']), daemon=True).start()

    def show_look(self, look):
        block = looks.BLOCK_BY_ID[look['block']]
        self.block.set(block['title'])
        for slot, choices in looks.SLOTS.items():
            self.slots[slot].set(choices[look[slot]])

    def apply_block(self):
        title = self.block.get()
        block = next((item for item in looks.BLOCKS if item['title'] == title), None)
        if block is None:
            return
        self.show_look({'block': block['id'], **block['look']})

    def chosen_look(self):
        title = self.block.get()
        block = next((item for item in looks.BLOCKS if item['title'] == title), looks.BLOCK_BY_ID['general'])
        look = {'block': block['id']}
        for slot, choices in looks.SLOTS.items():
            label = self.slots[slot].get()
            found = next((key for key, text in choices.items() if text == label), None)
            look[slot] = found or block['look'][slot]
        return looks.canonical(look)

    def chosen_include(self):
        return looks.canonical_include({key: bool(self.layers[key].get()) for key, _label, _default in looks.LAYERS})

    def chosen_box(self):
        if not self.use_box.get():
            return None
        try:
            return [float(self.box[name].get().replace(',', '.')) for name in ('w', 's', 'e', 'n')]
        except ValueError as exc:
            raise ValueError('the box needs four numbers in degrees') from exc

    def refresh_ready(self):
        if self.place is None:
            return
        slug_id = places.slug(self.place['id'])
        info = country_fetch.availability(slug_id, self.mods.get())
        text = {
            'installed': 'A ready pack is already in mods.',
            'local': 'A ready pack is on disk. It can be copied into mods without a build.',
            'url': 'A link to a ready pack is available.',
            'none': 'No ready pack. Build one from OpenStreetMap and elevation.',
        }[info['state']]
        self.ready.set(text)
        self.download_btn.configure(state=('normal' if info['state'] in ('local', 'url') else 'disabled'))
        self._ready = info

    def fetch_size(self, url, place_id):
        try:
            size = country_fetch.content_length(url)
        except Exception as exc:
            self.say(f'could not read the PBF size: {exc}')
            return
        if self.place and self.place['id'] == place_id:
            if size:
                self.size.set(f'OSM extract: {size / 2**30:.2f} GB' if size >= 2**30 else f'OSM extract: {size / 2**20:.0f} MB')
            else:
                self.size.set('OSM extract size is unknown')

    def busy(self, running):
        state = 'disabled' if running else 'normal'
        self.build_btn.configure(state=state)
        self.stop_btn.configure(state=('normal' if running else 'disabled'))
        if not running:
            self.refresh_ready()

    def run(self, target):
        if self.worker and self.worker.is_alive():
            return
        save_settings(self.mods.get())
        self.cancel = threading.Event()
        self.busy(True)

        def wrap():
            try:
                target()
            except Exception as exc:
                self.say(f'stopped: {exc}')
            finally:
                self.root.after(0, lambda: self.busy(False))
        self.worker = threading.Thread(target=wrap, daemon=True)
        self.worker.start()

    def download_ready(self):
        if self.place is None:
            return
        info = getattr(self, '_ready', None) or {}
        slug_id = places.slug(self.place['id'])
        mods = self.mods.get()

        def target():
            if info.get('state') == 'local':
                path = country_fetch.install_zip(info['path'], mods, slug_id)
            elif info.get('state') == 'url':
                dest = ROOT / 'out' / 'country-setup' / f'earthng_{slug_id}.zip'
                country_fetch.download(info['url'], dest, self.say, self.cancel)
                path = country_fetch.install_zip(dest, mods, slug_id)
            else:
                self.say('no ready pack')
                return
            self.say(f'pack at {path}')
        self.run(target)

    def build(self):
        if self.place is None:
            messagebox.showinfo('EarthNG', 'Pick a region first.')
            return
        try:
            box = self.chosen_box()
            look = self.chosen_look()
            include = self.chosen_include()
        except ValueError as exc:
            messagebox.showerror('EarthNG', str(exc))
            return
        size_text = self.size.get()
        if 'GB' in size_text and not box:
            if not messagebox.askokcancel('EarthNG', f'{size_text}. Building the whole extract takes disk space and time. Continue?'):
                return
        place, mods = self.place, self.mods.get()
        block = looks.BLOCK_BY_ID[look['block']]['title']

        def target():
            self.say(f"building “{place['name']}”, block “{block}”. Roads and elevation always go into the pack.")
            self.say('The look is read from earthng_planet.zip. If the planet was built earlier, click “Refresh planet code”.')
            country_fetch.build_place(place, mods, look=look, include=include, box=box,
                                      by_id=self.by_id, emit=self.say, cancel=self.cancel)
        self.run(target)

    def stop(self):
        if self.cancel is not None:
            self.cancel.set()
            self.say('stopping after the current step')

    def refresh_planet(self):
        mods = self.mods.get()

        def target():
            country_fetch.refresh_planet(mods, self.say)
        self.run(target)


def main():
    root = tk.Tk()
    Setup(root)
    root.mainloop()


if __name__ == '__main__':
    main()
