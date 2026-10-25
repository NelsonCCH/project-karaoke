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
  var scheme = dflt;
  try { scheme = localStorage.getItem(key) || dflt; } catch (e) {}
  function resolve(id){
    if (id !== "auto") return id;
    var dark = window.matchMedia && matchMedia("(prefers-color-scheme: dark)").matches;
    return dark ? (darkDefault || "nuit") : "paper";
  }
  function apply(id){
    var r = resolve(id), de = document.documentElement;
    de.setAttribute("data-scheme", r);
    de.classList.toggle("dark", !!DARK_SCHEMES[r]);
  }
  function build(){
    var m = document.getElementById("thememenu"), html = "", last = null;
    SCHEMES.forEach(function(sc){
      if (sc.group && sc.group !== last){
        html += '<div class="theme-group">' + tr(sc.group === "dark" ? "Dark" : "Light") + '</div>';
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
  apply(scheme);
  build();
  initTheme.rebuild = build;
  document.getElementById("thememenu").addEventListener("click", function(ev){
    var b = ev.target.closest("button[data-scheme]");
    if (!b) return;
    scheme = b.getAttribute("data-scheme");
    try { localStorage.setItem(key, scheme); } catch (e) {}
    apply(scheme);
    build();
    document.getElementById("theme").open = false;
  });
  document.addEventListener("click", function(ev){
    var d = document.getElementById("theme");
    if (d && d.open && !d.contains(ev.target)) d.open = false;
  });
  try {
    var mq = window.matchMedia && matchMedia("(prefers-color-scheme: dark)");
    if (mq && mq.addEventListener) mq.addEventListener("change", function(){ if (scheme === "auto") apply("auto"); });
  } catch (e) {}
}
