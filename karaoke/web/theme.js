/* Colour-scheme picker, the house pattern from Project Baby. Usage: initTheme("pk_scheme", "paper") after the
   page has <details class="theme" id="theme"><summary>🎨</summary><div class="theme-menu" id="thememenu"></div></details>.
   The page's own <head> sets data-scheme before first paint, so there is no flash. */
var SCHEMES = [
  { id:"auto",     name:"Auto",         sub:"follows the device", group:"",      dots:["#f6f3ec","#9aa3b2","#14161c"] },
  { id:"paper",    name:"Warm Paper",   sub:"warm · default",     group:"light", dots:["#f6f3ec","#f3e7dc","#8c5a3a"] },
  { id:"cafe",     name:"Café",         sub:"warm · coffee",      group:"light", dots:["#f4ecdf","#efe4d2","#7c4a1e"] },
  { id:"creme",    name:"Crème",        sub:"cool · classic",     group:"light", dots:["#f5f3ee","#eef1f6","#2f4bd7"] },
  { id:"sage",     name:"Sage",         sub:"green · calm",       group:"light", dots:["#eef3ee","#e6efe6","#4a7c5d"] },
  { id:"menthe",   name:"Menthe",       sub:"mint · fresh",       group:"light", dots:["#edf4f0","#e0ede7","#0f766e"] },
  { id:"blue",     name:"Dusty Blue",   sub:"blue · clean",       group:"light", dots:["#eff3f6","#e4edf3","#3d6c8a"] },
  { id:"blush",    name:"Blush & Plum", sub:"rose · gentle",      group:"light", dots:["#f7f1f3","#f3e4ea","#9a5570"] },
  { id:"slate",    name:"Clean Slate",  sub:"teal · modern",      group:"light", dots:["#f4f5f6","#e4efee","#2f6d6a"] },
  { id:"nuit",     name:"Nuit",         sub:"neutral",            group:"dark",  dots:["#14161c","#262b36","#7a90ff"] },
  { id:"ocean",    name:"Océan",        sub:"blue",               group:"dark",  dots:["#0c1524","#1e2e4a","#4f9fe0"] },
  { id:"bordeaux", name:"Bordeaux",     sub:"wine",               group:"dark",  dots:["#1b1216","#352430","#c05572"] }
];
var DARK_SCHEMES = { nuit:1, ocean:1, bordeaux:1 };

