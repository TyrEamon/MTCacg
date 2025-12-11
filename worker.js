export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const path = url.pathname;

    // ==========================================
    // 🧠 新增：云端记忆 API (供 Python Bot 使用)
    // ==========================================
    
    // API: 获取历史记录
    if (path === '/api/get_history') {
      const history = await env.KV.get('pixiv_history');
      return new Response(history || '');
    }

    // API: 更新历史记录
    if (path === '/api/update_history') {
      if (request.method !== 'POST') return new Response('Method not allowed', {status: 405});
      const newHistory = await request.text();
      await env.KV.put('pixiv_history', newHistory);
      return new Response('OK');
    }
    // ==========================================

    if (path.startsWith('/image/')) {
      const fileId = path.replace('/image/', '');
      return await proxyTelegramImage(fileId, env.BOT_TOKEN);
    }

    if (path === '/api/posts') {
      const q = url.searchParams.get('q');
      const offset = url.searchParams.get('offset') || 0;
      if (q === 'random') {
        const { results } = await env.DB.prepare("SELECT * FROM images ORDER BY RANDOM() LIMIT 1").all();
        return new Response(JSON.stringify(results), { headers: { 'Content-Type': 'application/json' }});
      }
      
      let sql = q 
        ? `SELECT * FROM images WHERE tags LIKE ? OR caption LIKE ? ORDER BY created_at DESC LIMIT 20 OFFSET ?`
        : `SELECT * FROM images ORDER BY created_at DESC LIMIT 20 OFFSET ?`;
      const params = q ? [`%${q}%`, `%${q}%`, offset] : [offset];
      
      try {
        const { results } = await env.DB.prepare(sql).bind(...params).all();
        return new Response(JSON.stringify(results), { headers: { 'Content-Type': 'application/json' }});
      } catch (e) { return new Response(JSON.stringify([]), {status: 500}); }
    }

    if (path.match(/^\/detail\/(.+)$/)) return await handleDetail(path.match(/^\/detail\/(.+)$/)[1], env);
    if (path === '/about') return new Response(htmlAbout(), {headers: {'Content-Type': 'text/html;charset=UTF-8'}});
    
    return new Response(htmlHome(), { headers: { 'Content-Type': 'text/html;charset=UTF-8' }});
  }
};

async function proxyTelegramImage(fileId, botToken) {
  try {
    const r1 = await fetch(`https://api.telegram.org/bot${botToken}/getFile?file_id=${fileId}`);
    const j1 = await r1.json();
    if (!j1.ok) return new Response('404', {status: 404});
    const r2 = await fetch(`https://api.telegram.org/file/bot${botToken}/${j1.result.file_path}`);
    const h = new Headers(r2.headers);
    h.set('Cache-Control', 'public, max-age=31536000, immutable');
    h.set('Access-Control-Allow-Origin', '*');
    return new Response(r2.body, { headers: h });
  } catch { return new Response('Error', {status: 500}); }
}

