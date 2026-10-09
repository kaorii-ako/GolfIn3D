// GolfIn3D site: interactive 3D swing, OS-aware downloads, tabs, copy, reveal.
document.documentElement.classList.add("js");
const REPO = "kaorii-ako/GolfIn3D";
const reduceMotion = matchMedia("(prefers-reduced-motion: reduce)").matches;

/* ------------------------------------------------------------ downloads */
const ASSETS = {
  win: { file: "GolfIn3D-Setup-Windows-x64.exe", label: "Download for Windows" },
  mac: { file: "GolfIn3D-macOS-arm64.dmg", label: "Download for macOS" },
  linux: { file: "GolfIn3D-Linux-x86_64.AppImage", label: "Download for Linux" },
};
function detectOS() {
  const ua = navigator.userAgent;
  const p = (navigator.userAgentData && navigator.userAgentData.platform) || navigator.platform || "";
  if (/iPhone|iPad|Android/i.test(ua)) return null;
  if (/Win/i.test(p) || /Windows/i.test(ua)) return "win";
  if (/Mac/i.test(p) || /Mac OS X/i.test(ua)) return "mac";
  if (/Linux/i.test(p) || /Linux/i.test(ua)) return "linux";
  return null;
}
const os = detectOS();
const heroDl = document.getElementById("hero-dl");
if (os) {
  heroDl.href = `https://github.com/${REPO}/releases/latest/download/${ASSETS[os].file}`;
  heroDl.querySelector("span").textContent = ASSETS[os].label;
}

fetch(`https://api.github.com/repos/${REPO}/releases/latest`)
  .then((r) => (r.ok ? r.json() : null))
  .then((j) => { if (j && j.tag_name) document.getElementById("ver").textContent = j.tag_name.replace(/^v/, ""); })
  .catch(() => {});

/* ------------------------------------------------------------ tabs */
const tablist = document.querySelector(".tabs");
const tabs = [...tablist.querySelectorAll("[role=tab]")];
function selectTab(i, focus) {
  tabs.forEach((t, j) => {
    const on = i === j;
    t.setAttribute("aria-selected", on);
    t.tabIndex = on ? 0 : -1;
    document.getElementById(t.getAttribute("aria-controls")).hidden = !on;
  });
  tablist.style.setProperty("--i", i);
  if (focus) tabs[i].focus();
}
tabs.forEach((t, i) => t.addEventListener("click", () => selectTab(i)));
tablist.addEventListener("keydown", (e) => {
  const i = tabs.findIndex((t) => t.getAttribute("aria-selected") === "true");
  if (e.key === "ArrowRight") selectTab((i + 1) % tabs.length, true);
  if (e.key === "ArrowLeft") selectTab((i - 1 + tabs.length) % tabs.length, true);
});
selectTab({ win: 0, mac: 1, linux: 2 }[os] ?? 0);

/* ------------------------------------------------------------ copy */
document.querySelectorAll(".copy").forEach((b) =>
  b.addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(b.previousElementSibling.textContent);
      b.classList.add("done");
      b.innerHTML = '<i class="ph ph-check"></i>';
      setTimeout(() => { b.classList.remove("done"); b.innerHTML = '<i class="ph ph-copy"></i>'; }, 1600);
    } catch { /* clipboard blocked: the text is still selectable */ }
  })
);

/* ------------------------------------------------------------ reveal */
const io = new IntersectionObserver((entries) => {
  for (const e of entries) if (e.isIntersecting) { e.target.classList.add("in"); io.unobserve(e.target); }
}, { rootMargin: "0px 0px -10% 0px" });
document.querySelectorAll(".reveal").forEach((el) => {
  const sibs = [...el.parentElement.children].filter((c) => c.classList.contains("reveal"));
  el.style.setProperty("--d", Math.min(sibs.indexOf(el), 5));
  io.observe(el);
});

/* ------------------------------------------------------------ 3D swing */
// A parametric golfer (metres, y up, facing +z, target at +x). Same projection and
// orbit-with-momentum as the app's 3D view.
const T0 = -1.45, T1 = 0.75;
const rad = (d) => (d * Math.PI) / 180;
const rotY = ([x, y, z], a) => [x * Math.cos(a) + z * Math.sin(a), y, -x * Math.sin(a) + z * Math.cos(a)];
const add = (a, b) => [a[0] + b[0], a[1] + b[1], a[2] + b[2]];
const mix = (a, b, t) => [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t];
const norm = (v) => { const l = Math.hypot(...v); return [v[0] / l, v[1] / l, v[2] / l]; };

