/* Minimal QR code encoder for short URLs: byte mode, error correction L, versions 1-5 (up to 106 bytes), so
   every version is a single Reed-Solomon block and needs no version-info area. Follows the layout of Nayuki's
   reference encoder. qrSvg(text) returns an <svg> string, black on white with the 4-module quiet zone. */
function qrMatrix(text){
  var bytes = Array.from(new TextEncoder().encode(text));
  var DATA = [0, 19, 34, 55, 80, 108], EC = [0, 7, 10, 15, 20, 26], ver = 1;
  while (ver <= 5 && bytes.length + 2 > DATA[ver]) ver++;
  if (ver > 5) throw new Error("text too long for this QR encoder");
  var size = 17 + 4 * ver, cap = DATA[ver];

  // Bit stream: mode 0100, 8-bit length, the bytes, terminator, then pad bytes.
  var bits = [];
  function put(v, n){ for (var i = n - 1; i >= 0; i--) bits.push((v >>> i) & 1); }
  put(4, 4); put(bytes.length, 8); bytes.forEach(function(b){ put(b, 8); });
  put(0, Math.min(4, cap * 8 - bits.length));
  while (bits.length % 8) bits.push(0);
  var data = [];
  for (var i = 0; i < bits.length; i += 8){ var b = 0; for (var j = 0; j < 8; j++) b = (b << 1) | bits[i + j]; data.push(b); }
  for (var pad = 0xEC; data.length < cap; pad ^= 0xEC ^ 0x11) data.push(pad);

  // Reed-Solomon error correction over GF(256), polynomial 0x11D.
  function mul(x, y){ var z = 0; for (var i = 7; i >= 0; i--){ z = (z << 1) ^ ((z >>> 7) * 0x11D); z ^= ((y >>> i) & 1) * x; } return z; }
  var deg = EC[ver], div = [], root = 1;
  for (i = 0; i < deg - 1; i++) div.push(0);
  div.push(1);
  for (i = 0; i < deg; i++){
    for (j = 0; j < deg; j++){ div[j] = mul(div[j], root); if (j + 1 < deg) div[j] ^= div[j + 1]; }
    root = mul(root, 2);
  }
  var rem = div.map(function(){ return 0; });
  data.forEach(function(b){
    var f = b ^ rem.shift(); rem.push(0);
    for (var k = 0; k < deg; k++) rem[k] ^= mul(div[k], f);
  });
  var words = data.concat(rem);

  var m = [], fn = [];
  for (i = 0; i < size; i++){ m.push(new Array(size).fill(false)); fn.push(new Array(size).fill(false)); }
  function set(x, y, dark){ m[y][x] = dark; fn[y][x] = true; }
  for (i = 0; i < size; i++){ set(6, i, i % 2 === 0); set(i, 6, i % 2 === 0); }      // timing
  [[3, 3], [size - 4, 3], [3, size - 4]].forEach(function(c){                         // finders + separators
    for (var dy = -4; dy <= 4; dy++) for (var dx = -4; dx <= 4; dx++){
      var x = c[0] + dx, y = c[1] + dy, d = Math.max(Math.abs(dx), Math.abs(dy));
      if (x >= 0 && x < size && y >= 0 && y < size) set(x, y, d !== 2 && d !== 4);
    }
  });
  if (ver > 1){                                                                        // one alignment pattern
    var p = size - 7;
    for (var dy = -2; dy <= 2; dy++) for (var dx = -2; dx <= 2; dx++) set(p + dx, p + dy, Math.max(Math.abs(dx), Math.abs(dy)) !== 1);
  }
  function format(mask){
    var d = (1 << 3) | mask, r = d;                                                    // EC level L = 01
    for (var k = 0; k < 10; k++) r = (r << 1) ^ ((r >>> 9) * 0x537);
    var f = ((d << 10) | r) ^ 0x5412, bit = function(k){ return ((f >>> k) & 1) === 1; };
    for (k = 0; k <= 5; k++) set(8, k, bit(k));
    set(8, 7, bit(6)); set(8, 8, bit(7)); set(7, 8, bit(8));
    for (k = 9; k < 15; k++) set(14 - k, 8, bit(k));
    for (k = 0; k < 8; k++) set(size - 1 - k, 8, bit(k));
    for (k = 8; k < 15; k++) set(8, size - 15 + k, bit(k));
    set(8, size - 8, true);                                                            // the dark module
  }
  format(0);                                                                           // reserve the area

  // Codewords in the zigzag, two columns at a time from the right, skipping the vertical timing column.
  var n = 0;
  for (var right = size - 1; right >= 1; right -= 2){
    if (right === 6) right = 5;
    for (var v = 0; v < size; v++) for (j = 0; j < 2; j++){
      var x = right - j, up = ((right + 1) & 2) === 0, y = up ? size - 1 - v : v;
      if (!fn[y][x] && n < words.length * 8){ m[y][x] = ((words[n >>> 3] >>> (7 - (n & 7))) & 1) === 1; n++; }
    }
  }
  for (y = 0; y < size; y++) for (x = 0; x < size; x++) if (!fn[y][x] && (x + y) % 2 === 0) m[y][x] = !m[y][x];   // mask 0
  format(0);
  return m;
}

function qrSvg(text){
  var m = qrMatrix(text), n = m.length, q = 4, path = "";
  for (var y = 0; y < n; y++) for (var x = 0; x < n; x++) if (m[y][x]) path += "M" + (x + q) + " " + (y + q) + "h1v1h-1z";
  return '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ' + (n + 2 * q) + " " + (n + 2 * q)
    + '" shape-rendering="crispEdges"><rect width="100%" height="100%" fill="#fff"/><path d="' + path + '" fill="#000"/></svg>';
}
