// Downloads cleaner dashboard. Everything on screen comes from the local server: the scan's progress, the plan (one cube per file),
// and one event per file as it is copied or moved. Replay plays the last run's own events again through the same code.
import * as THREE from "./vendor/three/build/three.module.min.js";
import { EffectComposer } from "./vendor/three/addons/postprocessing/EffectComposer.js";
import { RenderPass } from "./vendor/three/addons/postprocessing/RenderPass.js";
import { UnrealBloomPass } from "./vendor/three/addons/postprocessing/UnrealBloomPass.js";
import { OutputPass } from "./vendor/three/addons/postprocessing/OutputPass.js";

const $ = (s) => document.querySelector(s);
const TOKEN = document.querySelector("meta[name=token]").content;
async function api(path, body) {
  const opt = body === undefined ? { headers: { "X-Token": TOKEN } } : { method: "POST", headers: { "X-Token": TOKEN, "Content-Type": "application/json" }, body: JSON.stringify(body) };
  const r = await fetch(path, opt), j = await r.json();
  if (!r.ok) throw new Error(j.error || `HTTP ${r.status}`);
  return j;
}
const fmt = (n) => Math.round(n).toLocaleString("en-US");
const gb = (b) => b <= 0 ? "0 GB" : b >= 2 ** 30 ? (b / 2 ** 30).toFixed(1) + " GB" : b >= 2 ** 20 ? Math.round(b / 2 ** 20) + " MB" : Math.max(1, Math.round(b / 1024)) + " KB";
const COLORS = { Images: "#5AA9FF", Screenshots: "#3DF5C8", Videos: "#FF7EB6", Audio: "#7CE38B", Documents: "#FFD23F", Spreadsheets: "#5BD17A", Slides: "#FF8F5A",
  Installers: "#FFA552", Archives: "#C9A0FF", Code: "#B6E3FF", Fonts: "#E0C3FF", Other: "#9AA3AF", Duplicates: "#FF4D5E" };
const colorOf = (k) => COLORS[k] || "#" + ((Array.from(k).reduce((h, c) => (h * 31 + c.charCodeAt(0)) >>> 0, 7) & 0x7f7f7f) | 0x606060).toString(16).padStart(6, "0");
function toast(msg) { const t = $("#toast"); t.textContent = msg; t.classList.add("show"); clearTimeout(toast.h); toast.h = setTimeout(() => t.classList.remove("show"), 4200); }

// ---------- 3D scene ----------
const canvas = $("#gl");
const renderer = new THREE.WebGLRenderer({ canvas, antialias: true, powerPreference: "high-performance" });
renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
renderer.toneMapping = THREE.ACESFilmicToneMapping;
const scene = new THREE.Scene();
scene.background = new THREE.Color(0x05060a);
scene.fog = new THREE.FogExp2(0x05060a, 0.018);
const camera = new THREE.PerspectiveCamera(48, 1, 0.1, 400);
const composer = new EffectComposer(renderer);
composer.addPass(new RenderPass(scene, camera));
const bloom = new UnrealBloomPass(new THREE.Vector2(1, 1), 0.85, 0.5, 0.32);
composer.addPass(bloom);
composer.addPass(new OutputPass());
function resize() { const w = innerWidth, h = innerHeight; renderer.setSize(w, h, false); composer.setSize(w, h); bloom.setSize(w, h); camera.aspect = w / h; camera.updateProjectionMatrix(); }
addEventListener("resize", resize); resize();

// Stars and floor.
{ const n = 2600, p = new Float32Array(n * 3); for (let i = 0; i < n; i++) { const r = 70 + Math.random() * 90, a = Math.random() * Math.PI * 2, z = Math.random() * 2 - 1, s = Math.sqrt(1 - z * z); p.set([r * s * Math.cos(a), r * z * .6 + 10, r * s * Math.sin(a)], i * 3); }
  const g = new THREE.BufferGeometry(); g.setAttribute("position", new THREE.BufferAttribute(p, 3)); scene.add(new THREE.Points(g, new THREE.PointsMaterial({ color: 0x8fa3c7, size: .35, sizeAttenuation: true, fog: false }))); }