function phaseK(t) {
  if (t < -1.25) return 0;
  if (t < -0.3) { const u = (t + 1.25) / 0.95; return 1 - (1 - u) ** 2; }   // backswing slows into the top
  if (t < 0) { const u = (t + 0.3) / 0.3; return 1 - u * u; }              // downswing accelerates
  const u = Math.min(1, t / 0.55); return -(1 - (1 - u) ** 2);            // follow-through
}

function golfer(t) {
  const k = phaseK(t);
  const back = Math.max(k, 0), thru = Math.max(-k, 0);
  const sh = rad(92 * back - 105 * thru), hp = rad(45 * back - 80 * thru);
  const shift = t > -0.3 ? 0.07 * Math.min(1, (t + 0.3) / 0.3) : 0;   // hips bump toward the target
  const pelvis = [shift, 0.95, 0];
  const J = {};
  J.lhip = add(pelvis, rotY([0.15, 0, 0], -hp));
  J.rhip = add(pelvis, rotY([-0.15, 0, 0], -hp));
  const chest = add(pelvis, [0, 0.47, 0.2 - 0.08 * thru]);
  J.lsh = add(chest, rotY([0.2, 0, 0], -sh));
  J.rsh = add(chest, rotY([-0.2, 0, 0], -sh));
  J.head = add(chest, [0.02 * back, 0.24 + 0.04 * thru, 0.12 - 0.1 * thru]);
  const arm = rad(90 * Math.max(back, thru));
  const H = [0, 0.82 + 0.75 * Math.sin(arm), 0.42 - 0.14 * Math.sin(arm)];
  J.hand = add(rotY(H, -sh * 1.05), [shift, 0, 0]);
  J.lel = add(mix(J.lsh, J.hand, 0.5), rotY([0, -0.02, 0.06], -sh));
  J.rel = add(mix(J.rsh, J.hand, 0.5), rotY([-0.04, -0.12 * back, 0.08], -sh));
  J.lkn = [0.17 - 0.06 * back + 0.03 * thru, 0.5, 0.12];
  J.rkn = [-0.17 + 0.03 * back + 0.14 * thru, 0.5 + 0.03 * thru, 0.12 + 0.05 * thru];
  J.lan = [0.21, 0.06, 0];
  J.ran = [-0.21 + 0.06 * thru, 0.06 + 0.1 * thru, -0.02 * thru];
  const ball = [0.05, 0.022, 0.86];
  const dAddr = norm([ball[0] - 0.05, ball[1] - 0.82, ball[2] - 0.42]);
  const hinge = Math.min(1, back * 1.5), rel = Math.min(1, thru * 1.3);
  let dir = mix(dAddr, rotY([1, 0.05, 0], -sh), hinge);
  if (thru > 0) dir = mix(dAddr, norm([-0.55, -0.25, -0.8]), rel);
  J.club = add(J.hand, norm(dir).map((v) => v * 1.12));
  J.ball = t < 0.01 ? ball : add(ball, [0, 0.5 * Math.min(1, t * 6), 3 * t]);
  return { J, sh: Math.abs(92 * back - 105 * thru), hp: Math.abs(45 * back - 80 * thru) };
}

const BONES = [["lsh", "rsh"], ["lsh", "lel"], ["lel", "hand"], ["rsh", "rel"], ["rel", "hand"], ["lsh", "lhip"], ["rsh", "rhip"],
  ["lhip", "rhip"], ["lhip", "lkn"], ["lkn", "lan"], ["rhip", "rkn"], ["rkn", "ran"]];
const LEFT = new Set(["lsh", "lel", "lhip", "lkn", "lan"]);

const canvas = document.getElementById("swing3d");
const ctx = canvas.getContext("2d");
const scrub = document.getElementById("scrub");
const playBtn = document.getElementById("play");
const hudPhase = document.getElementById("hud-phase"), hudSh = document.getElementById("hud-sh"), hudHp = document.getElementById("hud-hp");
const view = { yaw: -0.75, pitch: 0.18, vy: 0, vp: 0, zoom: 1 };
let t = reduceMotion ? -0.3 : T0, playing = !reduceMotion, hold = 0, visible = true, last = performance.now();
const trail = [];   // sampled ahead of time: the whole hand + clubhead path
for (let s = T0; s <= T1; s += 0.01) { const { J } = golfer(s); trail.push({ s, hand: J.hand, club: J.club }); }

