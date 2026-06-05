"""
control_server.py
Port : 7420  -> http://localhost:7420
"""
from http.server import BaseHTTPRequestHandler, HTTPServer
import json, threading, urllib.parse

PORT = 7420
_switch = _switch_hud = _get_state = _set_led = None

_COLORS      = ["violet","cyan","blue","teal","green","yellow","orange","red","pink","white"]
_CASE_MODES  = ['off', 'static', 'wave', 'beat', 'beat_pulse', 'gradient', 'rainbow', 'comet', 'breathe', 'spin', 'dual']
_FAN_MODES   = ['off', 'static', 'wave', 'beat', 'beat_pulse', 'gradient', 'rainbow', 'comet', 'breathe', 'spin', 'dual']
_FLOAT_PARAMS= {"case_speed","fan_speed","case_length","brightness","case_decay"}
_MODES       = ["ascii_vhs","video","image","audio","blank"]
_HUD_STYLES  = ["full","terminal","clock","split","tiles","matrix","lyrics","audio_viz","blank"]

_HTML = '<!DOCTYPE html>\n<html lang="fr">\n<head>\n<meta charset="UTF-8">\n<meta name="viewport" content="width=device-width,initial-scale=1">\n<title>Box Screen</title>\n<style>\n  :root {\n    --bg:#060410;--panel:#0d0a1e;--border:#2a1f50;--accent:#c840ff;\n    --green:#00ff9d;--blue:#38bfff;--orange:#ff5500;--red:#ff0000;\n    --pink:#ff1464;--yellow:#ffc800;--teal:#00d482;--text:#d4c8f0;--muted:#6b5e8a;\n  }\n  *{box-sizing:border-box;margin:0;padding:0}\n  body{background:var(--bg);color:var(--text);font-family:\'Consolas\',\'Courier New\',monospace;min-height:100vh;padding:24px}\n  h1{font-size:1.1rem;letter-spacing:.25em;color:var(--accent);text-transform:uppercase;margin-bottom:4px}\n  .subtitle{font-size:.7rem;color:var(--muted);letter-spacing:.15em;margin-bottom:28px}\n  .grid{display:grid;grid-template-columns:1fr 1fr;gap:16px;max-width:860px}\n  .card{background:var(--panel);border:1px solid var(--border);border-radius:8px;padding:18px}\n  .card.full{grid-column:1/-1}\n  .card-title{font-size:.65rem;letter-spacing:.2em;color:var(--muted);text-transform:uppercase;margin-bottom:14px}\n  .btn-group{display:flex;flex-wrap:wrap;gap:6px}\n  button{background:transparent;border:1px solid var(--border);color:var(--muted);font-family:inherit;font-size:.75rem;letter-spacing:.1em;padding:6px 13px;border-radius:4px;cursor:pointer;text-transform:uppercase;transition:all .15s}\n  button:hover{border-color:var(--accent);color:var(--text)}\n  button.active{background:var(--accent);border-color:var(--accent);color:#fff;box-shadow:0 0 12px rgba(200,64,255,.35)}\n  button.mode-btn.active{background:#7c00e0;border-color:#c840ff}\n  button.hud-btn.active{background:#005e8a;border-color:#38bfff;color:#fff}\n  button.led-btn.active,button.param-btn.active{background:#1a1030;border-color:var(--accent);color:var(--accent)}\n  .led-row{display:flex;align-items:center;gap:12px;margin-bottom:10px;flex-wrap:wrap}\n  .row-label{font-size:.6rem;letter-spacing:.12em;color:var(--muted);text-transform:uppercase;min-width:72px}\n  .meters{display:flex;gap:24px;align-items:flex-end}\n  .meter-block{flex:1}\n  .meter-label{font-size:.6rem;letter-spacing:.15em;color:var(--muted);text-transform:uppercase;margin-bottom:6px}\n  .meter-value{font-size:1.6rem;font-weight:bold;line-height:1;margin-bottom:8px}\n  .bpm-val{color:var(--green)}.lvl-val{color:var(--blue)}\n  .bar-bg{height:6px;background:#1a1030;border-radius:3px;overflow:hidden}\n  .bar-fill{height:100%;border-radius:3px;transition:width .1s linear}\n  .bar-bpm{background:var(--green);box-shadow:0 0 6px var(--green)}\n  .bar-lvl{background:var(--blue);box-shadow:0 0 6px var(--blue)}\n  .beat-dot{display:inline-block;width:10px;height:10px;border-radius:50%;background:var(--orange);margin-left:10px;vertical-align:middle;opacity:0;transition:opacity .05s}\n  .beat-dot.flash{opacity:1}\n  .status-bar{margin-top:24px;max-width:860px;font-size:.6rem;color:var(--muted);letter-spacing:.1em;display:flex;justify-content:space-between}\n  #conn{color:var(--green)}#conn.off{color:var(--red)}\n</style>\n</head>\n<body>\n<h1>Box Screen</h1>\n<div class="subtitle">Trofeo Vision 9.16 — 1920×462</div>\n<div class="grid">\n  <div class="card">\n    <div class="card-title">Mode</div>\n    <div class="btn-group" id="mode-btns">\n      <button class="mode-btn" data-mode="ascii_vhs">ASCII VHS</button>\n      <button class="mode-btn" data-mode="video">Vidéo</button>\n      <button class="mode-btn" data-mode="image">Image</button>\n      <button class="mode-btn" data-mode="audio">Audio</button>\n      <button class="mode-btn" data-mode="blank">Blank</button>\n    </div>\n  </div>\n  <div class="card">\n    <div class="card-title">HUD Style</div>\n    <div class="btn-group" id="hud-btns">\n      <button class="hud-btn" data-hud="full">Full</button>\n      <button class="hud-btn" data-hud="terminal">Terminal</button>\n      <button class="hud-btn" data-hud="clock">Clock</button>\n      <button class="hud-btn" data-hud="split">Split</button>\n      <button class="hud-btn" data-hud="tiles">Tiles</button>\n      <button class="hud-btn" data-hud="matrix">Matrix</button>\n      <button class="hud-btn" data-hud="lyrics">Lyrics</button>\n      <button class="hud-btn" data-hud="audio_viz">Audio Viz</button>\n      <button class="hud-btn" data-hud="blank">Blank</button>\n    </div>\n  </div>\n  <div class="card full">\n    <div class="card-title">LEDs — Case</div>\n    <div class="led-row"><span class="row-label">Mode</span>\n      <div class="btn-group" id="case-mode-btns">\n        <button class="led-btn" data-path="/api/led/case_mode/off">OFF</button>\n        <button class="led-btn" data-path="/api/led/case_mode/static">STATIC</button>\n        <button class="led-btn" data-path="/api/led/case_mode/wave">WAVE</button>\n        <button class="led-btn" data-path="/api/led/case_mode/beat">BEAT</button>\n        <button class="led-btn" data-path="/api/led/case_mode/beat_pulse">BEAT PULSE</button>\n        <button class="led-btn" data-path="/api/led/case_mode/gradient">GRADIENT</button>\n        <button class="led-btn" data-path="/api/led/case_mode/rainbow">RAINBOW</button>\n        <button class="led-btn" data-path="/api/led/case_mode/strobe">STROBE</button>\n        <button class="led-btn" data-path="/api/led/case_mode/comet">COMET</button>\n      </div></div>\n    <div class="led-row"><span class="row-label">Couleur 1</span>\n      <div class="btn-group" id="case-color-btns"><button class="led-btn color-btn" data-zone="case_color" data-color="violet" style="background:#7c00b0;border-color:#c840ff">V</button>\n        <button class="led-btn color-btn" data-zone="case_color" data-color="cyan" style="background:#005577;border-color:#1ec8ff">C</button>\n        <button class="led-btn color-btn" data-zone="case_color" data-color="blue" style="background:#001a80;border-color:#0050ff">B</button>\n        <button class="led-btn color-btn" data-zone="case_color" data-color="teal" style="background:#004040;border-color:#00d296">T</button>\n        <button class="led-btn color-btn" data-zone="case_color" data-color="green" style="background:#005530;border-color:#00d482">G</button>\n        <button class="led-btn color-btn" data-zone="case_color" data-color="yellow" style="background:#604800;border-color:#ffc800">Y</button>\n        <button class="led-btn color-btn" data-zone="case_color" data-color="orange" style="background:#7a2200;border-color:#ff5500">O</button>\n        <button class="led-btn color-btn" data-zone="case_color" data-color="red" style="background:#700000;border-color:#ff0000">R</button>\n        <button class="led-btn color-btn" data-zone="case_color" data-color="pink" style="background:#7a0040;border-color:#ff1464">P</button>\n        <button class="led-btn color-btn" data-zone="case_color" data-color="white" style="background:#404040;border-color:#dddddd">W</button>\n        </div></div>\n    <div class="led-row"><span class="row-label">Couleur 2</span>\n      <div class="btn-group" id="case-color2-btns"><button class="led-btn color-btn" data-zone="case_color2" data-color="violet" style="background:#7c00b0;border-color:#c840ff">V</button>\n        <button class="led-btn color-btn" data-zone="case_color2" data-color="cyan" style="background:#005577;border-color:#1ec8ff">C</button>\n        <button class="led-btn color-btn" data-zone="case_color2" data-color="blue" style="background:#001a80;border-color:#0050ff">B</button>\n        <button class="led-btn color-btn" data-zone="case_color2" data-color="teal" style="background:#004040;border-color:#00d296">T</button>\n        <button class="led-btn color-btn" data-zone="case_color2" data-color="green" style="background:#005530;border-color:#00d482">G</button>\n        <button class="led-btn color-btn" data-zone="case_color2" data-color="yellow" style="background:#604800;border-color:#ffc800">Y</button>\n        <button class="led-btn color-btn" data-zone="case_color2" data-color="orange" style="background:#7a2200;border-color:#ff5500">O</button>\n        <button class="led-btn color-btn" data-zone="case_color2" data-color="red" style="background:#700000;border-color:#ff0000">R</button>\n        <button class="led-btn color-btn" data-zone="case_color2" data-color="pink" style="background:#7a0040;border-color:#ff1464">P</button>\n        <button class="led-btn color-btn" data-zone="case_color2" data-color="white" style="background:#404040;border-color:#dddddd">W</button>\n        </div></div>\n    <div class="led-row"><span class="row-label">Vitesse</span>\n      <div class="btn-group" id="case-speed-btns">\n        <button class="led-btn param-btn" data-path="/api/led/case_speed/0.25">x1/4</button>\n        <button class="led-btn param-btn" data-path="/api/led/case_speed/0.5">x1/2</button>\n        <button class="led-btn param-btn" data-path="/api/led/case_speed/1.0">x1</button>\n        <button class="led-btn param-btn" data-path="/api/led/case_speed/2.0">x2</button>\n        <button class="led-btn param-btn" data-path="/api/led/case_speed/4.0">x4</button>\n      </div></div>\n    <div class="led-row"><span class="row-label">Longueur</span>\n      <div class="btn-group" id="case-length-btns">\n        <button class="led-btn param-btn" data-path="/api/led/case_length/0.15">15%</button>\n        <button class="led-btn param-btn" data-path="/api/led/case_length/0.35">35%</button>\n        <button class="led-btn param-btn" data-path="/api/led/case_length/0.55">55%</button>\n        <button class="led-btn param-btn" data-path="/api/led/case_length/0.75">75%</button>\n        <button class="led-btn param-btn" data-path="/api/led/case_length/1.0">FULL</button>\n      </div></div>\n    <div class="led-row"><span class="row-label">Luminosité</span>\n      <div class="btn-group" id="case-brightness-btns">\n        <button class="led-btn param-btn" data-path="/api/led/brightness/0.2">DIM</button>\n        <button class="led-btn param-btn" data-path="/api/led/brightness/0.5">MED</button>\n        <button class="led-btn param-btn" data-path="/api/led/brightness/0.8">HIGH</button>\n        <button class="led-btn param-btn" data-path="/api/led/brightness/1.0">MAX</button>\n      </div></div>\n    <div class="led-row"><span class="row-label">Decay beat</span>\n      <div class="btn-group" id="case-decay-btns">\n        <button class="led-btn param-btn" data-path="/api/led/case_decay/0.1">SNAP</button>\n        <button class="led-btn param-btn" data-path="/api/led/case_decay/0.3">FAST</button>\n        <button class="led-btn param-btn" data-path="/api/led/case_decay/0.6">MED</button>\n        <button class="led-btn param-btn" data-path="/api/led/case_decay/1.2">SLOW</button>\n        <button class="led-btn param-btn" data-path="/api/led/case_decay/2.5">LONG</button>\n      </div></div>\n  </div>\n  <div class="card full">\n    <div class="card-title">LEDs — Fans</div>\n    <div class="led-row"><span class="row-label">Mode</span>\n      <div class="btn-group" id="fan-mode-btns">\n        <button class="led-btn" data-path="/api/led/fan_mode/off">OFF</button>\n        <button class="led-btn" data-path="/api/led/fan_mode/static">STATIC</button>\n        <button class="led-btn" data-path="/api/led/fan_mode/spin_bpm">SPIN BPM</button>\n        <button class="led-btn" data-path="/api/led/fan_mode/spin_fixed">SPIN</button>\n        <button class="led-btn" data-path="/api/led/fan_mode/dual">DUAL</button>\n        <button class="led-btn" data-path="/api/led/fan_mode/rainbow">RAINBOW</button>\n        <button class="led-btn" data-path="/api/led/fan_mode/breathe">BREATHE</button>\n        <button class="led-btn" data-path="/api/led/fan_mode/beat_pulse">BEAT PULSE</button>\n      </div></div>\n    <div class="led-row"><span class="row-label">Couleur 1</span>\n      <div class="btn-group" id="fan-color-btns"><button class="led-btn color-btn" data-zone="fan_color" data-color="violet" style="background:#7c00b0;border-color:#c840ff">V</button>\n        <button class="led-btn color-btn" data-zone="fan_color" data-color="cyan" style="background:#005577;border-color:#1ec8ff">C</button>\n        <button class="led-btn color-btn" data-zone="fan_color" data-color="blue" style="background:#001a80;border-color:#0050ff">B</button>\n        <button class="led-btn color-btn" data-zone="fan_color" data-color="teal" style="background:#004040;border-color:#00d296">T</button>\n        <button class="led-btn color-btn" data-zone="fan_color" data-color="green" style="background:#005530;border-color:#00d482">G</button>\n        <button class="led-btn color-btn" data-zone="fan_color" data-color="yellow" style="background:#604800;border-color:#ffc800">Y</button>\n        <button class="led-btn color-btn" data-zone="fan_color" data-color="orange" style="background:#7a2200;border-color:#ff5500">O</button>\n        <button class="led-btn color-btn" data-zone="fan_color" data-color="red" style="background:#700000;border-color:#ff0000">R</button>\n        <button class="led-btn color-btn" data-zone="fan_color" data-color="pink" style="background:#7a0040;border-color:#ff1464">P</button>\n        <button class="led-btn color-btn" data-zone="fan_color" data-color="white" style="background:#404040;border-color:#dddddd">W</button>\n        </div></div>\n    <div class="led-row"><span class="row-label">Couleur 2</span>\n      <div class="btn-group" id="fan-color2-btns"><button class="led-btn color-btn" data-zone="fan_color2" data-color="violet" style="background:#7c00b0;border-color:#c840ff">V</button>\n        <button class="led-btn color-btn" data-zone="fan_color2" data-color="cyan" style="background:#005577;border-color:#1ec8ff">C</button>\n        <button class="led-btn color-btn" data-zone="fan_color2" data-color="blue" style="background:#001a80;border-color:#0050ff">B</button>\n        <button class="led-btn color-btn" data-zone="fan_color2" data-color="teal" style="background:#004040;border-color:#00d296">T</button>\n        <button class="led-btn color-btn" data-zone="fan_color2" data-color="green" style="background:#005530;border-color:#00d482">G</button>\n        <button class="led-btn color-btn" data-zone="fan_color2" data-color="yellow" style="background:#604800;border-color:#ffc800">Y</button>\n        <button class="led-btn color-btn" data-zone="fan_color2" data-color="orange" style="background:#7a2200;border-color:#ff5500">O</button>\n        <button class="led-btn color-btn" data-zone="fan_color2" data-color="red" style="background:#700000;border-color:#ff0000">R</button>\n        <button class="led-btn color-btn" data-zone="fan_color2" data-color="pink" style="background:#7a0040;border-color:#ff1464">P</button>\n        <button class="led-btn color-btn" data-zone="fan_color2" data-color="white" style="background:#404040;border-color:#dddddd">W</button>\n        </div></div>\n    <div class="led-row"><span class="row-label">Vitesse</span>\n      <div class="btn-group" id="fan-speed-btns">\n        <button class="led-btn param-btn" data-path="/api/led/fan_speed/0.25">x1/4</button>\n        <button class="led-btn param-btn" data-path="/api/led/fan_speed/0.5">x1/2</button>\n        <button class="led-btn param-btn" data-path="/api/led/fan_speed/1.0">x1</button>\n        <button class="led-btn param-btn" data-path="/api/led/fan_speed/2.0">x2</button>\n        <button class="led-btn param-btn" data-path="/api/led/fan_speed/4.0">x4</button>\n      </div></div>\n  </div>\n  <div class="card full">\n    <div class="card-title">Audio <span class="beat-dot" id="beat-dot"></span></div>\n    <div class="meters">\n      <div class="meter-block">\n        <div class="meter-label">BPM</div>\n        <div class="meter-value bpm-val" id="bpm-val">-</div>\n        <div class="bar-bg"><div class="bar-fill bar-bpm" id="bpm-bar" style="width:0%"></div></div>\n      </div>\n      <div class="meter-block">\n        <div class="meter-label">Level (bass)</div>\n        <div class="meter-value lvl-val" id="lvl-val">-</div>\n        <div class="bar-bg"><div class="bar-fill bar-lvl" id="lvl-bar" style="width:0%"></div></div>\n      </div>\n    </div>\n  </div>\n</div>\n<div class="status-bar"><span id="conn">● connecté</span><span id="ts">-</span></div>\n<script>\nlet _lbs=-1;\nasync function post(u){try{await fetch(u,{method:\'POST\'})}catch(e){}}\ndocument.querySelectorAll(\'.mode-btn\').forEach(b=>b.addEventListener(\'click\',()=>post(\'/api/mode/\'+b.dataset.mode)));\ndocument.querySelectorAll(\'.hud-btn\').forEach(b=>b.addEventListener(\'click\',()=>post(\'/api/hud/\'+b.dataset.hud)));\ndocument.querySelectorAll(\'.led-btn\').forEach(b=>b.addEventListener(\'click\',()=>{\n  if(b.classList.contains(\'color-btn\')){\n    const z=b.dataset.zone,c=b.dataset.color,on=parseFloat(b.style.opacity||\'1\')>0.6;\n    if(on){post(\'/api/led/\'+(z.startsWith(\'case\')?\'case_mode\':\'fan_mode\')+\'/off\');}\n    else{post(\'/api/led/\'+z+\'/\'+c);}\n  }else{post(b.dataset.path);}\n}));\nasync function poll(){\n  try{\n    const d=await(await fetch(\'/api/status\')).json();\n    document.querySelectorAll(\'.mode-btn\').forEach(b=>b.classList.toggle(\'active\',b.dataset.mode===d.mode));\n    document.querySelectorAll(\'.hud-btn\').forEach(b=>b.classList.toggle(\'active\',b.dataset.hud===d.hud));\n    const bpm=d.bpm;\n    document.getElementById(\'bpm-val\').textContent=bpm?Math.round(bpm):\'-\';\n    document.getElementById(\'bpm-bar\').style.width=bpm?Math.min(100,(bpm-60)/100*100)+\'%\':\'0%\';\n    const lvl=d.level||0;\n    document.getElementById(\'lvl-val\').textContent=(lvl*100).toFixed(0)+\'%\';\n    document.getElementById(\'lvl-bar\').style.width=(lvl*100)+\'%\';\n    if(d.led){\n      const led=d.led;\n      document.querySelectorAll(\'#case-mode-btns .led-btn\').forEach(b=>b.classList.toggle(\'active\',b.dataset.path.split(\'/\').pop()===led.case_mode));\n      document.querySelectorAll(\'#fan-mode-btns .led-btn\').forEach(b=>b.classList.toggle(\'active\',b.dataset.path.split(\'/\').pop()===led.fan_mode));\n      document.querySelectorAll(\'#case-color-btns .led-btn\').forEach(b=>b.style.opacity=b.dataset.color===led.case_color?\'1\':\'0.35\');\n      document.querySelectorAll(\'#case-color2-btns .led-btn\').forEach(b=>b.style.opacity=b.dataset.color===led.case_color2?\'1\':\'0.35\');\n      document.querySelectorAll(\'#fan-color-btns .led-btn\').forEach(b=>b.style.opacity=b.dataset.color===led.fan_color?\'1\':\'0.35\');\n      document.querySelectorAll(\'#fan-color2-btns .led-btn\').forEach(b=>b.style.opacity=b.dataset.color===led.fan_color2?\'1\':\'0.35\');\n      const sp=(g,k)=>document.querySelectorAll(\'#\'+g+\' .led-btn\').forEach(b=>b.classList.toggle(\'active\',parseFloat(b.dataset.path.split(\'/\').pop())===led[k]));\n      sp(\'case-speed-btns\',\'case_speed\');sp(\'fan-speed-btns\',\'fan_speed\');\n      sp(\'case-length-btns\',\'case_length\');sp(\'case-brightness-btns\',\'brightness\');\n      sp(\'case-decay-btns\',\'case_decay\');\n    }\n    if(d.beat_seq!==_lbs){_lbs=d.beat_seq;const dot=document.getElementById(\'beat-dot\');dot.classList.add(\'flash\');setTimeout(()=>dot.classList.remove(\'flash\'),120);}\n    document.getElementById(\'conn\').className=\'\';\n    document.getElementById(\'conn\').textContent=\'● connecté\';\n    document.getElementById(\'ts\').textContent=new Date().toLocaleTimeString(\'fr-FR\');\n  }catch(e){document.getElementById(\'conn\').className=\'off\';document.getElementById(\'conn\').textContent=\'● déconnecté\';}\n}\npoll();setInterval(poll,300);\n</script>\n</body></html>'