const SIDEBAR_HTML = `
  <div id="overlay" onclick="toggleSidebar()" class="fixed inset-0 bg-black/60 z-40 hidden transition-opacity opacity-0" style="will-change: opacity;"></div>
  <aside id="sidebar" class="fixed top-0 left-0 w-72 h-full bg-[#1a1a1a]/95 border-r border-white/10 z-50 transform -translate-x-full transition-transform duration-300 ease-out shadow-2xl flex flex-col" style="will-change: transform;">
    <div class="p-6 border-b border-white/10 flex items-center justify-between">
      <h2 class="text-2xl font-bold bg-gradient-to-r from-pink-500 to-purple-500 bg-clip-text text-transparent">MtcACG</h2>
      <button onclick="toggleSidebar()" class="text-gray-400 hover:text-white">✕</button>
    </div>
    <nav class="flex-1 p-4 space-y-2">
    <!-- 首页 -->
    <a href="/" class="flex items-center p-3 text-gray-300 hover:bg-white/10 rounded-lg transition">
      <svg class="w-5 h-5 mr-3 text-gray-300" fill="none" stroke="currentColor" stroke-width="1.8" viewBox="0 0 24 24">
        <path stroke-linecap="round" stroke-linejoin="round" d="M3 11.5L12 4l9 7.5M5 10.5V20h5v-5h4v5h5v-9.5" />
      </svg>
      <span>首页</span>
    </a>
    
    <!-- 随机抽图 -->
    <a href="#" onclick="randomImage()" class="flex items-center p-3 text-gray-300 hover:bg-white/10 rounded-lg transition">
      <svg class="w-5 h-5 mr-3 text-gray-300" fill="none" stroke="currentColor" stroke-width="1.8" viewBox="0 0 24 24">
        <path stroke-linecap="round" stroke-linejoin="round" d="M4 6h4l3 6 3-6h4M4 18h4l3-6 3 6h4" />
      </svg>
      <span>随机抽图看看0w0</span>
    </a>
    
    <!-- 关于 -->
    <a href="/about" class="flex items-center p-3 text-gray-300 hover:bg-white/10 rounded-lg transition">
      <svg class="w-5 h-5 mr-3 text-gray-300" fill="none" stroke="currentColor" stroke-width="1.8" viewBox="0 0 24 24">
        <circle cx="12" cy="12" r="9" stroke-linecap="round" stroke-linejoin="round" />
        <path stroke-linecap="round" stroke-linejoin="round" d="M12 8.5v.01M11 11h1v5h1" />
      </svg>
      <span>关于本站</span>
    </a>    
      <div class="pt-4 mt-4 border-t border-white/10">
        <div class="flex items-center justify-between p-3">
          <span class="text-gray-300 flex items-center"><span class="mr-3">🔞</span> R18 哒咩~</span>
          <label class="relative inline-flex items-center cursor-pointer">
            <input type="checkbox" id="r18-toggle" class="sr-only peer" onchange="toggleR18(this)">
            <div class="w-11 h-6 bg-gray-600 peer-focus:outline-none rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-5 after:w-5 after:transition-all peer-checked:bg-pink-600"></div>
          </label>
        </div>
      </div>
      <div class="pt-4 mt-4 border-t border-white/10">
        <p class="px-3 text-xs font-bold text-gray-500 uppercase mb-2">Friends</p>
        <a href="https://github.com/TyrEamon/MTCacg" target="_blank" class="flex items-center p-3 text-gray-400 hover:text-white hover:bg-white/5 rounded-lg text-sm">
           🔗 GitHub
        </a>
      </div>
    </nav>
    <div class="p-4 text-xs text-center text-gray-600 border-t border-white/5">
      © 2025 MtcACG Gallery
    </div>
  </aside>
  <script>
    function toggleSidebar() {
      const sb = document.getElementById('sidebar');
      const ov = document.getElementById('overlay');
      const isOpen = !sb.classList.contains('-translate-x-full');
      if (isOpen) {
        sb.classList.add('-translate-x-full');
        ov.classList.remove('opacity-100');
        setTimeout(() => ov.classList.add('hidden'), 300);
      } else {
        ov.classList.remove('hidden');
        void ov.offsetWidth; 
        ov.classList.add('opacity-100');
        sb.classList.remove('-translate-x-full');
      }
    }
    async function randomImage() {
      toggleSidebar();
      const res = await fetch('/api/posts?q=random');
      const data = await res.json();
      if(data.length) window.location.href = '/detail/' + data[0].id;
    }
    function toggleR18(el) {
      localStorage.setItem('hide_r18', el.checked);
      location.reload(); 
    }
    if(localStorage.getItem('hide_r18') === 'true') document.getElementById('r18-toggle').checked = true;
  </script>
`;

