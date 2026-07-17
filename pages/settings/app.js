/* 视频解析插件 - 设置页逻辑 */

const bridge = window.AstrBotPluginPage;

const $ = (id) => document.getElementById(id);

/* ---- Config field mapping ---- */
const CONFIG_FIELDS = {
  // General
  auto_parse:           { id: "auto_parse",            type: "bool",   default: true },
  send_video_file:      { id: "send_video_file",       type: "bool",   default: true },
  cache_enabled:        { id: "cache_enabled",         type: "bool",   default: true },
  max_video_size_mb:    { id: "max_video_size_mb",     type: "int",    default: 50 },
  request_timeout:      { id: "request_timeout",       type: "int",    default: 30 },
  cache_ttl_minutes:    { id: "cache_ttl_minutes",     type: "int",    default: 30 },
  // Platform switches
  platform_douyin:      { id: "platform_douyin",       configPath: "enabled_platforms.douyin",  type: "bool", default: true },
  platform_bilibili:    { id: "platform_bilibili",     configPath: "enabled_platforms.bilibili", type: "bool", default: true },
  platform_xiaohongshu: { id: "platform_xiaohongshu",  configPath: "enabled_platforms.xiaohongshu", type: "bool", default: true },
  platform_twitter:     { id: "platform_twitter",      configPath: "enabled_platforms.twitter", type: "bool", default: true },
  // Cookies & APIs
  twitter_cookies:      { id: "twitter_cookies",       type: "text",  default: "" },
  xiaohongshu_cookies:  { id: "xiaohongshu_cookies",   type: "text",  default: "" },
  douyin_api_url:       { id: "douyin_api_url",        type: "text",  default: "" },
};

/* ---- Tab Switching ---- */
let currentTab = "general";

document.querySelectorAll(".tab").forEach((tab) => {
  tab.addEventListener("click", () => {
    const target = tab.dataset.tab;
    if (target === currentTab) return;
    currentTab = target;

    document.querySelectorAll(".tab").forEach((t) => {
      t.classList.toggle("active", t.dataset.tab === target);
      t.setAttribute("aria-selected", t.dataset.tab === target ? "true" : "false");
    });
    document.querySelectorAll(".tab-panel").forEach((p) => {
      p.classList.toggle("active", p.dataset.panel === target);
    });

    if (target === "stats") loadStats();
  });
});

/* ---- Status Toast ---- */
function setStatus(text, cls) {
  const el = $("save-status");
  el.textContent = text;
  el.className = "status-badge " + (cls || "");
}

/* ---- Load config from bridge ---- */
async function loadConfig() {
  try {
    const config = await bridge.apiGet("config");
    for (const [key, field] of Object.entries(CONFIG_FIELDS)) {
      const el = $(field.id);
      if (!el) continue;
      const path = field.configPath || key;
      const value = getNested(config, path, field.default);
      if (field.type === "bool") {
        el.checked = !!value;
      } else if (field.type === "int") {
        el.value = value ?? field.default;
      } else {
        el.value = value ?? "";
      }
    }
  } catch (e) {
    console.error("加载配置失败:", e);
    setStatus("加载失败", "error");
  }
}

/* ---- Save config ---- */
async function saveConfig() {
  setStatus("保存中...", "saving");
  try {
    const config = await bridge.apiGet("config");
    for (const [key, field] of Object.entries(CONFIG_FIELDS)) {
      const el = $(field.id);
      if (!el) continue;
      const path = field.configPath || key;
      let value;
      if (field.type === "bool") {
        value = el.checked;
      } else if (field.type === "int") {
        value = parseInt(el.value, 10) || field.default;
      } else {
        value = el.value || "";
      }
      setNested(config, path, value);
    }
    const response = await bridge.apiPost("config", config);
    setStatus(response.message || "已保存", "saved");
    setTimeout(() => setStatus("已加载", ""), 2000);
  } catch (e) {
    console.error("保存失败:", e);
    setStatus("保存失败: " + e.message, "error");
  }
}

/* ---- Nested config helpers ---- */
function getNested(obj, path, defaultValue) {
  const keys = path.split(".");
  let current = obj;
  for (const key of keys) {
    if (current == null || typeof current !== "object") return defaultValue;
    current = current[key];
  }
  return current !== undefined ? current : defaultValue;
}

function setNested(obj, path, value) {
  const keys = path.split(".");
  let current = obj;
  for (let i = 0; i < keys.length - 1; i++) {
    if (!current[keys[i]] || typeof current[keys[i]] !== "object") {
      current[keys[i]] = {};
    }
    current = current[keys[i]];
  }
  current[keys[keys.length - 1]] = value;
}

