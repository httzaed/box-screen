"""
control_server.py
Port : 7420  -> http://localhost:7420

Real-time state updates via Server-Sent Events (SSE):
- /api/events - SSE endpoint for live state updates
  Pushes: audio_spectrum, lyrics_position, auto_mode_scores, led_colors, beat_events
  Recommended poll rates: audio (10-15fps), auto_mode (0.5fps), others as needed
"""
from http.server import BaseHTTPRequestHandler, HTTPServer, ThreadingHTTPServer
from pathlib import Path
import json, threading, urllib.parse, time, queue
from collections import deque

PORT = 7420
_switch = _switch_hud = _get_state = _set_led = _auto_override = _set_auto_enabled = None

# Lock partagé pour protéger les calculs delta + switch dans _do_mode/_do_hud
# ThreadingHTTPServer est concurrent, il faut protéger l'accès à _switch et _switch_hud
_api_switch_lock = threading.Lock()



# ── SSE State Management ────────────────────────────────────────────────────────
_sse_clients = []  # List of (client_queue, last_connect_time)
_sse_lock = threading.Lock()
_SSE_CLIENT_TIMEOUT = 300  # Disconnect clients after 5 minutes of inactivity

# Import unified mode system
from modes import (
    DISPLAY_MODES as _MODES,
    HUD_STYLES as _HUD_STYLES,
    LED_MODES as _LED_MODES,
    LED_COLORS,
    SCENES,
    get_mode, get_led_config
)

# Local constants
_COLORS      = ["violet","cyan","blue","teal","green","yellow","orange","red","pink","white"]
_CASE_MODES  = _LED_MODES
_FAN_MODES   = _LED_MODES
_FLOAT_PARAMS= {"case_speed","fan_speed","case_length","brightness","case_decay"}
_VIZ_MODES   = ["ambience", "spectrum", "waveform"]
_SCENES_FILE = Path(__file__).parent / "scenes.json"


# ── Scene Management (uses scenes from modes.py) ───────────────────────────────────
def _load_scenes():
    """Load scenes from scenes.json or use defaults from modes.py."""
    try:
        if _SCENES_FILE.exists():
            return json.loads(_SCENES_FILE.read_text())
    except Exception as e:
        print(f"[SCENES] Error loading: {e}")

    # Return default scenes from modes.py
    return {k: {"mode": v.mode, "led": v.led.__dict__} for k, v in SCENES.items()}


def _save_scenes(scenes):
    """Save scenes to scenes.json."""
    try:
        _SCENES_FILE.write_text(json.dumps(scenes, indent=2))
    except Exception as e:
        print(f"[SCENES] Error saving: {e}")


def _apply_scene(scene_name):
    """Apply a scene by setting mode, HUD, and LED config."""
    try:
        scenes = _load_scenes()
        if scene_name not in scenes:
            print(f"[SCENE] Scene not found: {scene_name}")
            return False

        scene = scenes[scene_name]

        # Apply mode
        if "mode" in scene and _switch:
            _do_mode(scene["mode"])

        # Apply HUD
        if "hud" in scene and _switch_hud:
            _do_hud(scene["hud"])

        # Apply LED settings
        if "led" in scene and _set_led:
            led = scene["led"]
            for key, value in led.items():
                _set_led(key, value)

        return True
    except Exception as e:
        print(f"[SCENE] Error applying scene: {e}")
        return False


def _get_scenes_list():
    """Get list of available scene names safely."""
    try:
        scenes = _load_scenes()
        return list(scenes.keys())
    except Exception:
        return ["rave", "focus", "chill", "stealth", "gaming", "movie"]


def _get_auto_state():
    """Get auto mode state with scoring info."""
    try:
        import auto_mode
        return auto_mode.get_state()
    except Exception as e:
        return {'error': str(e)}


# ── SSE Client Management ─────────────────────────────────────────────────────────
def _sse_register_client(client_queue):
    """Register a new SSE client."""
    with _sse_lock:
        _sse_clients.append([client_queue, time.monotonic()])
        print(f"[SSE] Client connected ({len(_sse_clients)} active)")


def _sse_unregister_client(client_queue):
    """Unregister an SSE client."""
    with _sse_lock:
        _sse_clients[:] = [[q, t] for q, t in _sse_clients if q != client_queue]
        print(f"[SSE] Client disconnected ({len(_sse_clients)} active)")


def _sse_broadcast(event_type, data):
    """Broadcast an event to all connected SSE clients."""
    if not _sse_clients:
        return

    message = json.dumps(data)
    message_bytes = f"event: {event_type}\ndata: {message}\n\n".encode('utf-8')

    with _sse_lock:
        # Send to all clients and track dead ones
        dead_queues = []
        for client_queue, _ in _sse_clients:
            try:
                # Non-blocking put with size limit
                client_queue.put_nowait(message_bytes)
            except queue.Full:
                dead_queues.append(client_queue)
            except Exception:
                dead_queues.append(client_queue)

        # Remove dead clients (disconnection detected via write errors)
        if dead_queues:
            for dq in dead_queues:
                _sse_clients[:] = [[q, t] for q, t in _sse_clients if q != dq]