class _Handler(BaseHTTPRequestHandler):
    def log_message(self,fmt,*a): pass
    def _send(self,code,ct,body):
        if isinstance(body,str): body=body.encode()
        self.send_response(code)
        self.send_header("Content-Type",ct)
        self.send_header("Content-Length",len(body))
        self.send_header("Access-Control-Allow-Origin","*")
        self.end_headers()
        self.wfile.write(body)
    def do_GET(self):
        p=urllib.parse.urlparse(self.path).path
        if p in ("/","/index.html"):
            self._send(200,"text/html; charset=utf-8",_HTML)
        elif p=="/api/status":
            state=_get_state() if _get_state else {}
            try:
                import led_fans as lf; state["led"]=lf.get_cfg()
            except: state["led"]={}
            self._send(200,"application/json",json.dumps(state))
        else: self._send(404,"text/plain","Not found")
    def do_POST(self):
        parts=[x for x in urllib.parse.urlparse(self.path).path.split("/") if x]
        if len(parts)>=2 and parts[0]=="api":
            k=parts[1]
            if k=="mode" and len(parts)>=3 and _switch: _do_mode(parts[2]); self._send(200,"application/json",'{"ok":true}'); return
            if k=="hud" and len(parts)>=3 and _switch_hud: _do_hud(parts[2]); self._send(200,"application/json",'{"ok":true}'); return
            if k=="led" and len(parts)==4: _do_led(parts[2],parts[3]); self._send(200,"application/json",'{"ok":true}'); return
            if k=="youtube-position": _do_youtube_position(self); self._send(200,"application/json",'{"ok":true}'); return
        self._send(400,"application/json",'{"ok":false}')
    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin","*")
        self.send_header("Access-Control-Allow-Methods","GET,POST,OPTIONS")
        self.send_header("Access-Control-Allow-Headers","Content-Type")
        self.end_headers()