function resize() {
  const r = canvas.getBoundingClientRect(), dpr = Math.min(devicePixelRatio || 1, 2);
  canvas.width = r.width * dpr; canvas.height = r.height * dpr;
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
}
new ResizeObserver(resize).observe(canvas);

function project([x, y, z], w, h) {
  const cy = Math.cos(view.yaw), sy = Math.sin(view.yaw), cp = Math.cos(view.pitch), sp = Math.sin(view.pitch);
  const x1 = x * cy + z * sy, z1 = -x * sy + z * cy;
  const y1 = (y - 0.95) * cp - z1 * sp, z2 = (y - 0.95) * sp + z1 * cp;
  const f = Math.min(w, h) * 0.36 * view.zoom, persp = 4 / (4 + z2);
  return [w / 2 + x1 * f * persp, h * 0.46 - y1 * f * persp, z2];
}

function phaseName(t) {
  if (t < -1.2) return "Address";
  if (t < -0.36) return "Backswing";
  if (t < -0.24) return "Top";
  if (t < -0.015) return "Downswing";
  if (t < 0.05) return "Impact";
  return "Follow-through";
}

function draw() {
  const r = canvas.getBoundingClientRect(), w = r.width, h = r.height;
  ctx.clearRect(0, 0, w, h);
  const P = (v) => project(v, w, h);
  // ground: rings and a target line
  ctx.lineWidth = 1;
  for (let i = 1; i <= 4; i++) {
    ctx.beginPath();
    for (let a = 0; a <= 64; a++) {
      const [px, py] = P([Math.cos((a / 64) * 2 * Math.PI) * i * 0.45, 0, Math.sin((a / 64) * 2 * Math.PI) * i * 0.45]);
      a ? ctx.lineTo(px, py) : ctx.moveTo(px, py);
    }
    ctx.strokeStyle = `rgba(255,255,255,${0.09 - i * 0.015})`;
    ctx.stroke();
  }
  const [ax, ay] = P([-1.8, 0, 0.86]), [bx, by] = P([1.8, 0, 0.86]);
  const g = ctx.createLinearGradient(ax, ay, bx, by);
  g.addColorStop(0, "rgba(48,209,88,0)"); g.addColorStop(0.5, "rgba(48,209,88,0.35)"); g.addColorStop(1, "rgba(48,209,88,0)");
  ctx.strokeStyle = g; ctx.setLineDash([4, 6]); ctx.beginPath(); ctx.moveTo(ax, ay); ctx.lineTo(bx, by); ctx.stroke(); ctx.setLineDash([]);

  // paths up to now: clubhead (faint) and hands (accent)
  const upto = trail.filter((p) => p.s <= t && p.s >= -1.25);
  const path = (key, color, width) => {
    if (upto.length < 2) return;
    ctx.beginPath();
    upto.forEach((p, i) => { const [x, y] = P(p[key]); i ? ctx.lineTo(x, y) : ctx.moveTo(x, y); });
    ctx.strokeStyle = color; ctx.lineWidth = width; ctx.lineJoin = ctx.lineCap = "round"; ctx.stroke();
  };
  path("club", "rgba(255,255,255,0.16)", 1.5);
  path("hand", "rgba(48,209,88,0.75)", 2.5);

  const { J, sh, hp } = golfer(t);
  const S = Object.fromEntries(Object.entries(J).map(([k, v]) => [k, P(v)]));
  // ball
  ctx.fillStyle = "#f2f4f1";
  ctx.beginPath(); ctx.arc(S.ball[0], S.ball[1], 4, 0, 7); ctx.fill();
  // club
  ctx.strokeStyle = "rgba(220,224,220,0.9)"; ctx.lineWidth = 2;
  ctx.beginPath(); ctx.moveTo(S.hand[0], S.hand[1]); ctx.lineTo(S.club[0], S.club[1]); ctx.stroke();
  ctx.fillStyle = "#d9ddd8"; ctx.beginPath(); ctx.arc(S.club[0], S.club[1], 4.5, 0, 7); ctx.fill();
  // bones, far ones first
  const bones = BONES.map(([a, b]) => ({ a, b, z: (S[a][2] + S[b][2]) / 2 })).sort((m, n) => n.z - m.z);
  for (const { a, b } of bones) {
    const left = LEFT.has(a) && (LEFT.has(b) || b === "hand");
    ctx.strokeStyle = left ? "#30d158" : "rgba(242,244,241,0.92)";
    ctx.lineWidth = 4.5;
    ctx.beginPath(); ctx.moveTo(S[a][0], S[a][1]); ctx.lineTo(S[b][0], S[b][1]); ctx.stroke();
  }
  for (const k of ["lsh", "rsh", "lel", "rel", "hand", "lhip", "rhip", "lkn", "rkn", "lan", "ran"]) {
    ctx.fillStyle = LEFT.has(k) ? "#30d158" : "#f2f4f1";
    ctx.beginPath(); ctx.arc(S[k][0], S[k][1], 3.6, 0, 7); ctx.fill();
  }
  // head
  const hr = Math.min(w, h) * 0.36 * view.zoom * 0.1 * (4 / (4 + S.head[2]));
  ctx.strokeStyle = "rgba(242,244,241,0.92)"; ctx.lineWidth = 3;
  ctx.beginPath(); ctx.arc(S.head[0], S.head[1], hr, 0, 7); ctx.stroke();

  hudPhase.textContent = phaseName(t);
  hudSh.textContent = `${Math.round(sh)}°`;
  hudHp.textContent = `${Math.round(hp)}°`;
  const p = ((t - T0) / (T1 - T0)) * 100;
  scrub.value = Math.round(p * 10);
  scrub.style.setProperty("--p", `${p}%`);
}

