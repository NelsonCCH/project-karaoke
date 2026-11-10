# Application shell, web UI and install path

How the program starts and stops, who owns the session, the HTTP API, the settings files, the two web pages (the
remote and the lyrics screen), languages, colour schemes and styles, and the install path with everything it
assumes about the PC. The audio engine is in [audio.md](audio.md); the microphone and the Mac route in
[mic-and-mac.md](mic-and-mac.md).

Code: `karaoke/agent.py`, `desk.py`, `session.py` (not the mic DSP), `lyrics.py`, `__main__.py`, `__init__.py`,
`karaoke/web/**`; `tools/build_looks.py`, `_dump_recipes.js`, `fetch_fonts.py`, `doctor.py`, `make_shortcuts.ps1`,
`uninstall.ps1`, `restore_spotify_audio.py`; SETUP / AGENTS / GUIDE / README, `requirements.lock.txt`, `.gitignore`.
Tests: `test_agent.py`, `test_lyrics.py`, `test_song_keys.py`, `test_look_setting.py`, `test_presets.py`.

**About the numbers.** Where a figure was measured (browser timings, GPU, audio), it comes from the development PC
(RTX 3090, Realtek onboard audio, a USB mic, Windows 11). Section 3 says how to check each PC-specific assumption
on the machine in front of you.

---

## 1. Process model

### Two ways to run the same server

| Entry | Command | Started by | Lifetime |
|---|---|---|---|
| Desktop icon | `pythonw -m karaoke` -> `karaoke/__main__.py` -> `desk.main()` | "Project Karaoke.lnk" on the Desktop | while its window is open |
| Always-on helper | `pythonw -m karaoke.agent` -> `agent.main()` | "Project Karaoke Helper.lnk" in the Startup folder (optional) | forever, idle until a page presses Start |

Both run the *same* `Agent` class: an HTTP server on `0.0.0.0:8765`, a command loop on the main thread, and one
`Session`.

`desk.main()`:

1. `helper_running()` GETs `http://127.0.0.1:8765/state`; JSON containing `"settings"` means our helper is up. Else
   it tries to bind 8765: free -> `False`; taken by something else -> `None` -> a message box "Port 8765 is used by
   another program".
2. Helper already up: just `open_window()` and exit. The window then talks to the helper.
3. Otherwise this process *becomes* the helper: `Agent(desk=True)`, `on_ready = open_window`, `agent.run()`.
4. `open_window()` looks for `msedge.exe`, then `chrome.exe`, under `%ProgramFiles(x86)%`, `%ProgramFiles%` and
   `%LOCALAPPDATA%` (`Microsoft\Edge\Application`, `Google\Chrome\Application`) and launches
   `--app=http://localhost:8765/?desk=1 --window-size=520,900` (a chromeless app window). Fallback:
   `webbrowser.open`, a normal tab. Closing a tab still sends `/bye`, so it works, but it looks like a web page.
5. Any exception: logged to `agent.log`, plus a message box with a 3-frame traceback and the hint to run
   `tools\doctor.py`.

### Desk ownership: who stops the music when the window goes

- The window page is `/?desk=1`. It polls `/state?desk=1` every 500 ms; each such poll pushes `desk_deadline` to now
  + `DESK_GONE` (90 s). 90 s because a minimised or hidden Chromium window throttles timers to about one a minute.
- On `pagehide` it calls `sendBeacon("/bye")`: the deadline becomes min(deadline, now + `DESK_BYE` 3 s). 3 s, not 0,
  because a *reload* also fires `pagehide`, and the following poll re-arms the deadline.
- On its first poll, if `!s.active`, the window POSTs `/start {"output": "pc", "desk": true}` (auto-start).
  `desk: true` on a session that is not active sets `desk_owned = True`.
- When the deadline passes: if `desk_owned` or this is the desk process, and a session is active -> `session.stop()`.
  The desk process (`desk=True`) then returns from `run()` and exits (atexit restores Spotify). The always-on helper
  keeps running, idle.
- Consequences: with the helper running, closing the window stops karaoke that the window started, but not a session
  started from a phone (intended). A window that never polls (Edge failed to open) still lets the process die after
  90 s, because the deadline is armed at construction: no ghost process.

### The main-thread rule (invariant)

PortAudio WASAPI / WDM-KS streams must be opened and closed on one thread; from a worker they failed with "WDM-KS
-9999". So:

- HTTP handlers run on `ThreadingHTTPServer` threads and never touch audio. `/start`, `/stop`, `/set` and `/preset`
  are queued with `agent.post(fn)` onto `self.cmds`; `Agent.run()` (main thread) drains it with a 0.25 s timeout.
- These POSTs answer `{"ok": true}` *immediately*, before the command runs. The pages never learn of a refusal; they
  see the result on the next `/state` poll. (`/transport` is the exception: it calls `watcher.command` directly and
  returns its bool.)
- `Session` gets `call_main=agent.post`. Model loading happens in a worker thread (`restart().work`), then
  `call_main(open_on_main)` opens the streams back on the main thread. `Session._on_main(fn)` runs `fn` on the main
  thread and blocks the worker for the result (used to probe whether the WDM-KS speakers are free).
- Every main-loop iteration also runs: `route_check()` every 2 s while active; the crash-rescue check every 10 s
  while idle; the desk deadline; the Mac-player watchdog (`PLAYER_GONE`, 90 s).
- Exceptions inside queued commands are caught and logged ("command failed"); the loop survives.

