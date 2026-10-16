/* No cookies, persistence, third-party requests, or stale-data fallback. */
"use strict";
const $ = (id) => document.getElementById(id);
const viewButtons = [...document.querySelectorAll("[data-view]")];
const statuses = {possible: "可能仍可参与", closed: "报名已截止", unknown: "报名状态未确认"};
const labels = {all: "最新通知", current: "当前机会", history: "历史信息"};
let data = null, view = "all", pollTimer, expiryTimer, requestController, generation = 0;
const filterControls = {source: "publisher", type: "information-type", topic: "topic", status: "registration-status", published: "date-presence", from: "date-from", to: "date-to", category: "category"};

function queryPath() {
  const params = new URLSearchParams();
  for (const [key,id] of Object.entries(filterControls)) {
    const value = $(id).value;
    if (value && value !== "all") params.set(key, value);
  }
  return "/api/notices/v2" + (params.size ? "?" + params.toString() : "");
}

function populateFilters(payload) {
  for (const [key,id,label] of [["sources","publisher","全部来源"],["types","information-type","全部类型"],["topics","topic","全部主题"]]) {
    const selected = $(id).value;
    const values = [{id: "all", label}, ...payload.filter_options[key]];
    if (key === "topics") values.push({id: "unclassified", label: "主题未分类"});
    $(id).replaceChildren(...values.map((v) => Object.assign(document.createElement("option"), {value: v.id, textContent: v.label})));
    if (values.some((v) => v.id === selected)) $(id).value = selected;
  }
}

function formatTime(value) {
  const stamp = new Date(value);
  if (!Number.isFinite(stamp.getTime())) return "未提供";
  return new Intl.DateTimeFormat("zh-CN", {timeZone: "Asia/Shanghai", year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false}).format(stamp);
}

function sourceUrl(value) {
  const url = new URL(value);
  if (!["http:", "https:"].includes(url.protocol) || url.username || url.password ||
      (url.port && !["80", "443"].includes(url.port)) ||
      !(url.hostname === "cdu.edu.cn" || url.hostname.endsWith(".cdu.edu.cn"))) throw new Error("Invalid official URL");
  return url.href;
}

function validate(payload) {
  if (payload.schema_version !== 2 || payload.taxonomy_version !== 1 || !payload.filter_options ||
      !["sources","types","topics"].every((key) => Array.isArray(payload.filter_options[key]) && payload.filter_options[key].every((v) => typeof v.id === "string" && typeof v.label === "string")) ||
      !Array.isArray(payload.items) || !Array.isArray(payload.categories) ||
      !Number.isFinite(payload.valid_for_ms) || payload.valid_for_ms <= 0 || payload.valid_for_ms > 3600000 ||
      !["live", "offline_replay"].includes(payload.data_mode) || !payload.refreshed_at) throw new Error("Invalid response");
  const guids = new Set(), urls = new Set();
  payload.items = payload.items.filter((item) => {
    for (const key of ["guid", "title", "source_name", "source_url", "summary", "source_checked_at", "first_aggregated_at"]) {
      if (typeof item[key] !== "string" || !item[key]) throw new Error("Invalid item");
    }
    if (!Object.hasOwn(statuses, item.registration_status) || !["current", "history", "unknown"].includes(item.listing) ||
        !payload.categories.includes(item.category) || (item.source_published_date !== null && !/^20\d{2}-\d{2}-\d{2}$/.test(item.source_published_date))) throw new Error("Invalid state");
    sourceUrl(item.source_url);
    const c = item.classification;
    if (!c || c.version !== 1 || c.method !== "rules" || c.model_state !== "not_analyzed" ||
        !payload.filter_options.types.some((t) => t.id === c.type) || !Array.isArray(c.topics) ||
        !c.topics.every((t) => payload.filter_options.topics.some((o) => o.id === t)) ||
        !payload.filter_options.sources.some((s) => s.id === item.publisher_id) || !Array.isArray(item.discovery_source_ids)) throw new Error("Invalid classification");
    if (guids.has(item.guid) || urls.has(item.source_url)) return false;
    guids.add(item.guid); urls.add(item.source_url); return true;
  });
  return payload;
}