function frame(now) {
  const dt = Math.min(0.05, (now - last) / 1000);
  last = now;
  if (playing && !dragging) {
    if (hold > 0) hold -= dt;
    else {
      t += dt * 0.32;                                   // slow motion
      if (t >= T1) { t = T1; hold = 1.4; }
    }
    if (t >= T1 && hold <= 0) { t = T0; hold = 0.5; }
  }
  if (!grabbed && (Math.abs(view.vy) > 1e-4 || Math.abs(view.vp) > 1e-4)) {   // momentum after a flick
    view.yaw += view.vy; view.pitch = Math.max(-0.2, Math.min(1.2, view.pitch + view.vp));
    view.vy *= 0.94; view.vp *= 0.94;
  } else if (!grabbed && playing && !reduceMotion) {
    view.yaw += dt * 0.06;                              // slow idle orbit so the depth reads
  }
  draw();
  if (visible) requestAnimationFrame(frame);
}

// orbit: 1:1 while dragging, carries velocity on release
let grabbed = false, dragging = false, lx = 0, ly = 0;
canvas.addEventListener("pointerdown", (e) => {
  grabbed = true; lx = e.clientX; ly = e.clientY; view.vy = view.vp = 0;
  canvas.setPointerCapture(e.pointerId);
});
canvas.addEventListener("pointermove", (e) => {
  if (!grabbed) return;
  const dx = (e.clientX - lx) * 0.008, dy = (e.clientY - ly) * 0.006;
  lx = e.clientX; ly = e.clientY;
  view.yaw += dx; view.pitch = Math.max(-0.2, Math.min(1.2, view.pitch + dy));
  view.vy = dx; view.vp = dy;
});
const release = () => { grabbed = false; };
canvas.addEventListener("pointerup", release);
canvas.addEventListener("pointercancel", release);

scrub.addEventListener("input", () => {
  dragging = true;
  setPlaying(false);
  t = T0 + (scrub.value / 1000) * (T1 - T0);
  hold = 0;
});
scrub.addEventListener("change", () => { dragging = false; });

function setPlaying(on) {
  playing = on;
  playBtn.innerHTML = on ? '<i class="ph-fill ph-pause"></i>' : '<i class="ph-fill ph-play"></i>';
  playBtn.setAttribute("aria-label", on ? "Pause" : "Play");
}
playBtn.addEventListener("click", () => setPlaying(!playing));
setPlaying(playing);

// only animate while the stage is on screen
new IntersectionObserver(([e]) => {
  const was = visible;
  visible = e.isIntersecting;
  if (visible && !was) { last = performance.now(); requestAnimationFrame(frame); }
}).observe(canvas);
requestAnimationFrame((n) => { last = n; resize(); frame(n); });