### Start / stop lifecycle (`session.py`)

- **`start(output)`:** output must be "pc" or "mac"; if already active on a different output -> restart on the new
  one. Then `scan_devices(refresh=True)` (calls the private `sd._terminate()` / `_initialize()` so newly plugged
  devices appear), requires a device named `CABLE Output` (VB-Cable) and, for "pc", some speakers. It binds
  `127.0.0.1:8766` (`ENGINE_LOCK_PORT`) as a cross-process lock: only one engine per PC (window vs helper vs a test).
  If Spotify is not running, `os.startfile("spotify:")` (works for Store and classic installs). Then
  `route_check()`, then `restart()`.
- **`restart()`:** closes the old pipeline, bumps `gen` (a late model load from a previous generation is dropped),
  builds a `Pipeline`; the worker pauses other media players if a PC mic is chosen (`watcher.pause_others()`), runs
  `pipe.load()`, waits up to `SPEAKER_WAIT` (20 s) for paused players to release the speakers (a browser held them
  ~15 s, measured), then on the main thread `open_streams()`, applies the `room_eq.json` cuts, opens the mic, applies
  the settings.
- **`stop()`:** asks Spotify to pause (always, because `spotify_playing` lags 1 s or more), marks the session
  inactive, saves settings, closes mic and pipeline (which frees GPU memory), and restores Spotify's routing either
  at once or after `RESTORE_AFTER_PAUSE` (2 s, a Timer) so the last moment of the song does not leak unprocessed out
  of the speakers. Releases the 8766 lock. Status "Stopped.".
- **`route_check()`:** while active, every 2 s, re-routes Spotify to the cable if it is not confirmed there (a
  freshly opened Spotify reports `None` until it plays and must still be routed before its first song). If Spotify
  plays but stays off the cable for more than 6 s -> routing error "Spotify won't move to the cable ... Quit and
  reopen". Also warns when Windows' default output is the cable (the volume keys then do nothing).

### Crash, kill, and restoring Spotify's routing

Windows persists the per-app output (Spotify -> CABLE Input), even across reboots. Layers of defence:

1. Normal stop: `restore_spotify()` -> `approuting.route_spotify(False)` = back to "Default".
2. `atexit.register(restore_spotify)` in both `agent.main` and `desk.main` (clean exit, Python exception).
3. Crash rescue: any running agent, while idle (and more than 5 s after its own stop), every 10 s: if
   `approuting.spotify_on_cable()` and `engine_free()` (8766 bindable) -> restore and log "Spotify was left on the
   cable with no engine running: restoring it".
4. Manual: the desktop shortcut "Spotify Normal Audio" -> `pythonw tools\restore_spotify_audio.py`: opens Spotify if
   needed (waits up to 15 s + 2 s for its audio process), resets the routing, shows the outcome in a message box.
   Routing can only be changed while Spotify runs.
5. `tools\uninstall.ps1` also resets the routing.

A hard kill (Task Manager, power cut) skips 1 and 2; 3 only helps once the app runs again.

### HTTP API (port 8765; all JSON UTF-8; `Cache-Control: no-store` except `/look/`)

GET:

- `/` control.html, `/tv` tv.html, `/theme.css`, `/theme.js`, `/i18n.js`, `/qr.js` (the `PAGES` dict in agent.py).
  `PAGES` is a whitelist: any new top-level web file must be added to it.
- `/look/<path>`: files under `karaoke/web/look/` only (realpath containment check, else 404). Types by extension
  (`LOOK_TYPES`: css, js, png, jpg, svg, woff2). `Last-Modified` + `If-Modified-Since` -> 304. `.woff2` gets
  `max-age=31536000, immutable` (names are content hashes); everything else `no-cache` (revalidate on every load, so
  an update reaches every phone at once).
- `/state[?desk=1]` -> `Session.state()` plus agent extras (below). `?desk=1` re-arms the desk deadline.
- `/lyrics?serial=N` -> `{"serial", "loading": bool, "synced": [[t, line], ...], "plain": str, "source": str}`;
  while the background lookup runs: `{"serial", "loading": true}`; unknown serial: `{"serial", "missing": true}`.
- `/art?serial=N` -> cover image bytes (content type from the media session) or 404 `{"error": "no cover"}`.
- `/audio[?ahead=s]` -> the Mac player's raw stream (`netsink.py`; see [mic-and-mac.md](mic-and-mac.md)).

POST (JSON body; invalid JSON -> 400 `{"error": "bad json"}`; unknown path -> 404):

- `/start {"output": "pc"|"mac", "desk": bool}` (queued)
- `/stop {}` (queued)
- `/set {field: value, ...}` (queued); see Settings below. Unknown keys are silently dropped.
- `/preset {"action": "save", "name"}` | `{"action": "apply"|"update"|"delete", "index": i}` |
  `{"action": "rename", "index": i, "name"}` (queued). Names trimmed to 30 characters.
- `/transport {"action": "next"|"prev"|"playpause"|"pause"|"shuffle"}` or `{"action": "seek", "value": seconds}` ->
  `{"ok": bool}` (direct, through the Windows media session; works with no session active).
- `/bye` (beacon, any body): the desk window is closing.

### `/state` shape