async function handleDetail(id, env) {
  const img = await env.DB.prepare("SELECT * FROM images WHERE id = ?").bind(id).first();
  if (!img) return new Response("404", { status: 404 });

  const { results: related } = await env.DB.prepare("SELECT * FROM images WHERE id != ? ORDER BY RANDOM() LIMIT 4").bind(id).all();
  const imageUrl = `/image/${img.file_name}`; 
  const title = (img.caption || 'Untitled').split('\n')[0];
  const tagsHtml = (img.tags || '').split(' ').map(t => `<a href="/?q=${t}" class="tag-pill">#${t}</a>`).join(' ');

  return new Response(`
<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>${title} - MtcACG</title>
<link rel="icon" type="image/png" href="https://pub-d07d03b8c35d40309ce9c6d8216e885b.r2.dev/ACGg.png">
<script src="https://cdn.tailwindcss.com"></script>
<style>
  .tag-pill { background: rgba(255,255,255,0.1); padding: 4px 10px; border-radius: 20px; font-size: 12px; color: #f9a8d4; transition: all 0.2s; }
  .tag-pill:hover { background: #ec4899; color: white; }
  .bg-dynamic { 
    position: fixed; top: 0; left: 0; width: 100vw; height: 100vh;
    z-index: -1; background-image: url('${imageUrl}'); background-size: cover; background-position: center; 
    filter: blur(5px) brightness(0.6); transform: scale(1.1) translateZ(0); 
    will-change: transform; pointer-events: none;
  }
</style>
</head>
<body class="text-gray-100 min-h-screen font-sans overflow-x-hidden">
  <div class="bg-dynamic"></div>
  ${SIDEBAR_HTML}
  <div class="container mx-auto px-4 py-6 max-w-6xl relative z-10">
    <div class="flex items-center justify-between mb-8">
      <button onclick="toggleSidebar()" class="p-2 text-white/80 hover:text-white hover:bg-white/10 rounded-lg">
        <svg class="w-7 h-7" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 6h16M4 12h16M4 18h16"></path></svg>
      </button>
      <a href="/" class="text-xl font-bold tracking-widest text-white/90">MtcACG</a>
      <div class="w-8"></div>
    </div>
    <div class="grid grid-cols-1 lg:grid-cols-3 gap-8">
    <div class="lg:col-span-2 bg-black/40 backdrop-blur-md rounded-2xl overflow-hidden border border-white/10 shadow-2xl flex items-center justify-center p-2">
    <img src="${imageUrl}" class="max-h-[85vh] w-auto object-contain rounded-lg">
  </div>   
      <div class="lg:col-span-1 space-y-6">
        <div class="bg-black/40 backdrop-blur-md rounded-2xl p-8 border border-white/10">
          <h1 class="text-2xl font-bold text-white mb-2 leading-snug shadow-black drop-shadow-lg">${title}</h1>
          <div class="text-sm text-gray-400 font-mono mb-6">ID: ${img.id}</div>
          <div class="flex flex-wrap gap-2 mb-8">${tagsHtml}</div>
          <a href="${imageUrl}" download class="block w-full py-3 bg-gradient-to-r from-pink-600 to-purple-600 hover:from-pink-500 hover:to-purple-500 text-white text-center font-bold rounded-xl shadow-lg transform hover:-translate-y-0.5 transition-all">Download Original</a>
        </div>
        <div class="grid grid-cols-2 gap-4">
           ${related.map(r => `
              <a href="/detail/${r.id}" class="block aspect-square rounded-xl overflow-hidden border border-white/10 hover:border-pink-500 transition-all relative group">
                <img src="/image/${r.file_name}" class="w-full h-full object-cover">
                <div class="absolute inset-0 bg-black/20 group-hover:bg-transparent transition-colors"></div>
              </a>
            `).join('')}
        </div>
      </div>
    </div>
  </div>
</body>
</html>
  `, { headers: { "Content-Type": "text/html" } });
}