def _sse_get_audio_data():
    """Get real-time audio visualization data."""
    try:
        import bpm_source
        import numpy as np

        spectrum = bpm_source.get_viz_spectrum()
        waveform = bpm_source.get_viz_waveform()
        bands_list = bpm_source.get_viz_bands()
        band_history = bpm_source.get_viz_band_history()
        beat_ts, beat_seq = bpm_source.get_beat_event()

        # Convert numpy arrays to lists for JSON serialization
        return {
            'spectrum': spectrum.tolist() if spectrum is not None else [],
            'waveform': waveform.tolist() if waveform is not None else [],
            'bands': {
                'bass': float(bands_list[0]) if bands_list else 0.0,
                'mid': float(bands_list[1]) if len(bands_list) > 1 else 0.0,
                'treble': float(bands_list[2]) if len(bands_list) > 2 else 0.0
            },
            'band_history': band_history.tolist() if band_history is not None else [],
            'bpm': bpm_source.get_bpm() or 0,
            'level': float(bpm_source.get_level()) if bpm_source.get_level() is not None else 0.0,
            'beat_ts': beat_ts,
            'beat_seq': beat_seq
        }
    except Exception as e:
        return {'error': str(e)}


def _sse_get_led_color():
    """Get current LED fan color."""
    try:
        import led_fans
        color = led_fans._get_current_fan_color()
        return {
            'r': color[0],
            'g': color[1],
            'b': color[2],
            'hex': f'#{color[0]:02x}{color[1]:02x}{color[2]:02x}'
        }
    except Exception as e:
        return {'error': str(e)}


# ── SSE Background Thread ────────────────────────────────────────────────────────
_sse_running = False
_sse_thread = None


def _sse_broadcaster():
    """Background thread that broadcasts state updates to SSE clients."""
    global _sse_running

    # Event timing tracking
    last_audio_time = 0
    last_auto_time = 0
    last_led_time = 0
    last_beat_seq = None
    last_lyrics_state = None  # Memoize last lyrics state (artist, title, current_idx)

    AUDIO_INTERVAL = 0.05    # ~20 FPS for audio spectrum (sufficient for visual feedback)
    AUTO_INTERVAL = 2.0    # 0.5 FPS for auto mode (2 seconds)
    LED_INTERVAL = 0.5     # 2 FPS for LED color
    BEAT_DEBOUNCE = 0.1    # Minimum time between beat events

    while _sse_running:
        try:
            # Early-return si aucun client connecte - economise CPU
            if not _sse_clients:
                time.sleep(0.25)
                continue

            now = time.monotonic()

            # Audio spectrum data (high frequency)
            if now - last_audio_time >= AUDIO_INTERVAL:
                audio_data = _sse_get_audio_data()
                _sse_broadcast('audio_spectrum', audio_data)
                last_audio_time = now

                # Also check for beat events
                current_beat_seq = audio_data.get('beat_seq')
                if current_beat_seq is not None and current_beat_seq != last_beat_seq:
                    _sse_broadcast('beat_event', {
                        'seq': current_beat_seq,
                        'timestamp': audio_data.get('beat_ts', 0),
                        'level': audio_data.get('level', 0)
                    })
                    last_beat_seq = current_beat_seq

            # Auto mode state (low frequency)
            if now - last_auto_time >= AUTO_INTERVAL:
                auto_data = _get_auto_state()
                _sse_broadcast('auto_mode_scores', auto_data)
                last_auto_time = now

            # LED color (medium frequency)
            if now - last_led_time >= LED_INTERVAL:
                led_data = _sse_get_led_color()
                _sse_broadcast('led_colors', led_data)
                last_led_time = now

            # Lyrics position (on change only, tracked via state)
            try:
                import lyrics_source
                lyrics_state = lyrics_source.get_state()
                if lyrics_state:
                    lines = lyrics_state.get('lines', [])
                    current_idx = lyrics_state.get('current_idx', 0)
                    # Extraire la ligne courante uniquement (economie bande passante)
                    current_line = ''
                    if lines and 0 <= current_idx < len(lines):
                        line = lines[current_idx]
                        # Format selon la structure (synced: [time, text], non-synced: text direct)
                        if isinstance(line, list) and len(line) >= 2:
                            current_line = line[1]  # Format [time, text]
                        elif isinstance(line, str):
                            current_line = line
                        elif isinstance(line, dict):
                            current_line = line.get('text', '')

                    # Build current state key (artist, title, current_idx) - exclude 'loading' from comparison
                    current_state_key = (
                        lyrics_state.get('artist', ''),
                        lyrics_state.get('title', ''),
                        current_idx
                    )

                    # Only broadcast if meaningful state changed
                    if current_state_key != last_lyrics_state:
                        _sse_broadcast('lyrics_position', {
                            'artist': lyrics_state.get('artist', ''),
                            'title': lyrics_state.get('title', ''),
                            'current_idx': current_idx,
                            'total_lines': len(lines),
                            'synced': lyrics_state.get('has_sync', False),
                            'loading': lyrics_state.get('loading', False),
                            'current_line': current_line
                        })
                        last_lyrics_state = current_state_key
            except Exception:
                pass

            # Small sleep to prevent CPU spinning
            time.sleep(0.02)

        except Exception as e:
            print(f"[SSE] Broadcaster error: {e}")
            time.sleep(0.5)