function message(kind, title, copy) {
  $("notice-list").replaceChildren();
  $("message").hidden = false;
  $("message").dataset.kind = kind;
  $("message-title").textContent = title;
  $("message-copy").textContent = copy;
}

function clearData() {
  clearTimeout(expiryTimer);
  data = null;
  $("collected-at").textContent = $("refreshed-at").textContent = "—";
  $("replay-note").hidden = true;
  document.querySelectorAll("[data-count]").forEach((node) => { node.textContent = "—"; });
  $("source-list").replaceChildren(Object.assign(document.createElement("li"), {textContent: "当前未获取到可核验数据"}));
}

function unavailable(copy = "信息源暂时无法核验，已隐藏之前的通知。请稍后刷新。") {
  clearData();
  $("service-state").dataset.state = "error";
  $("service-state").lastChild.textContent = "信息暂不可用";
  $("result-count").textContent = "等待恢复";
  message("error", "校园信息暂不可用", copy);
}

function revalidate() {
  clearData();
  $("service-state").dataset.state = "loading";
  $("service-state").lastChild.textContent = "正在重新核验";
  $("result-count").textContent = "正在获取通知";
  message("loading", "正在获取校园信息", "页面恢复后会重新核验通知，请稍候。");
}

function card(item) {
  const fragment = $("notice-card").content.cloneNode(true);
  const find = (selector) => fragment.querySelector(selector);
  fragment.querySelector("article").dataset.guid = item.guid;
  const time = find(".date-value");
  if (item.source_published_date) {
    time.dateTime = item.source_published_date;
    const day = Object.assign(document.createElement("span"), {className: "date-day", textContent: item.source_published_date.slice(5).replace("-", "/")});
    const year = Object.assign(document.createElement("span"), {className: "date-year", textContent: item.source_published_date.slice(0, 4)});
    time.append(day, year);
    time.setAttribute("aria-label", item.source_published_date);
  } else time.textContent = "日期未提供";
  find(".category-tag").textContent = item.category;
  find(".type-tag").textContent = data.filter_options.types.find((t) => t.id === item.classification.type)?.label || "未分类";
  find(".topic-tags").textContent = item.classification.topics.map((id) => data.filter_options.topics.find((t) => t.id === id)?.label).join(" · ") || "主题未分类";
  find(".history-tag").hidden = item.listing !== "history";
  find(".status-tag").textContent = statuses[item.registration_status];
  find(".status-tag").dataset.state = item.registration_status;
  find(".notice-title").textContent = item.title;
  find(".source-name").textContent = "官方来源 · " + item.source_name;
  find(".summary").textContent = item.summary;
  find(".checked-at").textContent = "系统采集 " + formatTime(item.source_checked_at);
  find(".checked-at").title = "最近成功采集：" + item.source_checked_at + "；首次收录：" + item.first_aggregated_at;
  find(".original-link").href = sourceUrl(item.source_url);
  find(".original-link").setAttribute("aria-label", "查看官方原文：" + item.title);
  find(".source-url").textContent = item.source_url;
  return fragment;
}

function render() {
  $("list-title").textContent = labels[view];
  $("view-note").textContent = view === "current" ? "仅列出有明确时间依据的可能参与机会；具体资格仍请核对官方原文。" :
    view === "history" ? "历史报道或明确已截止的信息；报名状态未知的历史通知不会标为已截止。" :
      "原文发布日期优先；日期未提供时，按首次收录时间排序。";
  viewButtons.forEach((button) => button.setAttribute("aria-pressed", String(button.dataset.view === view)));
  if (!data) return;
  const count = (scope) => data.items.filter((item) => scope === "all" || item.listing === scope).length;
  document.querySelectorAll("[data-count]").forEach((node) => { node.textContent = count(node.dataset.count); });
  const category = $("category").value;
  const items = data.items.filter((item) => (view === "all" || item.listing === view) && (category === "all" || item.category === category));
  $("result-count").textContent = items.length + " 条信息";
  $("notice-list").replaceChildren(...items.map(card));
  $("message").hidden = items.length > 0;
  if (!items.length) {
    if (Object.values(filterControls).some((id) => $(id).value && $(id).value !== "all")) message("empty", "这些条件下暂时没有通知", "可以减少筛选条件，或重置后查看全部合格校园信息。未知状态不代表没有相关信息。");
    else if (view === "current") message("empty", "暂未确认当前可参与的机会", "你仍可在“最新通知”浏览校园信息。报名状态未确认，不代表已经截止。");
    else if (view === "history") message("empty", "暂时没有历史信息", "有符合历史条件的通知后，会在这里显示。");
    else message("empty", "暂时没有可展示的通知", "采集到通过质量检查的官方通知后，会自动出现在这里。");
  }
}