```
active            bool   session running
ready             bool   pipeline + separator loaded and streams open
status            str    last status line (English; translated client-side by i18n PATTERNS)
output            "pc"|"mac"
player            bool   a Mac player is connected (the UI switches the mic card to Mac mode)
spotify_playing   bool   from the Windows media session (lags ~1 s)
spotify_open      bool   Spotify has a media session at all
warning           str    routing error or "default output is the cable" ("" when not active)
spotify           {serial, position, title, artist, duration, shuffle}   where Spotify itself is (ahead by the delay)
playing           bool   audio actually coming out
track             {serial, title, artist, album?, duration, ...} | null   the HEARD track
position          float|null  heard position in s, already shifted by -lyrics_offset/1000
latency           float|null  seconds behind Spotify
now_playing       watcher.now_playing
settings          see below (the live dict)
mic               {"on": false, "error"} | {"on": true, error, warning, paused_apps, peak, latency_ms, events{...}}
mic_presets       [{"name", "values": {mic, echo, reverb_size, delay_level, delay_ms, delay_repeat, voice, gate, comp}}]
options           {model: {key: "label · size"}, delay: [labels], eq: [names], speakers: [names],
                   mic_devices: [names], voice: [names], reverb_size: [names]}
stats             pipeline stats incl. gpu_load, underruns, dropped, slip_times | null
phone_url         "http://<LAN ip>:8765/"  (agent.phone_url, see traps)
player_connects, player_stalls, player_underruns, player_buffer   Mac sink counters
addresses         this PC's IPv4 addresses (incl. Tailscale 100.x), hostname   for the Mac player
```

The mic peak is *consumed* by each `/state` read (it decays x0.3 per poll), so two open pages each see a lower
meter.

### Settings (`Session.settings` in memory, persisted to settings.json)

- **Live** (`LIVE`, applied at once): vocal, volume, key, lyrics_offset, eq, mic, echo, mic_device, voice,
  reverb_size, delay_level, delay_ms, delay_repeat, gate, comp, plus `look` (only stored).
- **Restart the engine** (`RESTART`): model, delay, speakers.
- **Validation in `set()`:** key int clamped -12..12; vocal / volume / mic / echo / delay_level / delay_repeat float
  0..100; lyrics_offset converted to a number (ms, clamped -10000..10000); delay_ms int 60..500; gate / comp bool;
  look must match `[a-z]{0,20}` (`LOOK_ID`); model / delay / eq / voice / reverb_size must be in their option lists
  and mic_device in the current inputs or "", otherwise the **whole** `/set` call is dropped. A value that cannot be
  converted (a non-numeric key or offset) raises inside the queued command, which is logged and the call is dropped.
  On load, a non-numeric saved `lyrics_offset` falls back to the default. `speakers` is not validated (see Known
  issues).

### Files on disk (repo root, all git-ignored)

- **`settings.json`** (`save()`): every setting except key / vocal / mic (those are per song or per session), with
  vocal / volume / echo rounded, plus `song_keys` {"artist - title" lower-cased: semitones}, `song_vocals` {same:
  percent}, `mic_presets` [...], `look`. Written with `indent=1`, not atomically. Loaded once when the Session is
  built; bad values fall back to defaults per key. The mic level always starts at 0 (no surprise squeal); vocal
  starts at `DEFAULT_VOCAL` 10 and key at 0 unless the song is remembered. Delete the file to reset.
  - Per-song memory: `song_id = "<artist> - <title>"`, stripped and lower-cased (the same song from any album or
    playlist); only values different from the default are stored; setting the default back removes the entry. The
    switch happens in the mixer at the first *heard* sample of the next song (`_on_track`), so the end of the old
    song keeps its key; a key or vocal set during the wait after a skip goes to the coming song (`_key_song`).
- **`lyrics_cache.json`** (`lyrics.LyricsCache`): `{"title\tartist\tround(duration)": {synced, plain, source, at}}`.
  Written atomically (tmp + `os.replace`). Synced answers are kept forever; "none" and plain-only answers are asked
  again after 7 days (`NONE_AGAIN`). "LRCLIB did not answer" is never stored: retried after 15 s, up to 3 times, then
  given up as empty for this process. Safe to delete.
- **`room_eq.json`** (written by `tools/loop_check.py --save`): `{"speakers": "<device name>", "measured": "<note>",
  "cuts": [["peak", Hz, dB, Q], ...]}`. Applied to the music and the voice only when `speakers` equals the current
  speakers setting and output is "pc". Delete = off.
- **`agent.log`**: `logging.WARNING` level from both entries. pythonw has no console, so this is the only record.
  Expect a harmless "CUDAExecutionProvider not available in ONNXruntime" line per model load. Never rotated.
- **`models/`** (`engine.MODEL_DIR`): audio-separator downloads.

---

## 2. The web pages

No build step: vanilla, ES5-style JS served straight from `karaoke/web/`. Load order on both pages:

1. an inline no-flash `<script>` (reads localStorage, sets `data-scheme`);
2. `theme.css`, `look/kit.css`, `look/looks-tune.css`, `look/fonts/fonts.css` (non-blocking `media=print onload`);
3. `look/looks.js`, `look/look.js`;
4. the body starts with `<div id="kit">` and a second inline script that applies a saved style before first paint;
5. `i18n.js`, `theme.js` (+ `qr.js` on the TV page), then the page script.

### control.html (the remote; also the desk window)