const grid = new THREE.PolarGridHelper(17, 24, 10, 96, 0x1d2a44, 0x131a2b); scene.add(grid);
const core = new THREE.Mesh(new THREE.IcosahedronGeometry(.9, 1), new THREE.MeshBasicMaterial({ color: 0xffd23f, wireframe: true, toneMapped: false }));
core.position.y = 2.2; scene.add(core);
const coreGlow = new THREE.Mesh(new THREE.SphereGeometry(.45, 24, 16), new THREE.MeshBasicMaterial({ color: 0xffe28a, toneMapped: false })); coreGlow.position.y = 2.2; scene.add(coreGlow);
const scanRing = new THREE.Mesh(new THREE.TorusGeometry(1, .03, 8, 128), new THREE.MeshBasicMaterial({ color: 0xffd23f, toneMapped: false, transparent: true, opacity: 0 }));
scanRing.rotation.x = Math.PI / 2; scene.add(scanRing);
const wave = new THREE.Mesh(new THREE.RingGeometry(.9, 1, 128), new THREE.MeshBasicMaterial({ color: 0x3ddc97, toneMapped: false, transparent: true, opacity: 0, side: THREE.DoubleSide }));
wave.rotation.x = -Math.PI / 2; wave.position.y = .05; scene.add(wave);

// File cubes: `orig` live in the vortex (the folder); `copy` fly to their tower. One cube can stand for several files on big folders.
const MAXP = 3000, BOX = new THREE.BoxGeometry(.17, .17, .17);
const mk = () => { const m = new THREE.InstancedMesh(BOX, new THREE.MeshBasicMaterial({ toneMapped: false }), MAXP); m.instanceMatrix.setUsage(THREE.DynamicDrawUsage); m.count = 0; m.frustumCulled = false; scene.add(m); return m; };
const orig = mk(), copy = mk();
const VA = new THREE.Vector3(), VB = new THREE.Vector3(), M = new THREE.Matrix4(), Q = new THREE.Quaternion(), V = new THREE.Vector3(), S = new THREE.Vector3(), C = new THREE.Color(), E = new THREE.Euler();
let P = [];            // particles: {a, r, y, w, kind, dup, tower, slot, file, leave, mode}
let towers = [];       // {name, color, x, z, count, slots, layerH, el, mesh, outline, landed}
let files = [], fileToP = [], phase = "idle", action = "copy", planned = null, scanCount = 0, fast = 0;
const towerGroup = new THREE.Group(); scene.add(towerGroup);
const portal = new THREE.Group(); scene.add(portal);
{ const t = new THREE.Mesh(new THREE.TorusGeometry(1.3, .07, 12, 96), new THREE.MeshBasicMaterial({ color: 0xff4d5e, toneMapped: false })); portal.add(t);
  const t2 = new THREE.Mesh(new THREE.TorusGeometry(.95, .025, 8, 96), new THREE.MeshBasicMaterial({ color: 0xff8a95, toneMapped: false })); portal.add(t2);
  const disk = new THREE.Mesh(new THREE.CircleGeometry(1.25, 64), new THREE.MeshBasicMaterial({ color: 0x3a0610, transparent: true, opacity: .75, side: THREE.DoubleSide })); portal.add(disk); portal.visible = false; }

function cloudPos(p, t, out) {
  const a = p.a + t * p.w * (1 + fast * 2.5), r = p.r * (phase === "scan" || phase === "hash" ? .92 + .08 * Math.sin(t * 3 + p.a) : 1);
  return out.set(Math.cos(a) * r, p.y + Math.sin(t * .9 + p.a * 3) * .08, Math.sin(a) * r);
}
function newParticle(i) { const r = 1.4 + 4.6 * Math.sqrt(Math.random()); return { a: Math.random() * Math.PI * 2, r, y: 2.2 + (Math.random() - .5) * 1.5 * (1 - r / 7), w: .5 / Math.sqrt(r), file: i, leave: null, landed: false }; }