/* ---- Manual Test ---- */
$("btn-parse").addEventListener("click", async () => {
  const url = $("url-input").value.trim();
  const platform = $("platform-select").value;
  if (!url) return;

  $("btn-parse").disabled = true;
  $("btn-parse").textContent = "解析中...";

  try {
    const result = await bridge.apiPost("parse", { url, platform: platform || undefined });
    $("result-container").style.display = "block";
    $("result-content").textContent = JSON.stringify(result, null, 2);
  } catch (e) {
    $("result-container").style.display = "block";
    $("result-content").textContent = "解析失败: " + e.message;
  } finally {
    $("btn-parse").disabled = false;
    $("btn-parse").textContent = "解析";
  }
});

$("url-input").addEventListener("keydown", (e) => {
  if (e.key === "Enter") $("btn-parse").click();
});

/* ---- Stats ---- */
const PLATFORM_COLORS = {
  "抖音": "color-dy", "B站": "color-bl",
  "小红书": "color-xhs",
  "X(Twitter)": "color-tw",
};

async function loadStats() {
  try {
    const [stats, cacheStatus] = await Promise.all([
      bridge.apiGet("stats"),
      bridge.apiGet("cache_status"),
    ]);
    renderStats(stats, cacheStatus);
  } catch (e) {
    console.error("加载统计失败:", e);
  }
}

function renderStats(stats, cache) {
  const total = stats.total || 0;
  const errors = stats.errors || 0;
  const successRate = total > 0 ? ((total - errors) / total * 100).toFixed(1) : "0.0";

  // Summary cards
  $("summary-grid").innerHTML = `
    <div class="stat-card">
      <div class="value">${total}</div>
      <div class="label">总解析次数</div>
    </div>
    <div class="stat-card">
      <div class="value">${errors}</div>
      <div class="label">失败次数</div>
    </div>
    <div class="stat-card">
      <div class="value">${successRate}%</div>
      <div class="label">成功率</div>
    </div>
    <div class="stat-card">
      <div class="value">${cache.active || 0}</div>
      <div class="label">活跃缓存</div>
    </div>
  `;

  // Platform distribution
  const platforms = stats.platforms || {};
  const maxVal = Math.max(1, ...Object.values(platforms));
  let chartHTML = "";
  for (const [name, count] of Object.entries(platforms)) {
    const pct = Math.round(count / maxVal * 100);
    const colorCls = PLATFORM_COLORS[name] || "color-dy";
    chartHTML += `
      <div class="bar-row">
        <span class="bar-label">${name}</span>
        <div class="bar-track">
          <div class="bar-fill ${colorCls}" style="width:${pct}%"></div>
        </div>
        <span class="bar-count">${count}</span>
      </div>`;
  }
  if (!chartHTML) {
    chartHTML = '<div class="empty-state">暂无解析记录</div>';
  }
  $("platform-chart").innerHTML = chartHTML;

  // Cache info
  $("cache-info").innerHTML = `
    <div class="info-row">
      <span class="info-label">缓存状态</span>
      <span class="info-value">${cache.cache_enabled ? "已启用" : "已禁用"}</span>
    </div>
    <div class="info-row">
      <span class="info-label">缓存 TTL</span>
      <span class="info-value">${cache.ttl_minutes || 30} 分钟</span>
    </div>
    <div class="info-row">
      <span class="info-label">活跃缓存</span>
      <span class="info-value">${cache.active || 0} 条</span>
    </div>
    <div class="info-row">
      <span class="info-label">总缓存条目</span>
      <span class="info-value">${cache.total || 0} 条</span>
    </div>
  `;
}

/* ---- Cache & Stats Management ---- */
$("btn-clear-cache").addEventListener("click", async () => {
  try {
    const result = await bridge.apiPost("clear_cache");
    setStatus(result.message || "缓存已清除", "saved");
    setTimeout(() => setStatus("已加载", ""), 2000);
    loadStats();
  } catch (e) {
    setStatus("清除失败: " + e.message, "error");
  }
});

$("btn-reset-stats").addEventListener("click", async () => {
  if (!confirm("确认重置所有统计数据？此操作不可撤销。")) return;
  try {
    await bridge.apiPost("reset_stats");
    setStatus("统计已重置", "saved");
    setTimeout(() => setStatus("已加载", ""), 2000);
    loadStats();
  } catch (e) {
    setStatus("重置失败: " + e.message, "error");
  }
});

$("btn-refresh-stats").addEventListener("click", loadStats);

/* ---- Save button ---- */
$("btn-save").addEventListener("click", saveConfig);

/* ---- Init ---- */
await bridge.ready();
await loadConfig();
setStatus("已加载", "");