- **Cards:** hero (cover, title / artist, seek bar, shuffle / prev / play-pause / next, status line, Start / Stop and
  "Lyrics screen"), Original vocal (slider + chips Off / 10% / Guide 40% / Original), Key (- / + / Reset), Volume,
  Sound (EQ chips built from `options.eq`), My presets (shown only with the mic card), Microphone (device select,
  level, meter, reverb + size chips, echo level / time / repeats, voice chips, Smooth / Noise gate; Mac mode shows
  level and reverb only), Lyrics timing (+-0.1 s, Reset -> -500 ms), Engine settings (a details block: delay /
  model / speakers selects + GPU stats), and a desk-only Keyboard card with the phone URL. A pop-up (`#popup`) shows
  `mic.warning` once per distinct text.
- **Polling:** `poll()` every 500 ms (`setTimeout` after each one completes); the desk adds `?desk=1`.
- **Debounce, and polling must not fight a drag.** `set(field, value)` records `touched[field] = now`, updates
  `S.settings` locally, re-renders, and batches all fields changed within 80 ms into one `POST /set`. `fresh(field)`
  is true when the field was not touched in the last 1500 ms; `render()` writes slider values only for fresh fields,
  and `poll()` copies the local value over the server's for every non-fresh live field before replacing `S`. The
  seek bar sets `touched.seek = now + 3000` while dragging. Selects are not refilled while focused. The delay / model
  / speakers selects POST directly (no debounce: they restart the engine).
- **Status text precedence** (in `render`): stopped (or an error status) -> warning -> error status -> loading ->
  Spotify not open -> playing ("N s behind Spotify") -> paused -> "Getting ready". An error is recognised by the regex
  `/fail|could not|another|not found|no speakers/i` on `status`: new error messages must match it to show as
  problems.
- **Keyboard** (desk only): Up / Down vocal +-10 (rounded to tens), V toggles 10 / 40, Left / Right key, 0 resets the
  key. Ignored in selects and text inputs.
- Start / Stop sends `output: "mac"` if a Mac player is connected, else "pc".
- `kitify()` adds `.btn` / `.chip` classes to every button, and `rangeFill` sets `--p` on sliders at each render:
  needed by the styles, harmless without one.
- **Presets UI:** optimistic local update, then `POST /preset`. "Matches" = all `PRESET_FIELDS` equal (numbers
  rounded) -> a lit chip. Delete needs a second click ("Sure?"). Rename on `change` of the inline input; the edit
  list is not rebuilt while you type in it.

### tv.html (the lyrics screen)

- Polls `/state` every 250 ms. On a new heard `track.serial`: fetch `/lyrics?serial=` (retry every 500 ms while
  `loading`, every 2 s on a network error), and set `--song` colour = a hash of title + artist into 10 fixed colours.
- **Render loop** in `requestAnimationFrame`: `target()` = last polled heard position + local elapsed time while
  playing; `clock` eases toward it (snaps if more than 0.4 s off). The current line = the last with `t <= clock`; it
  sits ~36% down the screen; scrolling glides (x0.12 per frame), not CSS transitions.
- **Messages:** "Can't reach the PC", "Karaoke is off" (+ "Scan the code ... or open <phone_url>"), the status while
  loading, "Play a song", "Finding lyrics…", "No lyrics found". Plain (unsynced) lyrics scroll in proportion to song
  time.
- **Top bar:** title · artist, a "Vocal N%" pill (while active), a "Key +-N" pill (when not 0), time, EN / 中文 and
  the palette (faint at 0.3 opacity until hovered). A 4 px progress bar. The cursor hides after 3 s idle.
- **QR card**, bottom right: `qrSvg(state.phone_url)` (`qr.js`, an in-house encoder: byte mode, ECC L, versions 1-5 =
  up to 106 bytes, verified by decoding). Hidden under 760 px wide (on a phone it would only cover lyrics).
- **Click-to-seek:** only when `seekable()`, i.e. a session is active and the heard track is the one Spotify is on
  (after a skip Spotify is already on the next song; seeking would hit the wrong one). A click POSTs `/transport
  seek` to `t - 1 s`; the line gets `.jump` and is held in view for up to 20 s until the heard clock is within
  [t - 2.5, t + 4], then normal following resumes. A touch drag of more than 10 px is not a click.
- **Browsing (scroll):** wheel (deltaMode-aware), touch drag, Up / Down (12% of the screen), PgUp / PgDn (6 steps).
  After `BROWSE_MS` (6 s) untouched it glides back; mouse movement over the lyrics extends that; Esc / Home returns
  at once (not during a jump).
- **Lyrics offset:** the server already folds `lyrics_offset` into `position`; the TV does no offset math. Negative =
  lines light up earlier. Default -500 ms. The offset is global, not per song.

### i18n.js

- `LANG` comes from `localStorage.pk_lang`, else `zh` if `navigator.language` starts with "zh", else `en`. Shared by
  both pages (same origin).
- Pages are written in English. `ZH` maps exact English strings to Traditional Chinese. `PATTERNS` regexes translate
  messages composed by the PC (session.py / pipeline.py status lines and warnings) with their captured parts.
- `T(s, n, extra)` translates, then fills `{n}` and `{key}` placeholders. `applyLang()` walks all text nodes
  (skipping script / style / select and anything under `[data-noi18n]`), stores the English in `node._en`, and
  translates `title`, `aria-label` and `<title>`. Dynamic text must be set through `T()` in the page code, with its
  container marked `data-noi18n`; otherwise `applyLang` re-translates already-translated text or fights `render()`.
- Values sent to the PC (EQ names, voice names, preset values, model keys) stay English; only the display is
  translated. Model labels "label · size" are translated by `Tm()` on the part before " · ".