function buildTowers(summary) {
  towers.forEach((t) => t.el.remove()); towerGroup.clear(); towers = [];
  const names = Object.keys(summary.kinds).filter((k) => P.some((p) => p.tower === k)).sort((a, b) => summary.kinds[b].files - summary.kinds[a].files);
  const n = names.length, R = 10.5;
  names.forEach((name, i) => {
    const gap = P.some((p) => p.tower === "Duplicates") ? .26 : 0, ang = -Math.PI / 2 + Math.PI * gap + (i + .5) / n * Math.PI * 2 * (1 - gap);   // a wedge left free for the duplicates portal
    const t = { name, color: colorOf(name), x: Math.cos(ang) * R, z: Math.sin(ang) * R, count: 0, landed: 0, real: summary.kinds[name] };
    towers.push(t);
  });
  if (P.some((p) => p.tower === "Duplicates")) towers.push({ name: "Duplicates", color: COLORS.Duplicates, x: 0, z: -R - .5, count: 0, landed: 0, real: { files: summary.duplicates, bytes: summary.duplicate_bytes }, portal: true });
  const byName = Object.fromEntries(towers.map((t) => [t.name, t]));
  P.forEach((p) => { const t = byName[p.tower]; if (t) p.slot = t.count++; });
  for (const t of towers) {
    const layers = Math.ceil(t.count / 25), H = Math.min(.21, 7.5 / Math.max(1, layers)); t.layerH = H; t.height = .2 + layers * H;
    if (t.portal) { portal.position.set(t.x, 2.6, t.z); portal.lookAt(0, 2.6, 0); portal.visible = true; }
    else {
      const base = new THREE.Mesh(new THREE.CylinderGeometry(.95, 1.05, .12, 6), new THREE.MeshBasicMaterial({ color: new THREE.Color(t.color).multiplyScalar(.16), toneMapped: false }));
      base.position.set(t.x, .06, t.z); towerGroup.add(base); t.base = base; t.baseColor = new THREE.Color(t.color);
      const edges = new THREE.LineSegments(new THREE.EdgesGeometry(new THREE.CylinderGeometry(.75, .75, 1, 6)), new THREE.LineBasicMaterial({ color: t.color, transparent: true, opacity: .4, toneMapped: false }));
      edges.position.set(t.x, .1, t.z); edges.scale.set(1, .001, 1); towerGroup.add(edges); t.outline = edges;
    }
    t.el = document.createElement("div"); t.el.className = "lbl" + (t.portal ? " dup" : ""); t.el.style.color = t.portal ? "" : t.color; $("#labels").append(t.el);
  }
  portal.visible = towers.some((t) => t.portal);
}
function slotPos(p, out) {
  const t = towers.find((x) => x.name === p.tower); if (!t) return cloudPos(p, 0, out);
  if (t.portal) return out.set(t.x * .97, 2.6, t.z * .97);
  const L = 25, layer = Math.floor(p.slot / L), k = p.slot % L;
  return out.set(t.x + ((k % 5) - 2) * .21, .25 + layer * t.layerH, t.z + (Math.floor(k / 5) - 2) * .21);
}

function setFiles(list, summary) {
  files = list; const N = list.length, n = Math.min(N, MAXP); fileToP = new Int32Array(N);
  const old = P; P = [];
  for (let j = 0; j < n; j++) {
    const i = Math.floor(j * N / n), f = list[i], p = old[j] || newParticle(i);
    p.file = i; p.kind = f.kind; p.dup = f.dup; p.leave = null; p.landed = false;
    p.tower = f.skip ? null : f.dup && f.dest.startsWith("_Duplicates") ? "Duplicates" : f.kind;
    P.push(p);
  }
  for (let j = 0, i = 0; i < N; i++) { while (j + 1 < n && Math.floor((j + 1) * N / n) <= i) j++; fileToP[i] = j; }
  orig.count = copy.count = P.length;
  P.forEach((p, j) => { orig.setColorAt(j, C.set(p.dup ? COLORS.Duplicates : colorOf(p.kind)).multiplyScalar(.55)); copy.setColorAt(j, C.set(p.tower === "Duplicates" ? COLORS.Duplicates : colorOf(p.kind)).multiplyScalar(.8)); p.glow = 0; });
  orig.instanceColor.needsUpdate = copy.instanceColor.needsUpdate = true;
  buildTowers(summary);
}
function scanParticles(count) {
  const n = Math.min(MAXP, count);
  while (P.length < n) { const p = newParticle(P.length); p.tower = null; p.born = clock; P.push(p); orig.setColorAt(P.length - 1, C.set("#56627a")); }
  orig.count = P.length; copy.count = 0; if (orig.instanceColor) orig.instanceColor.needsUpdate = true;
}

