/* English / Traditional Chinese for the control and lyrics pages. Load before theme.js.

   The pages are written in English. T(s) gives the Chinese for an English string (or s itself in English mode, or
   when there is no translation); "{n}" in a key is filled from T's second argument. applyLang() translates the
   page's static text nodes and title / aria-label attributes in place, remembering the English, so switching back
   and forth works; dynamic text goes through T() where the page sets it. Messages from the PC (status, warnings)
   arrive in English and are matched by PATTERNS. The choice is stored as pk_lang, shared by both pages; with
   none stored, a Chinese-language browser starts in Chinese. */
var LANG = (function(){
  try { var l = localStorage.getItem("pk_lang"); if (l === "en" || l === "zh") return l; } catch (e) {}
  return /^zh/i.test(navigator.language || "") ? "zh" : "en";
})();

var ZH = {
  // control page
  "Karaoke": "卡拉OK",
  "Colour scheme": "色彩主題",
  "Language": "語言",
  "Position in the song": "歌曲位置",
  "Shuffle": "隨機播放", "Previous": "上一首", "Play or pause": "播放或暫停", "Next": "下一首",
  "Connecting…": "連線中…",
  "Start karaoke": "開始卡拉OK", "Stop karaoke": "停止卡拉OK",
  "Lyrics screen ↗": "歌詞畫面 ↗",
  "Closing this window stops karaoke: Spotify pauses and goes back to your speakers.":
    "關閉這個視窗即停止卡拉OK：Spotify 會暫停，並回到你的喇叭播放。",
  "Original vocal": "原唱人聲",
  "Remembered for each song, like the key. New songs start at 10%.": "每首歌分別記住，跟升降調一樣。新歌由 10% 開始。",
  "Off": "關", "Guide 40%": "導唱 40%", "Original": "原曲",
  "Key": "升降調", "Reset": "重設",
  "Remembered for each song: it comes back the next time that song plays. New songs start at 0.":
    "每首歌分別記住：下次播同一首歌時自動套用。新歌由 0 開始。",
  "original key": "原調", "semitone": "個半音", "semitones": "個半音",
  "Volume": "音量",
  "Sound": "音效",
  "Changes at once: try them on a song you know. Bass presets never make it louder, so compare by ear.":
    "即時生效：用熟悉的歌試聽。低音預設不會令聲音變大，可以放心憑耳朵比較。",
  "Flat": "原聲", "Less bass": "少低音", "More bass": "多低音", "Clear": "清晰", "Warm": "溫暖", "Party": "派對",
  "Room for your voice": "讓出人聲位置", "Small speakers": "小喇叭", "Late night": "深夜",
  "Microphone": "麥克風", "Microphone (on the Mac)": "麥克風（在 Mac 上）",
  "Off: no microphone on this PC": "關：這部電腦不用麥克風",
  "Mic level": "麥克風音量", "Mic input level": "麥克風輸入音量",
  "Reverb": "殘響", "Small room": "小房間", "Room": "房間", "Hall": "大廳",
  "Echo": "回聲", "Echo level": "回聲音量", "Time": "時間", "Echo time": "回聲時間",
  "Repeats": "重複", "Echo repeats": "回聲重複",
  "Voice": "音色", "Natural": "自然", "Bright": "明亮", "Deep": "渾厚",
  "Smooth": "平滑", "Noise gate": "噪音閘",
  "Evens out loud and soft singing": "拉近大聲與小聲的差距",
  "Quiets the mic between phrases": "唱句之間自動收細麥克風",
  "Reverb is the room around your voice; Echo repeats it, like a karaoke machine. Smooth evens out loud and soft lines; Noise gate quiets the mic between phrases so it picks up less of the speakers.":
    "殘響是聲音周圍的空間感；回聲會把聲音重複，像卡拉OK機一樣。「平滑」拉近大聲與小聲的句子；「噪音閘」在唱句之間收細麥克風，減少收到喇叭的聲音。",
  "The level starts at 0 each time, so it never squeals: raise it slowly, pointing the mic away from the speakers.":
    "音量每次都由 0 開始，以免突然嘯叫：請慢慢推高，麥克風不要對着喇叭。",
  "The level starts at 0 each time so it never squeals. Through the Mac's speakers, keep it low.":
    "音量每次都由 0 開始，以免嘯叫。用 Mac 的喇叭時請保持低音量。",
  "The mic is too loud at its input (the bar turns orange): lower its level in Windows, Settings > System > Sound > Input, then use this slider.":
    "麥克風的輸入太大聲（音量條變橙色）：請先到 Windows「設定 > 系統 > 音效 > 輸入」調低它的音量，再用這個滑桿。",
  "About {n} ms from the mic to the speakers.": "麥克風到喇叭約 {n} 毫秒。",
  "Lyrics timing": "歌詞時間",
  "Minus shows each line earlier, plus later. −0.5 s gives the singer a moment to get ready. Saved for next time.":
    "減號令每句提早顯示，加號延後。−0.5 秒讓歌手有時間準備。會自動儲存。",
  "Engine settings": "引擎設定",
  "Delay": "延遲", "Longer removes the voice more cleanly.": "越長，去人聲越乾淨。",
  "Model": "模型", "A model you have not used yet downloads first (size shown).": "未用過的模型會先下載（已列出大小）。",
  "Speakers or headphones": "喇叭或耳機",
  "Longest, cleanest (~14 s or more)": "最長、最乾淨（約 14 秒或以上）",
  "Karaoke: becruily": "卡拉OK：becruily",
  "Karaoke: anvuew (small download)": "卡拉OK：anvuew（下載較小）",
  "Karaoke: frazer & becruily (fullest backing)": "卡拉OK：frazer & becruily（伴奏最飽滿）",
  "Karaoke: Gabox v2 (least vocal bleed)": "卡拉OK：Gabox v2（人聲殘留最少）",
  "All vocals removed, backing vocals too: Kim (recommended)": "去除所有人聲，連和音：Kim（推薦）",
  "GPU {n}% · underruns {u} · drift skips {d}": "GPU {n}% · 斷音 {u} · 漂移跳過 {d}",
  "Keyboard": "鍵盤",
  "Original vocal ±10%": "原唱人聲 ±10%", "Vocal 10% / 40% guide": "人聲 10%／導唱 40%",
  "Key down / up": "降調／升調", "Back to the original key": "回到原調",
  "Phone remote": "手機遙控",
  "On a phone on the same Wi-Fi, open": "用同一個 Wi-Fi 的手機開啟",
  "or scan the code on the lyrics screen.": "，或掃描歌詞畫面上的二維碼。",
  "Nothing playing on Spotify": "Spotify 沒有播放",
  "Karaoke is off. Spotify plays normally on the PC.": "卡拉OK 已關閉。Spotify 在電腦上照常播放。",
  "Loading the model…": "正在載入模型…",
  "Waiting for Spotify: open the Spotify app on this PC and play a song.": "等待 Spotify：請在這部電腦開啟 Spotify 並播放歌曲。",
  "Playing. What you hear is {n} s behind Spotify.": "播放中。你聽到的比 Spotify 慢 {n} 秒。",
  "Paused. Press play here or in Spotify.": "已暫停。請在這裡或 Spotify 按播放。",
  "Getting ready: the song starts in about {n} s.": "準備中：約 {n} 秒後開始。",
  "Problem": "有問題", "Starting": "啟動中", "Karaoke on": "卡拉OK 進行中", "Paused": "已暫停",
  "Getting ready": "準備中", "Not connected": "未連線",
  "Project Karaoke has stopped. Close this window and open it again from the desktop icon.":
    "Project Karaoke 已停止。請關閉這個視窗，再從桌面圖示開啟。",
  "Can't reach the PC — is it on?": "連不上電腦——電腦開着嗎？",
  // lyrics page
  "Karaoke lyrics": "卡拉OK 歌詞",
  "Connecting to the PC…": "正在連線到電腦…",
  "Karaoke is off.": "卡拉OK 已關閉。",
  "Scan the code to start it from your phone, or open {n}": "掃描二維碼用手機開始，或開啟 {n}",
  "Loading…": "載入中…",
  "Play a song on Spotify.": "請在 Spotify 播放歌曲。",
  "Finding lyrics…": "正在尋找歌詞…",
  "No lyrics found for this song.": "找不到這首歌的歌詞。",
  "The music plays on without the voice. Lyrics come from LRCLIB, a free community library.":
    "音樂照樣去人聲播放。歌詞來自 LRCLIB，一個免費的社群歌詞庫。",
  "Scan to control": "掃描即可遙控",
  "Vocal {n}%": "人聲 {n}%",
  "Key +{n}": "升 {n} 調", "Key −{n}": "降 {n} 調",
  // colour menu (theme.js)
  "Light": "淺色", "Dark": "深色", "Auto": "自動", "Song colour": "歌曲顏色",
  "follows the device": "跟隨裝置", "warm · default": "暖色 · 預設", "warm · coffee": "暖色 · 咖啡",
  "cool · classic": "冷色 · 經典", "green · calm": "綠色 · 平靜", "mint · fresh": "薄荷 · 清新",
  "blue · clean": "藍色 · 簡潔", "rose · gentle": "玫瑰 · 柔和", "teal · modern": "青色 · 現代",
  "neutral": "中性", "blue": "藍色", "wine": "酒紅", "like Spotify": "像 Spotify"
};