function htmlHome() {
  return `
<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>MtcACG</title>
<link rel="icon" type="image/png" href="https://pub-d07d03b8c35d40309ce9c6d8216e885b.r2.dev/ACGg.png">
<style>
body { margin: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background: #121212; color: #fff; overflow-x: hidden; }

/* --- 性能优化版 CSS --- */

#bg-layer { 
  position: fixed; top: 0; left: 0; width: 100vw; height: 100vh; z-index: -1; 
  background-size: cover; background-position: center; 
  /* 优化1：模糊度调低，减轻显卡负担 */
  filter: blur(6px) brightness(0.6); 
  transition: opacity 1s; opacity: 0; 
  /* 开启硬件加速 */
  transform: translate3d(0,0,0); 
  will-change: opacity; 
  pointer-events: none;
}

.card { 
  break-inside: avoid; 
  margin-bottom: 12px;   
  border-radius: 12px;   
  overflow: hidden; 
  background: #2a2a2a; 
  display: inline-block; 
  width: 100%; 
  position: relative; 
  transition: transform 0.2s ease-out; /* 动画时间改短一点更跟手 */
  box-shadow: 0 4px 6px rgba(0,0,0,0.3); 
  
  /* 优化3：提示浏览器优化渲染 */
  will-change: transform;
}

.header { position: sticky; top: 0; z-index: 30; background: rgba(0,0,0,0.3); backdrop-filter: blur(20px); border-bottom: 1px solid rgba(255,255,255,0.1); padding: 12px 16px; display: flex; align-items: center; justify-content: space-between; }
.logo { font-weight: 800; font-size: 18px; letter-spacing: 1px; color: #fff; text-decoration: none; }
.search-bar { flex: 1; max-width: 400px; margin: 0 16px; position: relative; }
input { background: rgba(255,255,255,0.1); border: 1px solid rgba(255,255,255,0.1); color: white; padding: 8px 16px; border-radius: 99px; width: 100%; outline: none; transition: 0.3s; font-size: 14px; }
input:focus { background: rgba(0,0,0,0.6); border-color: #ec4899; }

/* ========================================= */
/* 📱 手机端：原生瀑布流 (最适合手机) */
/* ========================================= */
.gallery-container {
  display: block;
  column-count: 2;
  column-gap: 8px;
  padding: 8px;
}

.card {
  break-inside: avoid;
  margin-bottom: 12px;
  border-radius: 12px;
  overflow: hidden;
  background: #2a2a2a;
  position: relative;
  transition: transform 0.2s;
  box-shadow: 0 4px 6px rgba(0,0,0,0.3);
}

.card:active { transform: scale(0.98); }
.card:hover { transform: scale(1.02) translateY(-5px); z-index: 20; box-shadow: 0 25px 30px -10px rgba(0,0,0,0.6); }

.card img {
  width: 100%;
  height: auto; /* 手机端自适应高度 */
  display: block;
  background: #222;
  transition: opacity 0.3s;
}

/* ========================================= */
/* 💻 电脑端 (宽于 768px)：启用错落砖墙布局 */
/* ========================================= */
@media(min-width: 768px) {
  .gallery-container {
    display: grid;
    /* 基础格子宽约 250px，自动填充 */
    grid-template-columns: repeat(auto-fill, minmax(250px, 1fr));
    grid-auto-rows: 250px;
    grid-auto-flow: dense; /* 自动填补空隙 */
    gap: 16px;
    padding: 20px;
    max-width: 1800px;
    margin: 0 auto;
    
    /* 重置瀑布流属性 */
    column-count: auto;
    width: auto;
  }

  .card {
    margin-bottom: 0;
    grid-column: span 1;
    grid-row: span 1;
    box-shadow: 0 4px 10px rgba(0,0,0,0.3);
  }

  /* --- 🎲 魔法：让电脑端图片有大有小 --- */
  /* 每 5 张出现一个大方块 (2x2) */
  .card:nth-child(5n) { grid-column: span 2; grid-row: span 2; }
  
  /* 每 7 张出现一个横条 (2x1) */
  .card:nth-child(7n) { grid-column: span 2; }
  
  /* 每 9 张出现一个竖条 (1x2) */
  .card:nth-child(9n) { grid-row: span 2; }

  .card img {
    height: 100%;
    object-fit: cover; /* 电脑端必须裁剪，否则对不齐 */
  }
}

.meta { position: absolute; bottom: 0; left: 0; right: 0; padding: 40px 10px 10px; background: linear-gradient(to top, rgba(0,0,0,0.9), transparent); opacity: 0; transition: 0.2s; }
.card:hover .meta { opacity: 1; }

/* 手机端：为了不遮挡小图，默认隐藏标题，只有点击进去才看标题，或者只显示一点点 */
@media(max-width: 768px) {
  .meta { padding: 20px 8px 8px; opacity: 1; } /* 手机端常驻显示标题背景 */
  .title { font-size: 11px; }
  .card { margin-bottom: 8px; border-radius: 8px; } /* 手机端卡片更紧凑 */
}

.title { font-size: 13px; font-weight: 600; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; text-shadow: 0 1px 2px black; }
.menu-btn { cursor: pointer; padding: 6px; }
</style>
<script src="https://cdn.tailwindcss.com"></script>
</head>
<body>
<div id="bg-layer"></div>
${SIDEBAR_HTML}
<div class="header">
  <div class="menu-btn" onclick="toggleSidebar()">
    <svg width="24" height="24" fill="none" stroke="white" stroke-width="2" stroke-linecap="round"><path d="M3 12h18M3 6h18M3 18h18"/></svg>
  </div>
  <div class="search-bar"><input type="text" id="search" placeholder=" 要搜索什么吖...." onchange="doSearch(this.value)"></div>
  <a href="/" class="logo">MtcACG</a>
</div>
<!-- 📱手机瀑布流 + 💻电脑砖墙 -->
<div class="gallery-container" id="g"></div>
<div id="status" class="text-center py-10 text-gray-500 text-sm">Loading...</div>
<script>
let offset = 0; let q = ''; let isLoading = false;
const hideR18 = localStorage.getItem('hide_r18') === 'true';

async function load(reset = false) {
  if (isLoading) return;
  isLoading = true;
  if(reset) { document.getElementById('g').innerHTML=''; offset=0; }
  try {
    const url = \`/api/posts?offset=\${offset}&q=\${encodeURIComponent(q)}\`;
    const res = await fetch(url);
    const data = await res.json();
    if (data.length === 0 && offset === 0) { document.getElementById('status').innerText = 'Nothing here...'; return; }
    if (offset === 0 && data.length > 0) {
      const bg = document.getElementById('bg-layer');
      bg.style.backgroundImage = \`url(/image/\${data[0].file_name})\`;
      bg.style.opacity = 1;
    }
    let html = '';
    data.forEach(item => {
      if (hideR18 && (item.tags||'').includes('R-18')) return;
      const title = (item.caption || '').split('\\n')[0];
      html += \`
        <a href="/detail/\${item.id}" class="card">
          <img src="/image/\${item.file_name}" loading="lazy" onload="this.style.background='transparent'">
          <div class="meta"><div class="title">\${title}</div></div>
        </a>\`;
    });
    document.getElementById('g').insertAdjacentHTML('beforeend', html);
    offset += data.length;
    if(data.length === 0) document.getElementById('status').innerText = 'No more images';
    else document.getElementById('status').style.display = 'none';
  } catch(e) { console.error(e); }
  isLoading = false;
}
function doSearch(val) { q = val; load(true); }
window.onscroll = () => { if ((window.innerHeight + scrollY) >= document.body.offsetHeight - 1000) load(); };
load();
</script>
</body>
</html>`;
}