def _sse_start():
    """Start the SSE broadcaster thread."""
    global _sse_running, _sse_thread
    if not _sse_running:
        _sse_running = True
        _sse_thread = threading.Thread(target=_sse_broadcaster, daemon=True, name='sse_broadcaster')
        _sse_thread.start()
        print("[SSE] Broadcaster started")


def _sse_stop():
    """Stop the SSE broadcaster thread."""
    global _sse_running
    _sse_running = False


# ── Load Phosphor UI from file ─────────────────────────────────────────────────
try:
    with open(Path(__file__).parent / "phosphor_ui.html", "r", encoding="utf-8") as f:
        _HTML = f.read()
except Exception:
    # Fallback UI simple
    _HTML = '''<!DOCTYPE html><html><head><meta charset="UTF-8"><title>Box Screen</title></head>
<body style="background:#020803;color:#2bff88;font-family:monospace;padding:20px;">
<h1>Box Screen Control</h1>
<p>UI file not found - using fallback interface</p>
<div style="margin:20px 0;">
    <h3>Mode</h3>
    <button onclick="fetch('/api/mode/ascii_vhs',{method:'POST'})">ASCII VHS</button><br><br>
    <button onclick="fetch('/api/mode/video',{method:'POST'})">Video</button><br><br>
    <button onclick="fetch('/api/mode/image',{method:'POST'})">Image</button><br><br>
    <button onclick="fetch('/api/mode/audio',{method:'POST'})">Audio</button><br><br>
    <button onclick="fetch('/api/mode/blank',{method:'POST'})">Blank</button>
</div>
<script>setInterval(()=>fetch('/api/status').then(r=>r.json()).then(d=>console.log(d)),1000);</script>
</body></html>'''


# ── Request Handler ───────────────────────────────────────────────────────────────

# ── Status Enrichment ───────────────────────────────────────────────────────────────
def _enrich_status_response(state):
    """Enrich the status response with audio, lyrics, and auto mode state."""
    try:
        import bpm_source
        state['audio'] = {
            'bpm': bpm_source.get_bpm(),
            'level': bpm_source.get_level()
        }
    except Exception:
        state['audio'] = {}

    try:
        import lyrics_source
        lyrics_state = lyrics_source.get_state()
        if lyrics_state:
            state['lyrics'] = {
                'artist': lyrics_state.get('artist', ''),
                'title': lyrics_state.get('title', ''),
                'position': lyrics_state.get('position', 0) or 0,
                'loading': lyrics_state.get('loading', False),
                'lines': lyrics_state.get('lines', []),
                'current_idx': lyrics_state.get('current_idx', 0),
                'synced': lyrics_state.get('has_sync', False)
            }
        else:
            state['lyrics'] = None
    except Exception:
        state['lyrics'] = None

    try:
        import auto_mode
        state['auto'] = auto_mode.get_state()
    except Exception:
        state['auto'] = {}

    return state