// Messages composed on the PC (karaoke/session.py, pipeline.py), with their variable parts.
var PATTERNS = [
  [/^Loading the model\.\.\.$/, "正在載入模型…"],
  [/^Downloading the model \((.+), first time only\)\. This can take a few minutes\.\.\.$/, "正在下載模型（$1，只限第一次），需時數分鐘…"],
  [/^Buffering ~(\d+) s\.\.\.$/, "緩衝中，約 $1 秒…"],
  [/^Stopped\.$/, "已停止。"],
  [/^Vocal removal failed: (.*) Press Stop, then Start\.$/, "去人聲失敗：$1 請按停止，再按開始。"],
  [/^Could not list the audio devices: (.*)$/, "無法列出音訊裝置：$1"],
  [/^VB-Audio Virtual Cable not found: install it from vb-audio\.com and reboot\.$/, "找不到 VB-Audio Virtual Cable：請從 vb-audio.com 安裝，然後重新開機。"],
  [/^No speakers or headphones found\. Connect some, then press Start again\.$/, "找不到喇叭或耳機。請接上，再按開始。"],
  [/^Another Project Karaoke \(window or helper\) is already running the engine\.$/, "另一個 Project Karaoke（視窗或背景助手）已在運行引擎。"],
  [/^Could not open Spotify: (.*)$/, "無法開啟 Spotify：$1"],
  [/^Failed to load model: (.*)$/, "載入模型失敗：$1"],
  [/^Failed to open audio: (.*)$/, "無法開啟音訊裝置：$1"],
  [/^(.+) is not connected\. Plug it in, then press Stop karaoke and Start karaoke\.$/, "$1 未連接。請插上，然後按「停止卡拉OK」再按「開始卡拉OK」。"],
  [/^Could not open the microphone: (.*)$/, "無法開啟麥克風：$1"],
  [/^Spotify won't move to the cable, so you hear the original song\. Close Spotify completely \(tray icon > Quit\) and open it again\.$/,
    "Spotify 無法轉到虛擬音效線，所以你聽到的是原曲。請完全關閉 Spotify（系統匣圖示 > Quit），再重新開啟。"],
  [/^Could not route Spotify to the cable: (.*)$/, "無法把 Spotify 轉到虛擬音效線：$1"],
  [/^Windows' default output is the cable, so the volume keys do nothing\. Settings > System > Sound > Output: pick your speakers\.$/,
    "Windows 的預設輸出是虛擬音效線，所以音量鍵無效。請到「設定 > 系統 > 音效 > 輸出」選擇你的喇叭。"],
  [/^~([\d.]+) s$/, "約 $1 秒"]
];