function htmlAbout() {
  return `
  <!DOCTYPE html>
  <html class="dark">
  <head>
    <meta charset="UTF-8"><meta name="viewport" content="width=device-width">
    <script src="https://cdn.tailwindcss.com"></script>
    <link rel="icon" href="data:image/svg+xml,<svg xmlns=%22http://www.w3.org/2000/svg%22 viewBox=%220 0 100 100%22><text y=%22.9em%22 font-size=%2290%22>🌸</text></svg>">
    <style>
      /* 动态背景 */
      #bg-layer { 
        position: fixed; top: 0; left: 0; width: 100vw; height: 100vh; z-index: -1; 
        background-size: cover; background-position: center; 
        filter: blur(3px) brightness(0.6); 
        transition: opacity 1s; opacity: 0; 
        transform: translate3d(0,0,0); will-change: opacity; pointer-events: none;
      }
      
      /* 隐藏滚动条但允许滚动 */
      .no-scrollbar::-webkit-scrollbar { display: none; }
      .no-scrollbar { -ms-overflow-style: none; scrollbar-width: none; }
      
      /* 玻璃板内的文字排版 */
      .content-box h2 { font-size: 1.25rem; font-weight: 700; margin-bottom: 1rem; color: #fff; border-left: 4px solid #ec4899; padding-left: 12px; margin-top: 2rem; }
      .content-box p { margin-bottom: 1rem; line-height: 1.7; color: #e5e7eb; }
      .content-box code { background: rgba(255,255,255,0.15); padding: 2px 6px; border-radius: 4px; font-family: monospace; color: #f9a8d4; }
      .content-box a { color: #f472b6; text-decoration: underline; text-underline-offset: 4px; transition: color 0.2s; }
      .content-box a:hover { color: #fff; }
    </style>
  </head>
  <body class="bg-gray-900 text-white min-h-screen flex items-center justify-center p-4">
    <div id="bg-layer"></div>
    ${SIDEBAR_HTML}
    
    <!-- 🟢 玻璃板容器：bg-black/30 (更透明), backdrop-blur-md (磨砂感) -->
    <div class="max-w-2xl w-full bg-black/20 backdrop-blur-md p-6 rounded-2xl shadow-2xl relative border border-white/10 content-box h-[85vh] overflow-y-auto no-scrollbar">
       
       <!-- 顶部栏 (透明背景) -->
       <div class="flex items-center justify-between mb-6 sticky top-0 z-10 py-2 border-b border-white/10" style="background: rgba(0,0,0,0.01); backdrop-filter: blur(1px);">
         <div class="flex items-center gap-4">
           <button onclick="toggleSidebar()" class="text-gray-300 hover:text-white transition p-1">☰</button>
           <h1 class="text-2xl font-bold bg-gradient-to-r from-pink-500 to-purple-500 bg-clip-text text-transparent">关于 MtcACG</h1>
         </div>
         <a href="/" class="text-xs bg-white/10 hover:bg-white/20 px-3 py-1.5 rounded-full transition border border-white/5">回到首页</a>
       </div>

       <!-- 序言 -->
       <section>
         <h2 class="text-xl font-medium text-white mb-3 flex items-center">
           <span class="w-1 h-6 bg-pink-500 rounded-full mr-3 opacity-80"></span>
           序言 · Prologue
         </h2>
         <p>
           在互联网的浩瀚烟海中，MtcACG 只是一个静谧的角落。
         </p>
         <p class="mt-2">
           这里没有喧嚣的爬虫，没有算法的裹挟。每一张图片，都来自我个人的凝视与收藏。它们或许是某个深夜的惊鸿一瞥，或许是某段记忆的色彩切片。我将它们安放于此，像是在数字世界里搭建了一座私人的空中花园。
         </p>
       </section>
     
       <!-- 功能 -->
       <section>
         <h2 class="text-xl font-medium text-white mb-3 flex items-center">
           <span class="w-1 h-6 bg-purple-500 rounded-full mr-3 opacity-80"></span>
           探索 · Explore
         </h2>
         <p>
           你可以通过 <code>#标签</code> 追寻线索，或是在 <code>瀑布流</code> 中随波逐流。
           这里还藏着一把通往里世界的钥匙 —— 点击左上角菜单，你可以开启或关闭 <strong>R-18 滤镜</strong>。请在这个静谧的空间里，保持一份得体的优雅。
         </p>
       </section>
     
       <!-- 接口 -->
       <section>
         <h2 class="text-xl font-medium text-white mb-3 flex items-center">
           <span class="w-1 h-6 bg-blue-500 rounded-full mr-3 opacity-80"></span>
           联结 · Connect
         </h2>
         <p>
           如果你也是一位孤独的收集者，想要与这座花园建立某种数字联结，我留下了一个简单的接口：
           <br>
           <code class="text-sm bg-white/5 px-2 py-1 rounded mt-2 inline-block font-mono text-pink-300">/api/posts?q=random</code>
           <br>
           它会随机赠予你一张此时此刻的风景。
         </p>
       </section>
     
       <!-- 尾声 -->
       <section>
         <h2 class="text-xl font-medium text-white mb-3 flex items-center">
           <span class="w-1 h-6 bg-gray-500 rounded-full mr-3 opacity-80"></span>
           寄语 · Epilogue
         </h2>
         <p>
           如果你在这里找到了共鸣，或是有话想对我说，欢迎通过 <a href="https://t.me/yourname" class="text-pink-400 hover:text-white transition-colors border-b border-pink-400/30 hover:border-white pb-0.5">Telegram</a> 投递信件。
         </p>
         <p class="mt-4 text-sm opacity-60">
           愿你在这里，捡拾到属于你的那一片颜料。
         </p>
       </section>

       <div class="mt-12 pt-8 border-t border-white/10 text-center text-xs text-gray-400">
         © 2025 MtcACG Gallery | Powered by Cloudflare Workers
       </div>

    </div>

    <script>
      (async function() {
        try {
          const res = await fetch('/api/posts?q=random');
          const data = await res.json();
          if (data.length > 0) {
            const bg = document.getElementById('bg-layer');
            bg.style.backgroundImage = \`url(/image/\${data[0].file_name})\`;
            bg.style.opacity = 1;
          }
        } catch(e) {}
      })();
    </script>
  </body>
  </html>`;
}