class _Handler(BaseHTTPRequestHandler):
    def log_message(self,fmt,*a): pass

    def _send(self,code,ct,body):
        try:
            if isinstance(body,str): body = body.encode()
            self.send_response(code)
            self.send_header("Content-Type",ct)
            self.send_header("Content-Length",len(body))
            self.send_header("Access-Control-Allow-Origin","*")
            self.end_headers()
            self.wfile.write(body)
        except Exception as e:
            print(f"[HTTP] Error sending response: {e}")

    def do_GET(self):
        try:
            p=urllib.parse.urlparse(self.path).path
            if p in ("/","/index.html"):
                self._send(200,"text/html; charset=utf-8",_HTML)
            elif p=="/api/events":
                # SSE endpoint for real-time state updates
                self._handle_sse()
                return
            elif p=="/api/status":
                state=_get_state() if _get_state else {}
                try:
                    import led_fans as lf; state["led"]=lf.get_cfg()
                except Exception: state["led"]={}
                # Add scenes list (with error handling)
                try:
                    state["scenes"] = _get_scenes_list()
                except Exception:
                    state["scenes"] = ["rave", "focus", "chill", "stealth", "gaming", "movie"]
                # Add viz mode
                try:
                    import render_audio_viz
                    state["viz_mode"] = render_audio_viz.get_viz_mode()
                except Exception:
                    state["viz_mode"] = "spectrum"
                # Add audio data for Phosphor UI
                try:
                    import bpm_source
                    state["bpm"] = bpm_source.get_bpm() or 0
                    state["level"] = bpm_source.get_level()
                    _, state["beat_seq"] = bpm_source.get_beat_event()
                except Exception:
                    state["bpm"] = 0
                    state["level"] = 0
                    state["beat_seq"] = 0
                # Add media state (track, position, duration, playing)
                try:
                    import lyrics_source
                    lyrics_state = lyrics_source.get_state()
                    if lyrics_state:
                        state["track"] = f"{lyrics_state.get('artist', '')} - {lyrics_state.get('title', '')}"
                        state["position"] = lyrics_state.get('position', 0) or 0
                        state["duration"] = 0  # Not available from lyrics_source
                        state["playing"] = not lyrics_state.get('loading', False)
                    else:
                        state["track"] = None
                        state["position"] = 0
                        state["duration"] = 0
                        state["playing"] = False
                except Exception:
                    state["track"] = None
                    state["position"] = 0
                    state["duration"] = 0
                    state["playing"] = False
                self._send(200,"application/json",json.dumps(state))
            elif p=="/api/scenes":
                try:
                    scenes=_load_scenes()
                    self._send(200,"application/json",json.dumps(scenes))
                except Exception as e:
                    print(f"[API] Error getting scenes: {e}")
                    self._send(200,"application/json",json.dumps(_load_scenes()))
            elif p=="/api/audio/data":
                # Get real-time audio data for visualization
                try:
                    import bpm_source
                    audio_data = {
                        "spectrum": bpm_source.get_viz_spectrum(),
                        "waveform": bpm_source.get_viz_waveform(),
                        "bands": {
                            "bass": bpm_source.get_viz_bands()[0],
                            "mid": bpm_source.get_viz_bands()[1],
                            "treble": bpm_source.get_viz_bands()[2]
                        },
                        "band_history": bpm_source.get_viz_band_history(),
                        "bpm": bpm_source.get_bpm() or 0,
                        "level": bpm_source.get_level(),
                        "beat_ts": bpm_source.get_beat_event()[0],
                        "beat_seq": bpm_source.get_beat_event()[1]
                    }
                    self._send(200,"application/json",json.dumps(audio_data))
                except Exception as e:
                    print(f"[API] Error getting audio data: {e}")
                    self._send(500,"application/json",json.dumps({"error": str(e)}))
            elif p=="/api/auto/state":
                # Get auto mode state with scoring info
                auto_data = _get_auto_state()
                self._send(200,"application/json",json.dumps(auto_data))
            elif p=="/api/lyrics/state":
                # Get current lyrics position and state
                try:
                    import lyrics_source
                    lyrics_state = lyrics_source.get_state()
                    if lyrics_state:
                        state = {
                            'artist': lyrics_state.get('artist', ''),
                            'title': lyrics_state.get('title', ''),
                            'current_idx': lyrics_state.get('current_idx', 0),
                            'total_lines': len(lyrics_state.get('lines', [])),
                            'synced': lyrics_state.get('has_sync', False),
                            'loading': lyrics_state.get('loading', False),
                            'lines': lyrics_state.get('lines', [])[:5]  # Preview first 5 lines
                        }
                        self._send(200,"application/json",json.dumps(state))
                    else:
                        self._send(200,"application/json",json.dumps({"active": False}))
                except Exception as e:
                    print(f"[API] Error getting lyrics state: {e}")
                    self._send(500,"application/json",json.dumps({"error": str(e)}))
            elif p=="/api/led/color":
                # Get current LED fan color
                try:
                    led_data = _sse_get_led_color()
                    self._send(200,"application/json",json.dumps(led_data))
                except Exception as e:
                    print(f"[API] Error getting LED color: {e}")
                    self._send(500,"application/json",json.dumps({"error": str(e)}))
            else:
                self._send(404,"text/plain","Not found")
        except Exception as e:
            print(f"[HTTP] GET error: {e}")
            self._send(500,"text/plain",f"Error: {e}")

    def do_POST(self):
        try:
            parts=[x for x in urllib.parse.urlparse(self.path).path.split("/") if x]
            if len(parts)>=2 and parts[0]=="api":
                k=parts[1]
                if k=="mode" and len(parts)>=3 and _switch:
                    _do_mode(parts[2])
                    self._send(200,"application/json",'{"ok":true}')
                    return
                if k=="hud" and len(parts)>=3 and _switch_hud:
                    _do_hud(parts[2])
                    self._send(200,"application/json",'{"ok":true}')
                    return
                if k=="led" and len(parts)==4:
                    _do_led(parts[2],parts[3])
                    self._send(200,"application/json",'{"ok":true}')
                    return
                if k=="scene" and len(parts)>=3:
                    result = _do_scene(parts[2])
                    self._send(200,"application/json",json.dumps(result))
                    return
                if k=="viz" and len(parts)>=3:
                    _do_viz(parts[2])
                    self._send(200,"application/json",'{"ok":true}')
                    return
                if k=="youtube-position":
                    _do_youtube_position(self)
                    self._send(200,"application/json",'{"ok":true}')
                    return
                if k=="lyrics-search":
                    _do_lyrics_search(self)
                    return
                if k=="lyrics-manual-search":
                    _do_lyrics_manual_search(self)
                    return
                if k=="lyrics-flag-temp":
                    _do_lyrics_flag_temp(self)
                    return
                if k=="lyrics-flag-perm":
                    _do_lyrics_flag_perm(self)
                    return
                if k=="cascade" and len(parts)>=4 and parts[2]=="force-viz":
                    v = parts[3].lower() in ("on","true","1")
                    try:
                        import render
                        render.set_force_viz(v)
                        self._send(200,"application/json",json.dumps({'ok':True,'force_viz':v}))
                    except Exception as e:
                        self._send(200,"application/json",json.dumps({'ok':False,'error':str(e)}))
                    return
                if k=="auto-enable":
                    result = _do_auto_enable()
                    self._send(200,"application/json",json.dumps(result))
                    return
                if k=="auto-disable":
                    result = _do_auto_disable()
                    self._send(200,"application/json",json.dumps(result))
                    return
                if k=="lyrics-offset" and len(parts)>=3:
                    result = _do_lyrics_offset(parts[2])
                    self._send(200,"application/json",json.dumps(result))
                    return
            self._send(400,"application/json",'{"ok":false}')
        except Exception as e:
            print(f"[HTTP] POST error: {e}")
            self._send(500, "application/json", json.dumps({"error": str(e)}))

    def do_OPTIONS(self):
        try:
            self.send_response(204)
            self.send_header("Access-Control-Allow-Origin","*")
            self.send_header("Access-Control-Allow-Methods","GET,POST,OPTIONS")
            self.send_header("Access-Control-Allow-Headers","Content-Type")
            self.end_headers()
        except Exception as e:
            print(f"[HTTP] OPTIONS error: {e}")

    def _handle_sse(self):
        """Handle Server-Sent Events connection for real-time updates."""
        try:
            # Send SSE headers
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("X-Accel-Buffering", "no")  # Disable nginx buffering
            self.end_headers()

            # Create a queue for this client
            client_queue = queue.Queue(maxsize=50)  # Prevent memory bloat
            _sse_register_client(client_queue)

            # Send initial connection message
            try:
                self.wfile.write(b"event: connected\ndata: {\"status\":\"ok\"}\n\n")
            except Exception:
                _sse_unregister_client(client_queue)
                return

            # Keep connection alive and push events
            last_keepalive = time.monotonic()
            while True:
                try:
                    # Wait for events with timeout (for keepalive)
                    message = client_queue.get(timeout=30.0)
                    try:
                        self.wfile.write(message)
                        self.wfile.flush()
                    except (ConnectionError, BrokenPipeError, OSError):
                        break
                except queue.Empty:
                    # Send keepalive every 30 seconds
                    now = time.monotonic()
                    if now - last_keepalive >= 25.0:
                        try:
                            self.wfile.write(b": keepalive\n\n")
                            self.wfile.flush()
                            last_keepalive = now
                        except (ConnectionError, BrokenPipeError, OSError):
                            break

        except Exception as e:
            print(f"[SSE] Connection error: {e}")
        finally:
            _sse_unregister_client(client_queue)