function T(s, n, extra){
  if (s == null) return s;
  var out = s;
  if (LANG === "zh"){
    if (Object.prototype.hasOwnProperty.call(ZH, s)) out = ZH[s];
    else for (var i = 0; i < PATTERNS.length; i++) if (PATTERNS[i][0].test(s)){ out = s.replace(PATTERNS[i][0], PATTERNS[i][1]); break; }
  }
  if (n !== undefined) out = out.replace("{n}", n);
  if (extra) for (var k in extra) out = out.replace("{" + k + "}", extra[k]);
  return out;
}

/* Translate the static page: every text node (skipping scripts, selects and anything inside [data-noi18n]) and
   the title / aria-label attributes. The English is kept on the node, so this can run again after a switch. */
function applyLang(){
  document.documentElement.lang = LANG === "zh" ? "zh-Hant" : "en";
  var walk = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT, {acceptNode: function(n){
    var p = n.parentNode;
    if (!n.nodeValue.trim() || p.closest("script,style,select,[data-noi18n]")) return NodeFilter.FILTER_REJECT;
    return NodeFilter.FILTER_ACCEPT;
  }});
  var node;
  while ((node = walk.nextNode())){
    if (node._en === undefined) node._en = node.nodeValue;
    var key = node._en.replace(/\s+/g, " ").trim();
    var tr = T(key);
    node.nodeValue = tr === key ? node._en : node._en.replace(/\S[\s\S]*\S|\S/, tr);
  }
  document.querySelectorAll("[title],[aria-label]").forEach(function(el){
    ["title", "aria-label"].forEach(function(a){
      if (!el.hasAttribute(a)) return;
      var k = "en_" + a.replace("-", "_");
      if (el.dataset[k] === undefined) el.dataset[k] = el.getAttribute(a);
      el.setAttribute(a, T(el.dataset[k]));
    });
  });
  var tt = document.querySelector("title");
  if (tt){ if (tt._en === undefined) tt._en = tt.textContent; tt.textContent = T(tt._en); }
  document.querySelectorAll(".lang-toggle button").forEach(function(b){ b.classList.toggle("on", b.dataset.lang === LANG); });
}

/* The EN / 中文 pill. onChange runs after the page is re-translated, so dynamic text can be redrawn. */
function initLang(onChange){
  document.querySelectorAll(".lang-toggle button").forEach(function(b){
    b.onclick = function(){
      LANG = b.dataset.lang;
      try { localStorage.setItem("pk_lang", LANG); } catch (e) {}
      applyLang();
      if (onChange) onChange();
    };
  });
  applyLang();
}