async function refresh() {
  clearTimeout(pollTimer);
  if (document.hidden) return;
  const ticket = ++generation;
  requestController?.abort();
  requestController = new AbortController();
  const controller = requestController;
  const timeout = setTimeout(() => controller.abort(), 6000);
  const started = performance.now();
  $("refresh").disabled = true;
  $("notice-list").setAttribute("aria-busy", "true");
  try {
    const response = await fetch(queryPath(), {cache: "no-store", credentials: "omit", signal: controller.signal, headers: {Accept: "application/json"}});
    if (response.status === 400) throw new Error("Invalid filters");
    if (!response.ok || !response.headers.get("content-type")?.startsWith("application/json")) throw new Error("Source unavailable");
    const payload = validate(await response.json());
    const remaining = payload.valid_for_ms - (performance.now() - started);
    if (remaining <= 0) throw new Error("Snapshot expired in transit");
    if (ticket !== generation || document.hidden) return;
    data = payload;
    populateFilters(payload);
    clearTimeout(expiryTimer);
    expiryTimer = setTimeout(() => unavailable("数据核验已过期，已隐藏通知。正在等待重新核验，请稍后刷新。"), remaining);
    $("service-state").dataset.state = "ready";
    $("service-state").lastChild.textContent = "官方来源 · 数据已核验";
    $("refreshed-at").textContent = formatTime(payload.refreshed_at);
    const latest = payload.items.map((i) => i.source_checked_at).sort((a,b) => Date.parse(b)-Date.parse(a))[0];
    $("collected-at").textContent = latest ? formatTime(latest) : "暂无采集记录";
    $("replay-note").hidden = payload.data_mode !== "offline_replay";
    const sources = [...new Set(payload.items.map((i) => i.source_name))].sort();
    $("source-list").replaceChildren(...(sources.length ? sources : ["暂无合格通知"]).map((name) => Object.assign(document.createElement("li"), {textContent: name})));
    render();
    pollTimer = setTimeout(refresh, Math.max(100, Math.min(5000, remaining / 2)));
  } catch (error) {
    if (ticket !== generation || document.hidden) return;
    unavailable(error.message === "Invalid filters" ? "筛选条件无效，请调整日期或重置筛选。" : undefined);
    pollTimer = setTimeout(refresh, 5000);
  } finally {
    clearTimeout(timeout);
    if (ticket === generation) {
      $("refresh").disabled = false;
      $("notice-list").setAttribute("aria-busy", "false");
    }
  }
}

viewButtons.forEach((button) => button.addEventListener("click", () => { view = button.dataset.view; render(); }));
Object.entries(filterControls).forEach(([key,id]) => $(id).addEventListener("change", () => {
  if (key === "published" && $(id).value === "unknown") {
    $("date-from").value = $("date-to").value = "";
  } else if ((key === "from" || key === "to") && $(id).value) $("date-presence").value = "known";
  $("date-from").disabled = $("date-to").disabled = $("date-presence").value === "unknown";
  revalidate(); refresh();
}));
$("reset-filters").addEventListener("click", () => {
  for (const [key,id] of Object.entries(filterControls)) $(id).value = ["from","to"].includes(key) ? "" : "all";
  $("date-from").disabled = $("date-to").disabled = false;
  view = "all"; revalidate(); render(); refresh();
});
$("refresh").addEventListener("click", refresh);
window.addEventListener("offline", () => { ++generation; requestController?.abort(); unavailable("网络已断开，通知暂时隐藏。网络恢复后会重新获取。"); });
window.addEventListener("online", refresh);
document.addEventListener("visibilitychange", () => {
  ++generation; requestController?.abort(); clearTimeout(pollTimer);
  revalidate();
  if (!document.hidden) refresh();
});
window.addEventListener("pagehide", () => { ++generation; requestController?.abort(); revalidate(); });
window.addEventListener("pageshow", () => { if (!data) refresh(); });