// ---------- camera ----------
const cam = { th: .9, ph: .42, r: 27, drag: null, idle: 99 };
canvas.addEventListener("pointerdown", (e) => { cam.drag = [e.clientX, e.clientY]; canvas.setPointerCapture(e.pointerId); });
canvas.addEventListener("pointermove", (e) => { if (!cam.drag) return; cam.th -= (e.clientX - cam.drag[0]) * .005; cam.ph = Math.min(1.25, Math.max(.08, cam.ph + (e.clientY - cam.drag[1]) * .004)); cam.drag = [e.clientX, e.clientY]; cam.idle = 0; });
canvas.addEventListener("pointerup", () => { cam.drag = null; });
canvas.addEventListener("wheel", (e) => { cam.r = Math.min(46, Math.max(12, cam.r * (1 + Math.sign(e.deltaY) * .08))); cam.idle = 0; }, { passive: true });

// ---------- the run, as events ----------
let queue = [], launched = 0, lastLaunch = 0, sortedCount = 0, dupCount = 0, dupBytes = 0, finaleAt = null, replaying = false;
function resetRun() {
  P.forEach((p) => { p.leave = null; p.landed = false; }); towers.forEach((t) => { t.landed = 0; t.realDone = 0; t.bytesDone = 0; });
  queue = []; launched = 0; sortedCount = 0; dupCount = 0; dupBytes = 0; finaleAt = null; $("#finale").classList.add("hidden");
}
function onEvent(ev, at) {
  const f = files[ev.i]; if (!f) return;
  if (ev.ok) { sortedCount++; const t = towers.find((x) => x.name === (f.dup && f.dest.startsWith("_Duplicates") ? "Duplicates" : f.kind)); if (t) { t.realDone = (t.realDone || 0) + 1; t.bytesDone = (t.bytesDone || 0) + f.size; } }
  if (f.dup) { dupCount++; dupBytes += f.size; }
  const j = fileToP[ev.i], p = P[j];
  if (p && p.file === ev.i && ev.ok && p.leave === null) { const t0 = Math.max(at, lastLaunch + .004); lastLaunch = t0; p.leave = t0; }
}