# ── API Functions ───────────────────────────────────────────────────────────────
def _do_mode(val):
    try:
        with _api_switch_lock:  # Protège le calcul delta + switch (ThreadingHTTPServer concurrent)
            state=_get_state() if _get_state else {}
            cur=state.get("mode",_MODES[0])
            if val=="next":
                _switch(+1)
                _notify_auto_override()
            elif val=="prev":
                _switch(-1)
                _notify_auto_override()
            elif val in _MODES:
                delta=_MODES.index(val)-(_MODES.index(cur) if cur in _MODES else 0)
                if delta:
                    _switch(delta)
                    _notify_auto_override()
    except Exception as e:
        print(f"[API] Mode error: {e}")


def _do_hud(val):
    try:
        with _api_switch_lock:  # Protège le calcul delta + switch (ThreadingHTTPServer concurrent)
            state=_get_state() if _get_state else {}
            cur=state.get("hud",_HUD_STYLES[0])
            if val=="next":
                _switch_hud(+1)
                _notify_auto_override()
            elif val=="prev":
                _switch_hud(-1)
                _notify_auto_override()
            elif val in _HUD_STYLES:
                delta=_HUD_STYLES.index(val)-(_HUD_STYLES.index(cur) if cur in _HUD_STYLES else 0)
                if delta:
                    # Run the HUD switch in a thread to avoid blocking HTTP response
                    # The _rates() calls in render_tiles/render_matrix can be slow
                    import threading
                    threading.Thread(
                        target=lambda: (_switch_hud(delta), _notify_auto_override()),
                        daemon=True
                    ).start()
    except Exception as e:
        print(f"[API] HUD error: {e}")