### Colour schemes and styles

Two layers in one menu (`<details class="theme">` + `#thememenu`, built by `theme.js`'s `initTheme(key, default,
darkDefault, extraSchemes)`).

1. **Plain colour schemes.** `theme.css` `:root[data-scheme=...]` overrides only `--paper --card --ink --muted
   --line --accent --accent-soft`; the semantic colours `--danger --warn --ok` stay fixed (re-brightened once for
   the dark schemes). Light: paper (default), cafe, creme, sage, menthe, blue, blush, slate; dark: nuit, ocean,
   bordeaux; plus `auto` (follows `prefers-color-scheme`, live). The TV page adds `song` ("Song colour", its
   default; dark). Keys: remote `pk_scheme` (default paper), TV `pk_tv_scheme` (default song).
2. **Styles ("looks")**, 10 of them: neonkroom, mongkok, wkw, outrun, blackcat, deco, brass, midcentury, qinghua,
   lofi. Stored in the same key as `"look:<id>"`; the last plain choice is kept in `<key>_plain` to return to.
   `applyLook(kit, id)` (look.js) puts the look's palette / type / shape values as CSS variables in `#kit`'s style
   attribute and every other recipe course as a `data-<course>` attribute (kit.css rules are keyed on them), sets
   `data-dark` and `data-look`, injects `.fx` layers and a `.scn` scene container; then `lookFrame()` sets
   `data-scnfit` to "band" (the painting in a top band when at least 40% of the visible card area is opaque) or
   "full". It also sets `html[data-look]` and `--look-bg`. While a look is on, the plain `data-scheme` is set to
   nuit or paper by the look's darkness so the semantic colours stay readable. `looks-tune.css` re-maps the scheme
   variables under `.kit` (`--paper: var(--bg)` etc.), so page CSS works under any look.

**Where the looks come from.** `karaoke/web/look/looks.js` (recipes + CSS variables, generated); `kit.css` (generated:
only the rules of a separate design arsenal that these 10 recipes can match); `assets/` (icon sets deco / gothic /
round / scifi / sharp / steam; ornaments brass / deco / geometric / grid / neon / notebook / porcelain; 3 scene
paintings); `looks-tune.css` (hand-written: fits the arsenal's 390x844 phone-mock-up rules to the real pages, the
per-style last mile, and a TV section written to be read from a sofa ~3 m away); `look.js` (hand-written: English
names in `LOOK_NAMES`, apply, the motion switch).

**`tools/build_looks.py` needs the design arsenal, a separate repo that is not public.** It takes the path to the
arsenal's `design-menu/` folder as a required argument (`kit*.css`, `icons.css`, `axes*.js`, `sets-v2.js`,
`library-sets.js`, `lookbook-notes.js`, `assets/`) and needs `node` on PATH (`tools/_dump_recipes.js` evaluates the
arsenal's JS in a vm sandbox to resolve recipes with `fullPick`). Looks are selected by their gallery names (the
`LOOKS` list). The pruner keeps a selector if it could match any recipe (ignoring `:not()`, honouring `:is()`), keeps
only the icons in `ICONS`, drops unused `@keyframes`, copies the referenced `assets/`, and rewrites `'Noto Sans TC'`
to fall back to Microsoft JhengHei / PingFang TC. **The generated output is committed**, so no PC ever needs to run
it; all tuning goes in `looks-tune.css`, `looks.js` and `look.js`.

**Changing a style without the arsenal:**

- Visual tweak to one look: add rules to `looks-tune.css` scoped `.kit[data-look="<id>"] ...` (remote) or
  `.kit.tv[data-look="<id>"] ...` (TV). Do not edit `kit.css`: its header says it is generated.
- Palette / fonts / radius of an existing look: edit its `vars` in `looks.js` (and the "dots" for the menu swatch).
- Rename in the menu: `LOOK_NAMES` in look.js + the two strings in `i18n.js` `ZH`.
- New look: possible only by recombining course values that already have rules in `kit.css` (the pruner removed
  every option no current recipe uses). Copy a `looks.js` entry, change `id` (must match `[a-z]{0,20}`, `LOOK_ID` in
  session.py, or the PC refuses to store it), `vars`, and courses chosen among values other looks use; add
  `LOOK_NAMES` and `ZH` strings. Removing a look: delete it from looks.js and `LOOK_NAMES` (a stored id that no
  longer exists falls back to the default scheme).
- Try any look in a browser with `?look=<id>` (forced, not stored; `?look=none` forces none) and `?motion=on`.

**Style sync through the PC.** Picking a scheme calls `initTheme.onPick` -> `POST /set {look: "<id>"}`, or
`{look: ""}` for any plain scheme. Each poll calls `initTheme.follow(state.settings.look)`: a page switches to the
PC's look or, when the PC says "", drops its look and goes back to its own `<key>_plain` scheme. Plain schemes stay
per page (remote paper, TV song colour). Guards: polls are ignored for 2 s after a local pick (an in-flight poll
carries the old value); on the first poll, a page holding a look while the PC holds none pushes its look to the PC
instead of dropping it (so upgrading from per-browser looks keeps the choice). A page forced with `?look=` never
follows.