// ---------- frame ----------
let clock = 0, last = performance.now();
function frame(now) {
  const dt = Math.min(.05, (now - last) / 1000); last = now; clock += dt; cam.idle += dt;
  fast += ((phase === "scan" || phase === "hash" ? 1 : 0) - fast) * Math.min(1, dt * 3);
  if (cam.idle > 3) cam.th += dt * .05;
  camera.position.set(Math.cos(cam.th) * Math.cos(cam.ph) * cam.r, 1.5 + Math.sin(cam.ph) * cam.r, Math.sin(cam.th) * Math.cos(cam.ph) * cam.r);
  camera.lookAt(0, 2.3, 0);
  // Replay: feed the stored events on their own schedule.
  while (queue.length && queue[0].at <= clock) { const q = queue.shift(); onEvent(q.ev, q.at); }
  core.rotation.y += dt * (.4 + fast * 2); core.rotation.x += dt * .15; coreGlow.scale.setScalar(1 + .12 * Math.sin(clock * 4) + fast * .3);
  scanRing.material.opacity = fast * (.6 + .4 * Math.sin(clock * 8)); const sr = 1.5 + ((clock * 2.2) % 4.5); scanRing.scale.setScalar(sr); scanRing.position.y = 2.2 + Math.sin(clock * 1.7) * .9;
  portal.children[0].rotation.z += dt * 1.2; portal.children[1].rotation.z -= dt * 2.1;
  let moving = 0;
  for (let j = 0; j < P.length; j++) {
    const p = P[j]; cloudPos(p, clock, V);
    const born = p.born !== undefined ? Math.min(1, (clock - p.born) / .6) : 1;
    let s = .2 + .8 * born, rot = clock * 1.5 + p.a * 7;
    if (p.leave !== null && clock >= p.leave) s *= action === "move" ? Math.max(0, 1 - (clock - p.leave) * 3) : 1;
    if (p.leave !== null && clock >= p.leave && action === "copy" && !p.dimmed) { orig.setColorAt(j, C.set(p.dup ? COLORS.Duplicates : colorOf(p.kind)).multiplyScalar(.13)); orig.instanceColor.needsUpdate = true; p.dimmed = true; }
    if (p.leave === null && p.dimmed) { orig.setColorAt(j, C.set(p.dup ? COLORS.Duplicates : colorOf(p.kind)).multiplyScalar(.55)); orig.instanceColor.needsUpdate = true; p.dimmed = false; }
    if (phase === "planned" && p.dup) s *= .85 + .35 * Math.abs(Math.sin(clock * 5 + j));
    M.compose(V, Q.setFromEuler(E.set(rot, rot * .7, 0)), S.setScalar(s)); orig.setMatrixAt(j, M);
    // The copy: hidden until it leaves, then an arc to its slot, then it stays (or vanishes into the portal).
    let cs = 0;
    if (p.leave !== null && clock >= p.leave) {
      const u = Math.min(1, (clock - p.leave) / .95), e = u < .5 ? 4 * u * u * u : 1 - (-2 * u + 2) ** 3 / 2;
      const a = cloudPos(p, p.leave, VA), b = slotPos(p, VB);
      V.lerpVectors(a, b, e); V.y += Math.sin(e * Math.PI) * (3.2 + (j % 7) * .25);
      const t = towers.find((x) => x.name === p.tower);
      cs = t && t.portal ? (u < 1 ? 1 - e * .6 : 0) : 1; rot = (1 - e) * 6;
      if (u < 1) moving++;
      const g = u < 1 ? 1.9 : .8; if (p.glow !== g) { copy.setColorAt(j, C.set(p.tower === "Duplicates" ? COLORS.Duplicates : colorOf(p.kind)).multiplyScalar(g)); copy.instanceColor.needsUpdate = true; p.glow = g; }
      if (u < 1) cs *= 1 + .5 * Math.sin(e * Math.PI);
      if (u >= 1 && !p.landed) { p.landed = true; if (t) { t.landed++; t.flash = 1; } }
    }
    M.compose(V, Q.setFromEuler(E.set(rot, rot, 0)), S.setScalar(cs)); copy.setMatrixAt(j, M);
  }
  orig.instanceMatrix.needsUpdate = copy.instanceMatrix.needsUpdate = true;
  // Towers: outline grows to the planned height; labels follow.
  for (const t of towers) {
    if (t.base) { t.flash = Math.max(0, (t.flash || 0) - dt * 4); t.base.material.color.copy(t.baseColor).multiplyScalar(.16 + .55 * t.flash); }
    if (t.outline) { const target = phase === "idle" ? .001 : t.height; t.outline.scale.y += (target - t.outline.scale.y) * Math.min(1, dt * 2.5); t.outline.position.y = .1 + t.outline.scale.y / 2; }
    V.set(t.x, (t.portal ? 4.3 : (t.outline ? t.outline.scale.y : 0) + .9), t.z).project(camera);
    const vis = V.z < 1; t.el.style.opacity = vis ? 1 : 0; t.el.style.left = ((V.x + 1) / 2 * innerWidth) + "px"; t.el.style.top = ((1 - V.y) / 2 * innerHeight) + "px";
    const done = t.realDone || 0, label = t.portal ? "_Duplicates" : t.name;
    t.el.innerHTML = `${label}<small>${fmt(phase === "planned" ? t.real.files : done)} files · ${gb(phase === "planned" ? t.real.bytes : t.bytesDone || 0)}</small>`;
  }
  // Finale: a shockwave when the last cube lands.
  if (finaleAt === null && (phase === "applied" || replaying) && launched && queue.length === 0 && moving === 0 && P.some((p) => p.leave !== null)) { finaleAt = clock; showFinale(); }
  if (finaleAt !== null && clock - finaleAt > 6) $("#finale").classList.add("hidden");
  if (finaleAt !== null) { const u = (clock - finaleAt) / 1.6; wave.material.opacity = Math.max(0, .8 * (1 - u)); wave.scale.setScalar(1 + u * 16); }
  stats();
  composer.render();
  requestAnimationFrame(frame);
}
requestAnimationFrame(frame);

