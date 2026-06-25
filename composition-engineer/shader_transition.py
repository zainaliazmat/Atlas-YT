"""Mason's signature shader-transition layer — a deterministic WebGL boundary transition.

WHAT THIS IS. A small, closed-vocabulary library of GLSL cross-scene transitions
(whip-pan, sdf-iris, glitch, domain-warp) ported from the gl-transitions catalogue
(MIT) and a builder that emits ONE self-contained HTML page. The page loads the two
boundary frames as base64 textures and draws the transition at a single `progress`
value read from `location.hash` (`#p=0.42`). The integration layer (hf_tools) renders
that page once per frame via headless Chrome's SwiftShader and splices the resulting
clip between two scenes at assembly.

WHY IT STAYS MASON-PURE. The render is byte-stable: SwiftShader (software GL) is
deterministic across runs/machines (spiked + proven), the page draws SYNCHRONOUSLY on
load (no requestAnimationFrame, no clock), progress is a pure function of the frame
index passed in the URL, and every shader is a pure function of (uv, progress) — any
procedural noise is a hashed function of uv, never time/random. So the same boundary
frames + shader + frame count produce identical bytes. This module is PURE (string
generation only); nothing here touches the toolchain or the filesystem.

DETERMINISM WALL (enforced by tests, mirrored from Mason's scan_determinism + HF lint):
the emitted HTML must contain NONE of Math.random / Date.now / performance.now /
requestAnimationFrame / setTimeout / fetch( / <animate / SMIL. Motion comes only from
the externally-stepped `progress` uniform.
"""
from __future__ import annotations

# Closed vocabulary — the ONLY shader tokens Iris/the assembler may request. Adding a
# transition means adding its GLSL body here AND a token to this tuple (a parity test
# guards that the two stay in lockstep, like Mason's other closed vocabularies).
SHADER_TRANSITIONS = ("whip-pan", "sdf-iris", "glitch", "domain-warp")

# Tokens that must never appear in a generated transition page (determinism wall).
BANNED_TOKENS = ("Math.random", "Date.now", "performance.now", "requestAnimationFrame",
                 "setTimeout", "setInterval", "fetch(", "<animate", "repeat:-1")

# Default transition length. ~0.45s reads as a deliberate signature beat, not a glitch.
DEFAULT_FRAMES = 14