def _do_led(zone,val):
    try:
        if _set_led:
            _set_led(zone,val)
        else:
            try:
                import led_fans as lf
                _apply_led(lf,zone,val)
            except Exception as e:
                print(f"[LED API] {e}")
    except Exception as e:
        print(f"[API] LED error: {e}")


def _map_ui_slider_to_led_param(slider_id, value):
    """Map UI slider IDs to LED config parameters."""
    mapping = {
        'speed': 'case_speed',
        'bright': 'brightness',
        'sens': 'case_decay',
        'decay': 'case_decay',
        'smooth': 'case_decay',
        'gain': 'brightness'
    }
    param = mapping.get(slider_id, slider_id)
    _do_led(param, value)


def _do_led_fan_mode(mode):
    try:
        if mode in _FAN_MODES:
            _do_led('fan_mode', mode)
    except Exception as e:
        print(f"[API] LED fan mode error: {e}")


def _do_led_fan_color(color):
    try:
        if color in _COLORS:
            _do_led('fan_color', color)
    except Exception as e:
        print(f"[API] LED fan color error: {e}")


def _do_led_case_mode(mode):
    """Adapter for LED case mode switching."""
    try:
        if mode in _CASE_MODES:
            _do_led('case_mode', mode)
    except Exception as e:
        print(f"[API] LED case mode error: {e}")


def _do_led_case_color(color):
    """Adapter for LED case color setting."""
    if color in _COLORS:
        _do_led('case_color', color)


def _do_scene_save(scene_name):
    """Save current state as a named scene."""
    try:
        state = _get_state() if _get_state else {}
        try:
            import led_fans as lf
            led_cfg = lf.get_cfg()
        except Exception:
            led_cfg = {}

        scenes = _load_scenes()
        scenes[scene_name] = {
            'mode': state.get('mode'),
            'hud': state.get('hud'),
            'led': led_cfg
        }
        _save_scenes(scenes)
        return {'ok': True}
    except Exception as e:
        return {'ok': False, 'error': str(e)}


def _do_scene(scene_name):
    """Handle scene API requests. Returns {'ok': True, 'scene_id': id} for save, {'ok': True} for load."""
    try:
        if scene_name == "save":
            # Save current state as a new scene
            state = _get_state() if _get_state else {}
            try:
                import led_fans as lf
                led_cfg = lf.get_cfg()
            except Exception:
                led_cfg = {}

            scene_id = f"user_{len(_get_scenes_list()) + 1}"
            scenes = _load_scenes()
            scenes[scene_id] = {
                "mode": state.get("mode"),
                "hud": state.get("hud"),
                "led": led_cfg
            }
            _save_scenes(scenes)
            print(f"[SCENE] Saved current state as {scene_id}")
            return {'ok': True, 'scene_id': scene_id}
        else:
            # Load and apply scene
            if _apply_scene(scene_name):
                print(f"[SCENE] Applied scene: {scene_name}")
                return {'ok': True}
            else:
                print(f"[SCENE] Scene not found: {scene_name}")
                return {'ok': False, 'error': 'Scene not found'}
    except Exception as e:
        print(f"[SCENE] Error: {e}")
        return {'ok': False, 'error': str(e)}


def _do_viz(mode):
    """Handle visualization mode API requests (spectrum/waveform)."""
    try:
        if mode in _VIZ_MODES:
            import render_audio_viz
            render_audio_viz.set_viz_mode(mode)
            print(f"[VIZ] Mode → {mode}")
        else:
            print(f"[VIZ] Unknown mode: {mode}")
    except Exception as e:
        print(f"[VIZ] Error: {e}")


def _do_youtube_position(self):
    """Receive YouTube timecode from browser extension."""
    try:
        content_length = int(self.headers.get('Content-Length', 0))
        if content_length > 0:
            body = self.rfile.read(content_length)
            data = json.loads(body.decode())
            # Forward to lyrics module
            try:
                import lyrics_source
                import time
                # Throttle YT output - print only every 5 seconds or on video change
                current_time = time.time()
                video_id = data.get('videoId', '')
                position = data.get('position', 0)

                # Static variable to track last print time and video
                if not hasattr(_do_youtube_position, '_last_yt_print'):
                    _do_youtube_position._last_yt_print = 0
                    _do_youtube_position._last_video_id = ''

                should_print = (
                    video_id != _do_youtube_position._last_video_id or  # Video changed
                    current_time - _do_youtube_position._last_yt_print > 5    # 5 seconds passed
                )

                if should_print:
                    print(f"[YT] {data.get('artist')} — {data.get('title')} @ {position:.1f}s")
                    _do_youtube_position._last_yt_print = current_time
                    _do_youtube_position._last_video_id = video_id

                lyrics_source.set_youtube_position(
                    video_id,
                    data.get('artist', ''),
                    data.get('title', ''),
                    position
                )
            except ImportError:
                pass  # lyrics_source not available
    except Exception as e:
        print(f"[YT API] Error: {e}")