function stats() {
  if (!planned) return;
  const s = planned.summary;
  $("#s-files").textContent = fmt(s.files);
  $("#s-sorted").textContent = fmt(sortedCount);
  const showAll = phase === "planned";
  $("#s-dups").textContent = fmt(showAll ? s.duplicates : dupCount);
  $("#s-waste").textContent = gb(showAll ? s.duplicate_bytes : dupBytes);
}
function showFinale() {
  if (!planned) return; const s = planned.summary;
  $("#f-files").textContent = `${fmt(sortedCount)} files sorted`;
  $("#f-dups").textContent = `${gb(s.duplicate_bytes)} of duplicates found`;
  $("#f-safe").textContent = action === "move" ? "Moved, not deleted · undo any time · ran offline" : "Nothing moved or deleted · ran offline";
  $("#finale").classList.remove("hidden");   // hidden again 6 s later on the animation's own clock (see frame())
}

// ---------- settings panel, built from the server's schema ----------
let schema = [], cfg = {};
function kindsText(k) { return Object.entries(k).map(([n, e]) => `${n}: ${e}`).join("\n"); }
function parseKinds(txt) { const out = {}; for (const line of txt.split("\n")) { const m = line.match(/^\s*([^:]+?)\s*:\s*(.*)$/); if (m && m[1]) out[m[1]] = m[2].trim().toLowerCase().replace(/[.,]/g, " ").split(/\s+/).filter(Boolean).join(" "); } return out; }
function buildSettings(state) {
  schema = state.schema; cfg = state.cfg;
  $("#source").value = cfg.source; $("#kinds").value = kindsText(cfg.kinds);
  $("#quick").innerHTML = ""; for (const q of state.quick) { const b = document.createElement("button"); b.textContent = q.split(/[\\/]/).pop(); b.title = q; b.onclick = () => { $("#source").value = q; $("#path").textContent = q; }; $("#quick").append(b); }
  setAction(cfg.action);
  const box = $("#fields"); box.innerHTML = "";
  for (const f of schema) {
    if (["source", "action", "kinds"].includes(f.key)) continue;
    const v = cfg[f.key], id = "f-" + f.key;
    if (Array.isArray(f.rule)) box.insertAdjacentHTML("beforeend", `<label class="field"><span>${f.help}</span><select id="${id}">${f.rule.map((o) => `<option ${o === v ? "selected" : ""}>${o}</option>`).join("")}</select></label>`);
    else if (f.rule === "bool") box.insertAdjacentHTML("beforeend", `<label class="toggle"><input type="checkbox" id="${id}" ${v ? "checked" : ""}>${f.help}</label>`);
    else if (f.rule === "number" || f.rule === "int") box.insertAdjacentHTML("beforeend", `<label class="field"><span>${f.help}</span><input id="${id}" type="number" min="0" step="${f.rule === "int" ? 1 : .1}" value="${v}"></label>`);
    else if (f.rule === "path") box.insertAdjacentHTML("beforeend", `<label class="field"><span>${f.help}</span><input id="${id}" value="${v}" spellcheck="false"></label>`);
    else if (f.rule === "kinds") { box.insertAdjacentHTML("beforeend", `<div class="field"><span>${f.help}</span><div class="kinds" id="${id}"></div></div>`); }
  }
  drawKindChips();
  $("#kinds").addEventListener("input", drawKindChips);
  $("#path").textContent = cfg.source;
  drawRuns(state.runs);
}
function drawKindChips() {
  const el = $("#f-include_kinds"); if (!el) return; const on = new Set(Array.from(el.querySelectorAll("input:checked")).map((i) => i.value).concat(el.dataset.init ? [] : cfg.include_kinds || [])); el.dataset.init = 1;
  const names = Object.keys(parseKinds($("#kinds").value)).concat(["Screenshots", "Other"]);
  el.innerHTML = names.map((n) => `<label><input type="checkbox" value="${n}" ${on.has(n) ? "checked" : ""}>${n}</label>`).join("");
}
function readSettings() {
  const c = { source: $("#source").value.trim(), action, kinds: parseKinds($("#kinds").value) };
  for (const f of schema) {
    const el = document.getElementById("f-" + f.key); if (!el) continue;
    if (f.rule === "bool") c[f.key] = el.checked; else if (f.rule === "kinds") c[f.key] = Array.from(el.querySelectorAll("input:checked")).map((i) => i.value);
    else if (f.rule === "number") c[f.key] = parseFloat(el.value) || 0; else if (f.rule === "int") c[f.key] = parseInt(el.value) || 0; else c[f.key] = el.value;
  }
  return c;
}
function setAction(a) {
  action = a; document.querySelectorAll("#action button").forEach((b) => b.classList.toggle("on", b.dataset.v === a));
  $("#actionhint").textContent = a === "move" ? "Originals move into the sorted folder. Every move is logged; Undo puts them back. Nothing is deleted." : "Sorted copies go to a new folder. Your files stay exactly where they are.";
  const chip = $("#modechip"); chip.textContent = a === "move" ? "Move mode · undo available" : "Copy mode · originals untouched"; chip.className = "chip " + (a === "move" ? "warn" : "ok");
  stale();
}
document.querySelectorAll("#action button").forEach((b) => b.onclick = () => setAction(b.dataset.v));
// Any change after a scan makes the plan stale: Clean waits for a new scan (the server refuses a stale plan too).
function stale() { if (planned && phase === "planned") { $("#b-apply").disabled = true; $("#b-apply").textContent = "Settings changed · scan again"; $("#b-apply").classList.remove("danger"); } }
$("#settings").addEventListener("input", stale); $("#settings").addEventListener("change", stale);
function drawRuns(list) {
  $("#runs").innerHTML = list.length ? "<b>Past runs</b>" : "";
  for (const r of list.slice(0, 6)) {
    const d = document.createElement("div"); d.className = "run";
    d.innerHTML = `<span>${new Date(r.started * 1000).toLocaleString()} · ${r.action} · ${fmt(r.files)}</span>`;
    if (!r.undone) { const b = document.createElement("button"); b.textContent = "Undo"; b.onclick = () => undo(r.run); d.append(b); } else d.insertAdjacentHTML("beforeend", "<em>undone</em>");
    $("#runs").append(d);
  }
}
function drawKinds(s) {
  const max = Math.max(1, ...Object.values(s.kinds).map((k) => k.bytes));
  $("#kindlist").innerHTML = Object.entries(s.kinds).sort((a, b) => b[1].bytes - a[1].bytes).map(([k, v]) =>
    `<div class="krow"><i style="background:${colorOf(k)}"></i><span>${k}</span><em>${fmt(v.files)} · ${gb(v.bytes)}</em><div class="kbar"><div style="width:${v.bytes / max * 100}%;background:${colorOf(k)}"></div></div></div>`).join("");
  const dups = files.filter((f) => f.dup).sort((a, b) => b.size - a.size).slice(0, 60);
  $("#duplist").innerHTML = dups.map((f) => `<li><b>${f.name.replace(/</g, "&lt;")}</b> · ${gb(f.size)}</li>`).join("");
  $("#dupbox").querySelector("summary").textContent = `Duplicates found (${fmt(s.duplicates)}, ${gb(s.duplicate_bytes)})`;
}