**Fonts.** `tools/fetch_fonts.py` downloads 14 Google Fonts families (latin + latin-ext slices; all slices of the two
CJK "TC" families; Noto Sans TC deliberately skipped, the OS CJK sans stands in) to `look/fonts/*.woff2`, named
`<family>-<md5(url)[:10]>.woff2`, and rewrites `fonts.css`. `*.woff2` is git-ignored (~45 MB) but `fonts.css` is
committed, so on a fresh clone every font request 404s until `fetch_fonts.py` has run once (SETUP runs it; it needs
internet). Without the fonts the looks fall back to system fonts: functional, less distinctive. Rerunning may rename
files if Google's URLs changed, producing a `fonts.css` diff; that is expected.

**Motion switch.** The looks animate (rain, glow, bokeh). Windows' "Animation effects: off" makes browsers report
`prefers-reduced-motion`, which the arsenal CSS honours, so on such a PC nothing moved. `html.motion-ok` re-enables
it; it is on unless `localStorage.pk_motion === "off"` (per browser, not synced through the PC). The menu row
"Motion on/off" sits after the Styles group.

---

## 3. Install path (SETUP.md) and what it assumes about the PC

Order: 0 prerequisites -> 1 venv + `pip install -r requirements.lock.txt --extra-index-url .../cu128`, then
`tools\fetch_fonts.py` -> 2 the person installs VB-CABLE as administrator and reboots, and resets Windows' output ->
3 `tools\doctor.py` -> 4 `doctor.py --speed` (downloads the default model, 1.7 GB) -> 5 first start from a console
with `python -m karaoke` (firewall prompt, answered by the person), then `make_shortcuts.ps1 [-NoHelper]` -> 6
listening test -> 7 GUIDE -> 8 the living room.

**ffmpeg is not needed.** audio-separator's `Separator()` normally refuses to start without `ffmpeg` on PATH, but
`engine.load_model` skips that check: the app only borrows its model loader and never writes audio.

### doctor.py (read-only; exit code 1 on any FAIL)

Checks: Python 3.11 (WARN otherwise) running inside a venv; the project is not under a path containing "onedrive";
free disk (FAIL under 1 GB, WARN under 2 or 4 GB depending on whether a `.ckpt` is already in `models/`); imports of
torch, audio_separator and the karaoke modules; CUDA available AND a real tensor op runs (old GPU architectures pass
`is_available()` but have no kernels), VRAM at least 6 GB (WARN); VB-Cable endpoints `CABLE Input` / `CABLE Output`;
Windows' default output is not the cable; some speakers exist; Spotify found (running, `Get-AppxPackage *Spotify*`,
or `%APPDATA%\Spotify\Spotify.exe`); port 8765 free or held by this app. `--speed [model]` times the separator at
every delay (PASS under 60% of real time, WARN under 85%, else FAIL).

It does not check: fonts present, Edge / Chrome present, the firewall rule, the network profile, Spotify signed in,
`pythonw.exe` present in the venv.

### Assumptions about the PC, and how to check them elsewhere

| Assumption | Where | How to discover / what to do |
|---|---|---|
| Python 3.11 via `py -3.11`, venv at `.venv` | SETUP step 1; the shortcuts hard-wire `<root>\.venv\Scripts\pythonw.exe` | `py -0p` lists installs. Moving the folder breaks the venv and the shortcuts: redo steps 1 and 5. |
| Python's firewall identity | The first console start uses `python.exe`; the shortcuts use `pythonw.exe`. A venv's `Scripts\python(w).exe` is a redirector that launches the *base* interpreter, so the rule probably lands on the base `python.exe`; `pythonw.exe` may trigger a second prompt, or be blocked silently if the first was answered for python.exe only. Not verified. | `Get-NetFirewallApplicationFilter \| ? Program -like '*python*'` (read-only), or Windows Security > Allow an app through firewall (the person changes it). A phone opening the remote is the real check. |
| Edge or Chrome in the standard install folders | `desk.open_window` | Otherwise a normal browser tab opens (works, looks like a web page). |
| Desktop and Startup folders | `make_shortcuts.ps1` uses `[Environment]::GetFolderPath("Desktop" / "Startup")`, which follows OneDrive redirection | Nothing to do. |
| Project not in OneDrive | SETUP step 0, doctor WARN | `$env:OneDrive`; compare with `(Get-Item .).FullName`. |
| Ports 8765 (HTTP) and 8766 (engine lock, loopback) free | `agent.PORT`, `session.ENGINE_LOCK_PORT` | `Get-NetTCPConnection -LocalPort 8765,8766`. No config option: changing them means editing the constants, `mac/player.py` and the docs. |
| Home network set to Private, firewall allows Private | GUIDE / SETUP troubleshooting | `Get-NetConnectionProfile` (read-only). The person changes it. |
| Phone URL = the interface that routes to 8.8.8.8 | `agent.phone_url` | With a VPN, Hyper-V switches or Tailscale exit nodes the QR may show the wrong IP; check `ipconfig`. When the page is opened via a LAN IP, that IP is used. |
| Device names | `CABLE Output` / `CABLE Input` substrings; "CABLE" excluded from speaker lists | VB-Cable variants (A/B cables, Voicemeeter) contain "CABLE" too; check `tools/check_devices.py`. See [audio.md](audio.md). |
| Spotify launched by the `spotify:` protocol | `Session.start`, `restore_spotify_audio.py` | Store and classic installs both register it. |
| NVIDIA driver >= 570 (cu128 wheels) | SETUP | `nvidia-smi`. |
| A nightly `onnx-weekly` pin | `requirements.lock.txt` | Old nightlies disappear from the index; SETUP has the unpinned fallback. |
| Internet for LRCLIB, model downloads and fonts | `lyrics.py`, engine, `fetch_fonts.py` | Lyrics need it at run time; the rest only once. |

