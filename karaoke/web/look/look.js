/* Styles ("looks") for the remote and lyrics pages. A look is a recipe from the Design style arsenal (looks.js,
   built by tools/build_looks.py): palette, type and shape become CSS variables on #kit, every other course a
   data-<course> attribute, which kit.css keys its rules on. looks-tune.css fits the arsenal's phone-mockup rules to
   the real pages. The choice is stored with the colour schemes, as pk_scheme = "look:<id>", and picked from the
   same menu (theme.js). Motion: the looks animate (rain, glow, bokeh); Windows with animation effects off
   is reported to pages as prefers-reduced-motion, so html.motion-ok plays them anyway: on unless the switch says "off". */
var LOOK_NAMES = {
  neonkroom: ["Neon K-room", "neon pink · purple"], mongkok: ["Mong Kok neon", "street signs · red"],
  wkw: ["Wong Kar-wai", "film · red & jade"], outrun: ["Outrun", "synthwave grid"],
  blackcat: ["Black cat night", "black · cat-eye green"], deco: ["Art Deco", "black · gold"],
  brass: ["Walnut & brass", "old radio"], midcentury: ["Mid-century", "mustard · orange"],
  qinghua: ["Blue & white", "porcelain"], lofi: ["Lo-fi evening", "dusk · lamp light"]
};

function lookById(id){ return LOOKS.filter(function(l){ return l.id === id; })[0]; }

/* Apply look `id` (or none: the plain house page) to the element `kit`. */
function applyLook(kit, id){
  var L = id && lookById(id), de = document.documentElement;
  kit.classList.toggle("kit", !!L);
  kit.removeAttribute("style");
  Object.keys(kit.dataset).forEach(function(k){ delete kit.dataset[k]; });
  if (L){
    kit.setAttribute("style", Object.keys(L.vars).map(function(k){ return "--" + k + ":" + L.vars[k]; }).join(";"));
    Object.keys(L.pick).forEach(function(c){ if (c !== "pal" && c !== "type") kit.dataset[c] = L.pick[c]; });
    kit.dataset.dark = L.dark;
    kit.dataset.look = L.id;
    if (!kit.querySelector(":scope>.fx"))
      kit.insertAdjacentHTML("afterbegin", '<i class="fx fx-a"></i><i class="fx fx-b"></i><i class="fx fx-c"></i><i class="fx fx-d"></i>');
    var scr = kit.querySelector(".scr");
    if (scr && !scr.querySelector(":scope>.scn"))
      scr.insertAdjacentHTML("afterbegin", '<div class="scn" aria-hidden="true"><div class="art"><i class="sn sn-1"></i><i class="sn sn-2"></i><i class="sn sn-3"></i><i class="sn sn-4"></i><i class="sn sn-5"></i><i class="sn sn-6"></i></div></div>');
    setTimeout(function(){ lookFrame(kit); }, 0);
  }
  if (L){ de.setAttribute("data-look", L.id); de.style.setProperty("--look-bg", L.vars.bg); }
  else { de.removeAttribute("data-look"); de.style.removeProperty("--look-bg"); }
}

/* How a scene painting is framed (as the arsenal's alive.js): full screen where the cards let it show through,
   a band at the top where solid cards would hide it anyway. */
function lookFrame(kit){
  if (!kit.dataset.scene || kit.dataset.scene === "none") return;
  var solid = 0, all = 0;
  kit.querySelectorAll("main .card").forEach(function(c){
    var r = c.getBoundingClientRect(), h = Math.min(r.bottom, innerHeight) - Math.max(r.top, 0);
    if (h <= 0) return;
    var s = getComputedStyle(c), m = s.backgroundColor.match(/[\d.]+/g) || [0, 0, 0, 0];
    all += h * r.width;
    if ((m.length > 3 ? +m[3] : 1) > .5 || s.backgroundImage.indexOf("url(") >= 0) solid += h * r.width;
  });
  kit.dataset.scnfit = all && solid / all >= .4 ? "band" : "full";
}

/* The motion switch. */
function lookMotion(){ try { return localStorage.getItem("pk_motion") !== "off"; } catch (e) { return true; } }
function setLookMotion(on){
  try { localStorage.setItem("pk_motion", on ? "on" : "off"); } catch (e) {}
  document.documentElement.classList.toggle("motion-ok", on);
}
document.documentElement.classList.toggle("motion-ok", lookMotion() || /[?&]motion=on\b/.test(location.search));