// ---------- actions ----------
let since = 0, pollTimer = null, lastEvents = [];
function busy(on) { $("#b-scan").disabled = on; $("#b-stop").classList.toggle("hidden", !on); document.body.classList.toggle("busy", on); }
async function scan() {
  try { await api("/api/scan", { cfg: readSettings() }); } catch (e) { return toast(e.message); }
  replaying = false; resetRun(); towers.forEach((t) => t.el.remove()); towers = []; towerGroup.clear(); portal.visible = false; P = []; planned = null; scanCount = 0;
  $("#path").textContent = $("#source").value; phase = "scan"; busy(true); $("#b-apply").disabled = true; $("#b-replay").disabled = true; poll();
}
let armed = 0;
async function clean() {
  if (!planned) return;
  if (action === "move" && Date.now() - armed > 4000) { armed = Date.now(); $("#b-apply").textContent = `Click again to move ${fmt(planned.summary.to_write)} files`; $("#b-apply").classList.add("danger"); return; }
  $("#b-apply").classList.remove("danger");
  try { await api("/api/apply", { cfg: readSettings() }); } catch (e) { return toast(e.message); }
  lastEvents = []; replaying = false; resetRun(); lastLaunch = clock; phase = "apply"; launched = 1; busy(true); since = 0; poll();
}
async function undo(run) { try { await api("/api/undo", { run }); phase = "undo"; busy(true); poll(); } catch (e) { toast(e.message); } }
function replay() {
  if (!lastEvents.length) return;
  replaying = true; resetRun(); phase = "applied";
  const D = 11, t0 = lastEvents[0].t, span = Math.max(.001, lastEvents[lastEvents.length - 1].t - t0), start = clock + 1.4;
  queue = lastEvents.map((ev) => ({ ev, at: start + (ev.t - t0) / span * D }));
  launched = 1; lastLaunch = start;
}
async function poll() {
  clearTimeout(pollTimer);
  let v; try { v = await api(`/api/progress?since=${since}`); } catch (e) { toast(e.message); return; }
  since = v.next;
  if (v.error) { phase = "error"; busy(false); toast(v.error); $("#phase").textContent = "Error"; return; }
  if (v.phase === "scan" || v.phase === "hash") { phase = v.phase; scanCount = v.phase === "scan" ? v.done : Math.max(scanCount, P.length); scanParticles(scanCount); $("#phase").textContent = v.phase === "scan" ? `Scanning · ${fmt(v.done)} files` : `Finding duplicates · ${fmt(v.done)} / ${fmt(v.total)}`; $("#prog").style.width = v.total ? (v.done / v.total * 100) + "%" : "15%"; }
  if (v.phase === "planned" && v.result && phase !== "planned") {
    planned = { ...v.result, cfgAction: action }; phase = "planned"; busy(false); scanParticles(v.result.files.length); setFiles(v.result.files, v.result.summary); drawKinds(v.result.summary);
    $("#phase").textContent = `Plan ready · ${fmt(v.result.summary.to_write)} files to ${action}`; $("#prog").style.width = "100%";
    $("#b-apply").disabled = !v.result.summary.to_write; $("#b-apply").textContent = `Clean · ${action} ${fmt(v.result.summary.to_write)} files`;
    return;
  }
  if (v.phase === "apply" || v.phase === "applied") {
    for (const ev of v.events) { onEvent(ev, clock); lastEvents.push(ev); }
    $("#prog").style.width = (v.total ? v.done / v.total * 100 : 0) + "%"; $("#phase").textContent = `${action === "move" ? "Moving" : "Copying"} · ${fmt(v.done)} / ${fmt(v.total)}`;
    if (v.phase === "applied") { phase = "applied"; busy(false); $("#b-apply").textContent = "Clean"; $("#b-apply").classList.remove("danger"); $("#b-replay").disabled = false; $("#b-open").disabled = false; $("#b-apply").disabled = true;
      $("#phase").textContent = `Done · ${fmt(v.result.written)} files ${v.result.action === "move" ? "moved" : "copied"}${v.result.stopped ? " (stopped)" : ""}`; refreshRuns(); return; }
  }
  if (v.phase === "undone") { busy(false); phase = "idle"; $("#phase").textContent = `Undone · ${fmt(v.result.undo.reversed)} files put back`; resetRun(); refreshRuns(); return; }
  if (v.phase === "undo") $("#phase").textContent = `Undoing · ${fmt(v.done)} / ${fmt(v.total)}`;
  pollTimer = setTimeout(poll, 120);
}
async function refreshRuns() { try { drawRuns((await api("/api/state")).runs); } catch (e) { /* the panel keeps the old list */ } }
$("#b-scan").onclick = scan;
$("#b-apply").onclick = clean;
$("#b-stop").onclick = () => api("/api/stop", {}).catch((e) => toast(e.message));
$("#b-replay").onclick = () => { if (phase === "apply") return; lastEvents.length && replay(); };
$("#b-open").onclick = () => api("/api/open", {}).catch((e) => toast(e.message));
addEventListener("keydown", (e) => { if (e.target.closest("input,textarea,select")) return; if (e.key === "h" || e.key === "H") document.body.classList.toggle("film"); if (e.key === "f" || e.key === "F") document.documentElement.requestFullscreen?.(); });

api("/api/state").then(buildSettings).catch((e) => toast(e.message));