def _do_mode(val):
    state=_get_state() if _get_state else {}
    cur=state.get("mode",_MODES[0])
    if val=="next": _switch(+1)
    elif val=="prev": _switch(-1)
    elif val in _MODES:
        delta=_MODES.index(val)-(_MODES.index(cur) if cur in _MODES else 0)
        if delta: _switch(delta)

def _do_hud(val):
    state=_get_state() if _get_state else {}
    cur=state.get("hud",_HUD_STYLES[0])
    if val=="next": _switch_hud(+1)
    elif val=="prev": _switch_hud(-1)
    elif val in _HUD_STYLES:
        delta=_HUD_STYLES.index(val)-(_HUD_STYLES.index(cur) if cur in _HUD_STYLES else 0)
        if delta: _switch_hud(delta)

def _do_led(zone,val):
    if _set_led: _set_led(zone,val)
    else:
        try:
            import led_fans as lf; _apply_led(lf,zone,val)
        except Exception as e: print(f"[LED API] {e}")

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

def _apply_led(lf,zone,val):
    if zone=="case_mode" and val in _CASE_MODES: lf.set_cfg(case_mode=val)
    elif zone=="fan_mode" and val in _FAN_MODES: lf.set_cfg(fan_mode=val)
    elif zone=="case_color" and val in _COLORS: lf.set_cfg(case_color=val)
    elif zone=="case_color2" and val in _COLORS: lf.set_cfg(case_color2=val)
    elif zone=="fan_color" and val in _COLORS: lf.set_cfg(fan_color=val)
    elif zone=="fan_color2" and val in _COLORS: lf.set_cfg(fan_color2=val)
    elif zone in _FLOAT_PARAMS:
        try: lf.set_cfg(**{zone:float(val)})
        except ValueError: pass
    elif zone=="case" and val in _CASE_MODES: lf.set_cfg(case_mode=val)
    elif zone=="fans" and val in _FAN_MODES: lf.set_cfg(fan_mode=val)
    elif zone=="fans_color" and val in _COLORS: lf.set_cfg(fan_color=val)
    elif zone=="fans_color2" and val in _COLORS: lf.set_cfg(fan_color2=val)

def _status_json():
    s=_get_state() if _get_state else {}
    if _set_led:
        try:
            import led_fans as lf
            cfg=lf.get_cfg()
            s["led"]=cfg
        except Exception: pass
    return s

def start(switch_fn=None,switch_hud_fn=None,get_state_fn=None,set_led_fn=None):
    global _switch,_switch_hud,_get_state,_set_led
    _switch=switch_fn; _switch_hud=switch_hud_fn
    _get_state=get_state_fn; _set_led=set_led_fn
    import threading
    t=threading.Thread(target=lambda:HTTPServer(("",PORT),_Handler).serve_forever(),daemon=True)
    t.start()
    print(f"[Dashboard] http://localhost:{PORT}")