function initTheme(key, dflt, darkDefault, extra){
  var tr = typeof T === "function" ? T : function(s){ return s; };   // i18n.js, when the page loads it
  if (extra) SCHEMES = extra.concat(SCHEMES);
  // Styles (look/look.js), when the page has them: listed first, stored as "look:<id>".
  var kit = document.getElementById("kit");
  if (kit && typeof LOOKS !== "undefined")
    SCHEMES = LOOKS.map(function(l){
      return {id: "look:" + l.id, name: LOOK_NAMES[l.id][0], sub: LOOK_NAMES[l.id][1], group: "style",
              dots: [l.vars.bg, l.vars.accent2 || l.vars.surface, l.vars.accent]};
    }).concat(SCHEMES);
  var scheme = dflt;
  try { scheme = localStorage.getItem(key) || dflt; } catch (e) {}
  var forced = /[?&]look=([\w-]+)/.exec(location.search);    // ?look=<id> for screenshots; not stored
  if (forced) scheme = forced[1] === "none" ? dflt : "look:" + forced[1];
  if (/^look:/.test(scheme) && !(kit && typeof LOOKS !== "undefined" && lookById(scheme.slice(5)))) scheme = dflt;
  function resolve(id){
    if (/^look:/.test(id)) return lookById(id.slice(5)).dark ? "nuit" : "paper";   // semantic colours only
    if (id !== "auto") return id;
    var dark = window.matchMedia && matchMedia("(prefers-color-scheme: dark)").matches;
    return dark ? (darkDefault || "nuit") : "paper";
  }
  function apply(id){
    var r = resolve(id), de = document.documentElement;
    de.setAttribute("data-scheme", r);
    de.classList.toggle("dark", !!DARK_SCHEMES[r]);
    if (kit && typeof applyLook === "function") applyLook(kit, /^look:/.test(id) ? id.slice(5) : null);
  }
  function build(){
    var m = document.getElementById("thememenu"), html = "", last = null;
    SCHEMES.forEach(function(sc){
      if (last === "style" && sc.group !== "style"){ html += motionRow(); last = null; }
      if (sc.group && sc.group !== last){
        html += '<div class="theme-group">' + tr(sc.group === "dark" ? "Dark" : sc.group === "style" ? "Styles" : "Light") + '</div>';
        last = sc.group;
      }
      var dots = sc.dots.map(function(c){ return '<i style="background:' + c + '"></i>'; }).join("");
      html += '<button type="button" role="menuitemradio" data-scheme="' + sc.id + '"'
        + (sc.id === scheme ? ' class="on" aria-checked="true"' : ' aria-checked="false"')
        + '><span class="sw-dots">' + dots + '</span><span class="sw-name">' + tr(sc.name)
        + '</span><span class="sw-sub">' + tr(sc.sub) + '</span></button>';
    });
    m.innerHTML = html;
  }
  // The styles animate; Windows with animation effects off asks pages for reduced motion, so this turns it back on.
  function motionRow(){
    var on = typeof lookMotion === "function" && lookMotion();
    return '<button type="button" class="motion-row" id="lookmotion" aria-pressed="' + on + '"><span class="sw-name">'
      + tr("Motion") + '</span><span class="sw-sub">' + tr(on ? "on" : "off") + '</span></button>';
  }
  apply(scheme);
  build();
  initTheme.rebuild = build;
  document.getElementById("thememenu").addEventListener("click", function(ev){
    if (ev.target.closest("#lookmotion")){ setLookMotion(!lookMotion()); build(); return; }
    var b = ev.target.closest("button[data-scheme]");
    if (!b) return;
    scheme = b.getAttribute("data-scheme");
    try { localStorage.setItem(key, scheme); if (!/^look:/.test(scheme)) localStorage.setItem(key + "_plain", scheme); } catch (e) {}
    apply(scheme);
    build();
    document.getElementById("theme").open = false;
    picked = Date.now();   // a poll already in flight still carries the old style: don't follow it back
    if (initTheme.onPick) initTheme.onPick(scheme);
  });
  // The style is shared through the PC (settings.look): every page calls this with what the PC holds, so a style
  // picked on the phone shows on the lyrics screen too. "" = no style: back to this page's own colour scheme.
  var picked = 0, first = true;
  initTheme.follow = function(lookId){
    if (forced || !kit || typeof LOOKS === "undefined" || Date.now() - picked < 2000) return;
    // A style picked before the PC kept one (or kept by a PC that forgot it): hand it to the PC, don't drop it.
    if (first){ first = false; if (!lookId && /^look:/.test(scheme)){ picked = Date.now(); if (initTheme.onPick) initTheme.onPick(scheme); return; } }
    var want = lookId && lookById(lookId) ? "look:" + lookId : "";
    if (want ? scheme === want : !/^look:/.test(scheme)) return;
    if (!want){ try { want = localStorage.getItem(key + "_plain") || dflt; } catch (e) { want = dflt; } }
    scheme = want;
    try { localStorage.setItem(key, scheme); } catch (e) {}
    apply(scheme);
    build();
  };
  document.addEventListener("click", function(ev){
    var d = document.getElementById("theme");
    if (d && d.open && !d.contains(ev.target)) d.open = false;
  });
  try {
    var mq = window.matchMedia && matchMedia("(prefers-color-scheme: dark)");
    if (mq && mq.addEventListener) mq.addEventListener("change", function(){ if (scheme === "auto") apply("auto"); });
  } catch (e) {}
}