def _do_lyrics_offset(offset):
    """Adjust lyrics sync offset for current song."""
    try:
        import lyrics_source
        # Get current song info from lyrics_source
        lyrics_state = lyrics_source.get_state()
        if not lyrics_state:
            return {'ok': False, 'error': 'No active track'}
        artist = lyrics_state.get('artist')
        title = lyrics_state.get('title')
        if artist and title:
            lyrics_source.set_song_offset(artist, title, float(offset))
            return {'ok': True}
        return {'ok': False, 'error': 'No active track'}
    except Exception as e:
        return {'ok': False, 'error': str(e)}


def _do_lyrics_search(self):
    """Search for lyrics with given artist and title."""
    try:
        content_length = int(self.headers.get('Content-Length', 0))
        if content_length > 0:
            body = self.rfile.read(content_length)
            data = json.loads(body.decode())

            artist = data.get('artist', '').strip()
            title = data.get('title', '').strip()

            if not artist or not title:
                self._send(400, "application/json", '{"ok":false,"error":"Missing artist or title"}')
                return

            print(f"[LYRICS API] Search request: {artist} - {title}")

            # Forward to lyrics module
            try:
                import lyrics_source
                lyrics_source._fetch_lyrics(artist, title)
                self._send(200, "application/json", '{"ok":true}')
            except ImportError:
                self._send(500, "application/json", '{"ok":false,"error":"lyrics_source not available"}')
            except Exception as e:
                self._send(500, "application/json", json.dumps({"ok": False, "error": str(e)}))
        else:
            self._send(400, "application/json", '{"ok":false,"error":"Missing request body"}')
    except Exception as e:
        print(f"[LYRICS API] Error: {e}")
        self._send(500, "application/json", json.dumps({"ok": False, "error": str(e)}))


def _do_lyrics_manual_search(self):
    """Recherche manuelle de lyrics avec toutes les sources."""
    try:
        content_length = int(self.headers.get('Content-Length', 0))
        if content_length > 0:
            body = self.rfile.read(content_length)
            data = json.loads(body.decode())

            artist = data.get('artist', '').strip()
            title = data.get('title', '').strip()
            video_id = data.get('video_id', None)

            if not artist or not title:
                self._send(400, "application/json", '{"ok":false,"error":"Missing artist or title"}')
                return

            print(f"[LYRICS API] Manual search: {artist} - {title}")

            try:
                from lyrics_manager import get_manager
                manager = get_manager()
                result = manager.search_manual(artist, title, video_id)

                if "error" in result:
                    self._send(404, "application/json", json.dumps(result))
                else:
                    # Force le reload des lyrics dans lyrics_source
                    import lyrics_source
                    lyrics_source.resync()  # Reset pour forcer reload
                    self._send(200, "application/json", json.dumps(result))
            except ImportError:
                self._send(500, "application/json", '{"ok":false,"error":"lyrics_manager not available"}')
            except Exception as e:
                self._send(500, "application/json", json.dumps({"ok": False, "error": str(e)}))
        else:
            self._send(400, "application/json", '{"ok":false,"error":"Missing request body"}')
    except Exception as e:
        print(f"[LYRICS API] Manual search error: {e}")
        self._send(500, "application/json", json.dumps({"ok": False, "error": str(e)}))


def _do_lyrics_flag_temp(self):
    """Marque temporairement (session) - évite cette source maintenant."""
    try:
        content_length = int(self.headers.get('Content-Length', 0))
        if content_length > 0:
            body = self.rfile.read(content_length)
            data = json.loads(body.decode())

            artist = data.get('artist', '').strip()
            title = data.get('title', '').strip()
            source = data.get('source', '').strip()

            if not artist or not title:
                self._send(400, "application/json", '{"ok":false,"error":"Missing artist or title"}')
                return

            try:
                from lyrics_manager import get_manager
                manager = get_manager()
                manager.flag_temp(artist, title, source)
                self._send(200, "application/json", '{"ok":true,"temp":true}')
            except ImportError:
                self._send(500, "application/json", '{"ok":false,"error":"lyrics_manager not available"}')
            except Exception as e:
                self._send(500, "application/json", json.dumps({"ok": False, "error": str(e)}))
        else:
            self._send(400, "application/json", '{"ok":false,"error":"Missing request body"}')
    except Exception as e:
        print(f"[LYRICS API] Flag temp error: {e}")
        self._send(500, "application/json", json.dumps({"ok": False, "error": str(e)}))


def _do_lyrics_flag_perm(self):
    """Marque de manière permanente - sauvegarde dans JSON."""
    try:
        content_length = int(self.headers.get('Content-Length', 0))
        if content_length > 0:
            body = self.rfile.read(content_length)
            data = json.loads(body.decode())

            artist = data.get('artist', '').strip()
            title = data.get('title', '').strip()
            source = data.get('source', '').strip()
            reason = data.get('reason', 'user_blacklisted')

            if not artist or not title:
                self._send(400, "application/json", '{"ok":false,"error":"Missing artist or title"}')
                return

            try:
                from lyrics_manager import get_manager
                manager = get_manager()
                manager.flag_permanent(artist, title, source, reason)
                self._send(200, "application/json", '{"ok":true,"permanent":true}')
            except ImportError:
                self._send(500, "application/json", '{"ok":false,"error":"lyrics_manager not available"}')
            except Exception as e:
                self._send(500, "application/json", json.dumps({"ok": False, "error": str(e)}))
        else:
            self._send(400, "application/json", '{"ok":false,"error":"Missing request body"}')
    except Exception as e:
        print(f"[LYRICS API] Flag perm error: {e}")
        self._send(500, "application/json", json.dumps({"ok": False, "error": str(e)}))