### Shortcuts and uninstall

- `make_shortcuts.ps1` creates "Project Karaoke.lnk" (`pythonw -m karaoke`, icon imageres.dll,103), "Spotify Normal
  Audio.lnk" (`pythonw tools\restore_spotify_audio.py`, imageres,109), and, unless `-NoHelper`, "Project Karaoke
  Helper.lnk" in Startup (`pythonw -m karaoke.agent`). WorkingDirectory = the repo root (the code finds its files
  relative to `__file__`, so the working directory only matters for `-m karaoke` to import the package).
- `uninstall.ps1` kills python processes whose command line ends in `-m karaoke` or `-m karaoke.agent`, resets
  Spotify's routing via the venv python (Spotify must be open), and deletes the three shortcuts. It leaves VB-Cable,
  the firewall rule and the folder for the person to remove. It does not close an open Edge app window (harmless:
  that shows "Not connected").

---

## 4. Design decisions, rejected alternatives, invariants, traps

### Decisions

- **Browser UI instead of Tk.** The Tk window was replaced by an Edge / Chrome `--app` window on the same control
  page, so the phone remote and the lyrics screen work without the always-on helper.
- **Window = lifetime.** Closing the window stops karaoke and restores Spotify; detected by beacon + 90 s of poll
  silence, with 3 s grace for reloads.
- **Helper optional**, a Startup-folder shortcut rather than a service or scheduled task. Only needed to start from
  a phone with nobody at the PC, or for the Mac route.
- **Stop pauses Spotify** before restoring the routing 2 s later; otherwise the song plays on unprocessed.
- **One engine per PC via a loopback port lock** rather than a lock file: the OS releases it on a crash.
- **Crash rescue** relies on that lock: cable routing + no engine = stale routing.
- **The Windows media session (SMTC) for transport, cover and position:** no Spotify API account needed. Repeat left
  out: Spotify accepts the call but ignores it.