# ----------------------------------------------------------------------
# GLSL transition bodies (gl-transitions style: a `vec4 transition(vec2 uv)` that mixes
# getFromColor(uv) / getToColor(uv) by the `progress` uniform). WebGL1 / GLSL ES 1.00.
# Each is a pure function of (uv, progress) — procedural noise is hashed from uv, so no
# clock or RNG is needed and the frame is reproducible.
# ----------------------------------------------------------------------
_GLSL: dict[str, str] = {
    # A fast directional whip with multi-tap motion blur along the pan axis. The "from"
    # frame smears out left while the "to" frame whips in from the right.
    "whip-pan": """
vec4 transition(vec2 uv){
  float p = smoothstep(0.0, 1.0, progress);
  vec2 dir = vec2(1.0, 0.0);
  vec4 acc = vec4(0.0);
  const int TAPS = 8;
  for(int i=0;i<TAPS;i++){
    float t = float(i)/float(TAPS-1);              // 0..1 across the blur smear
    float smear = (t-0.5)*0.18*(1.0-abs(2.0*p-1.0)); // widest at mid-transition
    vec2 fp = uv + dir*(p) + dir*smear;            // from-frame pushed out
    vec2 tp = uv - dir*(1.0-p) + dir*smear;        // to-frame pulled in
    vec4 fc = getFromColor(fract(fp));
    vec4 tc = getToColor(fract(tp));
    acc += mix(fc, tc, step(1.0-p, uv.x));
  }
  acc /= float(TAPS);
  // a brief bright streak rides the seam
  float seam = smoothstep(0.0, 0.04, abs(uv.x-(1.0-p)));
  return acc + (1.0-seam)*0.25*(1.0-abs(2.0*p-1.0));
}
""",
    # A signed-distance circular iris opening from the frame centre, revealing the "to"
    # frame inside an expanding disc with a thin bright ring on its edge.
    "sdf-iris": """
vec4 transition(vec2 uv){
  vec2 c = uv - 0.5;
  c.x *= R.x / R.y;                                // aspect-correct circle
  float d = length(c);
  float p = smoothstep(0.0, 1.0, progress);
  float radius = p * 0.95;
  float edge = 0.012;
  float reveal = smoothstep(radius+edge, radius-edge, d);  // 1 inside the disc
  vec4 col = mix(getFromColor(uv), getToColor(uv), reveal);
  float ring = smoothstep(edge, 0.0, abs(d-radius)) * (1.0-abs(2.0*p-1.0));
  return col + ring*0.5;
}
""",
    # An RGB-split block glitch: horizontal slabs jump sideways by a seeded hash and the
    # colour channels separate, peaking at mid-transition, then resolve onto the "to" frame.
    "glitch": """
float hash(vec2 p){ return fract(sin(dot(p, vec2(127.1,311.7)))*43758.5453123); }
vec4 transition(vec2 uv){
  float p = progress;
  float energy = 1.0 - abs(2.0*p-1.0);             // 0..1, peak at p=0.5
  float band = floor(uv.y*18.0);
  float jolt = (hash(vec2(band, floor(p*8.0)))-0.5) * 0.25 * energy;
  vec2 guv = vec2(fract(uv.x+jolt), uv.y);
  float split = 0.012*energy;
  vec4 base = mix(getFromColor(guv), getToColor(guv), smoothstep(0.35,0.65,p));
  float r = mix(getFromColor(guv+vec2(split,0.0)), getToColor(guv+vec2(split,0.0)), smoothstep(0.35,0.65,p)).r;
  float b = mix(getFromColor(guv-vec2(split,0.0)), getToColor(guv-vec2(split,0.0)), smoothstep(0.35,0.65,p)).b;
  return vec4(r, base.g, b, 1.0);
}
""",
    # A domain-warp dissolve: uv is warped by stacked sine folds that bulge at mid-
    # transition, so the two frames melt through each other before settling.
    "domain-warp": """
vec4 transition(vec2 uv){
  float p = smoothstep(0.0, 1.0, progress);
  float bump = 1.0 - abs(2.0*p-1.0);
  vec2 w = uv;
  w.x += sin(uv.y*12.0 + p*6.2831)*0.03*bump;
  w.y += sin(uv.x*10.0 + p*6.2831)*0.03*bump;
  w.x += sin(uv.y*26.0 - p*9.0)*0.012*bump;
  return mix(getFromColor(fract(w)), getToColor(fract(w)), p);
}
""",
}


# Default taste ordering for auto-assigned signature transitions (premium first). The
# Nth signature boundary in a video gets the Nth shader here. A storyboard may override
# per-beat by naming any token in SHADER_TRANSITIONS.
SIGNATURE_DEFAULT_ORDER = ("sdf-iris", "glitch", "domain-warp", "whip-pan")

# At most this many shader transitions per video (a signature beat is rare by design).
SHADER_BUDGET = 2


def validate_shader(name: str) -> bool:
    """True iff `name` is in the closed vocabulary."""
    return name in SHADER_TRANSITIONS


def default_signature_shader(index: int) -> str:
    """The taste-ordered default shader for the `index`-th signature boundary (0-based)."""
    return SIGNATURE_DEFAULT_ORDER[index % len(SIGNATURE_DEFAULT_ORDER)]


def chrome_flags() -> list[str]:
    """The headless-Chrome flags that make WebGL render byte-stable here (SwiftShader,
    software rasteriser). Spiked: identical md5 across runs and flag-equivalent sets."""
    return ["--headless=new", "--enable-unsafe-swiftshader", "--hide-scrollbars",
            "--force-color-profile=srgb", "--disable-lcd-text"]


def progress_for_frame(i: int, frames: int) -> float:
    """Deterministic eased progress for frame `i` of `frames` (endpoints 0.0 and 1.0)."""
    if frames <= 1:
        return 1.0
    return round(i / (frames - 1), 6)