def _handle_track_info(artist, title, duration=None, video_id=None):
    """Process track info from browser extension."""
    try:
        import lyrics_source
        if video_id:
            lyrics_source.set_youtube_position(video_id, artist, title, 0)
        return {'ok': True}
    except Exception as e:
        return {'ok': False, 'error': str(e)}


def _normalize_led_zone(zone):
    """Normalize UI zone names to backend zone names.

    Maps user-facing zone names to the internal configuration keys.
    For example, 'case' -> 'case_mode', 'fans' -> 'fan_mode'.
    """
    zone_map = {
        'case': 'case_mode',
        'fans': 'fan_mode',
        'case_color': 'case_color',
        'fans_color': 'fan_color',
        'case_color2': 'case_color2',
        'fans_color2': 'fan_color2',
    }
    return zone_map.get(zone, zone)


def _apply_led(lf,zone,val):
    try:
        # Normalize zone name first
        zone = _normalize_led_zone(zone)

        if zone=="case_mode" and val in _CASE_MODES:
            lf.set_cfg(case_mode=val)
        elif zone=="fan_mode" and val in _FAN_MODES:
            lf.set_cfg(fan_mode=val)
        elif zone=="case_color" and val in _COLORS:
            lf.set_cfg(case_color=val)
        elif zone=="case_color2" and val in _COLORS:
            lf.set_cfg(case_color2=val)
        elif zone=="fan_color" and val in _COLORS:
            lf.set_cfg(fan_color=val)
        elif zone=="fan_color2" and val in _COLORS:
            lf.set_cfg(fan_color2=val)
        elif zone in _FLOAT_PARAMS:
            try:
                lf.set_cfg(**{zone:float(val)})
            except ValueError:
                pass
    except Exception as e:
        print(f"[LED] Apply error: {e}")


def _status_json():
    s=_get_state() if _get_state else {}
    if _set_led:
        try:
            import led_fans as lf
            cfg=lf.get_cfg()
            s["led"]=cfg
        except Exception:
            pass
    return s


def _notify_auto_override():
    """Notifie auto_mode qu'un override manuel a eu lieu."""
    if _auto_override:
        try:
            state = _get_state() if _get_state else {}
            mode = state.get("mode")
            hud = state.get("hud")
            _auto_override(mode=mode, hud=hud)
        except Exception as e:
            print(f"[AUTO OVERRIDE] Erreur: {e}")


def _do_auto_enable():
    """Enable intelligent auto mode."""
    try:
        # Use callback to set auto mode flag to True
        if _set_auto_enabled:
            _set_auto_enabled(True)
        print("[AUTO] Mode auto activé")
        return {'ok': True}
    except Exception as e:
        print(f"[AUTO] Erreur activation auto mode: {e}")
        return {'ok': False, 'error': str(e)}


def _do_auto_disable():
    """Disable intelligent auto mode."""
    try:
        # Use callback to set auto mode flag to False
        if _set_auto_enabled:
            _set_auto_enabled(False)
        print("[AUTO] Mode auto désactivé")
        return {'ok': True}
    except Exception as e:
        print(f"[AUTO] Erreur désactivation auto mode: {e}")
        return {'ok': False, 'error': str(e)}


def start(switch_fn=None,switch_hud_fn=None,get_state_fn=None,set_led_fn=None,auto_override_fn=None,set_auto_enabled_fn=None,**_extra):
    global _switch,_switch_hud,_get_state,_set_led,_auto_override,_set_auto_enabled
    _switch=switch_fn
    _switch_hud=switch_hud_fn
    _get_state=get_state_fn
    _set_led=set_led_fn
    _auto_override=auto_override_fn
    _set_auto_enabled=set_auto_enabled_fn

    try:
        # Start HTTPServer
        print(f"[Dashboard] Starting HTTPServer on port {PORT}...")
        http_server = ThreadingHTTPServer(("127.0.0.1", PORT), _Handler)
        threading.Thread(target=http_server.serve_forever, daemon=True, name='http_server').start()
        print(f"[Dashboard] http://localhost:{PORT} (HTTPServer mode)")

        # Start SSE broadcaster for real-time state updates
        _sse_start()
        print(f"[SSE] Real-time events available at http://localhost:{PORT}/api/events")

    except Exception as e:
        print(f"[Dashboard] Error starting: {e}")
        import traceback
        traceback.print_exc()