- **Positions travel with samples:** the lyrics page knows exactly the heard moment at any delay.
- **Per-song key and vocal**, switched at the first heard sample; the global saved vocal was removed.
- **Lyrics matching:** exact `/get` first, then `/search` with the artist, then title only. A candidate must have the
  same normalised title (or the title followed by a glued "-", "(" or "[" tail) and a duration within 2.5 s
  (`DURATION_SLACK`); prefer synced, then the same artist, then the closest duration. Rejected: accepting any
  candidate of the same length (it showed another band's song). LRCLIB 503s are retried (after 0 / 1 / 2 / 4 s);
  "unanswered" is never cached as "none".
- **Lyric wipe and intro dots removed:** they guessed timing that LRC does not carry, and distracted the singer.
- **Lyrics offset sign:** negative = earlier; default -0.5 s.
- **QR encoder written in-house:** works offline, no CDN; versions 1-5 only.
- **Styles pruned from an arsenal** instead of shipping all of it (72 KB of CSS, 1.9 MB of pictures); fonts
  self-hosted (offline LAN use, no Google at run time) but not committed (size).
- **`/look/` caching:** revalidate everything except hashed fonts, so an update reaches all phones.
- **Style shared through the PC, colour schemes per page:** the TV keeps its song colour when no style is set.
- **Motion on by default** even under reduced-motion: with Windows' animation effects off it never played otherwise.
- **`#kit` id selectors beat kit.css's (0,5,0) scene rule**, which had put the header and the pop-up under the cards.
- **Windowless children** (`karaoke/__init__.py`): under pythonw every subprocess gets `CREATE_NO_WINDOW`, else
  `import torch`'s `ver` flashes a console.
- **Mic level starts at 0 every session**, and presets include the level so one tap gets you singing.
- **Pause other players before opening the speakers for the mic:** a browser tab on the speakers made WDM-KS fail and
  fall back silently to ~170 ms; browsers hold the device ~15 s after pausing.

### Rejected or reverted

- "Quality climb" (slowing playback 3% while longer windows warm up): the measured gain between 2 s and 6 s windows
  was only 0.3-1 dB, not worth it.
- Kim as the default model -> becruily by ear, although Kim measures cleanest (17.3 vs 11.2-11.7 dB SDR).
- Default delays tried: ~6 s, ~3 s, ~2 s; now ~4.5 s.

### Invariants and traps

- **`PAGES` whitelist:** a new web file outside `look/` 404s until it is added.
- **`/look/` containment** uses `realpath` + prefix; files must live under `web/look/`.
- **`LOOK_ID = [a-z]{0,20}`:** a new look id with digits, hyphens or capitals is silently refused by the PC.
- **New PC status / warning strings** need an i18n `PATTERNS` entry (else they show in English in 中文 mode) and, if
  they are errors, must match the control.html status regex to be styled as a problem.
- **`/set` answers ok before it runs;** a refused call is visible only in the next `/state`.
- **`save_settings` is not atomic** (the lyrics cache is); a crash mid-write loses settings.json (load then falls
  back to defaults).
- **The mic meter peak is reset by each `/state` read;** several open pages share it.
- **`phone_url`** uses the 8.8.8.8 route trick: wrong with VPNs; an offline PC falls back to 127.0.0.1.
- **The Session constructor touches real devices** and starts the SpotifyWatcher, so `test_agent.py` needs a Windows
  PC with sounddevice / winrt. The tests stub `save`, so settings.json is never written, but the Session still
  *reads* the real `settings.json` and `lyrics_cache.json`.
- **`test_lyrics.py::test_find_matches_chinese_artist_from_spotify_english_name`** calls the real LRCLIB (skips if
  it is down).
- **`scan_devices(refresh=True)`** calls the private `sd._terminate` / `_initialize`; only valid with no stream open.
- **Opening the desk window while a phone-started session runs** does not take ownership; closing the window will
  not stop that session (intended).

---

## 5. Debugging and extending

### Debug

- Start from a console: `.venv\Scripts\python -m karaoke` (errors print; otherwise a message box). The helper alone:
  `.venv\Scripts\python -m karaoke.agent`.
- `Invoke-RestMethod http://localhost:8765/state | ConvertTo-Json -Depth 6`: everything the UI knows, including
  `stats`, `mic.events`, `warning`, `status`.
- `agent.log` (WARNING and above; raise the level in `logging.basicConfig` temporarily for more).
- Port or engine conflicts: `Get-NetTCPConnection -LocalPort 8765,8766 | select OwningProcess`.
- The UI without audio: the `tests/test_agent.py` pattern (`Agent(port=0)`, `run_queued`).
- Screenshots of a style: `/?look=<id>&motion=on`, `/tv?look=<id>`.
- Tests: `.venv\Scripts\python -m pytest tests -q` (GPU tests included; ~1-2 min). This area: `test_agent`,
  `test_lyrics`, `test_song_keys`, `test_look_setting`, `test_presets`.

### Add a setting end to end (example: a live boolean `foo`)

1. `Session.__init__`: `"foo": saved.get("foo", False)`, validating the loaded value.
2. Add it to `LIVE` (or `RESTART` if it needs a pipeline rebuild) and validate / clamp it in `set()`.
3. `apply()`: push it to the pipeline or mic chain. If it is per session (not saved), exclude it in `save()` like
   key and mic.
4. If it belongs in a mic preset: add it to `PRESET_FIELDS` in session.py AND control.html.
5. If it is an enum, expose its options in `state()["options"]`.
6. control.html: markup in a card, wire `set("foo", value)`, render it behind a `fresh("foo")` guard, and add "foo"
   to the live-field list in `poll()` so a pending local change is not overwritten.
7. i18n.js: `ZH` strings for the label and hint.
8. A test in `tests/` (pattern: `Session.__new__` + a stubbed `save`, as in `test_look_setting.py`).
9. GUIDE.md control table, and README if the owner will see it.

### Add a page string with a translation

Write English in the HTML (static text is translated automatically), or use `T("...")` for dynamic text inside a
`data-noi18n` element; add `"English": "中文"` to `ZH`; placeholders `{n}` / `{name}` via `T(s, n, {name: v})`. For
text produced in Python, add a regex to `PATTERNS`. Check both languages with the EN / 中文 pill.

### Add a style tune

In `looks-tune.css`, under the right section, scoped by `.kit[data-look="id"]` (remote) or
`.kit.tv[data-look="id"]` (TV). Check it at a 390 px phone width, in the 520x900 desk window and on a TV / 1080p
screen, with and without motion.

---

## 6. Known issues and open ends

| Issue | Where | Suggested fix |
|---|---|---|
| **No Origin check on the HTTP API.** POST bodies are parsed whatever the Content-Type, so any web page opened on the PC could `sendBeacon` / form-POST `text/plain` JSON to `http://localhost:8765/stop`, `/set` or `/transport` (CSRF). There is no auth by design (GUIDE: "no password"); acceptable on a home network, but know it. | `agent.py` POST handler | Reject POSTs whose `Origin` header is present and is not the page's own host (`http://<this PC>:8765` or the Mac relay); require `Content-Type: application/json`. |
| **One bad enum value drops the whole `/set` batch**, including valid fields sent in the same 80 ms window, and the page is never told. | `Session.set` | Drop only the invalid keys (and log them), apply the rest. |
| **Values duplicated between JS and Python:** `PRESET_FIELDS` (control.html and session.py), `LYRICS_OFFSET` (-500 in both), and the live-field list in control.html's `poll()` vs `LIVE`. | control.html, session.py | Send them in `/state` (e.g. `options.preset_fields`, `options.live`, `options.lyrics_offset`) and read them in the page. |
| **SETUP's listening test (step 6) does not exercise** the Motion switch, mic presets, the auto-pause of other players when a mic starts (and the ~15 s wait), or room correction. GUIDE covers all of these. | SETUP.md step 6 | Add a short checklist pointing at the GUIDE sections. |
| **README's feature list** does not mention styles, presets, room correction or click-to-seek (only "How it works" mentions styles). | README.md | Add one line each. |
| **SETUP Troubleshooting lacks:** fonts missing (plain system fonts), styles not syncing (a page opened with `?look=` never follows), a `/set` silently refused. | SETUP.md | Add entries. |
| **GUIDE "Lyrics screen"** does not say that clicking a line works only while the heard song is the one Spotify plays (not during the wait after a skip). | GUIDE.md | Add the sentence. |
| **doctor does not check** fonts, Edge / Chrome, the firewall rule, the network profile, Spotify sign-in, or `pythonw.exe` in the venv. | `tools/doctor.py` | Add read-only WARN checks for fonts, browser and `pythonw.exe`; the rest stay manual. |