def build_transition_html(from_b64: str, to_b64: str, shader: str,
                          width: int, height: int) -> str:
    """Return ONE self-contained HTML page that draws `shader` between the two base64
    PNG frames at the `progress` read from location.hash (`#p=<float>`). Pure + byte-
    stable for identical arguments. Raises ValueError on an unknown shader token."""
    if not validate_shader(shader):
        raise ValueError(f"unknown shader transition {shader!r}; "
                         f"allowed: {SHADER_TRANSITIONS}")
    body = _GLSL[shader].strip()
    frag = (
        "precision highp float;\n"
        "uniform sampler2D uFrom;\n"
        "uniform sampler2D uTo;\n"
        "uniform float progress;\n"
        "uniform vec2 R;\n"
        "vec4 getFromColor(vec2 uv){return texture2D(uFrom, uv);}\n"
        "vec4 getToColor(vec2 uv){return texture2D(uTo, uv);}\n"
        f"{body}\n"
        "void main(){\n"
        "  vec2 uv = vec2(gl_FragCoord.x/R.x, 1.0 - gl_FragCoord.y/R.y);\n"
        "  gl_FragColor = transition(uv);\n"
        "}\n"
    )
    # NOTE: drawn synchronously on load (no rAF/clock); preserveDrawingBuffer so the
    # headless screenshot reads the rendered frame. Progress comes only from the URL.
    return f"""<!doctype html><html><head><meta charset="utf-8">
<style>html,body{{margin:0;background:#000}}canvas{{display:block}}</style></head>
<body><canvas id="c" width="{width}" height="{height}"></canvas>
<script id="img-from" type="x/uri">{from_b64}</script>
<script id="img-to" type="x/uri">{to_b64}</script>
<script>
function progress(){{var m=/p=([0-9.]+)/.exec(location.hash);return m?parseFloat(m[1]):0.0;}}
var cv=document.getElementById('c');
var gl=cv.getContext('webgl',{{preserveDrawingBuffer:true,antialias:false}});
var VS='attribute vec2 p;void main(){{gl_Position=vec4(p,0.0,1.0);}}';
var FS={_js_str(frag)};
function sh(t,s){{var o=gl.createShader(t);gl.shaderSource(o,s);gl.compileShader(o);return o;}}
var pr=gl.createProgram();
gl.attachShader(pr,sh(gl.VERTEX_SHADER,VS));
gl.attachShader(pr,sh(gl.FRAGMENT_SHADER,FS));
gl.linkProgram(pr);gl.useProgram(pr);
var buf=gl.createBuffer();gl.bindBuffer(gl.ARRAY_BUFFER,buf);
gl.bufferData(gl.ARRAY_BUFFER,new Float32Array([-1,-1,3,-1,-1,3]),gl.STATIC_DRAW);
var lp=gl.getAttribLocation(pr,'p');gl.enableVertexAttribArray(lp);
gl.vertexAttribPointer(lp,2,gl.FLOAT,false,0,0);
var pending=2;
function tex(uri,unit,uname){{
  var t=gl.createTexture();var img=new Image();
  img.onload=function(){{
    gl.activeTexture(gl.TEXTURE0+unit);gl.bindTexture(gl.TEXTURE_2D,t);
    gl.pixelStorei(gl.UNPACK_FLIP_Y_WEBGL,false);
    gl.texImage2D(gl.TEXTURE_2D,0,gl.RGBA,gl.RGBA,gl.UNSIGNED_BYTE,img);
    gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_WRAP_S,gl.CLAMP_TO_EDGE);
    gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_WRAP_T,gl.CLAMP_TO_EDGE);
    gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_MIN_FILTER,gl.LINEAR);
    gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_MAG_FILTER,gl.LINEAR);
    gl.uniform1i(gl.getUniformLocation(pr,uname),unit);
    if(--pending===0) draw();
  }};
  img.src=uri;
}}
function draw(){{
  gl.uniform2f(gl.getUniformLocation(pr,'R'),{width}.0,{height}.0);
  gl.uniform1f(gl.getUniformLocation(pr,'progress'),progress());
  gl.drawArrays(gl.TRIANGLES,0,3);gl.finish();
  document.title='DONE';
}}
tex(document.getElementById('img-from').textContent,0,'uFrom');
tex(document.getElementById('img-to').textContent,1,'uTo');
</script></body></html>"""


def _js_str(s: str) -> str:
    """Encode a Python string as a JS string literal (for inlining the shader source)."""
    return "'" + s.replace("\\", "\\\\").replace("'", "\\'").replace("\n", "\\n") + "'"
