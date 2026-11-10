// Helper for build_looks.py: runs the design arsenal's own scripts and prints the chosen recipes as JSON.
// node _dump_recipes.js <design-menu dir> '["name", ...]'
const fs = require("fs"), path = require("path"), vm = require("vm");
const [dir, namesJson] = process.argv.slice(2);
const ctx = {console, document: {addEventListener() {}, querySelectorAll() { return []; }, documentElement: {dataset: {}}},
             matchMedia: () => ({matches: false, addEventListener() {}}), localStorage: {getItem() { return null; }, setItem() {}},
             location: {search: "", hash: ""}, requestAnimationFrame() {}, setTimeout() {}};
ctx.window = ctx;
vm.createContext(ctx);
for (const f of ["axes.js", "axes-css1.js", "axes-css2.js", "axes-art.js", "axes-ornate.js", "axes-icons.js", "sets-v2.js",
                 "library-sets.js", "lookbook-notes.js", "axes-kits.js", "axes-alive.js", "axes-sig.js"])
  vm.runInContext(fs.readFileSync(path.join(dir, f), "utf8"), ctx, {filename: f});
ctx.NAMES = JSON.parse(namesJson);
vm.runInContext(`
var ALL = SETS.map(function(s){ var n = typeof s[1] === "number" ? LOOKBOOK_NOTES[s[1]] : null; return {name: s[0], pick: s[2], desc: n ? n[1] : ""}; })
  .concat(LIBRARY_SETS.map(function(s){ return {name: s[0], pick: s[2], desc: s[3] || ""}; }));
function opt(key, id){ var c = COURSES.filter(function(c){ return c.key === key; })[0]; return c.options.filter(function(o){ return o.id === id; })[0]; }
var R = {picks: {}, vars: {}, dark: {}, dots: {}, notes: {}};
NAMES.forEach(function(n){
  var s = ALL.filter(function(x){ return x.name === n; })[0];
  if (!s) throw new Error("no style " + n);
  var pick = fullPick(s.pick), v = {}, p = opt("pal", pick.pal);
  [p, opt("type", pick.type), opt("shape", pick.shape)].forEach(function(o){ if (o.v) Object.keys(o.v).forEach(function(k){ v[k] = o.v[k]; }); });
  R.picks[n] = pick; R.vars[n] = v; R.dark[n] = !!p.dark; R.dots[n] = [v.bg, v.surface, v.accent]; R.notes[n] = s.desc || p.note;
});
console.log(JSON.stringify(R));
`, ctx);
